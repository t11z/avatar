"""OpenAI model provider.

Discovers models via ``GET /v1/models`` and generates content via the Chat
Completions API. The ``openai`` SDK is an optional extra and is imported lazily
inside methods so this module imports cleanly even when the SDK is absent.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from avatar.core.registry import register_model
from avatar.core.types import (
    GenerationRequest,
    GenerationResult,
    ModelInfo,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from openai import AsyncOpenAI

_DEFAULT_MODEL = "gpt-4o-mini"
_DEFAULT_TIMEOUT = 30.0
_MODELS_CACHE_TTL = 300.0


@register_model("openai")
class OpenAIProvider:
    """ModelProvider backed by OpenAI's HTTP API via the ``openai`` SDK."""

    name = "openai"

    def __init__(self, settings: Mapping[str, Any]) -> None:
        self._api_key: str | None = settings.get("api_key")
        self._base_url: str | None = settings.get("base_url")
        self._organization: str | None = settings.get("organization")
        self._default_model: str = settings.get("model") or _DEFAULT_MODEL
        self._timeout: float = float(settings.get("timeout", _DEFAULT_TIMEOUT))
        self._cache_ttl: float = float(settings.get("models_cache_ttl", _MODELS_CACHE_TTL))

        self._client: AsyncOpenAI | None = None
        self._models_cache: list[ModelInfo] | None = None
        self._models_cache_at: float = 0.0

    # -- client -----------------------------------------------------------
    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            from openai import AsyncOpenAI  # lazy import of optional SDK

            if not self._api_key:
                raise RuntimeError("openai provider requires an 'api_key' setting")

            kwargs: dict[str, Any] = {"api_key": self._api_key, "timeout": self._timeout}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            if self._organization:
                kwargs["organization"] = self._organization
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    # -- discovery --------------------------------------------------------
    async def list_models(self) -> list[ModelInfo]:
        now = time.monotonic()
        if self._models_cache is not None and (now - self._models_cache_at) < self._cache_ttl:
            return self._models_cache

        client = self._get_client()
        page = await client.models.list()

        models: list[ModelInfo] = []
        for item in page.data:
            raw = self._to_dict(item)
            created = raw.get("created")
            created_at: datetime | None = None
            if isinstance(created, (int, float)):
                created_at = datetime.fromtimestamp(created, tz=UTC)
            model_id = raw.get("id") or getattr(item, "id", "")
            models.append(
                ModelInfo(
                    id=str(model_id),
                    provider=self.name,
                    display_name=str(model_id),
                    created_at=created_at,
                    raw=raw,
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

        call_kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": req.max_tokens,
        }
        # Pass-through caller params (e.g. temperature, top_p) without clobbering.
        for key, value in req.params.items():
            call_kwargs.setdefault(key, value)

        completion = await client.chat.completions.create(**call_kwargs)
        raw = self._to_dict(completion)

        text = ""
        stop_reason: str | None = None
        choices = raw.get("choices") or []
        if choices:
            first = choices[0]
            stop_reason = first.get("finish_reason")
            message = first.get("message") or {}
            text = message.get("content") or ""

        usage = raw.get("usage") or {}
        input_tokens = usage.get("prompt_tokens")
        output_tokens = usage.get("completion_tokens")

        refused = stop_reason == "content_filter"

        if req.max_chars is not None and len(text) > req.max_chars:
            text = text[: req.max_chars]

        return GenerationResult(
            text=text,
            model=str(raw.get("model") or model),
            provider=self.name,
            refused=refused,
            stop_reason=stop_reason,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
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
            await self._client.close()
            self._client = None

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def _to_dict(obj: Any) -> dict[str, Any]:
        for attr in ("model_dump", "to_dict"):
            fn = getattr(obj, attr, None)
            if callable(fn):
                try:
                    result = fn()
                except TypeError:
                    continue
                if isinstance(result, dict):
                    return result
        if isinstance(obj, dict):
            return dict(obj)
        return {}
