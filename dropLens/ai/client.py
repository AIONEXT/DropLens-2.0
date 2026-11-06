"""Thin HTTP client for OpenAI-compatible, Anthropic and Gemini APIs.

All calls are synchronous (they run on worker threads) and every failure is
raised as ``AIConnectionError`` with a human readable message, so the UI layer
can surface it without crashing.

Every request passes through :mod:`dropLens.net`, which enforces offline-only
mode and records what left the machine.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import requests

from .. import net
from .providers import AIProvider, KIND_ANTHROPIC, KIND_GATEWAY, KIND_GEMINI, KIND_OPENAI

TIMEOUT = 120

MAX_CHAT_CHARS = 400_000  # safety ceiling for prompt payload

EMBED_BATCH = 64          # texts per embedding request


class AIConnectionError(Exception):
    """Raised for any connectivity / provider error that the UI can display."""


class AINotConfigured(Exception):
    """Raised when no AI provider is configured."""


def _payload_chars(payload: Any) -> int:
    try:
        return len(json.dumps(payload, ensure_ascii=False, default=str))
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return 0


def _resolved(provider: AIProvider) -> AIProvider:
    """Point a ``gateway`` provider at the embedded loopback gateway.

    Routing, fallback and provider translation are declared in ``gateway.toml``
    (see :mod:`dropLens.ai.gateway`), so a gateway provider keeps the OpenAI
    wire format and only its base URL changes. The URL is loopback, which means
    offline-only mode still allows it.
    """
    if provider.kind != KIND_GATEWAY:
        return provider
    from . import gateway
    try:
        url = gateway.base_url()
    except gateway.GatewayUnavailable as exc:
        raise AIConnectionError(str(exc)) from exc
    return AIProvider(
        name=provider.name,
        kind=KIND_OPENAI,
        base_url=f"{url}/v1",
        api_key=provider.api_key or "droplens-local",
        model=provider.model,
        embedding_model=provider.embedding_model,
    )


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
          timeout: int = TIMEOUT, kind: str = "ai") -> dict[str, Any]:
    net.check(url)
    try:
        resp = requests.post(url, json=payload, headers=_headers(provider), timeout=timeout)
    except requests.RequestException as exc:
        raise AIConnectionError(f"Cannot reach {url}: {exc}") from exc
    net.log_egress(kind, url, _payload_chars(payload))
    if resp.status_code >= 400:
        detail = resp.text[:300]
        try:
            detail = json.loads(resp.text).get("error", {}).get("message", detail)
        except Exception:  # noqa: BLE001 - error bodies are not always JSON
            pass
        raise AIConnectionError(f"Provider error ({resp.status_code}): {detail}")
    return resp.json()


def _get(provider: AIProvider, url: str, timeout: int = TIMEOUT) -> dict[str, Any] | list[Any]:
    net.check(url)
    try:
        resp = requests.get(url, headers=_headers(provider, has_body=False), timeout=timeout)
    except requests.RequestException as exc:
        raise AIConnectionError(f"Cannot reach {url}: {exc}") from exc
    net.log_egress("models", url)
    if resp.status_code >= 400:
        raise AIConnectionError(f"Provider error ({resp.status_code}): {resp.text[:300]}")
    return resp.json()


def chat(provider: AIProvider, system: str, user: str, temperature: float = 0.3,
         max_tokens: Optional[int] = 2048) -> str:
    """One-shot chat: returns the assistant text reply."""
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]
    return chat_messages(provider, messages, temperature, max_tokens)


def chat_messages(provider: AIProvider, messages: list[dict[str, str]],
                  temperature: float = 0.3, max_tokens: Optional[int] = 2048) -> str:
    provider = _resolved(provider)
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
            "temperature": temperature,
            **({"max_tokens": max_tokens} if max_tokens else {})}
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
        "temperature": temperature,
        **({"max_tokens": max_tokens} if max_tokens else {}),
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
                             **({"maxOutputTokens": max_tokens} if max_tokens else {})},
    }
    # The key travels in the ``x-goog-api-key`` header (set by ``_headers``) and
    # never in the query string: ``?key=`` is Google's documented example, but a
    # URL carrying a credential ends up in proxy logs, browser histories and
    # network captures. Header auth is equally supported and does not leak.
    data = _post(provider, url, body)
    try:
        return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"]).strip()
    except (KeyError, IndexError, TypeError):
        raise AIConnectionError(f"Unexpected response shape from {provider.name}.")


def embed(provider: AIProvider, texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts. Returns list of vectors aligned with ``texts``."""
    if not texts:
        return []
    provider = _resolved(provider)
    if not provider.has_embeddings:
        raise AIConnectionError(f"{provider.name} does not expose an embedding model.")
    out: list[list[float]] = []
    step = EMBED_BATCH if provider.kind != KIND_GEMINI else 32
    for start in range(0, len(texts), step):
        part = texts[start:start + step]
        if provider.kind == KIND_GEMINI:
            out.extend(_embed_gemini_batch(provider, part))
        else:
            out.extend(_embed_openai(provider, part))
    return out


def _embed_openai(provider: AIProvider, texts: list[str]) -> list[list[float]]:
    url = f"{provider.base_url}/embeddings"
    data = _post(provider, url, {"model": provider.embedding_model, "input": texts})
    try:
        out: list[list[float]] = [None] * len(texts)  # type: ignore[list-item]
        for item in data["data"]:
            idx = int(item["index"])
            out[idx] = [float(x) for x in item["embedding"]]
        return out
    except (KeyError, TypeError, IndexError):
        raise AIConnectionError(f"Unexpected embedding response from {provider.name}.")


def _embed_gemini_batch(provider: AIProvider, texts: list[str]) -> list[list[float]]:
    """Gemini exposes a batch endpoint; fall back to one request per text."""
    url = f"{provider.base_url}/models/{provider.embedding_model}:batchEmbedContents"
    body = {"requests": [{"model": f"models/{provider.embedding_model}",
                          "content": {"parts": [{"text": t}]}} for t in texts]}
    try:
        data = _post(provider, url, body)
        return [[float(x) for x in item["values"]] for item in data["embeddings"]]
    except (AIConnectionError, KeyError, TypeError, IndexError):
        return [_embed_gemini(provider, t) for t in texts]


def _embed_gemini(provider: AIProvider, text: str) -> list[float]:
    url = f"{provider.base_url}/models/{provider.embedding_model}:embedContent"
    body = {"content": {"parts": [{"text": text}]}}
    data = _post(provider, url, body)
    try:
        return [float(x) for x in data["embedding"]["values"]]
    except (KeyError, TypeError):
        raise AIConnectionError(f"Unexpected embedding response from {provider.name}.")


def list_models(provider: AIProvider) -> list[str]:
    """Best-effort model listing for OpenAI-compatible service (Ollama, LM Studio…)."""
    provider = _resolved(provider)
    if provider.kind != KIND_OPENAI:
        return []
    url = f"{provider.base_url}/models"
    try:
        data = _get(provider, url, timeout=10)
        models = data.get("data") if isinstance(data, dict) else []
        return sorted(str(m.get("id", "")) for m in models if m.get("id"))
    except (AIConnectionError, KeyError, TypeError):
        return []


# ---------------------------------------------------------------------------
# Tool calling (function calling), provider neutral
# ---------------------------------------------------------------------------
# Normalised message shapes understood by every converter below:
#   {"role": "system"|"user"|"assistant", "content": str,
#    "tool_calls": [{"id": str, "name": str, "arguments": dict}]}
#   {"role": "tool", "tool_call_id": str, "name": str, "content": str}

def chat_tools(provider: AIProvider, messages: list[dict[str, Any]],
               tools: list[dict[str, Any]], temperature: float = 0.2,
               max_tokens: Optional[int] = 1200) -> dict[str, Any]:
    """One assistant turn that may request tool calls.

    Returns ``{"content": str, "tool_calls": [{"id", "name", "arguments"}]}``.
    An empty ``tools`` list means "answer without tools".
    """
    provider = _resolved(provider)
    total = sum(len(str(m.get("content", ""))) for m in messages)
    if total > MAX_CHAT_CHARS:
        raise AIConnectionError("Prompt too large for the active provider.")
    if provider.kind == KIND_GEMINI:
        return _tools_gemini(provider, messages, tools, temperature, max_tokens)
    if provider.kind == KIND_ANTHROPIC:
        return _tools_anthropic(provider, messages, tools, temperature, max_tokens)
    return _tools_openai(provider, messages, tools, temperature, max_tokens)


def _openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        role = str(m.get("role", "user"))
        if role == "tool":
            out.append({"role": "tool", "tool_call_id": str(m.get("tool_call_id", "")),
                        "content": str(m.get("content", ""))})
            continue
        calls = m.get("tool_calls") or []
        if role == "assistant" and calls:
            out.append({
                "role": "assistant",
                "content": str(m.get("content") or "") or None,
                "tool_calls": [
                    {"id": str(c.get("id") or f"call_{i}"), "type": "function",
                     "function": {"name": str(c.get("name", "")),
                                  "arguments": json.dumps(c.get("arguments") or {}, ensure_ascii=False)}}
                    for i, c in enumerate(calls)
                ],
            })
            continue
        out.append({"role": role, "content": str(m.get("content", ""))})
    return out


def _tools_openai(provider: AIProvider, messages, tools, temperature, max_tokens) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": provider.model,
        "messages": _openai_messages(messages),
        "temperature": temperature,
        **({"max_tokens": max_tokens} if max_tokens else {}),
    }
    if tools:
        body["tools"] = [{"type": "function", "function": t} for t in tools]
        body["tool_choice"] = "auto"
    data = _post(provider, f"{provider.base_url}/chat/completions", body)
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        raise AIConnectionError(f"Unexpected response shape from {provider.name}.")
    calls: list[dict[str, Any]] = []
    for i, call in enumerate(message.get("tool_calls") or []):
        fn = call.get("function") or {}
        raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except json.JSONDecodeError:
            args = {}
        calls.append({"id": str(call.get("id") or f"call_{i}"),
                      "name": str(fn.get("name", "")), "arguments": args})
    return {"content": str(message.get("content") or ""), "tool_calls": calls}


def _anthropic_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    system = " ".join(str(m.get("content", "")) for m in messages if m.get("role") == "system")
    out: list[dict[str, Any]] = []
    for m in messages:
        role = str(m.get("role", "user"))
        if role == "system":
            continue
        if role == "tool":
            block = {"type": "tool_result", "tool_use_id": str(m.get("tool_call_id", "")),
                     "content": str(m.get("content", ""))}
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
            continue
        if role == "assistant":
            blocks: list[dict[str, Any]] = []
            if m.get("content"):
                blocks.append({"type": "text", "text": str(m["content"])})
            for i, call in enumerate(m.get("tool_calls") or []):
                blocks.append({"type": "tool_use", "id": str(call.get("id") or f"call_{i}"),
                               "name": str(call.get("name", "")),
                               "input": call.get("arguments") or {}})
            if blocks:
                out.append({"role": "assistant", "content": blocks})
            continue
        out.append({"role": "user", "content": [{"type": "text", "text": str(m.get("content", ""))}]})
    return system, out


def _tools_anthropic(provider: AIProvider, messages, tools, temperature, max_tokens) -> dict[str, Any]:
    if not provider.api_key:
        raise AIConnectionError(f"{provider.name}: API key is required.")
    system, converted = _anthropic_messages(messages)
    body: dict[str, Any] = {
        "model": provider.model,
        "max_tokens": max_tokens or 1200,
        "temperature": temperature,
        "messages": converted,
    }
    if system:
        body["system"] = system
    if tools:
        body["tools"] = [{"name": t["name"], "description": t.get("description", ""),
                          "input_schema": t.get("parameters") or {"type": "object", "properties": {}}}
                         for t in tools]
    data = _post(provider, f"{provider.base_url}/v1/messages", body)
    try:
        blocks = data["content"]
    except (KeyError, TypeError):
        raise AIConnectionError(f"Unexpected response shape from {provider.name}.")
    text = "".join(str(b.get("text", "")) for b in blocks if b.get("type") == "text")
    calls = [{"id": str(b.get("id", "")), "name": str(b.get("name", "")),
              "arguments": b.get("input") or {}}
             for b in blocks if b.get("type") == "tool_use"]
    return {"content": text, "tool_calls": calls}


def _gemini_contents(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    system = " ".join(str(m.get("content", "")) for m in messages if m.get("role") == "system")
    contents: list[dict[str, Any]] = []
    for m in messages:
        role = str(m.get("role", "user"))
        if role == "system":
            continue
        if role == "tool":
            contents.append({"role": "user", "parts": [{"functionResponse": {
                "name": str(m.get("name", "")),
                "response": {"result": str(m.get("content", ""))},
            }}]})
            continue
        if role == "assistant":
            parts: list[dict[str, Any]] = []
            if m.get("content"):
                parts.append({"text": str(m["content"])})
            for call in m.get("tool_calls") or []:
                parts.append({"functionCall": {"name": str(call.get("name", "")),
                                               "args": call.get("arguments") or {}}})
            if parts:
                contents.append({"role": "model", "parts": parts})
            continue
        contents.append({"role": "user", "parts": [{"text": str(m.get("content", ""))}]})
    return system, contents


def _tools_gemini(provider: AIProvider, messages, tools, temperature, max_tokens) -> dict[str, Any]:
    system, contents = _gemini_contents(messages)
    body: dict[str, Any] = {
        "contents": contents,
        "generationConfig": {"temperature": temperature,
                             **({"maxOutputTokens": max_tokens} if max_tokens else {})},
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if tools:
        body["tools"] = [{"functionDeclarations": [
            {"name": t["name"], "description": t.get("description", ""),
             "parameters": t.get("parameters") or {"type": "object", "properties": {}}}
            for t in tools]}]
    url = f"{provider.base_url}/models/{provider.model}:generateContent"
    data = _post(provider, url, body)
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, TypeError):
        raise AIConnectionError(f"Unexpected response shape from {provider.name}.")
    text = "".join(str(p.get("text", "")) for p in parts if "text" in p)
    calls: list[dict[str, Any]] = []
    for i, part in enumerate(parts):
        call = part.get("functionCall")
        if call:
            calls.append({"id": f"call_{i}", "name": str(call.get("name", "")),
                          "arguments": call.get("args") or {}})
    return {"content": text, "tool_calls": calls}


def ping(provider: AIProvider) -> str:
    """Validate a provider and return a short human summary."""
    provider = _resolved(provider)
    if not provider.model:
        raise AIConnectionError("Set a model name first.")
    if provider.kind == KIND_ANTHROPIC and not provider.api_key:
        raise AIConnectionError("Anthropic requires an API key.")
    if provider.kind == KIND_GEMINI and not provider.base_url:
        raise AIConnectionError("Set the Gemini endpoint URL.")
    reply = chat(provider, "You are a connectivity probe.", "Reply with exactly: ok", max_tokens=8)
    return reply[:40] or "ok"
