"""Compiler tests for GSL-Ops — Sprint 2, Phase 2.

Tests compilation from GSL-Ops source to executable Python,
and verifies the compiled code runs correctly against a mock runtime.

Run with: PYTHONPATH=src pytest tests/test_compiler.py -v
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from hassaleh.engine.compiler import compile_rule, COMPILER_VERSION
from hassaleh.engine.runtime import RuleContext, PropertyOp


# ── Helper ──

def make_mock_context(
    match_rows: list[dict] | None = None,
    now: datetime | None = None,
    last_run: datetime | None = None,
) -> RuleContext:
    """Create a RuleContext with a mock Neo4j session."""
    session = MagicMock()
    if match_rows is not None:
        mock_records = list(match_rows)

        # Mock execute_read: call the function with a mock tx
        def _fake_execute_read(func):
            tx = MagicMock()
            tx.run.return_value = mock_records
            return func(tx)
        session.execute_read = _fake_execute_read

        # Also keep session.run for non-read paths
        session.run.return_value = mock_records

    ctx = RuleContext(
        neo4j_session=session,
        rule_id="test-rule",
        priority=10,
        now=now or datetime(2026, 3, 31, 12, 0, 0, tzinfo=timezone.utc),
        last_run=last_run,
    )
    return ctx


def exec_rule(source: str, ctx: RuleContext) -> None:
    """Execute compiled Python source against a context."""
    code = compile(source, "<gsl-ops>", "exec")
    ns = {}
    exec(code, ns)
    ns["evaluate"](ctx)


# ══════════════════════════════════════════════
# 1. Compilation produces valid Python
# ══════════════════════════════════════════════

def test_compile_produces_python():
    """compile_rule returns valid Python source."""
    source = compile_rule("""
MATCH (a:Agent):
    LET x = 42
""", rule_id="test-1")
    assert "def evaluate(ctx:" in source
    assert "RuleContext" in source
    # Should be valid Python
    compile(source, "<test>", "exec")


def test_compile_empty_rule():
    """Empty rule compiles to a pass."""
    source = compile_rule("", rule_id="empty")
    assert "pass" in source
    compile(source, "<test>", "exec")


def test_compiler_version():
    """Compiler version is set."""
    assert COMPILER_VERSION == "gsl-ops-0.1"


# ══════════════════════════════════════════════
# 2. MATCH blocks
# ══════════════════════════════════════════════

def test_match_iterates_rows():
    """MATCH compiles to a for loop over ctx.match() results."""
    source = compile_rule("""
MATCH (a:Agent):
    LET name = a.name
""")
    assert "ctx.match(" in source
    assert 'a = _row["a"]' in source


def test_match_with_relationship():
    """MATCH with relationship extracts both variables."""
    source = compile_rule("""
MATCH (a:Agent)-[:HAS_CAPABILITY]->(c:Capability):
    LET x = c.name
""")
    assert 'a = _row["a"]' in source
    assert 'c = _row["c"]' in source


# ══════════════════════════════════════════════
# 3. IF / ELIF / ELSE
# ══════════════════════════════════════════════

def test_if_compiles():
    """IF compiles to Python if statement."""
    source = compile_rule("""
MATCH (a:Agent):
    IF a.lifecycle == "running":
        LET x = 1
""")
    assert "if " in source
    assert 'ctx.prop(a, "lifecycle")' in source


def test_elif_else_compiles():
    """ELIF/ELSE compile correctly."""
    source = compile_rule("""
MATCH (a:Agent):
    IF a.count > 5:
        LET x = 1
    ELIF a.count > 0:
        LET x = 2
    ELSE:
        LET x = 3
""")
    assert "if " in source
    assert "elif " in source
    assert "else:" in source


# ══════════════════════════════════════════════
# 4. Effects
# ══════════════════════════════════════════════

def test_effect_set():
    """SET effect compiles to ctx.set_property."""
    source = compile_rule("""
MATCH (a:Agent):
    a.lifecycle = "failed"
""")
    assert 'ctx.set_property(a, "lifecycle"' in source


def test_effect_add():
    """+= compiles to ctx.add_property."""
    source = compile_rule("""
MATCH (a:Agent):
    a.count += 1
""")
    assert 'ctx.add_property(a, "count"' in source


def test_effect_sub():
    """-= compiles to ctx.sub_property."""
    source = compile_rule("""
MATCH (a:Agent):
    a.count -= 1
""")
    assert 'ctx.sub_property(a, "count"' in source


def test_effect_mul():
    """*= compiles to ctx.mul_property."""
    source = compile_rule("""
MATCH (a:Agent):
    a.score *= 0.95
""")
    assert 'ctx.mul_property(a, "score"' in source


# ══════════════════════════════════════════════
# 5. Actions
# ══════════════════════════════════════════════

def test_submit_intent_simple():
    """SUBMIT_INTENT compiles to ctx.submit_intent."""
    source = compile_rule("""
MATCH (a:Agent):
    SUBMIT_INTENT "restart_agent" ON a
""")
    assert 'ctx.submit_intent("restart_agent", a)' in source


def test_submit_intent_with_args():
    """SUBMIT_INTENT WITH compiles with args dict."""
    source = compile_rule("""
MATCH (a:Agent):
    SUBMIT_INTENT "exec-ls" ON a WITH {args: "-la", timeout: 30}
""")
    assert 'ctx.submit_intent("exec-ls", a,' in source
    assert '"args"' in source
    assert '"timeout"' in source


def test_log_compiles():
    """LOG compiles to ctx.log."""
    source = compile_rule("""
MATCH (a:Agent):
    LOG "Agent OK"
""")
    assert 'ctx.log("Agent OK")' in source


def test_log_with_level():
    """LOG with LEVEL compiles correctly."""
    source = compile_rule("""
MATCH (a:Agent):
    LOG "Problem" LEVEL "warning"
""")
    assert 'ctx.log("Problem", level="warning")' in source


def test_alert_compiles():
    """ALERT compiles to ctx.alert."""
    source = compile_rule("""
MATCH (a:Agent):
    ALERT "Down" ON a
""")
    assert 'ctx.alert("Down", a)' in source


# ══════════════════════════════════════════════
# 6. EVERY blocks
# ══════════════════════════════════════════════

def test_every_compiles_to_schedule_check():
    """EVERY compiles to ctx.should_run_schedule."""
    source = compile_rule("""
EVERY "PT5M":
    LOG "tick"
""")
    assert "ctx.should_run_schedule(" in source


# ══════════════════════════════════════════════
# 7. Built-in functions
# ══════════════════════════════════════════════

def test_now_function():
    """NOW() compiles to ctx.now()."""
    source = compile_rule("""
MATCH (a:Agent):
    LET t = NOW()
""")
    assert "ctx.now()" in source


def test_duration_function():
    """DURATION() compiles to ctx.duration()."""
    source = compile_rule("""
MATCH (a:Agent):
    LET d = DURATION("PT5M")
""")
    assert 'ctx.duration("PT5M")' in source


def test_min_max_functions():
    """MIN/MAX compile to Python builtins."""
    source = compile_rule("""
MATCH (a:Agent):
    LET x = MIN(a.count, 100)
    LET y = MAX(a.count, 0)
""")
    assert "min(" in source
    assert "max(" in source


# ══════════════════════════════════════════════
# 8. End-to-end execution
# ══════════════════════════════════════════════

def test_e2e_match_and_set():
    """Full cycle: compile → execute → verify effects."""
    source = compile_rule("""
MATCH (a:Agent):
    a.lifecycle = "restarting"
""", rule_id="e2e-test")

    agent = {"id": "dione", "lifecycle": "running", "name": "Dione"}
    ctx = make_mock_context(match_rows=[{"a": agent}])
    exec_rule(source, ctx)

    assert len(ctx.property_intents) == 1
    intent = ctx.property_intents[0]
    assert intent.property == "lifecycle"
    assert intent.op == PropertyOp.SET
    assert intent.value == "restarting"


def test_e2e_conditional_alert():
    """IF condition triggers ALERT."""
    source = compile_rule("""
MATCH (a:Agent):
    IF a.restart_count > 5:
        ALERT "Too many restarts" ON a
""", rule_id="alert-test")

    agent = {"id": "dione", "restart_count": 10}
    ctx = make_mock_context(match_rows=[{"a": agent}])
    exec_rule(source, ctx)

    assert len(ctx.alerts) == 1
    assert ctx.alerts[0].message == "Too many restarts"


def test_e2e_conditional_no_fire():
    """IF condition doesn't fire when false."""
    source = compile_rule("""
MATCH (a:Agent):
    IF a.restart_count > 5:
        ALERT "Too many restarts" ON a
""")

    agent = {"id": "dione", "restart_count": 2}
    ctx = make_mock_context(match_rows=[{"a": agent}])
    exec_rule(source, ctx)

    assert len(ctx.alerts) == 0


def test_e2e_submit_intent():
    """SUBMIT_INTENT creates a SubmitIntentAction."""
    source = compile_rule("""
MATCH (a:Agent):
    SUBMIT_INTENT "restart_agent" ON a
""")

    agent = {"id": "dione"}
    ctx = make_mock_context(match_rows=[{"a": agent}])
    exec_rule(source, ctx)

    assert len(ctx.submit_intents) == 1
    assert ctx.submit_intents[0].capability_id == "restart_agent"


def test_e2e_every_fires():
    """EVERY block fires when enough time has passed."""
    source = compile_rule("""
EVERY "PT5M":
    LOG "sweep"
""")

    ctx = make_mock_context(
        now=datetime(2026, 3, 31, 12, 10, tzinfo=timezone.utc),
        last_run=datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc),
    )
    exec_rule(source, ctx)

    assert len(ctx.logs) == 1
    assert ctx.logs[0].message == "sweep"


def test_e2e_every_skips():
    """EVERY block skips when not enough time has passed."""
    source = compile_rule("""
EVERY "PT5M":
    LOG "sweep"
""")

    ctx = make_mock_context(
        now=datetime(2026, 3, 31, 12, 2, tzinfo=timezone.utc),
        last_run=datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc),
    )
    exec_rule(source, ctx)

    assert len(ctx.logs) == 0


def test_let_self_reference():
    """LET count = count + 1 must not erase 'count' from RHS (Gemini fix D)."""
    source = compile_rule("""
MATCH (a:Agent):
    LET count = count + 1
""")
    # The compiled code should reference 'count' on the RHS
    assert "count + 1" in source or "count +  1" in source


def test_e2e_let_self_reference_executes():
    """LET count = count + 1 executes correctly."""
    source = compile_rule("""
MATCH (a:Agent):
    LET count = 5
    LET count = count + 1
""")
    agent = {"id": "dione"}
    ctx = make_mock_context(match_rows=[{"a": agent}])
    exec_rule(source, ctx)
    # No assertion on ctx — just verify it doesn't crash with SyntaxError


def test_unknown_function_rejected():
    """Unknown functions are rejected at compile time (security fix)."""
    import pytest
    with pytest.raises(ValueError, match="Unknown function"):
        compile_rule("""
MATCH (a:Agent):
    LET x = session()
""")


# ══════════════════════════════════════════════
# 9. FOREACH + Lists
# ══════════════════════════════════════════════

def test_foreach_compiles():
    """FOREACH compiles to Python for loop."""
    source = compile_rule("""
MATCH (a:Agent):
    FOREACH tag IN a.tags:
        LOG "tag found"
""")
    assert "for tag in" in source
    assert 'ctx.prop(a, "tags")' in source


def test_list_literal_compiles():
    """List literal compiles to Python list."""
    source = compile_rule("""
MATCH (a:Agent):
    LET statuses = ["running", "failed", "pending"]
""")
    assert '["running", "failed", "pending"]' in source


def test_empty_list_compiles():
    """Empty list compiles correctly."""
    source = compile_rule("""
MATCH (a:Agent):
    LET items = []
""")
    assert "[]" in source


def test_e2e_foreach_executes():
    """FOREACH executes over a list."""
    source = compile_rule("""
MATCH (a:Agent):
    FOREACH status IN ["a", "b", "c"]:
        LOG "item"
""")
    agent = {"id": "dione"}
    ctx = make_mock_context(match_rows=[{"a": agent}])
    exec_rule(source, ctx)

    # Should log 3 times (one per item)
    assert len(ctx.logs) == 3


def test_e2e_foreach_with_condition():
    """FOREACH with IF condition inside."""
    source = compile_rule("""
MATCH (a:Agent):
    FOREACH val IN [1, 2, 3, 4, 5]:
        IF val > 3:
            LOG "big"
""")
    agent = {"id": "dione"}
    ctx = make_mock_context(match_rows=[{"a": agent}])
    exec_rule(source, ctx)

    # Only 4 and 5 are > 3
    assert len(ctx.logs) == 2


def test_e2e_foreach_over_property():
    """FOREACH over a node property that is a list."""
    source = compile_rule("""
MATCH (a:Agent):
    FOREACH tag IN a.tags:
        LOG "tag"
""")
    agent = {"id": "dione", "tags": ["finance", "monitoring", "reporting"]}
    ctx = make_mock_context(match_rows=[{"a": agent}])
    exec_rule(source, ctx)

    assert len(ctx.logs) == 3


def test_e2e_agent_health_check():
    """Realistic agent health check rule executes correctly."""
    source = compile_rule("""
MATCH (a:Agent):
    IF a.last_heartbeat < NOW() - DURATION("PT5M"):
        IF a.restart_count_1h < 5:
            a.restart_count_1h += 1
            SUBMIT_INTENT "restart_agent" ON a
            LOG "Restarting agent" LEVEL "warning"
        ELSE:
            a.lifecycle = "circuit_broken"
            ALERT "Circuit breaker triggered" ON a
""", rule_id="health-check")

    # Agent that's been dead for 10 minutes
    stale_time = datetime(2026, 3, 31, 11, 50, tzinfo=timezone.utc)
    agent = {"id": "dione", "last_heartbeat": stale_time, "restart_count_1h": 2}

    ctx = make_mock_context(
        match_rows=[{"a": agent}],
        now=datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc),
    )
    exec_rule(source, ctx)

    # Should restart, not circuit break
    assert len(ctx.submit_intents) == 1
    assert ctx.submit_intents[0].capability_id == "restart_agent"
    assert len(ctx.property_intents) == 1
    assert ctx.property_intents[0].property == "restart_count_1h"
    assert ctx.property_intents[0].op == PropertyOp.ADD
    assert len(ctx.logs) == 1
    assert ctx.logs[0].level == "warning"
    assert len(ctx.alerts) == 0
