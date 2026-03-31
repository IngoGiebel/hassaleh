"""GSL-Ops Runtime — Execution context for compiled rules.

Compiled GSL-Ops rules call methods on a RuleContext object.
This module provides the runtime environment.

Unlike GWW3's stochastic runtime, this is fully deterministic:
no distributions, no probability gates, no sampling.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("hassaleh.engine.runtime")


# ──────────────────────────────────────────────
# Intent — output of rule evaluation
# ──────────────────────────────────────────────

class PropertyOp(str, Enum):
    """Property modification operations."""
    SET = "set"
    ADD = "add"
    SUB = "sub"
    MUL = "mul"


@dataclass
class RuleIntent:
    """An intent to modify a property, produced by a rule.

    Intents are accumulated per (node_id, property) and resolved
    by the priority-based conflict resolver.
    """
    node_id: str
    node_var: str
    property: str
    op: PropertyOp
    value: Any
    rule_id: str = ""
    priority: int = 100


@dataclass
class SubmitIntentAction:
    """An intent to create a Hassaleh Intent node (capability execution)."""
    capability_id: str
    target_node: Any  # Neo4j node dict
    args: dict[str, Any] = field(default_factory=dict)
    rule_id: str = ""


@dataclass
class LogAction:
    """A structured log entry from a rule."""
    message: str
    level: str = "info"
    rule_id: str = ""


@dataclass
class AlertAction:
    """An alert notification from a rule."""
    message: str
    target_node: Any = None
    rule_id: str = ""


# ──────────────────────────────────────────────
# RuleContext — runtime environment
# ──────────────────────────────────────────────

class RuleContext:
    """Runtime context passed to compiled GSL-Ops rule functions.

    Provides:
    - Neo4j graph queries (read-only)
    - Property access
    - Intent emission (property changes, capability execution, logs, alerts)
    - Time functions
    - Schedule evaluation
    """

    def __init__(
        self,
        neo4j_session,
        rule_id: str,
        priority: int = 100,
        now: datetime | None = None,
        last_run: datetime | None = None,
    ):
        self.session = neo4j_session
        self.rule_id = rule_id
        self.priority = priority
        self._now = now or datetime.now(timezone.utc)
        self._last_run = last_run

        # Accumulated outputs
        self.property_intents: list[RuleIntent] = []
        self.submit_intents: list[SubmitIntentAction] = []
        self.logs: list[LogAction] = []
        self.alerts: list[AlertAction] = []

    # ── Graph Queries ──

    def match(self, cypher_pattern: str, **params) -> list[dict[str, Any]]:
        """Execute a Cypher MATCH and return rows as dicts.

        The Daemon calls this synchronously within its async loop
        (via run_in_executor or sync Neo4j session).
        """
        query = f"MATCH {cypher_pattern} RETURN *"
        result = self.session.run(query, **params)
        return [dict(record) for record in result]

    # ── Property Access ──

    def prop(self, node: Any, name: str) -> Any:
        """Access a property on a Neo4j node."""
        if isinstance(node, dict):
            return node.get(name)
        if hasattr(node, "__getitem__"):
            try:
                return node[name]
            except (KeyError, IndexError):
                return None
        return getattr(node, name, None)

    def node_id(self, node: Any) -> str:
        """Extract the node ID for intent tracking."""
        if hasattr(node, "element_id"):
            return node.element_id
        if isinstance(node, dict):
            if "_element_id" in node:
                return node["_element_id"]
            if "id" in node:
                return str(node["id"])
        return str(id(node))

    # ── Property Modification Intents ──

    def set_property(self, node: Any, prop: str, value: Any) -> None:
        self.property_intents.append(RuleIntent(
            node_id=self.node_id(node),
            node_var="",
            property=prop,
            op=PropertyOp.SET,
            value=value,
            rule_id=self.rule_id,
            priority=self.priority,
        ))

    def add_property(self, node: Any, prop: str, value: Any) -> None:
        self.property_intents.append(RuleIntent(
            node_id=self.node_id(node),
            node_var="",
            property=prop,
            op=PropertyOp.ADD,
            value=value,
            rule_id=self.rule_id,
            priority=self.priority,
        ))

    def sub_property(self, node: Any, prop: str, value: Any) -> None:
        self.property_intents.append(RuleIntent(
            node_id=self.node_id(node),
            node_var="",
            property=prop,
            op=PropertyOp.SUB,
            value=value,
            rule_id=self.rule_id,
            priority=self.priority,
        ))

    def mul_property(self, node: Any, prop: str, value: Any) -> None:
        self.property_intents.append(RuleIntent(
            node_id=self.node_id(node),
            node_var="",
            property=prop,
            op=PropertyOp.MUL,
            value=value,
            rule_id=self.rule_id,
            priority=self.priority,
        ))

    # ── Actions ──

    def submit_intent(self, capability_id: str, target_node: Any,
                      args: dict[str, Any] | None = None) -> None:
        """Create an Intent for the Daemon to execute a capability."""
        self.submit_intents.append(SubmitIntentAction(
            capability_id=capability_id,
            target_node=target_node,
            args=args or {},
            rule_id=self.rule_id,
        ))

    def log(self, message: str, level: str = "info") -> None:
        """Emit a structured log entry."""
        self.logs.append(LogAction(
            message=message,
            level=level,
            rule_id=self.rule_id,
        ))
        # Also log to Python logger
        py_level = getattr(logging, level.upper(), logging.INFO)
        log.log(py_level, f"[rule:{self.rule_id}] {message}")

    def alert(self, message: str, target_node: Any = None) -> None:
        """Emit an alert notification."""
        self.alerts.append(AlertAction(
            message=message,
            target_node=target_node,
            rule_id=self.rule_id,
        ))
        log.warning(f"[rule:{self.rule_id}] ALERT: {message}")

    # ── Time Functions ──

    def now(self) -> datetime:
        """Current UTC time (injectable for testing)."""
        return self._now

    def duration(self, spec: str) -> timedelta:
        """Parse an ISO 8601 duration or shorthand (5m, 1h, 30s, 1d).

        Supports: PT{n}S, PT{n}M, PT{n}H, P{n}D, and shorthands.
        """
        spec = spec.strip('"').strip("'")

        # Shorthand: 5m, 1h, 30s, 1d
        m = re.match(r'^(\d+(?:\.\d+)?)(s|m|h|d)$', spec)
        if m:
            val = float(m.group(1))
            unit = m.group(2)
            if unit == "s":
                return timedelta(seconds=val)
            if unit == "m":
                return timedelta(minutes=val)
            if unit == "h":
                return timedelta(hours=val)
            if unit == "d":
                return timedelta(days=val)

        # ISO 8601: PT5M, PT1H30M, P1D, P1DT12H
        total_seconds = 0.0
        iso = re.match(
            r'^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$',
            spec
        )
        if iso:
            days = int(iso.group(1) or 0)
            hours = int(iso.group(2) or 0)
            minutes = int(iso.group(3) or 0)
            seconds = float(iso.group(4) or 0)
            total_seconds = days * 86400 + hours * 3600 + minutes * 60 + seconds
            return timedelta(seconds=total_seconds)

        raise ValueError(f"Cannot parse duration: {spec!r}")

    # ── Schedule ──

    def should_run_schedule(self, interval_spec: str) -> bool:
        """Check if enough time has passed since last run for EVERY blocks.

        Args:
            interval_spec: Duration string (ISO 8601 or shorthand)

        Returns:
            True if the rule should fire
        """
        if self._last_run is None:
            return True  # never run before
        interval = self.duration(interval_spec)
        return (self._now - self._last_run) >= interval

    # ── Utility ──

    def clamp(self, value: float, lo: float, hi: float) -> float:
        """Clamp a value between lo and hi."""
        return max(lo, min(hi, value))
