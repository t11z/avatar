"""Google Gemini model provider.

Discovers models via the ``google-genai`` client and generates content through
the Gemini API. The ``google-genai`` SDK is an optional extra and is imported
lazily inside methods so this module imports cleanly even when the SDK is absent.

A safety-blocked or otherwise non-completing response is surfaced as a
``GenerationResult`` with ``refused=True`` rather than raising.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from avatar.core.registry import register_model
from avatar.core.types import (
    GenerationRequest,
    GenerationResult,
    ModelInfo,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from google.genai import Client

_DEFAULT_MODEL = "gemini-1.5-flash"
_MODELS_CACHE_TTL = 300.0

# finish_reason values that indicate the model declined / was blocked.
_REFUSAL_FINISH_REASONS = {"SAFETY", "RECITATION", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII"}


@register_model("gemini")
class GeminiProvider:
    """ModelProvider backed by Google's Gemini API via the ``google-genai`` SDK."""

    name = "gemini"

    def __init__(self, settings: Mapping[str, Any]) -> None:
        self._api_key: str | None = settings.get("api_key")
        self._default_model: str = settings.get("model") or _DEFAULT_MODEL
        self._cache_ttl: float = float(settings.get("models_cache_ttl", _MODELS_CACHE_TTL))

        self._client: Client | None = None
        self._models_cache: list[ModelInfo] | None = None
        self._models_cache_at: float = 0.0

    # -- client -----------------------------------------------------------
    def _get_client(self) -> Client:
        if self._client is None:
            from google import genai  # lazy import of optional SDK

            if not self._api_key:
                raise RuntimeError("gemini provider requires an 'api_key' setting")

            self._client = genai.Client(api_key=self._api_key)
        return self._client

    # -- discovery --------------------------------------------------------
    async def list_models(self) -> list[ModelInfo]:
        now = time.monotonic()
        if self._models_cache is not None and (now - self._models_cache_at) < self._cache_ttl:
            return self._models_cache

        client = self._get_client()
        page = await client.aio.models.list()

        models: list[ModelInfo] = []
        for item in page:
            raw = self._to_dict(item)
            raw_name = raw.get("name") or getattr(item, "name", "")
            model_id = self._strip_prefix(str(raw_name))
            display = raw.get("display_name") or getattr(item, "display_name", None)
            models.append(
                ModelInfo(
                    id=model_id,
                    provider=self.name,
                    display_name=str(display) if display else model_id,
                    raw=raw,
                )
            )

        self._models_cache = models
        self._models_cache_at = now
        return models

    # -- generation -------------------------------------------------------
    async def generate(self, req: GenerationRequest) -> GenerationResult:
        from google.genai import types as genai_types  # lazy import of optional SDK

        client = self._get_client()
        model = req.model or self._default_model

        config_kwargs: dict[str, Any] = {"max_output_tokens": req.max_tokens}
        if req.system:
            config_kwargs["system_instruction"] = req.system
        # Pass-through caller params (e.g. temperature, top_p) without clobbering.
        for key, value in req.params.items():
            config_kwargs.setdefault(key, value)

        config = genai_types.GenerateContentConfig(**config_kwargs)

        response = await client.aio.models.generate_content(
            model=model,
            contents=req.user,
            config=config,
        )
        raw = self._to_dict(response)

        stop_reason = self._finish_reason(response, raw)
        refused = self._is_refused(response, raw, stop_reason)

        text = "" if refused else (self._extract_text(response, raw) or "")
        input_tokens, output_tokens = self._usage(raw)

        if req.max_chars is not None and len(text) > req.max_chars:
            text = text[: req.max_chars]

        return GenerationResult(
            text=text,
            model=model,
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
        # google-genai's Client holds httpx clients but exposes no public async
        # close; drop the reference so a fresh client is built on next use.
        self._client = None

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def _strip_prefix(name: str) -> str:
        return name[len("models/") :] if name.startswith("models/") else name

    def _extract_text(self, response: Any, raw: dict[str, Any]) -> str:
        text = getattr(response, "text", None)
        if isinstance(text, str) and text:
            return text
        # Fall back to assembling candidate parts from the raw payload.
        for candidate in raw.get("candidates") or []:
            content = candidate.get("content") or {}
            parts = content.get("parts") or []
            assembled = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
            if assembled:
                return assembled
        return ""

    def _finish_reason(self, response: Any, raw: dict[str, Any]) -> str | None:
        candidates = raw.get("candidates") or []
        if candidates:
            reason = candidates[0].get("finish_reason")
            if reason is not None:
                return self._enum_name(reason)
        cands = getattr(response, "candidates", None) or []
        if cands:
            reason = getattr(cands[0], "finish_reason", None)
            if reason is not None:
                return self._enum_name(reason)
        return None

    def _is_refused(self, response: Any, raw: dict[str, Any], stop_reason: str | None) -> bool:
        # Prompt-level block (the whole request was rejected).
        feedback = raw.get("prompt_feedback") or {}
        if feedback.get("block_reason"):
            return True
        pf = getattr(response, "prompt_feedback", None)
        if pf is not None and getattr(pf, "block_reason", None):
            return True
        # Candidate-level safety/recitation stop.
        if stop_reason and stop_reason.upper() in _REFUSAL_FINISH_REASONS:
            return True
        return False

    @staticmethod
    def _usage(raw: dict[str, Any]) -> tuple[int | None, int | None]:
        usage = raw.get("usage_metadata") or {}
        return usage.get("prompt_token_count"), usage.get("candidates_token_count")

    @staticmethod
    def _enum_name(value: Any) -> str:
        name = getattr(value, "name", None)
        if isinstance(name, str):
            return name
        return str(value)

    @staticmethod
    def _to_dict(obj: Any) -> dict[str, Any]:
        for attr in ("model_dump", "to_dict", "to_json_dict", "dict"):
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
