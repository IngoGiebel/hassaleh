"""Tests for hassaleh.runtime core dispatch loop (Sprint 13, Track A).

Covers the §2.4/§2.5 canonical order (validate → capability_check →
precondition → mutate) and exception-to-Result mapping. No real Neo4j
needed: Track A is the skeleton; Track B will plug in Cypher handlers.

Scenarios (per dispatch message and plan §2.4):
  1. unknown-intent-type         → validation-error, no tx opened
  2. missing-capability          → capability-denied, no tx opened
  3. handler-exception           → internal-error, tx rolled back
  4. successful dispatch         → ok, tx committed
  5. canonical-order             → validate before cap_check; cap_check
                                    before tx open
  6. non-ok handler result       → tx rolled back, result returned as-is
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from hassaleh.runtime import (
    Ctx,
    HassalehRuntime,
    Intent,
    Principal,
    Result,
    register_handler,
)
from hassaleh.runtime.core import _REGISTRY


@pytest.fixture(autouse=True)
def _reset_registry():
    """Handlers are registered at module import; reset between tests."""
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()


@pytest.fixture
def session_factory():
    """MagicMock session factory. session_factory() returns a ctx manager
    whose __enter__ yields a session; session.begin_transaction() returns a
    tx with commit()/rollback()."""
    factory = MagicMock(name="session_factory")
    session = factory.return_value.__enter__.return_value
    session.begin_transaction.return_value = MagicMock(name="tx")
    return factory


def _tx(session_factory):
    return session_factory.return_value.__enter__.return_value.begin_transaction.return_value


def _ctx(scopes=("market.analyst.write",)) -> Ctx:
    return Ctx(
        principal=Principal(id="api-key-1", scopes=list(scopes)),
        now=datetime(2026, 4, 24, 12, 0, tzinfo=timezone.utc),
    )


# --- 1. unknown-intent-type ---------------------------------------------

def test_unknown_intent_type_returns_validation_error(session_factory):
    runtime = HassalehRuntime(session_factory=session_factory)
    result = runtime.execute(Intent(type="no.such.type", payload={}), _ctx())

    assert result.kind == "validation-error"
    assert result.error_code == "unknown-intent-type"
    assert "no.such.type" in (result.error_message or "")
    # No tx should be opened for an unknown intent (validate runs first).
    session_factory.assert_not_called()


# --- 2. missing-capability ----------------------------------------------

def test_missing_capability_returns_denied_and_does_not_open_tx(session_factory):
    @register_handler("market.add-analyst-attempt", requires_capability="market.analyst.write")
    def _h(intent, tx, ctx):  # pragma: no cover - should not be called
        raise AssertionError("handler must not run when capability is missing")

    runtime = HassalehRuntime(session_factory=session_factory)
    ctx = _ctx(scopes=["market.reviewer.write"])  # wrong scope
    result = runtime.execute(
        Intent(type="market.add-analyst-attempt", payload={}), ctx
    )

    assert result.kind == "capability-denied"
    assert result.error_code == "scope-not-granted"
    assert "market.analyst.write" in (result.error_message or "")
    # Canonical-order invariant: cap_check precedes tx open.
    session_factory.assert_not_called()


# --- 3. handler-exception -----------------------------------------------

def test_handler_exception_returns_internal_error_and_rolls_back(session_factory):
    @register_handler("market.add-analyst-attempt", requires_capability="market.analyst.write")
    def _h(intent, tx, ctx):
        raise RuntimeError("boom")

    runtime = HassalehRuntime(session_factory=session_factory)
    result = runtime.execute(
        Intent(type="market.add-analyst-attempt", payload={}), _ctx()
    )

    assert result.kind == "internal-error"
    assert result.error_code == "handler-exception"

    tx = _tx(session_factory)
    tx.rollback.assert_called_once()
    tx.commit.assert_not_called()


# --- 4. successful dispatch ---------------------------------------------

def test_successful_dispatch_commits_and_returns_ok(session_factory):
    @register_handler("market.add-analyst-attempt", requires_capability="market.analyst.write")
    def _h(intent, tx, ctx):
        return Result(kind="ok", data={"attempt_id": "a-1"}, error_code=None, error_message=None)

    runtime = HassalehRuntime(session_factory=session_factory)
    result = runtime.execute(
        Intent(type="market.add-analyst-attempt", payload={"segment": "us"}), _ctx()
    )

    assert result.kind == "ok"
    assert result.data == {"attempt_id": "a-1"}
    tx = _tx(session_factory)
    tx.commit.assert_called_once()
    tx.rollback.assert_not_called()


# --- 5. canonical order (validate before cap_check) ---------------------

def test_canonical_order_validate_runs_before_capability_check(session_factory):
    """Unknown type + missing scope → validation-error, not capability-denied.

    Proves validate runs before capability_check: we cannot even look up
    `requires_capability` for a type that is not registered.
    """
    runtime = HassalehRuntime(session_factory=session_factory)
    result = runtime.execute(
        Intent(type="no.such.type", payload={}),
        _ctx(scopes=[]),  # no scopes either — but validate should short-circuit
    )
    assert result.kind == "validation-error"


# --- 6. non-ok handler result (e.g. precondition-failed) ----------------

def test_non_ok_handler_result_rolls_back_and_returns_as_is(session_factory):
    @register_handler("market.set-verdict", requires_capability="market.reviewer.write")
    def _h(intent, tx, ctx):
        return Result(
            kind="precondition-failed",
            data=None,
            error_code="terminal-verdict-exists",
            error_message="cannot overwrite terminal verdict",
        )

    runtime = HassalehRuntime(session_factory=session_factory)
    ctx = _ctx(scopes=["market.reviewer.write"])
    result = runtime.execute(
        Intent(type="market.set-verdict", payload={}), ctx
    )

    assert result.kind == "precondition-failed"
    assert result.error_code == "terminal-verdict-exists"
    tx = _tx(session_factory)
    tx.rollback.assert_called_once()
    tx.commit.assert_not_called()


# --- 7. Intent / Result are frozen --------------------------------------

def test_intent_and_result_are_frozen_dataclasses():
    intent = Intent(type="x", payload={})
    result = Result(kind="ok", data=None, error_code=None, error_message=None)
    with pytest.raises(Exception):
        intent.type = "y"  # type: ignore[misc]
    with pytest.raises(Exception):
        result.kind = "validation-error"  # type: ignore[misc]


# --- 8. duplicate handler registration is rejected ----------------------

def test_duplicate_handler_registration_raises():
    @register_handler("market.foo", requires_capability="x.y")
    def _a(intent, tx, ctx):  # pragma: no cover
        return Result(kind="ok", data=None, error_code=None, error_message=None)

    with pytest.raises(ValueError, match="already registered"):
        @register_handler("market.foo", requires_capability="x.y")
        def _b(intent, tx, ctx):  # pragma: no cover
            return Result(kind="ok", data=None, error_code=None, error_message=None)
