"""Tests for Sprint-14 Track C capability enforcement helpers."""

from __future__ import annotations

from hassaleh.runtime.capabilities import (
    ALLOWED_SCOPES,
    ApiKey,
    capability_api_key,
    capability_principal,
    check_scope,
)


def test_check_scope_happy_path_exact_match():
    api_key = ApiKey(id="key-1", scopes=["market.analyst.write"])

    assert check_scope(api_key, "market.analyst.write") is True


def test_check_scope_missing_scope_is_denied():
    api_key = ApiKey(id="key-1", scopes=["market.analyst.write"])

    assert check_scope(api_key, "market.reviewer.write") is False


def test_check_scope_empty_scopes_list_is_denied():
    api_key = ApiKey(id="key-1", scopes=[])

    assert check_scope(api_key, "market.analyst.write") is False


def test_check_scope_required_scope_not_in_allowed_set_is_denied_even_if_present():
    api_key = ApiKey(id="key-1", scopes=["market.*.write"])

    assert check_scope(api_key, "market.*.write") is False


def test_check_scope_does_not_apply_prefix_or_wildcard_semantics():
    api_key = ApiKey(id="key-1", scopes=["market", "market.*.write"])

    assert check_scope(api_key, "market.analyst.write") is False


def test_capability_fixture_factories_are_usable_by_track_b_and_f_tests():
    api_key = capability_api_key("market.writer.write", id="key-writer")
    principal = capability_principal("market.publisher.write", id="principal-pub")

    assert api_key == ApiKey(id="key-writer", scopes=["market.writer.write"])
    assert principal.id == "principal-pub"
    assert principal.scopes == ("market.publisher.write",)
    assert set(ALLOWED_SCOPES) == {
        "market.analyst.write",
        "market.reviewer.write",
        "market.writer.write",
        "market.publisher.write",
    }


def test_runtime_capability_check_denies_required_scope_outside_allowed_set():
    """Closed allowed-set: even matching unknown required scopes are denied."""
    from datetime import datetime, timezone

    from hassaleh.runtime.core import HassalehRuntime
    from hassaleh.runtime.types import Ctx, Intent, Principal, Result

    def handler(intent, tx, ctx):  # pragma: no cover - must not execute
        return Result.ok()

    runtime = HassalehRuntime(
        session_factory=lambda: None,  # type: ignore[arg-type]
        registry={"future.intent": (handler, "future.scope")},
    )

    result = runtime.execute(
        Intent(type="future.intent", payload={}),
        Ctx(
            principal=Principal(id="key-future", scopes=("future.scope",)),
            now=datetime(2026, 5, 7, tzinfo=timezone.utc),
        ),
    )

    assert result.kind == "capability-denied"
    assert result.error_code == "scope-not-granted"
