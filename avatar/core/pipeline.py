"""The content pipeline: from a trigger event to a published (or suppressed) post.

Steps (each observable): dedup → authorize → input-scan → prompt+generate →
output-scan (+ on-block policy) → guardrails (length, mention-spam) → publish
(or dry-run) → persist. The pipeline depends only on the core interfaces, so it
is fully unit-testable with fakes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from jinja2 import Environment, StrictUndefined

from ..config import AppConfig, ModelConfig
from ..obs.logging import get_logger
from ..obs.metrics import metrics
from ..obs.tracing import span
from .model import ModelProvider
from .platform import PlatformAdapter
from .policy import is_authorized, strip_foreign_mentions, truncate_graphemes
from .security import ContentScanner
from .store import Store
from .types import (
    GenerationRequest,
    Post,
    PostResult,
    ScanDirection,
    ScanRequest,
    ScanVerdict,
    TriggerEvent,
    TriggerKind,
)

log = get_logger("pipeline")

_DEFAULT_TEMPLATES = {
    "scheduled": "Write a short social post for {{ platform }}. Keep it under "
    "{{ max_chars }} characters.",
    "reply": "{{ author }} mentioned you and said:\n"
    '"""{{ mention_text }}"""\n'
    "Write a short, in-character reply for {{ platform }} under "
    "{{ max_chars }} characters.",
    "block_reply": "A message could not be answered as written. Respond briefly "
    "and in character on {{ platform }} without engaging with the content, "
    "under {{ max_chars }} characters.",
}


@dataclass
class Pipeline:
    config: AppConfig
    store: Store
    platforms: dict[str, PlatformAdapter]
    models: dict[str, ModelProvider]
    scanners: list[ContentScanner] = field(default_factory=list)
    system_prompt: str = "You are a friendly social media persona."
    _jinja: Environment = field(init=False)

    def __post_init__(self) -> None:
        self._jinja = Environment(undefined=StrictUndefined, autoescape=False)
        self._templates = {**_DEFAULT_TEMPLATES, **self.config.persona.templates}

    # -- public ---------------------------------------------------------------
    async def handle(self, event: TriggerEvent) -> PostResult | None:
        with span("pipeline.handle", trigger_kind=str(event.kind)):
            return await self._handle(event)

    async def _handle(self, event: TriggerEvent) -> PostResult | None:
        platform_id = event.platform or (event.mention.platform if event.mention else None)
        metrics.triggers_total.labels(str(event.kind), platform_id or "?").inc()

        if await self.store.seen(event.id):
            metrics.dedup_skipped_total.labels(str(event.kind)).inc()
            log.debug("dedup.skip", event_id=event.id)
            return None

        platform = self.platforms.get(platform_id or "")
        if platform is None:
            log.warning("platform.missing", platform=platform_id, event_id=event.id)
            return None

        if event.kind == TriggerKind.MENTION and not self._authorize(event):
            await self.store.mark_seen(event, None)
            return None

        if not await self._within_limits(event):
            return None

        # 1. input scan of untrusted mention text
        if event.mention and self.config.security.enabled and self.config.security.scan_input:
            verdict = await self._scan(
                event.mention.text, ScanDirection.INPUT, self._context(event)
            )
            if not verdict.allowed:
                return await self._on_block(event, platform, "input", verdict)

        # 2. generate
        text = await self._generate(event)
        if text is None:
            await self.store.mark_seen(event, None)
            return None

        # 3. output scan
        if self.config.security.enabled and self.config.security.scan_output:
            verdict = await self._scan(text, ScanDirection.OUTPUT, self._context(event))
            if not verdict.allowed:
                return await self._on_block(event, platform, "output", verdict)

        # 4. guardrails + publish
        return await self._finish(event, platform, text)

    # -- steps ----------------------------------------------------------------
    def _authorize(self, event: TriggerEvent) -> bool:
        m = event.mention
        assert m is not None
        mc = self.config.mentions
        if mc.ignore_bots and m.is_bot:
            return False
        if not is_authorized(
            m.author_handle,
            allow=mc.allow,
            deny=mc.deny,
            allow_patterns=mc.allow_patterns,
            deny_patterns=mc.deny_patterns,
        ):
            log.info("mention.unauthorized", author=m.author_handle)
            return False
        return True

    async def _within_limits(self, event: TriggerEvent) -> bool:
        limits = self.config.limits
        if limits.max_posts_per_day is not None:
            start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
            if await self.store.posts_since(start) >= limits.max_posts_per_day:
                log.warning("limit.daily_posts_reached")
                return False
        cooldown = max(limits.per_user_cooldown_seconds, self.config.mentions.cooldown_seconds)
        m = event.mention
        if cooldown and m and m.author_id:
            last = await self.store.last_user_reply(m.platform, m.author_id)
            if last and (datetime.now(UTC) - last).total_seconds() < cooldown:
                log.info("mention.cooldown", author=m.author_handle)
                return False
        return True

    def _model_config(self, event: TriggerEvent) -> ModelConfig:
        if event.kind == TriggerKind.SCHEDULED:
            for sched in self.config.schedules:
                if sched.name == event.schedule_name and sched.model:
                    return sched.model
        if event.kind == TriggerKind.MENTION and self.config.mentions.model:
            return self.config.mentions.model
        return self.config.model

    def _render(self, template_name: str, event: TriggerEvent, max_chars: int) -> str:
        tmpl = self._templates.get(template_name, self._templates["scheduled"])
        ctx = {
            "platform": event.platform or (event.mention.platform if event.mention else ""),
            "max_chars": max_chars,
            "schedule": event.schedule_name or "",
            "now": datetime.now(UTC).isoformat(),
            "author": event.mention.author_handle if event.mention else "",
            "mention_text": event.mention.text if event.mention else "",
        }
        return self._jinja.from_string(tmpl).render(**ctx)

    async def _generate(
        self, event: TriggerEvent, template_override: str | None = None
    ) -> str | None:
        mc = self._model_config(event)
        provider = self.models.get(mc.provider)
        if provider is None:
            log.error("model.provider_missing", provider=mc.provider)
            metrics.errors_total.labels("generate").inc()
            return None

        platform_id = event.platform or (event.mention.platform if event.mention else "")
        platform = self.platforms.get(platform_id)
        max_chars = platform.capabilities().max_chars if platform else 300
        template_name = (
            template_override
            or event.template
            or (self.config.mentions.template if event.kind == TriggerKind.MENTION else "scheduled")
        )
        user_prompt = self._render(template_name, event, max_chars)

        req = GenerationRequest(
            system=self.system_prompt,
            user=user_prompt,
            model=mc.model,
            max_tokens=mc.max_tokens,
            max_chars=max_chars,
            reasoning=mc.reasoning,
            params=mc.params,
        )
        start = time.perf_counter()
        try:
            with span("pipeline.generate", provider=mc.provider, model=mc.model):
                result = await provider.generate(req)
        except Exception as exc:
            metrics.llm_requests_total.labels(mc.provider, "error").inc()
            metrics.errors_total.labels("generate").inc()
            log.error("model.error", provider=mc.provider, error=str(exc))
            return None
        finally:
            metrics.llm_latency.labels(mc.provider).observe(time.perf_counter() - start)

        if result.input_tokens:
            metrics.llm_tokens_total.labels(mc.provider, "input").inc(result.input_tokens)
        if result.output_tokens:
            metrics.llm_tokens_total.labels(mc.provider, "output").inc(result.output_tokens)

        if result.refused:
            metrics.refusals_total.labels(mc.provider).inc()
            metrics.llm_requests_total.labels(mc.provider, "refused").inc()
            log.info("model.refused", provider=mc.provider)
            return None

        metrics.llm_requests_total.labels(mc.provider, "ok").inc()
        text = result.text.strip()
        return text or None

    async def _scan(self, text: str, direction: ScanDirection, context: dict) -> ScanVerdict:
        for scanner in self.scanners:
            try:
                with span("pipeline.scan", scanner=scanner.name, direction=str(direction)):
                    verdict = await scanner.scan(
                        ScanRequest(text=text, direction=direction, context=context)
                    )
            except Exception as exc:
                metrics.scan_errors_total.labels(scanner.name).inc()
                log.error("scan.error", scanner=scanner.name, error=str(exc))
                fail_open = self.config.security.fail_mode == "open"
                verdict = ScanVerdict(
                    allowed=fail_open, scanner=scanner.name, reasons=["scanner-error"]
                )
            metrics.scan_total.labels(str(direction), "allow" if verdict.allowed else "block").inc()
            if not verdict.allowed:
                metrics.scan_blocked_total.labels(str(direction)).inc()
                return verdict
        return ScanVerdict(allowed=True)

    async def _on_block(
        self,
        event: TriggerEvent,
        platform: PlatformAdapter,
        stage: str,
        verdict: ScanVerdict,
    ) -> PostResult | None:
        log.warning(
            "content.blocked",
            stage=stage,
            category=verdict.category,
            reasons=verdict.reasons,
            event_id=event.id,
        )
        if self.config.security.on_block != "persona_reply":
            await self.store.mark_seen(event, None)
            return None

        # Persona reply: generate a safe, in-character fallback and re-scan once.
        fallback = await self._generate(
            event, template_override=self.config.security.block_reply_template
        )
        if fallback is None:
            await self.store.mark_seen(event, None)
            return None
        recheck = await self._scan(fallback, ScanDirection.OUTPUT, self._context(event))
        if not recheck.allowed:
            log.warning("content.blocked.fallback", event_id=event.id)
            await self.store.mark_seen(event, None)
            return None
        return await self._finish(event, platform, fallback)

    async def _finish(
        self, event: TriggerEvent, platform: PlatformAdapter, text: str
    ) -> PostResult | None:
        caps = platform.capabilities()
        keep = event.mention.author_handle if event.mention else None
        text = strip_foreign_mentions(text, keep_handle=keep)
        text = truncate_graphemes(text, caps.max_chars)

        platform_id = event.platform or (event.mention.platform if event.mention else "")
        post = Post(platform=platform_id, text=text)

        if self.config.dry_run:
            metrics.dryrun_skipped_total.labels(platform_id).inc()
            log.info("dry_run.post", platform=platform_id, text=text, kind=str(event.kind))
            result = PostResult(
                platform=platform_id, post_id="dry-run", posted_at=datetime.now(UTC)
            )
            await self.store.mark_seen(event, result)
            return result

        start = time.perf_counter()
        try:
            with span("pipeline.publish", platform=platform_id):
                if event.kind == TriggerKind.MENTION and event.mention and caps.supports_reply:
                    result = await platform.reply(post, event.mention.to_ref())
                else:
                    result = await platform.post(post)
        except Exception as exc:
            metrics.posts_total.labels(platform_id, "error").inc()
            metrics.errors_total.labels("publish").inc()
            log.error("publish.error", platform=platform_id, error=str(exc))
            return None
        finally:
            metrics.post_latency.labels(platform_id).observe(time.perf_counter() - start)

        metrics.posts_total.labels(platform_id, "ok").inc()
        await self.store.record_post(result, kind=str(event.kind))
        await self.store.mark_seen(event, result)
        log.info("post.published", platform=platform_id, post_id=result.post_id)
        return result

    def _context(self, event: TriggerEvent) -> dict:
        return {
            "platform": event.platform or (event.mention.platform if event.mention else ""),
            "trigger_kind": str(event.kind),
            "author_handle": event.mention.author_handle if event.mention else None,
        }
