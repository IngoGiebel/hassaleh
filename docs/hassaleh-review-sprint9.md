# Hassaleh ⭐ — Comprehensive Code Review (Sprint 9)

You are reviewing the complete Hassaleh codebase — a graph-native agentic framework where all configuration, state, and coordination lives in Neo4j.

## Architecture Overview
- **Daemon** (daemon.py): Async systemd service. Processes Intents, evaluates GSL-Ops rules, manages agent lifecycle. Runs as `hassaleh-svc` user.
- **SDK** (sdk.py): Agent-facing read-only graph access + Intent submission. Used by agents (Dione, Inanna, etc.)
- **CLI** (cli.py): Operator CLI (`hassaleh status/init/agent/rule/intent/skill/domain/task/message/discussion`)
- **GSL-Ops Engine**: Custom rule language (gsl_ops.lark → compiler.py → runtime.py → resolver.py). Compiled to Python, cached in Neo4j.
- **Bridge** (openclaw.py, notifications.py): OpenClaw Gateway integration for Telegram notifications.
- **Domain System** (domain.py): Hierarchical skill/capability domains with prefix matching.
- **Schema** (schema.cypher, seed.cypher, seed_rules.cypher): Neo4j graph schema, seed data, operational rules.

## Review Focus Areas
1. **Security**: Cypher injection, exec() sandboxing, authentication, permission enforcement
2. **Correctness**: Race conditions, edge cases, error handling, async patterns
3. **Neo4j Patterns**: Query efficiency, index usage, transaction safety, connection lifecycle
4. **Architecture**: Separation of concerns, abstraction quality, extensibility
5. **GSL-Ops Compiler**: Grammar completeness, compilation correctness, runtime safety
6. **Production Readiness**: Logging, graceful shutdown, resource limits, monitoring
7. **Code Quality**: Type annotations, docstrings, naming, dead code

## Severity Levels
- **CRITICAL**: Security vulnerability, data loss risk, or crash bug
- **HIGH**: Significant bug, performance issue, or architectural problem
- **MEDIUM**: Code quality, missing edge cases, or minor bugs
- **LOW**: Style, documentation, or nice-to-have improvements

## Expected Output Format
For each finding:
```
### [SEVERITY] Title
**File:** filename.py, line(s) X-Y
**Category:** Security | Correctness | Performance | Architecture | Quality
**Description:** What's wrong and why it matters
**Fix:** Specific suggestion
```

---

## Source Files


### `src/hassaleh/daemon.py`
```py
#!/usr/bin/env python3.13
"""Hassaleh Daemon — Persistent async service for agent orchestration.

Runs as a systemd-managed service (hassaleh-svc user).
Processes agent Intents, enforces Capability permissions,
delegates system actions to per-capability OS users.

Reference: docs/CONCEPT.md v1.2, Section 3.2
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import socket
import sys
from datetime import datetime, timezone
from typing import Any, Sequence

from aiohttp import web
from neo4j import AsyncGraphDatabase, GraphDatabase

from hassaleh.engine.compiler import compile_rule, COMPILER_VERSION
from hassaleh.engine.runtime import RuleContext
from hassaleh.engine.resolver import resolve_intents
from hassaleh.bridge.openclaw import OpenClawBridge
from hassaleh.bridge.notifications import NotificationDispatcher
from hassaleh.domain import domain_matches_any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [hassaleh-daemon] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("hassaleh.daemon")

# ──────────────────────────────────────────────
# Configuration (loaded from Neo4j on boot)
# ──────────────────────────────────────────────

DEFAULT_CONFIG = {
    "tick_interval_ms": 1000,
    "intent_timeout_default_sec": 300,
    "max_concurrent_actions": 10,
    "health_endpoint_port": 9100,
    "sweep_interval_min": 15,
    "rule_eval_interval_sec": 60,  # Evaluate rules every 60s (separate from sweep)
    "circuit_breaker_decay_per_sweep": 1,
    "parallel_fail_fast": True,
}

TASK_TERMINAL_LIFECYCLES = frozenset({"success", "failed"})


def _next_sequential_task_id(tasks: Sequence[dict[str, Any]]) -> str | None:
    """Return the next sequential task that may transition to ready."""
    for task in tasks:
        lifecycle = task.get("lifecycle")
        if lifecycle == "success":
            continue
        if lifecycle == "pending":
            return task.get("id")
        return None
    return None


def _parallel_group_should_activate(
    lifecycles: Sequence[str],
    *,
    fail_fast: bool,
) -> bool:
    """Return True if pending parallel tasks should transition to ready."""
    if not lifecycles or "pending" not in lifecycles:
        return False
    if fail_fast and "failed" in lifecycles:
        return False
    return True


def _parent_group_lifecycle(
    lifecycles: Sequence[str],
    *,
    fail_fast: bool,
) -> str | None:
    """Resolve parent/group lifecycle from child task lifecycles."""
    if not lifecycles:
        return None
    if "awaiting_review" in lifecycles:
        return None
    if "failed" in lifecycles and (fail_fast or all(
        lifecycle in TASK_TERMINAL_LIFECYCLES for lifecycle in lifecycles
    )):
        return "failed"
    if all(lifecycle == "success" for lifecycle in lifecycles):
        return "success"
    return None


def _normalize_task_lifecycle_update(
    execution_mode: str | None,
    requested_lifecycle: str,
) -> str:
    """Apply execution-mode-specific task lifecycle transitions."""
    if execution_mode == "supervised" and requested_lifecycle == "success":
        return "awaiting_review"
    return requested_lifecycle


def _is_domain_allowed(
    allowed_domains: Sequence[str] | None,
    capability_domain: str | None,
) -> bool:
    """Return True when the capability domain is permitted for the agent."""
    return domain_matches_any(
        capability_domain,
        allowed_domains,
        allow_unscoped=True,
    )


def _domain_specificity(domain: str | None) -> tuple[int, int, str]:
    """Return a sortable specificity tuple for a capability domain."""
    normalized = (domain or "").strip().strip(".")
    if not normalized:
        return (0, 0, "")
    depth = normalized.count(".") + 1
    return (depth, len(normalized), normalized)


def _prefer_more_specific_capability(
    capabilities: Sequence[dict[str, Any]],
) -> dict[str, Any] | None:
    """Pick the capability with the most specific domain."""
    if not capabilities:
        return None
    return max(
        capabilities,
        key=lambda capability: (
            _domain_specificity(capability.get("domain")),
            str(capability.get("id", "")),
        ),
    )


# ──────────────────────────────────────────────
# Daemon
# ──────────────────────────────────────────────

class HassalehDaemon:
    """Main Daemon process — async event loop."""

    def __init__(self, neo4j_uri: str, neo4j_user: str, neo4j_password: str):
        self.neo4j_uri = neo4j_uri
        self.neo4j_user = neo4j_user
        self.neo4j_password = neo4j_password
        self.driver: Any = None
        self.sync_driver: Any = None  # Sync driver for rule evaluation
        self.config: dict = dict(DEFAULT_CONFIG)
        self.running = False
        self.tick_count = 0
        self.active_workers: dict[str, asyncio.Task] = {}
        self._shutdown_event = asyncio.Event()
        self._start_time: float = 0
        self._health_app: web.Application | None = None
        self._health_runner: web.AppRunner | None = None
        self._watchdog_usec: int = 0  # systemd watchdog interval (0 = disabled)
        self._compiled_rules: dict[str, Any] = {}  # rule_id → compiled function
        self._rule_last_run: dict[str, datetime] = {}  # rule_id → last execution time
        self._bridge: OpenClawBridge | None = None  # OpenClaw bridge (optional)
        self._notifier: NotificationDispatcher | None = None
        self._pending_alerts: list = []  # Alerts from rule evaluation (dispatched async)
        self._pending_error_logs: list = []

    # ── Lifecycle ──

    async def start(self) -> None:
        """Boot the Daemon: connect to Neo4j, load config, recover zombies, run loop."""
        log.info("Starting Hassaleh Daemon...")

        # Connect to Neo4j
        self.driver = AsyncGraphDatabase.driver(
            self.neo4j_uri,
            auth=(self.neo4j_user, self.neo4j_password),
        )
        log.info(f"Connected to Neo4j at {self.neo4j_uri}")

        # Verify connectivity
        async with self.driver.session() as session:
            result = await session.run("RETURN 1 AS ping")
            record = await result.single()
            assert record["ping"] == 1
        log.info("Neo4j connection verified")

        # Load configuration from graph
        await self._load_config()

        # Check schema version
        await self._check_schema_version()

        # Create sync driver for rule evaluation (rules use sync Neo4j)
        self.sync_driver = GraphDatabase.driver(
            self.neo4j_uri,
            auth=(self.neo4j_user, self.neo4j_password),
        )
        log.info("Sync Neo4j driver created for rule evaluation")

        # Load and compile rules
        await self._load_rules()

        # Recover zombie Intents
        await self._recover_zombies()

        # Start health endpoint
        await self._start_health_endpoint()

        # Initialize OpenClaw bridge (optional — works without it)
        await self._init_bridge()

        # Initialize watchdog
        self._init_watchdog()

        # Start the main loop
        self.running = True
        self._start_time = asyncio.get_event_loop().time()
        self._sd_notify("READY=1")
        log.info(f"Daemon running (tick interval: {self.config['tick_interval_ms']}ms)")

        await self._main_loop()

    async def shutdown(self) -> None:
        """Graceful shutdown: stop accepting, kill workers, update Intents."""
        log.info("Initiating graceful shutdown...")
        self.running = False
        self._sd_notify("STOPPING=1")

        # Cancel all active workers
        if self.active_workers:
            log.info(f"Terminating {len(self.active_workers)} active workers...")
            for intent_id, task in self.active_workers.items():
                task.cancel()
            # Wait for cancellation (up to 30s)
            await asyncio.gather(*self.active_workers.values(), return_exceptions=True)

            # Mark remaining running Intents as failed
            async with self.driver.session() as session:
                await session.run("""
                    MATCH (i:Intent {lifecycle: 'running'})
                    SET i.lifecycle = 'failed',
                        i.error_reason = 'Daemon shutdown',
                        i.completed_at = datetime({timezone: 'UTC'})
                """)
            log.info("Running Intents marked as failed")

        # Stop health endpoint
        await self._stop_health_endpoint()

        # Close OpenClaw bridge
        if self._bridge:
            await self._bridge.__aexit__(None, None, None)

        # Close Neo4j
        if self.sync_driver:
            self.sync_driver.close()
        if self.driver:
            await self.driver.close()
            log.info("Neo4j connection closed")

        log.info("Daemon shutdown complete")

    # ── Main Loop ──

    async def _main_loop(self) -> None:
        """1-second tick loop: process Intents, evaluate rules."""
        tick_interval = self.config["tick_interval_ms"] / 1000.0
        sweep_counter = 0
        sweep_interval_ticks = int(self.config["sweep_interval_min"] * 60 / tick_interval)
        rule_eval_counter = 0
        rule_eval_interval_ticks = int(self.config["rule_eval_interval_sec"] / tick_interval)

        while self.running:
            tick_start = asyncio.get_event_loop().time()
            self.tick_count += 1

            try:
                # Watchdog ping
                self._sd_notify("WATCHDOG=1")

                # Hot path: process pending Intents
                await self._process_pending_intents()

                # Task orchestration path: execution modes + parent state sync
                await self._orchestrate_task_execution_modes()

                # Clean up finished workers
                self._reap_workers()

                # Rule evaluation (configurable interval, default 60s)
                rule_eval_counter += 1
                if rule_eval_counter >= rule_eval_interval_ticks and self._compiled_rules:
                    await asyncio.get_event_loop().run_in_executor(
                        None, self._evaluate_rules
                    )
                    rule_eval_counter = 0

                    # Dispatch accumulated alerts/error logs via OpenClaw
                    if self._notifier and (self._pending_alerts or self._pending_error_logs):
                        try:
                            sent = await self._notifier.dispatch_all(
                                self._pending_alerts, self._pending_error_logs
                            )
                            if sent:
                                log.info(f"Dispatched {sent} notification(s)")
                        except Exception as e:
                            log.error(f"Notification dispatch failed: {e}")
                        self._pending_alerts.clear()
                        self._pending_error_logs.clear()

                # Periodic sweep
                sweep_counter += 1
                if sweep_counter >= sweep_interval_ticks:
                    await self._sweep()
                    sweep_counter = 0

            except Exception as e:
                log.error(f"Tick {self.tick_count} error: {e}", exc_info=True)

            # Sleep for remainder of tick
            elapsed = asyncio.get_event_loop().time() - tick_start
            sleep_time = max(0, tick_interval - elapsed)
            if sleep_time > 0:
                try:
                    await asyncio.wait_for(
                        self._shutdown_event.wait(),
                        timeout=sleep_time,
                    )
                    # Shutdown was signaled — initiate graceful shutdown
                    await self.shutdown()
                    return
                except asyncio.TimeoutError:
                    pass  # normal tick sleep expired

    # ── Intent Processing ──

    async def _process_pending_intents(self) -> None:
        """Find and process all pending Intents.

        Uses atomic claim: a single transaction MATCHes pending Intents
        and SETs them to 'claimed' so no other Daemon instance (or fast
        restart) can pick up the same Intent.  After claim, permission
        checks run; if they fail the Intent is rejected/parked, otherwise
        a worker is spawned and the Intent transitions to 'running'.
        """
        max_workers = self.config["max_concurrent_actions"]
        available_slots = max_workers - len(self.active_workers)
        if available_slots <= 0:
            return

        # ── Atomic claim: pending → claimed in one write transaction ──
        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (agent:Agent)-[:PROPOSED]->(i:Intent {lifecycle: 'pending'})
                WITH i, agent
                ORDER BY i.submitted_at ASC
                LIMIT $limit
                SET i.lifecycle = 'claimed'
                RETURN i, agent
            """, limit=available_slots)

            records = [record async for record in result]

        for record in records:
            intent = record["i"]
            agent = record["agent"]
            intent_id = intent["id"]

            # Check capability permission
            if intent["action"] == "execute_capability":
                has_cap, reason = await self._check_capability(agent["id"], intent)
                if not has_cap:
                    await self._reject_intent(intent_id, reason)
                    continue

                # Check if capability requires confirmation
                needs_approval = await self._needs_approval(intent)
                if needs_approval:
                    await self._park_intent(intent_id)
                    continue

            # Spawn async worker
            task = asyncio.create_task(
                self._execute_intent(intent_id, intent, agent),
                name=f"intent-{intent_id}",
            )
            self.active_workers[intent_id] = task
            log.info(f"Spawned worker for Intent {intent_id} (action: {intent['action']})")

    async def _execute_intent(self, intent_id: str, intent: dict, agent: dict) -> None:
        """Execute a single Intent asynchronously."""
        try:
            # Mark as running
            async with self.driver.session() as session:
                await session.run("""
                    MATCH (i:Intent {id: $id})
                    SET i.lifecycle = 'running',
                        i.started_at = datetime({timezone: 'UTC'})
                """, id=intent_id)

            action = intent["action"]

            if action == "execute_capability":
                await self._execute_capability(intent_id, intent)
            elif action == "update_property":
                await self._execute_update(intent_id, intent)
            elif action == "review_task":
                await self._execute_task_review(intent_id, agent)
            elif action == "assign_task":
                await self._execute_assign_task(intent_id, agent)
            else:
                await self._reject_intent(intent_id, f"Unknown action: {action}")
                return

        except asyncio.CancelledError:
            log.warning(f"Intent {intent_id} cancelled (shutdown)")
            raise
        except Exception as e:
            log.error(f"Intent {intent_id} failed: {e}", exc_info=True)
            await self._fail_intent(intent_id, str(e))

    async def _execute_capability(self, intent_id: str, intent: dict) -> None:
        """Execute a capability via async subprocess."""
        # Look up the capability's invoke_command and exec_as_user
        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (i:Intent {id: $id})-[:TARGETS]->(cap:Capability)
                RETURN cap.invoke_command AS command, cap.exec_as_user AS exec_user
            """, id=intent_id)
            record = await result.single()

        if not record:
            await self._fail_intent(intent_id, "No capability linked to Intent")
            return

        command = record["command"]
        exec_user = record["exec_user"]

        # Parse additional args from Intent value
        args = []
        if intent.get("value"):
            try:
                parsed = json.loads(intent["value"])
                if isinstance(parsed, dict) and "args" in parsed:
                    args = parsed["args"].split() if isinstance(parsed["args"], str) else parsed["args"]
            except (json.JSONDecodeError, TypeError):
                args = str(intent["value"]).split()

        # Build safe command array
        cmd = ["sudo", "-n", "-u", exec_user, command] + args

        log.info(f"Executing: {' '.join(cmd)}")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.config["intent_timeout_default_sec"],
            )

            stdout_str = stdout.decode("utf-8", errors="replace") if stdout else ""
            stderr_str = stderr.decode("utf-8", errors="replace") if stderr else ""

            async with self.driver.session() as session:
                await session.run("""
                    MATCH (i:Intent {id: $id})
                    SET i.lifecycle = $lifecycle,
                        i.stdout = $stdout,
                        i.stderr = $stderr,
                        i.exit_code = $exit_code,
                        i.completed_at = datetime({timezone: 'UTC'})
                """,
                    id=intent_id,
                    lifecycle="success" if proc.returncode == 0 else "failed",
                    stdout=stdout_str[:100000],  # cap at 100KB
                    stderr=stderr_str[:100000],
                    exit_code=proc.returncode,
                )

            log.info(f"Intent {intent_id}: exit={proc.returncode}, stdout={len(stdout_str)} bytes")

        except asyncio.TimeoutError:
            # Kill the orphaned subprocess
            try:
                proc.kill()
                await proc.wait()
            except (ProcessLookupError, OSError):
                pass
            await self._fail_intent(intent_id, "Execution timed out")

    async def _execute_update(self, intent_id: str, intent: dict) -> None:
        """Execute a property update on the target node."""
        prop = intent.get("property")
        value = intent.get("value")

        if not prop:
            await self._fail_intent(intent_id, "Missing property name")
            return

        async with self.driver.session() as session:
            if prop == "lifecycle":
                result = await session.run("""
                    MATCH (i:Intent {id: $id})-[:TARGETS]->(target)
                    RETURN labels(target) AS labels,
                           target.execution_mode AS execution_mode
                """, id=intent_id)
                record = await result.single()
                if record and "Task" in record["labels"]:
                    value = _normalize_task_lifecycle_update(
                        record.get("execution_mode"),
                        str(value),
                    )

            await session.run("""
                MATCH (i:Intent {id: $id})-[:TARGETS]->(target)
                SET target[$prop] = $value
            """, id=intent_id, prop=prop, value=value)

            await session.run("""
                MATCH (i:Intent {id: $id})
                SET i.lifecycle = 'success',
                    i.completed_at = datetime({timezone: 'UTC'})
            """, id=intent_id)

        log.info(f"Intent {intent_id}: updated property '{prop}'")

    async def _execute_task_review(self, intent_id: str, agent: dict) -> None:
        """Approve or reject a supervised task awaiting review."""
        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (i:Intent {id: $id})-[:TARGETS]->(t:Task)
                OPTIONAL MATCH (t)-[:SUPERVISED_BY]->(lead:Agent)
                RETURN t.id AS task_id,
                       t.lifecycle AS lifecycle,
                       collect(lead.id) AS lead_agent_ids,
                       i.value AS review_payload
            """, id=intent_id)
            record = await result.single()

            if not record or not record["task_id"]:
                await self._fail_intent(intent_id, "No Task linked to review Intent")
                return

            lead_agent_ids = [lead_id for lead_id in record["lead_agent_ids"] if lead_id]
            if agent["id"] not in lead_agent_ids:
                await self._reject_intent(intent_id, "Agent is not the supervising lead")
                return

            if record["lifecycle"] != "awaiting_review":
                await self._reject_intent(
                    intent_id,
                    f"Task is '{record['lifecycle']}', not 'awaiting_review'",
                )
                return

            approved = True
            comment = ""
            payload = record.get("review_payload")
            if payload:
                try:
                    parsed = json.loads(payload)
                    approved = bool(parsed.get("approved", False))
                    comment = str(parsed.get("comment", ""))
                except (TypeError, json.JSONDecodeError):
                    await self._fail_intent(intent_id, "Invalid review payload")
                    return

            target_lifecycle = "success" if approved else "failed"
            await session.run("""
                MATCH (t:Task {id: $task_id})
                SET t.lifecycle = $lifecycle,
                    t.completed_at = datetime({timezone: 'UTC'}),
                    t.reviewed_at = datetime({timezone: 'UTC'}),
                    t.reviewed_by = $agent_id,
                    t.review_comment = $comment
            """,
                task_id=record["task_id"],
                lifecycle=target_lifecycle,
                agent_id=agent["id"],
                comment=comment,
            )
            await session.run("""
                MATCH (i:Intent {id: $id})
                SET i.lifecycle = 'success',
                    i.completed_at = datetime({timezone: 'UTC'})
            """, id=intent_id)

        log.info(
            "Intent %s: reviewed task %s as %s",
            intent_id,
            record["task_id"],
            target_lifecycle,
        )

    async def _execute_assign_task(self, intent_id: str, agent: dict) -> None:
        """Create ASSIGNED_TO edge for a rule-generated task assignment intent."""
        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (i:Intent {id: $id})
                RETURN i.value AS payload
            """, id=intent_id)
            record = await result.single()
            if not record or not record.get("payload"):
                await self._fail_intent(intent_id, "Missing assignment payload")
                return

            try:
                payload = json.loads(record["payload"])
            except (TypeError, json.JSONDecodeError):
                await self._fail_intent(intent_id, "Invalid assignment payload")
                return

            task_id = payload.get("task_id")
            if not task_id:
                await self._fail_intent(intent_id, "Assignment payload missing task_id")
                return

            task_result = await session.run("""
                MATCH (t:Task {id: $task_id})
                OPTIONAL MATCH (t)-[:ASSIGNED_TO]->(existing:Agent)
                RETURN t.id AS task_id,
                       collect(existing.id) AS assigned_agent_ids
            """, task_id=task_id)
            task_record = await task_result.single()
            if not task_record or not task_record["task_id"]:
                await self._fail_intent(intent_id, f"Task '{task_id}' not found")
                return

            assigned_agent_ids = [
                assigned_id
                for assigned_id in task_record["assigned_agent_ids"]
                if assigned_id
            ]
            if assigned_agent_ids and agent["id"] not in assigned_agent_ids:
                await self._reject_intent(
                    intent_id,
                    f"Task already assigned to {', '.join(assigned_agent_ids)}",
                )
                return

            await session.run("""
                MATCH (t:Task {id: $task_id})
                MATCH (a:Agent {id: $agent_id})
                MERGE (t)-[:ASSIGNED_TO]->(a)
            """, task_id=task_id, agent_id=agent["id"])
            await session.run("""
                MATCH (i:Intent {id: $id})
                SET i.lifecycle = 'success',
                    i.completed_at = datetime({timezone: 'UTC'})
            """, id=intent_id)

        log.info("Intent %s: assigned task %s to %s", intent_id, task_id, agent["id"])

    # ── Permission Checks ──

    async def _check_capability(self, agent_id: str, intent: dict) -> tuple[bool, str]:
        """Verify the agent has the required capability and domain access."""
        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (a:Agent {id: $agent_id})
                OPTIONAL MATCH (a)-[:OPERATES_IN]->(ws:Workspace)
                MATCH (a)-[:HAS_CAPABILITY]->(cap:Capability)
                MATCH (i:Intent {id: $intent_id})-[:TARGETS]->(cap)
                RETURN ws.allowed_domains AS allowed_domains,
                       cap.domain AS capability_domain
            """, agent_id=agent_id, intent_id=intent["id"])
            record = await result.single()
            if not record:
                return False, "Agent lacks required capability"

            if not _is_domain_allowed(
                record.get("allowed_domains"),
                record.get("capability_domain"),
            ):
                domain = record.get("capability_domain") or "unscoped"
                return False, f"Capability domain '{domain}' is not allowed for agent"

            return True, ""

    async def _needs_approval(self, intent: dict) -> bool:
        """Check if the targeted capability requires human confirmation."""
        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (i:Intent {id: $id})-[:TARGETS]->(cap:Capability)
                RETURN cap.requires_confirmation AS needs
            """, id=intent["id"])
            record = await result.single()
            return bool(record and record["needs"])

    # ── Intent State Transitions ──

    async def _reject_intent(self, intent_id: str, reason: str) -> None:
        async with self.driver.session() as session:
            await session.run("""
                MATCH (i:Intent {id: $id})
                SET i.lifecycle = 'rejected',
                    i.error_reason = $reason,
                    i.completed_at = datetime({timezone: 'UTC'})
            """, id=intent_id, reason=reason)
        log.warning(f"Intent {intent_id} rejected: {reason}")

    async def _fail_intent(self, intent_id: str, reason: str) -> None:
        async with self.driver.session() as session:
            await session.run("""
                MATCH (i:Intent {id: $id})
                SET i.lifecycle = 'failed',
                    i.error_reason = $reason,
                    i.completed_at = datetime({timezone: 'UTC'})
            """, id=intent_id, reason=reason)
        log.error(f"Intent {intent_id} failed: {reason}")

    async def _park_intent(self, intent_id: str) -> None:
        async with self.driver.session() as session:
            await session.run("""
                MATCH (i:Intent {id: $id})
                SET i.lifecycle = 'awaiting_approval'
            """, id=intent_id)
        log.info(f"Intent {intent_id} parked for human approval")

    # ── Zombie Recovery ──

    async def _recover_zombies(self) -> None:
        """On boot: transition stale 'running' or 'claimed' Intents to 'failed'."""
        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (i:Intent)
                WHERE i.lifecycle IN ['running', 'claimed']
                SET i.lifecycle = 'failed',
                    i.error_reason = 'Daemon restarted during execution',
                    i.completed_at = datetime({timezone: 'UTC'})
                RETURN count(*) AS recovered
            """)
            record = await result.single()
            count = record["recovered"]
            if count > 0:
                log.warning(f"Recovered {count} zombie Intent(s)")

    # ── Task Orchestration ──

    async def _orchestrate_task_execution_modes(self) -> None:
        """Advance grouped tasks according to their execution mode."""
        await self._activate_sequential_task_groups()
        await self._activate_parallel_task_groups()
        await self._sync_parent_task_states()

    async def _activate_sequential_task_groups(self) -> None:
        """Promote only the next eligible task in each sequential group."""
        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (t:Task)
                WHERE t.execution_mode = 'sequential'
                  AND t.parent_task_id IS NOT NULL
                RETURN DISTINCT t.parent_task_id AS parent_task_id
            """)
            parent_task_ids = [
                record["parent_task_id"]
                async for record in result
                if record["parent_task_id"]
            ]

            for parent_task_id in parent_task_ids:
                tasks_result = await session.run("""
                    MATCH (t:Task {execution_mode: 'sequential', parent_task_id: $parent_task_id})
                    RETURN t.id AS id,
                           t.lifecycle AS lifecycle,
                           t.execution_order AS execution_order
                    ORDER BY coalesce(t.execution_order, 0) ASC, t.id ASC
                """, parent_task_id=parent_task_id)
                tasks = [dict(record) async for record in tasks_result]
                next_task_id = _next_sequential_task_id(tasks)
                if not next_task_id:
                    continue

                await session.run("""
                    MATCH (t:Task {id: $task_id})
                    WHERE t.lifecycle = 'pending'
                    SET t.lifecycle = 'ready'
                """, task_id=next_task_id)

    async def _activate_parallel_task_groups(self) -> None:
        """Promote pending tasks in parallel groups to ready together."""
        fail_fast = bool(self.config.get("parallel_fail_fast", True))
        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (t:Task)
                WHERE t.execution_mode = 'parallel'
                  AND t.parent_task_id IS NOT NULL
                RETURN DISTINCT t.parent_task_id AS parent_task_id
            """)
            parent_task_ids = [
                record["parent_task_id"]
                async for record in result
                if record["parent_task_id"]
            ]

            for parent_task_id in parent_task_ids:
                states_result = await session.run("""
                    MATCH (t:Task {execution_mode: 'parallel', parent_task_id: $parent_task_id})
                    RETURN t.lifecycle AS lifecycle
                """, parent_task_id=parent_task_id)
                lifecycles = [
                    record["lifecycle"]
                    async for record in states_result
                    if record["lifecycle"]
                ]
                if not _parallel_group_should_activate(lifecycles, fail_fast=fail_fast):
                    continue

                await session.run("""
                    MATCH (t:Task {execution_mode: 'parallel', parent_task_id: $parent_task_id})
                    WHERE t.lifecycle = 'pending'
                    SET t.lifecycle = 'ready'
                """, parent_task_id=parent_task_id)

    async def _sync_parent_task_states(self) -> None:
        """Sync parent task status from grouped child task state."""
        fail_fast = bool(self.config.get("parallel_fail_fast", True))
        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (t:Task)
                WHERE t.parent_task_id IS NOT NULL
                RETURN DISTINCT t.parent_task_id AS parent_task_id
            """)
            parent_task_ids = [
                record["parent_task_id"]
                async for record in result
                if record["parent_task_id"]
            ]

            for parent_task_id in parent_task_ids:
                states_result = await session.run("""
                    MATCH (t:Task {parent_task_id: $parent_task_id})
                    RETURN t.lifecycle AS lifecycle
                    ORDER BY coalesce(t.execution_order, 0) ASC, t.id ASC
                """, parent_task_id=parent_task_id)
                lifecycles = [
                    record["lifecycle"]
                    async for record in states_result
                    if record["lifecycle"]
                ]
                parent_lifecycle = _parent_group_lifecycle(
                    lifecycles,
                    fail_fast=fail_fast,
                )
                if not parent_lifecycle:
                    continue

                await session.run("""
                    MATCH (parent:Task {id: $parent_task_id})
                    WHERE parent.lifecycle <> $lifecycle
                    SET parent.lifecycle = $lifecycle,
                        parent.completed_at = datetime({timezone: 'UTC'})
                """, parent_task_id=parent_task_id, lifecycle=parent_lifecycle)

    # ── Sweep ──

    async def _sweep(self) -> None:
        """Periodic maintenance: decay circuit breakers, evaluate rules."""
        decay = self.config.get("circuit_breaker_decay_per_sweep", 1)
        async with self.driver.session() as session:
            # Decay circuit breaker counters
            await session.run("""
                MATCH (a:Agent)
                WHERE a.restart_count_1h > 0
                SET a.restart_count_1h = CASE
                    WHEN a.restart_count_1h - $decay < 0 THEN 0
                    ELSE a.restart_count_1h - $decay
                END
            """, decay=decay)

        log.info(f"Sweep complete (tick {self.tick_count})")

    # ── Worker Management ──

    def _reap_workers(self) -> None:
        """Remove finished workers from active set."""
        done = [k for k, t in self.active_workers.items() if t.done()]
        for k in done:
            task = self.active_workers.pop(k)
            if task.exception():
                log.error(f"Worker {k} raised: {task.exception()}")

    # ── OpenClaw Bridge ──

    async def _init_bridge(self) -> None:
        """Initialize the OpenClaw Gateway bridge (optional).

        Reads gateway URL and token from DaemonConfig or env vars.
        If not configured, the Daemon works without OpenClaw integration.
        """
        gateway_url = os.environ.get(
            "OPENCLAW_GATEWAY_URL",
            self.config.get("openclaw_gateway_url", ""),
        )
        gateway_token = os.environ.get(
            "OPENCLAW_GATEWAY_TOKEN",
            self.config.get("openclaw_gateway_token", ""),
        )
        notify_target = os.environ.get(
            "HASSALEH_NOTIFY_TARGET",
            self.config.get("notify_target", ""),
        )
        notify_channel = os.environ.get(
            "HASSALEH_NOTIFY_CHANNEL",
            self.config.get("notify_channel", "telegram"),
        )

        if not gateway_url or not gateway_token:
            log.info("OpenClaw bridge not configured (set OPENCLAW_GATEWAY_URL + OPENCLAW_GATEWAY_TOKEN)")
            return

        self._bridge = OpenClawBridge(gateway_url, gateway_token)
        await self._bridge.__aenter__()

        # Test connectivity
        if await self._bridge.is_healthy():
            log.info(f"OpenClaw bridge connected: {gateway_url}")
        else:
            log.warning(f"OpenClaw bridge configured but unreachable: {gateway_url}")

        # Set up notification dispatcher
        if notify_target:
            self._notifier = NotificationDispatcher(
                bridge=self._bridge,
                default_channel=notify_channel,
                default_target=notify_target,
            )
            log.info(f"Notifications → {notify_channel}:{notify_target}")
        else:
            log.info("Notification target not configured (set HASSALEH_NOTIFY_TARGET)")

    # ── Rule Engine ──

    async def _load_rules(self) -> None:
        """Load and compile all available Rule nodes from the graph."""
        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (r:Rule {lifecycle: 'available'})
                RETURN r.id AS id, r.rule_text AS rule_text,
                       r.compiled_python AS compiled_python,
                       r.compiler_version AS compiler_version,
                       r.priority AS priority
            """)
            rules = [dict(record) async for record in result]

        if not rules:
            log.info("No rules found in graph")
            return

        compiled_count = 0
        for rule in rules:
            rule_id = rule["id"]
            rule_text = rule.get("rule_text")
            cached_python = rule.get("compiled_python")
            cached_version = rule.get("compiler_version")

            if not rule_text:
                log.warning(f"Rule {rule_id} has no rule_text, skipping")
                continue

            # Use cached compilation if version matches
            if cached_python and cached_version == COMPILER_VERSION:
                python_source = cached_python
                log.info(f"Rule {rule_id}: using cached compilation")
            else:
                # Compile from source
                try:
                    python_source = compile_rule(rule_text, rule_id=rule_id)
                except Exception as e:
                    log.error(f"Rule {rule_id}: compilation failed: {e}")
                    continue

                # Cache the compiled Python back to the graph
                async with self.driver.session() as session:
                    await session.run("""
                        MATCH (r:Rule {id: $id})
                        SET r.compiled_python = $python,
                            r.compiled_at = datetime({timezone: 'UTC'}),
                            r.compiler_version = $version
                    """, id=rule_id, python=python_source, version=COMPILER_VERSION)
                log.info(f"Rule {rule_id}: compiled and cached (v{COMPILER_VERSION})")

            # Compile to Python code object with restricted namespace
            try:
                code = compile(python_source, f"<rule:{rule_id}>", "exec")
                # Restrict builtins — NO __import__ (prevents loading
                # arbitrary modules). RuleContext is pre-injected.
                safe_builtins = {
                    "True": True, "False": False, "None": None,
                    "abs": abs, "min": min, "max": max, "len": len,
                    "int": int, "float": float, "str": str, "bool": bool,
                    "round": round, "isinstance": isinstance,
                    "range": range, "enumerate": enumerate, "sorted": sorted,
                    "list": list, "print": print,
                }
                ns: dict[str, Any] = {
                    "__builtins__": safe_builtins,
                    "RuleContext": RuleContext,  # Pre-injected, no import needed
                }
                exec(code, ns)
                self._compiled_rules[rule_id] = {
                    "func": ns["evaluate"],
                    "priority": rule.get("priority", 100),
                }
                compiled_count += 1
            except Exception as e:
                log.error(f"Rule {rule_id}: exec failed: {e}")

        log.info(f"Loaded {compiled_count}/{len(rules)} rules")

    def _evaluate_rules(self) -> None:
        """Evaluate all compiled rules (runs in thread executor).

        Uses a sync Neo4j session since rules call ctx.match() synchronously.
        """
        now = datetime.now(timezone.utc)
        all_property_intents = []

        with self.sync_driver.session() as session:
            for rule_id, rule_data in self._compiled_rules.items():
                try:
                    ctx = RuleContext(
                        neo4j_session=session,
                        rule_id=rule_id,
                        priority=rule_data.get("priority", 100),
                        now=now,
                        last_run=self._rule_last_run.get(rule_id),
                    )

                    rule_data["func"](ctx)

                    # Collect outputs
                    all_property_intents.extend(ctx.property_intents)

                    # Process submit_intent actions (create Intent nodes)
                    for si in ctx.submit_intents:
                        self._create_rule_intent(session, si, rule_id)

                    # Accumulate alerts and error logs for async dispatch
                    self._pending_alerts.extend(ctx.alerts)
                    for l in ctx.logs:
                        if l.level == "error":
                            self._pending_error_logs.append(l)

                    # Update last run time
                    self._rule_last_run[rule_id] = now

                except Exception as e:
                    log.error(f"Rule {rule_id} evaluation failed: {e}", exc_info=True)

        # Resolve conflicting property intents
        if all_property_intents:
            # Fetch current values from the graph for ADD/SUB/MUL operations
            current_values = self._fetch_current_values(session, all_property_intents)
            self._apply_resolved_intents(all_property_intents, current_values)

    def _create_rule_intent(self, session, submit_action, rule_id: str) -> None:
        """Create an Intent node from a rule's SUBMIT_INTENT action.

        Creates the Intent node, links it to the target Agent via [:PROPOSED]
        (required for the Daemon's _process_pending_intents query), and
        links it to the Capability via [:TARGETS] when applicable.
        """
        import uuid
        intent_id = str(uuid.uuid4())

        try:
            target_id = None
            if isinstance(submit_action.target_node, dict):
                target_id = submit_action.target_node.get("id")

            args_json = json.dumps(submit_action.args) if submit_action.args else None
            action = "assign_task" if submit_action.capability_id == "assign-task" else "execute_capability"

            # Create Intent + PROPOSED link to the target agent in one query
            if target_id:
                session.run("""
                    MATCH (agent:Agent {id: $target_id})
                    CREATE (agent)-[:PROPOSED]->(i:Intent {
                        id: $intent_id,
                        submitted_at: datetime({timezone: 'UTC'}),
                        action: $action,
                        value: $args,
                        lifecycle: 'pending',
                        source: 'rule',
                        source_rule: $rule_id
                    })
                """, intent_id=intent_id, args=args_json,
                   rule_id=rule_id, target_id=target_id, action=action)
            else:
                # No target — create orphaned Intent (will need manual linking)
                session.run("""
                    CREATE (i:Intent {
                        id: $intent_id,
                        submitted_at: datetime({timezone: 'UTC'}),
                        action: $action,
                        value: $args,
                        lifecycle: 'pending',
                        source: 'rule',
                        source_rule: $rule_id
                    })
                """, intent_id=intent_id, args=args_json, rule_id=rule_id, action=action)

            # Link to capability via TARGETS
            if action == "execute_capability" and submit_action.capability_id:
                session.run("""
                    MATCH (i:Intent {id: $intent_id})
                    MATCH (cap:Capability {id: $cap_id})
                    CREATE (i)-[:TARGETS]->(cap)
                """, intent_id=intent_id, cap_id=submit_action.capability_id)

            log.info(f"Rule {rule_id} created Intent {intent_id} "
                     f"(capability: {submit_action.capability_id}, "
                     f"target: {target_id})")

        except Exception as e:
            log.error(f"Failed to create rule Intent: {e}")

    def _fetch_current_values(self, session, intents) -> dict:
        """Fetch current property values from the graph for resolver base values.

        Uses batched queries grouped by property name to avoid N+1 overhead.
        Neo4j doesn't support parameterized property keys, so we group by
        property and issue one query per unique property name.
        """
        current = {}
        # Collect unique (node_id, property) pairs
        keys = set()
        for intent in intents:
            keys.add((intent.node_id, intent.property))

        # Group by property name for batched queries
        from collections import defaultdict
        by_prop: dict[str, list[str]] = defaultdict(list)
        for node_id, prop in keys:
            if prop.isidentifier():
                by_prop[prop].append(node_id)

        for prop, node_ids in by_prop.items():
            try:
                # Separate element_ids from string ids
                elem_ids = [nid for nid in node_ids if nid.startswith("4:")]
                str_ids = [nid for nid in node_ids if not nid.startswith("4:")]

                if elem_ids:
                    result = session.run(
                        f"UNWIND $ids AS nid "
                        f"MATCH (n) WHERE elementId(n) = nid "
                        f"RETURN elementId(n) AS id, n.{prop} AS val",
                        ids=elem_ids,
                    )
                    for record in result:
                        if record["val"] is not None:
                            current[(record["id"], prop)] = record["val"]

                if str_ids:
                    result = session.run(
                        f"UNWIND $ids AS nid "
                        f"MATCH (n {{id: nid}}) "
                        f"RETURN n.id AS id, n.{prop} AS val",
                        ids=str_ids,
                    )
                    for record in result:
                        if record["val"] is not None:
                            current[(record["id"], prop)] = record["val"]

            except Exception as e:
                log.warning(f"Batch fetch for property '{prop}' failed: {e}")

        return current

    def _apply_resolved_intents(self, intents, current_values=None) -> None:
        """Apply resolved property intents to the graph.

        Note: Neo4j does not support parameterized property keys in SET,
        so we use f-string for the property name. The property name comes
        from compiled GSL-Ops rules (validated by Lark grammar, only
        PROP_ACCESS pattern: /[a-zA-Z_]\\w*\\.[a-zA-Z_]\\w*/).
        """
        resolved = resolve_intents(intents, current_values or {})

        with self.sync_driver.session() as session:
            for (node_id, prop), value in resolved.items():
                try:
                    # Validate prop name (defense-in-depth)
                    if not prop.isidentifier():
                        log.error(f"Invalid property name '{prop}', skipping")
                        continue

                    # Use element_id if available, otherwise match by id property
                    if node_id.startswith("4:"):  # Neo4j element_id format
                        session.run(
                            f"MATCH (n) WHERE elementId(n) = $nid SET n.{prop} = $value",
                            nid=node_id, value=value,
                        )
                    else:
                        session.run(
                            f"MATCH (n {{id: $nid}}) SET n.{prop} = $value",
                            nid=node_id, value=value,
                        )
                except Exception as e:
                    log.error(f"Failed to apply property {prop}={value} "
                              f"on node {node_id}: {e}")

        if resolved:
            log.info(f"Applied {len(resolved)} resolved property change(s)")

    # ── Watchdog (sd_notify) ──

    def _init_watchdog(self) -> None:
        """Initialize systemd watchdog if WATCHDOG_USEC is set."""
        usec = os.environ.get("WATCHDOG_USEC")
        if usec:
            self._watchdog_usec = int(usec)
            log.info(f"Watchdog enabled: {self._watchdog_usec / 1_000_000:.1f}s interval")
        else:
            log.info("Watchdog not configured (no WATCHDOG_USEC)")

    def _sd_notify(self, state: str) -> None:
        """Send notification to systemd via NOTIFY_SOCKET.

        Implements the sd_notify protocol without requiring the systemd Python package.
        """
        addr = os.environ.get("NOTIFY_SOCKET")
        if not addr:
            return

        try:
            if addr.startswith("@"):
                addr = "\0" + addr[1:]  # abstract socket

            sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            try:
                sock.sendto(state.encode(), addr)
            finally:
                sock.close()
        except Exception as e:
            log.warning(f"sd_notify failed: {e}")

    # ── Health Endpoint ──

    async def _start_health_endpoint(self) -> None:
        """Start minimal HTTP health endpoint."""
        port = self.config.get("health_endpoint_port", 9100)
        self._health_app = web.Application()
        self._health_app.router.add_get("/health", self._health_handler)

        self._health_runner = web.AppRunner(self._health_app)
        await self._health_runner.setup()
        site = web.TCPSite(self._health_runner, "127.0.0.1", port)
        await site.start()
        log.info(f"Health endpoint listening on http://127.0.0.1:{port}/health")

    async def _health_handler(self, request: web.Request) -> web.Response:
        """Handle GET /health."""
        uptime = asyncio.get_event_loop().time() - self._start_time if self._start_time else 0

        # Count agents and pending intents
        agents_running = 0
        pending_intents = 0
        try:
            async with self.driver.session() as session:
                result = await session.run(
                    "MATCH (a:Agent {lifecycle: 'running'}) RETURN count(a) AS c"
                )
                record = await result.single()
                agents_running = record["c"] if record else 0

                result = await session.run(
                    "MATCH (i:Intent {lifecycle: 'pending'}) RETURN count(i) AS c"
                )
                record = await result.single()
                pending_intents = record["c"] if record else 0
        except Exception as e:
            log.warning(f"Health check DB query failed: {e}")

        data = {
            "status": "healthy" if self.running else "shutting_down",
            "uptime_seconds": round(uptime),
            "tick_count": self.tick_count,
            "pending_intents": pending_intents,
            "active_workers": len(self.active_workers),
            "agents_running": agents_running,
            "rules_loaded": len(self._compiled_rules),
            "openclaw_bridge": "connected" if self._bridge else "not configured",
            "notifications": "enabled" if self._notifier else "disabled",
            "schema_version": "1.2",
        }
        return web.json_response(data)

    async def _stop_health_endpoint(self) -> None:
        """Stop the health endpoint."""
        if self._health_runner:
            await self._health_runner.cleanup()
            log.info("Health endpoint stopped")

    # ── Config Loading ──

    async def _load_config(self) -> None:
        """Load DaemonConfig from Neo4j."""
        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (dc:DaemonConfig {id: 'default'})
                RETURN dc
            """)
            record = await result.single()
            if record:
                node = record["dc"]
                for key in DEFAULT_CONFIG:
                    if key in node:
                        self.config[key] = node[key]
                log.info(f"Config loaded from graph: {self.config}")
            else:
                log.warning("No DaemonConfig found — using defaults")

    async def _check_schema_version(self) -> None:
        """Verify schema version compatibility."""
        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (sv:SystemVersion {id: 'hassaleh'})
                RETURN sv.schema_version AS version,
                       sv.compatible_daemon_versions AS compat
            """)
            record = await result.single()
            if record:
                log.info(f"Schema version: {record['version']}")
            else:
                log.warning("No SystemVersion found — skipping version check")


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

async def main():
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7690")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "hassaleh-dev-2026")

    daemon = HassalehDaemon(uri, user, password)

    # Signal handlers: set the shutdown event (lightweight, no double-shutdown)
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, daemon._shutdown_event.set)

    try:
        await daemon.start()
    except KeyboardInterrupt:
        pass
    finally:
        if daemon.running:
            await daemon.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
```


### `src/hassaleh/sdk.py`
```py
#!/usr/bin/env python3.13
"""Hassaleh Agent SDK — Read-only graph access + Intent submission.

Provides a guarded interface for agents to:
- Query the Neo4j graph (read-only, parameterized, timeout-enforced)
- Submit Intents for the Daemon to process
- Poll Intent results (lifecycle, stdout, stderr)
- Convenience methods for common queries

Reference: docs/CONCEPT.md v1.2, Section 3.3
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from neo4j import AsyncGraphDatabase

log = logging.getLogger("hassaleh.sdk")

# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────

DEFAULT_QUERY_CONFIG = {
    "default_timeout_ms": 3000,
    "max_result_rows": 10000,
    "report_query_timeout_ms": 30000,
}

# Write operations that agents must NOT execute via query()
# Agents write ONLY through submit_intent() → Daemon processes
BLOCKED_KEYWORDS = frozenset({
    "CREATE", "MERGE", "DELETE", "DETACH", "SET", "REMOVE",
    "DROP", "CALL", "LOAD CSV", "FOREACH",
})

# Allowed node labels for Intent TARGETS edges (prevents Cypher injection)
ALLOWED_TARGET_LABELS = frozenset({
    "Agent", "Task", "Capability", "Workspace", "Project", "Sprint",
    "DaemonConfig", "QueryConfig", "SystemVersion",
    "Message", "Discussion", "SkillDomain",
})


class HassalehSDK:
    """Agent-facing SDK for Hassaleh graph access.

    Usage:
        async with HassalehSDK() as sdk:
            tasks = await sdk.my_tasks("dione")
            intent_id = await sdk.submit_intent(...)
            result = await sdk.poll_intent(intent_id)
    """

    def __init__(
        self,
        neo4j_uri: str | None = None,
        neo4j_user: str | None = None,
        neo4j_password: str | None = None,
    ):
        self.neo4j_uri = neo4j_uri or os.environ.get("NEO4J_URI", "bolt://localhost:7690")
        self.neo4j_user = neo4j_user or os.environ.get("NEO4J_USER", "neo4j")
        self.neo4j_password = neo4j_password or os.environ.get("NEO4J_PASSWORD", "hassaleh-dev-2026")
        self.driver: Any = None
        self.query_config: dict = dict(DEFAULT_QUERY_CONFIG)

    async def __aenter__(self) -> HassalehSDK:
        await self.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    # ── Lifecycle ──

    async def connect(self) -> None:
        """Connect to Neo4j and load query config."""
        self.driver = AsyncGraphDatabase.driver(
            self.neo4j_uri,
            auth=(self.neo4j_user, self.neo4j_password),
        )
        # Verify connection
        async with self.driver.session() as session:
            result = await session.run("RETURN 1 AS ping")
            record = await result.single()
            assert record and record["ping"] == 1

        # Load query config from graph
        await self._load_query_config()
        log.info(f"SDK connected to {self.neo4j_uri}")

    async def close(self) -> None:
        """Close the Neo4j connection."""
        if self.driver:
            await self.driver.close()
            self.driver = None
            log.info("SDK disconnected")

    async def _load_query_config(self) -> None:
        """Load QueryConfig from the graph."""
        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (qc:QueryConfig {id: 'default'})
                RETURN qc.default_timeout_ms AS default_timeout_ms,
                       qc.max_result_rows AS max_result_rows,
                       qc.report_query_timeout_ms AS report_query_timeout_ms
            """)
            record = await result.single()
            if record:
                for key in DEFAULT_QUERY_CONFIG:
                    val = record.get(key)
                    if val is not None:
                        self.query_config[key] = val
                log.info(f"QueryConfig loaded: {self.query_config}")
            else:
                log.warning("No QueryConfig found — using defaults")

    # ── Core Query ──

    async def query(
        self,
        cypher: str,
        params: dict[str, Any] | None = None,
        timeout_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a parameterized read-only query.

        Args:
            cypher: Parameterized Cypher query (no string concatenation!)
            params: Query parameters
            timeout_ms: Per-query timeout (defaults to QueryConfig value)

        Returns:
            List of result records as dicts
        """
        if params is None:
            params = {}

        # Enforce read-only: reject queries with write keywords (word-boundary match)
        cypher_upper = cypher.upper()
        for keyword in BLOCKED_KEYWORDS:
            if re.search(rf'\b{keyword}\b', cypher_upper):
                raise PermissionError(
                    f"Write operation '{keyword}' blocked in SDK query(). "
                    f"Use submit_intent() for state changes."
                )

        timeout_ms = timeout_ms or self.query_config["default_timeout_ms"]
        max_rows = self.query_config["max_result_rows"]

        # Enforce result limit if not already in query
        if "LIMIT" not in cypher.upper():
            cypher = cypher.rstrip().rstrip(";") + f"\nLIMIT {max_rows}"

        timeout_sec = timeout_ms / 1000.0

        try:
            async with self.driver.session() as session:
                result = await asyncio.wait_for(
                    session.run(cypher, params),
                    timeout=timeout_sec,
                )
                records = []
                async for record in result:
                    records.append(dict(record))
                    if len(records) >= max_rows:
                        break
                return records

        except asyncio.TimeoutError:
            log.error(f"Query timed out after {timeout_ms}ms")
            raise TimeoutError(f"Query exceeded {timeout_ms}ms timeout")

    # ── Intent Submission ──

    async def submit_intent(
        self,
        agent_id: str,
        action: str,
        capability_id: str | None = None,
        target_id: str | None = None,
        target_label: str | None = None,
        property_name: str | None = None,
        value: str | None = None,
    ) -> str:
        """Submit an Intent for the Daemon to process.

        Args:
            agent_id: ID of the submitting agent
            action: Intent action (execute_capability, update_property, etc.)
            capability_id: Target capability ID (for execute_capability)
            target_id: Target node ID (for update_property)
            target_label: Target node label (for update_property)
            property_name: Property to update
            value: Value to set or args JSON

        Returns:
            The generated Intent ID
        """
        intent_id = str(uuid.uuid4())

        # Validate label early (before any writes)
        if action == "update_property" and target_label:
            if target_label not in ALLOWED_TARGET_LABELS:
                raise ValueError(
                    f"Invalid target label '{target_label}'. "
                    f"Allowed: {sorted(ALLOWED_TARGET_LABELS)}"
                )

        async with self.driver.session() as session:
            # Use a single write transaction so Intent + TARGETS are atomic
            async def _create_intent(tx):
                # Create Intent node + PROPOSED edge
                await tx.run("""
                    MATCH (agent:Agent {id: $agent_id})
                    CREATE (i:Intent {
                        id: $intent_id,
                        submitted_at: datetime({timezone: 'UTC'}),
                        action: $action,
                        property: $property_name,
                        value: $value,
                        lifecycle: 'pending',
                        started_at: null,
                        completed_at: null,
                        stdout: null,
                        stderr: null,
                        error_reason: null,
                        exit_code: null
                    })
                    CREATE (agent)-[:PROPOSED]->(i)
                """,
                    agent_id=agent_id,
                    intent_id=intent_id,
                    action=action,
                    property_name=property_name,
                    value=value,
                )

                # Create TARGETS edge to capability or target node
                if action == "execute_capability" and capability_id:
                    await tx.run("""
                        MATCH (i:Intent {id: $intent_id})
                        MATCH (cap:Capability {id: $cap_id})
                        CREATE (i)-[:TARGETS]->(cap)
                    """, intent_id=intent_id, cap_id=capability_id)

                elif action == "update_property" and target_id and target_label:
                    await tx.run(f"""
                        MATCH (i:Intent {{id: $intent_id}})
                        MATCH (target:{target_label} {{id: $target_id}})
                        CREATE (i)-[:TARGETS]->(target)
                    """, intent_id=intent_id, target_id=target_id)

            await session.execute_write(_create_intent)

        log.info(f"Submitted Intent {intent_id} (action: {action}, agent: {agent_id})")
        return intent_id

    # ── Intent Polling ──

    async def poll_intent(self, intent_id: str) -> dict[str, Any]:
        """Poll an Intent for its current state.

        Returns:
            Dict with lifecycle, stdout, stderr, error_reason, exit_code
        """
        records = await self.query("""
            MATCH (i:Intent {id: $id})
            RETURN i.lifecycle AS lifecycle,
                   i.stdout AS stdout,
                   i.stderr AS stderr,
                   i.error_reason AS error_reason,
                   i.exit_code AS exit_code,
                   i.submitted_at AS submitted_at,
                   i.completed_at AS completed_at
        """, {"id": intent_id})

        if not records:
            raise ValueError(f"Intent {intent_id} not found")
        return records[0]

    async def wait_for_intent(
        self,
        intent_id: str,
        timeout_sec: float = 60.0,
        poll_interval: float = 1.0,
    ) -> dict[str, Any]:
        """Wait for an Intent to reach a terminal state.

        Args:
            intent_id: Intent to wait for
            timeout_sec: Maximum wait time
            poll_interval: Seconds between polls

        Returns:
            Final Intent state dict

        Raises:
            TimeoutError: If Intent doesn't complete within timeout
        """
        deadline = asyncio.get_event_loop().time() + timeout_sec

        while asyncio.get_event_loop().time() < deadline:
            state = await self.poll_intent(intent_id)
            if state["lifecycle"] in ("success", "failed", "rejected"):
                return state
            await asyncio.sleep(poll_interval)

        raise TimeoutError(
            f"Intent {intent_id} did not complete within {timeout_sec}s"
        )

    # ── Convenience Methods ──

    async def my_tasks(self, agent_id: str) -> list[dict[str, Any]]:
        """Get all tasks assigned to an agent.

        Returns:
            List of task dicts with id, name, lifecycle, description
        """
        return await self.query("""
            MATCH (t:Task)-[:ASSIGNED_TO]->(a:Agent {id: $agent_id})
            RETURN t.id AS id,
                   t.name AS name,
                   t.lifecycle AS lifecycle,
                   t.description AS description,
                   t.expires_at AS expires_at
            ORDER BY t.lifecycle ASC, t.expires_at ASC
        """, {"agent_id": agent_id})

    async def project_status(self, project_id: str) -> dict[str, Any]:
        """Get project status overview.

        Returns:
            Dict with project info, sprint info, task counts
        """
        records = await self.query("""
            MATCH (p:Project {id: $project_id})
            OPTIONAL MATCH (p)-[:HAS_SPRINT]->(s:Sprint)
            OPTIONAL MATCH (s)-[:HAS_TASK]->(t:Task)
            RETURN p.name AS project_name,
                   p.lifecycle AS project_lifecycle,
                   s.name AS sprint_name,
                   s.lifecycle AS sprint_lifecycle,
                   count(t) AS total_tasks,
                   sum(CASE WHEN t.lifecycle = 'success' THEN 1 ELSE 0 END) AS done_tasks,
                   sum(CASE WHEN t.lifecycle = 'running' THEN 1 ELSE 0 END) AS running_tasks,
                   sum(CASE WHEN t.lifecycle = 'pending' THEN 1 ELSE 0 END) AS pending_tasks
        """, {"project_id": project_id})

        return records[0] if records else {}

    async def task_group_status(self, parent_task_id: str) -> list[dict[str, Any]]:
        """Get ordered status for all tasks in a task group."""
        return await self.query("""
            MATCH (t:Task {parent_task_id: $parent_task_id})
            RETURN t.id AS id,
                   t.name AS name,
                   t.lifecycle AS lifecycle,
                   t.execution_mode AS execution_mode,
                   t.execution_order AS execution_order,
                   t.parent_task_id AS parent_task_id
            ORDER BY coalesce(t.execution_order, 0) ASC, t.id ASC
        """, {"parent_task_id": parent_task_id})

    async def list_domains(self) -> list[dict[str, Any]]:
        """Return all skill domains in hierarchy order."""
        return await self.query("""
            MATCH (sd:SkillDomain)
            RETURN sd.id AS id,
                   sd.display_name AS display_name,
                   sd.description AS description,
                   sd.parent_domain AS parent_domain
            ORDER BY sd.id ASC
        """)

    async def capabilities_by_domain(self, domain_prefix: str) -> list[dict[str, Any]]:
        """Return capabilities whose domain matches a prefix."""
        normalized_prefix = str(domain_prefix).strip().strip(".")
        if not normalized_prefix:
            return await self.query("""
                MATCH (c:Capability)
                RETURN c.id AS id,
                       c.name AS name,
                       c.kind AS kind,
                       c.description AS description,
                       c.domain AS domain,
                       c.lifecycle AS lifecycle
                ORDER BY c.domain ASC, c.name ASC, c.id ASC
            """)

        return await self.query("""
            MATCH (c:Capability)
            WHERE c.domain = $domain_prefix
               OR c.domain STARTS WITH $nested_prefix
            RETURN c.id AS id,
                   c.name AS name,
                   c.kind AS kind,
                   c.description AS description,
                   c.domain AS domain,
                   c.lifecycle AS lifecycle
            ORDER BY c.domain ASC, c.name ASC, c.id ASC
        """, {
            "domain_prefix": normalized_prefix,
            "nested_prefix": f"{normalized_prefix}.",
        })

    async def domain_info(self, domain_id: str) -> dict[str, Any] | None:
        """Return a single domain with capability counts."""
        records = await self.query("""
            MATCH (sd:SkillDomain {id: $domain_id})
            OPTIONAL MATCH (c:Capability)-[:IN_DOMAIN]->(sd)
            RETURN sd.id AS id,
                   sd.display_name AS display_name,
                   sd.description AS description,
                   sd.parent_domain AS parent_domain,
                   count(c) AS capability_count
        """, {"domain_id": domain_id})
        return records[0] if records else None

    async def check_domain_permission(self, agent_id: str, capability_id: str) -> bool:
        """Return True when the agent's workspace allows the capability domain."""
        records = await self.query("""
            MATCH (a:Agent {id: $agent_id})
            OPTIONAL MATCH (a)-[:OPERATES_IN]->(ws:Workspace)
            MATCH (cap:Capability {id: $capability_id})
            RETURN ws.allowed_domains AS allowed_domains,
                   cap.domain AS capability_domain
        """, {
            "agent_id": agent_id,
            "capability_id": capability_id,
        })
        if not records:
            return False

        allowed_domains = records[0].get("allowed_domains")
        capability_domain = records[0].get("capability_domain")
        if not allowed_domains:
            return True
        if not capability_domain:
            return True
        normalized_domain = str(capability_domain).strip().strip(".")
        for domain_prefix in allowed_domains:
            normalized_prefix = str(domain_prefix).strip().strip(".")
            if not normalized_prefix:
                return True
            if (
                normalized_domain == normalized_prefix
                or normalized_domain.startswith(f"{normalized_prefix}.")
            ):
                return True
        return False

    async def review_task(
        self,
        task_id: str,
        agent_id: str,
        approved: bool,
        comment: str = "",
    ) -> None:
        """Submit a supervised task review intent."""
        intent_id = str(uuid.uuid4())
        payload = json.dumps({
            "approved": approved,
            "comment": comment,
        })

        async with self.driver.session() as session:
            async def _create_review_intent(tx):
                await tx.run("""
                    MATCH (agent:Agent {id: $agent_id})
                    CREATE (i:Intent {
                        id: $intent_id,
                        submitted_at: datetime({timezone: 'UTC'}),
                        action: 'review_task',
                        value: $payload,
                        lifecycle: 'pending',
                        started_at: null,
                        completed_at: null,
                        stdout: null,
                        stderr: null,
                        error_reason: null,
                        exit_code: null
                    })
                    CREATE (agent)-[:PROPOSED]->(i)
                """, agent_id=agent_id, intent_id=intent_id, payload=payload)
                await tx.run("""
                    MATCH (i:Intent {id: $intent_id})
                    MATCH (t:Task {id: $task_id})
                    CREATE (i)-[:TARGETS]->(t)
                """, intent_id=intent_id, task_id=task_id)

            await session.execute_write(_create_review_intent)

        log.info("Submitted review_task Intent %s for task %s", intent_id, task_id)

    # ── Messaging (Cursor-based) ──

    async def send_message(
        self,
        agent_id: str,
        content: str,
        context_id: str | None = None,
        context_label: str = "Task",
    ) -> str:
        """Send a message to a context (Task, Discussion, etc.).

        Creates a Message node, links it via SENT from the agent,
        and appends it to the NEXT linked-list for the context.

        Args:
            agent_id: Sending agent's ID
            content: Message content
            context_id: Optional context node ID (Task, Discussion)
            context_label: Label of the context node

        Returns:
            The generated Message ID
        """
        message_id = str(uuid.uuid4())

        async with self.driver.session() as session:
            async def _create_message(tx):
                # Create message + SENT edge
                await tx.run("""
                    MATCH (agent:Agent {id: $agent_id})
                    CREATE (m:Message {
                        id: $message_id,
                        timestamp: datetime({timezone: 'UTC'}),
                        content: $content
                    })
                    CREATE (agent)-[:SENT]->(m)
                """, agent_id=agent_id, message_id=message_id, content=content)

                # Link to context if provided
                if context_id and context_label:
                    if context_label not in ALLOWED_TARGET_LABELS:
                        return
                    await tx.run(f"""
                        MATCH (m:Message {{id: $message_id}})
                        MATCH (ctx:{context_label} {{id: $context_id}})
                        CREATE (m)-[:IN_CONTEXT_OF]->(ctx)
                    """, message_id=message_id, context_id=context_id)

                # Maintain HEAD_OF / TAIL_OF + NEXT chain for the context
                if context_id:
                    # Check if context already has a TAIL
                    result = await tx.run("""
                        MATCH (ctx {id: $context_id})
                        OPTIONAL MATCH (ctx)<-[:TAIL_OF]-(tail:Message)
                        RETURN tail.id AS tail_id, ctx IS NOT NULL AS ctx_exists
                    """, context_id=context_id)
                    record = await result.single()

                    if record and record["tail_id"]:
                        # Append: link old tail → new message, update TAIL_OF
                        await tx.run("""
                            MATCH (old_tail:Message {id: $tail_id})
                            MATCH (m:Message {id: $message_id})
                            MATCH (ctx {id: $context_id})<-[old_te:TAIL_OF]-(old_tail)
                            DELETE old_te
                            CREATE (old_tail)-[:NEXT]->(m)
                            CREATE (m)-[:TAIL_OF]->(ctx)
                        """, tail_id=record["tail_id"],
                           message_id=message_id, context_id=context_id)
                    else:
                        # First message: set as both HEAD and TAIL
                        await tx.run("""
                            MATCH (m:Message {id: $message_id})
                            MATCH (ctx {id: $context_id})
                            CREATE (m)-[:HEAD_OF]->(ctx)
                            CREATE (m)-[:TAIL_OF]->(ctx)
                        """, message_id=message_id, context_id=context_id)

            await session.execute_write(_create_message)

        log.info(f"Message {message_id} sent by {agent_id}")
        return message_id

    async def read_messages(
        self,
        agent_id: str,
        context_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Read unread messages for an agent (cursor-based).

        Follows the NEXT chain from the agent's LAST_READ cursor.
        If no cursor exists, starts from the HEAD of the context.

        Returns:
            List of message dicts with id, content, timestamp, sender
        """
        if context_id:
            # Two-step read: check cursor, then traverse NEXT chain
            # Step 1: Get cursor position
            cursor_records = await self.query("""
                MATCH (agent:Agent {id: $agent_id})
                OPTIONAL MATCH (agent)-[:LAST_READ]->(cursor:Message)
                RETURN cursor.id AS cursor_id
            """, {"agent_id": agent_id}, timeout_ms=5000)

            cursor_id = cursor_records[0]["cursor_id"] if cursor_records else None

            if cursor_id:
                # Follow NEXT chain from cursor
                return await self.query("""
                    MATCH (cursor:Message {id: $cursor_id})-[:NEXT*1..]->(m:Message)
                    MATCH (sender:Agent)-[:SENT]->(m)
                    RETURN m.id AS id, m.content AS content,
                           m.timestamp AS timestamp, sender.id AS sender_id,
                           sender.name AS sender_name
                    ORDER BY m.timestamp ASC
                """, {"cursor_id": cursor_id}, timeout_ms=10000)
            else:
                # No cursor: start from HEAD of context
                return await self.query("""
                    MATCH (head:Message)-[:HEAD_OF]->({id: $context_id})
                    MATCH path = (head)-[:NEXT*0..]->(m:Message)
                    MATCH (sender:Agent)-[:SENT]->(m)
                    RETURN m.id AS id, m.content AS content,
                           m.timestamp AS timestamp, sender.id AS sender_id,
                           sender.name AS sender_name
                    ORDER BY m.timestamp ASC
                """, {"context_id": context_id}, timeout_ms=10000)
        else:
            # Fallback: timestamp-based for cross-context reads
            return await self.query("""
                MATCH (agent:Agent {id: $agent_id})
                OPTIONAL MATCH (agent)-[:LAST_READ]->(cursor:Message)
                WITH agent, cursor
                MATCH (sender:Agent)-[:SENT]->(m:Message)
                WHERE cursor IS NULL OR m.timestamp > cursor.timestamp
                RETURN m.id AS id, m.content AS content,
                       m.timestamp AS timestamp, sender.id AS sender_id,
                       sender.name AS sender_name
                ORDER BY m.timestamp ASC
            """, {"agent_id": agent_id}, timeout_ms=10000)

    async def advance_cursor(self, agent_id: str, message_id: str) -> None:
        """Move an agent's LAST_READ cursor to a specific message.

        Deletes the old LAST_READ edge and creates a new one.
        """
        async with self.driver.session() as session:
            async def _advance(tx):
                await tx.run("""
                    MATCH (agent:Agent {id: $agent_id})
                    OPTIONAL MATCH (agent)-[old:LAST_READ]->()
                    DELETE old
                    WITH agent
                    MATCH (m:Message {id: $message_id})
                    CREATE (agent)-[:LAST_READ]->(m)
                """, agent_id=agent_id, message_id=message_id)
            await session.execute_write(_advance)

    # ── Discussions ──

    async def create_discussion(
        self,
        topic: str,
        context_id: str | None = None,
        context_label: str = "Project",
    ) -> str:
        """Create a new discussion for collaborative decision-making."""
        discussion_id = str(uuid.uuid4())

        async with self.driver.session() as session:
            async def _create(tx):
                await tx.run("""
                    CREATE (d:Discussion {
                        id: $id,
                        topic: $topic,
                        lifecycle: 'pending',
                        created_at: datetime({timezone: 'UTC'})
                    })
                """, id=discussion_id, topic=topic)

                if context_id and context_label in ALLOWED_TARGET_LABELS:
                    await tx.run(f"""
                        MATCH (d:Discussion {{id: $id}})
                        MATCH (ctx:{context_label} {{id: $context_id}})
                        CREATE (d)-[:IN_CONTEXT_OF]->(ctx)
                    """, id=discussion_id, context_id=context_id)

            await session.execute_write(_create)
        return discussion_id

    async def contribute_to_discussion(
        self,
        agent_id: str,
        discussion_id: str,
        position: str,
        reasoning: str,
    ) -> None:
        """Add an agent's position to a discussion."""
        async with self.driver.session() as session:
            async def _contribute(tx):
                await tx.run("""
                    MATCH (a:Agent {id: $agent_id})
                    MATCH (d:Discussion {id: $discussion_id})
                    CREATE (a)-[:CONTRIBUTED {
                        position: $position,
                        reasoning: $reasoning,
                        timestamp: datetime({timezone: 'UTC'})
                    }]->(d)
                    SET d.lifecycle = 'running'
                """, agent_id=agent_id, discussion_id=discussion_id,
                   position=position, reasoning=reasoning)
            await session.execute_write(_contribute)

    async def resolve_discussion(
        self,
        agent_id: str,
        discussion_id: str,
        resolution: str,
    ) -> None:
        """Resolve a discussion (leader decision).

        Only agents who have CONTRIBUTED to the discussion can resolve it.
        """
        async with self.driver.session() as session:
            async def _resolve(tx):
                # Verify agent has contributed (permission check)
                result = await tx.run("""
                    MATCH (a:Agent {id: $agent_id})-[:CONTRIBUTED]->(d:Discussion {id: $did})
                    RETURN count(*) AS has_contributed
                """, agent_id=agent_id, did=discussion_id)
                record = await result.single()
                if not record or record["has_contributed"] == 0:
                    raise PermissionError(
                        f"Agent '{agent_id}' has not contributed to discussion "
                        f"'{discussion_id}' and cannot resolve it"
                    )

                await tx.run("""
                    MATCH (a:Agent {id: $agent_id})
                    MATCH (d:Discussion {id: $discussion_id})
                    SET d.lifecycle = 'success',
                        d.resolution = $resolution,
                        d.resolved_at = datetime({timezone: 'UTC'})
                    CREATE (d)-[:DECIDED_BY]->(a)
                """, agent_id=agent_id, discussion_id=discussion_id,
                   resolution=resolution)
            await session.execute_write(_resolve)

    async def agent_info(self, agent_id: str) -> dict[str, Any] | None:
        """Get agent details."""
        records = await self.query("""
            MATCH (a:Agent {id: $agent_id})
            RETURN a.id AS id,
                   a.name AS name,
                   a.emoji AS emoji,
                   a.lifecycle AS lifecycle,
                   a.last_heartbeat AS last_heartbeat,
                   a.runtime AS runtime
        """, {"agent_id": agent_id})

        return records[0] if records else None
```


### `src/hassaleh/cli.py`
```py
#!/usr/bin/env python3.13
"""Hassaleh CLI — Command-line interface for operators and admins.

Usage:
    hassaleh status          Show Daemon health, agents, rules, intents
    hassaleh init            Initialize Neo4j schema + seed data
    hassaleh agent list      List all agents
    hassaleh agent info ID   Show agent details
    hassaleh rule list       List all rules
    hassaleh rule compile ID Force-recompile a rule, show generated Python
    hassaleh intent list     List recent intents
    hassaleh approve ID      Approve an awaiting_approval intent

Reference: docs/CONCEPT.md v1.2
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Suppress Neo4j "property does not exist" warnings (noisy but harmless)
logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)

from neo4j import GraphDatabase

from hassaleh.cli_fmt import (
    fmt_table, fmt_header, fmt_ok, fmt_warn, fmt_error,
    fmt_kv, fmt_section, fmt_code,
)
from hassaleh.domain import (
    format_domain_hierarchy,
)


# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────

DEFAULT_URI = "bolt://localhost:7690"
DEFAULT_USER = "neo4j"
DEFAULT_PASSWORD = ""
HEALTH_URL = "http://127.0.0.1:9100/health"
PROJECT_DIR = Path(__file__).parent.parent.parent  # hassaleh project root


def get_connection(args) -> dict:
    """Resolve Neo4j connection params from args → env → config → defaults."""
    uri = args.uri or os.environ.get("NEO4J_URI", DEFAULT_URI)
    user = args.user or os.environ.get("NEO4J_USER", DEFAULT_USER)
    password = args.password or os.environ.get("NEO4J_PASSWORD", DEFAULT_PASSWORD)

    # Try config file
    if not password:
        config_path = Path.home() / ".hassaleh" / "config.toml"
        if config_path.exists():
            try:
                import tomllib
                with open(config_path, "rb") as f:
                    cfg = tomllib.load(f)
                uri = uri or cfg.get("neo4j", {}).get("uri", DEFAULT_URI)
                user = user or cfg.get("neo4j", {}).get("user", DEFAULT_USER)
                password = password or cfg.get("neo4j", {}).get("password", "")
            except Exception:
                pass

    return {"uri": uri, "user": user, "password": password}


def connect(conn: dict):
    """Create a Neo4j driver and verify connectivity."""
    driver = GraphDatabase.driver(conn["uri"], auth=(conn["user"], conn["password"]))
    driver.verify_connectivity()
    return driver


def _score_skill_match(query: str, capability: dict[str, Any]) -> tuple[int, str]:
    """Return a sortable relevance tuple for skill search."""
    query_lc = query.casefold()
    name = str(capability.get("name", "")).casefold()
    description = str(capability.get("description", "")).casefold()
    cap_id = str(capability.get("id", ""))

    if not query_lc:
        return (0, cap_id)
    if name == query_lc:
        return (0, cap_id)
    if query_lc == cap_id.casefold():
        return (1, cap_id)
    if description == query_lc:
        return (2, cap_id)
    if query_lc in name:
        return (3, cap_id)
    if query_lc in description:
        return (4, cap_id)
    return (5, cap_id)


def _build_domain_tree(domain_rows: list[dict[str, Any]]) -> list[str]:
    """Build a tree-like domain listing with aggregated capability counts."""
    if not domain_rows:
        return ["(empty)"]

    by_parent: dict[str | None, list[dict[str, Any]]] = {}
    exact_counts = {
        row["id"]: int(row.get("exact_count", 0) or 0)
        for row in domain_rows
    }
    row_by_id = {row["id"]: row for row in domain_rows}

    for row in domain_rows:
        by_parent.setdefault(row.get("parent_domain"), []).append(row)

    def aggregate_count(domain_id: str) -> int:
        total = exact_counts.get(domain_id, 0)
        for child in by_parent.get(domain_id, []):
            total += aggregate_count(child["id"])
        return total

    lines: list[str] = []

    def walk(parent: str | None, lineage: list[bool]) -> None:
        children = sorted(by_parent.get(parent, []), key=lambda item: item["id"])
        for index, row in enumerate(children):
            is_last = index == len(children) - 1
            prefix = "".join("    " if last else "│   " for last in lineage)
            branch = "" if parent is None else ("└── " if is_last else "├── ")
            label = row["id"] if parent is None else row["id"].split(".")[-1]
            lines.append(f"{prefix}{branch}{label} ({aggregate_count(row['id'])})")
            walk(row["id"], [*lineage, is_last])

    root_nodes = [
        row for row in domain_rows
        if row.get("parent_domain") is None or row.get("parent_domain") not in row_by_id
    ]
    for row in sorted(root_nodes, key=lambda item: item["id"]):
        lines.append(f"{row['id']} ({aggregate_count(row['id'])})")
        walk(row["id"], [])

    return lines


def _build_skill_tree(
    domain_rows: list[dict[str, Any]],
    capabilities: list[dict[str, Any]],
) -> list[str]:
    """Build a domain tree with capabilities as leaf nodes."""
    if not capabilities:
        return ["(empty)"]

    by_parent: dict[str | None, list[dict[str, Any]]] = {}
    row_by_id = {row["id"]: row for row in domain_rows}
    capabilities_by_domain: dict[str, list[dict[str, Any]]] = {}

    for row in domain_rows:
        by_parent.setdefault(row.get("parent_domain"), []).append(row)

    for capability in capabilities:
        domain_id = capability.get("domain") or "unscoped"
        capabilities_by_domain.setdefault(domain_id, []).append(capability)

    def domain_has_visible_content(domain_id: str) -> bool:
        if capabilities_by_domain.get(domain_id):
            return True
        return any(domain_has_visible_content(child["id"]) for child in by_parent.get(domain_id, []))

    lines: list[str] = []

    def walk(domain_id: str, lineage: list[bool]) -> None:
        children = [
            child for child in sorted(by_parent.get(domain_id, []), key=lambda item: item["id"])
            if domain_has_visible_content(child["id"])
        ]
        skills = sorted(
            capabilities_by_domain.get(domain_id, []),
            key=lambda capability: (capability.get("name", ""), capability["id"]),
        )
        total_children = len(children) + len(skills)

        for index, child in enumerate(children):
            is_last = index == total_children - 1 and not skills
            prefix = "".join("    " if last else "│   " for last in lineage)
            branch = "└── " if is_last else "├── "
            label = child["id"].split(".")[-1]
            lines.append(f"{prefix}{branch}{label}")
            walk(child["id"], [*lineage, is_last])

        for skill_index, capability in enumerate(skills):
            is_last = (len(children) + skill_index) == total_children - 1
            prefix = "".join("    " if last else "│   " for last in lineage)
            branch = "└── " if is_last else "├── "
            lines.append(
                f"{prefix}{branch}{capability['id']} [{capability.get('kind', '?')}] "
                f"{capability.get('name', '')}"
            )

    root_nodes = [
        row for row in domain_rows
        if row.get("parent_domain") is None or row.get("parent_domain") not in row_by_id
    ]
    visible_roots = [
        row for row in sorted(root_nodes, key=lambda item: item["id"])
        if domain_has_visible_content(row["id"])
    ]

    for index, row in enumerate(visible_roots):
        lines.append(row["id"])
        walk(row["id"], [])

    if capabilities_by_domain.get("unscoped"):
        if lines:
            lines.append("unscoped")
        for capability in sorted(capabilities_by_domain["unscoped"], key=lambda item: item["id"]):
            lines.append(f"└── {capability['id']} [{capability.get('kind', '?')}] {capability.get('name', '')}")

    return lines


# ──────────────────────────────────────────────
# Commands
# ──────────────────────────────────────────────

def cmd_status(args) -> int:
    """Show Daemon health, agents, rules, pending intents."""
    # 1. Daemon health (via HTTP)
    fmt_header("Hassaleh Status")

    try:
        import urllib.request
        with urllib.request.urlopen(HEALTH_URL, timeout=3) as resp:
            health = json.loads(resp.read())
        print(fmt_section("Daemon"))
        print(fmt_kv("Status", fmt_ok(health["status"])))
        print(fmt_kv("Uptime", f"{health['uptime_seconds']}s"))
        print(fmt_kv("Ticks", str(health["tick_count"])))
        print(fmt_kv("Active Workers", str(health["active_workers"])))
        print(fmt_kv("Schema", health.get("schema_version", "?")))
        print(fmt_kv("Rules loaded", str(health.get("rules_loaded", "?"))))
        print(fmt_kv("OpenClaw Bridge", health.get("openclaw_bridge", "?")))
        print(fmt_kv("Notifications", health.get("notifications", "?")))
    except Exception as e:
        print(fmt_section("Daemon"))
        print(fmt_kv("Status", fmt_error("UNREACHABLE")))
        print(fmt_kv("Error", str(e)))

    # 2. Neo4j connection
    conn = get_connection(args)
    try:
        driver = connect(conn)
    except Exception as e:
        print(fmt_section("Neo4j"))
        print(fmt_kv("Status", fmt_error(f"Connection failed: {e}")))
        return 1

    with driver.session() as session:
        # 3. Agents
        result = session.run("""
            MATCH (a:Agent)
            RETURN a.id AS id, a.name AS name, a.lifecycle AS lifecycle,
                   a.last_heartbeat AS hb
            ORDER BY a.name
        """)
        agents = [dict(r) for r in result]

        print(fmt_section("Agents"))
        if agents:
            rows = [[a["id"], a.get("name", ""), a.get("lifecycle", "?"),
                      str(a.get("hb", "—"))] for a in agents]
            print(fmt_table(["ID", "Name", "Lifecycle", "Last Heartbeat"], rows))
        else:
            print("  No agents found")

        # 4. Rules
        result = session.run("""
            MATCH (r:Rule)
            RETURN r.id AS id, r.name AS name, r.lifecycle AS lifecycle,
                   r.priority AS priority, r.compiler_version AS cv
            ORDER BY r.priority ASC
        """)
        rules = [dict(r) for r in result]

        print(fmt_section("Rules"))
        if rules:
            rows = [[r["id"], r.get("name", ""), r.get("lifecycle", "?"),
                      str(r.get("priority", "?")), r.get("cv", "—")] for r in rules]
            print(fmt_table(["ID", "Name", "Lifecycle", "Priority", "Compiler"], rows))
        else:
            print("  No rules found")

        # 5. Pending Intents
        result = session.run("""
            MATCH (i:Intent)
            WHERE i.lifecycle IN ['pending', 'claimed', 'running', 'awaiting_approval']
            RETURN i.id AS id, i.lifecycle AS lifecycle, i.action AS action,
                   i.submitted_at AS submitted, i.source AS source
            ORDER BY i.submitted_at DESC
            LIMIT 10
        """)
        intents = [dict(r) for r in result]

        print(fmt_section("Active Intents"))
        if intents:
            rows = [[i["id"][:12] + "…", i.get("lifecycle", "?"),
                      i.get("action", "?"), str(i.get("submitted", "?")),
                      i.get("source", "agent")] for i in intents]
            print(fmt_table(["ID", "Lifecycle", "Action", "Submitted", "Source"], rows))
        else:
            print("  No active intents")

    driver.close()
    return 0


def cmd_init(args) -> int:
    """Initialize Neo4j schema + seed data."""
    fmt_header("Hassaleh Init")
    conn = get_connection(args)

    try:
        driver = connect(conn)
    except Exception as e:
        print(fmt_error(f"Connection failed: {e}"))
        return 1

    files = [
        ("Schema", PROJECT_DIR / "schema.cypher"),
        ("Seed Data", PROJECT_DIR / "seed.cypher"),
        ("Seed Rules", PROJECT_DIR / "seed_rules.cypher"),
    ]

    errors = 0
    with driver.session() as session:
        for label, path in files:
            if not path.exists():
                print(fmt_warn(f"{label}: {path} not found, skipping"))
                continue

            cypher = path.read_text()
            # Split on semicolons and execute each statement
            statements = [s.strip() for s in cypher.split(";") if s.strip()]
            file_errors = 0
            for stmt in statements:
                # Skip comments-only blocks
                lines = [l for l in stmt.split("\n") if l.strip() and not l.strip().startswith("//")]
                if not lines:
                    continue
                try:
                    session.run(stmt)
                except Exception as e:
                    print(fmt_warn(f"{label}: {e}"))
                    file_errors += 1

            if file_errors:
                print(fmt_warn(f"{label}: {file_errors} error(s) in {len(statements)} statements"))
                errors += file_errors
            else:
                print(fmt_ok(f"{label}: applied ({len(statements)} statements)"))

    driver.close()
    if errors:
        print(fmt_error(f"Init completed with {errors} error(s)"))
        return 1
    print(fmt_ok("Init complete"))
    return 0


def cmd_agent_list(args) -> int:
    """List all agents."""
    conn = get_connection(args)
    driver = connect(conn)

    with driver.session() as session:
        result = session.run("""
            MATCH (a:Agent)
            OPTIONAL MATCH (a)-[:HAS_CAPABILITY]->(c:Capability)
            RETURN a.id AS id, a.name AS name, a.emoji AS emoji,
                   a.lifecycle AS lifecycle, a.last_heartbeat AS hb,
                   a.runtime AS runtime, count(c) AS caps
            ORDER BY a.name
        """)
        agents = [dict(r) for r in result]

    driver.close()

    if not agents:
        print("No agents found")
        return 0

    fmt_header("Agents")
    rows = [[
        a.get("emoji", "") + " " + a.get("name", a["id"]),
        a["id"], a.get("lifecycle", "?"), str(a["caps"]),
        a.get("runtime", "—"), str(a.get("hb", "—"))
    ] for a in agents]
    print(fmt_table(["Name", "ID", "Lifecycle", "Caps", "Runtime", "Last HB"], rows))
    return 0


def cmd_agent_info(args) -> int:
    """Show detailed agent info."""
    conn = get_connection(args)
    driver = connect(conn)

    with driver.session() as session:
        # Agent details
        result = session.run("""
            MATCH (a:Agent {id: $id})
            RETURN a
        """, id=args.agent_id)
        record = result.single()

        if not record:
            print(fmt_error(f"Agent '{args.agent_id}' not found"))
            driver.close()
            return 1

        agent = dict(record["a"])
        fmt_header(f"Agent: {agent.get('name', agent.get('id'))}")
        for k, v in sorted(agent.items()):
            print(fmt_kv(k, str(v)))

        # Capabilities
        result = session.run("""
            MATCH (a:Agent {id: $id})-[:HAS_CAPABILITY]->(c:Capability)
            RETURN c.id AS id, c.name AS name, c.kind AS kind
            ORDER BY c.name
        """, id=args.agent_id)
        caps = [dict(r) for r in result]

        if caps:
            print(fmt_section("Capabilities"))
            rows = [[c["id"], c.get("name", ""), c.get("kind", "?")] for c in caps]
            print(fmt_table(["ID", "Name", "Kind"], rows))

        # Recent Intents
        result = session.run("""
            MATCH (a:Agent {id: $id})-[:PROPOSED]->(i:Intent)
            RETURN i.id AS id, i.lifecycle AS lifecycle, i.action AS action,
                   i.submitted_at AS submitted
            ORDER BY i.submitted_at DESC
            LIMIT 5
        """, id=args.agent_id)
        intents = [dict(r) for r in result]

        if intents:
            print(fmt_section("Recent Intents"))
            rows = [[i["id"][:12] + "…", i.get("lifecycle", "?"),
                      i.get("action", "?"), str(i.get("submitted", "?"))] for i in intents]
            print(fmt_table(["ID", "Lifecycle", "Action", "Submitted"], rows))

    driver.close()
    return 0


def cmd_rule_list(args) -> int:
    """List all rules."""
    conn = get_connection(args)
    driver = connect(conn)

    with driver.session() as session:
        result = session.run("""
            MATCH (r:Rule)
            RETURN r.id AS id, r.name AS name, r.lifecycle AS lifecycle,
                   r.priority AS priority, r.compiler_version AS cv,
                   r.compiled_at AS compiled_at, r.author AS author
            ORDER BY r.priority ASC
        """)
        rules = [dict(r) for r in result]

    driver.close()

    if not rules:
        print("No rules found")
        return 0

    fmt_header("Rules")
    rows = [[
        r["id"], r.get("name", ""), r.get("lifecycle", "?"),
        str(r.get("priority", "?")), r.get("author", "—"),
        r.get("cv", "—"), str(r.get("compiled_at", "—"))
    ] for r in rules]
    print(fmt_table(["ID", "Name", "Lifecycle", "Priority", "Author", "Compiler", "Compiled"], rows))
    return 0


def cmd_rule_compile(args) -> int:
    """Force-recompile a rule and show generated Python."""
    from hassaleh.engine.compiler import compile_rule, COMPILER_VERSION

    conn = get_connection(args)
    driver = connect(conn)

    with driver.session() as session:
        result = session.run("""
            MATCH (r:Rule {id: $id})
            RETURN r.rule_text AS rule_text, r.name AS name
        """, id=args.rule_id)
        record = result.single()

        if not record:
            print(fmt_error(f"Rule '{args.rule_id}' not found"))
            driver.close()
            return 1

        rule_text = record["rule_text"]
        if not rule_text:
            print(fmt_error(f"Rule '{args.rule_id}' has no rule_text"))
            driver.close()
            return 1

        fmt_header(f"Compiling: {record.get('name', args.rule_id)}")

        print(fmt_section("GSL-Ops Source"))
        print(fmt_code(rule_text))

        try:
            python_source = compile_rule(rule_text, rule_id=args.rule_id)
        except Exception as e:
            print(fmt_error(f"Compilation failed: {e}"))
            driver.close()
            return 1

        print(fmt_section(f"Generated Python (compiler v{COMPILER_VERSION})"))
        print(fmt_code(python_source))

        # Update cache in graph
        if not args.dry_run:
            session.run("""
                MATCH (r:Rule {id: $id})
                SET r.compiled_python = $python,
                    r.compiled_at = datetime({timezone: 'UTC'}),
                    r.compiler_version = $version
            """, id=args.rule_id, python=python_source, version=COMPILER_VERSION)
            print(fmt_ok("Compiled and cached in graph"))
        else:
            print(fmt_warn("Dry run — not saved to graph"))

    driver.close()
    return 0


def cmd_intent_list(args) -> int:
    """List recent intents."""
    conn = get_connection(args)
    driver = connect(conn)

    with driver.session() as session:
        filters = []
        params: dict[str, Any] = {"limit": args.limit or 20}

        if args.lifecycle:
            filters.append("i.lifecycle = $lifecycle")
            params["lifecycle"] = args.lifecycle
        if args.source:
            filters.append("i.source = $source")
            params["source"] = args.source

        where = "WHERE " + " AND ".join(filters) if filters else ""

        result = session.run(f"""
            MATCH (i:Intent)
            {where}
            RETURN i.id AS id, i.lifecycle AS lifecycle, i.action AS action,
                   i.submitted_at AS submitted, i.completed_at AS completed,
                   i.source AS source, i.exit_code AS exit_code,
                   i.error_reason AS error
            ORDER BY i.submitted_at DESC
            LIMIT $limit
        """, **params)
        intents = [dict(r) for r in result]

    driver.close()

    if not intents:
        print("No intents found")
        return 0

    fmt_header("Intents")
    rows = [[
        i["id"][:12] + "…", i.get("lifecycle", "?"), i.get("action", "?"),
        str(i.get("submitted", "?")), i.get("source", "agent"),
        str(i.get("exit_code", "—")), (i.get("error", "") or "")[:40]
    ] for i in intents]
    print(fmt_table(["ID", "Status", "Action", "Submitted", "Source", "Exit", "Error"], rows))
    return 0


def cmd_skill_list(args) -> int:
    """List all capabilities grouped by skill domain."""
    conn = get_connection(args)
    driver = connect(conn)
    domain_prefix = (args.domain or "").strip().strip(".")
    nested_prefix = f"{domain_prefix}." if domain_prefix else ""

    with driver.session() as session:
        domain_result = session.run("""
            MATCH (sd:SkillDomain)
            RETURN sd.id AS id,
                   sd.display_name AS display_name,
                   sd.description AS description,
                   sd.parent_domain AS parent_domain
            ORDER BY sd.id
        """)
        domains = [dict(r) for r in domain_result]

        result = session.run("""
            MATCH (c:Capability)
            WHERE $domain_prefix = ''
               OR c.domain = $domain_prefix
               OR c.domain STARTS WITH $nested_prefix
            RETURN c.id AS id,
                   c.name AS name,
                   c.domain AS domain,
                   c.kind AS kind,
                   c.lifecycle AS lifecycle
            ORDER BY c.name, c.id
        """, domain_prefix=domain_prefix, nested_prefix=nested_prefix)
        capabilities = [dict(r) for r in result]

    driver.close()

    if not capabilities:
        print("No skills found")
        return 0

    fmt_header("Skills")
    for line in _build_skill_tree(domains, capabilities):
        print(line)
    return 0


def cmd_skill_search(args) -> int:
    """Search capabilities by name and description."""
    conn = get_connection(args)
    driver = connect(conn)
    query_lc = args.query.casefold()

    with driver.session() as session:
        result = session.run("""
            MATCH (c:Capability)
            WHERE toLower(coalesce(c.name, '')) CONTAINS $query
               OR toLower(coalesce(c.description, '')) CONTAINS $query
               OR toLower(coalesce(c.id, '')) CONTAINS $query
            RETURN c.id AS id,
                   c.name AS name,
                   c.domain AS domain,
                   c.kind AS kind,
                   c.lifecycle AS lifecycle,
                   c.description AS description
        """, query=query_lc)
        capabilities = [dict(r) for r in result]

    driver.close()

    capabilities.sort(key=lambda cap: _score_skill_match(args.query, cap))

    if not capabilities:
        print("No skills found")
        return 0

    fmt_header(f"Skill Search: {args.query}")
    rows = [[
        cap["id"],
        cap.get("name", ""),
        cap.get("domain", "—"),
        cap.get("kind", "?"),
        (cap.get("description", "") or "")[:50],
    ] for cap in capabilities]
    print(fmt_table(["ID", "Name", "Domain", "Kind", "Description"], rows))
    return 0


def cmd_skill_info(args) -> int:
    """Show detailed information for a single capability."""
    conn = get_connection(args)
    driver = connect(conn)

    with driver.session() as session:
        result = session.run("""
            MATCH (c:Capability {id: $id})
            OPTIONAL MATCH (c)-[:IN_DOMAIN]->(sd:SkillDomain)
            RETURN c AS capability,
                   sd.id AS skill_domain_id,
                   sd.display_name AS skill_domain_name,
                   sd.description AS skill_domain_description
        """, id=args.skill_id)
        record = result.single()

        if not record or not record["capability"]:
            print(fmt_error(f"Skill '{args.skill_id}' not found"))
            driver.close()
            return 1

        capability = dict(record["capability"])

        agents_result = session.run("""
            MATCH (a:Agent)-[:HAS_CAPABILITY]->(c:Capability {id: $id})
            RETURN a.id AS id, a.name AS name, a.lifecycle AS lifecycle
            ORDER BY a.name, a.id
        """, id=args.skill_id)
        agents = [dict(r) for r in agents_result]

    driver.close()

    fmt_header(f"Skill: {capability.get('name', capability.get('id'))}")
    for key, value in sorted(capability.items()):
        print(fmt_kv(key, str(value)))

    print(fmt_section("Domain"))
    print(fmt_kv("Hierarchy", format_domain_hierarchy(capability.get("domain"))))
    if record.get("skill_domain_id"):
        print(fmt_kv("Node ID", record["skill_domain_id"]))
    if record.get("skill_domain_name"):
        print(fmt_kv("Node Name", record["skill_domain_name"]))
    if record.get("skill_domain_description"):
        print(fmt_kv("Description", record["skill_domain_description"]))

    if agents:
        print(fmt_section("Agents"))
        rows = [[
            agent["id"],
            agent.get("name", ""),
            agent.get("lifecycle", "?"),
        ] for agent in agents]
        print(fmt_table(["ID", "Name", "Lifecycle"], rows))

    return 0


def cmd_domain_list(args) -> int:
    """List the skill domain hierarchy with capability counts."""
    conn = get_connection(args)
    driver = connect(conn)

    with driver.session() as session:
        result = session.run("""
            MATCH (sd:SkillDomain)
            OPTIONAL MATCH (c:Capability)-[:IN_DOMAIN]->(sd)
            RETURN sd.id AS id,
                   sd.display_name AS display_name,
                   sd.parent_domain AS parent_domain,
                   count(c) AS exact_count
            ORDER BY sd.id
        """)
        domains = [dict(r) for r in result]

    driver.close()

    fmt_header("Domains")
    for line in _build_domain_tree(domains):
        print(line)
    return 0


def cmd_domain_info(args) -> int:
    """Show a domain with its capabilities."""
    conn = get_connection(args)
    driver = connect(conn)
    domain_id = args.domain_id.strip().strip(".")
    nested_prefix = f"{domain_id}."

    with driver.session() as session:
        result = session.run("""
            MATCH (sd:SkillDomain {id: $domain_id})
            OPTIONAL MATCH (parent:SkillDomain {id: sd.parent_domain})
            RETURN sd.id AS id,
                   sd.display_name AS display_name,
                   sd.description AS description,
                   sd.parent_domain AS parent_domain,
                   parent.display_name AS parent_display_name
        """, domain_id=domain_id)
        domain = result.single()
        if not domain:
            print(fmt_error(f"Domain '{args.domain_id}' not found"))
            driver.close()
            return 1

        caps_result = session.run("""
            MATCH (c:Capability)
            WHERE c.domain = $domain_id
               OR c.domain STARTS WITH $nested_prefix
            RETURN c.id AS id,
                   c.name AS name,
                   c.kind AS kind,
                   c.domain AS domain,
                   c.description AS description,
                   c.lifecycle AS lifecycle
            ORDER BY c.domain ASC, c.name ASC, c.id ASC
        """, domain_id=domain_id, nested_prefix=nested_prefix)
        capabilities = [dict(r) for r in caps_result]

    driver.close()

    fmt_header(f"Domain: {domain['id']}")
    print(fmt_kv("Display Name", domain.get("display_name", domain["id"])))
    print(fmt_kv("Description", domain.get("description", "")))
    print(fmt_kv("Hierarchy", format_domain_hierarchy(domain["id"])))
    print(fmt_kv("Parent", domain.get("parent_domain") or "root"))

    print(fmt_section("Capabilities"))
    if not capabilities:
        print("  No capabilities found")
        return 0

    rows = [[
        capability["id"],
        capability.get("name", ""),
        capability.get("domain", "—"),
        capability.get("kind", "?"),
        capability.get("lifecycle", "?"),
    ] for capability in capabilities]
    print(fmt_table(["ID", "Name", "Domain", "Kind", "Lifecycle"], rows))
    return 0


# ──────────────────────────────────────────────
# Message + Discussion Commands
# ──────────────────────────────────────────────

def cmd_message_list(args) -> int:
    """List messages in a context."""
    conn = get_connection(args)
    driver = connect(conn)

    with driver.session() as session:
        if args.context:
            result = session.run("""
                MATCH (sender:Agent)-[:SENT]->(m:Message)-[:IN_CONTEXT_OF]->(ctx {id: $ctx})
                RETURN m.id AS id, m.content AS content, m.timestamp AS ts,
                       sender.name AS sender
                ORDER BY m.timestamp ASC
                LIMIT $limit
            """, ctx=args.context, limit=args.limit)
        else:
            result = session.run("""
                MATCH (sender:Agent)-[:SENT]->(m:Message)
                RETURN m.id AS id, m.content AS content, m.timestamp AS ts,
                       sender.name AS sender
                ORDER BY m.timestamp DESC
                LIMIT $limit
            """, limit=args.limit)

        messages = [dict(r) for r in result]

    driver.close()

    if not messages:
        print("No messages found")
        return 0

    fmt_header("Messages")
    rows = [[
        m.get("sender", "?"),
        (m.get("content", "") or "")[:60],
        str(m.get("ts", "?"))
    ] for m in messages]
    print(fmt_table(["Sender", "Content", "Timestamp"], rows))
    return 0


def cmd_discussion_list(args) -> int:
    """List discussions."""
    conn = get_connection(args)
    driver = connect(conn)

    with driver.session() as session:
        result = session.run("""
            MATCH (d:Discussion)
            OPTIONAL MATCH (a:Agent)-[:CONTRIBUTED]->(d)
            WITH d, count(a) AS contributors
            RETURN d.id AS id, d.topic AS topic, d.lifecycle AS lifecycle,
                   d.resolution AS resolution, contributors
            ORDER BY d.created_at DESC
        """)
        discussions = [dict(r) for r in result]

    driver.close()

    if not discussions:
        print("No discussions found")
        return 0

    fmt_header("Discussions")
    rows = [[
        d["id"][:12] + "…",
        (d.get("topic", "") or "")[:40],
        d.get("lifecycle", "?"),
        str(d.get("contributors", 0)),
        (d.get("resolution", "") or "—")[:30],
    ] for d in discussions]
    print(fmt_table(["ID", "Topic", "Status", "Contributors", "Resolution"], rows))
    return 0


def cmd_discussion_show(args) -> int:
    """Show discussion details with all contributions."""
    conn = get_connection(args)
    driver = connect(conn)

    with driver.session() as session:
        # Discussion details
        result = session.run("""
            MATCH (d:Discussion {id: $id})
            OPTIONAL MATCH (d)-[:DECIDED_BY]->(leader:Agent)
            RETURN d, leader.name AS decided_by
        """, id=args.discussion_id)
        record = result.single()

        if not record:
            print(fmt_error(f"Discussion '{args.discussion_id}' not found"))
            driver.close()
            return 1

        disc = dict(record["d"])
        fmt_header(f"Discussion: {disc.get('topic', '?')}")
        print(fmt_kv("ID", disc.get("id", "?")))
        print(fmt_kv("Status", disc.get("lifecycle", "?")))
        if disc.get("resolution"):
            print(fmt_kv("Resolution", disc["resolution"]))
        if record.get("decided_by"):
            print(fmt_kv("Decided by", record["decided_by"]))

        # Contributions
        result = session.run("""
            MATCH (a:Agent)-[c:CONTRIBUTED]->(d:Discussion {id: $id})
            RETURN a.name AS agent, c.position AS position,
                   c.reasoning AS reasoning, c.timestamp AS ts
            ORDER BY c.timestamp ASC
        """, id=args.discussion_id)
        contributions = [dict(r) for r in result]

        if contributions:
            print(fmt_section("Contributions"))
            for c in contributions:
                print(fmt_kv(c.get("agent", "?"),
                            f"{c.get('position', '?')} — {c.get('reasoning', '')}"))

    driver.close()
    return 0


# ──────────────────────────────────────────────
# Report Commands
# ──────────────────────────────────────────────

def _report_output(data: dict, fmt: str, title: str) -> None:
    """Output report in requested format."""
    if fmt == "json":
        print(json.dumps(data, indent=2, default=str))
    elif fmt == "markdown":
        print(f"# {title}\n")
        for section, content in data.items():
            print(f"## {section}\n")
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict):
                        print("| " + " | ".join(str(v) for v in item.values()) + " |")
                    else:
                        print(f"- {item}")
            elif isinstance(content, dict):
                for k, v in content.items():
                    print(f"- **{k}:** {v}")
            else:
                print(str(content))
            print()
    else:
        fmt_header(title)
        for section, content in data.items():
            print(fmt_section(section))
            if isinstance(content, dict):
                for k, v in content.items():
                    print(fmt_kv(k, str(v)))
            elif isinstance(content, list) and content:
                if isinstance(content[0], dict):
                    headers = list(content[0].keys())
                    rows = [[str(item.get(h, "")) for h in headers] for item in content]
                    print(fmt_table(headers, rows))
                else:
                    for item in content:
                        print(f"    - {item}")
            else:
                print(f"    {content}")


def cmd_report_agents(args) -> int:
    """Agent activity report."""
    conn = get_connection(args)
    driver = connect(conn)

    with driver.session() as session:
        # Agent overview
        result = session.run("""
            MATCH (a:Agent)
            OPTIONAL MATCH (a)-[:PROPOSED]->(i:Intent)
            WITH a,
                 count(i) AS total_intents,
                 sum(CASE WHEN i.lifecycle = 'success' THEN 1 ELSE 0 END) AS success,
                 sum(CASE WHEN i.lifecycle = 'failed' THEN 1 ELSE 0 END) AS failed,
                 sum(CASE WHEN i.lifecycle = 'rejected' THEN 1 ELSE 0 END) AS rejected
            RETURN a.id AS id, a.name AS name, a.lifecycle AS lifecycle,
                   a.last_heartbeat AS last_hb, a.restart_count_1h AS restarts,
                   total_intents, success, failed, rejected
            ORDER BY a.name
        """)
        agents = [dict(r) for r in result]

    driver.close()

    data = {
        "Agents": [{
            "Name": a.get("name", a["id"]),
            "Lifecycle": a.get("lifecycle", "?"),
            "Heartbeat": str(a.get("last_hb", "—")),
            "Restarts": str(a.get("restarts", 0)),
            "Intents": str(a.get("total_intents", 0)),
            "Success": str(a.get("success", 0)),
            "Failed": str(a.get("failed", 0)),
            "Rejected": str(a.get("rejected", 0)),
        } for a in agents]
    }

    _report_output(data, args.format, "Agent Activity Report")
    return 0


def cmd_report_rules(args) -> int:
    """Rule evaluation report."""
    conn = get_connection(args)
    driver = connect(conn)

    with driver.session() as session:
        # Rules overview
        result = session.run("""
            MATCH (r:Rule)
            RETURN r.id AS id, r.name AS name, r.lifecycle AS lifecycle,
                   r.priority AS priority, r.compiler_version AS cv,
                   r.compiled_at AS compiled_at
            ORDER BY r.priority ASC
        """)
        rules = [dict(r) for r in result]

        # Count rule-generated intents
        result = session.run("""
            MATCH (i:Intent {source: 'rule'})
            RETURN i.source_rule AS rule_id,
                   count(i) AS total,
                   sum(CASE WHEN i.lifecycle = 'success' THEN 1 ELSE 0 END) AS success,
                   sum(CASE WHEN i.lifecycle = 'pending' THEN 1 ELSE 0 END) AS pending
        """)
        intent_stats = {r["rule_id"]: dict(r) for r in result}

    driver.close()

    data = {
        "Rules": [{
            "Name": r.get("name", r["id"]),
            "Priority": str(r.get("priority", "?")),
            "Status": r.get("lifecycle", "?"),
            "Compiler": r.get("cv", "—"),
            "Intents Created": str(intent_stats.get(r["id"], {}).get("total", 0)),
            "Pending": str(intent_stats.get(r["id"], {}).get("pending", 0)),
        } for r in rules]
    }

    _report_output(data, args.format, "Rule Evaluation Report")
    return 0


def cmd_report_intents(args) -> int:
    """Intent statistics report."""
    conn = get_connection(args)
    driver = connect(conn)

    with driver.session() as session:
        # Overall stats
        result = session.run("""
            MATCH (i:Intent)
            RETURN i.lifecycle AS lifecycle, count(i) AS count
            ORDER BY count DESC
        """)
        by_lifecycle = {r["lifecycle"]: r["count"] for r in result}

        # By source
        result = session.run("""
            MATCH (i:Intent)
            RETURN coalesce(i.source, 'agent') AS source, count(i) AS count
        """)
        by_source = {r["source"]: r["count"] for r in result}

        # Recent failures
        result = session.run("""
            MATCH (i:Intent {lifecycle: 'failed'})
            RETURN i.id AS id, i.error_reason AS error, i.source_rule AS rule,
                   i.completed_at AS completed
            ORDER BY i.completed_at DESC
            LIMIT 5
        """)
        failures = [dict(r) for r in result]

    driver.close()

    total = sum(by_lifecycle.values())
    success_rate = f"{by_lifecycle.get('success', 0) / total * 100:.0f}%" if total else "—"

    data = {
        "Summary": {
            "Total Intents": total,
            "Success Rate": success_rate,
        },
        "By Lifecycle": by_lifecycle,
        "By Source": by_source,
        "Recent Failures": [{
            "ID": f["id"][:12] + "…",
            "Error": (f.get("error") or "—")[:50],
            "Rule": f.get("rule", "—"),
        } for f in failures] if failures else "None",
    }

    _report_output(data, args.format, "Intent Statistics")
    return 0


def cmd_report_daily(args) -> int:
    """Combined daily summary."""
    conn = get_connection(args)
    driver = connect(conn)

    with driver.session() as session:
        # Daemon health
        health_data = {}
        try:
            import urllib.request
            with urllib.request.urlopen(HEALTH_URL, timeout=3) as resp:
                health_data = json.loads(resp.read())
        except Exception:
            health_data = {"status": "unreachable"}

        # Agent count
        result = session.run("MATCH (a:Agent) RETURN count(a) AS c")
        agent_count = result.single()["c"]

        # Active agents
        result = session.run("""
            MATCH (a:Agent {lifecycle: 'running'})
            RETURN a.id AS id, a.name AS name
        """)
        active_agents = [f"{r['name'] or r['id']}" for r in result]

        # Rule count
        result = session.run("MATCH (r:Rule {lifecycle: 'available'}) RETURN count(r) AS c")
        rule_count = result.single()["c"]

        # Today's intents
        result = session.run("""
            MATCH (i:Intent)
            WHERE i.submitted_at >= datetime({timezone: 'UTC'}) - duration('P1D')
            RETURN i.lifecycle AS lifecycle, count(i) AS count
        """)
        today_intents = {r["lifecycle"]: r["count"] for r in result}

        # Today's alerts (from rule-generated intents)
        result = session.run("""
            MATCH (i:Intent {source: 'rule'})
            WHERE i.submitted_at >= datetime({timezone: 'UTC'}) - duration('P1D')
            RETURN count(i) AS c
        """)
        today_alerts = result.single()["c"]

    driver.close()

    data = {
        "Daemon": {
            "Status": health_data.get("status", "?"),
            "Uptime": f"{health_data.get('uptime_seconds', '?')}s",
            "Rules Loaded": str(health_data.get("rules_loaded", "?")),
            "Bridge": health_data.get("openclaw_bridge", "?"),
        },
        "Agents": {
            "Total": agent_count,
            "Active": ", ".join(active_agents) if active_agents else "None",
        },
        "Rules": {
            "Available": rule_count,
        },
        "Today's Activity": {
            "Intents (24h)": sum(today_intents.values()),
            "By Status": today_intents if today_intents else "None",
            "Rule-Generated": today_alerts,
        },
    }

    _report_output(data, args.format, f"Daily Summary — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}")
    return 0


def cmd_heartbeat(args) -> int:
    """Send agent heartbeat — updates last_heartbeat + lifecycle in graph."""
    conn = get_connection(args)
    driver = connect(conn)

    with driver.session() as session:
        result = session.run("""
            MATCH (a:Agent {id: $id})
            SET a.last_heartbeat = datetime({timezone: 'UTC'}),
                a.lifecycle = 'running'
            RETURN a.id AS id, a.name AS name
        """, id=args.agent_id)
        record = result.single()

        if not record:
            print(fmt_error(f"Agent '{args.agent_id}' not found"))
            driver.close()
            return 1

        print(fmt_ok(f"Heartbeat: {record.get('name', args.agent_id)} @ UTC now"))

    driver.close()
    return 0


def cmd_approve(args) -> int:
    """Approve an awaiting_approval intent."""
    conn = get_connection(args)
    driver = connect(conn)

    with driver.session() as session:
        result = session.run("""
            MATCH (i:Intent {id: $id})
            RETURN i.lifecycle AS lifecycle
        """, id=args.intent_id)
        record = result.single()

        if not record:
            print(fmt_error(f"Intent '{args.intent_id}' not found"))
            driver.close()
            return 1

        if record["lifecycle"] != "awaiting_approval":
            print(fmt_error(f"Intent is '{record['lifecycle']}', not 'awaiting_approval'"))
            driver.close()
            return 1

        session.run("""
            MATCH (i:Intent {id: $id})
            SET i.lifecycle = 'pending',
                i.approved_at = datetime({timezone: 'UTC'})
        """, id=args.intent_id)

        print(fmt_ok(f"Intent {args.intent_id} approved → pending"))

    driver.close()
    return 0


def cmd_task_review(args) -> int:
    """Approve or reject a supervised task review."""
    conn = get_connection(args)
    driver = connect(conn)

    with driver.session() as session:
        result = session.run("""
            MATCH (t:Task {id: $id})
            OPTIONAL MATCH (t)-[:SUPERVISED_BY]->(lead:Agent)
            RETURN t.execution_mode AS execution_mode,
                   t.lifecycle AS lifecycle,
                   collect(lead.id) AS lead_agent_ids
        """, id=args.task_id)
        record = result.single()

        if not record:
            print(fmt_error(f"Task '{args.task_id}' not found"))
            driver.close()
            return 1

        if record["execution_mode"] != "supervised":
            print(fmt_error(f"Task '{args.task_id}' is not supervised"))
            driver.close()
            return 1

        if record["lifecycle"] != "awaiting_review":
            print(fmt_error(
                f"Task is '{record['lifecycle']}', not 'awaiting_review'"
            ))
            driver.close()
            return 1

        lead_agent_ids = [lead_id for lead_id in record["lead_agent_ids"] if lead_id]
        reviewer_id = args.agent_id
        if not reviewer_id:
            if len(lead_agent_ids) == 1:
                reviewer_id = lead_agent_ids[0]
            else:
                print(fmt_error(
                    "Task review requires --agent-id when multiple or no supervisors are linked"
                ))
                driver.close()
                return 1
        elif reviewer_id not in lead_agent_ids:
            print(fmt_error(f"Agent '{reviewer_id}' is not linked via SUPERVISED_BY"))
            driver.close()
            return 1

        approved = bool(args.approve)
        lifecycle = "success" if approved else "failed"
        session.run("""
            MATCH (t:Task {id: $id})
            SET t.lifecycle = $lifecycle,
                t.completed_at = datetime({timezone: 'UTC'}),
                t.reviewed_at = datetime({timezone: 'UTC'}),
                t.reviewed_by = $reviewer_id,
                t.review_comment = $comment
        """,
            id=args.task_id,
            lifecycle=lifecycle,
            reviewer_id=reviewer_id,
            comment=args.comment,
        )

        print(fmt_ok(
            f"Task {args.task_id} reviewed by {reviewer_id} → {lifecycle}"
        ))

    driver.close()
    return 0


# ──────────────────────────────────────────────
# Argument Parser
# ──────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hassaleh",
        description="Hassaleh — Graph-native agent orchestration CLI",
    )

    # Global connection flags
    parser.add_argument("--uri", help="Neo4j URI (default: bolt://localhost:7690)")
    parser.add_argument("--user", help="Neo4j user (default: neo4j)")
    parser.add_argument("--password", help="Neo4j password")

    sub = parser.add_subparsers(dest="command", help="Available commands")

    # status
    sub.add_parser("status", help="Show system status")

    # init
    sub.add_parser("init", help="Initialize Neo4j schema + seed data")

    # agent
    agent_parser = sub.add_parser("agent", help="Agent management")
    agent_sub = agent_parser.add_subparsers(dest="agent_command")
    agent_sub.add_parser("list", help="List all agents")
    info_parser = agent_sub.add_parser("info", help="Show agent details")
    info_parser.add_argument("agent_id", help="Agent ID")

    # rule
    rule_parser = sub.add_parser("rule", help="Rule management")
    rule_sub = rule_parser.add_subparsers(dest="rule_command")
    rule_sub.add_parser("list", help="List all rules")
    compile_parser = rule_sub.add_parser("compile", help="Recompile a rule")
    compile_parser.add_argument("rule_id", help="Rule ID")
    compile_parser.add_argument("--dry-run", action="store_true", help="Don't save to graph")

    # intent
    intent_parser = sub.add_parser("intent", help="Intent management")
    intent_sub = intent_parser.add_subparsers(dest="intent_command")
    list_parser = intent_sub.add_parser("list", help="List intents")
    list_parser.add_argument("--lifecycle", help="Filter by lifecycle",
                             choices=["pending", "claimed", "running", "success",
                                      "failed", "rejected", "awaiting_approval"])
    list_parser.add_argument("--source", help="Filter by source",
                             choices=["agent", "rule"])
    list_parser.add_argument("--limit", type=int, default=20, help="Max results")

    # skill
    skill_parser = sub.add_parser("skill", help="Skill and capability discovery")
    skill_sub = skill_parser.add_subparsers(dest="skill_command")
    skill_list = skill_sub.add_parser("list", help="List available skills")
    skill_list.add_argument("--domain", help="Filter by domain prefix")
    skill_search = skill_sub.add_parser("search", help="Search skills")
    skill_search.add_argument("query", help="Search query")
    skill_info = skill_sub.add_parser("info", help="Show skill details")
    skill_info.add_argument("skill_id", help="Skill or capability ID")

    # domain
    domain_parser = sub.add_parser("domain", help="Skill domain taxonomy")
    domain_sub = domain_parser.add_subparsers(dest="domain_command")
    domain_sub.add_parser("list", help="List all domains")
    domain_info = domain_sub.add_parser("info", help="Show domain details")
    domain_info.add_argument("domain_id", help="Domain ID")

    # approve
    approve_parser = sub.add_parser("approve", help="Approve an intent")
    approve_parser.add_argument("intent_id", help="Intent ID")

    # task
    task_parser = sub.add_parser("task", help="Task management")
    task_sub = task_parser.add_subparsers(dest="task_command")
    task_review = task_sub.add_parser("review", help="Review a supervised task")
    task_review.add_argument("task_id", help="Task ID")
    task_review_group = task_review.add_mutually_exclusive_group(required=True)
    task_review_group.add_argument("--approve", action="store_true", help="Approve the task")
    task_review_group.add_argument("--reject", action="store_true", help="Reject the task")
    task_review.add_argument("--agent-id", help="Supervising lead agent ID")
    task_review.add_argument("--comment", default="", help="Optional review comment")

    # heartbeat (for agents to report they're alive)
    hb_parser = sub.add_parser("heartbeat", help="Send agent heartbeat to graph")
    hb_parser.add_argument("agent_id", help="Agent ID")

    # message
    msg_parser = sub.add_parser("message", help="Inter-agent messages")
    msg_sub = msg_parser.add_subparsers(dest="message_command")
    msg_list = msg_sub.add_parser("list", help="List messages in a context")
    msg_list.add_argument("--context", help="Context node ID (Task/Discussion)")
    msg_list.add_argument("--limit", type=int, default=20)

    # discussion
    disc_parser = sub.add_parser("discussion", help="Multi-agent discussions")
    disc_sub = disc_parser.add_subparsers(dest="discussion_command")
    disc_sub.add_parser("list", help="List discussions")
    disc_show = disc_sub.add_parser("show", help="Show discussion details")
    disc_show.add_argument("discussion_id", help="Discussion ID")

    # report
    report_parser = sub.add_parser("report", help="Generate reports")
    report_sub = report_parser.add_subparsers(dest="report_command")

    rp_agents = report_sub.add_parser("agents", help="Agent activity report")
    rp_rules = report_sub.add_parser("rules", help="Rule evaluation report")
    rp_intents = report_sub.add_parser("intents", help="Intent statistics")
    rp_daily = report_sub.add_parser("daily", help="Combined daily summary")

    for rp in [rp_agents, rp_rules, rp_intents, rp_daily]:
        rp.add_argument("--format", choices=["text", "json", "markdown"],
                        default="text", help="Output format")
        rp.add_argument("--days", type=int, default=1, help="Lookback period in days")

    return parser


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    commands = {
        "status": cmd_status,
        "init": cmd_init,
        "approve": cmd_approve,
        "heartbeat": cmd_heartbeat,
    }

    # Message subcommands
    if args.command == "message":
        handler = {"list": cmd_message_list}.get(args.message_command)
        if not handler:
            print("Usage: hassaleh message {list}")
            return 1
        try:
            return handler(args)
        except Exception as e:
            print(fmt_error(str(e)))
            return 1

    # Discussion subcommands
    if args.command == "discussion":
        handler = {"list": cmd_discussion_list, "show": cmd_discussion_show}.get(
            args.discussion_command)
        if not handler:
            print("Usage: hassaleh discussion {list|show}")
            return 1
        try:
            return handler(args)
        except Exception as e:
            print(fmt_error(str(e)))
            return 1

    # Report subcommands
    if args.command == "report":
        report_handlers = {
            "agents": cmd_report_agents,
            "rules": cmd_report_rules,
            "intents": cmd_report_intents,
            "daily": cmd_report_daily,
        }
        handler = report_handlers.get(args.report_command)
        if not handler:
            print("Usage: hassaleh report {agents|rules|intents|daily}")
            return 1
        try:
            return handler(args)
        except Exception as e:
            print(fmt_error(str(e)))
            return 1

    if args.command == "skill":
        handler = {
            "list": cmd_skill_list,
            "search": cmd_skill_search,
            "info": cmd_skill_info,
        }.get(args.skill_command)
        if not handler:
            print("Usage: hassaleh skill {list|search|info}")
            return 1
        try:
            return handler(args)
        except Exception as e:
            print(fmt_error(str(e)))
            return 1

    if args.command == "domain":
        handler = {
            "list": cmd_domain_list,
            "info": cmd_domain_info,
        }.get(args.domain_command)
        if not handler:
            print("Usage: hassaleh domain {list|info}")
            return 1
        try:
            return handler(args)
        except Exception as e:
            print(fmt_error(str(e)))
            return 1

    if args.command == "task":
        handler = {"review": cmd_task_review}.get(args.task_command)
        if not handler:
            print("Usage: hassaleh task {review}")
            return 1
        try:
            return handler(args)
        except Exception as e:
            print(fmt_error(str(e)))
            return 1

    if args.command in commands:
        try:
            return commands[args.command](args)
        except Exception as e:
            print(fmt_error(str(e)))
            return 1

    if args.command == "agent":
        handler = {"list": cmd_agent_list, "info": cmd_agent_info}.get(
            args.agent_command)
        if not handler:
            print("Usage: hassaleh agent {list|info}")
            return 1
    elif args.command == "rule":
        handler = {"list": cmd_rule_list, "compile": cmd_rule_compile}.get(
            args.rule_command)
        if not handler:
            print("Usage: hassaleh rule {list|compile}")
            return 1
    elif args.command == "intent":
        handler = {"list": cmd_intent_list}.get(args.intent_command)
        if not handler:
            print("Usage: hassaleh intent {list}")
            return 1
    else:
        parser.print_help()
        return 0

    try:
        return handler(args)
    except Exception as e:
        print(fmt_error(str(e)))
        return 1


if __name__ == "__main__":
    sys.exit(main())
```


### `src/hassaleh/cli_fmt.py`
```py
"""CLI output formatting for Hassaleh.

Provides consistent, colorful terminal output without heavy dependencies.
Uses ANSI escape codes directly (no click/rich dependency).
"""

from __future__ import annotations

import os
import sys


# ──────────────────────────────────────────────
# Color support
# ──────────────────────────────────────────────

def _supports_color() -> bool:
    """Check if stdout supports ANSI colors."""
    if os.environ.get("NO_COLOR"):
        return False
    if not hasattr(sys.stdout, "isatty"):
        return False
    return sys.stdout.isatty()


_COLOR = _supports_color()


def _c(code: str, text: str) -> str:
    """Apply ANSI color code if supported."""
    if not _COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"


# ──────────────────────────────────────────────
# Color helpers
# ──────────────────────────────────────────────

def green(text: str) -> str:
    return _c("32", text)

def red(text: str) -> str:
    return _c("31", text)

def yellow(text: str) -> str:
    return _c("33", text)

def cyan(text: str) -> str:
    return _c("36", text)

def bold(text: str) -> str:
    return _c("1", text)

def dim(text: str) -> str:
    return _c("2", text)


# ──────────────────────────────────────────────
# Semantic formatters
# ──────────────────────────────────────────────

def fmt_ok(text: str) -> str:
    return green(f"✅ {text}")

def fmt_warn(text: str) -> str:
    return yellow(f"⚠️  {text}")

def fmt_error(text: str) -> str:
    return red(f"❌ {text}")

def fmt_header(text: str) -> None:
    print()
    print(bold(f"⭐ {text}"))
    print(dim("─" * (len(text) + 2)))

def fmt_section(title: str) -> str:
    return "\n" + cyan(f"  ── {title} ──")

def fmt_kv(key: str, value: str, indent: int = 4) -> str:
    return " " * indent + f"{bold(key)}: {value}"

def fmt_code(code: str) -> str:
    """Format a code block with line numbers."""
    lines = code.rstrip().split("\n")
    numbered = []
    for i, line in enumerate(lines, 1):
        num = dim(f"{i:4d} │ ")
        numbered.append(f"    {num}{line}")
    return "\n".join(numbered)


# ──────────────────────────────────────────────
# Table formatter
# ──────────────────────────────────────────────

def fmt_table(headers: list[str], rows: list[list[str]], indent: int = 4) -> str:
    """Format data as an aligned ASCII table.

    Args:
        headers: Column headers
        rows: List of row data (each row is a list of strings)
        indent: Left indent spaces

    Returns:
        Formatted table string
    """
    if not rows:
        return " " * indent + "(empty)"

    # Calculate column widths
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(str(cell)))

    # Format header
    prefix = " " * indent
    header_line = prefix + "  ".join(
        bold(h.ljust(widths[i])) for i, h in enumerate(headers)
    )
    separator = prefix + "  ".join("─" * w for w in widths)

    # Format rows
    data_lines = []
    for row in rows:
        cells = []
        for i, cell in enumerate(row):
            if i < len(widths):
                cells.append(str(cell).ljust(widths[i]))
        data_lines.append(prefix + "  ".join(cells))

    return "\n".join([header_line, separator] + data_lines)
```


### `src/hassaleh/domain.py`
```py
"""Skill domain taxonomy helpers."""

from __future__ import annotations

from collections.abc import Iterable


def normalize_domain(value: str | None) -> str:
    """Return a normalized dot-notation domain string."""
    if value is None:
        return ""
    return str(value).strip().strip(".")


def domain_matches_prefix(domain: str | None, prefix: str | None) -> bool:
    """Return True when domain equals the prefix or is nested under it."""
    normalized_domain = normalize_domain(domain)
    normalized_prefix = normalize_domain(prefix)
    if not normalized_prefix:
        return True
    if not normalized_domain:
        return False
    return (
        normalized_domain == normalized_prefix
        or normalized_domain.startswith(f"{normalized_prefix}.")
    )


def domain_matches_any(
    domain: str | None,
    prefixes: Iterable[str] | None,
    *,
    allow_unscoped: bool = False,
) -> bool:
    """Return True if domain matches at least one allowed prefix."""
    normalized_domain = normalize_domain(domain)
    normalized_prefixes = [
        normalize_domain(prefix)
        for prefix in (prefixes or [])
        if normalize_domain(prefix)
    ]
    if not normalized_prefixes:
        return True
    if not normalized_domain:
        return allow_unscoped
    return any(domain_matches_prefix(normalized_domain, prefix) for prefix in normalized_prefixes)


def domain_hierarchy(domain: str | None) -> list[str]:
    """Expand a dot-notation domain into cumulative hierarchy parts."""
    normalized_domain = normalize_domain(domain)
    if not normalized_domain:
        return []

    parts = normalized_domain.split(".")
    hierarchy: list[str] = []
    for index in range(1, len(parts) + 1):
        hierarchy.append(".".join(parts[:index]))
    return hierarchy


def format_domain_hierarchy(domain: str | None) -> str:
    """Format a domain hierarchy for human-readable CLI output."""
    hierarchy = domain_hierarchy(domain)
    if not hierarchy:
        return "unscoped"
    return " > ".join(part.split(".")[-1] for part in hierarchy)
```


### `src/hassaleh/engine/gsl_ops.lark`
```lark
// ═══════════════════════════════════════════════════════════════
// GSL-Ops (Hassaleh Deterministic Rule Language) — Lark EBNF Grammar
// ═══════════════════════════════════════════════════════════════
//
// A deterministic subset of GSL (GWW3 Symbolic Logic).
// No distributions, no truth values, no randomness.
// Designed for agent orchestration rules.
//
// Parser:  Earley + PythonIndenter
// Syntax:  Python-style colon blocks
//
//   MATCH (a:Agent):
//       IF a.last_heartbeat < NOW() - duration("PT5M"):
//           IF a.restart_count_1h < 5:
//               SUBMIT_INTENT "restart_agent" ON a
//           ELSE:
//               ALERT "Circuit breaker: agent unresponsive" ON a
//
// Reference: docs/CONCEPT.md v1.2, Section 4.14
// Origin: Ported from GWW3 gsl.lark v3, stripped for determinism.

// ──────────────────────────────────────────
// Program structure
// ──────────────────────────────────────────

start: (_NEWLINE | statement)*

?statement: match_block
          | if_block
          | elif_block
          | else_block
          | every_block
          | foreach_block
          | let_stmt
          | effect_stmt
          | action_stmt
          | comment_line

// ──────────────────────────────────────────
// Block-opening statements (colon + indent)
// ──────────────────────────────────────────

// MATCH …:  →  Graph pattern query. Entire line captured as terminal.
match_block: MATCH_LINE _NEWLINE _INDENT body _DEDENT

// IF / ELIF / ELSE  →  Conditional branching
if_block: "IF" expr ":" _NEWLINE _INDENT body _DEDENT
elif_block: "ELIF" expr ":" _NEWLINE _INDENT body _DEDENT
else_block: "ELSE" ":" _NEWLINE _INDENT body _DEDENT

// EVERY …:  →  Time-triggered rule (evaluated by Daemon scheduler)
every_block: "EVERY" duration ":" _NEWLINE _INDENT body _DEDENT

// FOREACH var IN collection:  →  Iterate over a list/collection
foreach_block: "FOREACH" NAME "IN" expr ":" _NEWLINE _INDENT body _DEDENT

// Body: sequence of statements inside an indented block
body: (_NEWLINE | statement)*

// ──────────────────────────────────────────
// Simple statements (no block)
// ──────────────────────────────────────────

// LET — variable binding (deterministic only, no sampling)
let_stmt: "LET" NAME "=" expr

// Effects — property modifications (deterministic, no truth values)
effect_stmt: PROP_ACCESS "+=" expr            -> effect_add
           | PROP_ACCESS "-=" expr            -> effect_sub
           | PROP_ACCESS "*=" expr            -> effect_mul
           | PROP_ACCESS "=" expr             -> effect_set

// Hassaleh-specific actions
action_stmt: submit_intent
           | log_stmt
           | alert_stmt

// SUBMIT_INTENT — create an Intent node for the Daemon to process
//   SUBMIT_INTENT "capability_id" ON target_var
//   SUBMIT_INTENT "capability_id" ON target_var WITH {key: value, ...}
submit_intent: "SUBMIT_INTENT" STRING "ON" NAME with_clause?

// LOG — structured logging
//   LOG "message" LEVEL "info"
//   LOG "message"
log_stmt: "LOG" STRING log_level?

log_level: "LEVEL" STRING

// ALERT — notification trigger (dispatched via Daemon)
//   ALERT "message" ON target_var
//   ALERT "message"
alert_stmt: "ALERT" STRING ("ON" NAME)?

// WITH clause for SUBMIT_INTENT
with_clause: "WITH" "{" kv_pair ("," kv_pair)* "}"
kv_pair: NAME ":" expr

// Comment line (standalone, not inline)
comment_line: COMMENT

// ──────────────────────────────────────────
// Duration literals
// ──────────────────────────────────────────

// ISO 8601 durations: "PT5M", "PT1H", "P1D"
// Also support human-readable: 5m, 1h, 30s, 1d
duration: STRING
        | DURATION_LITERAL

DURATION_LITERAL.3: /\d+[smhd]/

// ──────────────────────────────────────────
// Expressions (precedence: low → high)
// ──────────────────────────────────────────

?expr: or_expr

?or_expr: and_expr (OR_OP and_expr)*

?and_expr: not_expr (AND_OP not_expr)*

?not_expr: NOT_OP not_expr                -> negation
         | comparison

?comparison: arith (COMP_OP arith)?

?arith: term ((PLUS | MINUS) term)*

?term: factor ((STAR | SLASH | PERCENT) factor)*

?factor: MINUS atom                       -> neg
       | atom

?atom: NUMBER                             -> number
     | STRING                             -> string_literal
     | PROP_ACCESS                        -> prop_access
     | NAME                               -> var_ref
     | func_call
     | list_comp
     | list_literal
     | "(" expr ")"
     | "true"                             -> bool_true
     | "false"                            -> bool_false
     | "null"                             -> null_literal
     | "None"                             -> null_literal

// List literal: [expr, expr, ...]
list_comp: "[" expr "FOR" NAME "IN" expr ("IF" expr)? "]"
list_literal: "[" list_items? "]"
list_items: expr ("," expr)*

// ──────────────────────────────────────────
// Built-in functions (deterministic only)
// ──────────────────────────────────────────

func_call: NAME "(" func_args? ")"

func_args: expr ("," expr)*

// Available functions (resolved in compiler/runtime):
//   NOW()              — current UTC datetime
//   DURATION("PT5M")   — parse ISO 8601 duration
//   ABS(x)             — absolute value
//   MIN(a, b)          — minimum
//   MAX(a, b)          — maximum
//   CLAMP(x, lo, hi)   — clamp value
//   LEN(x)             — length/count
//   STR(x)             — string conversion
//   INT(x)             — integer conversion
//   FLOAT(x)           — float conversion

// ──────────────────────────────────────────
// Operators
// ──────────────────────────────────────────

OR_OP.2:    "||" | "or"
AND_OP.2:   "&&" | "and"
NOT_OP.2:   "!" | "not"
COMP_OP.2:  ">=" | "<=" | ">" | "<" | "==" | "!=" | "not in" | "in"
PLUS:     "+"
MINUS:    "-"
STAR:     "*"
SLASH:    "/"
PERCENT:  "%"

// ──────────────────────────────────────────
// Terminals
// ──────────────────────────────────────────

// MATCH line: captures entire "MATCH ... :" as one token.
// Cypher patterns contain colons (e.g. :Agent), so we capture the whole line.
MATCH_LINE.3: /MATCH\s+.+:/

// Property access: Node.property (e.g. a.lifecycle, agent.last_heartbeat)
PROP_ACCESS.2: /[a-zA-Z_]\w*\.[a-zA-Z_]\w*/

// Identifiers
NAME: /[a-zA-Z_]\w*/

// Numbers: 42, 3.14, 1_000, 5e9
NUMBER: /\-?\d[\d_]*(\.\d[\d_]*)?([eE][\-+]?\d+)?/

// Strings: "hello", 'world'
STRING: /\"[^\"]*\"/ | /'[^']*'/

// Comments
COMMENT: /#[^\n]*/

// ──────────────────────────────────────────
// Whitespace
// ──────────────────────────────────────────

_NEWLINE: /(\r?\n[\t ]*)+/

%ignore /[\t \f]+/
%ignore COMMENT

%declare _INDENT _DEDENT
```


### `src/hassaleh/engine/compiler.py`
```py
"""GSL-Ops Compiler — Transpile GSL-Ops parse trees to executable Python.

Takes a Lark Tree (from parser.parse_rule) and generates a Python
function that, when called with a RuleContext, evaluates the rule.

Generated code signature:
    def evaluate(ctx: RuleContext) -> None:
        # ... emits intents, logs, alerts to ctx

The generated source is human-readable and stored in the Rule node's
compiled_python property.

Reference: docs/CONCEPT.md v1.2, Section 4.14
"""

from __future__ import annotations

import re
from lark import Tree, Token
from hassaleh.engine.parser import parse_rule

COMPILER_VERSION = "gsl-ops-0.1"


class GSLOpsCompiler:
    """Compile a GSL-Ops parse tree to Python source code."""

    def __init__(self, rule_id: str = "unknown"):
        self.rule_id = rule_id
        self._indent = 0
        self._lines: list[str] = []

    def compile(self, tree: Tree) -> str:
        """Compile a Lark Tree (start node) to Python source."""
        self._indent = 0
        self._lines = []

        # Header — no import statement; RuleContext is pre-injected into
        # the exec() namespace by the Daemon to avoid needing __import__
        self._emit("")
        self._emit("")
        self._emit("def evaluate(ctx) -> None:")
        self._indent = 1
        self._emit(f'"""Compiled from GSL-Ops rule: {self.rule_id}"""')

        # Process top-level statements
        has_statements = False
        for child in tree.children:
            if isinstance(child, Tree):
                self._compile_node(child)
                has_statements = True

        if not has_statements:
            self._emit("pass")

        return "\n".join(self._lines) + "\n"

    # ── Dispatch ──

    def _compile_node(self, node: Tree) -> None:
        handler = getattr(self, f"_compile_{node.data}", None)
        if handler:
            handler(node)
        else:
            for child in node.children:
                if isinstance(child, Tree):
                    self._compile_node(child)

    # ── MATCH block ──

    def _compile_match_block(self, node: Tree) -> None:
        match_line = self._get_token(node, "MATCH_LINE")
        # Strip "MATCH " prefix and trailing ":"
        cypher = match_line[6:].rstrip().rstrip(":")

        self._emit(f'for _row in ctx.match({cypher!r}):')
        self._indent += 1

        # Extract variable names from Cypher pattern
        var_names = self._extract_cypher_vars(cypher)
        for var in var_names:
            self._emit(f'{var} = _row["{var}"]')

        # Compile body
        body = self._find_child(node, "body")
        if body:
            self._compile_body(body)
        else:
            self._emit("pass")

        self._indent -= 1

    # ── IF / ELIF / ELSE ──

    def _compile_if_block(self, node: Tree) -> None:
        cond = self._extract_condition(node)
        self._emit(f"if {cond}:")
        self._indent += 1
        body = self._find_child(node, "body")
        if body:
            self._compile_body(body)
        else:
            self._emit("pass")
        self._indent -= 1

    def _compile_elif_block(self, node: Tree) -> None:
        cond = self._extract_condition(node)
        self._emit(f"elif {cond}:")
        self._indent += 1
        body = self._find_child(node, "body")
        if body:
            self._compile_body(body)
        else:
            self._emit("pass")
        self._indent -= 1

    def _compile_else_block(self, node: Tree) -> None:
        self._emit("else:")
        self._indent += 1
        body = self._find_child(node, "body")
        if body:
            self._compile_body(body)
        else:
            self._emit("pass")
        self._indent -= 1

    # ── FOREACH block ──

    def _compile_foreach_block(self, node: Tree) -> None:
        name = self._get_token(node, "NAME")
        # Expression is everything between NAME and body
        expr_children = []
        body = None
        found_name = False
        for child in node.children:
            if isinstance(child, Tree) and child.data == "body":
                body = child
            elif isinstance(child, Token) and child.type == "NAME" and not found_name:
                found_name = True
            elif found_name and not (isinstance(child, Tree) and child.data == "body"):
                expr_children.append(child)

        collection_expr = self._compile_expr_list(expr_children)
        self._emit(f"for {name} in {collection_expr}:")
        self._indent += 1

        if body:
            self._compile_body(body)
        else:
            self._emit("pass")

        self._indent -= 1

    # ── EVERY block ──

    def _compile_every_block(self, node: Tree) -> None:
        # Extract duration
        duration = self._find_child(node, "duration")
        dur_str = self._compile_duration(duration) if duration else '"PT1M"'

        self._emit(f'if ctx.should_run_schedule({dur_str}):')
        self._indent += 1

        body = self._find_child(node, "body")
        if body:
            self._compile_body(body)
        else:
            self._emit("pass")

        self._indent -= 1

    # ── LET ──

    def _compile_let_stmt(self, node: Tree) -> None:
        # Grammar: let_stmt: "LET" NAME "=" expr
        # Children: [NAME_token, ...expr_nodes]
        # The first NAME token is the variable name; everything after is the expr.
        name = self._get_token(node, "NAME")
        # Find the index of the first NAME token, take everything after it
        name_idx = 0
        for i, c in enumerate(node.children):
            if isinstance(c, Token) and c.type == "NAME":
                name_idx = i
                break
        # Expr is everything after the NAME (Lark strips "LET" and "=" keywords)
        expr_children = node.children[name_idx + 1:]
        expr = self._compile_expr_list(expr_children)
        self._emit(f"{name} = {expr}")

    # ── Effects ──

    def _compile_effect_set(self, node: Tree) -> None:
        prop, expr = self._extract_effect(node)
        node_var, prop_name = prop.split(".", 1)
        self._emit(f'ctx.set_property({node_var}, "{prop_name}", {expr})')

    def _compile_effect_add(self, node: Tree) -> None:
        prop, expr = self._extract_effect(node)
        node_var, prop_name = prop.split(".", 1)
        self._emit(f'ctx.add_property({node_var}, "{prop_name}", {expr})')

    def _compile_effect_sub(self, node: Tree) -> None:
        prop, expr = self._extract_effect(node)
        node_var, prop_name = prop.split(".", 1)
        self._emit(f'ctx.sub_property({node_var}, "{prop_name}", {expr})')

    def _compile_effect_mul(self, node: Tree) -> None:
        prop, expr = self._extract_effect(node)
        node_var, prop_name = prop.split(".", 1)
        self._emit(f'ctx.mul_property({node_var}, "{prop_name}", {expr})')

    # ── Actions ──

    def _compile_submit_intent(self, node: Tree) -> None:
        cap_id = self._get_token(node, "STRING").strip('"').strip("'")
        target_var = self._get_token(node, "NAME")
        with_clause = self._find_child(node, "with_clause")

        if with_clause:
            kv_pairs = list(with_clause.find_data("kv_pair"))
            args_parts = []
            for kv in kv_pairs:
                key = self._get_token(kv, "NAME")
                val_children = [c for c in kv.children
                                if not (isinstance(c, Token) and c.type == "NAME")]
                val = self._compile_expr_list(val_children)
                args_parts.append(f'"{key}": {val}')
            args_dict = "{" + ", ".join(args_parts) + "}"
            self._emit(f'ctx.submit_intent("{cap_id}", {target_var}, {args_dict})')
        else:
            self._emit(f'ctx.submit_intent("{cap_id}", {target_var})')

    def _compile_log_stmt(self, node: Tree) -> None:
        strings = [c for c in node.children
                   if isinstance(c, Token) and c.type == "STRING"]
        message = strings[0].strip('"').strip("'") if strings else ""
        level_node = self._find_child(node, "log_level")
        if level_node:
            level_str = self._get_token(level_node, "STRING").strip('"').strip("'")
            self._emit(f'ctx.log("{message}", level="{level_str}")')
        else:
            self._emit(f'ctx.log("{message}")')

    def _compile_alert_stmt(self, node: Tree) -> None:
        msg = self._get_token(node, "STRING").strip('"').strip("'")
        target_var = self._get_token(node, "NAME")
        if target_var:
            self._emit(f'ctx.alert("{msg}", {target_var})')
        else:
            self._emit(f'ctx.alert("{msg}")')

    # ── Body ──

    def _compile_body(self, node: Tree) -> None:
        has_stmts = False
        for child in node.children:
            if isinstance(child, Tree):
                self._compile_node(child)
                has_stmts = True
        if not has_stmts:
            self._emit("pass")

    # ── Expression compilation ──

    def _extract_condition(self, node: Tree) -> str:
        """Extract the condition expression from an IF/ELIF node."""
        # Everything that's not the body is the condition
        cond_parts = []
        for child in node.children:
            if isinstance(child, Tree) and child.data == "body":
                continue
            if isinstance(child, Tree):
                cond_parts.append(child)
            elif isinstance(child, Token) and child.type not in (
                "_NEWLINE", "_INDENT", "_DEDENT"
            ):
                cond_parts.append(child)
        return self._compile_expr_list(cond_parts)

    def _extract_effect(self, node: Tree) -> tuple[str, str]:
        """Extract (prop_access, value_expr) from an effect node."""
        prop = self._get_token(node, "PROP_ACCESS")
        value_children = [c for c in node.children
                          if not (isinstance(c, Token) and c.type in
                                  ("PROP_ACCESS", "MOD_OP"))]
        value = self._compile_expr_list(value_children)
        return prop, value

    def _compile_expr_list(self, nodes: list) -> str:
        """Compile a list of AST nodes/tokens into a Python expression."""
        parts = []
        for node in nodes:
            if isinstance(node, Token):
                parts.append(self._compile_token(node))
            elif isinstance(node, Tree):
                parts.append(self._compile_expr_tree(node))
        result = " ".join(parts) if parts else "None"
        return result.strip()

    def _compile_expr_tree(self, node: Tree) -> str:
        """Compile a single expression tree node to Python."""
        d = node.data

        if d == "number":
            return str(node.children[0])

        if d == "string_literal":
            return str(node.children[0])

        if d == "prop_access":
            pa = str(node.children[0])
            var, prop = pa.split(".", 1)
            return f'ctx.prop({var}, "{prop}")'

        if d == "var_ref":
            name = str(node.children[0])
            return name

        if d == "bool_true":
            return "True"

        if d == "bool_false":
            return "False"

        if d == "null_literal":
            return "None"

        if d == "negation":
            # Grammar: not_expr: NOT_OP not_expr -> negation
            # children[0] = NOT_OP token, children[1] = expression tree
            tree_children = [c for c in node.children if isinstance(c, Tree)]
            inner = self._compile_expr_tree(tree_children[0]) if tree_children else "None"
            return f"(not {inner})"

        if d == "neg":
            # Grammar: factor: MINUS atom -> neg
            # children[0] = MINUS token, children[1] = atom tree
            tree_children = [c for c in node.children if isinstance(c, Tree)]
            inner = self._compile_expr_tree(tree_children[0]) if tree_children else "0"
            return f"(-{inner})"

        if d == "list_literal":
            return self._compile_list_literal(node)

        if d == "list_comp":
            return self._compile_list_comp(node)

        if d == "func_call":
            return self._compile_func_call(node)

        if d in ("comparison", "and_expr", "or_expr", "arith", "term"):
            parts = []
            for ch in node.children:
                if isinstance(ch, Token):
                    parts.append(self._compile_token(ch))
                elif isinstance(ch, Tree):
                    parts.append(self._compile_expr_tree(ch))
            return f"({' '.join(parts)})"

        # Fallback: recurse
        parts = []
        for ch in node.children:
            if isinstance(ch, Token):
                parts.append(self._compile_token(ch))
            elif isinstance(ch, Tree):
                parts.append(self._compile_expr_tree(ch))
        return " ".join(parts)

    def _compile_list_literal(self, node: Tree) -> str:
        """Compile [a, b, c] to a Python list."""
        items_node = self._find_child(node, "list_items")
        if not items_node:
            return "[]"
        items = [self._compile_expr_tree(ch) for ch in items_node.children
                 if isinstance(ch, Tree)]
        return f"[{', '.join(items)}]"

    def _compile_list_comp(self, node: Tree) -> str:
        """Compile [expr FOR name IN iterable IF cond] to Python."""
        expr_tree = next((ch for ch in node.children if isinstance(ch, Tree)), None)
        if expr_tree is None:
            return "[]"

        names = [str(ch) for ch in node.children
                 if isinstance(ch, Token) and ch.type == "NAME"]
        comp_var = names[0] if names else "_item"

        tree_count = 0
        iterable_tree = None
        cond_tree = None

        for ch in node.children:
            if isinstance(ch, Tree):
                tree_count += 1
                if tree_count == 2:
                    iterable_tree = ch
                elif tree_count == 3:
                    cond_tree = ch

        expr = self._compile_expr_tree(expr_tree)
        iterable = self._compile_expr_tree(iterable_tree) if iterable_tree else "[]"
        if_clause = ""
        if cond_tree is not None:
            condition = self._compile_expr_tree(cond_tree)
            if_clause = f" if {condition}"

        return f"[{expr} for {comp_var} in {iterable}{if_clause}]"

    def _compile_func_call(self, node: Tree) -> str:
        """Compile a function call."""
        func_name = self._get_token(node, "NAME")
        args_node = self._find_child(node, "func_args")

        # Map GSL-Ops function names to runtime methods
        FUNC_MAP = {
            "NOW": "ctx.now()",
            "DURATION": None,  # special handling
            "ABS": "abs",
            "MIN": "min",
            "MAX": "max",
            "CLAMP": "ctx.clamp",
            "LEN": "len",
            "STR": "str",
            "INT": "int",
            "FLOAT": "float",
            "RANGE": "range",
            "KEYS": "ctx.keys",
            "SORTED": "sorted",
            "LIST": "list",
            "CONTAINS": "ctx.contains",
            "APPEND": "ctx.append",
            "CONCAT": "ctx.concat",
            "FLATTEN": "ctx.flatten",
            "UNIQUE": "ctx.unique",
            "SLICE": "ctx.slice",
            "SUM": "sum",
            "AVG": "ctx.avg",
            "FIRST": "ctx.first",
            "LAST": "ctx.last",
            "COUNT": "len",
            "ZIP": "ctx.zip",
            "ENUMERATE": "list(enumerate",
        }

        if func_name == "NOW":
            return "ctx.now()"

        if func_name == "DURATION":
            if args_node:
                arg = self._compile_expr_list(list(args_node.children))
                return f"ctx.duration({arg})"
            return 'ctx.duration("PT0S")'

        mapped = FUNC_MAP.get(func_name)
        if mapped and args_node:
            args = [self._compile_expr_tree(ch) for ch in args_node.children
                    if isinstance(ch, Tree)]
            if func_name == "ENUMERATE":
                return f"{mapped}({', '.join(args)}))"
            return f"{mapped}({', '.join(args)})"

        # Unknown function — reject at compile time (security: prevents
        # calling arbitrary ctx methods like ctx.session())
        raise ValueError(
            f"Unknown function '{func_name}' in rule '{self.rule_id}'. "
            f"Allowed: {', '.join(sorted(FUNC_MAP.keys()))}"
        )

    def _compile_token(self, token: Token) -> str:
        t = str(token)
        if token.type == "OR_OP":
            return "or"
        if token.type == "AND_OP":
            return "and"
        if token.type == "NOT_OP":
            return "not"
        if token.type == "COMP_OP":
            return t
        return t

    def _compile_duration(self, node: Tree) -> str:
        """Compile a duration node."""
        # Check for STRING child (ISO 8601)
        string_tok = self._get_token(node, "STRING")
        if string_tok:
            return string_tok

        # Check for DURATION_LITERAL (e.g. "5m", "1h")
        dur_lit = self._get_token(node, "DURATION_LITERAL")
        if dur_lit:
            return f'"{dur_lit}"'

        return '"PT0S"'

    # ── Helpers ──

    def _emit(self, line: str) -> None:
        self._lines.append("    " * self._indent + line)

    def _get_token(self, node: Tree, token_type: str) -> str:
        for ch in node.children:
            if isinstance(ch, Token) and ch.type == token_type:
                return str(ch)
        return ""

    def _find_child(self, node: Tree, data: str) -> Tree | None:
        for ch in node.children:
            if isinstance(ch, Tree) and ch.data == data:
                return ch
        return None

    def _extract_cypher_vars(self, cypher: str) -> list[str]:
        """Extract bound variable names from a Cypher pattern."""
        node_vars = re.findall(r'\((\w+)(?::\w+)?(?:\s*\{[^}]*\})?\)', cypher)
        rel_vars = re.findall(r'\[(\w+):\w+', cypher)
        return list(dict.fromkeys(node_vars + rel_vars))


# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────

def compile_rule(rule_text: str, rule_id: str = "unknown") -> str:
    """Parse and compile a GSL-Ops rule to Python source.

    Args:
        rule_text: GSL-Ops source text
        rule_id: Rule identifier

    Returns:
        Python source code string
    """
    tree = parse_rule(rule_text)
    compiler = GSLOpsCompiler(rule_id=rule_id)
    return compiler.compile(tree)
```


### `src/hassaleh/engine/runtime.py`
```py
"""GSL-Ops Runtime — Execution context for compiled rules.

Compiled GSL-Ops rules call methods on a RuleContext object.
This module provides the runtime environment.

Unlike GWW3's stochastic runtime, this is fully deterministic:
no distributions, no probability gates, no sampling.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
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

        Uses execute_read() to enforce read-only transactions at the
        Neo4j protocol level, preventing any Cypher injection that
        attempts write operations (CREATE, DELETE, SET, etc.).
        """
        query = f"MATCH {cypher_pattern} RETURN *"

        def _read_tx(tx):
            result = tx.run(query, **params)
            return [dict(record) for record in result]

        return self.session.execute_read(_read_tx)

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
        """Extract the node ID for intent tracking.

        Raises ValueError if no stable ID can be determined (prevents
        silent mismatches in the conflict resolver).
        """
        if hasattr(node, "element_id"):
            return node.element_id
        if isinstance(node, dict):
            if "_element_id" in node:
                return node["_element_id"]
            if "id" in node:
                return str(node["id"])
        raise ValueError(
            f"Cannot determine stable node ID for {type(node).__name__}. "
            f"Node must have 'element_id', '_element_id', or 'id'."
        )

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

    def keys(self, node: Any) -> list[str]:
        """Get property keys of a Neo4j node."""
        if isinstance(node, dict):
            return list(node.keys())
        if hasattr(node, "keys"):
            return list(node.keys())
        return []

    def contains(self, items: Any, value: Any) -> bool:
        """Return True if value is present in items."""
        for item in self._to_list(items):
            if item == value:
                return True
        return False

    def append(self, items: Any, value: Any) -> list[Any]:
        """Return a new list with value appended."""
        result = self._to_list(items)
        result.append(value)
        return result

    def concat(self, left: Any, right: Any) -> list[Any]:
        """Return two list-like values merged into a new list."""
        return self._to_list(left) + self._to_list(right)

    def flatten(self, items: Any) -> list[Any]:
        """Flatten one nesting level from a list-like value."""
        flattened: list[Any] = []
        for item in self._to_list(items):
            if self._is_flattenable(item):
                flattened.extend(list(item))
            else:
                flattened.append(item)
        return flattened

    def unique(self, items: Any) -> list[Any]:
        """Deduplicate while preserving original order."""
        result: list[Any] = []
        for item in self._to_list(items):
            if item not in result:
                result.append(item)
        return result

    def slice(self, items: Any, start: Any, end: Any) -> list[Any]:
        """Return a Python-style slice of a list-like value."""
        values = self._to_list(items)
        return values[start:end]

    def avg(self, items: Any) -> float | None:
        """Return the arithmetic mean, or None for an empty list."""
        values = self._to_list(items)
        if not values:
            return None
        return sum(values) / len(values)

    def first(self, items: Any) -> Any:
        """Return the first element, or None if empty."""
        values = self._to_list(items)
        return values[0] if values else None

    def last(self, items: Any) -> Any:
        """Return the last element, or None if empty."""
        values = self._to_list(items)
        return values[-1] if values else None

    def zip(self, left: Any, right: Any) -> list[tuple[Any, Any]]:
        """Return paired items from two list-like values."""
        return list(zip(self._to_list(left), self._to_list(right)))

    def _to_list(self, value: Any) -> list[Any]:
        """Coerce list-like runtime values into a list without mutating input."""
        if value is None:
            return []
        if isinstance(value, list):
            return list(value)
        if isinstance(value, tuple):
            return list(value)
        if isinstance(value, range):
            return list(value)
        if isinstance(value, Iterable) and not isinstance(value, (str, bytes, dict)):
            return list(value)
        return [value]

    def _is_flattenable(self, value: Any) -> bool:
        """Return True when flatten() should expand the value one level."""
        return isinstance(value, Iterable) and not isinstance(
            value, (str, bytes, dict)
        )
```


### `src/hassaleh/engine/resolver.py`
```py
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

import logging
from collections import defaultdict
from typing import Any

from hassaleh.engine.runtime import PropertyOp, RuleIntent

log = logging.getLogger("hassaleh.engine.resolver")


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
            # Warn on ambiguous same-priority SET conflicts
            same_prio = [s for s in sets if s.priority == winner.priority]
            if len(same_prio) > 1:
                rule_ids = [s.rule_id for s in same_prio]
                log.warning(
                    f"SET conflict on ({key[0]}, {key[1]}): "
                    f"{len(same_prio)} rules at priority {winner.priority} "
                    f"({rule_ids}). Winner: {winner.rule_id}"
                )
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
```


### `src/hassaleh/bridge/openclaw.py`
```py
"""OpenClaw Gateway Bridge — HTTP client for the OpenClaw Gateway API.

Provides async methods for:
- Health checks (gateway status)
- Sending messages via channels (Telegram, etc.)
- Session management (list, spawn, send)
- System events (wake)

The OpenClaw Gateway runs on localhost and requires a bearer token
for authentication.

Reference: OpenClaw docs/gateway/protocol.md
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urljoin

import aiohttp

log = logging.getLogger("hassaleh.bridge.openclaw")


class OpenClawBridge:
    """Async HTTP client for OpenClaw Gateway.

    Usage:
        bridge = OpenClawBridge("http://localhost:18789", "your-token")
        async with bridge:
            health = await bridge.health()
            await bridge.send_message("telegram", "5580211068", "Hello!")
    """

    def __init__(self, gateway_url: str, gateway_token: str):
        self.gateway_url = gateway_url.rstrip("/")
        self.gateway_token = gateway_token
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> OpenClawBridge:
        self._session = aiohttp.ClientSession(
            headers={
                "Authorization": f"Bearer {self.gateway_token}",
                "Content-Type": "application/json",
            },
            timeout=aiohttp.ClientTimeout(total=30),
        )
        return self

    async def __aexit__(self, *exc) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    # ── Health ──

    async def health(self) -> dict[str, Any]:
        """Check OpenClaw Gateway health."""
        try:
            async with self._session.get(f"{self.gateway_url}/health") as resp:
                if resp.status == 200:
                    return await resp.json()
                return {"status": "error", "code": resp.status}
        except Exception as e:
            log.warning(f"OpenClaw health check failed: {e}")
            return {"status": "unreachable", "error": str(e)}

    async def is_healthy(self) -> bool:
        """Quick health check."""
        h = await self.health()
        return h.get("status") == "ok" or "version" in h

    # ── Messaging ──

    async def send_message(
        self,
        channel: str,
        target: str,
        message: str,
        silent: bool = False,
    ) -> dict[str, Any]:
        """Send a message via OpenClaw channel plugin.

        Args:
            channel: Channel name (telegram, discord, etc.)
            target: Target chat ID or username
            message: Message text
            silent: Send without notification sound

        Returns:
            Response dict with messageId, ok, etc.
        """
        payload = {
            "action": "send",
            "channel": channel,
            "target": target,
            "message": message,
        }
        if silent:
            payload["silent"] = True

        return await self._tool_call("message", payload)

    # ── Sessions ──

    async def list_sessions(
        self,
        kinds: list[str] | None = None,
        limit: int = 20,
        active_minutes: int | None = None,
    ) -> dict[str, Any]:
        """List active sessions."""
        payload: dict[str, Any] = {"limit": limit}
        if kinds:
            payload["kinds"] = kinds
        if active_minutes:
            payload["activeMinutes"] = active_minutes

        return await self._tool_call("sessions_list", payload)

    async def spawn_session(
        self,
        task: str,
        label: str | None = None,
        runtime: str = "subagent",
        model: str | None = None,
        timeout_seconds: int = 0,
    ) -> dict[str, Any]:
        """Spawn an isolated sub-agent session.

        Args:
            task: Task description for the sub-agent
            label: Optional label for logs/UI
            runtime: "subagent" or "acp"
            model: Optional model override
            timeout_seconds: Run timeout (0 = no timeout)

        Returns:
            Response with runId, childSessionKey, status
        """
        payload: dict[str, Any] = {
            "task": task,
            "runtime": runtime,
        }
        if label:
            payload["label"] = label
        if model:
            payload["model"] = model
        if timeout_seconds:
            payload["runTimeoutSeconds"] = timeout_seconds

        return await self._tool_call("sessions_spawn", payload)

    async def send_to_session(
        self,
        session_key: str,
        message: str,
        timeout_seconds: int = 30,
    ) -> dict[str, Any]:
        """Send a message to another session."""
        return await self._tool_call("sessions_send", {
            "sessionKey": session_key,
            "message": message,
            "timeoutSeconds": timeout_seconds,
        })

    # ── System Events ──

    async def wake(self, text: str, mode: str = "now") -> dict[str, Any]:
        """Send a wake/system event."""
        return await self._tool_call("cron", {
            "action": "wake",
            "text": text,
            "mode": mode,
        })

    # ── Internal ──

    async def _tool_call(self, tool: str, params: dict[str, Any]) -> dict[str, Any]:
        """Execute a tool call via the OpenClaw Gateway API.

        Uses the gateway's tool invocation endpoint.
        """
        url = f"{self.gateway_url}/api/tools/{tool}"

        try:
            async with self._session.post(url, json=params) as resp:
                result = await resp.json()
                if resp.status != 200:
                    log.warning(f"Tool {tool} returned {resp.status}: {result}")
                return result
        except aiohttp.ClientError as e:
            log.error(f"Tool call {tool} failed: {e}")
            return {"ok": False, "error": str(e)}
        except Exception as e:
            log.error(f"Tool call {tool} unexpected error: {e}")
            return {"ok": False, "error": str(e)}
```


### `src/hassaleh/bridge/notifications.py`
```py
"""Notification dispatcher — Routes rule ALERT actions to messaging channels.

Dispatches alerts from GSL-Ops rules through the OpenClaw bridge
to Telegram, Discord, or other configured channels.

Usage:
    dispatcher = NotificationDispatcher(bridge, default_channel, default_target)
    await dispatcher.dispatch_alert(alert_action)
"""

from __future__ import annotations

import logging
from typing import Any

from hassaleh.bridge.openclaw import OpenClawBridge
from hassaleh.engine.runtime import AlertAction, LogAction

log = logging.getLogger("hassaleh.bridge.notifications")


class NotificationDispatcher:
    """Routes rule outputs to messaging channels via OpenClaw.

    Dispatches:
    - ALERT actions → Telegram/Discord messages
    - LOG actions at "error" level → optional error notifications
    """

    def __init__(
        self,
        bridge: OpenClawBridge,
        default_channel: str = "telegram",
        default_target: str = "",
        notify_on_error_logs: bool = True,
    ):
        self.bridge = bridge
        self.default_channel = default_channel
        self.default_target = default_target
        self.notify_on_error_logs = notify_on_error_logs

    async def dispatch_alert(self, alert: AlertAction) -> bool:
        """Send an alert notification via OpenClaw messaging.

        Args:
            alert: AlertAction from a GSL-Ops rule

        Returns:
            True if sent successfully
        """
        if not self.default_target:
            log.warning(f"No notification target configured, dropping alert: {alert.message}")
            return False

        # Format the alert message
        target_info = ""
        if alert.target_node and isinstance(alert.target_node, dict):
            target_name = alert.target_node.get("name", alert.target_node.get("id", "?"))
            target_info = f" [{target_name}]"

        message = f"⚠️ **Hassaleh Alert**{target_info}\n\n{alert.message}\n\n_Rule: {alert.rule_id}_"

        try:
            result = await self.bridge.send_message(
                channel=self.default_channel,
                target=self.default_target,
                message=message,
            )
            if result.get("ok"):
                log.info(f"Alert dispatched: {alert.message[:50]}")
                return True
            else:
                log.warning(f"Alert dispatch failed: {result}")
                return False
        except Exception as e:
            log.error(f"Alert dispatch error: {e}")
            return False

    async def dispatch_error_log(self, log_action: LogAction) -> bool:
        """Send error-level log as notification (if enabled).

        Args:
            log_action: LogAction with level="error"

        Returns:
            True if sent
        """
        if not self.notify_on_error_logs:
            return False
        if log_action.level != "error":
            return False
        if not self.default_target:
            return False

        message = f"🔴 **Hassaleh Error Log**\n\n{log_action.message}\n\n_Rule: {log_action.rule_id}_"

        try:
            result = await self.bridge.send_message(
                channel=self.default_channel,
                target=self.default_target,
                message=message,
                silent=True,  # Don't buzz for log-level errors
            )
            return result.get("ok", False)
        except Exception as e:
            log.error(f"Error log dispatch failed: {e}")
            return False

    async def dispatch_all(
        self,
        alerts: list[AlertAction],
        logs: list[LogAction] | None = None,
    ) -> int:
        """Dispatch all pending alerts and error logs.

        Returns:
            Number of successfully dispatched notifications
        """
        sent = 0

        for alert in alerts:
            if await self.dispatch_alert(alert):
                sent += 1

        if logs and self.notify_on_error_logs:
            for log_action in logs:
                if log_action.level == "error":
                    if await self.dispatch_error_log(log_action):
                        sent += 1

        return sent
```


### `schema.cypher`
```cypher
// ═══════════════════════════════════════════════════════════════
// Hassaleh — Neo4j Schema (MVP)
// ═══════════════════════════════════════════════════════════════
//
// Node types: Agent, Capability, SkillDomain, Workspace, Intent, Task,
//             DaemonConfig, QueryConfig, SystemVersion
//
// Reference: docs/CONCEPT.md v1.2
// Created: 2026-03-29 by Dione 🌙

// ──────────────────────────────────────────
// Uniqueness constraints
// ──────────────────────────────────────────

CREATE CONSTRAINT agent_id IF NOT EXISTS
  FOR (a:Agent) REQUIRE a.id IS UNIQUE;

CREATE CONSTRAINT capability_id IF NOT EXISTS
  FOR (c:Capability) REQUIRE c.id IS UNIQUE;

CREATE CONSTRAINT skilldomain_id IF NOT EXISTS
  FOR (sd:SkillDomain) REQUIRE sd.id IS UNIQUE;

CREATE CONSTRAINT workspace_id IF NOT EXISTS
  FOR (w:Workspace) REQUIRE w.id IS UNIQUE;

CREATE CONSTRAINT intent_id IF NOT EXISTS
  FOR (i:Intent) REQUIRE i.id IS UNIQUE;

CREATE CONSTRAINT task_id IF NOT EXISTS
  FOR (t:Task) REQUIRE t.id IS UNIQUE;

CREATE CONSTRAINT daemonconfig_id IF NOT EXISTS
  FOR (dc:DaemonConfig) REQUIRE dc.id IS UNIQUE;

CREATE CONSTRAINT queryconfig_id IF NOT EXISTS
  FOR (qc:QueryConfig) REQUIRE qc.id IS UNIQUE;

CREATE CONSTRAINT systemversion_id IF NOT EXISTS
  FOR (sv:SystemVersion) REQUIRE sv.id IS UNIQUE;

CREATE CONSTRAINT rule_id IF NOT EXISTS
  FOR (r:Rule) REQUIRE r.id IS UNIQUE;

CREATE CONSTRAINT message_id IF NOT EXISTS
  FOR (m:Message) REQUIRE m.id IS UNIQUE;

CREATE CONSTRAINT discussion_id IF NOT EXISTS
  FOR (d:Discussion) REQUIRE d.id IS UNIQUE;

// ──────────────────────────────────────────
// Indexes for frequent queries
// ──────────────────────────────────────────

// Daemon hot-path: find pending Intents
CREATE INDEX intent_lifecycle IF NOT EXISTS
  FOR (i:Intent) ON (i.lifecycle);

// Daemon hot-path: find running agents for health checks
CREATE INDEX agent_lifecycle IF NOT EXISTS
  FOR (a:Agent) ON (a.lifecycle);

// Task assignment queries
CREATE INDEX task_lifecycle IF NOT EXISTS
  FOR (t:Task) ON (t.lifecycle);

CREATE INDEX task_execution_mode IF NOT EXISTS
  FOR (t:Task) ON (t.execution_mode);

CREATE INDEX task_parent_group IF NOT EXISTS
  FOR (t:Task) ON (t.parent_task_id);

CREATE INDEX task_execution_order IF NOT EXISTS
  FOR (t:Task) ON (t.execution_order);

// Capability lookup by kind
CREATE INDEX capability_kind IF NOT EXISTS
  FOR (c:Capability) ON (c.kind);

// Capability lookup by problem domain
CREATE INDEX capability_domain IF NOT EXISTS
  FOR (c:Capability) ON (c.domain);

// Rule lifecycle for loading available rules
CREATE INDEX rule_lifecycle IF NOT EXISTS
  FOR (r:Rule) ON (r.lifecycle);

// Message timestamp for ordering queries
CREATE INDEX message_timestamp IF NOT EXISTS
  FOR (m:Message) ON (m.timestamp);

// Discussion lifecycle
CREATE INDEX discussion_lifecycle IF NOT EXISTS
  FOR (d:Discussion) ON (d.lifecycle);
```


### `seed.cypher`
```cypher
// ═══════════════════════════════════════════════════════════════
// Hassaleh — Seed Data (MVP)
// ═══════════════════════════════════════════════════════════════
//
// Creates the minimum graph needed for Sprint 1:
// 1 Agent, 1 Capability, 1 Workspace, 1 Task, 3 singletons
//
// Reference: docs/CONCEPT.md v1.2
// Created: 2026-03-29 by Dione 🌙

// ──────────────────────────────────────────
// Singletons
// ──────────────────────────────────────────

MERGE (dc:DaemonConfig {id: "default"})
SET dc += {
  tick_interval_ms: 1000,
  sweep_interval_min: 15,
  audit_schedule: "0 3 * * *",
  intent_timeout_default_sec: 300,
  max_concurrent_actions: 10,
  parallel_fail_fast: true,
  circuit_breaker_window_min: 60,
  circuit_breaker_decay_per_sweep: 1,
  notification_channel: "openclaw",
  health_endpoint_port: 9100
};

MERGE (qc:QueryConfig {id: "default"})
SET qc += {
  default_timeout_ms: 3000,
  max_result_rows: 10000,
  max_transaction_memory_mb: 1024,
  report_query_timeout_ms: 30000
};

MERGE (sv:SystemVersion {id: "hassaleh"})
SET sv += {
  schema_version: "1.2",
  last_migration: datetime({timezone: 'UTC'}),
  compatible_daemon_versions: ["1.2"]
};

// ──────────────────────────────────────────
// Workspace
// ──────────────────────────────────────────

MERGE (ws:Workspace {id: "hassaleh-workspace"})
SET ws += {
  path: "/home/uranus/moltbot-workspace/projects/hassaleh",
  os: "ubuntu",
  writable: true,
  description: "Hassaleh project workspace",
  allowed_domains: null
};

// ──────────────────────────────────────────
// Agent: Dione (first managed agent)
// ──────────────────────────────────────────

MERGE (a:Agent {id: "dione"})
SET a += {
  name: "Dione",
  emoji: "🌙",
  description: "Finance agent — portfolio monitoring, market analysis, reporting. Sprint 1 lead for Hassaleh.",
  personality: "Sachlich-präzise, aber locker und gerne mit Humor",
  runtime: "openclaw",
  lifecycle: "pending",
  last_heartbeat: null,
  os_pid: null,
  credits_remaining: null,
  health_check_interval_sec: 300,
  restart_count_1h: 0,
  max_restarts_1h: 5
};

// Agent → Workspace
MATCH (a:Agent {id: "dione"}), (ws:Workspace {id: "hassaleh-workspace"})
MERGE (a)-[:OPERATES_IN {since: datetime({timezone: 'UTC'})}]->(ws);

// ──────────────────────────────────────────
// Skill Domain Taxonomy
// ──────────────────────────────────────────

MERGE (sd:SkillDomain {id: "system"})
SET sd += {display_name: "System", description: "System-level execution, filesystem, package, and network operations.", parent_domain: null};
MERGE (sd:SkillDomain {id: "data"})
SET sd += {display_name: "Data", description: "Graph, ETL, and query operations.", parent_domain: null};
MERGE (sd:SkillDomain {id: "orchestration"})
SET sd += {display_name: "Orchestration", description: "Rule, task, and agent orchestration.", parent_domain: null};
MERGE (sd:SkillDomain {id: "reporting"})
SET sd += {display_name: "Reporting", description: "Activity and metrics reporting.", parent_domain: null};
MERGE (sd:SkillDomain {id: "security"})
SET sd += {display_name: "Security", description: "Security audit and secret management.", parent_domain: null};
MERGE (sd:SkillDomain {id: "communication"})
SET sd += {display_name: "Communication", description: "Notification and inter-agent messaging.", parent_domain: null};

MERGE (sd:SkillDomain {id: "system.exec"})
SET sd += {display_name: "System Exec", description: "OS command execution.", parent_domain: "system"};
MERGE (sd:SkillDomain {id: "system.fs"})
SET sd += {display_name: "Filesystem", description: "Filesystem operations.", parent_domain: "system"};
MERGE (sd:SkillDomain {id: "system.pkg"})
SET sd += {display_name: "Package Management", description: "Package management.", parent_domain: "system"};
MERGE (sd:SkillDomain {id: "system.net"})
SET sd += {display_name: "Networking", description: "Network operations.", parent_domain: "system"};

MERGE (sd:SkillDomain {id: "data.graph"})
SET sd += {display_name: "Graph Operations", description: "Graph database operations.", parent_domain: "data"};
MERGE (sd:SkillDomain {id: "data.etl"})
SET sd += {display_name: "ETL", description: "Data extraction and transformation.", parent_domain: "data"};
MERGE (sd:SkillDomain {id: "data.query"})
SET sd += {display_name: "Data Query", description: "Data querying.", parent_domain: "data"};

MERGE (sd:SkillDomain {id: "orchestration.rules"})
SET sd += {display_name: "Rules", description: "Rule engine operations.", parent_domain: "orchestration"};
MERGE (sd:SkillDomain {id: "orchestration.tasks"})
SET sd += {display_name: "Tasks", description: "Task management.", parent_domain: "orchestration"};
MERGE (sd:SkillDomain {id: "orchestration.agents"})
SET sd += {display_name: "Agents", description: "Agent lifecycle management.", parent_domain: "orchestration"};

MERGE (sd:SkillDomain {id: "reporting.activity"})
SET sd += {display_name: "Activity Reporting", description: "Activity reports.", parent_domain: "reporting"};
MERGE (sd:SkillDomain {id: "reporting.metrics"})
SET sd += {display_name: "Metrics", description: "Metric collection and analysis.", parent_domain: "reporting"};

MERGE (sd:SkillDomain {id: "security.audit"})
SET sd += {display_name: "Security Audit", description: "Security auditing.", parent_domain: "security"};
MERGE (sd:SkillDomain {id: "security.secrets"})
SET sd += {display_name: "Secrets", description: "Secret management.", parent_domain: "security"};

MERGE (sd:SkillDomain {id: "communication.notify"})
SET sd += {display_name: "Notifications", description: "Notifications and alerts.", parent_domain: "communication"};
MERGE (sd:SkillDomain {id: "communication.msg"})
SET sd += {display_name: "Messaging", description: "Inter-agent messaging.", parent_domain: "communication"};

MATCH (child:SkillDomain {id: "system.exec"}), (parent:SkillDomain {id: "system"})
MERGE (child)-[:BELONGS_TO_DOMAIN]->(parent);
MATCH (child:SkillDomain {id: "system.fs"}), (parent:SkillDomain {id: "system"})
MERGE (child)-[:BELONGS_TO_DOMAIN]->(parent);
MATCH (child:SkillDomain {id: "system.pkg"}), (parent:SkillDomain {id: "system"})
MERGE (child)-[:BELONGS_TO_DOMAIN]->(parent);
MATCH (child:SkillDomain {id: "system.net"}), (parent:SkillDomain {id: "system"})
MERGE (child)-[:BELONGS_TO_DOMAIN]->(parent);
MATCH (child:SkillDomain {id: "data.graph"}), (parent:SkillDomain {id: "data"})
MERGE (child)-[:BELONGS_TO_DOMAIN]->(parent);
MATCH (child:SkillDomain {id: "data.etl"}), (parent:SkillDomain {id: "data"})
MERGE (child)-[:BELONGS_TO_DOMAIN]->(parent);
MATCH (child:SkillDomain {id: "data.query"}), (parent:SkillDomain {id: "data"})
MERGE (child)-[:BELONGS_TO_DOMAIN]->(parent);
MATCH (child:SkillDomain {id: "orchestration.rules"}), (parent:SkillDomain {id: "orchestration"})
MERGE (child)-[:BELONGS_TO_DOMAIN]->(parent);
MATCH (child:SkillDomain {id: "orchestration.tasks"}), (parent:SkillDomain {id: "orchestration"})
MERGE (child)-[:BELONGS_TO_DOMAIN]->(parent);
MATCH (child:SkillDomain {id: "orchestration.agents"}), (parent:SkillDomain {id: "orchestration"})
MERGE (child)-[:BELONGS_TO_DOMAIN]->(parent);
MATCH (child:SkillDomain {id: "reporting.activity"}), (parent:SkillDomain {id: "reporting"})
MERGE (child)-[:BELONGS_TO_DOMAIN]->(parent);
MATCH (child:SkillDomain {id: "reporting.metrics"}), (parent:SkillDomain {id: "reporting"})
MERGE (child)-[:BELONGS_TO_DOMAIN]->(parent);
MATCH (child:SkillDomain {id: "security.audit"}), (parent:SkillDomain {id: "security"})
MERGE (child)-[:BELONGS_TO_DOMAIN]->(parent);
MATCH (child:SkillDomain {id: "security.secrets"}), (parent:SkillDomain {id: "security"})
MERGE (child)-[:BELONGS_TO_DOMAIN]->(parent);
MATCH (child:SkillDomain {id: "communication.notify"}), (parent:SkillDomain {id: "communication"})
MERGE (child)-[:BELONGS_TO_DOMAIN]->(parent);
MATCH (child:SkillDomain {id: "communication.msg"}), (parent:SkillDomain {id: "communication"})
MERGE (child)-[:BELONGS_TO_DOMAIN]->(parent);

// ──────────────────────────────────────────
// Capabilities
// ──────────────────────────────────────────

MERGE (cap:Capability {id: "exec-ls"})
SET cap += {
  name: "List directory contents",
  kind: "cli",
  description: "Execute ls command to list files in a directory",
  domain: "system.exec",
  invoke_command: "/usr/bin/ls",
  invoke_params_schema: '{"args": "string"}',
  version: null,
  source: null,
  exec_as_user: "hassaleh-fs",
  requires_auth: false,
  requires_confirmation: false,
  rate_limit: null,
  cost_model: "free",
  lifecycle: "available"
};

MERGE (cap:Capability {id: "graph-query"})
SET cap += {
  name: "Graph Query",
  kind: "cypher",
  description: "Execute read-only Cypher queries",
  domain: "data.graph",
  invoke_command: "hassaleh.graph_query",
  invoke_params_schema: '{"query": "string"}',
  version: "1.0",
  source: "builtin",
  exec_as_user: "hassaleh-daemon",
  requires_auth: false,
  requires_confirmation: false,
  rate_limit: null,
  cost_model: "free",
  lifecycle: "available"
};

MERGE (cap:Capability {id: "send-notification"})
SET cap += {
  name: "Send Notification",
  kind: "api",
  description: "Send notifications via OpenClaw",
  domain: "communication.notify",
  invoke_command: "hassaleh.send_notification",
  invoke_params_schema: '{"text": "string"}',
  version: "1.0",
  source: "builtin",
  exec_as_user: "hassaleh-daemon",
  requires_auth: false,
  requires_confirmation: false,
  rate_limit: null,
  cost_model: "free",
  lifecycle: "available"
};

MERGE (cap:Capability {id: "list-agents"})
SET cap += {
  name: "List Agents",
  kind: "cypher",
  description: "List agent status",
  domain: "orchestration.agents",
  invoke_command: "hassaleh.list_agents",
  invoke_params_schema: '{"filter": "string"}',
  version: "1.0",
  source: "builtin",
  exec_as_user: "hassaleh-daemon",
  requires_auth: false,
  requires_confirmation: false,
  rate_limit: null,
  cost_model: "free",
  lifecycle: "available"
};

MERGE (cap:Capability {id: "security-audit"})
SET cap += {
  name: "Security Audit",
  kind: "cli",
  description: "Run security audit script",
  domain: "security.audit",
  invoke_command: "/usr/bin/env",
  invoke_params_schema: '{"args": "string"}',
  version: "1.0",
  source: "builtin",
  exec_as_user: "hassaleh-audit",
  requires_auth: false,
  requires_confirmation: true,
  rate_limit: null,
  cost_model: "free",
  lifecycle: "available"
};

MERGE (cap:Capability {id: "file-read"})
SET cap += {
  name: "File Read",
  kind: "cli",
  description: "Read file contents",
  domain: "system.fs",
  invoke_command: "/usr/bin/cat",
  invoke_params_schema: '{"args": "string"}',
  version: "1.0",
  source: "builtin",
  exec_as_user: "hassaleh-fs",
  requires_auth: false,
  requires_confirmation: false,
  rate_limit: null,
  cost_model: "free",
  lifecycle: "available"
};

// Agent → Capability
MATCH (a:Agent {id: "dione"}), (cap:Capability {id: "exec-ls"})
MERGE (a)-[:HAS_CAPABILITY {granted_at: datetime({timezone: 'UTC'})}]->(cap);

MATCH (a:Agent {id: "dione"}), (cap:Capability {id: "graph-query"})
MERGE (a)-[:HAS_CAPABILITY {granted_at: datetime({timezone: 'UTC'})}]->(cap);

MATCH (a:Agent {id: "dione"}), (cap:Capability {id: "send-notification"})
MERGE (a)-[:HAS_CAPABILITY {granted_at: datetime({timezone: 'UTC'})}]->(cap);

MATCH (a:Agent {id: "dione"}), (cap:Capability {id: "list-agents"})
MERGE (a)-[:HAS_CAPABILITY {granted_at: datetime({timezone: 'UTC'})}]->(cap);

MATCH (a:Agent {id: "dione"}), (cap:Capability {id: "security-audit"})
MERGE (a)-[:HAS_CAPABILITY {granted_at: datetime({timezone: 'UTC'})}]->(cap);

MATCH (a:Agent {id: "dione"}), (cap:Capability {id: "file-read"})
MERGE (a)-[:HAS_CAPABILITY {granted_at: datetime({timezone: 'UTC'})}]->(cap);

MATCH (cap:Capability {id: "exec-ls"}), (sd:SkillDomain {id: "system.exec"})
MERGE (cap)-[:IN_DOMAIN]->(sd);

MATCH (cap:Capability {id: "graph-query"}), (sd:SkillDomain {id: "data.graph"})
MERGE (cap)-[:IN_DOMAIN]->(sd);

MATCH (cap:Capability {id: "send-notification"}), (sd:SkillDomain {id: "communication.notify"})
MERGE (cap)-[:IN_DOMAIN]->(sd);

MATCH (cap:Capability {id: "list-agents"}), (sd:SkillDomain {id: "orchestration.agents"})
MERGE (cap)-[:IN_DOMAIN]->(sd);

MATCH (cap:Capability {id: "security-audit"}), (sd:SkillDomain {id: "security.audit"})
MERGE (cap)-[:IN_DOMAIN]->(sd);

MATCH (cap:Capability {id: "file-read"}), (sd:SkillDomain {id: "system.fs"})
MERGE (cap)-[:IN_DOMAIN]->(sd);

// ──────────────────────────────────────────
// Task: MVP test task
// ──────────────────────────────────────────

MERGE (t:Task {id: "mvp-test-ls"})
SET t += {
  name: "MVP Test: Execute ls and verify output",
  description: "Agent claims this task, submits an Intent to execute ls -la on the workspace, reads the result from the Intent feedback.",
  lifecycle: "pending",
  execution_mode: "sequential",
  execution_order: 1,
  parent_task_id: null,
  completed_at: null,
  expires_at: datetime({timezone: 'UTC'}) + duration('P7D'),
  verification_method: "Intent lifecycle == success AND stdout contains schema.cypher",
  idempotency_key: null
};

// Task → Agent (assigned)
MATCH (t:Task {id: "mvp-test-ls"}), (a:Agent {id: "dione"})
MERGE (t)-[:ASSIGNED_TO]->(a);
```


### `seed_rules.cypher`
```cypher
// ═══════════════════════════════════════════════════════════════
// Hassaleh — Seed Rules (Sprint 2)
// ═══════════════════════════════════════════════════════════════
//
// Operational rules for agent orchestration.
// Apply after schema.cypher and seed.cypher.

// ──────────────────────────────────────────
// Rule 1: Agent Health Check
// ──────────────────────────────────────────
// Checks running agents for stale heartbeats.
// Restarts up to 5 times, then circuit-breaks.

MERGE (r1:Rule {id: "agent-health-check"})
SET r1 += {
  name: "Agent Health Check",
  version: 1,
  category: "operations",
  priority: 10,
  lifecycle: "available",
  description: "Check agent health every 5 min. Restart if unresponsive. Circuit breaker after 5 failures.",
  author: "dione",
  rule_text: 'EVERY "PT5M":
    MATCH (a:Agent {lifecycle: \'running\'}):
        IF a.last_heartbeat < NOW() - DURATION("PT5M"):
            IF a.restart_count_1h < 5:
                a.restart_count_1h += 1
                SUBMIT_INTENT "restart_agent" ON a
                LOG "Restarting unresponsive agent" LEVEL "warning"
            ELSE:
                a.lifecycle = "circuit_broken"
                ALERT "Circuit breaker: agent exceeded restart limit" ON a
                LOG "Circuit breaker triggered" LEVEL "error"'
};

// ──────────────────────────────────────────
// Rule 2: Task Timeout Sweep
// ──────────────────────────────────────────
// Fails tasks that have exceeded their expiry time.

MERGE (r2:Rule {id: "task-timeout-sweep"})
SET r2 += {
  name: "Task Timeout Sweep",
  version: 1,
  category: "operations",
  priority: 20,
  lifecycle: "available",
  description: "Fail tasks that have exceeded their expires_at time.",
  author: "dione",
  rule_text: 'EVERY "PT15M":
    MATCH (t:Task {lifecycle: \'running\'}):
        IF t.expires_at < NOW():
            t.lifecycle = "failed"
            t.error_reason = "Task timed out"
            LOG "Task timed out" LEVEL "warning"'
};

// ──────────────────────────────────────────
// Rule 3: Task Assignment
// ──────────────────────────────────────────
// Assigns pending tasks to agents whose capabilities match the task's
// required capability edge. The assign_task intent is idempotent and
// will no-op if the task is already assigned.

MERGE (r3:Rule {id: "task-assignment"})
SET r3 += {
  name: "Task Assignment",
  version: 1,
  category: "operations",
  priority: 30,
  lifecycle: "available",
  description: "Assign pending unclaimed tasks to agents with matching capabilities.",
  author: "dione",
  rule_text: 'EVERY "PT1M":
    MATCH (t:Task {lifecycle: \'pending\'})-[:REQUIRES_CAPABILITY]->(c:Capability)<-[:HAS_CAPABILITY]-(a:Agent):
        SUBMIT_INTENT "assign-task" ON a WITH {task_id: t.id}
        LOG "Queued task assignment" LEVEL "info"'
};

// ──────────────────────────────────────────
// Rule 4: Bulk Reset Failed Tasks
// ──────────────────────────────────────────
// Re-queues retryable failed tasks in a batch sweep.

MERGE (r4:Rule {id: "bulk-reset-failed-tasks"})
SET r4 += {
  name: "Bulk Reset Failed Tasks",
  version: 1,
  category: "operations",
  priority: 25,
  lifecycle: "available",
  description: "Reset retryable failed tasks back to pending during scheduled sweeps.",
  author: "dione",
  rule_text: 'EVERY "PT10M":
    MATCH (t:Task {lifecycle: \'failed\'}):
        FOREACH reset_state IN ["pending"]:
            IF t.retry_count < 3 && CONTAINS(["timeout", "crash", "oom"], t.error_reason):
                t.lifecycle = reset_state
                t.retry_count += 1
                LOG "Bulk reset failed task" LEVEL "warning"'
};

// ──────────────────────────────────────────
// Rule 5: Capability Alert
// ──────────────────────────────────────────
// Alerts when an assigned agent does not have the task's required capability.

MERGE (r5:Rule {id: "capability-alert"})
SET r5 += {
  name: "Capability Alert",
  version: 1,
  category: "operations",
  priority: 15,
  lifecycle: "available",
  description: "Notify when an assigned task requires a capability the agent does not have.",
  author: "dione",
  rule_text: 'EVERY "PT5M":
    MATCH (t:Task)-[:ASSIGNED_TO]->(a:Agent), (t)-[:REQUIRES_CAPABILITY]->(c:Capability) WHERE NOT (a)-[:HAS_CAPABILITY]->(c):
        ALERT "Assigned task requires missing capability" ON a
        LOG "Capability mismatch detected" LEVEL "error"'
};
```


### `docs/CONCEPT.md`
```md
# Hassaleh — Concept Document

*Created: 2026-03-29 by Ingo Giebel + Dione 🌙*
*Revised: 2026-03-29 — v0.2: Gemini Deep Think review incorporated*
*Revised: 2026-03-29 — v0.3: Daemon enforcement model, DB access separation, CronJob-driven rule evaluation*
*Revised: 2026-03-29 — v0.4: Continuous async Daemon, Intent feedback, workspace fast-path, all config in DB*
*Revised: 2026-03-29 — v0.5: Third Gemini DT review: async subprocess, zombie recovery, HITL, SDK safety, missing features*
*Revised: 2026-03-29 — v1.0: Final review: Capability merge, consistency fixes, graceful shutdown, UTC mandate, Docker dev-env*
*Revised: 2026-03-29 — v1.1: Consistency audit: 15 fixes (renumbering, cross-refs, terminology, datetime UTC, relationships)*
*Revised: 2026-03-29 — v1.2: Final 8 fixes from audit + version renaming*
*Revised: 2026-03-31 — v1.3: GSL-Ops language reference (FOREACH, lists, functions), OpenClaw Bridge, conflict resolution details*
*Status: v1.3 — Sprint 4 complete*

---

## 1. Vision

Hassaleh is a **graph-native agentic framework** where the entire configuration, state, and coordination of AI agents is stored in a **Neo4j graph database**.

A capable AI agent (e.g. Claude Opus, steered via OpenClaw) can read the graph to:
- Generate reports on agent activity and project progress
- Understand the full system topology (who does what, with which capabilities, on which projects)
- Audit rule execution and agent decisions

The graph is the **single source of truth** — no YAML files, no scattered configs, no hidden state.

---

## 2. Core Principles

1. **The graph IS the runtime configuration** — Agents, capabilities, schedules, and rules live as nodes and edges in Neo4j.
2. **Rule-based orchestration** — Agent startup, task assignment, failure recovery, and synchronization are governed by declarative rules (GSL-Ops), not imperative code.
3. **Database-mediated communication** — Agents exchange information through the graph, not through direct messaging. Every contribution and consensus is persisted.
4. **Trusted Daemon architecture** — Agents never hold Neo4j write credentials or raw shell access. The Hassaleh Daemon mediates all write operations (database + system) and enforces permissions via OS-level user separation. Agents receive read-only database credentials for direct graph queries.
5. **Auditable by design** — Every agent action, decision, and discussion is logged as graph nodes. A reporting agent can reconstruct the full history.
6. **Fail-safe operation** — Rules define what happens when an agent fails, runs out of credits, or becomes unresponsive. Circuit breakers and exponential backoff prevent crash loops.
7. **Edge-first modeling** — Relationships between entities are always explicit edges, never string-based foreign keys. If it's a reference, it's an edge.
8. **Ubuntu-first** — Initial platform support is Ubuntu Linux. Other platforms may follow.

---

## 3. Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│  Agents (Dione, Inanna, Codex, Claude Code, ...)    │
│  • Run as unprivileged OS user (hassaleh-agent)     │
│  • Neo4j READ-ONLY access (user: hassaleh_reader)   │
│  • Direct r/w to assigned Workspace directories     │
│  • Submit intents via Daemon API for privileged ops  │
│  • Query graph via hassaleh.query() SDK (read-only)  │
└─────────┬───────────────────────┬───────────────────┘
          │ Intent API            │ Read-Proxy SDK
          │ (write requests)      │ (guarded read-only)
          ▼                       ▼
┌─────────────────────┐  ┌───────────────────────────┐
│  Hassaleh Daemon    │  │  Neo4j Graph Database      │
│  (systemd service)  │  │                             │
│                     │  │  Two database users:        │
│  • Persistent async │  │  • hassaleh_daemon (r/w)    │
│    event loop       │  │  • hassaleh_reader (r/o)    │
│    (1-second ticks) │  │                             │
│  • Runs as          │  │  ALL configuration stored   │
│    hassaleh-svc     │──│  here (no external configs) │
│  • Neo4j r/w access │  │                             │
│  • Compiles GSL-Ops │  │  ACID transactions           │
│    rules on boot +  │  │  Single source of truth      │
│    on-change        │  └───────────────────────────┘
│  • Async action     │
│    workers (never   │
│    blocks on I/O)   │
│  • Intent feedback  │
│    (stdout/stderr)  │
│  • SecretRef        │
│    resolution       │
└─────────────────────┘
```

### 3.1 OS-Level Enforcement

The Daemon does not merely *describe* permissions — it **enforces** them at the operating system level:

| Component | OS User | Neo4j User | Capabilities |
|-----------|---------|------------|-------------|
| **Hassaleh Daemon** | `hassaleh-svc` | `hassaleh_daemon` (read/write) | Full DB writes, rule evaluation, agent lifecycle, delegates capability execution to per-capability OS users |
| **AI Agents** | `hassaleh-agent` | `hassaleh_reader` (read-only) | Direct read-only graph queries, direct r/w to assigned Workspaces, submit Intents to Daemon API |
| **Capability Users** | `hassaleh-fs`, `hassaleh-writer`, `hassaleh-exec`, `hassaleh-net`, `hassaleh-pkg` | — | Per-capability-class OS users with minimal permissions (invoked by Daemon via `sudo -n -u`) |
| **Human Admin** | user account (e.g. `uranus`) | `neo4j` (admin) | Full DB access, Daemon management, manual overrides |

The `hassaleh-agent` OS user has no `sudo`, no write access outside assigned Workspace directories, and no ability to start privileged processes. All privileged operations are proxied through the Daemon, which validates `[:HAS_CAPABILITY]` edges and then executes via `sudo -n -u` to the appropriate capability-specific OS user.

### 3.2 Daemon Execution Model

The Daemon is a **persistent asyncio service** managed by systemd, with a fast internal event loop:

**Hot Path (1-second ticks):**
1. Read pending Intents submitted by agents
2. Validate each Intent against Capability permissions
3. For approved Intents: execute via async action workers (never block the loop)
4. Write Intent results (stdout, stderr, error_reason) back to the Intent node
5. Evaluate GSL-Ops rules against current graph state
6. Check for triggered events (new Messages, Milestone completions, etc.)
7. Dispatch notifications to affected agents

**Background Tasks (periodic, run inside the same Daemon process):**

| Task | Interval | Purpose |
|------|----------|---------|
| `sweep` | Every 15 min | Archive expired TimeBuckets. Clean up stale Memory nodes. Decay circuit breaker counters (subtract `circuit_breaker_decay_per_sweep` from `restart_count_1h`, creating a rolling recovery window). |
| `audit` | Daily | Generate daily activity summary. Check rule consistency. Verify graph integrity. |

**Key design decisions:**
- **Never blocks on I/O:** System actions use `asyncio.create_subprocess_exec()` (not `subprocess.run`!). The worker `await`s `process.communicate()`, yielding control back to the tick loop while the OS works. The Daemon updates the Intent to `running` and immediately returns to process other agents.
- **Zombie Intent recovery:** On boot, the Daemon queries for all Intents with `lifecycle: "running"` and transitions them to `failed` with `error_reason: "Daemon restarted during execution"`. This allows agents to detect the failure and cleanly retry.
- **Rules compiled once:** GSL-Ops rules are compiled to Python on Daemon boot (from `compiled_python` cache or fresh from `rule_text`) and recompiled only when Rule nodes are modified. No per-tick recompilation.
- **Managed by systemd:** Automatic restart on crash, journald logging, resource limits via cgroup. The Daemon pings the systemd watchdog (`sd_notify`) on every tick — if the asyncio loop hangs, systemd automatically restarts the service.
- **Health endpoint:** The Daemon exposes a minimal HTTP health endpoint (configurable port in DaemonConfig) for monitoring tools (uptime, tick count, pending intents, active workers).
- **All configuration from the graph:** The Daemon reads its own configuration (tick interval, sweep schedule, timeout defaults) from a `(:DaemonConfig)` singleton node in Neo4j. No external config files.
- **Graceful shutdown:** On SIGTERM/SIGINT, the Daemon stops pulling new Intents, sends SIGTERM to all active subprocess workers, awaits their exit (up to 30s timeout), writes `failed` with `error_reason: "Daemon shutdown"` to any still-running Intents, then exits cleanly. This prevents orphaned background processes.
- **Neo4j read-only fallback:** If Neo4j enters read-only mode (e.g., disk full), the Daemon catches `TransientError` exceptions, pauses the hot-path, and emits a loud alert rather than crash-looping.
- **UTC everywhere:** All timestamps in Python use `datetime.now(datetime.UTC)`. All Cypher uses `datetime({timezone: 'UTC'})`. No local timezone assumptions.

### 3.3 Agent Read Access

Agents query the graph through the **`hassaleh.query()` SDK** — a lightweight Python library that:
- Connects to Neo4j as `hassaleh_reader` (read-only)
- **Forces parameterized queries** — agents pass query templates + parameters, never raw string concatenation. This prevents Cypher injection.
- Applies **per-query timeouts** (loaded from `(:QueryConfig)` node in the graph, passed with each query call)
- Enforces **result-set limits** (configurable per query type, stored in DB)
- Provides convenience methods for common queries (`hassaleh.my_tasks()`, `hassaleh.project_status()`, etc.)
- Provides `hassaleh.submit_intent()` for submitting write requests to the Daemon

**Credential bootstrapping:** When the Daemon (or systemd) spawns an agent process, it injects `NEO4J_URI`, `NEO4J_USER=hassaleh_reader`, and `NEO4J_PASSWORD` as **environment variables**. Agents never query the graph for their own DB credentials.

**Security model:** No regex-based Cypher linting. The `hassaleh_reader` Neo4j role physically cannot write, create, or delete. Combined with per-query timeouts and memory limits, this provides robust protection without brittle pattern matching.

**What agents can read:**
- Project status, task assignments, sprint progress
- Messages and Discussion threads
- Their own health state and model assignments
- All graph data (shared knowledge — "swarm brain")
- Intent results (stdout, stderr, error_reason)

**What agents cannot do:**
- Write, create, update, or delete any nodes or relationships
- Access actual secrets (SecretRef nodes contain only vault references)
- Run queries that exceed the configured timeout or memory limits

**Query guardrails are stored in the graph** (not in neo4j.conf):
```
(:QueryConfig {
    id: "default",
    default_timeout_ms: 3000,          # per-query timeout (passed with each call)
    max_result_rows: 10000,            # hard limit on returned rows
    max_transaction_memory_mb: 1024,   # per-transaction memory limit
    report_query_timeout_ms: 30000     # longer timeout for reporting queries
})
```

This avoids a single global DBMS timeout that would be either too strict for reports or too lenient for routine queries.

---

## 4. Node Types

### 4.1 Model

An AI model that can be instantiated to power an agent.

```
(:Model {
    id: "claude-opus-4-6",
    name: "Claude Opus 4.6",
    provider: "anthropic",
    type: "llm",                        # llm | embedding | vision | tts | stt
    context_window: 200000,
    supports_tools: true,
    supports_vision: true,
    
    # Instantiation methods (how to start this model)
    invoke_cli: "claude --model opus ...",
    invoke_adk: "anthropic.messages.create(...)",
    invoke_openclaw: "sessions_spawn runtime=acp ...",
    
    # Operational
    cost_per_mtok_input: 15.0,
    cost_per_mtok_output: 75.0,
    rate_limit_rpm: 50,
    auth_method: "oauth",               # oauth | api_key | local
    lifecycle: "available"              # → universal LifecycleStatus
})
```

**Relationships:**
```
(Model)-[:AUTHENTICATES_VIA]->(SecretRef)
```

**Secrets:** Model authentication credentials are **never stored in the graph**. See SecretRef (4.13).

### 4.2 Agent

An AI agent with defined capabilities. Each agent has one or more models assigned (with priority for fallback).

```
(:Agent {
    id: "dione",
    name: "Dione",
    emoji: "🌙",
    description: "Finance agent — portfolio monitoring, market analysis, reporting",
    personality: "Sachlich-präzise, aber locker und gerne mit Humor",
    
    # Runtime
    runtime: "openclaw",                # openclaw | adk | standalone | docker
    
    # State
    lifecycle: "running",               # → universal LifecycleStatus
    last_heartbeat: datetime({timezone: "UTC"}),
    os_pid: 12345,                      # Linux PID for process liveness verification
    credits_remaining: null,            # null = unlimited (subscription)
    health_check_interval_sec: 300,
    restart_count_1h: 0,                # circuit breaker tracking
    max_restarts_1h: 5                  # suspend after this many
})
```

**Relationships:**
```
(Agent)-[:USES_MODEL {priority: 1, fallback: false}]->(Model)
(Agent)-[:USES_MODEL {priority: 2, fallback: true}]->(Model)
(Agent)-[:OPERATES_IN]->(Workspace)
(Agent)-[:HAS_CAPABILITY]->(Capability) # tools, skills, system actions — all unified
(Agent)-[:LAST_READ]->(Message)         # cursor for message queue
```

### 4.3 Capability

A unified abstraction for anything an agent can do: MCP server calls, CLI tools, skills, filesystem operations, network access, package management. From the Daemon's perspective, they are all **parameterized system executions**.

```
(:Capability {
    id: "firecrawl-search",
    name: "Firecrawl Search",
    kind: "mcp_server",                 # mcp_server | cli | api | skill | filesystem | network | package
    description: "Web search with full page content extraction via Firecrawl API",
    
    # Invocation (executed by Daemon, not agent)
    invoke_command: "mcporter call firecrawl.firecrawl_search",
    invoke_params_schema: '{"query": "string", "limit": "int"}',
    
    # Skill metadata (for kind: "skill")
    version: null,                      # e.g. "1.0.0" for skills
    source: null,                       # clawhub | local | github
    
    # OS execution context
    exec_as_user: "hassaleh-net",       # dedicated OS user for this capability
    requires_auth: true,
    requires_confirmation: false,       # true → Intent goes to awaiting_approval
    
    # Operational constraints
    rate_limit: "500 credits/month",
    cost_model: "per_call",             # per_call | free | subscription
    
    lifecycle: "available"              # → LifecycleStatus (available | deprecated)
})
```

**Per-capability OS user isolation:** Each Capability specifies an `exec_as_user` — a dedicated Ubuntu user with **only** the permissions needed for this capability class. The Daemon executes via `asyncio.create_subprocess_exec("sudo", "-n", "-u", exec_as_user, ...)`:

| Capability Kind | OS User | Permissions |
|----------------|---------|------------|
| `filesystem` (read) | `hassaleh-fs` | Read-only access to workspace dirs |
| `filesystem` (write) | `hassaleh-writer` | Write access to specific output dirs |
| `cli` / `skill` (scripts) | `hassaleh-exec` | Execute scripts in whitelisted paths |
| `mcp_server` / `api` / `network` | `hassaleh-net` | Outbound HTTP only, no listeners |
| `package` | `hassaleh-pkg` | `apt` with restricted package list |

**Safe async subprocess execution:** The Daemon **never** uses `shell=True`, `subprocess.run`, or string concatenation. All delegated actions use `asyncio.create_subprocess_exec` with strict argument arrays:
```python
proc = await asyncio.create_subprocess_exec(
    "sudo", "-n", "-u", exec_as_user,
    "/path/to/script", safe_arg1, safe_arg2,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
)
stdout, stderr = await proc.communicate()
```

**Relationships:**
```
(Agent)-[:HAS_CAPABILITY {granted_at: datetime({timezone: "UTC"})}]->(Capability)
(Capability)-[:CONFIGURED_IN]->(ConfigFile)
(Capability)-[:AUTHENTICATES_VIA]->(SecretRef)
(Capability)-[:LOCATED_AT]->(Artifact)    # for skills: SKILL.md file
(Capability)-[:DEPENDS_ON]->(Capability)  # skill depends on MCP server, etc.
(Project)-[:REQUIRES_CAPABILITY]->(Capability)
```

### 4.4 Workspace

A filesystem directory where agents and projects operate. Agents have **direct OS-level read/write access** to their assigned Workspace directories (no need to route file I/O through the Daemon).

```
(:Workspace {
    id: "gww3-workspace",
    path: "/home/uranus/moltbot-workspace/projects/games-of-ww3",
    os: "ubuntu",
    writable: true,                     # agents get r/w access
    description: "GWW3 project workspace"
})
```

Workspaces are **project-specific**. An agent working on GWW3 gets access to the GWW3 workspace, not the entire filesystem. The `hassaleh-agent` OS user is granted group-level r/w access to assigned workspace directories.

**Relationships:**
```
(Agent)-[:OPERATES_IN {since: datetime({timezone: "UTC"})}]->(Workspace)
(Project)-[:LOCATED_IN]->(Workspace)
```

**Access enforcement:** When the Daemon assigns an agent to a project, it ensures the `hassaleh-agent` user has OS-level group permissions on that project's Workspace directory. When unassigned, access is revoked.

### 4.5 ConfigFile

A configuration file referenced by capabilities or agents.

```
(:ConfigFile {
    id: "mcporter-config",
    path: "config/mcporter.json",
    format: "json",
    description: "MCP server configuration for mcporter"
})
```

**Relationships:** None (referenced by Capability via `[:CONFIGURED_IN]`).

### 4.6 CronJob

A scheduled recurring or one-shot task.

```
(:CronJob {
    id: "daily-maintenance",
    name: "Daily System Maintenance",
    schedule: "0 4 * * *",
    timezone: "Europe/Berlin",
    command: "bash scripts/daily-maintenance.sh",
    
    enabled: true,
    last_run: datetime({timezone: "UTC"}),
    lifecycle: "success",               # last run status → universal LifecycleStatus
    next_run: datetime({timezone: "UTC"}),
    retry_on_failure: true,
    max_retries: 3
})
```

**Relationships:**
```
(CronJob)-[:EXECUTED_BY]->(Agent)
(CronJob)-[:BELONGS_TO]->(Project)
(CronJob)-[:TRIGGERS]->(CronJob)
```

### 4.7 Project

The central organizing node. Projects form a **hierarchy** (parent/child) and can represent anything from a long-running endeavor to a recurring task.

```
(:Project {
    id: "gww3",
    name: "Games of World War 3",
    description: "AI-agent-based geopolitical real-time simulation...",
    type: "development",               # development | operations | recurring | research
    
    # Timeline
    started_at: datetime({timezone: "UTC"}),
    target_date: null,
    lifecycle: "running",              # → universal LifecycleStatus
    priority: "high"                   # critical | high | medium | low
})
```

**Relationships:**
```
(Project)-[:HAS_SUBPROJECT]->(Project)
(Project)-[:DEPENDS_ON]->(Project)
(Project)-[:HAS_AGENT {role: "orchestrator"}]->(Agent)
(Project)-[:HAS_AGENT {role: "developer"}]->(Agent)
(Project)-[:HAS_AGENT {role: "reviewer"}]->(Agent)
(Project)-[:REQUIRES_CAPABILITY]->(Capability)
(Project)-[:HAS_CRONJOB]->(CronJob)
(Project)-[:HAS_SPRINT]->(Sprint)
(Project)-[:HAS_ARTIFACT]->(Artifact)
(Project)-[:GOVERNED_BY]->(Rule)
(Project)-[:LOCATED_IN]->(Workspace)
(Project)-[:HAS_REPO {url: "https://github.com/..."}]->(Artifact)
```

### 4.8 Sprint

A time-boxed work phase within a project.

```
(:Sprint {
    id: "gww3-sprint-4",
    name: "Sprint 4: Rules Engine (GSL)",
    description: "Build the GSL parser, evaluator, and intent reducer",
    
    started_at: datetime({timezone: "UTC"}),
    target_date: date("2026-04-15"),
    lifecycle: "running"
})
```

**Relationships:**
```
(Sprint)-[:HAS_TASK]->(Task)
(Sprint)-[:HAS_MILESTONE]->(Milestone)
(Sprint)-[:NEXT]->(Sprint)
```

### 4.9 Task

A concrete unit of work within a sprint.

```
(:Task {
    id: "gww3-s4-parser-v3",
    name: "GSL Parser v3 — Python-style colon blocks",
    description: "Rewrite .lark grammar with MATCH …: / IF …: syntax",
    
    lifecycle: "success",
    completed_at: datetime({timezone: "UTC"}),
    expires_at: datetime({timezone: "UTC"}),             # timeout — auto-reset if exceeded
    verification_method: "pytest tests/engine/test_gsl_parser.py",
    idempotency_key: null               # for external side effects
})
```

**Relationships:**
```
(Task)-[:ASSIGNED_TO]->(Agent)
(Task)-[:DEPENDS_ON]->(Task)
(Task)-[:HAS_MEMORY]->(Memory)          # agent scratchpad for this task
```

### 4.10 Milestone

A checkpoint with verifiable acceptance criteria.

```
(:Milestone {
    id: "gww3-s4-parser-passes",
    name: "GSL parser passes all 26 tests",
    check_command: "PYTHONPATH=src python3.13 -m pytest tests/engine/ -q",
    check_expected: "26 passed",
    
    lifecycle: "success",
    reached_at: datetime({timezone: 'UTC'})
})
```

**Relationships:** None (referenced by Sprint via `[:HAS_MILESTONE]`).

### 4.11 Artifact

A file, directory, or external resource linked to a project.

```
(:Artifact {
    id: "gww3-gsl-grammar",
    name: "GSL Grammar v3",
    type: "file",                      # file | directory | database | url | docker_image
    path: "src/gww3/engine/parser/gsl.lark",
    description: "Lark EBNF grammar for the GSL rule language"
})
```

**Relationships:** None (referenced by Capability via `[:LOCATED_AT]`, Project via `[:HAS_ARTIFACT]`, Message via `[:REFERENCES]`).

### 4.12 Memory

Ephemeral working memory for agents during task execution. Separated from the audit trail.

```
(:Memory {
    id: uuid(),
    created_at: datetime({timezone: "UTC"}),
    content: '{"step": 3, "intermediate_results": [...]}',
    ttl_hours: 72                       # auto-expire after N hours
})
```

**Relationships:**
```
(Task)-[:HAS_MEMORY]->(Memory)
(Agent)-[:OWNS_MEMORY]->(Memory)
```

### 4.13 SecretRef

A reference to a secret stored **outside** the graph (environment variable, vault, encrypted file). The Hassaleh Daemon resolves these at runtime.

```
(:SecretRef {
    id: "anthropic-api-key",
    vault_type: "env",                 # env | file | vault | 1password
    vault_key: "ANTHROPIC_API_KEY",
    description: "Anthropic API key for Claude models"
})
```

**The graph never contains actual secrets.** Only references.

**Relationships:**
```
(Model)-[:AUTHENTICATES_VIA]->(SecretRef)
(Capability)-[:AUTHENTICATES_VIA]->(SecretRef)
```

### 4.14 Rule

A declarative rule governing agent behavior within a project. Uses GSL-Ops (deterministic subset of GSL).

```
(:Rule {
    id: "agent-health-check",
    version: 1,
    category: ["operations"],
    priority: 10,                      # lower = higher priority (for conflict resolution)
    
    rule_text: "...",                  # GSL-Ops source (compiled by Daemon at runtime)
    
    description: "Check agent health every 5 min. Restart if unresponsive. Circuit breaker after 5 failures.",
    author: "ingo",
    lifecycle: "available"
})
```

**Compilation:** The Daemon compiles `rule_text` → `compiled_python` on boot and whenever a Rule node is modified, then writes the result back to the Rule node. At runtime, the Daemon executes the cached `compiled_python` directly — no per-tick recompilation.

```
(:Rule {
    ...
    rule_text: "MATCH (a:Agent):\n    ...",      # GSL-Ops source (authoritative)
    compiled_python: "def evaluate(ctx):\n ...",  # cached compiled output
    compiled_at: datetime({timezone: "UTC"}),
    compiler_version: "gsl-ops-0.4",
    ...
})
```

**Security:** Since agents only have read-only DB access (`hassaleh_reader`), they cannot inject malicious rule text or tamper with compiled code. Only the human admin (via `neo4j` user) can create or modify Rule nodes. The Daemon validates that `compiler_version` matches its own version before executing — stale compiled code triggers automatic recompilation from `rule_text`.

**Conflict resolution:** When multiple rules target the same property on the same node, the rule with the **lowest priority number wins** (priority 1 overrides priority 10). For additive/modifier operations (numeric), the GWW3-style commutative reducer is used. For absolute state assignments (enums like `lifecycle`), the highest-priority rule wins.

**Resolution order:** SET → ADD/SUB → MUL. SET operations resolve by priority first, then additive modifications are summed (commutative), then multiplicative modifiers scale the result.

**Rule Governance:**

Rules are authored and modified interactively by the responsible human operator, with the agent acting as an intelligent assistant in the process. The key principles:

1. **Human authority:** Only the human admin (via `neo4j` user or `hassaleh rule` CLI) can create, modify, or deactivate Rule nodes. Agents cannot modify rules — they have read-only graph access.
2. **Interactive authoring:** Hassaleh includes a **Rule Authoring Skill** that guides the operator through rule creation in natural language. The agent helps translate operational intent ("restart agents that haven't checked in for 5 minutes, but stop after 5 retries") into valid GSL-Ops syntax, compiles it, and shows the generated Python for review before committing to the graph.
3. **Auditability:** Every Rule node tracks `author`, `version`, `compiled_at`, and `compiler_version`. The `hassaleh rule compile --dry-run` command lets operators inspect generated code without affecting the running system.
4. **Runtime modifiability:** Rules can be updated at any time. The Daemon detects version/compiler mismatches on the next evaluation cycle and automatically recompiles from the authoritative `rule_text`.
5. **HITL safety net:** Capabilities can be marked `requires_confirmation: true`, which parks Intents in `awaiting_approval` state until a human approves them via `hassaleh approve <id>`. This provides a circuit breaker for sensitive operations that rules might trigger.
6. **Graceful override:** Any rule can be deactivated by setting `lifecycle: 'disabled'` without deleting it. The `hassaleh rule list` CLI shows all rules including disabled ones for full transparency.

**Relationships:**
```
(Project)-[:GOVERNED_BY]->(Rule)
(Rule)-[:APPLIES_TO]->(Agent)
```

---

### 4.14.1 GSL-Ops Language Reference

GSL-Ops (Graph Symbolic Logic — Operations) is a deterministic subset of GSL, designed for agent orchestration rules. It compiles to Python and executes in a sandboxed `RuleContext`.

**Origin:** Ported from the GWW3 game engine's GSL language (v3), stripped of distributions, truth values, and randomness.

**Parser:** Lark (Earley) with PythonIndenter for Python-style indentation blocks.

#### Block Statements

```
MATCH (a:Agent {lifecycle: 'running'}):
    # Body executes for each matching row from Neo4j
    LET name = a.name

IF condition:
    # Conditional execution
    ...
ELIF other_condition:
    ...
ELSE:
    ...

EVERY "PT5M":
    # Schedule-triggered block — fires when interval has elapsed since last run
    # Supports ISO 8601 ("PT5M", "P1D") and shorthands (5m, 1h, 30s)
    ...

FOREACH item IN collection:
    # Iterate over a list, property, or function result
    # collection can be: list literal, node property, RANGE(), KEYS()
    ...
```

#### Simple Statements

```
LET x = expression                     # Variable binding

target.property = expression            # SET (direct assignment)
target.property += expression           # ADD (additive)
target.property -= expression           # SUB (subtractive)
target.property *= expression           # MUL (multiplicative)

SUBMIT_INTENT "capability_id" ON target                      # Create Intent
SUBMIT_INTENT "capability_id" ON target WITH {key: value}    # With arguments

LOG "message"                           # Structured log (default: info)
LOG "message" LEVEL "warning"           # With explicit level

ALERT "message"                         # Notification (dispatched via bridge)
ALERT "message" ON target               # With target context
```

#### Expressions

```
# Arithmetic: +, -, *, /, %
# Comparison: ==, !=, >, <, >=, <=, in, not in
# Logical: &&, ||, !, and, or, not
# Literals: 42, 3.14, "string", true, false, null, None
# Property access: node.property
# List literals: [a, b, c], []
# Function calls: NOW(), DURATION("PT5M"), MIN(a, b), MAX(a, b)
#                 ABS(x), CLAMP(x, lo, hi), LEN(x)
#                 STR(x), INT(x), FLOAT(x)
#                 RANGE(start, end), KEYS(node), SORTED(list), LIST(x)
```

#### Example: Agent Health Check with Circuit Breaker

```
EVERY "PT5M":
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
```

#### Example: Bulk Retry Failed Tasks

```
EVERY "PT10M":
    MATCH (t:Task {lifecycle: 'failed'}):
        FOREACH reason IN ["timeout", "crash", "oom"]:
            IF t.error_reason == reason:
                IF t.retry_count < 3:
                    t.lifecycle = "pending"
                    t.retry_count += 1
                    LOG "Retrying task" LEVEL "info"
```

#### Compilation Pipeline

```
GSL-Ops text → Lark parse (Earley + PythonIndenter) → AST
    → GSLOpsCompiler → Python source → compile() → exec(code, safe_builtins)
    → evaluate(ctx: RuleContext)
```

- Compiled Python is cached in the Rule node's `compiled_python` property
- `compiler_version` tracks staleness — version mismatch triggers recompilation
- `exec()` runs in a restricted namespace (no `open`, `eval`, `exec`)
- `ctx.match()` uses Neo4j read-only transactions (`execute_read()`)

---

### 4.14.2 OpenClaw Bridge

The Hassaleh Daemon integrates with an OpenClaw Gateway running on the same host.

**Communication:** HTTP API (localhost, bearer token authentication).

**Capabilities:**
- **Notifications:** Rule ALERT actions are dispatched as Telegram/Discord messages via OpenClaw's messaging infrastructure
- **Session Management:** Daemon can list, spawn, and communicate with OpenClaw agent sessions
- **Wake Events:** Daemon can trigger system events to notify the main agent

**Configuration:** Gateway URL, token, and notification target are stored in DaemonConfig or environment variables (`OPENCLAW_GATEWAY_URL`, `OPENCLAW_GATEWAY_TOKEN`, `HASSALEH_NOTIFY_TARGET`).

**Graceful degradation:** The bridge is optional. If not configured or unreachable, the Daemon operates normally — alerts are logged but not dispatched to messaging channels.

---

### 4.14.3 Built-in Skills

Hassaleh ships with a set of built-in Skills that agents can invoke via their capabilities. Skills are stored as Capability nodes in the graph with `kind: 'skill'` and provide structured interaction patterns.

| Skill | ID | Description |
|-------|-----|-------------|
| **Rules Author** | `hassaleh-rules-author` | Interactive GSL-Ops rule authoring. Guides the operator through translating operational intent into valid rules: clarifies trigger conditions, actions, and safety constraints in natural language, generates GSL-Ops syntax, compiles and previews the generated Python (`--dry-run`), and commits to the graph only after explicit human approval. Default skill for all rule creation requests. |
| **Graph Explorer** | `hassaleh-graph-explorer` | Interactive graph querying and visualization. Translates natural language questions ("which agents have capabilities for file access?") into Cypher, executes read-only, and formats results. |
| **Report Builder** | `hassaleh-report-builder` | Generates structured reports from graph data. Supports agent activity, rule evaluation, intent statistics, and project progress reports in text, JSON, or Markdown format. |
| **Health Auditor** | `hassaleh-health-auditor` | Analyzes system health: agent heartbeat freshness, rule evaluation success rates, Intent failure patterns, and Neo4j connectivity. Generates recommendations for rule adjustments. |

**Skill invocation:** When an agent receives a request that matches a skill's domain (e.g., "create a rule that..."), it automatically invokes the corresponding skill. The skill provides the structured interaction pattern; the agent provides the conversational interface. Skills are implemented as Python modules in `src/hassaleh/skills/` and registered as Capability nodes during `hassaleh init`.

### 4.14.4 Skill Domain Taxonomy

Capabilities are organized into hierarchical dot-notation domains. The initial taxonomy is:
`system.exec`, `system.fs`, `system.pkg`, `system.net`,
`data.graph`, `data.etl`, `data.query`,
`orchestration.rules`, `orchestration.tasks`, `orchestration.agents`,
`reporting.activity`, `reporting.metrics`,
`security.audit`, `security.secrets`,
`communication.notify`, `communication.msg`.

Each capability stores its domain directly on `Capability.domain`. The field is indexed so operators and agents can do fast prefix lookups such as `system` or `orchestration`.

Hassaleh also materializes the taxonomy as `SkillDomain` nodes:

```
(:SkillDomain {
    id: "system.exec",
    display_name: "System Exec",
    description: "OS command execution.",
    parent_domain: "system"
})
```

Child domains connect to their parent via `(:SkillDomain)-[:BELONGS_TO_DOMAIN]->(:SkillDomain)`. Capabilities link to their exact leaf domain with `(:Capability)-[:IN_DOMAIN]->(:SkillDomain)`.

This gives three practical benefits:
- **Discovery:** `hassaleh skill list` groups capabilities by domain, `hassaleh skill search` searches names and descriptions, and `hassaleh domain list/info` makes the taxonomy itself browsable.
- **Permissions:** `Workspace.allowed_domains` defines which domain prefixes are executable inside that workspace. If the list is unset or empty, the workspace is unrestricted.
- **Selection:** when several capabilities are plausible matches, the Daemon prefers the most specific domain, so `system.exec.ls` wins over a generic `system.exec` handler.

Prefix matching is segment-aware: `system` matches `system.exec`, but `sys` does not. This keeps discovery and permission checks predictable.

The taxonomy is intentionally small and graph-native. New capabilities should reuse an existing domain when possible; new domains should only be added when they improve discovery, policy boundaries, or ecosystem clarity.

---

### 4.15 Message

Inter-agent communication node. Part of a linked-list message queue.

```
(:Message {
    id: uuid(),
    timestamp: datetime({timezone: "UTC"}),
    content: "I've completed the GSL parser rewrite. 26 tests pass."
})
```

**Relationships:**
```
(Agent)-[:SENT]->(Message)
(Message)-[:NEXT]->(Message)            # linked-list for O(1) cursor traversal
(Message)-[:IN_CONTEXT_OF]->(Task)
(Message)-[:IN_CONTEXT_OF]->(Discussion)
(Message)-[:REFERENCES]->(Artifact)     # explicit edge, not JSON metadata
(Agent)-[:LAST_READ]->(Message)         # cursor — agent's read position
```

**Efficient retrieval:** Agents don't poll by timestamp. They follow their `LAST_READ` cursor forward through the `NEXT` chain. Empty check = O(1).

### 4.16 Intent

A proposed state change or action submitted by an agent, processed by the Daemon. Includes full feedback loop so agents can read results.

```
(:Intent {
    id: uuid(),
    submitted_at: datetime({timezone: "UTC"}),
    action: "update_property",          # update_property | create_node | create_edge | execute_capability
    property: "lifecycle",              # target identified via [:TARGETS] edge, not string FK
    value: "failed",
    idempotency_key: "health-check-dione-2026-03-29T13:00",
    
    # ── Feedback (written by Daemon) ──
    lifecycle: "pending",              # pending | awaiting_approval | running | success | failed | rejected
    started_at: null,
    completed_at: null,
    stdout: null,                      # captured output (for execute_capability)
    stderr: null,                      # captured errors
    error_reason: null,                # why it was rejected/failed
    exit_code: null                    # for capability execution
})
```

Agents poll their submitted Intents (via read-only SDK) to retrieve capability outputs and detect rejections. This closes the feedback loop — no agent hangs waiting for a result that never comes.

**Relationships:**
```
(Agent)-[:PROPOSED]->(Intent)
(Intent)-[:TARGETS]->(Node)
(Rule)-[:GENERATED]->(Intent)
```

### 4.17 SystemTrace

Operational log entries (errors, restarts, performance). Separated from agent communication.

```
(:SystemTrace {
    id: uuid(),
    timestamp: datetime({timezone: "UTC"}),
    level: "error",                    # debug | info | warn | error | fatal
    source: "daemon",                  # daemon | agent | rule | cronjob
    message: "Agent dione unresponsive for 15 minutes. Restarting.",
    metadata: '{"restart_count": 3}'
})
```

**Relationships:**
```
(SystemTrace)-[:ABOUT]->(Agent)
(SystemTrace)-[:IN_CONTEXT_OF]->(Project)
(SystemTrace)-[:IN_BUCKET]->(TimeBucket)
```

### 4.18 TimeBucket

Partitioning node for log aggregation. Prevents supernode problem on Project nodes.

```
(:TimeBucket {
    id: "2026-03-29",
    date: date("2026-03-29"),
    type: "daily"
})
```

**Relationships:**
```
(SystemTrace)-[:IN_BUCKET]->(TimeBucket)
(Message)-[:IN_BUCKET]->(TimeBucket)
(TimeBucket)-[:NEXT]->(TimeBucket)
```

**Archival:** A CronJob archives TimeBuckets older than 30 days to JSONL and DETACH DELETEs them.

### 4.19 DaemonConfig

Singleton node — all Daemon runtime settings. No external config files.

```
(:DaemonConfig {
    id: "default",
    tick_interval_ms: 1000,            # hot-path loop interval
    sweep_interval_min: 15,
    audit_schedule: "0 3 * * *",       # daily at 03:00
    intent_timeout_default_sec: 300,   # max time for an Intent to complete
    max_concurrent_actions: 10,        # async action worker pool size
    circuit_breaker_window_min: 60,    # time window for restart counting
    circuit_breaker_decay_per_sweep: 1,# subtract from restart_count each sweep
    notification_channel: "openclaw",  # how to wake agents
    health_endpoint_port: 9100         # systemd watchdog + monitoring
})
```

**Relationships:** None (singleton, read by Daemon on boot).

### 4.20 QueryConfig

Singleton node — read-access guardrails for agents. Loaded by the hassaleh.query() SDK.

```
(:QueryConfig {
    id: "default",
    default_timeout_ms: 3000,
    max_result_rows: 10000,
    max_transaction_memory_mb: 1024,
    blocked_patterns: ["MATCH (a), (b), (c)", "DETACH DELETE", "CALL db."],
    report_query_timeout_ms: 30000
})
```

**Relationships:** None (singleton, read by Agent SDK on init).

### 4.21 SystemVersion

Singleton node — tracks the graph schema version for safe migrations.

```
(:SystemVersion {
    id: "hassaleh",
    schema_version: "0.4",
    last_migration: datetime({timezone: "UTC"}),
    compatible_daemon_versions: ["1.0", "0.5"]
})
```

**Relationships:** None (singleton, checked by Daemon on boot).

The Daemon checks this on boot and refuses to start (or applies migration scripts) if the code version is incompatible.

### 4.22 Discussion

A structured multi-agent discussion for collaborative decision-making.

```
(:Discussion {
    id: uuid(),
    topic: "Should we use LALR or Earley parser for GSL?",
    lifecycle: "success",              # resolved
    resolution: "Earley — handles ambiguity, performance is sufficient",
    resolved_at: datetime({timezone: "UTC"})
})
```

**Relationships:**
```
(Discussion)-[:IN_CONTEXT_OF]->(Project)
(Discussion)-[:IN_CONTEXT_OF]->(Task)
(Agent)-[:CONTRIBUTED {position: "pro-earley", reasoning: "...", timestamp: datetime({timezone: "UTC"})}]->(Discussion)
(Discussion)-[:DECIDED_BY]->(Agent)
```

---

## 5. Universal Lifecycle Status

All stateful nodes use a consistent lifecycle enum:

| Status | Meaning |
|--------|---------|
| `available` | Static resource ready for use (Model, Capability) |
| `deprecated` | Static resource still functional but scheduled for removal |
| `pending` | Created, not yet started |
| `awaiting_approval` | Parked — requires human confirmation before proceeding |
| `running` | Actively executing |
| `success` | Completed successfully |
| `failed` | Completed with failure |
| `rejected` | Denied by Daemon (permission check failed, rule conflict, etc.) |
| `suspended` | Paused (by rule or human) — can be resumed |
| `archived` | Retained for history, no longer active |

Used by: Agent, Model, Capability, Rule, Project, Sprint, Task, Milestone, CronJob, Discussion, Intent.

---

## 6. GSL-Ops: Deterministic Rule Subset

Hassaleh uses **GSL-Ops**, a deterministic subset of the GWW3 GSL language:

| GSL Feature | GSL-Ops | Notes |
|-------------|---------|-------|
| `MATCH …:` blocks | ✅ | Graph pattern matching |
| `IF …:` blocks | ✅ | Boolean conditions |
| `LET` bindings | ✅ | Variable computation |
| Effects (`+=`, `-=`, `=`) | ✅ | State modifications |
| Truth Values `⟨p, c⟩` | ❌ Stripped | No probabilistic execution |
| Distributions (`𝒩`, `𝐿𝑁`, `𝛽`) | ❌ Stripped | No Monte Carlo sampling |
| `DELAYED` blocks | ✅ Simplified | Timer-based deferred actions |
| `CONVERGE` operator | ❌ Stripped | Not needed for operations |

**Conflict resolution** uses **priority numbers** on rules (lower = higher priority), not commutative math reduction. For numeric properties, additive reduction is available. For enum properties (like `lifecycle`), the highest-priority rule wins.

---

## 7. Orchestration Rules

Agent coordination is **purely rule-based**. Rules define:

### 7.1 Agent Lifecycle

```
# Health check with circuit breaker
MATCH (a:Agent {lifecycle: "running"}):
    IF a.last_heartbeat < datetime({timezone: 'UTC'}) - duration("PT15M"):
        IF a.restart_count_1h >= a.max_restarts_1h:
            a.lifecycle = "suspended"
            NOTIFY "ingo" "Agent {a.name} suspended after {a.max_restarts_1h} restarts"
        IF a.restart_count_1h < a.max_restarts_1h:
            a.restart_count_1h += 1
            RESTART a

# Credit exhaustion fallback
MATCH (a:Agent)-[:USES_MODEL {priority: 1}]->(m:Model):
    IF a.credits_remaining < 1000 ∧ a.credits_remaining > 0:
        MATCH (a)-[:USES_MODEL {fallback: true}]->(fb:Model):
            SWITCH a TO fb
            LOG "Switched {a.name} to {fb.name} (low credits)"
```

### 7.2 Task Execution Modes

| Mode | Description | Pattern |
|------|-------------|---------|
| **Sequential** | A works → B reviews → merge or redo | `A completes → B reviews → merge` |
| **Discussion** | Agents debate via Message nodes | `All contribute → vote → leader decides` |
| **Parallel** | Independent agents, sync at milestone | `A on task 1, B on task 2 → sync` |
| **Supervised** | Worker + monitor | `Worker runs → Supervisor checks every N ticks` |

### 7.3 Task Timeouts

Every Task has an `expires_at` property. A background rule sweeps for expired tasks:

```
MATCH (t:Task {lifecycle: "running"}):
    IF t.expires_at < datetime({timezone: 'UTC'}):
        t.lifecycle = "failed"
        MATCH (t)-[:ASSIGNED_TO]->(a:Agent):
            a.lifecycle = "pending"
        LOG "Task {t.name} expired — unassigned from agent"
```

### 7.4 Communication Protocol

Agents **never communicate directly**. All exchange flows through Message nodes:

1. Agent submits a Message intent to the Daemon
2. Daemon creates `(:Message)` node, links it to the `NEXT` chain
3. Other agents advance their `LAST_READ` cursor to find new messages
4. Consensus is recorded as a Discussion resolution
5. The project lead agent (or human) can override any decision

### 7.5 Idempotency

For tasks with external side effects (sending emails, API calls, deployments), the Task carries an `idempotency_key`. If an agent crashes after performing the action but before logging it, the Daemon checks the key on restart and skips re-execution.

---

## 8. Human-in-the-Loop (HITL)

For sensitive actions (Capabilities with `requires_confirmation: true`), the Daemon does **not** execute immediately:

1. Agent submits an Intent targeting a Capability that requires confirmation
2. Daemon sets the Intent to `awaiting_approval`
3. Human admin is notified (via OpenClaw, Telegram, or Daemon health endpoint)
4. Admin reviews the Intent in the graph (via CLI `hassaleh approve <intent-id>` or direct Cypher as `neo4j` admin user)
5. Admin sets Intent to `pending` (approved) or `rejected` (denied with reason)
6. Daemon processes the Intent on its next tick

This keeps humans in the loop for irreversible or dangerous operations without blocking the entire orchestration pipeline.

---

## 9. Observability

### 9.1 Daemon Health Endpoint

The Daemon exposes a minimal HTTP health endpoint (port configured in `DaemonConfig.health_endpoint_port`):

```json
GET /health
{
    "status": "healthy",
    "uptime_seconds": 3600,
    "tick_count": 3592,
    "pending_intents": 2,
    "active_workers": 1,
    "last_tick_ms": 12,
    "schema_version": "0.5",
    "agents_running": 3,
    "agents_failed": 0
}
```

### 9.2 SystemTrace + TimeBucket

All operational events are logged as SystemTrace nodes, partitioned by TimeBucket (daily). See sections 4.17 and 4.18.

### 9.3 Alerting

Alerts are dispatched via the Daemon's notification channel (configured in DaemonConfig):
- Agent failures (circuit breaker triggered)
- Intent rejections (permission denied)
- Intents awaiting human approval
- Schema version mismatches
- Daemon health anomalies (tick latency > threshold)

---

## 10. Agent Registration & Discovery

New agents register by having the human admin create an `(:Agent)` node in the graph with appropriate `[:USES_MODEL]`, `[:HAS_CAPABILITY]`, and `[:OPERATES_IN]` edges.

The Daemon discovers agents by querying for `(:Agent)` nodes with `lifecycle: "pending"` and initiates their startup sequence (spawning the process, injecting credentials, setting `os_pid`).

Self-registration by agents is **not supported** — this is a deliberate security decision. All agent creation flows through the human admin.

---

## 11. File Coordination

When agents work on shared workspaces, file coordination uses the Intent mechanism:

1. Agent A finishes writing a file and submits an Intent: `{action: "artifact_ready", path: "reports/analysis.md"}`
2. Daemon creates/updates an `(:Artifact)` node and links it to the Task
3. Agent B (or a rule) can query for `(:Artifact)` nodes in the `success` state before reading the file

This avoids race conditions where one agent reads a file that another is still writing.

---

## 12. Multi-Host Considerations

The initial version is **single-host** (Ubuntu). However, the architecture supports multi-host extension:
- Neo4j can be accessed remotely (Bolt protocol)
- Agents on remote hosts use the `hassaleh.query()` SDK over the network
- Intents are submitted via HTTP API to the Daemon
- Workspace access would require shared filesystems (NFS, SSHFS) or artifact transfer via the graph

Multi-host support is **deferred** to post-MVP.

---

## 13. Testing Strategy

| Level | What | How |
|-------|------|-----|
| **Unit** | SDK query guards, Intent validation, GSL-Ops compiler | pytest, mock Neo4j driver |
| **Integration** | Daemon tick loop, Intent processing, async workers | Real Neo4j test instance, test agent |
| **End-to-End** | Full loop: agent → Intent → Daemon → action → feedback | Dedicated test DB, dummy agent, real systemd service |
| **Rule Testing** | GSL-Ops rules produce correct Intents | Parse → compile → evaluate against test graph |
| **Chaos** | Daemon crash recovery, zombie Intents, circuit breakers | Kill Daemon mid-tick, verify recovery on restart |

The GWW3 test database infrastructure (multiple Neo4j instances on different ports) is reused for Hassaleh testing.

---

## 14. Reporting

A reporting agent queries the graph to generate:

- **Project status reports:** Sprint progress, task completion, blockers
- **Agent activity reports:** Actions per agent, uptime, error rates
- **Discussion summaries:** What was debated, who said what, what was decided
- **Resource usage:** Model costs, API calls, credit burn rate
- **Timeline analysis:** Planned vs actual dates, velocity trends

All reports generated from **graph queries only** — no external state needed.

---

## 15. Technology Stack

| Component | Technology |
|-----------|-----------|
| Database | Neo4j 2026.x (Community Edition) |
| Language | Python 3.13 |
| Rule Engine | GSL-Ops (deterministic subset of GSL from GWW3) |
| Parser | Lark 1.3.1 (Earley + PythonIndenter) |
| Agent Runtime | OpenClaw, Google ADK, standalone |
| Daemon | Python asyncio (systemd-managed, persistent) |
| Agent SDK | `hassaleh.query()` — guarded read-only Neo4j access |
| CLI | `hassaleh` (planned) |
| Platform | Ubuntu 24.04+ (initial) |

---

## 16. Relationship to GWW3

Hassaleh extracts and generalizes the **rule engine architecture** developed for Games of World War 3:

| GWW3 Component | Hassaleh Equivalent |
|----------------|-------------------|
| GSL parser + compiler | Reused, stripped to GSL-Ops (no distributions) |
| Neo4j schema | Generalized for agents/projects instead of nations/conflicts |
| Intent accumulator | Simplified — priority-based for enums, additive for numbers |
| Tick engine | Adapted as Daemon orchestration loop |
| Deterministic RNG | Available but optional |
| Confidence fields | Available but optional |

When Hassaleh is operational, it will be used to orchestrate further GWW3 development and all other Alpha Auriga projects.

---

## 17. Roadmap

| Phase | Goal |
|-------|------|
| **0 — Concept** ✅ | Architecture document, GitHub repo |
| **1 — Schema + Seed** | Neo4j schema (constraints, indexes), DaemonConfig/QueryConfig/SystemVersion singletons, seed data for 1 project + 1 agent |
| **2 — Daemon MVP** | Persistent async service (systemd), Intent processing loop, Capability enforcement via `sudo -n -u`, SecretRef resolution, Intent feedback (stdout/stderr) |
| **2.5 — Agent SDK** | `hassaleh.query()` read-proxy with Cypher linting, per-query timeouts from DB, convenience methods |
| **3 — Blackboard Spike** | 1 real agent (Dione) submitting Intents + reading results. Prove the full loop: agent → Intent → Daemon → action → feedback → agent reads result |

### MVP Sprint (Phase 1–3): First 6 Files

| File | Purpose |
|------|---------|
| `schema.cypher` | CREATE CONSTRAINT/INDEX for MVP node types (Agent, Capability, Workspace, Intent, Task, DaemonConfig, QueryConfig, SystemVersion) |
| `seed.cypher` | Create initial Agent, Capability (`ls` command), Task, Workspace, DaemonConfig, QueryConfig, SystemVersion nodes |
| `setup_os.sh` | Create OS users (`hassaleh-svc`, `hassaleh-agent`, `hassaleh-fs`, `hassaleh-writer`, `hassaleh-exec`, `hassaleh-net`, `hassaleh-pkg`), configure `/etc/sudoers.d/hassaleh`, install `hassaleh-daemon.service` systemd unit file |
| `daemon.py` | Asyncio event loop, Neo4j polling for pending Intents, `create_subprocess_exec` async workers, zombie recovery on boot, graceful shutdown, systemd watchdog |
| `sdk.py` | `hassaleh.query()` (parameterized read-only) + `hassaleh.submit_intent()` |
| `agent_dummy.py` | Test agent: claim a Task, write a file to Workspace, submit Intent to execute `ls -la` via Capability, read result |

### Subsequent Phases

| Phase | Goal |
|-------|------|
| **4 — Rule Engine** | Port GSL-Ops from GWW3, compile on boot, priority-based conflict resolution |
| **5 — CLI** | `hassaleh init`, `hassaleh status`, `hassaleh report` |
| **6 — Integration** | OpenClaw integration, first operational rules (health checks, task assignment) |
| **7 — Reporting** | Graph-based project/agent reporting |
| **8 — Multi-Agent** | Discussion protocol, consensus mechanism, workspace permission management |
| **9 — Docker Dev-Env** | Dockerized development environment: Neo4j instance + simulated OS-level isolation (hassaleh-svc/agent/exec users) + Daemon + sample agent. Enables `docker compose up` onboarding for open-source contributors |

---

## Changelog

| Date | Version | Changes |
|------|---------|---------|
| 2026-03-29 | v0.1 | Initial concept: 14 node types, orchestration rules, roadmap |
| 2026-03-29 | v0.2 | Gemini Deep Think review: +7 node types (Workspace, ConfigFile, Memory, SecretRef, Message, Intent, SystemTrace, TimeBucket, Discussion split). Trusted Daemon architecture. GSL-Ops subset. Priority-based conflict resolution. Circuit breakers. Task timeouts. Idempotency keys. Edge-first modeling (no string FKs). Message cursor pattern. TimeBucket log partitioning. Universal LifecycleStatus enum. Roadmap reordered (Daemon before Rule Engine). |
| 2026-03-29 | v0.3 | OS-level enforcement: dedicated OS users (`hassaleh-svc`, `hassaleh-agent`). Dual Neo4j users (`hassaleh_daemon` r/w, `hassaleh_reader` r/o). Per-action OS user isolation (`hassaleh-fs`, `hassaleh-writer`, `hassaleh-exec`, `hassaleh-net`, `hassaleh-pkg`). Triple-layered enforcement (graph + daemon + OS). `exec_as_user` field on SystemAction nodes. |
| 2026-03-29 | v0.4 | **Second Gemini DT review.** CronJob Daemon → persistent asyncio service (systemd). 1-second ticks + async action workers (never blocks). Intent feedback loop (lifecycle, stdout, stderr, error_reason). `hassaleh.query()` read-proxy SDK with Cypher linting. Per-query timeouts from DB (not global DBMS timeout). QueryConfig + DaemonConfig + SystemVersion singleton nodes. All configuration in graph (no external config files). Workspaces project-specific with direct OS-level agent r/w access. Safe subprocess execution (`sudo -n -u`, no `shell=True`). Rules compiled on boot + on-change (not per-tick). Memory limits 1 GB (not 256 MB). Roadmap reordered: Daemon → SDK → Blackboard Spike → Rule Engine. |
| 2026-03-29 | v0.5 | **Third Gemini DT review.** async subprocess, zombie recovery, systemd watchdog, HITL, SDK safety, observability, testing, file coordination, agent registration, multi-host, MVP sprint scope. |
| 2026-03-29 | v1.0 | **Fourth Gemini DT review (final — GO).** Merged Tool + Skill + SystemAction → **Capability** (single unified node). Fixed string FK in Intent (`target_node_id` → `[:TARGETS]` edge). Added `available` + `deprecated` to LifecycleStatus. Fixed CronJob `last_status` → `lifecycle`. Message metadata → `[:REFERENCES]` edge. Graceful Daemon shutdown (SIGTERM handler, orphan process cleanup). Neo4j read-only fallback handling. UTC mandate for all timestamps. `setup_os.sh` added to MVP (6th file). Docker dev-env added to roadmap (Phase 9). 17 sections, 22 node types. |
| 2026-03-29 | v1.1 | **Final consistency audit (15 fixes).** Removed orphaned SystemAction text block from ConfigFile section. Fixed all `datetime()` → `datetime({timezone: 'UTC'})`. Renumbered sections 4.4–4.22 (no gaps). Fixed cross-references (SecretRef 4.13, SystemTrace/TimeBucket 4.17/4.18). Replaced all stale terminology (SystemAction→Capability, Tool→Capability, execute_command→execute_capability). Added `Rule` to LifecycleStatus "Used by" list. Added Relationships sections to Model, ConfigFile, Milestone, Artifact, DaemonConfig, QueryConfig, SystemVersion. Fixed SecretRef relationship (Tool→Capability). Added all 7 OS users to setup_os.sh description. Fixed node type count to 22. |
| 2026-03-29 | v1.2 | **Second consistency audit (8 fixes).** "with which tools"→"capabilities" (Sec 1). `datetime()` → UTC in GSL pseudocode (Sec 7.1, 7.3). Added Relationships: None to QueryConfig (Sec 4.20). Renamed v1.01→v1.1, v1.02→v1.2 for cleaner versioning. |

---

*This document has been reviewed through 4 iterations of Gemini Deep Think analysis plus 2 consistency audits. It is internally consistent and ready for implementation.*
```

