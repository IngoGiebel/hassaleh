"""Priority-based conflict resolver for GSL-Ops rule intents.

When multiple rules target the same property on the same node,
the resolver determines the final value using these strategies:

1. SET operations: lowest priority number wins (priority 1 beats priority 10)
2. ADD/SUB operations: all are applied additively (commutative)
3. MUL operations: all are applied multiplicatively (commutative)
4. Mixed: SET is applied first, then ADD/SUB, then MUL

Reference: docs/CONCEPT.md v1.2, Section 4.14
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from hassaleh.engine.runtime import PropertyOp, RuleIntent


def resolve_intents(
    intents: list[RuleIntent],
    current_values: dict[tuple[str, str], Any] | None = None,
) -> dict[tuple[str, str], Any]:
    """Resolve conflicting property intents into final values.

    Args:
        intents: List of RuleIntents from all evaluated rules
        current_values: Optional dict of (node_id, property) → current value
                        for ADD/SUB/MUL operations

    Returns:
        Dict of (node_id, property) → resolved value
    """
    if current_values is None:
        current_values = {}

    # Group intents by (node_id, property)
    grouped: dict[tuple[str, str], list[RuleIntent]] = defaultdict(list)
    for intent in intents:
        key = (intent.node_id, intent.property)
        grouped[key].append(intent)

    # Resolve each group
    result: dict[tuple[str, str], Any] = {}

    for key, group in grouped.items():
        # Separate by operation type
        sets = [i for i in group if i.op == PropertyOp.SET]
        adds = [i for i in group if i.op == PropertyOp.ADD]
        subs = [i for i in group if i.op == PropertyOp.SUB]
        muls = [i for i in group if i.op == PropertyOp.MUL]

        # Start with current value or 0
        current = current_values.get(key, 0)

        # 1. SET: lowest priority wins
        if sets:
            winner = min(sets, key=lambda i: i.priority)
            current = winner.value

        # 2. ADD/SUB: all applied (commutative)
        for intent in adds:
            current = _safe_add(current, intent.value)
        for intent in subs:
            current = _safe_add(current, -intent.value)

        # 3. MUL: all applied (commutative)
        for intent in muls:
            current = _safe_mul(current, intent.value)

        result[key] = current

    return result


def _safe_add(a: Any, b: Any) -> Any:
    """Add two values, handling type mismatches gracefully."""
    try:
        return a + b
    except TypeError:
        # If current is not numeric, try converting
        try:
            return float(a) + float(b)
        except (ValueError, TypeError):
            return b  # Fall back to replacement


def _safe_mul(a: Any, b: Any) -> Any:
    """Multiply two values, handling type mismatches gracefully."""
    try:
        return a * b
    except TypeError:
        try:
            return float(a) * float(b)
        except (ValueError, TypeError):
            return a  # Keep original on failure
