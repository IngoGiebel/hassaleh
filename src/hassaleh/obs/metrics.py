from __future__ import annotations

import logging
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from neo4j.exceptions import Neo4jError, ServiceUnavailable, TransientError
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from aiohttp import web

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
from hassaleh.obs.context import current_agent_id
from hassaleh.obs.cypher_patterns import CYPHER_PATTERNS

DEFAULT_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
UNKNOWN_BUCKET = "__other__"
UNKNOWN_AGENT_ID = "__unknown__"
SLOW_QUERY_SECONDS = 0.100
METRIC_NAMES = (
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
)
_TOOL_COMMANDS = frozenset(
    {
        "/usr/bin/env",
        "/usr/bin/ls",
        "/usr/bin/printf",
        "assign_task",
        "hassaleh.graph_query_inspector",
        "hassaleh.metrics_daily_summary",
        "hassaleh.openclaw_alert_dispatch",
        "hassaleh.rule_author",
        "hassaleh.workflow_cron_maintenance",
        "invoke_command",
        "review_task",
        "update_property",
        UNKNOWN_BUCKET,
    }
)
_CYPHER_PATTERN_SET = frozenset(CYPHER_PATTERNS)
_LOCK = threading.Lock()
_STATE: "MetricsState | None" = None
_LOG = logging.getLogger("hassaleh.obs.metrics")


@dataclass
class MetricsState:
    registry: CollectorRegistry
    intent_submitted_total: Counter
    intent_duration_seconds: Histogram
    auth_attempts_total: Counter
    auth_bcrypt_duration_seconds: Histogram
    heartbeat_received_total: Counter
    heartbeat_missed_total: Counter
    heartbeat_interval_seconds: Histogram
    cypher_query_duration_seconds: Histogram
    cypher_query_slow_total: Counter
    tool_invocation_total: Counter
    tool_invocation_duration_seconds: Histogram
    errors_total: Counter
    active_agents: Gauge
    active_intents: Gauge
    version_info: Gauge
    warned_patterns: set[str] = field(default_factory=set)
    warned_commands: set[str] = field(default_factory=set)
    active_intent_states: set[str] = field(default_factory=set)


def _package_version() -> str:
    pyproject = Path(__file__).resolve().parents[3] / "pyproject.toml"
    if not pyproject.exists():
        return "0.0.0"
    text = pyproject.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else "0.0.0"


def _env_flag(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip().lower()


def is_metrics_enabled() -> bool:
    return _env_flag("HASSALEH_OBS", "off") != "off"


def _new_state(*, env: str) -> MetricsState:
    registry = CollectorRegistry(auto_describe=True)
    state = MetricsState(
        registry=registry,
        intent_submitted_total=Counter(
            "hassaleh_intent_submitted_total",
            "Intent submissions",
            labelnames=("agent_id", "result"),
            registry=registry,
        ),
        intent_duration_seconds=Histogram(
            "hassaleh_intent_duration_seconds",
            "Intent duration by lifecycle stage",
            labelnames=("stage",),
            buckets=DEFAULT_BUCKETS,
            registry=registry,
        ),
        auth_attempts_total=Counter(
            "hassaleh_auth_attempts_total",
            "Authentication attempts",
            labelnames=("result",),
            registry=registry,
        ),
        auth_bcrypt_duration_seconds=Histogram(
            "hassaleh_auth_bcrypt_duration_seconds",
            "Bcrypt verification duration",
            buckets=DEFAULT_BUCKETS,
            registry=registry,
        ),
        heartbeat_received_total=Counter(
            "hassaleh_heartbeat_received_total",
            "Accepted heartbeat writes",
            labelnames=("agent_id",),
            registry=registry,
        ),
        heartbeat_missed_total=Counter(
            "hassaleh_heartbeat_missed_total",
            "Heartbeat gaps detected during lifecycle sweep",
            labelnames=("agent_id",),
            registry=registry,
        ),
        heartbeat_interval_seconds=Histogram(
            "hassaleh_heartbeat_interval_seconds",
            "Observed interval between accepted heartbeats",
            labelnames=("agent_id",),
            buckets=DEFAULT_BUCKETS,
            registry=registry,
        ),
        cypher_query_duration_seconds=Histogram(
            "hassaleh_cypher_query_duration_seconds",
            "Cypher query duration by classified pattern",
            labelnames=("pattern",),
            buckets=DEFAULT_BUCKETS,
            registry=registry,
        ),
        cypher_query_slow_total=Counter(
            "hassaleh_cypher_query_slow_total",
            "Slow Cypher queries over 100ms",
            labelnames=("pattern",),
            registry=registry,
        ),
        tool_invocation_total=Counter(
            "hassaleh_tool_invocation_total",
            "Tool invocation outcomes",
            labelnames=("agent_id", "command", "result"),
            registry=registry,
        ),
        tool_invocation_duration_seconds=Histogram(
            "hassaleh_tool_invocation_duration_seconds",
            "Tool invocation duration",
            labelnames=("command",),
            buckets=DEFAULT_BUCKETS,
            registry=registry,
        ),
        errors_total=Counter(
            "hassaleh_errors_total",
            "Classified errors",
            labelnames=("type", "source"),
            registry=registry,
        ),
        active_agents=Gauge(
            "hassaleh_active_agents",
            "Cached live agent count",
            registry=registry,
        ),
        active_intents=Gauge(
            "hassaleh_active_intents",
            "Cached live intent count by state",
            labelnames=("state",),
            registry=registry,
        ),
        version_info=Gauge(
            "hassaleh_version_info",
            "Build and environment information",
            labelnames=("version", "env"),
            registry=registry,
        ),
    )
    state.active_agents.set(0)
    state.version_info.labels(version=_package_version(), env=env).set(1)
    return state


def setup_metrics(service_name: str | None = None, env: str | None = None) -> MetricsState | None:
    del service_name  # Reserved for future per-service differentiation.

    global _STATE
    if not is_metrics_enabled():
        return None

    with _LOCK:
        if _STATE is not None:
            return _STATE
        _STATE = _new_state(env=(env or os.getenv("HASSALEH_ENV") or "dev").lower())
        return _STATE


def shutdown_metrics() -> None:
    global _STATE
    with _LOCK:
        _STATE = None


def get_metrics_registry() -> CollectorRegistry | None:
    state = _STATE
    if state is not None:
        return state.registry
    return None


def _ensure_state() -> MetricsState | None:
    state = _STATE
    if state is not None:
        return state
    return setup_metrics()


def render_metrics() -> bytes | None:
    state = _ensure_state()
    if state is None:
        return None
    return generate_latest(state.registry)


def _warn_unknown_label(kind: str, value: str, warned_values: set[str]) -> None:
    if value in warned_values:
        return
    warned_values.add(value)
    _LOG.warning("Unknown %s label %r bucketed into %s", kind, value, UNKNOWN_BUCKET)


def _normalize_pattern(pattern: str | None, state: MetricsState) -> str:
    candidate = (pattern or "").strip()
    if candidate in _CYPHER_PATTERN_SET:
        return candidate
    if candidate:
        _warn_unknown_label("pattern", candidate, state.warned_patterns)
    return UNKNOWN_BUCKET


def _normalize_command(command: str | None, state: MetricsState) -> str:
    candidate = (command or "").strip()
    if candidate in _TOOL_COMMANDS:
        return candidate
    if candidate:
        _warn_unknown_label("command", candidate, state.warned_commands)
    return UNKNOWN_BUCKET


def _label_agent_id(agent_id: str | None) -> str:
    return agent_id or current_agent_id(UNKNOWN_AGENT_ID) or UNKNOWN_AGENT_ID


def classify_exception(exc: BaseException) -> str:
    if isinstance(
        exc,
        (
            AuthenticationError,
            AgentNotFoundError,
            AgentDisabledError,
            HeartbeatTokenMismatchError,
        ),
    ):
        return "auth"
    if isinstance(
        exc,
        (
            CapabilityNotFoundError,
            CapabilityDeniedError,
            CapabilityParamError,
        ),
    ):
        return "capability"
    if isinstance(exc, (AccessDeniedError, PermissionError)):
        return "permission"
    if isinstance(exc, (ValueError, TypeError)):
        return "validation"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, (Neo4jError, ServiceUnavailable, TransientError)):
        return "graph"
    return "internal"


def record_error(exc: BaseException, *, source: str) -> None:
    state = _ensure_state()
    if state is None:
        return
    state.errors_total.labels(type=classify_exception(exc), source=source).inc()


def record_auth_attempt(result: str) -> None:
    state = _ensure_state()
    if state is None:
        return
    state.auth_attempts_total.labels(result=result).inc()


def observe_auth_bcrypt_duration(duration_seconds: float) -> None:
    state = _ensure_state()
    if state is None:
        return
    state.auth_bcrypt_duration_seconds.observe(duration_seconds)


def record_intent_submitted(*, agent_id: str | None = None, result: str = "accepted") -> None:
    state = _ensure_state()
    if state is None:
        return
    state.intent_submitted_total.labels(agent_id=_label_agent_id(agent_id), result=result).inc()


def observe_intent_duration(stage: str, duration_seconds: float) -> None:
    state = _ensure_state()
    if state is None:
        return
    state.intent_duration_seconds.labels(stage=stage).observe(duration_seconds)


def record_heartbeat_received(*, agent_id: str | None = None, interval_seconds: float | None = None) -> None:
    state = _ensure_state()
    if state is None:
        return
    label_agent = _label_agent_id(agent_id)
    state.heartbeat_received_total.labels(agent_id=label_agent).inc()
    if interval_seconds is not None and interval_seconds >= 0:
        state.heartbeat_interval_seconds.labels(agent_id=label_agent).observe(interval_seconds)


def record_heartbeat_missed(agent_id: str) -> None:
    state = _ensure_state()
    if state is None:
        return
    state.heartbeat_missed_total.labels(agent_id=agent_id).inc()


def observe_cypher_query(duration_seconds: float, *, pattern: str | None) -> None:
    state = _ensure_state()
    if state is None:
        return
    normalized = _normalize_pattern(pattern, state)
    state.cypher_query_duration_seconds.labels(pattern=normalized).observe(duration_seconds)
    if duration_seconds > SLOW_QUERY_SECONDS:
        state.cypher_query_slow_total.labels(pattern=normalized).inc()


def record_tool_invocation(
    *,
    command: str | None,
    result: str,
    duration_seconds: float | None = None,
    agent_id: str | None = None,
) -> None:
    state = _ensure_state()
    if state is None:
        return
    normalized = _normalize_command(command, state)
    label_agent = _label_agent_id(agent_id)
    state.tool_invocation_total.labels(
        agent_id=label_agent,
        command=normalized,
        result=result,
    ).inc()
    if duration_seconds is not None and duration_seconds >= 0:
        state.tool_invocation_duration_seconds.labels(command=normalized).observe(duration_seconds)


def set_active_agents(count: int) -> None:
    state = _ensure_state()
    if state is None:
        return
    state.active_agents.set(count)


def set_active_intents(counts: Mapping[str, int]) -> None:
    state = _ensure_state()
    if state is None:
        return

    next_states = {str(key) for key in counts}
    all_states = state.active_intent_states | next_states
    for lifecycle in all_states:
        state.active_intents.labels(state=lifecycle).set(int(counts.get(lifecycle, 0)))
    state.active_intent_states = all_states


def _reset_for_tests() -> None:
    shutdown_metrics()
