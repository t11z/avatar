"""Trigger contract — the seam for schedule/mention/future trigger sources.

A trigger runs for the lifetime of the process and calls ``emit`` with a
normalised :class:`TriggerEvent` whenever it fires.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from .types import TriggerEvent

# Triggers emit events; the return value of the handler is ignored.
Emit = Callable[[TriggerEvent], Awaitable[Any]]


@runtime_checkable
class Trigger(Protocol):
    name: str

    async def run(self, emit: Emit) -> None:
        """Run until cancelled, calling ``emit`` for each fired event."""
        ...
