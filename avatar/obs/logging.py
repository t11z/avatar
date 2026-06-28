"""Structured logging with secret redaction.

Never log secrets: keys whose name looks like a credential are redacted, and a
:class:`Secret` wrapper renders as ``****`` wherever it is logged.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

_SECRET_HINTS = ("token", "key", "secret", "password", "authorization", "api_key")


class Secret:
    """Wrap a sensitive value so it never renders in logs or reprs."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "Secret('****')"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return "****"


def _redact(
    _logger: Any, _name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for k, v in list(event_dict.items()):
        if isinstance(v, Secret):
            event_dict[k] = "****"
        elif any(h in k.lower() for h in _SECRET_HINTS):
            event_dict[k] = "****"
    return event_dict


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    renderer = (
        structlog.processors.JSONRenderer() if fmt == "json" else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _redact,
            structlog.processors.StackInfoRenderer(),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
