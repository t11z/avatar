"""Store contract — durable state for dedup, history and scheduler buckets.

Treat trigger delivery as *at-least-once*: the ``seen``/``mark_seen`` pair is
the guard against double-posting, and recorded replies let a restart detect
"already answered" even if a handled-mark was lost.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from .types import PostResult, TriggerEvent


@runtime_checkable
class Store(Protocol):
    async def init(self) -> None: ...

    async def seen(self, event_id: str) -> bool:
        """Has this trigger event already been handled?"""
        ...

    async def mark_seen(self, event: TriggerEvent, result: PostResult | None) -> None:
        """Record that an event was handled (optionally with the produced post)."""
        ...

    async def record_post(self, result: PostResult, *, kind: str) -> None: ...

    async def posts_since(self, since: datetime) -> int:
        """Count posts published since ``since`` (for daily caps)."""
        ...

    async def last_user_reply(self, platform: str, author_id: str) -> datetime | None:
        """When did we last reply to this user (per-user cooldown)?"""
        ...

    async def get_cursor(self, key: str) -> str | None: ...

    async def set_cursor(self, key: str, value: str) -> None: ...

    async def aclose(self) -> None: ...
