"""Shared data-transfer objects used across every layer.

These types are the lingua franca between triggers, the content pipeline,
model providers and platform adapters. Keep them small, serialisable and free
of any platform/provider specifics.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class TriggerKind(StrEnum):
    SCHEDULED = "scheduled"
    MENTION = "mention"


class ScanDirection(StrEnum):
    INPUT = "input"
    OUTPUT = "output"


class Ref(BaseModel):
    """A reference to an existing post on a platform.

    ``post_id`` is the platform-native identifier. ``uri`` and ``cid`` are
    optional fields some platforms (e.g. AT Protocol) require to reply.
    """

    platform: str
    post_id: str
    uri: str | None = None
    cid: str | None = None
    author_handle: str | None = None


class Post(BaseModel):
    """Content to be published to a platform."""

    platform: str
    text: str
    in_reply_to: Ref | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PostResult(BaseModel):
    platform: str
    post_id: str
    url: str | None = None
    posted_at: datetime | None = None


class Mention(BaseModel):
    """An incoming mention of the bot's handle."""

    platform: str
    post_id: str
    author_handle: str
    author_id: str | None = None
    text: str = ""
    thread_root_id: str | None = None
    url: str | None = None
    created_at: datetime | None = None
    is_bot: bool = False
    raw: dict[str, Any] = Field(default_factory=dict)

    def to_ref(self) -> Ref:
        return Ref(
            platform=self.platform,
            post_id=self.post_id,
            uri=self.raw.get("uri"),
            cid=self.raw.get("cid"),
            author_handle=self.author_handle,
        )


class TriggerEvent(BaseModel):
    """A normalised event emitted by any trigger source."""

    id: str
    kind: TriggerKind
    platform: str | None = None
    schedule_name: str | None = None
    template: str | None = None
    mention: Mention | None = None
    created_at: datetime | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class ModelInfo(BaseModel):
    """A model exposed by a provider, discovered at runtime."""

    id: str
    provider: str
    display_name: str | None = None
    created_at: datetime | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class GenerationRequest(BaseModel):
    system: str
    user: str
    model: str | None = None
    max_tokens: int = 512
    max_chars: int | None = None
    reasoning: str | None = None  # e.g. "none" | "low" | "medium" | "high"
    params: dict[str, Any] = Field(default_factory=dict)


class GenerationResult(BaseModel):
    text: str
    model: str
    provider: str
    refused: bool = False
    stop_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class ScanRequest(BaseModel):
    text: str
    direction: ScanDirection
    context: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ScanVerdict(BaseModel):
    """Result of a content scan.

    ``allowed`` is the single boolean every scanner must produce. The rest is
    advisory metadata for logging/metrics.
    """

    allowed: bool
    category: str | None = None
    reasons: list[str] = Field(default_factory=list)
    scanner: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class Capabilities(BaseModel):
    """What a platform adapter supports — used for capability gating."""

    max_chars: int = 300
    supports_reply: bool = True
    supports_media: bool = False
    can_poll_mentions: bool = True
