"""Thin HTTP client for OpenAI-compatible, Anthropic and Gemini APIs.

All calls are synchronous (they run on worker threads) and every failure is
raised as ``AIConnectionError`` with a human readable message, so the UI layer
can surface it without crashing.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import requests

from .providers import AIProvider, KIND_ANTHROPIC, KIND_GEMINI, KIND_OPENAI

TIMEOUT = 120

MAX_CHAT_CHARS = 400_000  # safety ceiling for prompt payload


class AIConnectionError(Exception):
    """Raised for any connectivity / provider error that the UI can display."""


class AINotConfigured(Exception):
    """Raised when no AI provider is configured."""


def _headers(provider: AIProvider, has_body: bool = True) -> dict[str, str]:
    h: dict[str, str] = {
        "User-Agent": "DropLens/2.0",
    }
    if provider.kind == KIND_ANTHROPIC and provider.api_key:
        h["x-api-key"] = provider.api_key
        h["anthropic-version"] = "2023-06-01"
    elif provider.kind == KIND_GEMINI:
        if provider.api_key:
            h["x-goog-api-key"] = provider.api_key
    elif provider.api_key:
        h["Authorization"] = f"Bearer {provider.api_key}"
    if has_body:
        h["Content-Type"] = "application/json"
    return h


def _post(provider: AIProvider, url: str, payload: dict[str, Any] | None = None,
          timeout: int = TIMEOUT) -> dict[str, Any]:
    try:
        resp = requests.post(url, json=payload, headers=_headers(provider), timeout=timeout)
    except requests.RequestException as exc:
        raise AIConnectionError(f"Cannot reach {url}: {exc}") from exc
    if resp.status_code >= 400:
        detail = resp.text[:300]
        try:
            detail = json.loads(resp.text).get("error", {}).get("message", detail)
        except Exception:
            pass
        raise AIConnectionError(f"Provider error ({resp.status_code}): {detail}")
    return resp.json()


def _get(provider: AIProvider, url: str, timeout: int = TIMEOUT) -> dict[str, Any] | list[Any]:
    try:
        resp = requests.get(url, headers=_headers(provider, has_body=False), timeout=timeout)
    except requests.RequestException as exc:
        raise AIConnectionError(f"Cannot reach {url}: {exc}") from exc
    if resp.status_code >= 400:
        raise AIConnectionError(f"Provider error ({resp.status_code}): {resp.text[:300]}")
    return resp.json()


def _payload_tail(provider: AIProvider, messages: list[dict[str, str]],
                  temperature: float, max_tokens: Optional[int]) -> dict[str, Any]:
    return {"temperature": temperature,
            **( {"max_tokens": max_tokens} if max_tokens else {})}


def chat(provider: AIProvider, system: str, user: str, temperature: float = 0.3,
         max_tokens: Optional[int] = 2048) -> str:
    """One-shot chat: returns the assistant text reply."""
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]
    return chat_messages(provider, messages, temperature, max_tokens)


def chat_messages(provider: AIProvider, messages: list[dict[str, str]],
                  temperature: float = 0.3, max_tokens: Optional[int] = 2048) -> str:
    total = sum(len(str(m.get("content", ""))) for m in messages)
    if total > MAX_CHAT_CHARS:
        raise AIConnectionError("Prompt too large for the active provider.")
    if provider.kind == KIND_GEMINI:
        return _chat_gemini(provider, messages, temperature, max_tokens)
    if provider.kind == KIND_ANTHROPIC:
        return _chat_anthropic(provider, messages, temperature, max_tokens)
    return _chat_openai(provider, messages, temperature, max_tokens)


def _chat_openai(provider: AIProvider, messages, temperature, max_tokens) -> str:
    url = f"{provider.base_url}/chat/completions"
    body = {"model": provider.model, "messages": messages,
            **_payload_tail(provider, messages, temperature, max_tokens)}
    data = _post(provider, url, body)
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError):
        raise AIConnectionError(f"Unexpected response shape from {provider.name}.")


def _chat_anthropic(provider: AIProvider, messages, temperature, max_tokens) -> str:
    if not provider.api_key:
        raise AIConnectionError(f"{provider.name}: API key is required.")
    url = f"{provider.base_url}/v1/messages"
    system = " ".join(str(m.get("content", "")) for m in messages if m.get("role") == "system")
    body: dict[str, Any] = {
        "model": provider.model,
        "system": system,
        "messages": [m for m in messages if m.get("role") != "system"],
        **_payload_tail(provider, messages, temperature, max_tokens),
    }
    data = _post(provider, url, body)
    try:
        return "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text").strip()
    except (KeyError, TypeError):
        raise AIConnectionError(f"Unexpected response shape from {provider.name}.")


def _chat_gemini(provider: AIProvider, messages, temperature, max_tokens) -> str:
    url = f"{provider.base_url}/models/{provider.model}:generateContent"
    gemini_messages = [m for m in messages if m.get("role") != "system"]
    contents = []
    for m in gemini_messages:
        contents.append({"role": "model" if m.get("role") == "assistant" else "user",
                         "parts": [{"text": m.get("content", "")}]})
    body = {
        "contents": contents,
        "generationConfig": {"temperature": temperature,
                             **( {"maxOutputTokens": max_tokens} if max_tokens else {})},
    }
    if provider.api_key:
        url = f"{url}?key={provider.api_key}"
    data = _post(provider, url, body)
    try:
        return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"]).strip()
    except (KeyError, IndexError, TypeError):
        raise AIConnectionError(f"Unexpected response shape from {provider.name}.")


def embed(provider: AIProvider, texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts. Returns list of vectors aligned with ``texts``."""
    if not texts:
        return []
    if not provider.has_embeddings:
        raise AIConnectionError(f"{provider.name} does not expose an embedding model.")
    if provider.kind == KIND_GEMINI:
        return [_embed_gemini(provider, t) for t in texts]
    return _embed_openai(provider, texts)


def _embed_openai(provider: AIProvider, texts: list[str]) -> list[list[float]]:
    url = f"{provider.base_url}/embeddings"
    data = _post(provider, url, {"model": provider.embedding_model, "input": texts})
    try:
        out: list[list[float]] = [None] * len(texts)  # type: ignore[list-item]
        for item in data["data"]:
            idx = int(item["index"])
            out[idx] = [float(x) for x in item["embedding"]]
        return out
    except (KeyError, TypeError):
        raise AIConnectionError(f"Unexpected embedding response from {provider.name}.")


def _embed_gemini(provider: AIProvider, text: str) -> list[float]:
    url = f"{provider.base_url}/models/{provider.embedding_model}:embedContent"
    body = {"content": {"parts": [{"text": text}]}}
    if provider.api_key:
        url = f"{url}?key={provider.api_key}"
    data = _post(provider, url, body)
    try:
        return [float(x) for x in data["embedding"]["values"]]
    except (KeyError, TypeError):
        raise AIConnectionError(f"Unexpected embedding response from {provider.name}.")


def list_models(provider: AIProvider) -> list[str]:
    """Best-effort model listing for OpenAI-compatible service (Ollama, LM Studio…)."""
    if provider.kind != KIND_OPENAI:
        return []
    url = f"{provider.base_url}/models"
    try:
        data = _get(provider, url, timeout=10)
        models = data.get("data") if isinstance(data, dict) else []
        return sorted(str(m.get("id", "")) for m in models if m.get("id"))
    except (AIConnectionError, KeyError, TypeError):
        return []


def ping(provider: AIProvider) -> str:
    """Validate a provider and return a short human summary."""
    if not provider.model:
        raise AIConnectionError("Set a model name first.")
    if provider.kind == KIND_ANTHROPIC and not provider.api_key:
        raise AIConnectionError("Anthropic requires an API key.")
    if provider.kind == KIND_GEMINI and not provider.base_url:
        raise AIConnectionError("Set the Gemini endpoint URL.")
    reply = chat(provider, "You are a connectivity probe.", "Reply with exactly: ok", max_tokens=8)
    return reply[:40] or "ok"