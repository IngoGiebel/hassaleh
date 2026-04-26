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

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Callable, ContextManager, Dict, Tuple

from .types import Ctx, Intent, Principal, Result

Handler = Callable[[Intent, Any, Ctx], Result]
SessionFactory = Callable[[], ContextManager[Any]]

_REGISTRY: Dict[str, Tuple[Handler, str]] = {}


def register_handler(
    intent_type: str, *, requires_capability: str
) -> Callable[[Handler], Handler]:
    """Register an Intent handler under `intent_type` with its required
    capability. The capability is declared at registration (§2.3) — the
    Intent itself carries none (I-CR-3)."""

    def _register(handler: Handler) -> Handler:
        if intent_type in _REGISTRY:
            raise ValueError(
                f"handler for intent type {intent_type!r} already registered"
            )
        _REGISTRY[intent_type] = (handler, requires_capability)
        return handler

    return _register


class HassalehRuntime:
    """Sprint-13 Intent dispatch runtime.

    Constructor takes a `session_factory` — a zero-arg callable returning
    a context manager whose `__enter__` yields a Neo4j-style session with
    `begin_transaction()`. The runtime opens a transaction only AFTER
    validate+capability_check succeed (plan §2.4), so malformed and
    unauthorized calls cost zero Cypher.
    """

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def execute(self, intent: Intent, ctx: Ctx) -> Result:
        # 1. validate — unknown type short-circuits before any cap lookup.
        entry = _REGISTRY.get(intent.type)
        if entry is None:
            return Result(
                kind="validation-error",
                data=None,
                error_code="unknown-intent-type",
                error_message=f"unknown intent type: {intent.type}",
            )
        handler, required_capability = entry

        # 2. capability_check — exact match against ApiKey.scopes (§2.5).
        if required_capability not in ctx.principal.scopes:
            return Result(
                kind="capability-denied",
                data=None,
                error_code="scope-not-granted",
                error_message=f"missing required capability: {required_capability}",
            )

        # Runtime-bound values (§2.4): callers supply identity, but the
        # runtime owns the handler-visible UTC timestamp.
        runtime_ctx = replace(ctx, now=datetime.now(timezone.utc))

        # 3+4. open tx, run handler (precondition+mutate inside ONE tx).
        try:
            with self._session_factory() as session:
                tx = session.begin_transaction()
                try:
                    result = handler(intent, tx, runtime_ctx)
                except Exception as exc:
                    try:
                        tx.rollback()
                    except Exception:
                        # Preserve the original handler exception mapping;
                        # Track D will get structured logging later.
                        pass
                    return Result(
                        kind="internal-error",
                        data=None,
                        error_code="handler-exception",
                        error_message=f"{type(exc).__name__}: {exc}",
                    )

                # Commit IFF handler returned ok; any other kind rolls back.
                if result.kind == "ok":
                    try:
                        tx.commit()
                    except Exception as exc:
                        return Result(
                            kind="internal-error",
                            data=None,
                            error_code="commit-failed",
                            error_message=f"{type(exc).__name__}: {exc}",
                        )
                else:
                    try:
                        tx.rollback()
                    except Exception as exc:
                        return Result(
                            kind="internal-error",
                            data=None,
                            error_code="rollback-failed",
                            error_message=f"{type(exc).__name__}: {exc}",
                        )
                return result
        except Exception as exc:
            return Result(
                kind="internal-error",
                data=None,
                error_code="session-open-failed",
                error_message=f"{type(exc).__name__}: {exc}",
            )


__all__ = [
    "HassalehRuntime",
    "Handler",
    "SessionFactory",
    "register_handler",
    "_REGISTRY",
    "Ctx",
    "Intent",
    "Principal",
    "Result",
]
