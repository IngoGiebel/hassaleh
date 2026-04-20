"""Hassaleh observability package entrypoints.

Track C (tracing) must be importable before Track A/B are fully merged, so
logging imports are optional and tracing symbols are always exported.
"""

from __future__ import annotations

from hassaleh.obs.tracing import (
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

__all__ = [
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

try:  # Track A extends the package surface when obs/logging.py is present.
    from hassaleh.obs.logging import bind_logger, get_logger, setup  # type: ignore
except Exception:  # pragma: no cover - Track C stays importable without Track A.
    pass
else:
    __all__.extend(["bind_logger", "get_logger", "setup"])
