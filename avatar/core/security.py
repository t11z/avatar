"""ContentScanner contract — optional, vendor-neutral security hook.

A scanner inspects content (incoming mentions and/or outgoing posts) and
returns a :class:`ScanVerdict`. The interface is deliberately generic so any
external content-security service — regardless of its request/response schema —
can be wired in via configuration, and so contributors can add non-HTTP
scanners through the registry.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .types import ScanRequest, ScanVerdict


@runtime_checkable
class ContentScanner(Protocol):
    name: str

    async def scan(self, req: ScanRequest) -> ScanVerdict:
        """Return a verdict for the given content."""
        ...

    async def aclose(self) -> None: ...
