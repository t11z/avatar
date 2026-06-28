"""Tests for the Ollama model provider, with httpx fully mocked (no network)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from avatar.core.types import GenerationRequest
from avatar.models.ollama import OllamaProvider
from tests.contract import ModelContract

_TAGS_RESPONSE = {
    "models": [
        {
            "name": "llama3:latest",
            "model": "llama3:latest",
            "modified_at": "2024-01-02T03:04:05.000000Z",
            "size": 123,
        },
        {"name": "mistral:7b", "modified_at": "not-a-date"},
    ]
}

_CHAT_RESPONSE = {
    "model": "llama3:latest",
    "message": {"role": "assistant", "content": "hi there"},
    "done": True,
    "done_reason": "stop",
    "prompt_eval_count": 11,
    "eval_count": 4,
}


def _make_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json=_TAGS_RESPONSE)
        if request.url.path == "/api/chat":
            return httpx.Response(200, json=_CHAT_RESPONSE)
        return httpx.Response(404, json={"error": "not found"})

    return httpx.MockTransport(handler)


def _patch_client(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    real_init = httpx.AsyncClient.__init__

    def init(self: httpx.AsyncClient, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("transport", transport)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", init)


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _make_transport())


class TestOllamaContract(ModelContract):
    async def make_model(self) -> OllamaProvider:
        # The contract suite runs without the per-test fixture, so install the
        # mock transport directly here.
        mp = pytest.MonkeyPatch()
        _patch_client(mp, _make_transport())
        return OllamaProvider({"host": "http://ollama.test:11434"})


@pytest.mark.usefixtures("patched")
async def test_default_host() -> None:
    provider = OllamaProvider({})
    assert provider._host == "http://localhost:11434"
    await provider.aclose()


@pytest.mark.usefixtures("patched")
async def test_list_models_maps_fields() -> None:
    provider = OllamaProvider({"host": "http://ollama.test:11434"})
    models = await provider.list_models()
    assert [m.id for m in models] == ["llama3:latest", "mistral:7b"]
    assert models[0].provider == "ollama"
    assert models[0].created_at is not None
    assert models[1].created_at is None  # unparseable date tolerated
    await provider.aclose()


@pytest.mark.usefixtures("patched")
async def test_list_models_cached() -> None:
    provider = OllamaProvider({"host": "http://ollama.test:11434"})
    first = await provider.list_models()
    second = await provider.list_models()
    assert first is second  # served from cache
    await provider.aclose()


@pytest.mark.usefixtures("patched")
async def test_generate_returns_text_and_usage() -> None:
    provider = OllamaProvider({"host": "http://ollama.test:11434"})
    result = await provider.generate(
        GenerationRequest(system="be brief", user="hi", model="llama3:latest", max_tokens=8)
    )
    assert result.text == "hi there"
    assert result.model == "llama3:latest"
    assert result.provider == "ollama"
    assert result.refused is False
    assert result.stop_reason == "stop"
    assert result.input_tokens == 11
    assert result.output_tokens == 4
    await provider.aclose()


@pytest.mark.usefixtures("patched")
async def test_generate_truncates_to_max_chars() -> None:
    provider = OllamaProvider({"host": "http://ollama.test:11434"})
    result = await provider.generate(GenerationRequest(system="", user="hi", max_chars=2))
    assert result.text == "hi"
    await provider.aclose()


@pytest.mark.usefixtures("patched")
async def test_healthcheck_ok() -> None:
    provider = OllamaProvider({"host": "http://ollama.test:11434"})
    assert await provider.healthcheck() is True
    await provider.aclose()


async def test_healthcheck_false_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    _patch_client(monkeypatch, httpx.MockTransport(handler))
    provider = OllamaProvider({"host": "http://ollama.test:11434"})
    assert await provider.healthcheck() is False
    await provider.aclose()
