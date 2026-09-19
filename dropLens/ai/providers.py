"""AI provider registry: presets for local + cloud model backends.

Every provider speaks a documented REST API:

  * ``openai-compatible`` — works with Ollama, LM Studio, llama.cpp, vLLM,
    OpenAI, OpenRouter, Groq, Mistral, Together, Azure (OpenAI schema).
  * ``anthropic``          — Anthropic Messages API.
  * ``gemini``             — Google Generative Language API.

Embeddings are optional (semantic search lights up only when the active
provider exposes an embedding model).
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Optional

KIND_OPENAI = "openai"
KIND_ANTHROPIC = "anthropic"
KIND_GEMINI = "gemini"

PRESETS: dict[str, dict[str, Any]] = {
    "Ollama (local)": {
        "kind": KIND_OPENAI,
        "base_url": "http://localhost:11434/v1",
        "api_key": "",
        "model": "llama3.1",
        "embedding_model": "nomic-embed-text",
        "hint": "Free local models; keeps everything offline.",
    },
    "LM Studio (local)": {
        "kind": KIND_OPENAI,
        "base_url": "http://localhost:1234/v1",
        "api_key": "",
        "model": "local-model",
        "embedding_model": "",
        "hint": "Load local GGUF models with a built-in server.",
    },
    "llama.cpp (local)": {
        "kind": KIND_OPENAI,
        "base_url": "http://localhost:8080/v1",
        "api_key": "",
        "model": "local-model",
        "embedding_model": "",
        "hint": "Runs against llama-server inference.",
    },
    "OpenAI": {
        "kind": KIND_OPENAI,
        "base_url": "https://api.openai.com/v1",
        "api_key": "",
        "model": "gpt-4o-mini",
        "embedding_model": "text-embedding-3-small",
        "hint": "Cloud GPT models (API key required).",
    },
    "Azure OpenAI": {
        "kind": KIND_OPENAI,
        "base_url": "https://<resource>.openai.azure.com/openai/v1",
        "api_key": "",
        "model": "gpt-4o-mini",
        "embedding_model": "",
        "hint": "Set the resource host in the URL.",
    },
    "OpenRouter": {
        "kind": KIND_OPENAI,
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "",
        "model": "openrouter/auto",
        "embedding_model": "",
        "hint": "One key, hundreds of models.",
    },
    "Groq": {
        "kind": KIND_OPENAI,
        "base_url": "https://api.groq.com/openai/v1",
        "api_key": "",
        "model": "llama-3.3-70b-versatile",
        "embedding_model": "",
        "hint": "Fast cloud inference (API key required).",
    },
    "Mistral": {
        "kind": KIND_OPENAI,
        "base_url": "https://api.mistral.ai/v1",
        "api_key": "",
        "model": "mistral-small-latest",
        "embedding_model": "mistral-embed",
        "hint": "Cloud models (API key required).",
    },
    "Anthropic Claude": {
        "kind": KIND_ANTHROPIC,
        "base_url": "https://api.anthropic.com",
        "api_key": "",
        "model": "claude-3-5-haiku-latest",
        "embedding_model": "",
        "hint": "Claude Messages API (API key required).",
    },
    "Google Gemini": {
        "kind": KIND_GEMINI,
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "api_key": "",
        "model": "gemini-2.0-flash",
        "embedding_model": "text-embedding-004",
        "hint": "Vertex-style endpoint via API key query param.",
    },
    "Custom (OpenAI-compatible)": {
        "kind": KIND_OPENAI,
        "base_url": "http://localhost:8000/v1",
        "api_key": "",
        "model": "",
        "embedding_model": "",
        "hint": "Any OpenAI-compatible server or gateway.",
    },
}


class AIProvider:
    def __init__(
        self,
        name: str,
        kind: str = KIND_OPENAI,
        base_url: str = "",
        api_key: str = "",
        model: str = "",
        embedding_model: str = "",
    ):
        self.name = name
        self.kind = kind
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.model = model
        self.embedding_model = embedding_model or ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "kind": self.kind, "base_url": self.base_url,
            "api_key": self.api_key, "model": self.model,
            "embedding_model": self.embedding_model,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AIProvider":
        return cls(
            name=str(d.get("name", "Provider")),
            kind=str(d.get("kind", KIND_OPENAI)),
            base_url=str(d.get("base_url", "")),
            api_key=str(d.get("api_key", "")),
            model=str(d.get("model", "")),
            embedding_model=str(d.get("embedding_model", "")),
        )

    @property
    def has_embeddings(self) -> bool:
        return bool(self.embedding_model)

    @property
    def is_local(self) -> bool:
        host = (self.base_url or "").lower()
        return "localhost" in host or "127.0.0.1" in host or ":11434" in host

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AIProvider {self.name} ({self.kind}) model={self.model}>"


class AIConfig:
    """Persisted AI preferences (stored inside settings.json under 'ai')."""

    def __init__(self, data: Optional[dict[str, Any]] = None):
        data = data or {}
        providers_data = data.get("providers") or []
        self.providers: list[AIProvider] = [
            AIProvider.from_dict(p) for p in providers_data if isinstance(p, dict)
        ]
        if not self.providers:
            self.providers = [
                AIProvider(name=name, **{k: v for k, v in preset.items() if k != "hint"})
                for name, preset in PRESETS.items()
            ]
        self.active: str = str(data.get("active", self.providers[0].name if self.providers else ""))

    def active_provider(self) -> Optional[AIProvider]:
        for p in self.providers:
            if p.name == self.active:
                return p
        return self.providers[0] if self.providers else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "providers": [p.to_dict() for p in self.providers],
            "active": self.active,
        }

    def upsert_provider(self, provider: AIProvider) -> None:
        for i, p in enumerate(self.providers):
            if p.name == provider.name:
                self.providers[i] = provider
                return
        self.providers.append(provider)

    def delete_provider(self, name: str) -> None:
        self.providers = [p for p in self.providers if p.name != name]
        if self.active == name:
            self.active = self.providers[0].name if self.providers else ""

    @staticmethod
    def merge(defaults: list[AIProvider], saved: Optional[dict], active: Optional[str]) -> "AIConfig":
        return AIConfig({"providers": saved or [p.to_dict() for p in defaults], "active": active or ""})


def preset_for(name: str) -> Optional[dict[str, Any]]:
    p = PRESETS.get(name)
    return deepcopy(p) if p else None