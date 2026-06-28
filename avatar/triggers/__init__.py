"""Trigger sources: build the live triggers from validated config.

A :class:`~avatar.core.trigger.Trigger` runs for the process lifetime and emits
normalised :class:`~avatar.core.types.TriggerEvent` objects via ``emit``. This
package provides the scheduled and mention sources and the factory that wires
them from :class:`~avatar.config.AppConfig`.
"""

from __future__ import annotations

from typing import Any

from avatar.config import AppConfig
from avatar.core.platform import PlatformAdapter
from avatar.core.trigger import Trigger
from avatar.obs.logging import get_logger

from .mention import MentionTrigger
from .schedule import ScheduleTrigger

__all__ = ["MentionTrigger", "ScheduleTrigger", "build_triggers"]

log = get_logger(__name__)


def build_triggers(
    config: AppConfig,
    *,
    platforms: dict[str, PlatformAdapter],
    store: Any,
) -> list[Trigger]:
    """Construct the list of triggers for *config*.

    - One :class:`ScheduleTrigger` per enabled schedule.
    - If mentions are enabled, one :class:`MentionTrigger` per platform id in
      ``config.mentions.platforms`` (or all platform ids when unset) that exists
      in *platforms* and whose ``capabilities().can_poll_mentions`` is True.
      Others are logged and skipped.

    ``store`` is accepted for symmetry with other builders and to allow future
    dedup/cursor wiring; the triggers themselves emit raw events and leave
    persistence to the pipeline.
    """
    triggers: list[Trigger] = []

    for sched in config.schedules:
        if not sched.enabled:
            continue
        triggers.append(ScheduleTrigger(sched))

    if config.mentions.enabled:
        handles = {p.id: p.handle for p in config.platforms}
        target_ids = config.mentions.platforms or list(platforms.keys())
        for pid in target_ids:
            platform = platforms.get(pid)
            if platform is None:
                log.warning("mention_platform_missing", platform=pid)
                continue
            if not platform.capabilities().can_poll_mentions:
                log.warning("mention_platform_cannot_poll", platform=pid)
                continue
            triggers.append(
                MentionTrigger(
                    pid,
                    platform,
                    config.mentions,
                    handle=handles.get(pid),
                )
            )

    return triggers
