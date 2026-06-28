"""Anthropic (Claude) model provider.

Discovers models via the Models API (``client.models.list()``) with an optional
TTL cache so discovery is "rolling" without a network call on every request, and
generates content via the Messages API.

Provider quirks handled here:
  * Newer Claude models (Opus/Fable generation) reject ``temperature``/``top_p``
    and use adaptive thinking instead of a fixed budget. We therefore do NOT send
    sampling parameters by default; callers may opt in via ``req.params``.
  * A safety refusal arrives as a successful response with
    ``stop_reason == "refusal"`` — we surface that as ``GenerationResult.refused``
    before attempting to read any content.

The ``anthropic`` SDK is an optional extra and is imported lazily inside methods
so this module imports cleanly even when the SDK is absent.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any

from avatar.core.registry import register_model
from avatar.core.types import GenerationRequest, GenerationResult, ModelInfo

if TYPE_CHECKING:  # pragma: no cover - typing only
    from anthropic import AsyncAnthropic

DEFAULT_MODEL = "claude-opus-4-8"
DEFAULT_MODELS_TTL = 3600.0


@register_model("anthropic")
class AnthropicProvider:
    """ModelProvider backed by the Anthropic Python SDK."""

    name = "anthropic"

    def __init__(self, settings: Mapping[str, Any]) -> None:
        self._settings = dict(settings)
        self._api_key = self._settings.get("api_key")
        self._base_url = self._settings.get("base_url") or self._settings.get("host")
        self._default_model = self._settings.get("model") or DEFAULT_MODEL
        timeout = self._settings.get("timeout")
        self._timeout = float(timeout) if timeout is not None else None
        ttl = self._settings.get("models_ttl", DEFAULT_MODELS_TTL)
        self._models_ttl = float(ttl) if ttl is not None else 0.0

        self._client: AsyncAnthropic | None = None
        # TTL cache for list_models.
        self._models_cache: list[ModelInfo] | None = None
        self._models_cache_at: float = 0.0

    # -- client lifecycle ----------------------------------------------------
    def _get_client(self) -> AsyncAnthropic:
        """Construct the SDK client lazily (so a missing key/SDK fails on use)."""
        if self._client is None:
            from anthropic import AsyncAnthropic  # lazy import — optional extra

            kwargs: dict[str, Any] = {}
            if self._api_key is not None:
                kwargs["api_key"] = self._api_key
            if self._base_url is not None:
                kwargs["base_url"] = self._base_url
            if self._timeout is not None:
                kwargs["timeout"] = self._timeout
            self._client = AsyncAnthropic(**kwargs)
        return self._client

    # -- discovery -----------------------------------------------------------
    async def list_models(self) -> list[ModelInfo]:
        """Fetch available models, served from a TTL cache when still fresh."""
        now = time.monotonic()
        if (
            self._models_cache is not None
            and self._models_ttl > 0
            and (now - self._models_cache_at) < self._models_ttl
        ):
            return self._models_cache

        client = self._get_client()
        models: list[ModelInfo] = []
        # client.models.list() auto-paginates when iterated.
        async for model in client.models.list():
            models.append(self._to_model_info(model))

        self._models_cache = models
        self._models_cache_at = now
        return models

    def _to_model_info(self, model: Any) -> ModelInfo:
        raw = self._as_dict(model)
        created = getattr(model, "created_at", None)
        if not isinstance(created, datetime):
            created = None
        return ModelInfo(
            id=getattr(model, "id", "") or "",
            provider=self.name,
            display_name=getattr(model, "display_name", None),
            created_at=created,
            raw=raw,
        )

    # -- generation ----------------------------------------------------------
    async def generate(self, req: GenerationRequest) -> GenerationResult:
        client = self._get_client()
        model = req.model or self._default_model

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": req.max_tokens,
            "system": req.system,
            "messages": [{"role": "user", "content": req.user}],
        }
        # IMPORTANT: do NOT send temperature/top_p by default — newer Claude
        # models reject them. Only forward what the caller explicitly opts into.
        kwargs.update(req.params)

        message = await client.messages.create(**kwargs)

        stop_reason = getattr(message, "stop_reason", None)
        refused = stop_reason == "refusal"

        text = "" if refused else self._extract_text(message)

        usage = getattr(message, "usage", None)
        input_tokens = getattr(usage, "input_tokens", None) if usage is not None else None
        output_tokens = getattr(usage, "output_tokens", None) if usage is not None else None

        return GenerationResult(
            text=text,
            model=getattr(message, "model", model) or model,
            provider=self.name,
            refused=refused,
            stop_reason=stop_reason,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            raw=self._as_dict(message),
        )

    @staticmethod
    def _extract_text(message: Any) -> str:
        parts: list[str] = []
        for block in getattr(message, "content", None) or []:
            if getattr(block, "type", None) == "text":
                parts.append(getattr(block, "text", "") or "")
        return "".join(parts)

    # -- health / lifecycle --------------------------------------------------
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

    # -- helpers -------------------------------------------------------------
    @staticmethod
    def _as_dict(obj: Any) -> dict[str, Any]:
        for attr in ("model_dump", "to_dict", "dict"):
            method = getattr(obj, attr, None)
            if callable(method):
                try:
                    result = method()
                except Exception:
                    continue
                if isinstance(result, dict):
                    return result
        return {}
