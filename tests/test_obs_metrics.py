from __future__ import annotations

from aiohttp.test_utils import make_mocked_request
import pytest

from hassaleh.errors import (
    AccessDeniedError,
    AgentDisabledError,
    AgentNotFoundError,
    AuthenticationError,
    CapabilityDeniedError,
    CapabilityNotFoundError,
    CapabilityParamError,
    HeartbeatTokenMismatchError,
)
from hassaleh.obs.context import bind_request_context
from hassaleh.obs.metrics import (
    MetricsState,
    classify_exception,
    _ensure_state,
    metrics_handler,
    observe_cypher_query,
    record_error,
    record_heartbeat_missed,
    record_heartbeat_received,
    record_intent_submitted,
    record_tool_invocation,
    set_active_agents,
    set_active_intents,
    setup_metrics,
    shutdown_metrics,
)


@pytest.fixture(autouse=True)
def reset_metrics():
    shutdown_metrics()
    yield
    shutdown_metrics()


async def test_metric_registration_and_metrics_endpoint(monkeypatch):
    monkeypatch.setenv("HASSALEH_OBS", "on")
    state = setup_metrics(env="dev")

    assert state is not None
    assert state.registry is not None

    set_active_agents(3)
    set_active_intents({"pending": 7})

    response = await metrics_handler(make_mocked_request("GET", "/metrics"))
    body = response.body.decode()

    expected = [
        "hassaleh_intent_submitted_total",
        "hassaleh_intent_duration_seconds",
        "hassaleh_auth_attempts_total",
        "hassaleh_auth_bcrypt_duration_seconds",
        "hassaleh_heartbeat_received_total",
        "hassaleh_heartbeat_missed_total",
        "hassaleh_heartbeat_interval_seconds",
        "hassaleh_cypher_query_duration_seconds",
        "hassaleh_cypher_query_slow_total",
        "hassaleh_tool_invocation_total",
        "hassaleh_tool_invocation_duration_seconds",
        "hassaleh_errors_total",
        "hassaleh_active_agents",
        "hassaleh_active_intents",
        "hassaleh_version_info",
    ]
    for metric_name in expected:
        assert metric_name in body


async def test_obs_off_is_noop(monkeypatch):
    monkeypatch.setenv("HASSALEH_OBS", "off")
    state = setup_metrics(env="dev")

    assert state is None
    assert _ensure_state() is None

    response = await metrics_handler(make_mocked_request("GET", "/metrics"))
    assert response.status == 404


def test_classify_exception_branches():
    assert classify_exception(AuthenticationError()) == "auth"
    assert classify_exception(AgentNotFoundError()) == "auth"
    assert classify_exception(AgentDisabledError()) == "auth"
    assert classify_exception(HeartbeatTokenMismatchError()) == "auth"
    assert classify_exception(CapabilityNotFoundError("a")) == "capability"
    assert classify_exception(CapabilityDeniedError("a")) == "capability"
    assert classify_exception(CapabilityParamError("a")) == "capability"
    assert classify_exception(AccessDeniedError()) == "permission"
    assert classify_exception(PermissionError()) == "permission"
    assert classify_exception(ValueError()) == "validation"
    assert classify_exception(TypeError()) == "validation"
    assert classify_exception(TimeoutError()) == "timeout"
    assert classify_exception(RuntimeError()) == "internal"


def test_cardinality_guard_unknown_pattern_and_command(monkeypatch, caplog):
    monkeypatch.setenv("HASSALEH_OBS", "on")
    setup_metrics(env="dev")

    with bind_request_context(agent_id="agent-x", intent_id="intent-y"):
        observe_cypher_query(0.25, pattern="totally.unknown.pattern")
        record_tool_invocation(command="surprise-command", result="ok", duration_seconds=0.12)

    # We don't return the guarded values from the functions directly, but we can check the logs
    assert "metrics label bucketed" in caplog.text.lower() or "bucketed into" in caplog.text.lower()
    assert "totally.unknown.pattern" in caplog.text
    assert "surprise-command" in caplog.text


async def test_heartbeat_and_intent_counters_increment(monkeypatch):
    monkeypatch.setenv("HASSALEH_OBS", "on")
    setup_metrics(env="dev")

    with bind_request_context(agent_id="agent-42"):
        record_intent_submitted(result="accepted")
        record_heartbeat_received()
        record_heartbeat_missed("agent-42")
        record_error(RuntimeError("boom"), source="intent")

    response = await metrics_handler(make_mocked_request("GET", "/metrics"))
    body = response.body.decode()

    assert 'hassaleh_intent_submitted_total_total{agent_id="agent-42",result="accepted"} 1.0' in body or 'hassaleh_intent_submitted_total{agent_id="agent-42",result="accepted"} 1.0' in body
    assert 'hassaleh_heartbeat_received_total_total{agent_id="agent-42"} 1.0' in body or 'hassaleh_heartbeat_received_total{agent_id="agent-42"} 1.0' in body
    assert 'hassaleh_heartbeat_missed_total_total{agent_id="agent-42"} 1.0' in body or 'hassaleh_heartbeat_missed_total{agent_id="agent-42"} 1.0' in body
    assert 'hassaleh_errors_total_total{source="intent",type="internal"} 1.0' in body or 'hassaleh_errors_total{source="intent",type="internal"} 1.0' in body
