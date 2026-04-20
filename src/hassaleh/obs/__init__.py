"""Hassaleh observability package.

Track A (logging) is the root-handler owner — `obs.setup()` configures
structlog + stdlib logging before any other track runs (plan §2.4).

Track C (tracing) is imported optionally so Track A can merge first per
the plan's explicit merge order (A → B → C → D → E → F, plan §5).
"""

from __future__ import annotations

from hassaleh.obs.logging import (
    ObsLoggingConfig,
    bind_logger,
    get_logger,
    rebind_daemon_logger,
    setup,
)

__all__ = [
    "ObsLoggingConfig",
    "bind_logger",
    "get_logger",
    "rebind_daemon_logger",
    "setup",
]

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
except Exception:  # pragma: no cover - Track A stays importable without Track C.
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
