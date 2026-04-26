"""Runtime value types for Sprint-13 Intent dispatch.

See docs/sprint-13-plan.md §2.2. The shapes are fixed in v1:
  * Intent has NO `capability` field (I-CR-3): the runtime reads the
    required scope from the handler registry, not from the caller.
  * Intent has NO `idempotency_key` field (G-CR-5): underspecified in v0,
    deferred to v2.
  * Result.kind values are kebab-case verbatim (G-CR-4): used as-is as
    the Prometheus `result` label by Track D — no translation layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Mapping

ResultKind = Literal[
    "ok",
    "precondition-failed",
    "capability-denied",
    "validation-error",
    "internal-error",
]


@dataclass(frozen=True)
class Intent:
    type: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class Result:
    kind: ResultKind
    data: Mapping[str, Any] | None
    error_code: str | None
    error_message: str | None


@dataclass(frozen=True)
class Principal:
    """Authenticated caller. `scopes` is an exact-match tuple of capability
    strings granted on the ApiKey (plan §2.5)."""

    id: str
    scopes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Ctx:
    """Per-execute handler context. `now` is runtime-bound UTC time taken
    at transaction-open; handlers MUST NOT call `datetime.now()` directly
    (plan §2.4, "Runtime-bound values")."""

    principal: Principal
    now: datetime
