"""Tests for hassaleh.runtime.observability (Sprint 13, Track D).

Covers §2.6 (authoritative):

  * Root span `hassaleh.intent.execute` with attributes `intent.type`,
    `principal.id`, and four child spans in order:
        intent.validate
        intent.capability_check
        intent.precondition
        intent.mutate
  * Counter `hassaleh_intent_total{intent_type, result}` — two labels only.
  * Histogram `hassaleh_intent_duration_seconds{intent_type}`.
  * One structlog line per intent with fields:
        intent_type, trace_id, duration_ms, result,
        principal_id, and error_code (only when result != "ok").
  * Result label values are kebab-case, passed through from `Result.kind`
    with no runtime translation (§2.6, G-CR-4).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.util._once import Once
from prometheus_client import REGISTRY as DEFAULT_PROM_REGISTRY

from hassaleh.obs import logging as obs_logging
from hassaleh.obs import tracing as obs_tracing
from hassaleh.runtime import Ctx, Intent, Principal, Result
from hassaleh.runtime import observability as obs


class _InMemoryExporter(SpanExporter):
    def __init__(self) -> None:
        self.spans: list = []

    def export(self, spans):
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self):
        return None


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    """Isolate every test: fresh metrics registry, no ambient OTel provider,
    no leaked env vars."""

    for var in (
        "HASSALEH_OBS",
        "HASSALEH_ENV",
        "HASSALEH_TRACE_FORCE",
        "HASSALEH_TRACE_SAMPLE_RATE",
        "HASSALEH_LOG_LEVEL",
    ):
        monkeypatch.delenv(var, raising=False)

    def _reset_otel_global_once():
        # Reset OTel's process-wide once-guard so setup_tracing() can install
        # a fresh provider in later tests (and in later test files) without
        # triggering "Overriding of current TracerProvider" on stderr.
        otel_trace._TRACER_PROVIDER_SET_ONCE = Once()
        otel_trace._TRACER_PROVIDER = None

    obs._reset_for_tests()
    obs_tracing.shutdown_tracing()
    _reset_otel_global_once()
    yield
    obs._reset_for_tests()
    obs_tracing.shutdown_tracing()
    _reset_otel_global_once()


def _ctx(scopes=("market.analyst.write",), principal_id="api-key-7") -> Ctx:
    return Ctx(
        principal=Principal(id=principal_id, scopes=tuple(scopes)),
        now=datetime(2026, 4, 24, 12, 0, tzinfo=timezone.utc),
    )


def _intent(type_="market.add-analyst-attempt", **payload) -> Intent:
    return Intent(type=type_, payload=dict(payload) or {"segment": "us"})


# ── §2.6 Metrics ─────────────────────────────────────────────────────────


def test_record_metric_ticks_counter_with_intent_type_and_kebab_case_result(monkeypatch):
    """hassaleh_intent_total{intent_type, result} — two labels only.

    `result` uses kebab-case verbatim from `Result.kind` (no translation)."""
    monkeypatch.setenv("HASSALEH_OBS", "on")
    intent = _intent()
    result = Result(
        kind="precondition-failed",
        data=None,
        error_code="terminal-verdict-exists",
        error_message=None,
    )

    obs.record_metric(intent, result, duration_ms=42.5)

    registry = obs.get_runtime_registry()
    value = registry.get_sample_value(
        "hassaleh_intent_total",
        {"intent_type": "market.add-analyst-attempt", "result": "precondition-failed"},
    )
    assert value == 1.0


def test_record_metric_ticks_histogram_with_intent_type_label_only(monkeypatch):
    """hassaleh_intent_duration_seconds has exactly one label: intent_type."""
    monkeypatch.setenv("HASSALEH_OBS", "on")
    intent = _intent(type_="market.set-verdict")
    result = Result(kind="ok", data=None, error_code=None, error_message=None)

    obs.record_metric(intent, result, duration_ms=100.0)

    registry = obs.get_runtime_registry()
    count = registry.get_sample_value(
        "hassaleh_intent_duration_seconds_count",
        {"intent_type": "market.set-verdict"},
    )
    total = registry.get_sample_value(
        "hassaleh_intent_duration_seconds_sum",
        {"intent_type": "market.set-verdict"},
    )
    assert count == 1.0
    # duration passed in ms, exposed in seconds.
    assert total == pytest.approx(0.100, rel=1e-6)


def test_counter_has_only_intent_type_and_result_labels(monkeypatch):
    """G-CR-3: capability_granted label was removed; two labels only."""
    monkeypatch.setenv("HASSALEH_OBS", "on")
    obs.record_metric(
        _intent(),
        Result(kind="ok", data=None, error_code=None, error_message=None),
        duration_ms=1.0,
    )
    registry = obs.get_runtime_registry()
    # Walk the metric families and find the sample for `hassaleh_intent_total`
    # (Prometheus strips the `_total` suffix when naming the family).
    total_samples = [
        sample
        for family in registry.collect()
        for sample in family.samples
        if sample.name == "hassaleh_intent_total"
    ]
    assert total_samples, "hassaleh_intent_total sample not emitted"
    # Labels on the counter must match §2.6 exactly (G-CR-3: no
    # capability_granted).
    assert set(total_samples[0].labels.keys()) == {"intent_type", "result"}


@pytest.mark.parametrize(
    "kind",
    [
        "ok",
        "precondition-failed",
        "capability-denied",
        "validation-error",
        "internal-error",
    ],
)
def test_record_metric_per_label_tick_for_every_result_kind(kind, monkeypatch):
    """Every §2.2 `ResultKind` value survives the round-trip unchanged
    (kebab-case hyphens, no underscore translation — G-CR-4)."""
    monkeypatch.setenv("HASSALEH_OBS", "on")
    intent = _intent()
    result = Result(
        kind=kind,  # type: ignore[arg-type]
        data=None,
        error_code=None if kind == "ok" else "x",
        error_message=None,
    )

    obs.record_metric(intent, result, duration_ms=1.0)

    registry = obs.get_runtime_registry()
    value = registry.get_sample_value(
        "hassaleh_intent_total",
        {"intent_type": intent.type, "result": kind},
    )
    assert value == 1.0, f"expected one tick for result={kind!r}"


# ── §2.6 Tracing: four child spans in order ──────────────────────────────


def test_four_child_spans_under_root_in_canonical_order(monkeypatch):
    """Root span `hassaleh.intent.execute` has exactly four children in the
    canonical order validate → capability_check → precondition → mutate."""
    monkeypatch.setenv("HASSALEH_OBS", "on")
    monkeypatch.setenv("HASSALEH_ENV", "dev")
    exporter = _InMemoryExporter()
    obs_tracing.setup_tracing(
        service_name="hassaleh-test", env="dev", sample_rate=1.0, exporter=exporter
    )

    intent = _intent()
    ctx = _ctx()
    with obs.start_root_span(intent, ctx):
        with obs.validate_span():
            pass
        with obs.capability_check_span():
            pass
        with obs.precondition_span():
            pass
        with obs.mutate_span():
            pass

    # Spans export on close; children finish first.
    names = [s.name for s in exporter.spans]
    # Root is the last to close (wraps the others).
    assert names[-1] == "hassaleh.intent.execute"

    # Children — in the order they were closed — must match §2.6 exactly.
    child_names = names[:-1]
    assert child_names == [
        "intent.validate",
        "intent.capability_check",
        "intent.precondition",
        "intent.mutate",
    ]

    root = exporter.spans[-1]
    # Each child's parent id equals root.span_id.
    for child in exporter.spans[:-1]:
        assert child.parent is not None, f"{child.name} has no parent"
        assert child.parent.span_id == root.context.span_id, (
            f"{child.name} not parented to hassaleh.intent.execute"
        )


def test_root_span_carries_intent_type_and_principal_id_attributes(monkeypatch):
    """§2.6: root span attributes are `intent.type` and `principal.id`."""
    monkeypatch.setenv("HASSALEH_OBS", "on")
    monkeypatch.setenv("HASSALEH_ENV", "dev")
    exporter = _InMemoryExporter()
    obs_tracing.setup_tracing(
        service_name="hassaleh-test", env="dev", sample_rate=1.0, exporter=exporter
    )

    intent = _intent(type_="market.set-verdict")
    ctx = _ctx(principal_id="api-key-alpha")
    with obs.start_root_span(intent, ctx):
        pass

    root = next(s for s in exporter.spans if s.name == "hassaleh.intent.execute")
    assert root.attributes["intent.type"] == "market.set-verdict"
    assert root.attributes["principal.id"] == "api-key-alpha"


def test_no_result_child_span(monkeypatch):
    """§2.6 G-CR-2: no `intent.result` child span — result is the return
    value of execute(), captured by the root span's attributes, not a span."""
    monkeypatch.setenv("HASSALEH_OBS", "on")
    monkeypatch.setenv("HASSALEH_ENV", "dev")
    exporter = _InMemoryExporter()
    obs_tracing.setup_tracing(
        service_name="hassaleh-test", env="dev", sample_rate=1.0, exporter=exporter
    )

    with obs.start_root_span(_intent(), _ctx()):
        with obs.validate_span():
            pass

    names = {s.name for s in exporter.spans}
    assert "intent.result" not in names


# ── §2.6 Logs: one structlog line per intent, field presence ─────────────


def _parse_stderr_json(captured: str) -> dict:
    # Multiple lines may appear during obs setup; grab the one our test emits.
    lines = [ln for ln in captured.strip().splitlines() if ln.strip()]
    for line in reversed(lines):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("msg") == "intent executed":
            return payload
    raise AssertionError(f"no 'intent executed' log line in:\n{captured}")


def test_emit_log_has_required_fields_on_success(monkeypatch, capsys):
    """Success path: intent_type, result, duration_ms, principal_id, trace_id —
    `error_code` MUST be absent when result == "ok"."""
    monkeypatch.setenv("HASSALEH_OBS", "on")
    monkeypatch.setenv("HASSALEH_ENV", "prod")
    obs_logging.setup("hassaleh-daemon", "prod")
    exporter = _InMemoryExporter()
    obs_tracing.setup_tracing(
        service_name="hassaleh-test", env="prod", sample_rate=1.0, exporter=exporter
    )

    intent = _intent()
    ctx = _ctx(principal_id="api-key-log")
    result = Result(kind="ok", data={"attempt_id": "a-1"}, error_code=None, error_message=None)

    with obs.start_root_span(intent, ctx) as root:
        expected_trace_id = f"{root.get_span_context().trace_id:032x}"
        obs.emit_log(intent, ctx, result, duration_ms=7.25)

    payload = _parse_stderr_json(capsys.readouterr().err)
    assert payload["intent_type"] == "market.add-analyst-attempt"
    assert payload["result"] == "ok"
    assert payload["duration_ms"] == 7.25
    assert payload["principal_id"] == "api-key-log"
    assert payload["trace_id"] == expected_trace_id
    assert "error_code" not in payload


def test_emit_log_includes_error_code_on_non_ok(monkeypatch, capsys):
    """§2.6: `error_code` present only when result != "ok"."""
    monkeypatch.setenv("HASSALEH_OBS", "on")
    monkeypatch.setenv("HASSALEH_ENV", "prod")
    obs_logging.setup("hassaleh-daemon", "prod")

    intent = _intent()
    ctx = _ctx()
    result = Result(
        kind="capability-denied",
        data=None,
        error_code="scope-not-granted",
        error_message="missing required capability",
    )

    obs.emit_log(intent, ctx, result, duration_ms=0.1)

    payload = _parse_stderr_json(capsys.readouterr().err)
    assert payload["result"] == "capability-denied"
    assert payload["error_code"] == "scope-not-granted"


def test_emit_log_omits_trace_id_when_no_span_active(monkeypatch, capsys):
    """When no span is recording, trace_id is absent from the log fields
    (OTel `get_current_span()` yields an invalid context)."""
    monkeypatch.setenv("HASSALEH_OBS", "on")
    monkeypatch.setenv("HASSALEH_ENV", "prod")
    obs_logging.setup("hassaleh-daemon", "prod")
    # Deliberately do NOT configure a TracerProvider — get_current_span() will
    # return a non-recording INVALID_SPAN.

    obs.emit_log(
        _intent(),
        _ctx(),
        Result(kind="ok", data=None, error_code=None, error_message=None),
        duration_ms=1.0,
    )

    payload = _parse_stderr_json(capsys.readouterr().err)
    assert "trace_id" not in payload


# ── §4.2 / §5.2 Isolation & HASSALEH_OBS=off ──────────────────────────────


def test_record_metric_noop_when_obs_off(monkeypatch):
    """record_metric() with HASSALEH_OBS=off must NOT increment the counter."""
    intent = _intent()
    result = Result(kind="ok", data=None, error_code=None, error_message=None)

    monkeypatch.setenv("HASSALEH_OBS", "on")
    obs.record_metric(intent, result, duration_ms=10.0)
    registry = obs.get_runtime_registry()
    before = registry.get_sample_value(
        "hassaleh_intent_total",
        {"intent_type": intent.type, "result": "ok"},
    )

    monkeypatch.setenv("HASSALEH_OBS", "off")
    obs.record_metric(intent, result, duration_ms=10.0)
    after = registry.get_sample_value(
        "hassaleh_intent_total",
        {"intent_type": intent.type, "result": "ok"},
    )
    assert after == before == 1.0


def test_emit_log_noop_when_obs_off(monkeypatch, capsys):
    """emit_log() with HASSALEH_OBS=off must produce no structured-log output."""
    monkeypatch.setenv("HASSALEH_OBS", "off")
    monkeypatch.setenv("HASSALEH_ENV", "prod")
    obs_logging.setup("hassaleh-daemon", "prod")

    intent = _intent()
    ctx = _ctx()
    result = Result(kind="ok", data=None, error_code=None, error_message=None)
    obs.emit_log(intent, ctx, result, duration_ms=1.0)

    captured = capsys.readouterr()
    assert "intent executed" not in captured.err
    assert "intent executed" not in captured.out


def test_cross_registry_isolation(monkeypatch):
    """Ensure record_metric() uses the dedicated registry, not the default one."""
    monkeypatch.setenv("HASSALEH_OBS", "on")
    intent = _intent()
    result = Result(kind="ok", data=None, error_code=None, error_message=None)
    obs.record_metric(intent, result, duration_ms=1.0)
    val = DEFAULT_PROM_REGISTRY.get_sample_value(
        "hassaleh_intent_total",
        {"intent_type": intent.type, "result": "ok"},
    )
    assert val is None


# ── helpers are importable for Track A ───────────────────────────────────


def test_public_surface_is_exported():
    """Track A imports these names — ensure they exist on the module."""
    for name in (
        "start_root_span",
        "validate_span",
        "capability_check_span",
        "precondition_span",
        "mutate_span",
        "record_metric",
        "emit_log",
        "get_runtime_registry",
    ):
        assert hasattr(obs, name), f"observability module missing {name!r}"
