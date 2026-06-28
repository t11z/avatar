"""In-memory store — for tests and pure scheduled posting (no restart durability)."""

from __future__ import annotations

from datetime import UTC, datetime

from ..core.types import PostResult, TriggerEvent


class MemoryStore:
    def __init__(self) -> None:
        self._seen: set[str] = set()
        self._posts: list[tuple[datetime, str]] = []
        self._user_replies: dict[tuple[str, str], datetime] = {}
        self._cursors: dict[str, str] = {}

    async def init(self) -> None:
        return None

    async def seen(self, event_id: str) -> bool:
        return event_id in self._seen

    async def mark_seen(self, event: TriggerEvent, result: PostResult | None) -> None:
        self._seen.add(event.id)
        if result and event.mention and event.mention.author_id:
            self._user_replies[(event.mention.platform, event.mention.author_id)] = datetime.now(
                UTC
            )

    async def record_post(self, result: PostResult, *, kind: str) -> None:
        self._posts.append((result.posted_at or datetime.now(UTC), result.platform))

    async def posts_since(self, since: datetime) -> int:
        return sum(1 for ts, _ in self._posts if ts >= since)

    async def last_user_reply(self, platform: str, author_id: str) -> datetime | None:
        return self._user_replies.get((platform, author_id))

    async def get_cursor(self, key: str) -> str | None:
        return self._cursors.get(key)

    async def set_cursor(self, key: str, value: str) -> None:
        self._cursors[key] = value

    async def aclose(self) -> None:
        return None
