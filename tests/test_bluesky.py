"""Tests for the Bluesky platform adapter (atproto SDK mocked)."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from avatar.core.types import Post, PostResult, Ref
from avatar.platforms.bluesky import BlueskyAdapter
from tests.contract import PlatformContract


class _FakeAuthor:
    def __init__(self, handle: str, did: str) -> None:
        self.handle = handle
        self.did = did


class _FakeRecord:
    def __init__(self, text: str, created_at: str | None = None) -> None:
        self.text = text
        self.created_at = created_at
        self.reply = None


class _FakeNotification:
    def __init__(
        self,
        reason: str,
        uri: str,
        cid: str,
        author: _FakeAuthor,
        record: _FakeRecord,
    ) -> None:
        self.reason = reason
        self.uri = uri
        self.cid = cid
        self.author = author
        self.record = record


class _FakeNotificationResponse:
    def __init__(self, notifications: list[_FakeNotification]) -> None:
        self.notifications = notifications


class _FakeSendResponse:
    def __init__(self, uri: str, cid: str) -> None:
        self.uri = uri
        self.cid = cid


class _FakeNotificationNS:
    def __init__(self, response: _FakeNotificationResponse) -> None:
        self._response = response
        self.calls = 0

    def list_notifications(self) -> _FakeNotificationResponse:
        self.calls += 1
        return self._response


class _FakeClient:
    """Stand-in for atproto.Client."""

    def __init__(self, notifications: list[_FakeNotification] | None = None) -> None:
        self.login_calls: list[tuple[str, str]] = []
        self.send_calls: list[dict[str, Any]] = []
        ns = _FakeNotificationNS(_FakeNotificationResponse(notifications or []))
        self.app = types.SimpleNamespace(bsky=types.SimpleNamespace(notification=ns))

    def login(self, handle: str, password: str) -> None:
        self.login_calls.append((handle, password))

    def send_post(self, text: str, reply_to: Any = None) -> _FakeSendResponse:
        self.send_calls.append({"text": text, "reply_to": reply_to})
        return _FakeSendResponse(uri="at://did:plc:bot/app.bsky.feed.post/abc123", cid="cid-xyz")


def _install_fake_atproto(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a fake ``atproto`` module so lazy imports resolve without the SDK."""

    class _StrongRefMain:
        def __init__(self, uri: str, cid: str) -> None:
            self.uri = uri
            self.cid = cid

    class _ReplyRef:
        def __init__(self, parent: Any, root: Any) -> None:
            self.parent = parent
            self.root = root

    models = types.SimpleNamespace(
        ComAtprotoRepoStrongRef=types.SimpleNamespace(Main=_StrongRefMain),
        AppBskyFeedPost=types.SimpleNamespace(ReplyRef=_ReplyRef),
    )
    fake = types.ModuleType("atproto")
    fake.Client = _FakeClient  # type: ignore[attr-defined]
    fake.models = models  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "atproto", fake)


def _make_adapter(
    monkeypatch: pytest.MonkeyPatch,
    notifications: list[_FakeNotification] | None = None,
) -> tuple[BlueskyAdapter, _FakeClient]:
    _install_fake_atproto(monkeypatch)
    adapter = BlueskyAdapter(
        {
            "id": "bluesky",
            "handle": "bot.bsky.social",
            "app_password": "secret",
            "poll_interval_seconds": 0,
        }
    )
    client = _FakeClient(notifications=notifications)
    monkeypatch.setattr(adapter, "_build_client", lambda: client)
    return adapter, client


class TestBlueskyContract(PlatformContract):
    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._monkeypatch = monkeypatch

    async def make_platform(self) -> BlueskyAdapter:
        adapter, _ = _make_adapter(self._monkeypatch)
        return adapter


async def test_construction_needs_no_network() -> None:
    # No fake atproto installed, no settings: must not raise on import/construct.
    adapter = BlueskyAdapter({"id": "bsky"})
    assert adapter.name == "bsky"
    caps = adapter.capabilities()
    assert caps.max_chars == 300
    assert caps.can_poll_mentions is True


async def test_post_calls_client(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, client = _make_adapter(monkeypatch)
    result = await adapter.post(Post(platform="bluesky", text="hello world"))
    assert isinstance(result, PostResult)
    assert client.login_calls == [("bot.bsky.social", "secret")]
    assert client.send_calls[0]["text"] == "hello world"
    assert client.send_calls[0]["reply_to"] is None
    assert result.post_id == "abc123"
    assert result.url == "https://bsky.app/profile/did:plc:bot/post/abc123"


async def test_reply_passes_refs(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, client = _make_adapter(monkeypatch)
    ref = Ref(
        platform="bluesky",
        post_id="parent",
        uri="at://did:plc:other/app.bsky.feed.post/parent",
        cid="parent-cid",
    )
    await adapter.reply(Post(platform="bluesky", text="a reply"), ref)
    reply_to = client.send_calls[0]["reply_to"]
    assert reply_to is not None
    assert reply_to.parent.uri == "at://did:plc:other/app.bsky.feed.post/parent"
    assert reply_to.parent.cid == "parent-cid"


async def test_login_happens_once(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, client = _make_adapter(monkeypatch)
    await adapter.post(Post(platform="bluesky", text="one"))
    await adapter.post(Post(platform="bluesky", text="two"))
    assert len(client.login_calls) == 1


async def test_healthcheck(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = _make_adapter(monkeypatch)
    assert await adapter.healthcheck() is True

    missing = BlueskyAdapter({"id": "bluesky"})
    assert await missing.healthcheck() is False


async def test_ensure_client_requires_credentials() -> None:
    adapter = BlueskyAdapter({"id": "bluesky"})
    with pytest.raises(RuntimeError):
        await adapter._ensure_client()


async def test_stream_mentions_maps_notification(monkeypatch: pytest.MonkeyPatch) -> None:
    note = _FakeNotification(
        reason="mention",
        uri="at://did:plc:author/app.bsky.feed.post/men01",
        cid="men-cid",
        author=_FakeAuthor(handle="alice.bsky.social", did="did:plc:author"),
        record=_FakeRecord(text="@bot hello", created_at="2026-06-28T12:00:00Z"),
    )
    other = _FakeNotification(
        reason="like",
        uri="at://did:plc:author/app.bsky.feed.like/like01",
        cid="like-cid",
        author=_FakeAuthor(handle="bob.bsky.social", did="did:plc:bob"),
        record=_FakeRecord(text=""),
    )
    adapter, _ = _make_adapter(monkeypatch, notifications=[note, other])

    # The adapter seeds its seen-cursor on the first poll (no emit). The helper
    # returns an empty first poll so the mention shows up as new afterwards, and
    # patches asyncio.sleep so the generator advances without real delay.
    mention = await _first_mention(monkeypatch, adapter)

    assert mention.platform == "bluesky"
    assert mention.author_handle == "alice.bsky.social"
    assert mention.author_id == "did:plc:author"
    assert mention.text == "@bot hello"
    assert mention.post_id == "men01"
    assert mention.url == "https://bsky.app/profile/did:plc:author/post/men01"
    # .to_ref() must reconstruct uri/cid from raw.
    ref = mention.to_ref()
    assert ref.uri == "at://did:plc:author/app.bsky.feed.post/men01"
    assert ref.cid == "men-cid"


async def _first_mention(monkeypatch: pytest.MonkeyPatch, adapter: BlueskyAdapter):
    """Drive stream_mentions until the first Mention is yielded.

    The adapter seeds its seen-cursor on the first poll (no emit), so the test
    notification must appear as *new* on a later poll. We simulate that by
    returning an empty list on the first poll and the notification afterwards.
    """
    client = await adapter._ensure_client()
    ns = client.app.bsky.notification
    full_response = ns._response
    empty = _FakeNotificationResponse([])

    state = {"first": True}

    def _list() -> Any:
        if state["first"]:
            state["first"] = False
            return empty
        return full_response

    ns.list_notifications = _list  # type: ignore[method-assign]

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("avatar.platforms.bluesky.asyncio.sleep", _no_sleep)

    async for mention in adapter.stream_mentions():
        return mention
    raise AssertionError("no mention emitted")
