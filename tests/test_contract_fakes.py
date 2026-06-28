"""Prove the reusable contract suites pass against the in-process fakes."""

from __future__ import annotations

from tests.contract import ModelContract, PlatformContract, ScannerContract
from tests.fakes import FakeModel, FakePlatform, FakeScanner


class TestFakePlatform(PlatformContract):
    async def make_platform(self):
        return FakePlatform()


class TestFakeModel(ModelContract):
    async def make_model(self):
        return FakeModel()


class TestFakeScanner(ScannerContract):
    async def make_scanner(self):
        return FakeScanner()
