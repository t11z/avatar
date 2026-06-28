"""SQLite-backed store via aiosqlite. Durable dedup, history and cursors."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from ..core.types import PostResult, TriggerEvent

_SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS handled_triggers (
    event_id   TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    platform   TEXT,
    post_id    TEXT,
    handled_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS post_history (
    post_id   TEXT NOT NULL,
    platform  TEXT NOT NULL,
    kind      TEXT NOT NULL,
    url       TEXT,
    posted_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_post_history_time ON post_history (posted_at);

CREATE TABLE IF NOT EXISTS user_replies (
    platform   TEXT NOT NULL,
    author_id  TEXT NOT NULL,
    replied_at TEXT NOT NULL,
    PRIMARY KEY (platform, author_id)
);

CREATE TABLE IF NOT EXISTS cursors (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


class SQLiteStore:
    def __init__(self, path: str) -> None:
        self._path = path
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        await self._db.executescript(_SCHEMA)
        async with self._db.execute("SELECT version FROM schema_version") as cur:
            row = await cur.fetchone()
        if row is None:
            await self._db.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (_SCHEMA_VERSION,)
            )
        await self._db.commit()

    @property
    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("store not initialised; call init() first")
        return self._db

    async def seen(self, event_id: str) -> bool:
        async with self._conn.execute(
            "SELECT 1 FROM handled_triggers WHERE event_id = ?", (event_id,)
        ) as cur:
            return await cur.fetchone() is not None

    async def mark_seen(self, event: TriggerEvent, result: PostResult | None) -> None:
        now = _iso(datetime.now(UTC))
        await self._conn.execute(
            "INSERT OR REPLACE INTO handled_triggers "
            "(event_id, kind, platform, post_id, handled_at) VALUES (?, ?, ?, ?, ?)",
            (event.id, str(event.kind), event.platform, result.post_id if result else None, now),
        )
        if result and event.mention and event.mention.author_id:
            await self._conn.execute(
                "INSERT OR REPLACE INTO user_replies "
                "(platform, author_id, replied_at) VALUES (?, ?, ?)",
                (event.mention.platform, event.mention.author_id, now),
            )
        await self._conn.commit()

    async def record_post(self, result: PostResult, *, kind: str) -> None:
        await self._conn.execute(
            "INSERT INTO post_history (post_id, platform, kind, url, posted_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                result.post_id,
                result.platform,
                kind,
                result.url,
                _iso(result.posted_at or datetime.now(UTC)),
            ),
        )
        await self._conn.commit()

    async def posts_since(self, since: datetime) -> int:
        async with self._conn.execute(
            "SELECT COUNT(*) FROM post_history WHERE posted_at >= ?", (_iso(since),)
        ) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def last_user_reply(self, platform: str, author_id: str) -> datetime | None:
        async with self._conn.execute(
            "SELECT replied_at FROM user_replies WHERE platform = ? AND author_id = ?",
            (platform, author_id),
        ) as cur:
            row = await cur.fetchone()
        return datetime.fromisoformat(row[0]) if row else None

    async def get_cursor(self, key: str) -> str | None:
        async with self._conn.execute("SELECT value FROM cursors WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
        return row[0] if row else None

    async def set_cursor(self, key: str, value: str) -> None:
        await self._conn.execute(
            "INSERT OR REPLACE INTO cursors (key, value) VALUES (?, ?)", (key, value)
        )
        await self._conn.commit()

    async def aclose(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None
