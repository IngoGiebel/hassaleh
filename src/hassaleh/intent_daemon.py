"""IntentDaemon — MVP Intent Pipeline daemon processing.

Handles: claiming intents, capability enforcement, execution,
state transitions, reaper, and orphan recovery.

Reference: docs/spec-mvp-test.md v1.1, Sections 5.1–5.5
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import subprocess
import time
from typing import Any

from neo4j import AsyncGraphDatabase

from hassaleh.capabilities.exec_ls import (
    OUTPUT_TRUNCATION_LIMIT,
    validate_exec_ls_path,
)
from hassaleh.errors import CapabilityParamError

log = logging.getLogger("hassaleh.intent_daemon")

# Valid state transitions per spec §5.1
VALID_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"claimed"},
    "claimed": {"rejected", "running"},
    "running": {"success", "failed"},
    # Terminal states — no outgoing transitions
    "success": set(),
    "failed": set(),
    "rejected": set(),
}

# Reaper threshold: 2x the default 30s timeout = 60s (spec §5.5)
REAPER_STALE_SEC = 60


class IntentDaemon:
    """Daemon process for claiming and executing Intents.

    Args:
        neo4j_uri: Neo4j bolt URI
        neo4j_user: Neo4j username
        neo4j_password: Neo4j password
        instance_id: Unique daemon instance ID (for claimed_by tracking)
        intent_timeout_sec: Per-intent execution timeout (default 30s)
        max_concurrent: Max simultaneous intent processing (default 3)
    """

    def __init__(
        self,
        neo4j_uri: str,
        neo4j_user: str,
        neo4j_password: str,
        instance_id: str | None = None,
        intent_timeout_sec: int = 30,
        max_concurrent: int = 3,
    ):
        self.neo4j_uri = neo4j_uri
        self.neo4j_user = neo4j_user
        self.neo4j_password = neo4j_password
        self.instance_id = instance_id or f"daemon-{socket.gethostname()}-{os.getpid()}"
        self.intent_timeout_sec = intent_timeout_sec
        self.max_concurrent = max_concurrent
        self.driver: Any = None

    async def connect(self) -> None:
        """Connect to Neo4j."""
        self.driver = AsyncGraphDatabase.driver(
            self.neo4j_uri,
            auth=(self.neo4j_user, self.neo4j_password),
        )
        async with self.driver.session() as session:
            result = await session.run("RETURN 1 AS ping")
            record = await result.single()
            assert record and record["ping"] == 1
        log.info("IntentDaemon connected (%s)", self.instance_id)

    async def close(self) -> None:
        """Close the Neo4j connection."""
        if self.driver:
            await self.driver.close()
            self.driver = None

    def _get_driver(self) -> Any:
        """Return the active driver, or create a temporary one."""
        if self.driver:
            return self.driver
        # For stateless calls (e.g. transition_intent without connect)
        return AsyncGraphDatabase.driver(
            self.neo4j_uri,
            auth=(self.neo4j_user, self.neo4j_password),
        )

    async def transition_intent(self, intent_id: str, to_state: str) -> bool:
        """Validate and apply a state transition.

        Enforces the state machine defined in spec §5.1.
        Raises ValueError for invalid transitions.
        Returns True on success.
        """
        temp_driver = None
        driver = self.driver
        if driver is None:
            driver = AsyncGraphDatabase.driver(
                self.neo4j_uri,
                auth=(self.neo4j_user, self.neo4j_password),
            )
            temp_driver = driver

        try:
            async with driver.session() as session:
                result = await session.run(
                    "MATCH (i:Intent {id: $id}) RETURN i.status AS status",
                    id=intent_id,
                )
                record = await result.single()

            if record is None:
                raise ValueError(f"Intent '{intent_id}' not found")

            current = record["status"]
            allowed = VALID_TRANSITIONS.get(current, set())

            if to_state not in allowed:
                raise ValueError(
                    f"Invalid transition: {current} → {to_state}"
                )

            async with driver.session() as session:
                await session.run(
                    "MATCH (i:Intent {id: $id}) "
                    "SET i.status = $state, i.updated_at = datetime()",
                    id=intent_id,
                    state=to_state,
                )

            return True
        finally:
            if temp_driver:
                await temp_driver.close()

    async def claim_intent(self, intent_id: str) -> bool:
        """Atomically claim an intent with capability verification (spec §5.2 step 2).

        Uses a single Cypher query that matches the SUBMITTED_BY->Agent->HAS_CAPABILITY
        chain. If the agent lacks the capability, the intent is rejected.

        Returns True if claimed successfully, False if already claimed by another daemon.
        """
        async with self.driver.session() as session:
            # Atomic claim + capability check in one query
            result = await session.run("""
                MATCH (i:Intent {id: $id, status: 'pending'})
                      -[:SUBMITTED_BY]->(a:Agent)
                      -[:HAS_CAPABILITY]->(c:Capability {id: i.capability_id})
                SET i.status = 'claimed',
                    i.claimed_at = datetime(),
                    i.claimed_by = $daemon_id,
                    i.updated_at = datetime()
                RETURN i.id AS id
            """, id=intent_id, daemon_id=self.instance_id)
            record = await result.single()

        if record is not None:
            log.info("Claimed intent %s", intent_id)
            return True

        # Claim failed — either already claimed, or agent lacks capability.
        # Try the rejection path for missing capability.
        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (i:Intent {id: $id, status: 'pending'})
                SET i.status = 'rejected',
                    i.error = 'Agent lacks required capability',
                    i.updated_at = datetime(),
                    i.completed_at = datetime()
                RETURN i.id AS id
            """, id=intent_id)
            record = await result.single()

        if record is not None:
            log.info("Rejected intent %s (capability denied)", intent_id)
            return False

        # Already claimed by another daemon — no action needed
        log.debug("Intent %s already claimed by another daemon", intent_id)
        return False

    async def process_intent(self, intent_id: str) -> None:
        """Full processing pipeline for a single intent (spec §5.2).

        1. Claim (atomic with capability check)
        2. Validate params
        3. Execute capability
        4. Store result
        """
        # Step 1: Claim
        async with self.driver.session() as session:
            result = await session.run(
                "MATCH (i:Intent {id: $id}) "
                "RETURN i.status AS status, i.capability_id AS cap, i.params AS params",
                id=intent_id,
            )
            record = await result.single()

        if record is None:
            log.error("Intent %s not found", intent_id)
            return

        status = record["status"]
        capability_id = record["cap"]
        params = json.loads(record["params"])

        # If still pending, claim it first
        if status == "pending":
            claimed = await self.claim_intent(intent_id)
            if not claimed:
                return  # rejected or already claimed

        # Transition to running
        start_time = time.monotonic()
        async with self.driver.session() as session:
            await session.run(
                "MATCH (i:Intent {id: $id, status: 'claimed'}) "
                "SET i.status = 'running', i.updated_at = datetime()",
                id=intent_id,
            )

        # Step 2: Validate params
        if capability_id == "exec-ls":
            path = params.get("path", "")
            try:
                resolved_path = validate_exec_ls_path(path)
            except CapabilityParamError as e:
                elapsed_ms = int((time.monotonic() - start_time) * 1000)
                await self._fail_intent(intent_id, f"Parameter validation failed: {e}", elapsed_ms)
                return

            # Step 3: Execute
            try:
                result_data = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: subprocess.run(
                            ["ls", "-la", resolved_path],
                            capture_output=True,
                            text=True,
                            timeout=self.intent_timeout_sec,
                        ),
                    ),
                    timeout=self.intent_timeout_sec,
                )
            except asyncio.TimeoutError:
                elapsed_ms = int((time.monotonic() - start_time) * 1000)
                await self._fail_intent(
                    intent_id,
                    f"Execution timed out after {self.intent_timeout_sec}s",
                    elapsed_ms,
                )
                return

            elapsed_ms = int((time.monotonic() - start_time) * 1000)

            if result_data.returncode != 0:
                await self._fail_intent(
                    intent_id,
                    result_data.stderr.strip() or f"ls exited with code {result_data.returncode}",
                    elapsed_ms,
                )
                return

            # Step 4: Store result (with truncation)
            stdout = result_data.stdout
            if len(stdout) > OUTPUT_TRUNCATION_LIMIT:
                stdout = stdout[:OUTPUT_TRUNCATION_LIMIT] + "\n[output truncated at 64KB]"

            async with self.driver.session() as session:
                await session.run("""
                    MATCH (i:Intent {id: $id})
                    SET i.status = 'success',
                        i.result = $result,
                        i.duration_ms = $ms,
                        i.completed_at = datetime(),
                        i.updated_at = datetime()
                """, id=intent_id, result=stdout, ms=elapsed_ms)

            log.info("Intent %s succeeded (%dms)", intent_id, elapsed_ms)

    async def _fail_intent(self, intent_id: str, error: str, duration_ms: int | None = None) -> None:
        """Mark an intent as failed with an error message."""
        async with self.driver.session() as session:
            await session.run("""
                MATCH (i:Intent {id: $id})
                SET i.status = 'failed',
                    i.error = $error,
                    i.duration_ms = $ms,
                    i.completed_at = datetime(),
                    i.updated_at = datetime()
            """, id=intent_id, error=error, ms=duration_ms)
        log.info("Intent %s failed: %s", intent_id, error)

    async def run_reaper(self) -> int:
        """Fail orphaned intents stuck in claimed/running beyond threshold (spec §5.5).

        Returns the number of reaped intents.
        """
        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (i:Intent)
                WHERE i.status IN ['claimed', 'running']
                  AND i.updated_at < datetime() - duration({seconds: $stale_sec})
                SET i.status = 'failed',
                    i.error = 'Daemon lost — execution state unknown (recovered by reaper)',
                    i.completed_at = datetime(),
                    i.updated_at = datetime()
                RETURN count(i) AS cnt
            """, stale_sec=REAPER_STALE_SEC)
            record = await result.single()

        reaped = record["cnt"] if record else 0
        if reaped:
            log.info("Reaper recovered %d orphaned intent(s)", reaped)
        return reaped

    async def recover_own_orphans(self) -> int:
        """On startup, fail all intents claimed by this daemon's prior run (spec §5.5).

        Returns the number of recovered intents.
        """
        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (i:Intent {claimed_by: $daemon_id})
                WHERE i.status IN ['claimed', 'running']
                SET i.status = 'failed',
                    i.error = 'Daemon restarted — prior execution lost',
                    i.completed_at = datetime(),
                    i.updated_at = datetime()
                RETURN count(i) AS cnt
            """, daemon_id=self.instance_id)
            record = await result.single()

        recovered = record["cnt"] if record else 0
        if recovered:
            log.info("Recovered %d orphan(s) from prior instance %s", recovered, self.instance_id)
        return recovered
