"""Tests for the triggers package (schedule, mention, build_triggers)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from avatar.config import MentionsConfig, PlatformConfig, ScheduleConfig, load_config_from_dict
from avatar.core.types import Capabilities, Mention, TriggerEvent, TriggerKind
from avatar.triggers import MentionTrigger, ScheduleTrigger, build_triggers
from tests.fakes import FakePlatform

pytestmark = pytest.mark.asyncio


class _Collector:
    def __init__(self) -> None:
        self.events: list[TriggerEvent] = []

    async def __call__(self, event: TriggerEvent) -> None:
        self.events.append(event)


# --------------------------------------------------------------------------- #
# ScheduleTrigger
# --------------------------------------------------------------------------- #


class _FakeCron:
    """Stand-in for croniter returning a fixed next fire time."""

    def __init__(self, expr: str, start: datetime) -> None:
        self._fire = datetime(2026, 6, 28, 12, 30, tzinfo=UTC)

    def get_next(self, _type: type[datetime]) -> datetime:
        return self._fire


async def test_schedule_fires_once_with_expected_event(monkeypatch) -> None:
    sched = ScheduleConfig(
        name="daily", cron="30 12 * * *", platform="bluesky", template="scheduled"
    )
    trig = ScheduleTrigger(sched)

    # Make croniter deterministic.
    monkeypatch.setattr("croniter.croniter", _FakeCron)

    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    # Stop the infinite loop after the first emit.
    collector = _Collector()

    class _StopAfterOne(Exception):
        pass

    async def emit(event: TriggerEvent) -> None:
        collector.events.append(event)
        raise _StopAfterOne

    with pytest.raises(_StopAfterOne):
        await trig.run(emit)

    assert len(collector.events) == 1
    ev = collector.events[0]
    assert ev.id == "sched:daily:2026-06-28T12:30"
    assert ev.kind == TriggerKind.SCHEDULED
    assert ev.platform == "bluesky"
    assert ev.schedule_name == "daily"
    assert ev.template == "scheduled"
    # Slept toward the fire time (positive delay), no jitter configured.
    assert any(s > 0 for s in sleeps)


async def test_schedule_applies_jitter(monkeypatch) -> None:
    sched = ScheduleConfig(name="j", cron="* * * * *", platform="bluesky", jitter_seconds=10)
    trig = ScheduleTrigger(sched)
    monkeypatch.setattr("croniter.croniter", _FakeCron)

    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("asyncio.sleep", fake_sleep)
    # Deterministic jitter -> 5000 ms -> 5.0 s.
    monkeypatch.setattr("secrets.randbelow", lambda n: 5000)

    class _Stop(Exception):
        pass

    async def emit(event: TriggerEvent) -> None:
        raise _Stop

    with pytest.raises(_Stop):
        await trig.run(emit)

    assert 5.0 in sleeps


# --------------------------------------------------------------------------- #
# MentionTrigger
# --------------------------------------------------------------------------- #


class _StreamPlatform(FakePlatform):
    def __init__(self, mentions: list[Mention], **kw) -> None:
        super().__init__(**kw)
        self._mentions = mentions

    async def stream_mentions(self):
        for m in self._mentions:
            yield m


async def test_mention_trigger_maps_stream_to_events() -> None:
    mentions = [
        Mention(platform="bluesky", post_id="1", author_handle="alice", text="hi"),
        Mention(platform="bluesky", post_id="2", author_handle="bob", text="yo"),
    ]
    platform = _StreamPlatform(mentions, name="bluesky")
    cfg = MentionsConfig(enabled=True, template="reply")
    trig = MentionTrigger("bluesky", platform, cfg, handle="me")

    collector = _Collector()
    await trig.run(collector)

    assert len(collector.events) == 2
    first = collector.events[0]
    assert first.id == "mention:bluesky:1"
    assert first.kind == TriggerKind.MENTION
    assert first.template == "reply"
    assert first.mention is not None
    assert first.mention.author_handle == "alice"


async def test_mention_trigger_skips_self() -> None:
    mentions = [
        Mention(platform="bluesky", post_id="1", author_handle="@Me", text="self"),
        Mention(platform="bluesky", post_id="2", author_handle="alice", text="other"),
    ]
    platform = _StreamPlatform(mentions, name="bluesky")
    cfg = MentionsConfig(enabled=True, ignore_self=True)
    trig = MentionTrigger("bluesky", platform, cfg, handle="me")

    collector = _Collector()
    await trig.run(collector)

    assert [e.mention.post_id for e in collector.events] == ["2"]


# --------------------------------------------------------------------------- #
# build_triggers
# --------------------------------------------------------------------------- #


class _NoPollPlatform(FakePlatform):
    def capabilities(self) -> Capabilities:
        return Capabilities(can_poll_mentions=False)


def _config(**overrides) -> object:
    base = {
        "schedules": [
            {"name": "daily", "cron": "0 12 * * *", "platform": "bluesky"},
            {"name": "off", "cron": "0 9 * * *", "platform": "bluesky", "enabled": False},
        ],
        "mentions": {"enabled": True},
        "platforms": [
            {"id": "bluesky", "type": "bluesky", "handle": "me.bsky.social"},
            {"id": "x", "type": "x"},
        ],
    }
    base.update(overrides)
    return load_config_from_dict(base)


async def test_build_triggers_schedule_and_mentions() -> None:
    cfg = _config()
    platforms = {"bluesky": FakePlatform(name="bluesky"), "x": FakePlatform(name="x")}
    triggers = build_triggers(cfg, platforms=platforms, store=None)

    schedule_names = [t.name for t in triggers if isinstance(t, ScheduleTrigger)]
    mention_names = sorted(t.name for t in triggers if isinstance(t, MentionTrigger))

    # Only the enabled schedule.
    assert schedule_names == ["schedule:daily"]
    # A mention trigger per pollable platform.
    assert mention_names == ["mention:bluesky", "mention:x"]


async def test_build_triggers_skips_non_pollable_platforms() -> None:
    cfg = _config()
    platforms = {"bluesky": FakePlatform(name="bluesky"), "x": _NoPollPlatform(name="x")}
    triggers = build_triggers(cfg, platforms=platforms, store=None)

    mention_names = [t.name for t in triggers if isinstance(t, MentionTrigger)]
    assert mention_names == ["mention:bluesky"]


async def test_build_triggers_mentions_disabled() -> None:
    cfg = _config(mentions={"enabled": False})
    platforms = {"bluesky": FakePlatform(name="bluesky")}
    triggers = build_triggers(cfg, platforms=platforms, store=None)
    assert not any(isinstance(t, MentionTrigger) for t in triggers)


async def test_build_triggers_passes_handle_for_self_skip() -> None:
    cfg = _config()
    platforms = {"bluesky": FakePlatform(name="bluesky"), "x": FakePlatform(name="x")}
    triggers = build_triggers(cfg, platforms=platforms, store=None)
    bsky = next(t for t in triggers if isinstance(t, MentionTrigger) and t.platform_id == "bluesky")
    assert bsky.handle == "me.bsky.social"
    _ = PlatformConfig  # imported for type clarity
