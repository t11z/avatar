"""Health + metrics HTTP server (aiohttp).

Serves ``/healthz`` (liveness), ``/readyz`` (config loaded AND at least one
platform authenticated) and ``/metrics`` (Prometheus exposition).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from aiohttp import web
from prometheus_client import generate_latest

from .metrics import metrics

ReadyCheck = Callable[[], Awaitable[bool]]


def build_app(ready_check: ReadyCheck) -> web.Application:
    app = web.Application()

    async def healthz(_req: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def readyz(_req: web.Request) -> web.Response:
        ready = await ready_check()
        return web.json_response(
            {"status": "ready" if ready else "not-ready"},
            status=200 if ready else 503,
        )

    async def metrics_handler(_req: web.Request) -> web.Response:
        return web.Response(
            body=generate_latest(metrics.registry),
            content_type="text/plain",
        )

    app.add_routes(
        [
            web.get("/healthz", healthz),
            web.get("/readyz", readyz),
            web.get("/metrics", metrics_handler),
        ]
    )
    return app


def _split_addr(addr: str) -> tuple[str, int]:
    host, _, port = addr.rpartition(":")
    return host or "0.0.0.0", int(port)


async def start_health_server(addr: str, ready_check: ReadyCheck) -> web.AppRunner:
    app = build_app(ready_check)
    runner = web.AppRunner(app)
    await runner.setup()
    host, port = _split_addr(addr)
    site = web.TCPSite(runner, host, port)
    await site.start()
    return runner
