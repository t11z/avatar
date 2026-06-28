"""PlatformAdapter contract — the seam for X, Threads, Bluesky, ...

Implementations live in ``avatar.platforms`` and register themselves with
``@register_platform("name")``. The constructor receives the platform's config
sub-dict (already env-interpolated); secrets are read from it / the environment.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from .types import Capabilities, Mention, Post, PostResult, Ref


@runtime_checkable
class PlatformAdapter(Protocol):
    name: str

    def capabilities(self) -> Capabilities:
        """Static description of what this adapter supports (capability gating)."""
        ...

    async def post(self, content: Post) -> PostResult:
        """Publish a top-level post."""
        ...

    async def reply(self, content: Post, in_reply_to: Ref) -> PostResult:
        """Publish a reply to an existing post."""
        ...

    def stream_mentions(self) -> AsyncIterator[Mention]:
        """Yield mentions of the bot's handle (polling or streaming).

        Must be a long-running async generator. Adapters that cannot read
        mentions (e.g. unfunded API tiers) should report
        ``capabilities().can_poll_mentions == False`` and may raise
        ``NotImplementedError`` here.
        """
        ...

    async def healthcheck(self) -> bool:
        """Return True if the adapter is authenticated and usable."""
        ...

    async def aclose(self) -> None:
        """Release any resources (HTTP clients, sessions)."""
        ...
