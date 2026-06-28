"""Tests for the Anthropic model provider with the SDK fully mocked.

The ``anthropic`` SDK is an optional extra and is NOT installed in the test
venv, so we never import it — instead we replace ``AnthropicProvider._get_client``
with a factory returning a hand-rolled fake async client.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from avatar.core.types import GenerationRequest, GenerationResult, ModelInfo
from avatar.models.anthropic import AnthropicProvider
from tests.contract import ModelContract

pytestmark = pytest.mark.asyncio


# --- fakes mimicking the anthropic SDK surface ------------------------------
class _FakeModel:
    def __init__(self, id: str, display_name: str | None = None) -> None:
        self.id = id
        self.display_name = display_name
        self.created_at = datetime(2026, 1, 1, tzinfo=UTC)

    def model_dump(self) -> dict[str, Any]:
        return {"id": self.id, "display_name": self.display_name}


class _AsyncModelList:
    """Async-iterable stand-in for client.models.list()."""

    def __init__(self, models: list[_FakeModel]) -> None:
        self._models = models

    def __aiter__(self):
        async def gen():
            for m in self._models:
                yield m

        return gen()


class _Usage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _TextBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _Message:
    def __init__(
        self,
        *,
        content: list[Any],
        stop_reason: str,
        model: str = "claude-opus-4-8",
        input_tokens: int = 11,
        output_tokens: int = 7,
    ) -> None:
        self.content = content
        self.stop_reason = stop_reason
        self.model = model
        self.usage = _Usage(input_tokens, output_tokens)

    def model_dump(self) -> dict[str, Any]:
        return {"stop_reason": self.stop_reason, "model": self.model}


class _Models:
    def __init__(self, models: list[_FakeModel]) -> None:
        self._models = models
        self.calls = 0

    def list(self) -> _AsyncModelList:
        self.calls += 1
        return _AsyncModelList(self._models)


class _Messages:
    def __init__(self, message: _Message) -> None:
        self._message = message
        self.last_kwargs: dict[str, Any] | None = None

    async def create(self, **kwargs: Any) -> _Message:
        self.last_kwargs = kwargs
        return self._message


class _FakeClient:
    def __init__(
        self,
        *,
        models: list[_FakeModel] | None = None,
        message: _Message | None = None,
    ) -> None:
        self.models = _Models(models or [_FakeModel("claude-opus-4-8", "Claude Opus 4.8")])
        self.messages = _Messages(
            message or _Message(content=[_TextBlock("hi")], stop_reason="end_turn")
        )
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


def _provider_with(client: _FakeClient, **settings: Any) -> AnthropicProvider:
    p = AnthropicProvider({"api_key": "sk-test", **settings})
    p._client = client  # inject our fake instead of building a real SDK client
    return p


# --- contract conformance ---------------------------------------------------
class TestAnthropicContract(ModelContract):
    async def make_model(self) -> AnthropicProvider:
        return _provider_with(_FakeClient())


# --- list_models ------------------------------------------------------------
async def test_list_models_maps_to_modelinfo() -> None:
    client = _FakeClient(
        models=[
            _FakeModel("claude-opus-4-8", "Claude Opus 4.8"),
            _FakeModel("claude-haiku-4-5", "Claude Haiku 4.5"),
        ]
    )
    provider = _provider_with(client)

    models = await provider.list_models()

    assert [m.id for m in models] == ["claude-opus-4-8", "claude-haiku-4-5"]
    assert all(isinstance(m, ModelInfo) for m in models)
    assert all(m.provider == "anthropic" for m in models)
    assert models[0].display_name == "Claude Opus 4.8"
    assert models[0].raw == {"id": "claude-opus-4-8", "display_name": "Claude Opus 4.8"}


async def test_list_models_caches_within_ttl() -> None:
    client = _FakeClient()
    provider = _provider_with(client, models_ttl=3600)

    await provider.list_models()
    await provider.list_models()

    assert client.models.calls == 1  # second call served from cache


async def test_list_models_ttl_zero_disables_cache() -> None:
    client = _FakeClient()
    provider = _provider_with(client, models_ttl=0)

    await provider.list_models()
    await provider.list_models()

    assert client.models.calls == 2


# --- generate ---------------------------------------------------------------
async def test_generate_normal() -> None:
    client = _FakeClient(
        message=_Message(
            content=[_TextBlock("Hello "), _TextBlock("world")],
            stop_reason="end_turn",
            input_tokens=42,
            output_tokens=9,
        )
    )
    provider = _provider_with(client)

    result = await provider.generate(
        GenerationRequest(system="be brief", user="say hi", max_tokens=64)
    )

    assert isinstance(result, GenerationResult)
    assert result.text == "Hello world"
    assert result.refused is False
    assert result.stop_reason == "end_turn"
    assert result.provider == "anthropic"
    assert result.input_tokens == 42
    assert result.output_tokens == 9


async def test_generate_does_not_send_sampling_params_by_default() -> None:
    client = _FakeClient()
    provider = _provider_with(client)

    await provider.generate(GenerationRequest(system="s", user="u", max_tokens=32))

    kwargs = client.messages.last_kwargs
    assert kwargs is not None
    assert "temperature" not in kwargs
    assert "top_p" not in kwargs
    assert kwargs["model"] == "claude-opus-4-8"
    assert kwargs["max_tokens"] == 32
    assert kwargs["messages"] == [{"role": "user", "content": "u"}]


async def test_generate_forwards_opt_in_params() -> None:
    client = _FakeClient()
    provider = _provider_with(client)

    await provider.generate(
        GenerationRequest(system="s", user="u", max_tokens=32, params={"temperature": 0.5})
    )

    assert client.messages.last_kwargs["temperature"] == 0.5


async def test_generate_refusal_sets_flag_and_skips_content() -> None:
    # A refusal: stop_reason == "refusal". We must not read content.
    client = _FakeClient(
        message=_Message(
            content=[_TextBlock("should be ignored")],
            stop_reason="refusal",
        )
    )
    provider = _provider_with(client)

    result = await provider.generate(GenerationRequest(system="s", user="dangerous", max_tokens=32))

    assert result.refused is True
    assert result.text == ""
    assert result.stop_reason == "refusal"


# --- lifecycle / health -----------------------------------------------------
class _BoomModels:
    def list(self) -> Any:
        raise RuntimeError("network down")


async def test_healthcheck_true_and_false() -> None:
    provider = _provider_with(_FakeClient())
    assert await provider.healthcheck() is True

    bad_client = _FakeClient()
    bad_client.models = _BoomModels()
    bad = _provider_with(bad_client)
    assert await bad.healthcheck() is False


async def test_aclose_closes_client() -> None:
    client = _FakeClient()
    provider = _provider_with(client)
    await provider.aclose()
    assert client.closed is True


async def test_module_imports_without_sdk() -> None:
    # Importing the module must not require the anthropic SDK.
    import importlib

    mod = importlib.import_module("avatar.models.anthropic")
    assert mod.AnthropicProvider.name == "anthropic"
