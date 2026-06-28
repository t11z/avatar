"""Tests for the X (Twitter) platform adapter.

All HTTP is mocked with an ``httpx.MockTransport``; no network and no real SDK
(``tweepy`` is not installed) are required. Tests cover the contract, capability
gating (no bearer -> ``can_poll_mentions`` False, ``stream_mentions`` raises),
and post/reply request shaping.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from avatar.core.platform import PlatformAdapter
from avatar.core.types import Post, Ref
from avatar.platforms.x import XAdapter
from tests.contract import PlatformContract

pytestmark = pytest.mark.asyncio

_OAUTH_SETTINGS: dict[str, Any] = {
    "id": "x",
    "api_key": "ck",
    "api_secret": "cs",
    "access_token": "at",
    "access_secret": "as",
    "handle": "mybot",
    "poll_interval_seconds": 0,
}


def _make_adapter(handler, settings: dict[str, Any] | None = None) -> XAdapter:
    adapter = XAdapter(settings or dict(_OAUTH_SETTINGS))
    transport = httpx.MockTransport(handler)
    adapter._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        base_url="https://api.twitter.com/2",
        transport=transport,
        timeout=5.0,
    )
    return adapter


class TestXContract(PlatformContract):
    async def make_platform(self) -> PlatformAdapter:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(201, json={"data": {"id": "12345", "text": "contract test post"}})

        return _make_adapter(handler)


async def test_capabilities_no_bearer_disables_polling() -> None:
    adapter = XAdapter(dict(_OAUTH_SETTINGS))
    caps = adapter.capabilities()
    assert caps.max_chars == 280
    assert caps.supports_reply is True
    assert caps.can_poll_mentions is False


async def test_capabilities_with_bearer_enables_polling() -> None:
    settings = dict(_OAUTH_SETTINGS, bearer_token="bt")
    adapter = XAdapter(settings)
    assert adapter.capabilities().can_poll_mentions is True


async def test_stream_mentions_without_bearer_raises() -> None:
    adapter = XAdapter(dict(_OAUTH_SETTINGS))
    gen = adapter.stream_mentions()
    with pytest.raises(NotImplementedError):
        await gen.__anext__()


async def test_post_creates_tweet() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={"data": {"id": "999", "text": "hi"}})

    adapter = _make_adapter(handler)
    result = await adapter.post(Post(platform="x", text="hi"))
    assert result.post_id == "999"
    assert result.url == "https://x.com/mybot/status/999"
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/tweets")
    assert captured["body"] == {"text": "hi"}
    assert captured["auth"].startswith("OAuth ")
    await adapter.aclose()


async def test_reply_includes_in_reply_to() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={"data": {"id": "1000"}})

    adapter = _make_adapter(handler)
    ref = Ref(platform="x", post_id="555")
    result = await adapter.reply(Post(platform="x", text="re"), ref)
    assert result.post_id == "1000"
    assert captured["body"]["text"] == "re"
    assert captured["body"]["reply"] == {"in_reply_to_tweet_id": "555"}
    await adapter.aclose()


async def test_post_without_oauth_fails_lazily() -> None:
    # Construction tolerates missing secrets; the call fails clearly.
    adapter = XAdapter({"id": "x"})
    with pytest.raises(RuntimeError):
        await adapter.post(Post(platform="x", text="hi"))


async def test_construction_tolerates_empty_settings() -> None:
    adapter = XAdapter({})
    assert adapter.name == "x"
    assert adapter.capabilities().can_poll_mentions is False


async def test_stream_mentions_polls_with_since_id(monkeypatch) -> None:
    # Don't actually wait the poll interval between polls.
    async def _no_sleep(*_a, **_k) -> None:
        return None

    monkeypatch.setattr("avatar.platforms.x.asyncio.sleep", _no_sleep)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/users/by/username/mybot"):
            return httpx.Response(200, json={"data": {"id": "777", "username": "mybot"}})
        if "/mentions" in path:
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "id": "20",
                                "text": "hey @mybot",
                                "author_id": "1",
                                "conversation_id": "20",
                                "created_at": "2026-06-28T10:00:00.000Z",
                            },
                            {
                                "id": "19",
                                "text": "older @mybot",
                                "author_id": "2",
                                "conversation_id": "19",
                            },
                        ],
                        "includes": {
                            "users": [
                                {"id": "1", "username": "alice"},
                                {"id": "2", "username": "bob"},
                            ]
                        },
                    },
                )
            # Second poll must carry since_id from the newest tweet.
            assert request.url.params.get("since_id") == "20"
            raise _StopPolling

        return httpx.Response(404)

    settings = dict(_OAUTH_SETTINGS, bearer_token="bt")
    adapter = _make_adapter(handler, settings)

    received = []
    with pytest.raises(_StopPolling):
        async for mention in adapter.stream_mentions():
            received.append(mention)

    # Emitted oldest-first.
    assert [m.post_id for m in received] == ["19", "20"]
    assert received[0].author_handle == "bob"
    assert received[1].author_handle == "alice"
    assert received[1].created_at is not None
    assert received[1].url == "https://x.com/alice/status/20"
    await adapter.aclose()


class _StopPolling(Exception):
    pass
