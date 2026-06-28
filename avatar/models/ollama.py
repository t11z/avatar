"""Ollama model provider.

Talks to a local (or remote) Ollama server over its plain HTTP API using
``httpx`` — there is no extra SDK to install. Models are discovered via
``GET /api/tags`` and content is generated via ``POST /api/chat``.

``httpx`` is imported lazily inside methods so this module imports cleanly even
if the dependency were absent. An :class:`httpx.AsyncClient` is owned per
provider instance and closed in :meth:`aclose`.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any

from avatar.core.registry import register_model
from avatar.core.types import GenerationRequest, GenerationResult, ModelInfo

if TYPE_CHECKING:  # pragma: no cover - typing only
    import httpx

_DEFAULT_HOST = "http://localhost:11434"
_DEFAULT_MODEL = "llama3"
_DEFAULT_TIMEOUT = 120.0
_MODELS_CACHE_TTL = 300.0


@register_model("ollama")
class OllamaProvider:
    """ModelProvider backed by an Ollama server's HTTP API (no SDK)."""

    name = "ollama"

    def __init__(self, settings: Mapping[str, Any]) -> None:
        host = settings.get("host") or _DEFAULT_HOST
        self._host: str = str(host).rstrip("/")
        self._default_model: str = settings.get("model") or _DEFAULT_MODEL
        self._timeout: float = float(settings.get("timeout", _DEFAULT_TIMEOUT))
        self._cache_ttl: float = float(settings.get("models_cache_ttl", _MODELS_CACHE_TTL))

        self._client: httpx.AsyncClient | None = None
        self._models_cache: list[ModelInfo] | None = None
        self._models_cache_at: float = 0.0

    # -- client -----------------------------------------------------------
    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            import httpx  # lazy import of optional dependency

            self._client = httpx.AsyncClient(base_url=self._host, timeout=self._timeout)
        return self._client

    # -- discovery --------------------------------------------------------
    async def list_models(self) -> list[ModelInfo]:
        now = time.monotonic()
        if self._models_cache is not None and (now - self._models_cache_at) < self._cache_ttl:
            return self._models_cache

        client = self._get_client()
        response = await client.get("/api/tags")
        response.raise_for_status()
        payload = response.json()

        models: list[ModelInfo] = []
        for item in payload.get("models") or []:
            if not isinstance(item, dict):
                continue
            model_id = item.get("name") or item.get("model") or ""
            models.append(
                ModelInfo(
                    id=str(model_id),
                    provider=self.name,
                    display_name=str(model_id) or None,
                    created_at=self._parse_modified_at(item.get("modified_at")),
                    raw=item,
                )
            )

        self._models_cache = models
        self._models_cache_at = now
        return models

    # -- generation -------------------------------------------------------
    async def generate(self, req: GenerationRequest) -> GenerationResult:
        client = self._get_client()
        model = req.model or self._default_model

        messages: list[dict[str, str]] = []
        if req.system:
            messages.append({"role": "system", "content": req.system})
        messages.append({"role": "user", "content": req.user})

        options: dict[str, Any] = {"num_predict": req.max_tokens}
        # Pass-through caller params (e.g. temperature, top_p) without clobbering.
        for key, value in req.params.items():
            options.setdefault(key, value)

        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": options,
        }

        response = await client.post("/api/chat", json=body)
        response.raise_for_status()
        raw = response.json()

        message = raw.get("message") or {}
        text = message.get("content") or ""

        stop_reason = raw.get("done_reason")
        if stop_reason is None and raw.get("done") is True:
            stop_reason = "stop"

        if req.max_chars is not None and len(text) > req.max_chars:
            text = text[: req.max_chars]

        return GenerationResult(
            text=text,
            model=str(raw.get("model") or model),
            provider=self.name,
            refused=False,
            stop_reason=stop_reason,
            input_tokens=raw.get("prompt_eval_count"),
            output_tokens=raw.get("eval_count"),
            raw=raw,
        )

    # -- lifecycle --------------------------------------------------------
    async def healthcheck(self) -> bool:
        try:
            await self.list_models()
        except Exception:
            return False
        return True

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def _parse_modified_at(value: Any) -> datetime | None:
        if not isinstance(value, str) or not value:
            return None
        text = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None
