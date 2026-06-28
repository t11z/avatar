"""Tests for the Threads platform adapter.

All HTTP is mocked with an ``httpx.MockTransport``; no network and no SDK are
required. Tests cover the contract, the two-step create-container + publish
flow, reply shaping, lazy credential failure, and the gated mention reading.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from avatar.core.platform import PlatformAdapter
from avatar.core.types import Post, Ref
from avatar.platforms.threads import ThreadsAdapter
from tests.contract import PlatformContract

pytestmark = pytest.mark.asyncio

_SETTINGS: dict[str, Any] = {
    "id": "threads",
    "user_id": "u123",
    "access_token": "tok",
    "handle": "mybot",
}


def _make_adapter(handler, settings: dict[str, Any] | None = None) -> ThreadsAdapter:
    adapter = ThreadsAdapter(settings or dict(_SETTINGS))
    transport = httpx.MockTransport(handler)
    adapter._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        base_url="https://graph.threads.net/v1.0",
        transport=transport,
        timeout=5.0,
    )
    return adapter


def _two_step_handler(captured: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        path = request.url.path
        if path.endswith("/threads_publish"):
            return httpx.Response(200, json={"id": "pub999"})
        if path.endswith("/threads"):
            return httpx.Response(200, json={"id": "cont1"})
        return httpx.Response(404)

    return handler


class TestThreadsContract(PlatformContract):
    async def make_platform(self) -> PlatformAdapter:
        return _make_adapter(_two_step_handler([]))


async def test_capabilities() -> None:
    adapter = ThreadsAdapter(dict(_SETTINGS))
    caps = adapter.capabilities()
    assert caps.max_chars == 500
    assert caps.supports_reply is True
    assert caps.supports_media is False
    assert caps.can_poll_mentions is False


async def test_post_two_step_flow() -> None:
    captured: list[httpx.Request] = []
    adapter = _make_adapter(_two_step_handler(captured))

    result = await adapter.post(Post(platform="threads", text="hello threads"))

    assert result.post_id == "pub999"
    assert result.url == "https://www.threads.net/@mybot/post/pub999"

    # Step 1: create container.
    create = captured[0]
    assert create.method == "POST"
    assert create.url.path == "/v1.0/u123/threads"
    assert create.url.params.get("media_type") == "TEXT"
    assert create.url.params.get("text") == "hello threads"
    assert create.url.params.get("access_token") == "tok"

    # Step 2: publish container with the returned creation_id.
    publish = captured[1]
    assert publish.method == "POST"
    assert publish.url.path == "/v1.0/u123/threads_publish"
    assert publish.url.params.get("creation_id") == "cont1"
    assert publish.url.params.get("access_token") == "tok"

    await adapter.aclose()


async def test_reply_sets_reply_to_id() -> None:
    captured: list[httpx.Request] = []
    adapter = _make_adapter(_two_step_handler(captured))

    ref = Ref(platform="threads", post_id="parent42")
    result = await adapter.reply(Post(platform="threads", text="re"), ref)

    assert result.post_id == "pub999"
    create = captured[0]
    assert create.url.params.get("reply_to_id") == "parent42"
    assert create.url.params.get("text") == "re"
    await adapter.aclose()


async def test_post_without_credentials_fails_lazily() -> None:
    adapter = ThreadsAdapter({"id": "threads"})
    with pytest.raises(RuntimeError):
        await adapter.post(Post(platform="threads", text="hi"))


async def test_construction_tolerates_empty_settings() -> None:
    adapter = ThreadsAdapter({})
    assert adapter.name == "threads"
    assert adapter.capabilities().can_poll_mentions is False


async def test_stream_mentions_raises_not_implemented() -> None:
    adapter = ThreadsAdapter(dict(_SETTINGS))
    gen = adapter.stream_mentions()
    with pytest.raises(NotImplementedError):
        await gen.__anext__()


async def test_healthcheck_without_credentials_is_false() -> None:
    adapter = ThreadsAdapter({"id": "threads"})
    assert await adapter.healthcheck() is False


async def test_healthcheck_ok() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1.0/u123"
        return httpx.Response(200, json={"id": "u123"})

    adapter = _make_adapter(handler)
    assert await adapter.healthcheck() is True
    await adapter.aclose()
