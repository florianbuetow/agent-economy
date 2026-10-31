"""Telemetry helper: disabled is a no-op; enabled links logs to spans and propagates."""

from __future__ import annotations

import json
import logging

import httpx
import pytest
from opentelemetry import trace
from opentelemetry.instrumentation.httpx import AsyncOpenTelemetryTransport
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from service_commons.logging import JSONFormatter
from service_commons.telemetry import TelemetryConfig, annotate, current_trace_ids, setup_telemetry

pytestmark = pytest.mark.unit


def test_disabled_telemetry_installs_nothing() -> None:
    logger = logging.getLogger("telemetry-disabled")
    before = list(logger.handlers)
    setup_telemetry(
        TelemetryConfig(
            enabled=False, otlp_endpoint="http://127.0.0.1:1", sample_ratio=1.0, excluded_urls=""
        ),
        service_name="x",
        app=None,
        log_logger=logger,
    )
    assert logger.handlers == before
    assert current_trace_ids() is None


def test_log_lines_carry_trace_ids_and_attributes_land_on_the_span() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    record = logging.LogRecord("svc", logging.INFO, __file__, 1, "hello", None, None)
    with trace.use_span(tracer.start_span("work"), end_on_exit=True):
        annotate(task_id="t-1", amount=50)
        ids = current_trace_ids()
        line = json.loads(JSONFormatter().format(record))
    assert ids is not None
    assert (line["trace_id"], line["span_id"]) == ids
    span = exporter.get_finished_spans()[0]
    assert span.attributes is not None
    assert span.attributes["task_id"] == "t-1"
    assert span.attributes["amount"] == 50


def test_config_has_no_defaults() -> None:
    with pytest.raises(ValueError):
        TelemetryConfig(enabled=True)  # type: ignore[call-arg]


async def test_outbound_httpx_carries_traceparent_when_instrumented() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update({k: v for k, v in request.headers.items() if k == "traceparent"})
        return httpx.Response(200)

    # The global instrumentor wraps httpx's default transports; the same wrapper is
    # exercised here around a mock transport.
    provider = TracerProvider()
    transport = AsyncOpenTelemetryTransport(httpx.MockTransport(handler), tracer_provider=provider)
    async with httpx.AsyncClient(transport=transport) as client:
        with trace.use_span(provider.get_tracer("t").start_span("parent"), end_on_exit=True):
            await client.get("http://svc.test/health")
    assert "traceparent" in seen
