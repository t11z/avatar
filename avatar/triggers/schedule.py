"""Schedule trigger — fires on a cron expression with optional CSPRNG jitter.

Misfire policy is *skip*: each iteration computes the next fire time relative to
*now*, so slots missed while the process was down are never replayed. The fire
time is bucketed to the minute in the event id so a restart inside the same slot
does not double-post (the store dedupes on event id).
"""

from __future__ import annotations

import asyncio
import secrets
from datetime import UTC, datetime

from avatar.config import ScheduleConfig
from avatar.core.trigger import Emit
from avatar.core.types import TriggerEvent, TriggerKind
from avatar.obs.logging import get_logger

log = get_logger(__name__)


class ScheduleTrigger:
    """Run a single :class:`ScheduleConfig` on its cron, emitting one event per fire."""

    def __init__(self, sched: ScheduleConfig) -> None:
        self.sched = sched
        self.name = f"schedule:{sched.name}"

    def _now(self) -> datetime:
        return datetime.now(UTC)

    async def run(self, emit: Emit) -> None:
        from croniter import croniter

        while True:
            now = self._now()
            itr = croniter(self.sched.cron, now)
            fire_at: datetime = itr.get_next(datetime)

            # Sleep until the scheduled fire time (skip policy: relative to now).
            delay = (fire_at - now).total_seconds()
            if delay > 0:
                await asyncio.sleep(delay)

            # Add CSPRNG jitter in [0, jitter_seconds) to avoid thundering herds.
            jitter = self.sched.jitter_seconds
            if jitter > 0:
                # secrets.randbelow gives a uniform integer in [0, n); scale to ms
                # for sub-second resolution, then back to seconds.
                jitter_s = secrets.randbelow(jitter * 1000) / 1000.0
                if jitter_s > 0:
                    await asyncio.sleep(jitter_s)

            fire_iso_minute = fire_at.strftime("%Y-%m-%dT%H:%M")
            event = TriggerEvent(
                id=f"sched:{self.sched.name}:{fire_iso_minute}",
                kind=TriggerKind.SCHEDULED,
                platform=self.sched.platform,
                schedule_name=self.sched.name,
                template=self.sched.template,
                created_at=self._now(),
            )
            log.info(
                "schedule_fire",
                schedule=self.sched.name,
                platform=self.sched.platform,
                fire_at=fire_iso_minute,
            )
            await emit(event)
