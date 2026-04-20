"""OpenTelemetry tracing helpers for Hassaleh observability (Sprint 12 / Track C).

This module is intentionally self-contained so Track C can land before
Track A (structured logging) and Track B (metrics) wire it into the daemon.
All helpers are safe to import when observability is disabled: they either
become no-ops or return ``None``.
"""

from __future__ import annotations

import contextlib
import contextvars
import functools
import os
from collections.abc import Awaitable, Callable, Iterator
from typing import Any, ParamSpec, TypeVar, cast

from opentelemetry import context as otel_context
from opentelemetry import propagate, trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.sampling import (
    ALWAYS_OFF,
    ALWAYS_ON,
    ParentBased,
    TraceIdRatioBased,
)
from opentelemetry.trace import NonRecordingSpan, SpanKind
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

TRACEPARENT_KEY = "traceparent"
DEFAULT_SAMPLE_RATE = 0.10
_DEFAULT_SERVICE_NAME = "hassaleh"
_DEFAULT_ENV = "dev"

_STAGE_SPAN_NAMES = {
    "auth": "intent.auth",
    "validate": "intent.validate",
    "execute": "intent.execute",
    "persist": "intent.persist_result",
    "result": "intent.result",
}

_FORCE_SAMPLE_VAR: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "hassaleh_trace_force", default=False
)

_CONFIGURED_PROVIDER: TracerProvider | None = None
_CONFIGURED_PROCESSOR: BatchSpanProcessor | None = None
_CONFIGURED_EXPORTER: SpanExporter | None = None

P = ParamSpec("P")
R = TypeVar("R")


class _ForceAwareSampler:
    """Sampler wrapper honouring HASSALEH_TRACE_FORCE and local force hints."""

    def __init__(self, fallback) -> None:
        self._fallback = fallback

    def should_sample(self, parent_context, trace_id, name, kind=None, attributes=None, links=None, trace_state=None):
        attributes = attributes or {}
        if _should_force_sample() or attributes.get("hassaleh.force_sample"):
            return ALWAYS_ON.should_sample(
                parent_context,
                trace_id,
                name,
                kind=kind,
                attributes=attributes,
                links=links,
                trace_state=trace_state,
            )
        return self._fallback.should_sample(
            parent_context,
            trace_id,
            name,
            kind=kind,
            attributes=attributes,
            links=links,
            trace_state=trace_state,
        )

    def get_description(self) -> str:
        return f"force-aware({self._fallback.get_description()})"


def _env_flag(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip().lower()


def _should_force_sample() -> bool:
    return _env_flag("HASSALEH_TRACE_FORCE") in {"1", "true", "yes", "on"} or _FORCE_SAMPLE_VAR.get()


def is_obs_enabled() -> bool:
    return _env_flag("HASSALEH_OBS", "off") == "on"


def _parse_sample_rate(value: str | None) -> float:
    if value is None or value == "":
        return DEFAULT_SAMPLE_RATE
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return DEFAULT_SAMPLE_RATE
    return min(1.0, max(0.0, parsed))


def _build_sampler(sample_rate: float):
    if _should_force_sample():
        return ALWAYS_ON
    return _ForceAwareSampler(ParentBased(root=TraceIdRatioBased(sample_rate)))


def _build_exporter(endpoint: str | None = None, *, exporter: SpanExporter | None = None) -> SpanExporter:
    if exporter is not None:
        return exporter
    return OTLPSpanExporter(endpoint=endpoint or os.getenv("HASSALEH_OTLP_ENDPOINT"))


def setup_tracing(
    service_name: str = _DEFAULT_SERVICE_NAME,
    *,
    env: str | None = None,
    endpoint: str | None = None,
    sample_rate: float | None = None,
    exporter: SpanExporter | None = None,
) -> TracerProvider | None:
    """Configure the global tracer provider.

    Returns the configured provider when observability is enabled, otherwise
    ``None`` and leaves the ambient default provider untouched.
    """

    global _CONFIGURED_PROVIDER, _CONFIGURED_PROCESSOR, _CONFIGURED_EXPORTER

    if not is_obs_enabled():
        _CONFIGURED_PROVIDER = None
        _CONFIGURED_PROCESSOR = None
        _CONFIGURED_EXPORTER = None
        return None

    shutdown_tracing()

    chosen_rate = DEFAULT_SAMPLE_RATE if sample_rate is None else sample_rate
    if sample_rate is None:
        chosen_rate = _parse_sample_rate(os.getenv("HASSALEH_TRACE_SAMPLE_RATE"))

    resource = Resource.create(
        {
            "service.name": service_name,
            "deployment.environment": env or os.getenv("HASSALEH_ENV", _DEFAULT_ENV),
        }
    )
    provider = TracerProvider(resource=resource, sampler=_build_sampler(chosen_rate))
    trace.set_tracer_provider(provider)

    configured_exporter = _build_exporter(endpoint, exporter=exporter)
    processor = (
        SimpleSpanProcessor(configured_exporter)
        if exporter is not None
        else BatchSpanProcessor(configured_exporter)
    )
    provider.add_span_processor(processor)

    _CONFIGURED_PROVIDER = provider
    _CONFIGURED_PROCESSOR = processor
    _CONFIGURED_EXPORTER = configured_exporter
    return provider


def shutdown_tracing(timeout_millis: int = 5000) -> None:
    global _CONFIGURED_PROVIDER, _CONFIGURED_PROCESSOR, _CONFIGURED_EXPORTER

    if _CONFIGURED_PROVIDER is not None:
        _CONFIGURED_PROVIDER.shutdown()
    elif _CONFIGURED_PROCESSOR is not None:
        _CONFIGURED_PROCESSOR.shutdown()
    elif _CONFIGURED_EXPORTER is not None:
        _CONFIGURED_EXPORTER.shutdown()

    _CONFIGURED_PROVIDER = None
    _CONFIGURED_PROCESSOR = None
    _CONFIGURED_EXPORTER = None


@contextlib.contextmanager
def hint_error_prone() -> Iterator[None]:
    """Force sampling for the enclosed span tree.

    This is the Track C implementation of the v1 head-based "likely to be
    error-prone" escape hatch described in the Sprint 12 plan.
    """

    token = _FORCE_SAMPLE_VAR.set(True)
    try:
        yield
    finally:
        _FORCE_SAMPLE_VAR.reset(token)


@contextlib.contextmanager
def span_context(name: str, **attributes: Any) -> Iterator[Any]:
    """Create a span context manager that degrades to a no-op when disabled."""

    if not is_obs_enabled():
        yield None
        return

    if _CONFIGURED_PROVIDER:
        tracer = _CONFIGURED_PROVIDER.get_tracer("hassaleh.obs.tracing")
    else:
        tracer = trace.get_tracer("hassaleh.obs.tracing")
    with tracer.start_as_current_span(name, attributes=attributes or None) as span:
        yield span


@contextlib.contextmanager
def stage_span(stage: str, **attributes: Any) -> Iterator[Any]:
    span_name = _STAGE_SPAN_NAMES[stage]
    with span_context(span_name, **attributes):
        yield


@contextlib.contextmanager
def auth_stage(**attributes: Any) -> Iterator[Any]:
    with stage_span("auth", **attributes):
        yield


@contextlib.contextmanager
def validate_stage(**attributes: Any) -> Iterator[Any]:
    with stage_span("validate", **attributes):
        yield


@contextlib.contextmanager
def execute_stage(**attributes: Any) -> Iterator[Any]:
    with stage_span("execute", **attributes):
        yield


@contextlib.contextmanager
def persist_stage(**attributes: Any) -> Iterator[Any]:
    with stage_span("persist", **attributes):
        yield


@contextlib.contextmanager
def result_stage(**attributes: Any) -> Iterator[Any]:
    with stage_span("result", **attributes):
        yield


def trace_span(name: str) -> Callable[[Callable[P, R] | Callable[P, Awaitable[R]]], Callable[P, R] | Callable[P, Awaitable[R]]]:
    """Decorator that wraps sync or async functions in a span."""

    def decorator(func: Callable[P, R] | Callable[P, Awaitable[R]]):
        if not is_obs_enabled():
            return func

        if callable(getattr(func, "__await__", None)):
            # Defensive branch for unusual awaitable callables.
            pass

        if _is_async_callable(func):
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
                with span_context(name):
                    return await cast(Callable[P, Awaitable[R]], func)(*args, **kwargs)

            return functools.wraps(func)(async_wrapper)

        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            with span_context(name):
                return cast(Callable[P, R], func)(*args, **kwargs)

        return functools.wraps(func)(sync_wrapper)

    return decorator


def _is_async_callable(func: Any) -> bool:
    import inspect

    return inspect.iscoroutinefunction(func)


def get_current_traceparent() -> str | None:
    """Return the current span context in W3C traceparent format."""

    span = trace.get_current_span()
    if span is None or isinstance(span, NonRecordingSpan):
        return None

    ctx = span.get_span_context()
    if not ctx.is_valid:
        return None

    flags = int(ctx.trace_flags)
    return f"00-{ctx.trace_id:032x}-{ctx.span_id:016x}-{flags:02x}"


def extract_traceparent(traceparent: str | None):
    """Extract W3C trace context from Intent.traceparent for daemon use."""

    if not traceparent:
        return otel_context.Context()
    carrier = {TRACEPARENT_KEY: traceparent}
    return TraceContextTextMapPropagator().extract(carrier=carrier)


def inject_current_context() -> dict[str, str]:
    carrier: dict[str, str] = {}
    if is_obs_enabled():
        propagate.inject(carrier)
    return carrier


def start_span_from_traceparent(name: str, traceparent: str | None, **attributes: Any):
    """Helper for daemon-side resumption of a trace from Intent.traceparent."""

    if not is_obs_enabled():
        return contextlib.nullcontext(None)

    if _CONFIGURED_PROVIDER:
        tracer = _CONFIGURED_PROVIDER.get_tracer("hassaleh.obs.tracing")
    else:
        tracer = trace.get_tracer("hassaleh.obs.tracing")
    parent_context = extract_traceparent(traceparent)
    return tracer.start_as_current_span(
        name,
        context=parent_context,
        kind=SpanKind.INTERNAL,
        attributes=attributes or None,
    )
