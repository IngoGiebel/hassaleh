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
import uuid
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

# ──────────────────────────────────────────────
# IL-07: capability execution trust boundary
# ──────────────────────────────────────────────
#
# `_execute_capability()` reads `invoke_command` and `exec_as_user` off a
# `Capability` node and builds a `sudo -n -u <user> <command> ...` invocation.
# Any actor with write access to `Capability` could otherwise pick the binary
# and the target uid (lateral movement). The graph is treated as untrusted
# input; this allowlist is the local source of truth for what (binary, uid)
# pair each `capability_id` is permitted to run as.
#
# Mutating this allowlist requires a code change + review; it cannot be
# changed by writing to Neo4j. Schema/seed updates that introduce a new
# capability must add a matching entry here in the same change.
#
# Tests inject ephemeral entries via `monkeypatch.setitem`.
CAPABILITY_ALLOWLIST: dict[str, tuple[str, str]] = {
    "exec-ls":                    ("/usr/bin/ls",                       "hassaleh-fs"),
    "graph-query-inspector":      ("hassaleh.graph_query_inspector",    "hassaleh-daemon"),
    "rule-author-basic":          ("hassaleh.rule_author",              "hassaleh-daemon"),
    "audit-log-export":           ("/usr/bin/printf",                   "hassaleh-audit"),
    "metrics-daily-summary":      ("hassaleh.metrics_daily_summary",    "hassaleh-daemon"),
    "ci-test-runner":             ("/usr/bin/env",                      "hassaleh-ci"),
    "openclaw-alert-dispatch":    ("hassaleh.openclaw_alert_dispatch",  "hassaleh-daemon"),
    "workflow-cron-maintenance":  ("hassaleh.workflow_cron_maintenance","hassaleh-daemon"),
}

# IL-07 / IL-04: agent-visible vocabulary for graph-trust failures. Full
# detail (capability_id, expected vs. actual binary/uid, intent_id) is logged
# server-side under the same correlation ID; the agent only ever sees the
# sanitized form so that allowlist failures are indistinguishable from any
# other "Parameter validation failed" outcome (see docs/IL-04-implementation.md).
_MSG_PARAM_VALIDATION_FAILED = "Parameter validation failed"


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
                RETURN cap.id AS cap_id, cap.invoke_command AS command, cap.exec_as_user AS exec_user
            """, id=intent_id)
            record = await result.single()

        if not record:
            await self._fail_intent(intent_id, "No capability linked to Intent")
            return

        cap_id = record["cap_id"]
        command = record["command"]
        exec_user = record["exec_user"]

        # IL-07 validation boundary
        if cap_id not in CAPABILITY_ALLOWLIST:
            cid = uuid.uuid4().hex
            log.error(f"Intent {intent_id} validation failed [cid: {cid}]: capability {cap_id} not in allowlist")
            await self._fail_intent(intent_id, f"Parameter validation failed [cid: {cid}]")
            return

        allowed_cmd, allowed_user = CAPABILITY_ALLOWLIST[cap_id]
        if command != allowed_cmd or exec_user != allowed_user:
            cid = uuid.uuid4().hex
            log.error(
                f"Intent {intent_id} validation failed [cid: {cid}]: capability {cap_id} values "
                f"(cmd={command!r}, user={exec_user!r}) do not match allowlist "
                f"(expected cmd={allowed_cmd!r}, user={allowed_user!r})"
            )
            await self._fail_intent(intent_id, f"Parameter validation failed [cid: {cid}]")
            return

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
                MATCH (a:Agent {id: $agent_id})-[:HAS_CAPABILITY]->(cap:Capability)
                MATCH (i:Intent {id: $intent_id})-[:TARGETS]->(cap)
                RETURN a.allowed_domains AS allowed_domains,
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
        bind = self.config.get("health_endpoint_bind", "0.0.0.0")
        self._health_app = web.Application()
        self._health_app.router.add_get("/health", self._health_handler)

        self._health_runner = web.AppRunner(self._health_app)
        await self._health_runner.setup()
        site = web.TCPSite(self._health_runner, bind, port)
        await site.start()
        log.info(f"Health endpoint listening on http://{bind}:{port}/health")

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
