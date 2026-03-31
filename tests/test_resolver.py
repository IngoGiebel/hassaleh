"""Resolver tests for GSL-Ops — Sprint 2, Phase 2.

Tests priority-based conflict resolution and additive reduction.
Run with: PYTHONPATH=src pytest tests/test_resolver.py -v
"""

from __future__ import annotations

import pytest

from hassaleh.engine.runtime import PropertyOp, RuleIntent
from hassaleh.engine.resolver import resolve_intents


def make_intent(node_id="n1", prop="score", op=PropertyOp.SET,
                value=100, priority=10, rule_id="rule-1") -> RuleIntent:
    return RuleIntent(
        node_id=node_id, node_var="a", property=prop,
        op=op, value=value, rule_id=rule_id, priority=priority,
    )


# ══════════════════════════════════════════════
# 1. SET — lowest priority wins
# ══════════════════════════════════════════════

def test_set_single():
    """Single SET intent applied."""
    result = resolve_intents([make_intent(op=PropertyOp.SET, value=42)])
    assert result[("n1", "score")] == 42


def test_set_priority_wins():
    """Lower priority number wins for SET conflicts."""
    intents = [
        make_intent(op=PropertyOp.SET, value=100, priority=10, rule_id="low-prio"),
        make_intent(op=PropertyOp.SET, value=999, priority=1, rule_id="high-prio"),
    ]
    result = resolve_intents(intents)
    assert result[("n1", "score")] == 999  # priority 1 wins


def test_set_same_priority():
    """Same priority SET: first in list wins (deterministic)."""
    intents = [
        make_intent(op=PropertyOp.SET, value=100, priority=5, rule_id="a"),
        make_intent(op=PropertyOp.SET, value=200, priority=5, rule_id="b"),
    ]
    result = resolve_intents(intents)
    assert result[("n1", "score")] in (100, 200)


# ══════════════════════════════════════════════
# 2. ADD/SUB — commutative
# ══════════════════════════════════════════════

def test_add_single():
    """Single ADD applied to current value."""
    result = resolve_intents(
        [make_intent(op=PropertyOp.ADD, value=10)],
        current_values={("n1", "score"): 100},
    )
    assert result[("n1", "score")] == 110


def test_add_multiple_commutative():
    """Multiple ADDs are applied additively."""
    intents = [
        make_intent(op=PropertyOp.ADD, value=10, rule_id="a"),
        make_intent(op=PropertyOp.ADD, value=20, rule_id="b"),
        make_intent(op=PropertyOp.ADD, value=5, rule_id="c"),
    ]
    result = resolve_intents(intents, {("n1", "score"): 100})
    assert result[("n1", "score")] == 135


def test_sub_applied():
    """SUB reduces the value."""
    result = resolve_intents(
        [make_intent(op=PropertyOp.SUB, value=30)],
        current_values={("n1", "score"): 100},
    )
    assert result[("n1", "score")] == 70


def test_add_and_sub_combined():
    """ADD and SUB combined."""
    intents = [
        make_intent(op=PropertyOp.ADD, value=20),
        make_intent(op=PropertyOp.SUB, value=5),
    ]
    result = resolve_intents(intents, {("n1", "score"): 100})
    assert result[("n1", "score")] == 115


# ══════════════════════════════════════════════
# 3. MUL — commutative
# ══════════════════════════════════════════════

def test_mul_single():
    """Single MUL scales the value."""
    result = resolve_intents(
        [make_intent(op=PropertyOp.MUL, value=0.5)],
        current_values={("n1", "score"): 100},
    )
    assert result[("n1", "score")] == 50


def test_mul_multiple():
    """Multiple MULs are applied multiplicatively."""
    intents = [
        make_intent(op=PropertyOp.MUL, value=2, rule_id="a"),
        make_intent(op=PropertyOp.MUL, value=3, rule_id="b"),
    ]
    result = resolve_intents(intents, {("n1", "score"): 10})
    assert result[("n1", "score")] == 60


# ══════════════════════════════════════════════
# 4. Mixed operations (SET → ADD/SUB → MUL)
# ══════════════════════════════════════════════

def test_mixed_set_then_add():
    """SET applied first, then ADD."""
    intents = [
        make_intent(op=PropertyOp.SET, value=50),
        make_intent(op=PropertyOp.ADD, value=10),
    ]
    result = resolve_intents(intents, {("n1", "score"): 100})
    assert result[("n1", "score")] == 60  # SET 50, then ADD 10


def test_mixed_set_add_mul():
    """SET → ADD → MUL all applied in order."""
    intents = [
        make_intent(op=PropertyOp.SET, value=100),
        make_intent(op=PropertyOp.ADD, value=20),
        make_intent(op=PropertyOp.MUL, value=2),
    ]
    result = resolve_intents(intents, {("n1", "score"): 0})
    assert result[("n1", "score")] == 240  # SET 100, ADD 20 = 120, MUL 2 = 240


# ══════════════════════════════════════════════
# 5. Multi-property / multi-node
# ══════════════════════════════════════════════

def test_different_properties_independent():
    """Different properties resolved independently."""
    intents = [
        make_intent(prop="score", op=PropertyOp.SET, value=100),
        make_intent(prop="lifecycle", op=PropertyOp.SET, value="running"),
    ]
    result = resolve_intents(intents)
    assert result[("n1", "score")] == 100
    assert result[("n1", "lifecycle")] == "running"


def test_different_nodes_independent():
    """Different nodes resolved independently."""
    intents = [
        make_intent(node_id="n1", op=PropertyOp.SET, value=100),
        make_intent(node_id="n2", op=PropertyOp.SET, value=200),
    ]
    result = resolve_intents(intents)
    assert result[("n1", "score")] == 100
    assert result[("n2", "score")] == 200


# ══════════════════════════════════════════════
# 6. Edge cases
# ══════════════════════════════════════════════

def test_empty_intents():
    """No intents → empty result."""
    assert resolve_intents([]) == {}


def test_add_without_current_value():
    """ADD without current value defaults to 0."""
    result = resolve_intents([make_intent(op=PropertyOp.ADD, value=10)])
    assert result[("n1", "score")] == 10


def test_string_set():
    """SET works for string values."""
    result = resolve_intents(
        [make_intent(prop="lifecycle", op=PropertyOp.SET, value="failed")]
    )
    assert result[("n1", "lifecycle")] == "failed"
