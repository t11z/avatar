"""Prometheus metrics. A single ``metrics`` singleton is imported everywhere."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Histogram


class Metrics:
    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry(auto_describe=True)
        self.triggers_total = Counter(
            "avatar_triggers_total",
            "Trigger events received",
            ["kind", "platform"],
            registry=self.registry,
        )
        self.posts_total = Counter(
            "avatar_posts_total",
            "Posts attempted",
            ["platform", "result"],
            registry=self.registry,
        )
        self.post_latency = Histogram(
            "avatar_post_latency_seconds",
            "Publish latency",
            ["platform"],
            registry=self.registry,
        )
        self.mentions_polled_total = Counter(
            "avatar_mentions_polled_total",
            "Mentions polled",
            ["platform"],
            registry=self.registry,
        )
        self.dedup_skipped_total = Counter(
            "avatar_dedup_skipped_total",
            "Events skipped by dedup",
            ["kind"],
            registry=self.registry,
        )
        self.dryrun_skipped_total = Counter(
            "avatar_dryrun_skipped_total",
            "Posts skipped in dry-run",
            ["platform"],
            registry=self.registry,
        )
        self.llm_requests_total = Counter(
            "avatar_llm_requests_total",
            "LLM requests",
            ["provider", "outcome"],
            registry=self.registry,
        )
        self.llm_tokens_total = Counter(
            "avatar_llm_tokens_total",
            "LLM tokens",
            ["provider", "direction"],
            registry=self.registry,
        )
        self.llm_latency = Histogram(
            "avatar_llm_latency_seconds",
            "LLM latency",
            ["provider"],
            registry=self.registry,
        )
        self.refusals_total = Counter(
            "avatar_refusals_total",
            "Model refusals",
            ["provider"],
            registry=self.registry,
        )
        self.scan_total = Counter(
            "avatar_scan_total",
            "Content scans",
            ["direction", "verdict"],
            registry=self.registry,
        )
        self.scan_blocked_total = Counter(
            "avatar_scan_blocked_total",
            "Scans that blocked content",
            ["direction"],
            registry=self.registry,
        )
        self.scan_errors_total = Counter(
            "avatar_scan_errors_total",
            "Scanner errors",
            ["scanner"],
            registry=self.registry,
        )
        self.rate_limited_total = Counter(
            "avatar_rate_limited_total",
            "Rate-limit responses",
            ["platform"],
            registry=self.registry,
        )
        self.errors_total = Counter(
            "avatar_errors_total",
            "Unhandled errors by stage",
            ["stage"],
            registry=self.registry,
        )


# Module-level singleton used across the app.
metrics = Metrics()
