"""Bluesky (AT Protocol) platform adapter.

Wraps the ``atproto`` SDK behind the :class:`PlatformAdapter` protocol. The SDK
is imported lazily inside methods so this module imports even when ``atproto``
is not installed (it is declared as an optional ``platforms`` extra). The client
authenticates lazily on first use, so construction needs no network access.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from datetime import datetime
from typing import Any

import structlog

from avatar.core.registry import register_platform
from avatar.core.types import Capabilities, Mention, Post, PostResult, Ref

log = structlog.get_logger(__name__)

_DEFAULT_POLL_INTERVAL = 60.0


@register_platform("bluesky")
class BlueskyAdapter:
    """Platform adapter for Bluesky using the AT Protocol."""

    def __init__(self, settings: Mapping[str, Any]) -> None:
        self.settings = dict(settings)
        self.name = str(self.settings.get("id") or "bluesky")
        self._handle = self.settings.get("handle")
        self._app_password = self.settings.get("app_password")
        self._poll_interval = float(
            self.settings.get("poll_interval_seconds") or _DEFAULT_POLL_INTERVAL
        )
        self._client: Any | None = None

    # -- capabilities --------------------------------------------------------
    def capabilities(self) -> Capabilities:
        return Capabilities(
            max_chars=300,
            supports_reply=True,
            supports_media=False,
            can_poll_mentions=True,
        )

    # -- auth ----------------------------------------------------------------
    def _build_client(self) -> Any:
        """Construct an atproto client (lazy import)."""
        from atproto import Client

        return Client()

    async def _ensure_client(self) -> Any:
        """Authenticate lazily on first use and cache the client."""
        if self._client is not None:
            return self._client
        if not self._handle or not self._app_password:
            raise RuntimeError(
                "Bluesky adapter requires 'handle' and 'app_password' settings to authenticate"
            )
        client = self._build_client()

        def _login() -> Any:
            return client.login(self._handle, self._app_password)

        await asyncio.to_thread(_login)
        self._client = client
        return client

    # -- posting -------------------------------------------------------------
    async def post(self, content: Post) -> PostResult:
        client = await self._ensure_client()

        def _send() -> Any:
            return client.send_post(text=content.text)

        resp = await asyncio.to_thread(_send)
        return self._to_post_result(resp)

    async def reply(self, content: Post, in_reply_to: Ref) -> PostResult:
        client = await self._ensure_client()
        reply_ref = self._build_reply_ref(in_reply_to)

        def _send() -> Any:
            return client.send_post(text=content.text, reply_to=reply_ref)

        resp = await asyncio.to_thread(_send)
        return self._to_post_result(resp)

    def _build_reply_ref(self, ref: Ref) -> Any:
        """Build the atproto reply ref from a :class:`Ref`.

        The root falls back to the parent when no distinct root is known.
        """
        from atproto import models

        parent = models.ComAtprotoRepoStrongRef.Main(uri=ref.uri or "", cid=ref.cid or "")
        return models.AppBskyFeedPost.ReplyRef(parent=parent, root=parent)

    def _to_post_result(self, resp: Any) -> PostResult:
        uri = getattr(resp, "uri", None)
        post_id = self._rkey_from_uri(uri) if uri else (uri or "")
        url = self._web_url(uri) if uri else None
        return PostResult(platform=self.name, post_id=post_id, url=url)

    @staticmethod
    def _rkey_from_uri(uri: str) -> str:
        # at://did/app.bsky.feed.post/<rkey>
        return uri.rsplit("/", 1)[-1] if uri else uri

    def _web_url(self, uri: str) -> str | None:
        # at://<did>/app.bsky.feed.post/<rkey> -> https://bsky.app/profile/<did>/post/<rkey>
        try:
            _, rest = uri.split("at://", 1)
            did, _collection, rkey = rest.split("/", 2)
        except ValueError:
            return None
        return f"https://bsky.app/profile/{did}/post/{rkey}"

    # -- mentions ------------------------------------------------------------
    async def stream_mentions(self) -> AsyncIterator[Mention]:
        client = await self._ensure_client()
        seen: set[str] = set()
        first_pass = True

        while True:

            def _list() -> Any:
                return client.app.bsky.notification.list_notifications()

            try:
                resp = await asyncio.to_thread(_list)
            except Exception as exc:  # keep polling on transient errors
                log.warning("bluesky.list_notifications_failed", error=str(exc))
                await asyncio.sleep(self._poll_interval)
                continue

            notifications = getattr(resp, "notifications", None) or []
            for note in notifications:
                if getattr(note, "reason", None) != "mention":
                    continue
                uri = getattr(note, "uri", None)
                if not uri or uri in seen:
                    continue
                seen.add(uri)
                # On the first pass, seed the cursor without re-emitting backlog.
                if first_pass:
                    continue
                yield self._to_mention(note)

            first_pass = False
            await asyncio.sleep(self._poll_interval)

    def _to_mention(self, note: Any) -> Mention:
        author = getattr(note, "author", None)
        record = getattr(note, "record", None)
        uri = getattr(note, "uri", "") or ""
        cid = getattr(note, "cid", None)
        text = getattr(record, "text", "") if record is not None else ""
        thread_root_id = self._reply_root_uri(record)
        created_at = self._parse_dt(getattr(record, "created_at", None))
        return Mention(
            platform=self.name,
            post_id=self._rkey_from_uri(uri),
            author_handle=getattr(author, "handle", "") if author is not None else "",
            author_id=getattr(author, "did", None) if author is not None else None,
            text=text,
            thread_root_id=thread_root_id,
            url=self._web_url(uri),
            created_at=created_at,
            is_bot=False,
            raw={"uri": uri, "cid": cid},
        )

    @staticmethod
    def _reply_root_uri(record: Any) -> str | None:
        reply = getattr(record, "reply", None) if record is not None else None
        root = getattr(reply, "root", None) if reply is not None else None
        return getattr(root, "uri", None) if root is not None else None

    @staticmethod
    def _parse_dt(value: Any) -> datetime | None:
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None

    # -- lifecycle -----------------------------------------------------------
    async def healthcheck(self) -> bool:
        try:
            await self._ensure_client()
        except Exception as exc:  # healthcheck must not raise
            log.warning("bluesky.healthcheck_failed", error=str(exc))
            return False
        return True

    async def aclose(self) -> None:
        self._client = None
