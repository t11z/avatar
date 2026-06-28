"""Reusable contract-test suites for adapters.

Every platform/model/scanner adapter should ship a test that subclasses the
matching contract and implements the ``make_*`` factory (wiring the adapter to
a mocked transport). Subclasses inherit the conformance checks for free.

Example (in ``tests/test_bluesky.py``)::

    from tests.contract import PlatformContract

    class TestBluesky(PlatformContract):
        async def make_platform(self):
            return BlueskyAdapter({...}, http=mock_client)
"""

from __future__ import annotations

import pytest

from avatar.core.model import ModelProvider
from avatar.core.platform import PlatformAdapter
from avatar.core.security import ContentScanner
from avatar.core.types import (
    GenerationRequest,
    ModelInfo,
    Post,
    PostResult,
    ScanDirection,
    ScanRequest,
    ScanVerdict,
)


class PlatformContract:
    async def make_platform(self) -> PlatformAdapter:
        raise NotImplementedError

    async def test_conforms(self) -> None:
        p = await self.make_platform()
        assert isinstance(p, PlatformAdapter)
        assert isinstance(p.name, str) and p.name
        caps = p.capabilities()
        assert caps.max_chars > 0
        await p.aclose()

    async def test_post_returns_result(self) -> None:
        p = await self.make_platform()
        result = await p.post(Post(platform=p.name, text="contract test post"))
        assert isinstance(result, PostResult)
        assert result.post_id
        await p.aclose()


class ModelContract:
    async def make_model(self) -> ModelProvider:
        raise NotImplementedError

    async def test_conforms(self) -> None:
        m = await self.make_model()
        assert isinstance(m, ModelProvider)
        assert isinstance(m.name, str) and m.name
        await m.aclose()

    async def test_list_models(self) -> None:
        m = await self.make_model()
        models = await m.list_models()
        assert isinstance(models, list)
        assert all(isinstance(x, ModelInfo) for x in models)
        await m.aclose()

    async def test_generate(self) -> None:
        m = await self.make_model()
        result = await m.generate(
            GenerationRequest(system="be brief", user="say hi", max_tokens=16)
        )
        assert result.text or result.refused
        assert result.provider == m.name
        await m.aclose()


class ScannerContract:
    async def make_scanner(self) -> ContentScanner:
        raise NotImplementedError

    async def test_conforms(self) -> None:
        s = await self.make_scanner()
        assert isinstance(s, ContentScanner)
        assert isinstance(s.name, str) and s.name
        await s.aclose()

    async def test_scan_returns_verdict(self) -> None:
        s = await self.make_scanner()
        verdict = await s.scan(ScanRequest(text="hello", direction=ScanDirection.OUTPUT))
        assert isinstance(verdict, ScanVerdict)
        assert isinstance(verdict.allowed, bool)
        await s.aclose()


# Mark every test method as async-friendly under asyncio_mode=auto.
pytestmark = pytest.mark.asyncio
