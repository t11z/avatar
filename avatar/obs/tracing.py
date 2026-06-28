"""OpenTelemetry tracing — optional and degrades to a no-op.

Tracing is enabled via config; if the OTel packages aren't installed or it's
disabled, ``span`` is a context manager that does nothing, so call sites stay
identical either way.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

_tracer: Any | None = None


def configure_tracing(
    enabled: bool, otlp_endpoint: str | None, service_name: str = "avatar"
) -> None:
    global _tracer
    if not enabled:
        _tracer = None
        return
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)
        if otlp_endpoint:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )

            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint))
            )
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer(service_name)
    except Exception:  # pragma: no cover - missing optional deps
        _tracer = None


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[None]:
    if _tracer is None:
        yield
        return
    with _tracer.start_as_current_span(name) as s:  # pragma: no cover - needs otel
        for k, v in attributes.items():
            s.set_attribute(k, v)
        yield
