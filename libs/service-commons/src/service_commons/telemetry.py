"""Thin OpenTelemetry setup shared by every service and agent.

One call in ``create_app()`` (or an agent's ``main``) installs tracing for FastAPI and
every outbound httpx client, and ships the service logger's records over OTLP with
their trace ids. Everything is driven by a required ``telemetry:`` config section;
with ``enabled: false`` nothing is installed. Exporters are batch and non-blocking, so
a missing collector only drops telemetry; file logs remain the record.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from opentelemetry import trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from fastapi import FastAPI


class TelemetryConfig(BaseModel):
    """Required ``telemetry:`` section; no defaults."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool
    otlp_endpoint: str
    sample_ratio: float
    excluded_urls: str


_state: dict[str, Any] = {"tracer_provider": None, "logger_provider": None, "handler": None}


def setup_telemetry(
    config: TelemetryConfig,
    *,
    service_name: str,
    app: FastAPI | None,
    log_logger: logging.Logger,
) -> None:
    """Install tracing, httpx propagation and OTLP log export when enabled.

    Idempotent per process: a second call with telemetry already installed only
    instruments the new ``app``.
    """
    if not config.enabled:
        return
    if _state["tracer_provider"] is None:
        resource = Resource.create({"service.name": service_name})
        tracer_provider = TracerProvider(
            resource=resource,
            sampler=ParentBased(TraceIdRatioBased(config.sample_ratio)),
        )
        tracer_provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{config.otlp_endpoint}/v1/traces"))
        )
        trace.set_tracer_provider(tracer_provider)
        logger_provider = LoggerProvider(resource=resource)
        logger_provider.add_log_record_processor(
            BatchLogRecordProcessor(OTLPLogExporter(endpoint=f"{config.otlp_endpoint}/v1/logs"))
        )
        set_logger_provider(logger_provider)
        handler = LoggingHandler(level=logging.NOTSET, logger_provider=logger_provider)
        log_logger.addHandler(handler)
        HTTPXClientInstrumentor().instrument()
        _state.update(
            {
                "tracer_provider": tracer_provider,
                "logger_provider": logger_provider,
                "handler": handler,
            }
        )
    if app is not None:
        FastAPIInstrumentor.instrument_app(app, excluded_urls=config.excluded_urls)


def shutdown_telemetry() -> None:
    """Flush and stop the exporters; safe to call when telemetry was never enabled."""
    tracer_provider = _state["tracer_provider"]
    logger_provider = _state["logger_provider"]
    if tracer_provider is not None:
        tracer_provider.shutdown()
    if logger_provider is not None:
        logger_provider.shutdown()


def annotate(**attributes: str | int | float | bool) -> None:
    """Attach business attributes (``task_id``, ``agent_id``, amounts) to the current span."""
    span = trace.get_current_span()
    if span.is_recording():
        span.set_attributes(attributes)


def current_trace_ids() -> tuple[str, str] | None:
    """``(trace_id, span_id)`` as hex when inside a recorded span, else ``None``."""
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return None
    return format(context.trace_id, "032x"), format(context.span_id, "016x")
