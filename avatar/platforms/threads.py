"""Threads platform adapter, backed by the Meta Graph Threads API over httpx.

Publishing on Threads is a **two-step** flow on the Graph API:

1. ``POST /{user_id}/threads`` creates a *media container* (for text-only posts,
   ``media_type=TEXT`` plus the ``text`` field) and returns a container ``id``.
2. ``POST /{user_id}/threads_publish`` with ``creation_id=<container id>``
   publishes the container and returns the final media ``id``.

Replies use the same two-step flow with a ``reply_to_id`` on the container.

Mention reading uses the Threads ``GET /{user_id}/mentions`` endpoint, which
requires the ``threads_manage_mentions`` permission (an advanced permission that
must be granted through Meta App Review). When credentials with that scope are
configured, :meth:`stream_mentions` polls that endpoint like the other adapters:
the first pass seeds the seen-cursor without replying to the backlog, then each
new mention is emitted once.

The module is SDK-free; all HTTP is done with ``httpx.AsyncClient`` (imported at
module top level as ``httpx`` is a hard dependency). No optional third-party SDK
is required.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from datetime import datetime
from typing import Any

import httpx
import structlog

from avatar.core.registry import register_platform
from avatar.core.types import Capabilities, Mention, Post, PostResult, Ref

log = structlog.get_logger(__name__)

_API_BASE = "https://graph.threads.net/v1.0"
_DEFAULT_TIMEOUT = 30.0
_DEFAULT_POLL_INTERVAL = 60.0
_MAX_CHARS = 500
# Fields requested from the Threads mentions endpoint.
_MENTION_FIELDS = "id,text,username,timestamp,permalink"


@register_platform("threads")
class ThreadsAdapter:
    """Platform adapter for Threads via the Meta Graph API.

    Constructor settings (all optional at construction; failures are lazy)::

        {
            "id": "threads",            # optional adapter id -> self.name
            "user_id": "...",           # Threads user id (the "me" id)
            "access_token": "...",      # long-lived Threads access token
            "handle": "mybot",          # used to build post URLs
        }
    """

    def __init__(self, settings: Mapping[str, Any]) -> None:
        self.name = str(settings.get("id") or "threads")
        self._settings = dict(settings)
        self._user_id = settings.get("user_id")
        self._access_token = settings.get("access_token")
        self.handle = settings.get("handle")
        self._poll_interval = float(settings.get("poll_interval_seconds") or _DEFAULT_POLL_INTERVAL)

        self._client: httpx.AsyncClient | None = None

    # -- infrastructure ------------------------------------------------------
    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=_API_BASE, timeout=_DEFAULT_TIMEOUT)
        return self._client

    def _require_credentials(self) -> tuple[str, str]:
        missing = [
            key
            for key, val in (
                ("user_id", self._user_id),
                ("access_token", self._access_token),
            )
            if not val
        ]
        if missing:
            raise RuntimeError(
                "Threads adapter is missing credentials "
                f"({', '.join(missing)}); posting requires user_id and access_token."
            )
        return str(self._user_id), str(self._access_token)

    # -- protocol ------------------------------------------------------------
    def capabilities(self) -> Capabilities:
        return Capabilities(
            max_chars=_MAX_CHARS,
            supports_reply=True,
            supports_media=False,
            # Mention polling works given a token with threads_manage_mentions.
            can_poll_mentions=True,
        )

    async def _create_container(self, text: str, *, reply_to_id: str | None = None) -> str:
        """Step 1: create a TEXT media container and return its id."""
        user_id, access_token = self._require_credentials()
        params: dict[str, str] = {
            "media_type": "TEXT",
            "text": text,
            "access_token": access_token,
        }
        if reply_to_id:
            params["reply_to_id"] = reply_to_id
        client = self._get_client()
        resp = await client.post(f"/{user_id}/threads", params=params)
        resp.raise_for_status()
        creation_id = str(resp.json().get("id", ""))
        if not creation_id:
            raise RuntimeError("Threads container creation returned no id")
        return creation_id

    async def _publish_container(self, creation_id: str) -> PostResult:
        """Step 2: publish the container and return the final PostResult."""
        user_id, access_token = self._require_credentials()
        client = self._get_client()
        resp = await client.post(
            f"/{user_id}/threads_publish",
            params={"creation_id": creation_id, "access_token": access_token},
        )
        resp.raise_for_status()
        post_id = str(resp.json().get("id", ""))
        url_out = (
            f"https://www.threads.net/@{self.handle}/post/{post_id}"
            if self.handle and post_id
            else None
        )
        return PostResult(
            platform=self.name,
            post_id=post_id,
            url=url_out,
            posted_at=datetime.now(),
        )

    async def post(self, content: Post) -> PostResult:
        creation_id = await self._create_container(content.text)
        return await self._publish_container(creation_id)

    async def reply(self, content: Post, in_reply_to: Ref) -> PostResult:
        creation_id = await self._create_container(content.text, reply_to_id=in_reply_to.post_id)
        return await self._publish_container(creation_id)

    # -- mentions ------------------------------------------------------------
    async def stream_mentions(self) -> AsyncIterator[Mention]:
        """Poll ``GET /{user_id}/mentions`` and yield each new mention once.

        Needs a token with the ``threads_manage_mentions`` permission. The first
        pass seeds the seen-cursor without emitting the backlog, so the bot does
        not reply to old mentions on startup. Transient errors are logged and
        retried on the next interval rather than killing the poller.
        """
        user_id, access_token = self._require_credentials()
        client = self._get_client()
        seen: set[str] = set()
        first_pass = True

        while True:
            try:
                resp = await client.get(
                    f"/{user_id}/mentions",
                    params={"fields": _MENTION_FIELDS, "access_token": access_token},
                )
                resp.raise_for_status()
                data = resp.json().get("data") or []
            except Exception as exc:  # keep polling on transient errors
                log.warning("threads.mentions_poll_failed", error=str(exc))
                await asyncio.sleep(self._poll_interval)
                continue

            for item in data:
                mid = str(item.get("id") or "")
                if not mid or mid in seen:
                    continue
                seen.add(mid)
                # On the first pass, seed the cursor without re-emitting backlog.
                if first_pass:
                    continue
                yield self._to_mention(item)

            first_pass = False
            await asyncio.sleep(self._poll_interval)

    def _to_mention(self, item: Mapping[str, Any]) -> Mention:
        return Mention(
            platform=self.name,
            post_id=str(item.get("id") or ""),
            author_handle=str(item.get("username") or ""),
            text=str(item.get("text") or ""),
            url=item.get("permalink"),
            created_at=self._parse_dt(item.get("timestamp")),
            is_bot=False,
            raw=dict(item),
        )

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

    async def healthcheck(self) -> bool:
        if not (self._user_id and self._access_token):
            return False
        try:
            client = self._get_client()
            resp = await client.get(
                f"/{self._user_id}",
                params={"fields": "id", "access_token": str(self._access_token)},
            )
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
