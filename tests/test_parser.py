"""Parser tests for GSL-Ops grammar — Sprint 2, Phase 1.

Tests all GSL-Ops language constructs.
Run with: PYTHONPATH=src pytest tests/test_parser.py -v
"""

from __future__ import annotations

import pytest
from lark import Tree
from lark.exceptions import UnexpectedInput

from hassaleh.engine.parser import parse_rule


# ── Helper ──

def assert_parses(rule_text: str, expected_root: str = "start") -> Tree:
    """Assert that rule_text parses successfully and return the tree."""
    tree = parse_rule(rule_text)
    assert tree.data == expected_root
    return tree


def assert_parse_fails(rule_text: str):
    """Assert that rule_text fails to parse."""
    with pytest.raises(Exception):  # UnexpectedInput or similar
        parse_rule(rule_text)


# ══════════════════════════════════════════════
# 1. MATCH blocks
# ══════════════════════════════════════════════

def test_match_simple():
    """Simple MATCH with one node."""
    tree = assert_parses("""
MATCH (a:Agent):
    LET x = a.lifecycle
""")
    assert any(c.data == "match_block" for c in tree.find_data("match_block"))


def test_match_with_relationship():
    """MATCH with relationship pattern."""
    assert_parses("""
MATCH (a:Agent)-[:HAS_CAPABILITY]->(c:Capability):
    LET cap = c.name
""")


def test_match_with_properties():
    """MATCH with inline property filter."""
    assert_parses("""
MATCH (a:Agent {lifecycle: 'running'}):
    LET name = a.name
""")


# ══════════════════════════════════════════════
# 2. IF / ELIF / ELSE
# ══════════════════════════════════════════════

def test_if_simple():
    """Simple IF condition."""
    assert_parses("""
MATCH (a:Agent):
    IF a.lifecycle == "running":
        LET x = 1
""")


def test_if_elif_else():
    """Full IF / ELIF / ELSE chain."""
    assert_parses("""
MATCH (a:Agent):
    IF a.restart_count_1h > 5:
        ALERT "Circuit breaker triggered"
    ELIF a.restart_count_1h > 0:
        LOG "Agent recovering"
    ELSE:
        LOG "Agent healthy"
""")


def test_nested_if():
    """Nested IF blocks."""
    assert_parses("""
MATCH (a:Agent):
    IF a.lifecycle == "running":
        IF a.restart_count_1h < 5:
            SUBMIT_INTENT "restart_agent" ON a
""")


# ══════════════════════════════════════════════
# 3. LET statements
# ══════════════════════════════════════════════

def test_let_simple():
    """Simple variable binding."""
    assert_parses("""
MATCH (a:Agent):
    LET x = 42
""")


def test_let_expression():
    """LET with arithmetic expression."""
    assert_parses("""
MATCH (a:Agent):
    LET timeout = a.max_retries * 2 + 1
""")


def test_let_function_call():
    """LET with function call."""
    assert_parses("""
MATCH (a:Agent):
    LET age = NOW() - a.last_heartbeat
""")


# ══════════════════════════════════════════════
# 4. Effects (property modifications)
# ══════════════════════════════════════════════

def test_effect_set():
    """Direct property assignment."""
    assert_parses("""
MATCH (a:Agent):
    a.lifecycle = "failed"
""")


def test_effect_add():
    """Additive property change."""
    assert_parses("""
MATCH (a:Agent):
    a.restart_count_1h += 1
""")


def test_effect_sub():
    """Subtractive property change."""
    assert_parses("""
MATCH (a:Agent):
    a.restart_count_1h -= 1
""")


def test_effect_mul():
    """Multiplicative property change."""
    assert_parses("""
MATCH (a:Agent):
    a.score *= 0.95
""")


# ══════════════════════════════════════════════
# 5. Actions (SUBMIT_INTENT, LOG, ALERT)
# ══════════════════════════════════════════════

def test_submit_intent_simple():
    """SUBMIT_INTENT without WITH clause."""
    assert_parses("""
MATCH (a:Agent):
    SUBMIT_INTENT "restart_agent" ON a
""")


def test_submit_intent_with_args():
    """SUBMIT_INTENT with WITH clause."""
    assert_parses("""
MATCH (a:Agent):
    SUBMIT_INTENT "exec-ls" ON a WITH {args: "-la", timeout: 30}
""")


def test_log_simple():
    """LOG without level."""
    assert_parses("""
MATCH (a:Agent):
    LOG "Agent heartbeat OK"
""")


def test_log_with_level():
    """LOG with explicit level."""
    assert_parses("""
MATCH (a:Agent):
    LOG "Agent unresponsive" LEVEL "warning"
""")


def test_alert_simple():
    """ALERT without target."""
    assert_parses("""
MATCH (a:Agent):
    ALERT "System overloaded"
""")


def test_alert_with_target():
    """ALERT with ON target."""
    assert_parses("""
MATCH (a:Agent):
    ALERT "Agent crashed" ON a
""")


# ══════════════════════════════════════════════
# 6. EVERY (scheduled rules)
# ══════════════════════════════════════════════

def test_every_with_string_duration():
    """EVERY with ISO 8601 duration string."""
    assert_parses("""
EVERY "PT5M":
    MATCH (a:Agent {lifecycle: 'running'}):
        LOG "Health check"
""")


def test_every_with_numeric_duration():
    """EVERY with numeric + unit."""
    assert_parses("""
EVERY 5m:
    LOG "Periodic sweep"
""")


# ══════════════════════════════════════════════
# 7. Expressions & operators
# ══════════════════════════════════════════════

def test_comparison_operators():
    """All comparison operators."""
    for op in ["==", "!=", ">", "<", ">=", "<="]:
        assert_parses(f"""
MATCH (a:Agent):
    IF a.count {op} 5:
        LET x = 1
""")


def test_logical_and_or():
    """Logical AND and OR."""
    assert_parses("""
MATCH (a:Agent):
    IF a.lifecycle == "running" && a.restart_count_1h < 5:
        LOG "OK"
""")
    assert_parses("""
MATCH (a:Agent):
    IF a.lifecycle == "failed" || a.lifecycle == "crashed":
        LOG "Problem"
""")


def test_logical_not():
    """NOT operator."""
    assert_parses("""
MATCH (a:Agent):
    IF not a.is_healthy:
        ALERT "Unhealthy"
""")


def test_arithmetic():
    """Arithmetic expressions."""
    assert_parses("""
MATCH (a:Agent):
    LET x = (a.count + 1) * 2 - 3 / 4
""")


def test_modulo():
    """Modulo operator."""
    assert_parses("""
MATCH (a:Agent):
    IF a.tick_count % 10 == 0:
        LOG "Every 10th tick"
""")


# ══════════════════════════════════════════════
# 8. Built-in functions
# ══════════════════════════════════════════════

def test_func_now():
    """NOW() function."""
    assert_parses("""
MATCH (a:Agent):
    LET t = NOW()
""")


def test_func_min_max():
    """MIN and MAX functions."""
    assert_parses("""
MATCH (a:Agent):
    LET x = MIN(a.count, 100)
    LET y = MAX(a.count, 0)
""")


def test_func_abs():
    """ABS function."""
    assert_parses("""
MATCH (a:Agent):
    LET x = ABS(a.score - 50)
""")


def test_func_clamp():
    """CLAMP function."""
    assert_parses("""
MATCH (a:Agent):
    LET x = CLAMP(a.count, 0, 100)
""")


# ══════════════════════════════════════════════
# 9. Booleans and null
# ══════════════════════════════════════════════

def test_bool_literals():
    """Boolean literals."""
    assert_parses("""
MATCH (a:Agent):
    IF true:
        LET x = false
""")


def test_null_literal():
    """Null literal."""
    assert_parses("""
MATCH (a:Agent):
    IF a.last_heartbeat == null:
        ALERT "Never seen"
""")


# ══════════════════════════════════════════════
# 10. Full operational rules (realistic)
# ══════════════════════════════════════════════

def test_agent_health_check_rule():
    """Realistic agent health check rule."""
    assert_parses("""
MATCH (a:Agent {lifecycle: 'running'}):
    IF a.last_heartbeat < NOW() - DURATION("PT5M"):
        IF a.restart_count_1h < 5:
            a.restart_count_1h += 1
            SUBMIT_INTENT "restart_agent" ON a
            LOG "Restarting unresponsive agent" LEVEL "warning"
        ELSE:
            a.lifecycle = "circuit_broken"
            ALERT "Circuit breaker: agent exceeded restart limit" ON a
            LOG "Circuit breaker triggered" LEVEL "error"
""")


def test_task_timeout_sweep_rule():
    """Realistic task timeout sweep rule."""
    assert_parses("""
EVERY "PT15M":
    MATCH (t:Task {lifecycle: 'running'}):
        IF t.expires_at < NOW():
            t.lifecycle = "failed"
            t.error_reason = "Task timed out"
            LOG "Task timed out"
""")


def test_intent_cleanup_rule():
    """Rule to clean up old completed intents."""
    assert_parses("""
EVERY "PT1H":
    MATCH (i:Intent):
        IF i.lifecycle == "success" && i.completed_at < NOW() - DURATION("P7D"):
            LOG "Cleaning old intent"
""")


# ══════════════════════════════════════════════
# 11. Error cases
# ══════════════════════════════════════════════

# ══════════════════════════════════════════════
# 12. FOREACH + List literals
# ══════════════════════════════════════════════

def test_foreach_simple():
    """FOREACH with list literal."""
    assert_parses("""
MATCH (a:Agent):
    FOREACH status IN ["running", "failed"]:
        LOG "checking"
""")


def test_foreach_with_property():
    """FOREACH iterating over a property (list)."""
    assert_parses("""
MATCH (a:Agent):
    FOREACH tag IN a.tags:
        LOG "tag"
""")


def test_foreach_with_range():
    """FOREACH with RANGE function."""
    assert_parses("""
FOREACH i IN RANGE(1, 10):
    LOG "tick"
""")


def test_list_literal_empty():
    """Empty list literal."""
    assert_parses("""
MATCH (a:Agent):
    LET x = []
""")


def test_list_literal_numbers():
    """List of numbers."""
    assert_parses("""
MATCH (a:Agent):
    LET thresholds = [1, 5, 10, 50]
""")


def test_list_literal_mixed():
    """List with mixed expressions."""
    assert_parses("""
MATCH (a:Agent):
    LET items = [a.name, "default", 42]
""")


def test_foreach_nested_in_match():
    """FOREACH inside MATCH with IF."""
    assert_parses("""
MATCH (a:Agent):
    FOREACH cap IN KEYS(a):
        IF cap != "id":
            LOG "property found"
""")


def test_foreach_realistic_rule():
    """Realistic rule: iterate failed tasks and reset."""
    assert_parses("""
EVERY "PT10M":
    MATCH (t:Task {lifecycle: 'failed'}):
        FOREACH reason IN ["timeout", "crash", "oom"]:
            IF t.error_reason == reason:
                t.lifecycle = "pending"
                t.retry_count += 1
                LOG "Retrying task"
""")


def test_missing_colon_fails():
    """Missing colon after MATCH should fail."""
    assert_parse_fails("""
MATCH (a:Agent)
    LET x = 1
""")


def test_missing_indent_fails():
    """Missing indent after colon should fail."""
    assert_parse_fails("""
MATCH (a:Agent):
LET x = 1
""")
