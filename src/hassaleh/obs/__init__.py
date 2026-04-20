"""Hassaleh observability package.

Track A (logging) is the root-handler owner — `obs.setup()` configures
structlog + stdlib logging before any other track runs (plan §2.4).

Track B (metrics) extends that setup with an opt-in Prometheus registry.
Track C (tracing) remains optional so the package stays importable when the
OTEL extras are not installed.
"""

from __future__ import annotations

from hassaleh.obs.context import (
    bind_request_context,
    clear_request_context,
    current_agent_id,
    current_intent_id,
    get_request_context,
)
from hassaleh.obs.logging import (
    ObsLoggingConfig,
    bind_logger,
    get_logger,
    rebind_daemon_logger,
    setup as setup_logging,
)

METRICS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def setup(service_name: str, env: str | None = None) -> ObsLoggingConfig:
    config = setup_logging(service_name, env)
    setup_metrics(service_name=service_name, env=config.env)
    return config


__all__ = [
    "ObsLoggingConfig",
    "METRICS_CONTENT_TYPE",
    "bind_logger",
    "bind_request_context",
    "clear_request_context",
    "current_agent_id",
    "current_intent_id",
    "get_logger",
    "get_request_context",
    "rebind_daemon_logger",
    "setup",
]

try:
    from hassaleh.obs.metrics import (
        classify_exception,
        get_metrics_registry,
        is_metrics_enabled,
        observe_auth_bcrypt_duration,
        observe_cypher_query,
        observe_intent_duration,
        record_auth_attempt,
        record_error,
        record_heartbeat_missed,
        record_heartbeat_received,
        record_intent_submitted,
        record_tool_invocation,
        render_metrics,
        set_active_agents,
        set_active_intents,
        setup_metrics,
        shutdown_metrics,
    )
except ModuleNotFoundError as exc:  # pragma: no cover - dependency may be absent pre-install.
    if exc.name != "prometheus_client":
        raise

    def setup_metrics(*args, **kwargs):  # type: ignore[no-redef]
        return None

    def shutdown_metrics() -> None:  # type: ignore[no-redef]
        return None

    def get_metrics_registry():  # type: ignore[no-redef]
        return None

    def is_metrics_enabled() -> bool:  # type: ignore[no-redef]
        return False

    def render_metrics():  # type: ignore[no-redef]
        return None

    def classify_exception(exc):  # type: ignore[no-redef]
        del exc
        return "internal"

    def record_auth_attempt(*args, **kwargs) -> None:  # type: ignore[no-redef]
        return None

    def observe_auth_bcrypt_duration(*args, **kwargs) -> None:  # type: ignore[no-redef]
        return None

    def record_intent_submitted(*args, **kwargs) -> None:  # type: ignore[no-redef]
        return None

    def observe_intent_duration(*args, **kwargs) -> None:  # type: ignore[no-redef]
        return None

    def record_heartbeat_received(*args, **kwargs) -> None:  # type: ignore[no-redef]
        return None

    def record_heartbeat_missed(*args, **kwargs) -> None:  # type: ignore[no-redef]
        return None

    def observe_cypher_query(*args, **kwargs) -> None:  # type: ignore[no-redef]
        return None

    def record_tool_invocation(*args, **kwargs) -> None:  # type: ignore[no-redef]
        return None

    def record_error(*args, **kwargs) -> None:  # type: ignore[no-redef]
        return None

    def set_active_agents(*args, **kwargs) -> None:  # type: ignore[no-redef]
        return None

    def set_active_intents(*args, **kwargs) -> None:  # type: ignore[no-redef]
        return None
else:
    __all__.extend(
        [
            "classify_exception",
            "get_metrics_registry",
            "is_metrics_enabled",
            "observe_auth_bcrypt_duration",
            "observe_cypher_query",
            "observe_intent_duration",
            "record_auth_attempt",
            "record_error",
            "record_heartbeat_missed",
            "record_heartbeat_received",
            "record_intent_submitted",
            "record_tool_invocation",
            "render_metrics",
            "set_active_agents",
            "set_active_intents",
            "setup_metrics",
            "shutdown_metrics",
        ]
    )

try:  # Track C extends the surface once obs/tracing.py lands.
    from hassaleh.obs.tracing import (  # type: ignore
        TRACEPARENT_KEY,
        auth_stage,
        execute_stage,
        extract_traceparent,
        get_current_traceparent,
        hint_error_prone,
        is_obs_enabled,
        persist_stage,
        result_stage,
        setup_tracing,
        shutdown_tracing,
        stage_span,
        start_span_from_traceparent,
        trace_span,
        validate_stage,
    )
except Exception:  # pragma: no cover - package stays importable without Track C deps.
    pass
else:
    __all__.extend(
        [
            "TRACEPARENT_KEY",
            "auth_stage",
            "execute_stage",
            "extract_traceparent",
            "get_current_traceparent",
            "hint_error_prone",
            "is_obs_enabled",
            "persist_stage",
            "result_stage",
            "setup_tracing",
            "shutdown_tracing",
            "stage_span",
            "start_span_from_traceparent",
            "trace_span",
            "validate_stage",
        ]
    )
