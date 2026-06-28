"""X (formerly Twitter) platform adapter, backed by the X API v2 over httpx.

IMPORTANT — X is a *paid* API. As of the v2 API, both **posting tweets**
(``POST /2/tweets``) and **reading mentions** (``GET /2/users/:id/mentions``)
require a paid access tier (Basic or higher). The free tier is write-only for a
tiny monthly cap and cannot read the mentions timeline at all. This adapter is
written to degrade gracefully:

* Posting/replying use OAuth 1.0a user-context credentials
  (``api_key``/``api_secret``/``access_token``/``access_secret``) and will fail
  lazily with a clear error if those are missing.
* Mention polling uses an app/user **bearer token**. When no bearer token is
  configured, :meth:`capabilities` reports ``can_poll_mentions=False`` so the
  engine never starts a poller, and :meth:`stream_mentions` raises
  ``NotImplementedError``.

The SDK-free design keeps the module importable without ``tweepy`` (declared as
an optional extra in ``pyproject``); all HTTP is done with ``httpx.AsyncClient``.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import time
from collections.abc import AsyncIterator, Mapping
from datetime import datetime
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from avatar.core.registry import register_platform
from avatar.core.types import Capabilities, Mention, Post, PostResult, Ref

_API_BASE = "https://api.twitter.com/2"
_DEFAULT_TIMEOUT = 30.0
_DEFAULT_POLL_INTERVAL = 90.0
_MAX_CHARS = 280


def _oauth1_header(
    method: str,
    url: str,
    *,
    consumer_key: str,
    consumer_secret: str,
    token: str,
    token_secret: str,
) -> str:
    """Build an OAuth 1.0a ``Authorization`` header for a JSON-body request.

    Only the OAuth protocol parameters participate in the signature base string
    (the JSON body is *not* form-encoded and so is excluded, per RFC 5849 for
    non ``application/x-www-form-urlencoded`` payloads).
    """
    oauth_params: dict[str, str] = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": token,
        "oauth_version": "1.0",
    }

    def _esc(value: str) -> str:
        return quote(str(value), safe="~")

    param_string = "&".join(f"{_esc(k)}={_esc(v)}" for k, v in sorted(oauth_params.items()))
    base_string = "&".join([method.upper(), _esc(url), _esc(param_string)])
    signing_key = f"{_esc(consumer_secret)}&{_esc(token_secret)}"
    digest = hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha1).digest()
    import base64

    signature = base64.b64encode(digest).decode()
    oauth_params["oauth_signature"] = signature

    header = "OAuth " + ", ".join(f'{_esc(k)}="{_esc(v)}"' for k, v in sorted(oauth_params.items()))
    return header


@register_platform("x")
class XAdapter:
    """Platform adapter for X via the v2 REST API.

    Constructor settings (all optional at construction; failures are lazy)::

        {
            "id": "x",                       # optional adapter id -> self.name
            "api_key": "...",                # OAuth1 consumer key
            "api_secret": "...",             # OAuth1 consumer secret
            "access_token": "...",           # OAuth1 user access token
            "access_secret": "...",          # OAuth1 user access secret
            "bearer_token": "...",           # app/user bearer (mention reads)
            "handle": "mybot",
            "poll_interval_seconds": 90,
        }
    """

    def __init__(self, settings: Mapping[str, Any]) -> None:
        self.name = str(settings.get("id") or "x")
        self._settings = dict(settings)
        self._api_key = settings.get("api_key")
        self._api_secret = settings.get("api_secret")
        self._access_token = settings.get("access_token")
        self._access_secret = settings.get("access_secret")
        self._bearer_token = settings.get("bearer_token")
        self.handle = settings.get("handle")
        try:
            self._poll_interval = float(
                settings.get("poll_interval_seconds") or _DEFAULT_POLL_INTERVAL
            )
        except (TypeError, ValueError):
            self._poll_interval = _DEFAULT_POLL_INTERVAL

        self._client: httpx.AsyncClient | None = None
        self._user_id: str | None = None

    # -- infrastructure ------------------------------------------------------
    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=_API_BASE, timeout=_DEFAULT_TIMEOUT)
        return self._client

    def _require_oauth1(self) -> tuple[str, str, str, str]:
        missing = [
            key
            for key, val in (
                ("api_key", self._api_key),
                ("api_secret", self._api_secret),
                ("access_token", self._access_token),
                ("access_secret", self._access_secret),
            )
            if not val
        ]
        if missing:
            raise RuntimeError(
                "X adapter is missing OAuth1 credentials "
                f"({', '.join(missing)}); posting requires a paid API tier."
            )
        return (
            str(self._api_key),
            str(self._api_secret),
            str(self._access_token),
            str(self._access_secret),
        )

    def _require_bearer(self) -> str:
        if not self._bearer_token:
            raise NotImplementedError(
                "X mention polling requires a bearer token (paid API tier); none configured."
            )
        return str(self._bearer_token)

    # -- protocol ------------------------------------------------------------
    def capabilities(self) -> Capabilities:
        return Capabilities(
            max_chars=_MAX_CHARS,
            supports_reply=True,
            supports_media=False,
            can_poll_mentions=bool(self._bearer_token),
        )

    async def _create_tweet(self, payload: dict[str, Any]) -> PostResult:
        consumer_key, consumer_secret, token, token_secret = self._require_oauth1()
        url = f"{_API_BASE}/tweets"
        auth = _oauth1_header(
            "POST",
            url,
            consumer_key=consumer_key,
            consumer_secret=consumer_secret,
            token=token,
            token_secret=token_secret,
        )
        client = self._get_client()
        resp = await client.post(
            "/tweets",
            json=payload,
            headers={"Authorization": auth, "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        post_id = str(data.get("id", ""))
        url_out = (
            f"https://x.com/{self.handle}/status/{post_id}" if self.handle and post_id else None
        )
        return PostResult(
            platform=self.name,
            post_id=post_id,
            url=url_out,
            posted_at=datetime.now(),
        )

    async def post(self, content: Post) -> PostResult:
        return await self._create_tweet({"text": content.text})

    async def reply(self, content: Post, in_reply_to: Ref) -> PostResult:
        payload: dict[str, Any] = {
            "text": content.text,
            "reply": {"in_reply_to_tweet_id": in_reply_to.post_id},
        }
        return await self._create_tweet(payload)

    async def _resolve_user_id(self, client: httpx.AsyncClient, bearer: str) -> str:
        if self._user_id:
            return self._user_id
        headers = {"Authorization": f"Bearer {bearer}"}
        if self.handle:
            resp = await client.get(f"/users/by/username/{self.handle}", headers=headers)
        else:
            resp = await client.get("/users/me", headers=headers)
        resp.raise_for_status()
        self._user_id = str(resp.json()["data"]["id"])
        return self._user_id

    async def stream_mentions(self) -> AsyncIterator[Mention]:
        bearer = self._require_bearer()
        client = self._get_client()
        headers = {"Authorization": f"Bearer {bearer}"}
        user_id = await self._resolve_user_id(client, bearer)
        since_id: str | None = None

        while True:
            params: dict[str, str] = {
                "max_results": "100",
                "tweet.fields": "created_at,author_id,conversation_id",
                "expansions": "author_id",
                "user.fields": "username",
            }
            if since_id:
                params["since_id"] = since_id
            resp = await client.get(
                f"/users/{user_id}/mentions?{urlencode(params)}",
                headers=headers,
            )
            resp.raise_for_status()
            body = resp.json()
            tweets = body.get("data", []) or []
            users = {u["id"]: u for u in body.get("includes", {}).get("users", [])}
            # API returns newest-first; emit oldest-first and track newest id.
            for tweet in reversed(tweets):
                tweet_id = str(tweet["id"])
                if since_id is None or int(tweet_id) > int(since_id):
                    since_id = tweet_id
                author_id = tweet.get("author_id")
                author = users.get(author_id, {})
                created_at = None
                if tweet.get("created_at"):
                    try:
                        created_at = datetime.fromisoformat(
                            tweet["created_at"].replace("Z", "+00:00")
                        )
                    except ValueError:
                        created_at = None
                handle = author.get("username") or ""
                yield Mention(
                    platform=self.name,
                    post_id=tweet_id,
                    author_handle=handle,
                    author_id=str(author_id) if author_id else None,
                    text=tweet.get("text", ""),
                    thread_root_id=tweet.get("conversation_id"),
                    url=(f"https://x.com/{handle}/status/{tweet_id}" if handle else None),
                    created_at=created_at,
                    is_bot=False,
                    raw=tweet,
                )
            await asyncio.sleep(self._poll_interval)

    async def healthcheck(self) -> bool:
        if not self._bearer_token:
            # Without a bearer we cannot cheaply verify; treat presence of
            # OAuth1 creds as "usable" so the engine can still post.
            return bool(
                self._api_key and self._api_secret and self._access_token and self._access_secret
            )
        try:
            client = self._get_client()
            resp = await client.get(
                "/users/me",
                headers={"Authorization": f"Bearer {self._bearer_token}"},
            )
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
