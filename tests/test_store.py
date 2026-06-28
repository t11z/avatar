from __future__ import annotations

from datetime import UTC, datetime, timedelta

from avatar.core.types import PostResult, TriggerEvent, TriggerKind
from avatar.store.sqlite import SQLiteStore


def _event(event_id="e1") -> TriggerEvent:
    return TriggerEvent(id=event_id, kind=TriggerKind.SCHEDULED, platform="fake")


async def test_dedup_survives_reconnect(tmp_path):
    path = str(tmp_path / "avatar.db")
    store = SQLiteStore(path)
    await store.init()
    assert not await store.seen("e1")
    await store.mark_seen(_event("e1"), PostResult(platform="fake", post_id="p1"))
    assert await store.seen("e1")
    await store.aclose()

    # Simulate a restart: a fresh connection to the same file.
    store2 = SQLiteStore(path)
    await store2.init()
    assert await store2.seen("e1")
    await store2.aclose()


async def test_posts_since_counts(tmp_path):
    store = SQLiteStore(str(tmp_path / "a.db"))
    await store.init()
    await store.record_post(
        PostResult(platform="fake", post_id="p1", posted_at=datetime.now(UTC)), kind="scheduled"
    )
    yesterday = datetime.now(UTC) - timedelta(days=1)
    assert await store.posts_since(yesterday) == 1
    tomorrow = datetime.now(UTC) + timedelta(days=1)
    assert await store.posts_since(tomorrow) == 0
    await store.aclose()


async def test_cursor_roundtrip(tmp_path):
    store = SQLiteStore(str(tmp_path / "a.db"))
    await store.init()
    assert await store.get_cursor("bsky") is None
    await store.set_cursor("bsky", "cursor-123")
    assert await store.get_cursor("bsky") == "cursor-123"
    await store.aclose()
