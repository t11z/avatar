"""Configuration loading and validation.

Non-secret structure lives in YAML; secrets are referenced via ``${ENV_VAR}``
(with optional ``${ENV_VAR:-default}``) and resolved from the environment, so
credentials never sit in the config file. Validation is fail-fast via pydantic.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import regex
import yaml
from pydantic import BaseModel, Field

_ENV_PATTERN = regex.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")


def _interpolate(value: Any) -> Any:
    """Recursively expand ``${VAR}`` / ``${VAR:-default}`` in strings."""
    if isinstance(value, str):

        def _repl(m: regex.Match[str]) -> str:
            name, default = m.group(1), m.group(2)
            env = os.environ.get(name)
            if env is not None:
                return env
            if default is not None:
                return default
            return ""

        return _ENV_PATTERN.sub(_repl, value)
    if isinstance(value, dict):
        return {k: _interpolate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v) for v in value]
    return value


class LogConfig(BaseModel):
    level: str = "INFO"
    format: str = "json"  # "json" | "console"


class TracingConfig(BaseModel):
    enabled: bool = False
    otlp_endpoint: str | None = None
    service_name: str = "avatar"


class ObservabilityConfig(BaseModel):
    metrics_addr: str = "0.0.0.0:9090"
    health_addr: str = "0.0.0.0:8080"
    tracing: TracingConfig = Field(default_factory=TracingConfig)


class StoreConfig(BaseModel):
    type: str = "sqlite"  # "sqlite" | "memory"
    path: str = "/data/avatar.db"


class PersonaConfig(BaseModel):
    system_prompt: str = "You are a friendly social media persona."
    system_prompt_file: str | None = None
    templates: dict[str, str] = Field(default_factory=dict)

    def resolved_system_prompt(self, base_dir: Path | None = None) -> str:
        if self.system_prompt_file:
            p = Path(self.system_prompt_file)
            if base_dir and not p.is_absolute():
                p = base_dir / p
            return p.read_text(encoding="utf-8")
        return self.system_prompt


class ModelConfig(BaseModel):
    model_config = {"extra": "allow"}

    provider: str = "ollama"
    model: str = "llama3"
    max_tokens: int = 400
    reasoning: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    # discovery
    discover: bool = True
    discovery_ttl_seconds: int = 3600
    model_allow_patterns: list[str] = Field(default_factory=list)
    model_deny_patterns: list[str] = Field(default_factory=list)


class PlatformConfig(BaseModel):
    model_config = {"extra": "allow"}

    id: str
    type: str
    enabled: bool = True
    handle: str | None = None
    poll_interval_seconds: int = 60


class ScheduleConfig(BaseModel):
    model_config = {"extra": "allow"}

    name: str
    cron: str
    jitter_seconds: int = 0
    platform: str
    template: str = "scheduled"
    model: ModelConfig | None = None
    enabled: bool = True


class MentionsConfig(BaseModel):
    enabled: bool = False
    platforms: list[str] | None = None  # None => all enabled platforms
    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)
    allow_patterns: list[str] = Field(default_factory=list)
    deny_patterns: list[str] = Field(default_factory=list)
    template: str = "reply"
    cooldown_seconds: int = 0
    ignore_self: bool = True
    ignore_bots: bool = True
    model: ModelConfig | None = None


class LimitsConfig(BaseModel):
    max_posts_per_day: int | None = None
    max_tokens_per_day: int | None = None
    per_user_cooldown_seconds: int = 0


class ScannerConfig(BaseModel):
    model_config = {"extra": "allow"}

    name: str
    type: str = "http"
    enabled: bool = True


class SecurityConfig(BaseModel):
    enabled: bool = False
    fail_mode: str = "closed"  # "open" | "closed"
    on_block: str = "suppress"  # "suppress" | "persona_reply"
    scan_input: bool = True
    scan_output: bool = True
    block_reply_template: str = "block_reply"
    scanners: list[ScannerConfig] = Field(default_factory=list)


class ProvidersConfig(BaseModel):
    model_config = {"extra": "allow"}
    # Free-form per-provider settings (api keys via ${ENV}); adapters read their own.


class AppConfig(BaseModel):
    dry_run: bool = True
    log: LogConfig = Field(default_factory=LogConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    store: StoreConfig = Field(default_factory=StoreConfig)
    persona: PersonaConfig = Field(default_factory=PersonaConfig)
    model: ModelConfig = Field(default_factory=ModelConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    platforms: list[PlatformConfig] = Field(default_factory=list)
    schedules: list[ScheduleConfig] = Field(default_factory=list)
    mentions: MentionsConfig = Field(default_factory=MentionsConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)


def load_config(path: str | Path) -> AppConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    interpolated = _interpolate(raw)
    return AppConfig.model_validate(interpolated)


def load_config_from_dict(data: dict[str, Any]) -> AppConfig:
    return AppConfig.model_validate(_interpolate(data))
