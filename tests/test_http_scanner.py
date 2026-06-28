"""Tests for the generic HTTP content scanner.

The key property under test: the SAME scanner class adapts to wildly different
response schemas through configuration alone. All HTTP is mocked — no network,
no SDK needed beyond httpx/jinja2/jmespath.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from avatar.core.types import ScanDirection, ScanRequest, ScanVerdict
from avatar.security.http_scanner import HttpScanner
from tests.contract import ScannerContract


def _mock_transport(scanner: HttpScanner, *, status: int = 200, body: Any) -> None:
    """Replace the scanner's AsyncClient with one backed by a mock transport.

    Captures the last outgoing request on ``scanner.last_request`` for asserts.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        scanner.last_request = request  # type: ignore[attr-defined]
        return httpx.Response(status, json=body)

    transport = httpx.MockTransport(handler)
    scanner._client = httpx.AsyncClient(transport=transport, timeout=scanner._timeout)


class TestHttpScannerContract(ScannerContract):
    async def make_scanner(self) -> HttpScanner:
        scanner = HttpScanner(
            {
                "name": "http",
                "endpoint": "https://scan.example/v1",
                "verdict": {"path": "action", "block_values": ["block"]},
            }
        )
        _mock_transport(scanner, body={"action": "allow"})
        return scanner


@pytest.mark.asyncio
async def test_schema_a_action_block() -> None:
    """Schema A: {"action": "block"} via verdict.path + block_values."""
    scanner = HttpScanner(
        {
            "endpoint": "https://scan.example/a",
            "verdict": {
                "path": "action",
                "block_values": ["block"],
                "allow_values": ["allow"],
                "category_path": "reason",
            },
        }
    )
    _mock_transport(scanner, body={"action": "block", "reason": "toxicity"})

    verdict = await scanner.scan(ScanRequest(text="bad stuff", direction=ScanDirection.OUTPUT))
    assert isinstance(verdict, ScanVerdict)
    assert verdict.allowed is False
    assert verdict.category == "toxicity"
    await scanner.aclose()


@pytest.mark.asyncio
async def test_schema_b_block_expression() -> None:
    """Schema B (totally different shape): {"result": {"flagged": true}}."""
    scanner = HttpScanner(
        {
            "endpoint": "https://scan.example/b",
            "verdict": {"block_expression": "result.flagged"},
        }
    )
    _mock_transport(scanner, body={"result": {"flagged": True}})

    verdict = await scanner.scan(ScanRequest(text="bad stuff", direction=ScanDirection.OUTPUT))
    assert verdict.allowed is False
    assert "block_expression matched" in verdict.reasons
    await scanner.aclose()


@pytest.mark.asyncio
async def test_allowed_response() -> None:
    """An allowed response yields allowed=True for both schemas' configs."""
    scanner = HttpScanner(
        {
            "endpoint": "https://scan.example/b",
            "verdict": {"block_expression": "result.flagged"},
        }
    )
    _mock_transport(scanner, body={"result": {"flagged": False}})

    verdict = await scanner.scan(ScanRequest(text="totally fine", direction=ScanDirection.INPUT))
    assert verdict.allowed is True
    await scanner.aclose()


@pytest.mark.asyncio
async def test_request_template_and_auth() -> None:
    """request_template renders to JSON and bearer auth header is sent."""
    scanner = HttpScanner(
        {
            "endpoint": "https://scan.example/c",
            "auth": {"kind": "bearer", "token": "secret-123"},
            "request_template": '{"contents": [{"prompt": {{ text | tojson }}}]}',
            "verdict": {"path": "action", "block_values": ["block"]},
        }
    )
    _mock_transport(scanner, body={"action": "allow"})

    verdict = await scanner.scan(ScanRequest(text='he said "hi"', direction=ScanDirection.OUTPUT))
    assert verdict.allowed is True

    req: httpx.Request = scanner.last_request  # type: ignore[attr-defined]
    assert req.headers["Authorization"] == "Bearer secret-123"
    sent = json.loads(req.content)
    assert sent == {"contents": [{"prompt": 'he said "hi"'}]}
    await scanner.aclose()


@pytest.mark.asyncio
async def test_query_auth_and_fields() -> None:
    """query auth puts the token in the URL; request_fields builds the body."""
    scanner = HttpScanner(
        {
            "endpoint": "https://scan.example/d",
            "auth": {"kind": "query", "param": "key", "token": "qtok"},
            "request_fields": {"prompt": "{{ text }}", "dir": "{{ direction }}"},
            "verdict": {"path": "verdict", "block_values": ["deny"]},
        }
    )
    _mock_transport(scanner, body={"verdict": "ok"})

    await scanner.scan(ScanRequest(text="hello", direction=ScanDirection.INPUT))
    req: httpx.Request = scanner.last_request  # type: ignore[attr-defined]
    assert req.url.params["key"] == "qtok"
    sent = json.loads(req.content)
    assert sent == {"prompt": "hello", "dir": "input"}
    await scanner.aclose()


@pytest.mark.asyncio
async def test_block_on_status() -> None:
    """A status code in block_on_status blocks regardless of body."""
    scanner = HttpScanner(
        {
            "endpoint": "https://scan.example/e",
            "block_on_status": [451],
            "verdict": {"path": "action", "block_values": ["block"]},
        }
    )
    _mock_transport(scanner, status=451, body={"action": "allow"})

    verdict = await scanner.scan(ScanRequest(text="x", direction=ScanDirection.OUTPUT))
    assert verdict.allowed is False
    await scanner.aclose()


@pytest.mark.asyncio
async def test_request_error_propagates() -> None:
    """Transport errors are NOT swallowed — the pipeline handles fail policy."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("boom", request=request)

    scanner = HttpScanner({"endpoint": "https://scan.example/f", "verdict": {"path": "a"}})
    scanner._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), timeout=scanner._timeout
    )

    with pytest.raises(httpx.ConnectTimeout):
        await scanner.scan(ScanRequest(text="x", direction=ScanDirection.OUTPUT))
    await scanner.aclose()


@pytest.mark.asyncio
async def test_missing_endpoint_raises() -> None:
    scanner = HttpScanner({"verdict": {"path": "a"}})
    with pytest.raises(RuntimeError):
        await scanner.scan(ScanRequest(text="x", direction=ScanDirection.OUTPUT))
    await scanner.aclose()
