"""Sprint-13 Track D: observability wiring for Intent execution.

See docs/sprint-13-plan.md §2.6 (authoritative). Exposes a minimal
surface for Track A's ``HassalehRuntime.execute`` to call around each
dispatch:

    with start_root_span(intent, ctx) as root:
        with validate_span():             ...
        with capability_check_span():     ...
        with precondition_span():         ...
        with mutate_span():               ...
    record_metric(intent, result, duration_ms)
    emit_log(intent, ctx, result, duration_ms)

Sprint-12's OTel (``hassaleh.obs.tracing``) and structlog
(``hassaleh.obs.logging``) surfaces are reused directly. A dedicated
``CollectorRegistry`` is used for the two new Sprint-13 metrics so they
coexist with the existing Sprint-12 metric family — the Sprint-12
``hassaleh_intent_duration_seconds`` carries a ``stage`` label, whereas
§2.6 declares an ``intent_type``-labelled histogram under the same
name. Prometheus rejects two same-named metrics with different label
sets in one registry, so this module keeps its own registry.

Caller precondition: the daemon/runtime entrypoint must run
``hassaleh.obs.logging.setup("hassaleh-daemon", env)`` before the first
dispatch. ``emit_log`` deliberately does not configure structlog itself;
it assumes the entrypoint-installed processor chain is already active.
"""

from __future__ import annotations

import contextlib
import threading
from typing import Any, Iterator

from opentelemetry import trace as otel_trace
from prometheus_client import CollectorRegistry, Counter, Histogram

from hassaleh.obs import logging as obs_logging
from hassaleh.obs import tracing as obs_tracing

from .types import Ctx, Intent, Result

_ROOT_SPAN_NAME = "hassaleh.intent.execute"
_CHILD_SPAN_NAMES = (
    "intent.validate",
    "intent.capability_check",
    "intent.precondition",
    "intent.mutate",
)
_DURATION_BUCKETS = (
    0.0005,
    0.001,
    0.002,
    0.005,
    0.01,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)
# Logger names intentionally bypass SERVICE_ENUM; obs_logging.setup() is the
# only SERVICE_ENUM-validated service-name surface.
_LOGGER_NAME = "hassaleh.runtime"

_ALLOWED_INTENT_TYPES = frozenset(
    {
        "market.add-analyst-attempt",
        "market.set-verdict",
        "market.register-writer-attempt",
        "market.update-writer-attempt",
        "market.update-writer-attempt-by-publisher",
        "market.set-published",
    }
)

_registry: CollectorRegistry | None = None
_intent_total: Counter | None = None
_intent_duration: Histogram | None = None
_intent_unknown_type_total: Counter | None = None
_metrics_lock = threading.Lock()


def _ensure_metrics() -> tuple[Counter, Histogram, CollectorRegistry]:
    global _registry, _intent_total, _intent_duration, _intent_unknown_type_total
    if (
        _registry is None
        or _intent_total is None
        or _intent_duration is None
        or _intent_unknown_type_total is None
    ):
        with _metrics_lock:
            if (
                _registry is None
                or _intent_total is None
                or _intent_duration is None
                or _intent_unknown_type_total is None
            ):
                registry = CollectorRegistry(auto_describe=True)
                _registry = registry
                _intent_total = Counter(
                    "hassaleh_intent_total",
                    "Intent dispatch terminal outcomes (Sprint-13 runtime).",
                    labelnames=("intent_type", "result"),
                    registry=registry,
                )
                _intent_duration = Histogram(
                    "hassaleh_intent_duration_seconds",
                    "Intent dispatch wall duration from validate through commit/rollback.",
                    labelnames=("intent_type",),
                    buckets=_DURATION_BUCKETS,
                    registry=registry,
                )
                _intent_unknown_type_total = Counter(
                    "hassaleh_intent_unknown_type_total",
                    "Intent metric emissions with unregistered intent.type labels.",
                    registry=registry,
                )
    if _intent_total is None or _intent_duration is None or _registry is None:
        raise RuntimeError("runtime metrics registry not initialized")
    return _intent_total, _intent_duration, _registry


def initialize_runtime_metrics() -> None:
    """Eagerly initialize the runtime metrics registry when OBS is enabled."""
    if obs_tracing.is_obs_enabled():
        _ensure_metrics()


def get_runtime_registry() -> CollectorRegistry:
    """Dedicated registry for the two Sprint-13 runtime metrics."""
    _, _, registry = _ensure_metrics()
    return registry


@contextlib.contextmanager
def start_root_span(intent: Intent, ctx: Ctx) -> Iterator[Any]:
    """Root span `hassaleh.intent.execute` with `intent.type` and
    `principal.id` attributes per §2.6. Degrades to a no-op context
    when observability is disabled."""
    with obs_tracing.span_context(
        _ROOT_SPAN_NAME,
        **{"intent.type": intent.type, "principal.id": ctx.principal.id},
    ) as span:
        yield span


@contextlib.contextmanager
def _child(name: str) -> Iterator[Any]:
    with obs_tracing.span_context(name) as span:
        yield span


def validate_span():  # noqa: D401 - thin wrapper
    """Child span `intent.validate` (§2.6, step 1 of 4)."""
    return _child(_CHILD_SPAN_NAMES[0])


def capability_check_span():
    """Child span `intent.capability_check` (§2.6, step 2 of 4)."""
    return _child(_CHILD_SPAN_NAMES[1])


def precondition_span():
    """Child span `intent.precondition` (§2.6, step 3 of 4)."""
    return _child(_CHILD_SPAN_NAMES[2])


def mutate_span():
    """Child span `intent.mutate` (§2.6, step 4 of 4)."""
    return _child(_CHILD_SPAN_NAMES[3])


def record_metric(intent: Intent, result: Result, duration_ms: float) -> None:
    """Tick `hassaleh_intent_total{intent_type, result}` and observe
    `hassaleh_intent_duration_seconds{intent_type}`. `result.kind` is
    used verbatim (kebab-case, no translation — §2.6 / G-CR-4).

    Honours `HASSALEH_OBS=off` as a true zero-cost no-op: no registry
    allocation, no counter/histogram tick, no Prometheus client touch."""
    if not obs_tracing.is_obs_enabled():
        return
    counter, histogram, _ = _ensure_metrics()
    intent_type = intent.type
    if intent_type not in _ALLOWED_INTENT_TYPES:
        intent_type = "unknown"
        if _intent_unknown_type_total is None:  # defensive; _ensure_metrics establishes it
            raise RuntimeError("runtime metrics guard counter not initialized")
        _intent_unknown_type_total.inc()
    counter.labels(intent_type=intent_type, result=result.kind).inc()
    histogram.labels(intent_type=intent_type).observe(duration_ms / 1000.0)


def emit_log(
    intent: Intent, ctx: Ctx, result: Result, duration_ms: float
) -> None:
    """Emit one structlog line `"intent executed"` with the §2.6 field
    set: intent_type, trace_id (when a span is recording), duration_ms,
    result, principal_id, and error_code (only when result != "ok").

    Honours `HASSALEH_OBS=off` as a true zero-cost no-op: no logger
    bind, no field assembly, no stdlib log line."""
    if not obs_tracing.is_obs_enabled():
        return
    fields: dict[str, Any] = {
        "intent_type": intent.type,
        "duration_ms": duration_ms,
        "result": result.kind,
        "principal_id": ctx.principal.id,
    }
    trace_id = _current_trace_id()
    if trace_id is not None:
        fields["trace_id"] = trace_id
    if result.kind != "ok" and result.error_code is not None:
        fields["error_code"] = result.error_code

    logger = obs_logging.get_logger(_LOGGER_NAME)
    logger.info("intent executed", **fields)


def _current_trace_id() -> str | None:
    span = otel_trace.get_current_span()
    ctx = span.get_span_context()
    if not ctx.is_valid:
        return None
    return f"{ctx.trace_id:032x}"


def _reset_for_tests() -> None:
    """Tear down module-level metrics so tests can start from zero."""
    global _registry, _intent_total, _intent_duration, _intent_unknown_type_total
    _registry = None
    _intent_total = None
    _intent_duration = None
    _intent_unknown_type_total = None


__all__ = [
    "start_root_span",
    "validate_span",
    "capability_check_span",
    "precondition_span",
    "mutate_span",
    "record_metric",
    "emit_log",
    "get_runtime_registry",
    "initialize_runtime_metrics",
]
