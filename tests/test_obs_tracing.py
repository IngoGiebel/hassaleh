from __future__ import annotations

import asyncio

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from hassaleh.obs import tracing


class InMemoryExporter(SpanExporter):
    def __init__(self) -> None:
        self.spans = []

    def export(self, spans):
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self):
        return None


@pytest.fixture(autouse=True)
def clean_tracing(monkeypatch):
    monkeypatch.delenv("HASSALEH_TRACE_FORCE", raising=False)
    monkeypatch.delenv("HASSALEH_TRACE_SAMPLE_RATE", raising=False)
    monkeypatch.setenv("HASSALEH_OBS", "on")
    tracing.shutdown_tracing()
    yield
    tracing.shutdown_tracing()


def _install_test_provider(monkeypatch, *, sample_rate=1.0, force=False):
    exporter = InMemoryExporter()
    if force:
        monkeypatch.setenv("HASSALEH_TRACE_FORCE", "1")
    provider = tracing.setup_tracing(
        service_name="test-service",
        env="test",
        sample_rate=sample_rate,
        exporter=exporter,
    )
    assert provider is not None
    return exporter


def test_sampling_behaviour_respects_rate_and_force_override(monkeypatch):
    off_exporter = _install_test_provider(monkeypatch, sample_rate=0.0)
    with tracing.span_context("sampled-off") as span:
        assert not span.is_recording()
    assert off_exporter.spans == []

    tracing.shutdown_tracing()

    forced_exporter = _install_test_provider(monkeypatch, sample_rate=0.0, force=True)
    with tracing.span_context("force-on") as span:
        assert span.is_recording()
    assert [s.name for s in forced_exporter.spans] == ["force-on"]


def test_trace_span_decorator_wraps_async_function(monkeypatch):
    exporter = _install_test_provider(monkeypatch, sample_rate=1.0)

    @tracing.trace_span("decorated.work")
    async def do_work(x, y):
        current = trace.get_current_span().get_span_context()
        assert current.is_valid
        return x + y

    result = asyncio.run(do_work(2, 3))
    assert result == 5
    assert [s.name for s in exporter.spans] == ["decorated.work"]


def test_traceparent_round_trip_and_stage_helpers(monkeypatch):
    exporter = _install_test_provider(monkeypatch, sample_rate=1.0)

    with tracing.span_context("sdk.submit"):
        traceparent = tracing.get_current_traceparent()
        assert traceparent is not None

        with tracing.start_span_from_traceparent("intent.lifecycle", traceparent):
            with tracing.auth_stage(agent_id="agent-a"):
                pass
            with tracing.validate_stage(intent_id="intent-1"):
                pass

    names = [span.name for span in exporter.spans]
    assert names == ["intent.auth", "intent.validate", "intent.lifecycle", "sdk.submit"]

    by_name = {span.name: span for span in exporter.spans}
    sdk_span = by_name["sdk.submit"]
    intent_span = by_name["intent.lifecycle"]
    assert intent_span.context.trace_id == sdk_span.context.trace_id
    assert intent_span.parent.span_id == sdk_span.context.span_id
    assert by_name["intent.auth"].parent.span_id == intent_span.context.span_id
    assert by_name["intent.validate"].parent.span_id == intent_span.context.span_id


def test_hint_error_prone_forces_sampling_even_when_ratio_is_zero(monkeypatch):
    exporter = _install_test_provider(monkeypatch, sample_rate=0.0)

    with tracing.hint_error_prone():
        with tracing.span_context("error-prone") as span:
            assert span.is_recording()

    assert [s.name for s in exporter.spans] == ["error-prone"]
