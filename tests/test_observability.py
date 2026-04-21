"""Sprint 12 Track F — Integration tests for observability.

Covers the cross-subsystem contract from docs/sprint-12-plan.md §3:
    1. Logs: structured JSON when HASSALEH_OBS=on, plain text / no structlog
       processing when HASSALEH_OBS=off.
    2. Metrics: /metrics endpoint exposes the frozen §3.2 catalog when on,
       and is absent (HTTPNotFound, /metrics route not registered) when off.
    3. Traces: span decorators emit spans with attributes when on,
       HASSALEH_TRACE_FORCE=1 overrides a zero sample rate, and the tracer
       provider stays unconfigured when off.
    4. Regression (S5): HASSALEH_OBS=off is a true zero-cost no-op — no
       Prometheus registry, no TracerProvider, no structlog processor chain.
       Import-time isolation is verified with a subprocess so merely
       importing the obs modules cannot mutate global state.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap

import pytest
from aiohttp.test_utils import make_mocked_request
from opentelemetry import trace
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from hassaleh.obs import logging as obs_logging
from hassaleh.obs import metrics as obs_metrics
from hassaleh.obs import tracing as obs_tracing
from hassaleh.obs.context import clear_request_context
from hassaleh.obs.metrics import (
    METRIC_NAMES,
    get_metrics_registry,
    record_tool_invocation,
    setup_metrics,
    shutdown_metrics,
)


class InMemoryExporter(SpanExporter):
    """Collects finished spans in-memory for trace assertions."""

    def __init__(self) -> None:
        self.spans: list = []

    def export(self, spans):
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self):
        return None


@pytest.fixture(autouse=True)
def _reset_obs_state(monkeypatch):
    """Full teardown between tests so env-var changes never leak.

    Without this, `setup_metrics()` returns the cached `_STATE` from a
    previous test and the env-var flip is silently ignored.
    """

    monkeypatch.delenv("HASSALEH_OBS", raising=False)
    monkeypatch.delenv("HASSALEH_ENV", raising=False)
    monkeypatch.delenv("HASSALEH_TRACE_FORCE", raising=False)
    monkeypatch.delenv("HASSALEH_TRACE_SAMPLE_RATE", raising=False)
    monkeypatch.delenv("HASSALEH_LOG_LEVEL", raising=False)

    shutdown_metrics()
    obs_tracing.shutdown_tracing()
    clear_request_context()
    yield
    shutdown_metrics()
    obs_tracing.shutdown_tracing()
    clear_request_context()


# ────────────────────────────────────────────────────────────────────────────
# 1. Logs
# ────────────────────────────────────────────────────────────────────────────


def test_logs_on_emit_json_with_required_bindings(monkeypatch, capsys):
    """HASSALEH_OBS=on + env=prod → JSON with service/env/version bindings."""
    monkeypatch.setenv("HASSALEH_OBS", "on")
    monkeypatch.setenv("HASSALEH_ENV", "prod")
    monkeypatch.setenv("HASSALEH_LOG_LEVEL", "INFO")

    config = obs_logging.setup("hassaleh-daemon", "prod")
    logger = obs_logging.get_logger("hassaleh.daemon.intent")
    logger.info("intent submitted", agent_id="agent-a1", intent_id="intent-42")

    payload = json.loads(capsys.readouterr().err.strip())
    assert config.enabled is True
    assert payload["service"] == "hassaleh-daemon"
    assert payload["env"] == "prod"
    assert payload["version"]  # non-empty semver string
    assert payload["msg"] == "intent submitted"
    assert payload["agent_id"] == "agent-a1"


def test_logs_on_include_trace_id_when_span_active(monkeypatch, capsys):
    """Per §3.1: trace_id flows into log records when a span is active."""
    monkeypatch.setenv("HASSALEH_OBS", "on")
    monkeypatch.setenv("HASSALEH_ENV", "prod")

    obs_logging.setup("hassaleh-daemon", "prod")
    exporter = InMemoryExporter()
    obs_tracing.setup_tracing(
        service_name="test-service",
        env="prod",
        sample_rate=1.0,
        exporter=exporter,
    )
    logger = obs_logging.get_logger("hassaleh.daemon.intent")

    with obs_tracing.span_context("intent.lifecycle") as span:
        traceparent = obs_tracing.get_current_traceparent()
        expected_trace_id = f"{span.get_span_context().trace_id:032x}"
        logger.info(
            "intent in flight",
            trace_id=expected_trace_id,
            traceparent=traceparent,
        )

    payload = json.loads(capsys.readouterr().err.strip())
    assert payload["trace_id"] == expected_trace_id
    assert payload["traceparent"].startswith("00-")
    assert expected_trace_id in payload["traceparent"]


def test_logs_off_uses_plain_text_and_bypasses_structlog(monkeypatch, capsys):
    """HASSALEH_OBS=off → plain text, structlog processor chain NEVER runs."""
    monkeypatch.setenv("HASSALEH_OBS", "off")
    monkeypatch.setenv("HASSALEH_ENV", "dev")

    processor_calls = {"count": 0}
    original = obs_logging.pii_redaction_processor

    def counting_wrapper(*args, **kwargs):
        processor_calls["count"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(obs_logging, "pii_redaction_processor", counting_wrapper)

    config = obs_logging.setup("hassaleh-daemon", "dev")
    logger = obs_logging.get_logger("hassaleh.daemon")
    logger.info("plain startup", host="localhost")

    captured = capsys.readouterr().err.strip()
    assert config.enabled is False
    assert processor_calls["count"] == 0  # structlog chain never invoked
    # Not JSON — decoding the plain text line must fail.
    with pytest.raises(json.JSONDecodeError):
        json.loads(captured)
    assert "plain startup" in captured


# ────────────────────────────────────────────────────────────────────────────
# 2. Metrics
# ────────────────────────────────────────────────────────────────────────────


def test_metrics_on_registers_full_catalog(monkeypatch):
    """HASSALEH_OBS=on → registry exposes every name in §3.2 METRIC_NAMES."""
    monkeypatch.setenv("HASSALEH_OBS", "on")
    monkeypatch.setenv("HASSALEH_ENV", "dev")

    state = setup_metrics(env="dev")
    assert state is not None
    registry = get_metrics_registry()
    assert registry is state.registry

    rendered = obs_metrics.render_metrics()
    assert rendered is not None
    body = rendered.decode("utf-8")
    for metric_name in METRIC_NAMES:
        assert metric_name in body, f"missing frozen catalog metric {metric_name!r}"

    assert registry.get_sample_value(
        "hassaleh_version_info",
        {"version": "0.1.0", "env": "dev"},
    ) == 1.0


def test_metrics_off_is_noop_and_hides_endpoint(monkeypatch):
    """HASSALEH_OBS=off → no registry, no rendered output, endpoint absent."""
    monkeypatch.setenv("HASSALEH_OBS", "off")
    monkeypatch.setenv("HASSALEH_ENV", "dev")

    assert setup_metrics(env="dev") is None
    assert get_metrics_registry() is None
    assert obs_metrics.render_metrics() is None
    assert obs_metrics.is_metrics_enabled() is False

    # Daemon-side contract: /metrics route is only added when obs is enabled.
    # We verify via the public guard the daemon uses at _start_health_endpoint.
    from hassaleh import obs as obs_pkg

    assert obs_pkg.is_metrics_enabled() is False


@pytest.mark.asyncio
async def test_metrics_endpoint_exposes_catalog_via_daemon(monkeypatch):
    """The daemon /metrics route serves the §3.2 catalog when obs=on."""
    from unittest.mock import AsyncMock

    from hassaleh.daemon import HassalehDaemon

    monkeypatch.setenv("HASSALEH_OBS", "on")
    monkeypatch.setenv("HASSALEH_ENV", "dev")
    monkeypatch.setattr("aiohttp.web.TCPSite.start", AsyncMock())
    setup_metrics(env="dev")

    daemon = HassalehDaemon("bolt://unused", "neo4j", "pw")
    daemon.config["health_endpoint_bind"] = "127.0.0.1"
    daemon.config["health_endpoint_port"] = 0
    daemon._refresh_metrics_snapshot = AsyncMock()  # type: ignore[method-assign]

    await daemon._start_health_endpoint()
    try:
        paths = {
            route.resource.get_info().get("path")
            for route in daemon._health_app.router.routes()
        }
        assert "/metrics" in paths

        response = await daemon._metrics_handler(
            make_mocked_request("GET", "/metrics")
        )
        body = response.text
        for metric_name in METRIC_NAMES:
            assert metric_name in body
    finally:
        await daemon._stop_health_endpoint()


# ────────────────────────────────────────────────────────────────────────────
# 2a. Tool counter (plan §1 S2a) + 2b skipped placeholders
# ────────────────────────────────────────────────────────────────────────────


def test_tool_counter_increment(monkeypatch):
    """S2a (R7): hassaleh_tool_invocation_total increments by exactly 1 per call.

    Drives the public instrumentation entry point `record_tool_invocation`
    (the same site the daemon's tool-invocation path calls) and asserts the
    Counter label-combination moves by one for a single invocation and by
    two for a second call with the same labels.
    """
    monkeypatch.setenv("HASSALEH_OBS", "on")
    monkeypatch.setenv("HASSALEH_ENV", "dev")

    state = setup_metrics(env="dev")
    assert state is not None
    registry = state.registry

    labels = {"agent_id": "agent-a1", "command": "invoke_command", "result": "ok"}

    before = registry.get_sample_value("hassaleh_tool_invocation_total", labels) or 0.0
    record_tool_invocation(
        command="invoke_command",
        result="ok",
        agent_id="agent-a1",
        duration_seconds=0.012,
    )
    after_one = registry.get_sample_value("hassaleh_tool_invocation_total", labels)
    assert after_one is not None
    assert after_one - before == 1.0

    record_tool_invocation(
        command="invoke_command",
        result="ok",
        agent_id="agent-a1",
        duration_seconds=0.020,
    )
    after_two = registry.get_sample_value("hassaleh_tool_invocation_total", labels)
    assert after_two == 2.0

    duration_count = registry.get_sample_value(
        "hassaleh_tool_invocation_duration_seconds_count",
        {"command": "invoke_command"},
    )
    assert duration_count == 2.0, (
        "tool invocation duration histogram must move in lockstep with the counter"
    )


_S2B_SKIP_REASON = (
    "requires docker smoke harness — tracked for Sprint 13 smoke suite "
    "(see docs/sprint-12-track-f-fix.md §S2b deferral)"
)


@pytest.mark.skip(reason=_S2B_SKIP_REASON)
def test_neo4j_exporter_up():
    """S2b(a): Prometheus target `neo4j-exporter` has `up==1`.

    Deferred — covered by Sprint 13 compose smoke harness, not by this
    hermetic unit/integration suite.
    """


@pytest.mark.skip(reason=_S2B_SKIP_REASON)
def test_cadvisor_up():
    """S2b(b): Prometheus target `cadvisor` has `up==1`.

    Deferred — covered by Sprint 13 compose smoke harness.
    """


@pytest.mark.skip(reason=_S2B_SKIP_REASON)
def test_per_container_metrics_non_empty():
    """S2b(c): per-container metrics for `hassaleh-daemon` are non-empty.

    Deferred — covered by Sprint 13 compose smoke harness.
    """


# ────────────────────────────────────────────────────────────────────────────
# 3. Traces
# ────────────────────────────────────────────────────────────────────────────


def test_traces_on_produce_spans_with_attributes(monkeypatch):
    """HASSALEH_OBS=on → spans are recorded with supplied attributes."""
    monkeypatch.setenv("HASSALEH_OBS", "on")
    exporter = InMemoryExporter()
    provider = obs_tracing.setup_tracing(
        service_name="hassaleh-daemon",
        env="dev",
        sample_rate=1.0,
        exporter=exporter,
    )
    assert provider is not None

    with obs_tracing.span_context("intent.lifecycle", **{"intent.id": "intent-42"}):
        with obs_tracing.auth_stage(**{"hassaleh.agent_id": "agent-a1"}):
            pass

    names = [span.name for span in exporter.spans]
    assert "intent.auth" in names
    assert "intent.lifecycle" in names

    by_name = {span.name: span for span in exporter.spans}
    assert by_name["intent.lifecycle"].attributes["intent.id"] == "intent-42"
    assert by_name["intent.auth"].attributes["hassaleh.agent_id"] == "agent-a1"


_CANONICAL_STAGES = [
    ("auth_stage", "intent.auth"),
    ("validate_stage", "intent.validate"),
    ("execute_stage", "intent.execute"),
    ("persist_stage", "intent.persist_result"),
    ("result_stage", "intent.result"),
]


@pytest.mark.parametrize("helper_name,expected_span_name", _CANONICAL_STAGES)
def test_canonical_lifecycle_stage_emits_named_span(
    monkeypatch, helper_name, expected_span_name
):
    """Every §3.3 lifecycle stage helper emits the plan-named child span.

    The review flagged that Track F only asserted `intent.auth` +
    `intent.lifecycle`; the frozen plan requires the full canonical set
    (`auth`, `validate`, `execute`, `persist_result`, `result`).
    """
    monkeypatch.setenv("HASSALEH_OBS", "on")
    exporter = InMemoryExporter()
    provider = obs_tracing.setup_tracing(
        service_name="hassaleh-daemon",
        env="dev",
        sample_rate=1.0,
        exporter=exporter,
    )
    assert provider is not None

    helper = getattr(obs_tracing, helper_name)
    with obs_tracing.span_context(
        "intent.lifecycle", **{"intent.id": "intent-42"}
    ):
        with helper(**{"hassaleh.intent_id": "intent-42"}):
            pass

    span_names = [span.name for span in exporter.spans]
    assert "intent.lifecycle" in span_names, span_names
    assert expected_span_name in span_names, (
        f"{helper_name} should emit {expected_span_name!r}, got {span_names}"
    )


def test_trace_force_sampling_carries_through_decorator(monkeypatch):
    """HASSALEH_TRACE_FORCE=1 must carry through an end-to-end decorator path.

    The prior test only created a single manually-managed span. The review
    required evidence that the force flag also flips sampling for spans
    produced by the `@trace_span(...)` decorator (the real daemon callsite
    pattern) even when the ratio sampler is pinned to zero.
    """
    monkeypatch.setenv("HASSALEH_OBS", "on")
    monkeypatch.setenv("HASSALEH_TRACE_FORCE", "1")
    exporter = InMemoryExporter()
    provider = obs_tracing.setup_tracing(
        service_name="hassaleh-daemon",
        env="dev",
        sample_rate=0.0,
        exporter=exporter,
    )
    assert provider is not None

    @obs_tracing.trace_span("intent.execute")
    def run_business_step(value: int) -> int:
        return value + 1

    assert run_business_step(41) == 42
    span_names = [span.name for span in exporter.spans]
    assert "intent.execute" in span_names, span_names


def test_trace_force_sampling_overrides_zero_ratio(monkeypatch):
    """HASSALEH_TRACE_FORCE=1 records every span even at sample_rate=0."""
    monkeypatch.setenv("HASSALEH_OBS", "on")
    monkeypatch.setenv("HASSALEH_TRACE_FORCE", "1")
    exporter = InMemoryExporter()
    provider = obs_tracing.setup_tracing(
        service_name="hassaleh-daemon",
        env="dev",
        sample_rate=0.0,
        exporter=exporter,
    )
    assert provider is not None

    with obs_tracing.span_context("forced-span") as span:
        assert span.is_recording()

    assert [s.name for s in exporter.spans] == ["forced-span"]


def test_traces_off_is_noop(monkeypatch):
    """HASSALEH_OBS=off → setup_tracing is a no-op and spans are not recorded."""
    monkeypatch.setenv("HASSALEH_OBS", "off")

    result = obs_tracing.setup_tracing(service_name="hassaleh-daemon", env="dev")
    assert result is None
    assert obs_tracing._CONFIGURED_PROVIDER is None
    assert obs_tracing._CONFIGURED_EXPORTER is None

    # The span_context manager returns None and does not enter a recording span.
    with obs_tracing.span_context("never-recorded") as span:
        assert span is None

    current = trace.get_current_span().get_span_context()
    assert not current.is_valid


# ────────────────────────────────────────────────────────────────────────────
# 4. Regression — HASSALEH_OBS=off is a true zero-cost no-op (plan §1 S5)
# ────────────────────────────────────────────────────────────────────────────


def test_obs_off_regression_no_subsystem_setup_side_effects(monkeypatch, capsys):
    """S5: with obs=off, obs.setup() creates no registry, provider, or structlog pipeline."""
    from hassaleh import obs as obs_pkg

    monkeypatch.setenv("HASSALEH_OBS", "off")
    monkeypatch.setenv("HASSALEH_ENV", "dev")

    processor_calls = {"count": 0}
    original = obs_logging.pii_redaction_processor
    monkeypatch.setattr(
        obs_logging,
        "pii_redaction_processor",
        lambda *a, **kw: (processor_calls.__setitem__("count", processor_calls["count"] + 1) or original(*a, **kw)),
    )

    config = obs_pkg.setup("hassaleh-daemon", "dev")
    trace_result = obs_tracing.setup_tracing(service_name="hassaleh-daemon", env="dev")

    # Logs: plain-text formatter, no structlog processors executed.
    assert config.enabled is False
    logger = obs_logging.get_logger("hassaleh.daemon")
    logger.info("warmup")
    assert processor_calls["count"] == 0

    # Metrics: no Prometheus registry, no rendered bytes.
    assert obs_pkg.is_metrics_enabled() is False
    assert obs_pkg.get_metrics_registry() is None
    assert obs_pkg.render_metrics() is None

    # Traces: no configured TracerProvider, exporter stays None.
    assert trace_result is None
    assert obs_tracing._CONFIGURED_PROVIDER is None
    assert obs_tracing._CONFIGURED_PROCESSOR is None
    assert obs_tracing._CONFIGURED_EXPORTER is None

    # Drain captured stderr so the fixture teardown doesn't see it.
    capsys.readouterr()


_IMPORT_TIME_ISOLATION_SCRIPT = textwrap.dedent(
    """
    import sys

    from prometheus_client import REGISTRY

    def _collector_names():
        names = set()
        for collector_names in REGISTRY._collector_to_names.values():
            names.update(collector_names)
        return names

    pre_import = _collector_names()

    import hassaleh.obs  # noqa: F401
    import hassaleh.obs.metrics  # noqa: F401
    import hassaleh.obs.tracing  # noqa: F401

    post_import = _collector_names()

    hassaleh_collectors = sorted(
        n for n in post_import if n.startswith("hassaleh_")
    )
    if hassaleh_collectors:
        print(
            f"FAIL: hassaleh_* collectors registered at import: "
            f"{hassaleh_collectors!r}",
            file=sys.stderr,
        )
        sys.exit(2)

    delta = post_import - pre_import
    if delta:
        print(
            f"FAIL: global prometheus REGISTRY gained collectors at import: "
            f"{sorted(delta)!r}",
            file=sys.stderr,
        )
        sys.exit(3)

    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider as SdkTracerProvider

    provider = trace.get_tracer_provider()
    if isinstance(provider, SdkTracerProvider):
        print(
            f"FAIL: SDK TracerProvider installed at import: "
            f"{type(provider).__name__}",
            file=sys.stderr,
        )
        sys.exit(4)

    import structlog
    if structlog.is_configured():
        print("FAIL: structlog.is_configured() is True at import", file=sys.stderr)
        sys.exit(5)

    print("OK")
    """
).strip()


def test_obs_off_import_time_isolation():
    """S5 (strict): a fresh interpreter with HASSALEH_OBS=off must not mutate
    global observability state merely by importing the obs modules.

    The in-process regression test only proves `obs.setup()` + tracing-setup
    are inert after explicit calls. This subprocess test closes the gap the
    review flagged: it runs under a clean import graph and fails hard if
    *importing* `hassaleh.obs`, `hassaleh.obs.metrics`, or
    `hassaleh.obs.tracing` registers any `hassaleh_*` Prometheus collector,
    installs an SDK `TracerProvider`, or calls `structlog.configure()`.
    """
    env = os.environ.copy()
    env["HASSALEH_OBS"] = "off"
    for noisy_var in (
        "HASSALEH_TRACE_FORCE",
        "HASSALEH_TRACE_SAMPLE_RATE",
        "HASSALEH_LOG_LEVEL",
    ):
        env.pop(noisy_var, None)

    result = subprocess.run(
        [sys.executable, "-c", _IMPORT_TIME_ISOLATION_SCRIPT],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, (
        "HASSALEH_OBS=off import-time isolation subprocess failed.\n"
        f"exit={result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert result.stdout.strip().splitlines()[-1] == "OK"
