"""Sprint-13 runtime dispatch core: skeleton + dispatch loop.

See docs/sprint-13-plan.md §2.3, §2.4, §2.5.

Canonical execution order (§2.5):

    validate → capability_check → precondition → mutate

Tracks filling the gaps downstream:
  * Track B — Cypher handlers (precondition + mutate bodies)
  * Track C — capability scope catalogue + schema index
  * Track D — observability (spans, metrics, structured logs)

Track A (this module) owns only the skeleton and the rules governing
*when* a handler runs, *when* a tx opens, and *when* it commits vs.
rolls back.
"""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Protocol, Tuple

from . import observability as obs
from .capabilities import check_scope
from .types import Ctx, Intent, Result


class Tx(Protocol):
    """Protocol for a Neo4j-style transaction."""

    def run(self, query: str, parameters: Dict[str, Any] | None = None, **kwargs: Any) -> Any: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


class Session(Protocol):
    """Protocol for a Neo4j-style session."""

    def begin_transaction(self) -> Tx: ...
    def __enter__(self) -> Session: ...
    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None: ...


Handler = Callable[[Intent, Tx, Ctx], Result]
SessionFactory = Callable[[], Session]

_REGISTRY: Dict[str, Tuple[Handler, str]] = {}


def register_handler(
    intent_type: str, *, requires_capability: str
) -> Callable[[Handler], Handler]:
    """Register an Intent handler under `intent_type` with its required
    capability. The capability is declared at registration (§2.3) — the
    Intent itself carries none (I-CR-3)."""

    if not requires_capability.strip():
        raise ValueError("requires_capability must be a non-empty string")

    def _register(handler: Handler) -> Handler:
        if intent_type in _REGISTRY:
            raise ValueError(
                f"handler for intent type {intent_type!r} already registered"
            )
        _REGISTRY[intent_type] = (handler, requires_capability)
        return handler

    return _register


def _reset_registry_for_tests() -> None:
    """Internal helper to clear the module-level registry between tests."""
    _REGISTRY.clear()


class HassalehRuntime:
    """Sprint-13 Intent dispatch runtime.

    Constructor takes a `session_factory` — a zero-arg callable returning
    a session (supporting the context manager protocol) whose `__enter__`
    yields the session itself, which provides `begin_transaction()`.
    The runtime opens a transaction only AFTER validate+capability_check
    succeed (plan §2.4).
    """

    def __init__(
        self,
        session_factory: SessionFactory,
        registry: Dict[str, Tuple[Handler, str]] | None = None,
    ) -> None:
        obs.initialize_runtime_metrics()
        self._session_factory = session_factory
        self._registry = registry if registry is not None else _REGISTRY

    def execute(self, intent: Intent, ctx: Ctx) -> Result:
        start_time = time.perf_counter()
        with obs.start_root_span(intent, ctx):
            # 1. validate — unknown type short-circuits before any cap lookup.
            with obs.validate_span():
                entry = self._registry.get(intent.type)
                if entry is None:
                    result = Result.validation_error(
                        error_code="unknown-intent-type",
                        error_message=f"unknown intent type: {intent.type}",
                    )
                    duration_ms = (time.perf_counter() - start_time) * 1000
                    obs.record_metric(intent, result, duration_ms)
                    obs.emit_log(intent, ctx, result, duration_ms)
                    return result
                handler, required_capability = entry

            # 2. capability_check — exact match against ApiKey.scopes (§2.5).
            with obs.capability_check_span():
                if not check_scope(ctx.principal, required_capability):
                    result = Result.capability_denied(
                        error_code="scope-not-granted",
                        error_message=f"missing required capability: {required_capability}",
                    )
                    duration_ms = (time.perf_counter() - start_time) * 1000
                    obs.record_metric(intent, result, duration_ms)
                    obs.emit_log(intent, ctx, result, duration_ms)
                    return result

            # Runtime-bound values (§2.4): callers supply identity, but the
            # runtime owns the handler-visible UTC timestamp.
            runtime_ctx = replace(ctx, now=datetime.now(timezone.utc))

            # 3+4. open tx, run handler (precondition+mutate inside ONE tx).
            try:
                with self._session_factory() as session:
                    tx = session.begin_transaction()
                    try:
                        result = handler(intent, tx, runtime_ctx)
                    except Exception:
                        try:
                            tx.rollback()
                        except Exception:
                            pass
                        result = Result.internal_error(
                            error_code="handler-exception",
                            error_message="internal handler error",
                        )
                        duration_ms = (time.perf_counter() - start_time) * 1000
                        obs.record_metric(intent, result, duration_ms)
                        obs.emit_log(intent, ctx, result, duration_ms)
                        return result

                    # Commit IFF handler returned ok; any other kind rolls back.
                    if result.kind == "ok":
                        try:
                            tx.commit()
                        except Exception:
                            result = Result.internal_error(
                                error_code="commit-failed",
                                error_message="failed to commit transaction",
                            )
                    else:
                        try:
                            tx.rollback()
                        except Exception:
                            result = Result.internal_error(
                                error_code="rollback-failed",
                                error_message="failed to rollback transaction",
                            )
                    
                    duration_ms = (time.perf_counter() - start_time) * 1000
                    obs.record_metric(intent, result, duration_ms)
                    obs.emit_log(intent, ctx, result, duration_ms)
                    return result
            except Exception:
                result = Result.internal_error(
                    error_code="session-open-failed",
                    error_message="failed to open session",
                )
                duration_ms = (time.perf_counter() - start_time) * 1000
                obs.record_metric(intent, result, duration_ms)
                obs.emit_log(intent, ctx, result, duration_ms)
                return result


__all__ = [
    "HassalehRuntime",
    "Handler",
    "SessionFactory",
    "register_handler",
    "Ctx",
    "Intent",
    "Principal",
    "Result",
    "Tx",
    "Session",
]

