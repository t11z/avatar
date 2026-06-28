"""Mention trigger — streams a platform's mentions into normalised events.

Self-authored posts are skipped when ``ignore_self`` is set, by comparing the
mention's ``author_handle`` to the platform's configured handle.
"""

from __future__ import annotations

from avatar.config import MentionsConfig
from avatar.core.platform import PlatformAdapter
from avatar.core.trigger import Emit
from avatar.core.types import TriggerEvent, TriggerKind
from avatar.obs.logging import get_logger

log = get_logger(__name__)


def _normalise_handle(handle: str | None) -> str | None:
    if handle is None:
        return None
    return handle.lstrip("@").lower()


class MentionTrigger:
    """Run a single platform's mention stream, emitting one event per mention."""

    def __init__(
        self,
        platform_id: str,
        platform: PlatformAdapter,
        mentions: MentionsConfig,
        *,
        handle: str | None = None,
    ) -> None:
        self.platform_id = platform_id
        self.platform = platform
        self.mentions = mentions
        self.handle = handle
        self.name = f"mention:{platform_id}"

    async def run(self, emit: Emit) -> None:
        own = _normalise_handle(self.handle)
        async for m in self.platform.stream_mentions():
            if self.mentions.ignore_self and own is not None:
                if _normalise_handle(m.author_handle) == own:
                    log.debug(
                        "mention_skip_self",
                        platform=self.platform_id,
                        post_id=m.post_id,
                    )
                    continue
            event = TriggerEvent(
                id=f"mention:{self.platform_id}:{m.post_id}",
                kind=TriggerKind.MENTION,
                platform=self.platform_id,
                mention=m,
                template=self.mentions.template,
                created_at=m.created_at,
            )
            log.info(
                "mention_fire",
                platform=self.platform_id,
                post_id=m.post_id,
                author=m.author_handle,
            )
            await emit(event)
