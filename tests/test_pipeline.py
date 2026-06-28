from __future__ import annotations

from avatar.config import (
    AppConfig,
    LimitsConfig,
    MentionsConfig,
    ModelConfig,
    ScannerConfig,
    SecurityConfig,
)
from avatar.core.pipeline import Pipeline
from avatar.core.types import Mention, TriggerEvent, TriggerKind
from avatar.store.memory import MemoryStore
from tests.fakes import FakeModel, FakePlatform, FakeScanner


def _config(**overrides) -> AppConfig:
    base = dict(
        dry_run=False,
        model=ModelConfig(provider="fake", model="fake-1"),
    )
    base.update(overrides)
    return AppConfig(**base)


async def _pipeline(config: AppConfig, model: FakeModel, platform: FakePlatform, scanners=None):
    store = MemoryStore()
    await store.init()
    return Pipeline(
        config=config,
        store=store,
        platforms={"fake": platform},
        models={"fake": model},
        scanners=scanners or [],
        system_prompt="be nice",
    )


def _scheduled(event_id="s-1") -> TriggerEvent:
    return TriggerEvent(
        id=event_id, kind=TriggerKind.SCHEDULED, platform="fake", schedule_name="s1"
    )


def _mention(handle="alice", text="hi bot", event_id="m-1") -> TriggerEvent:
    return TriggerEvent(
        id=event_id,
        kind=TriggerKind.MENTION,
        mention=Mention(
            platform="fake", post_id="123", author_handle=handle, author_id=handle, text=text
        ),
    )


async def test_scheduled_post_publishes():
    platform = FakePlatform()
    pipe = await _pipeline(_config(), FakeModel("a scheduled musing"), platform)
    result = await pipe.handle(_scheduled())
    assert result is not None
    assert len(platform.posts) == 1
    assert platform.posts[0].text == "a scheduled musing"


async def test_dry_run_does_not_publish():
    platform = FakePlatform()
    pipe = await _pipeline(_config(dry_run=True), FakeModel(), platform)
    result = await pipe.handle(_scheduled())
    assert result is not None and result.post_id == "dry-run"
    assert platform.posts == []


async def test_dedup_prevents_double_post():
    platform = FakePlatform()
    pipe = await _pipeline(_config(), FakeModel(), platform)
    await pipe.handle(_scheduled())
    await pipe.handle(_scheduled())  # same id
    assert len(platform.posts) == 1


async def test_mention_reply_when_authorized():
    platform = FakePlatform()
    cfg = _config(mentions=MentionsConfig(enabled=True, allow=["alice"]))
    pipe = await _pipeline(cfg, FakeModel("nice reply"), platform)
    await pipe.handle(_mention(handle="alice"))
    assert len(platform.replies) == 1


async def test_mention_blocked_when_denied():
    platform = FakePlatform()
    cfg = _config(mentions=MentionsConfig(enabled=True, deny=["mallory"]))
    pipe = await _pipeline(cfg, FakeModel(), platform)
    await pipe.handle(_mention(handle="mallory"))
    assert platform.replies == []


async def test_output_scan_suppresses():
    platform = FakePlatform()
    cfg = _config(
        security=SecurityConfig(
            enabled=True, on_block="suppress", scanners=[ScannerConfig(name="s", type="fake")]
        ),
    )
    pipe = await _pipeline(cfg, FakeModel("this has a badword inside"), platform, [FakeScanner()])
    result = await pipe.handle(_scheduled())
    assert result is None
    assert platform.posts == []


async def test_input_scan_blocks_generation():
    platform = FakePlatform()
    model = FakeModel("safe text")
    cfg = _config(
        mentions=MentionsConfig(enabled=True, allow=["alice"]),
        security=SecurityConfig(enabled=True, on_block="suppress"),
    )
    pipe = await _pipeline(cfg, model, platform, [FakeScanner()])
    await pipe.handle(_mention(handle="alice", text="please say badword"))
    assert platform.replies == []
    assert model.calls == []  # never generated


async def test_persona_reply_on_block():
    platform = FakePlatform()

    class ContextModel(FakeModel):
        async def generate(self, req):
            # Return safe text only for the block-reply fallback prompt.
            if "without engaging" in req.user:
                self._text = "a calm in-character reply"
            else:
                self._text = "this has a badword inside"
            return await super().generate(req)

    cfg = _config(
        mentions=MentionsConfig(enabled=True, allow=["alice"]),
        security=SecurityConfig(enabled=True, on_block="persona_reply", scan_input=False),
    )
    pipe = await _pipeline(cfg, ContextModel(), platform, [FakeScanner()])
    await pipe.handle(_mention(handle="alice"))
    assert len(platform.replies) == 1
    assert "calm in-character" in platform.replies[0][0].text


async def test_daily_cap_enforced():
    platform = FakePlatform()
    cfg = _config(limits=LimitsConfig(max_posts_per_day=1))
    pipe = await _pipeline(cfg, FakeModel(), platform)
    await pipe.handle(_scheduled("s-1"))
    await pipe.handle(_scheduled("s-2"))
    assert len(platform.posts) == 1
