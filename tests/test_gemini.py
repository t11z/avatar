"""Tests for the Gemini model provider.

The ``google-genai`` SDK is an optional extra and is NOT installed in the test
venv. We inject a fake client by monkeypatching the provider's client factory,
and a fake ``google.genai.types`` module for ``generate``. No network and no
real SDK are required.
"""

from __future__ import annotations

import sys
import types as pytypes
from typing import Any

import pytest

from avatar.core.types import GenerationRequest
from avatar.models.gemini import GeminiProvider
from tests.contract import ModelContract


# --- fake google.genai.types ------------------------------------------------
class _FakeGenerateContentConfig:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


def _install_fake_genai_types(monkeypatch: pytest.MonkeyPatch) -> None:
    google_mod = sys.modules.get("google") or pytypes.ModuleType("google")
    genai_mod = pytypes.ModuleType("google.genai")
    types_mod = pytypes.ModuleType("google.genai.types")
    types_mod.GenerateContentConfig = _FakeGenerateContentConfig  # type: ignore[attr-defined]
    genai_mod.types = types_mod  # type: ignore[attr-defined]
    google_mod.genai = genai_mod  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google", google_mod)
    monkeypatch.setitem(sys.modules, "google.genai", genai_mod)
    monkeypatch.setitem(sys.modules, "google.genai.types", types_mod)


# --- fake client objects ----------------------------------------------------
class _FakeModelObj:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def model_dump(self) -> dict[str, Any]:
        return dict(self._data)

    def __getattr__(self, item: str) -> Any:
        try:
            return self._data[item]
        except KeyError as exc:  # pragma: no cover - defensive
            raise AttributeError(item) from exc


class _FakeResponse:
    def __init__(self, data: dict[str, Any], text: str | None = None) -> None:
        self._data = data
        self.text = text

    def model_dump(self) -> dict[str, Any]:
        return dict(self._data)


class _FakeModelsNamespace:
    def __init__(self, models: list[_FakeModelObj], response: _FakeResponse) -> None:
        self._models = models
        self._response = response
        self.list_calls = 0
        self.last_generate_kwargs: dict[str, Any] | None = None

    async def list(self) -> list[_FakeModelObj]:
        self.list_calls += 1
        return self._models

    async def generate_content(self, **kwargs: Any) -> _FakeResponse:
        self.last_generate_kwargs = kwargs
        return self._response


class _FakeAio:
    def __init__(self, models: _FakeModelsNamespace) -> None:
        self.models = models


class FakeGenaiClient:
    def __init__(self, response: _FakeResponse | None = None) -> None:
        models = _FakeModelsNamespace(
            [
                _FakeModelObj(
                    {"name": "models/gemini-1.5-flash", "display_name": "Gemini 1.5 Flash"}
                ),
                _FakeModelObj({"name": "models/gemini-1.5-pro", "display_name": "Gemini 1.5 Pro"}),
            ],
            response
            or _FakeResponse(
                {
                    "candidates": [
                        {
                            "finish_reason": "STOP",
                            "content": {"parts": [{"text": "hi there"}]},
                        }
                    ],
                    "usage_metadata": {
                        "prompt_token_count": 7,
                        "candidates_token_count": 3,
                    },
                },
                text="hi there",
            ),
        )
        self.aio = _FakeAio(models)


def _make_provider(response: _FakeResponse | None = None) -> tuple[GeminiProvider, FakeGenaiClient]:
    provider = GeminiProvider({"api_key": "key-test"})
    fake = FakeGenaiClient(response)
    provider._get_client = lambda: fake  # type: ignore[method-assign]
    provider._client = fake
    return provider, fake


class TestGeminiContract(ModelContract):
    async def make_model(self) -> GeminiProvider:
        # Contract test_generate calls generate(), which needs the fake types module.
        import google.genai.types as _t  # noqa: F401  (ensure import path resolvable)

        provider, _ = _make_provider()
        return provider

    @pytest.fixture(autouse=True)
    def _patch_types(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_genai_types(monkeypatch)


pytestmark = pytest.mark.asyncio


async def test_list_models_maps_and_strips_prefix() -> None:
    provider, _ = _make_provider()
    models = await provider.list_models()
    assert [m.id for m in models] == ["gemini-1.5-flash", "gemini-1.5-pro"]
    assert all(m.provider == "gemini" for m in models)
    assert models[0].display_name == "Gemini 1.5 Flash"
    assert models[0].raw["name"] == "models/gemini-1.5-flash"


async def test_list_models_caches() -> None:
    provider, fake = _make_provider()
    await provider.list_models()
    await provider.list_models()
    assert fake.aio.models.list_calls == 1


async def test_generate_builds_config_and_maps_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_genai_types(monkeypatch)
    provider, fake = _make_provider()
    result = await provider.generate(
        GenerationRequest(system="be brief", user="say hi", max_tokens=16)
    )
    assert result.text == "hi there"
    assert result.provider == "gemini"
    assert result.model == "gemini-1.5-flash"
    assert result.stop_reason == "STOP"
    assert result.refused is False
    assert result.input_tokens == 7
    assert result.output_tokens == 3

    kwargs = fake.aio.models.last_generate_kwargs
    assert kwargs is not None
    assert kwargs["model"] == "gemini-1.5-flash"
    assert kwargs["contents"] == "say hi"
    config = kwargs["config"]
    assert config.kwargs["max_output_tokens"] == 16
    assert config.kwargs["system_instruction"] == "be brief"


async def test_generate_respects_max_chars(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_genai_types(monkeypatch)
    provider, _ = _make_provider()
    result = await provider.generate(
        GenerationRequest(system="", user="hi", max_tokens=16, max_chars=2)
    )
    assert result.text == "hi"


async def test_generate_passthrough_params(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_genai_types(monkeypatch)
    provider, fake = _make_provider()
    await provider.generate(
        GenerationRequest(
            system="s", user="u", model="gemini-1.5-pro", max_tokens=8, params={"temperature": 0.1}
        )
    )
    kwargs = fake.aio.models.last_generate_kwargs
    assert kwargs is not None
    assert kwargs["model"] == "gemini-1.5-pro"
    assert kwargs["config"].kwargs["temperature"] == 0.1


async def test_generate_safety_block_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_genai_types(monkeypatch)
    response = _FakeResponse(
        {
            "candidates": [{"finish_reason": "SAFETY", "content": {"parts": []}}],
            "usage_metadata": {"prompt_token_count": 5, "candidates_token_count": 0},
        },
        text=None,
    )
    provider, _ = _make_provider(response)
    result = await provider.generate(GenerationRequest(system="s", user="something", max_tokens=16))
    assert result.refused is True
    assert result.text == ""
    assert result.stop_reason == "SAFETY"


async def test_generate_prompt_block_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_genai_types(monkeypatch)
    response = _FakeResponse(
        {
            "prompt_feedback": {"block_reason": "SAFETY"},
            "candidates": [],
        },
        text=None,
    )
    provider, _ = _make_provider(response)
    result = await provider.generate(GenerationRequest(system="", user="x", max_tokens=8))
    assert result.refused is True
    assert result.text == ""


async def test_construction_tolerates_missing_secrets() -> None:
    GeminiProvider({})


async def test_missing_api_key_fails_lazily(monkeypatch: pytest.MonkeyPatch) -> None:
    # Inject a fake ``google.genai`` so we exercise the api_key guard rather than
    # the (also-lazy) ModuleNotFoundError when the SDK is absent.
    google_mod = sys.modules.get("google") or pytypes.ModuleType("google")
    genai_mod = pytypes.ModuleType("google.genai")
    genai_mod.Client = lambda **kw: object()  # type: ignore[attr-defined]
    google_mod.genai = genai_mod  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google", google_mod)
    monkeypatch.setitem(sys.modules, "google.genai", genai_mod)

    provider = GeminiProvider({})
    with pytest.raises(RuntimeError):
        provider._get_client()


async def test_aclose_drops_client() -> None:
    provider, _ = _make_provider()
    await provider.aclose()
    assert provider._client is None
