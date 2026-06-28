"""Tests for the OpenAI model provider.

The ``openai`` SDK is an optional extra and is NOT installed in the test venv,
so we inject a fake ``AsyncOpenAI`` client by monkeypatching the provider's
client factory. No network and no real SDK are required.
"""

from __future__ import annotations

from typing import Any

import pytest

from avatar.core.types import GenerationRequest
from avatar.models.openai import OpenAIProvider
from tests.contract import ModelContract


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


class _FakeModelsPage:
    def __init__(self, data: list[dict[str, Any]]) -> None:
        self.data = [_FakeModelObj(d) for d in data]


class _FakeCompletion:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def model_dump(self) -> dict[str, Any]:
        return dict(self._data)


class _FakeModelsNamespace:
    def __init__(self, page: _FakeModelsPage) -> None:
        self._page = page
        self.list_calls = 0

    async def list(self) -> _FakeModelsPage:
        self.list_calls += 1
        return self._page


class _FakeChatCompletions:
    def __init__(self, completion: _FakeCompletion) -> None:
        self._completion = completion
        self.last_kwargs: dict[str, Any] | None = None

    async def create(self, **kwargs: Any) -> _FakeCompletion:
        self.last_kwargs = kwargs
        return self._completion


class _FakeChat:
    def __init__(self, completions: _FakeChatCompletions) -> None:
        self.completions = completions


class FakeAsyncOpenAI:
    def __init__(self) -> None:
        self.models = _FakeModelsNamespace(
            _FakeModelsPage(
                [
                    {"id": "gpt-4o", "created": 1700000000, "object": "model"},
                    {"id": "gpt-4o-mini", "created": 1700000001, "object": "model"},
                ]
            )
        )
        self.chat = _FakeChat(
            _FakeChatCompletions(
                _FakeCompletion(
                    {
                        "model": "gpt-4o-mini",
                        "choices": [
                            {
                                "finish_reason": "stop",
                                "message": {"role": "assistant", "content": "hi there"},
                            }
                        ],
                        "usage": {"prompt_tokens": 7, "completion_tokens": 3},
                    }
                )
            )
        )
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def _make_provider() -> tuple[OpenAIProvider, FakeAsyncOpenAI]:
    provider = OpenAIProvider({"api_key": "sk-test"})
    fake = FakeAsyncOpenAI()
    provider._get_client = lambda: fake  # type: ignore[method-assign]
    provider._client = fake
    return provider, fake


class TestOpenAIContract(ModelContract):
    async def make_model(self) -> OpenAIProvider:
        provider, _ = _make_provider()
        return provider


pytestmark = pytest.mark.asyncio


async def test_list_models_maps_fields() -> None:
    provider, _fake = _make_provider()
    models = await provider.list_models()
    assert [m.id for m in models] == ["gpt-4o", "gpt-4o-mini"]
    assert all(m.provider == "openai" for m in models)
    assert models[0].created_at is not None
    assert models[0].raw["object"] == "model"


async def test_list_models_caches() -> None:
    provider, fake = _make_provider()
    await provider.list_models()
    await provider.list_models()
    assert fake.models.list_calls == 1


async def test_generate_builds_messages_and_maps_usage() -> None:
    provider, fake = _make_provider()
    result = await provider.generate(
        GenerationRequest(system="be brief", user="say hi", max_tokens=16)
    )
    assert result.text == "hi there"
    assert result.provider == "openai"
    assert result.model == "gpt-4o-mini"
    assert result.stop_reason == "stop"
    assert result.refused is False
    assert result.input_tokens == 7
    assert result.output_tokens == 3

    kwargs = fake.chat.completions.last_kwargs
    assert kwargs is not None
    assert kwargs["max_tokens"] == 16
    assert kwargs["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "say hi"},
    ]


async def test_generate_respects_max_chars() -> None:
    provider, _ = _make_provider()
    result = await provider.generate(
        GenerationRequest(system="", user="hi", max_tokens=16, max_chars=2)
    )
    assert result.text == "hi"


async def test_generate_passthrough_params() -> None:
    provider, fake = _make_provider()
    await provider.generate(
        GenerationRequest(
            system="s", user="u", model="gpt-4o", max_tokens=8, params={"temperature": 0.1}
        )
    )
    kwargs = fake.chat.completions.last_kwargs
    assert kwargs is not None
    assert kwargs["model"] == "gpt-4o"
    assert kwargs["temperature"] == 0.1


async def test_construction_tolerates_missing_secrets() -> None:
    # Construction must not raise even without an api_key or any SDK installed.
    OpenAIProvider({})


async def test_missing_api_key_fails_lazily(monkeypatch: pytest.MonkeyPatch) -> None:
    # Inject a fake ``openai`` module so we exercise the api_key guard rather
    # than the (also-lazy) ModuleNotFoundError when the SDK is absent.
    import sys
    import types

    fake_module = types.ModuleType("openai")
    fake_module.AsyncOpenAI = FakeAsyncOpenAI  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", fake_module)

    provider = OpenAIProvider({})
    with pytest.raises(RuntimeError):
        provider._get_client()


async def test_aclose_closes_client() -> None:
    provider, fake = _make_provider()
    await provider.aclose()
    assert fake.closed is True
    assert provider._client is None
