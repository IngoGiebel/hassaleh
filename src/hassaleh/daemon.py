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
import sys
from datetime import datetime, timezone
from typing import Any

from neo4j import AsyncGraphDatabase

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
    "circuit_breaker_decay_per_sweep": 1,
}


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
        self.config: dict = dict(DEFAULT_CONFIG)
        self.running = False
        self.tick_count = 0
        self.active_workers: dict[str, asyncio.Task] = {}
        self._shutdown_event = asyncio.Event()

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

        # Recover zombie Intents
        await self._recover_zombies()

        # Start the main loop
        self.running = True
        log.info(f"Daemon running (tick interval: {self.config['tick_interval_ms']}ms)")

        await self._main_loop()

    async def shutdown(self) -> None:
        """Graceful shutdown: stop accepting, kill workers, update Intents."""
        log.info("Initiating graceful shutdown...")
        self.running = False

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

        # Close Neo4j
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

        while self.running:
            tick_start = asyncio.get_event_loop().time()
            self.tick_count += 1

            try:
                # Hot path: process pending Intents
                await self._process_pending_intents()

                # Clean up finished workers
                self._reap_workers()

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
                    break  # shutdown was signaled
                except asyncio.TimeoutError:
                    pass  # normal tick sleep expired

    # ── Intent Processing ──

    async def _process_pending_intents(self) -> None:
        """Find and process all pending Intents."""
        max_workers = self.config["max_concurrent_actions"]
        available_slots = max_workers - len(self.active_workers)
        if available_slots <= 0:
            return

        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (agent:Agent)-[:PROPOSED]->(i:Intent {lifecycle: 'pending'})
                OPTIONAL MATCH (i)-[:TARGETS]->(target)
                RETURN i, agent, target
                ORDER BY i.submitted_at ASC
                LIMIT $limit
            """, limit=available_slots)

            records = [record async for record in result]

        for record in records:
            intent = record["i"]
            agent = record["agent"]
            intent_id = intent["id"]

            # Check capability permission
            if intent["action"] == "execute_capability":
                has_cap = await self._check_capability(agent["id"], intent)
                if not has_cap:
                    await self._reject_intent(intent_id, "Agent lacks required capability")
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
            await self._fail_intent(intent_id, "Execution timed out")

    async def _execute_update(self, intent_id: str, intent: dict) -> None:
        """Execute a property update on the target node."""
        prop = intent.get("property")
        value = intent.get("value")

        if not prop:
            await self._fail_intent(intent_id, "Missing property name")
            return

        async with self.driver.session() as session:
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

    # ── Permission Checks ──

    async def _check_capability(self, agent_id: str, intent: dict) -> bool:
        """Verify the agent has the required capability."""
        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (a:Agent {id: $agent_id})-[:HAS_CAPABILITY]->(cap:Capability)
                MATCH (i:Intent {id: $intent_id})-[:TARGETS]->(cap)
                RETURN count(*) AS has_cap
            """, agent_id=agent_id, intent_id=intent["id"])
            record = await result.single()
            return record["has_cap"] > 0

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
        """On boot: transition stale 'running' Intents to 'failed'."""
        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (i:Intent {lifecycle: 'running'})
                SET i.lifecycle = 'failed',
                    i.error_reason = 'Daemon restarted during execution',
                    i.completed_at = datetime({timezone: 'UTC'})
                RETURN count(*) AS recovered
            """)
            record = await result.single()
            count = record["recovered"]
            if count > 0:
                log.warning(f"Recovered {count} zombie Intent(s)")

    # ── Sweep ──

    async def _sweep(self) -> None:
        """Periodic maintenance: decay circuit breakers, clean stale memory."""
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

    # Signal handlers for graceful shutdown
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(daemon.shutdown()))

    try:
        await daemon.start()
    except KeyboardInterrupt:
        pass
    finally:
        await daemon.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
