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
import uuid

from neo4j import AsyncDriver, AsyncGraphDatabase

from hassaleh.capabilities.exec_ls import (
    execute_ls,
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
        self.driver: AsyncDriver | None = None

    async def __aenter__(self) -> IntentDaemon:
        await self.connect()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def connect(self) -> None:
        """Connect to Neo4j."""
        self.driver = AsyncGraphDatabase.driver(
            self.neo4j_uri,
            auth=(self.neo4j_user, self.neo4j_password),
        )
        async with self.driver.session() as session:
            result = await session.run("RETURN 1 AS ping")
            record = await result.single()
            if not record or record["ping"] != 1:
                raise ConnectionError("Neo4j connection verification failed")
        log.info("IntentDaemon connected (%s)", self.instance_id)

    async def close(self) -> None:
        """Close the Neo4j connection."""
        if self.driver:
            await self.driver.close()
            self.driver = None

    async def transition_intent(self, intent_id: str, to_state: str) -> bool:
        """Validate and apply a state transition atomically.

        Enforces the state machine defined in spec §5.1.
        Uses a single Cypher write transaction to prevent TOCTOU races (A7-S1):
        the atomic CAS runs first; a diagnostic read happens only on failure.
        Raises ValueError for invalid transitions or missing intents.
        Returns True on success.
        """
        # Build the set of states that may transition to to_state
        allowed_from = [s for s, targets in VALID_TRANSITIONS.items() if to_state in targets]
        if not allowed_from:
            raise ValueError(f"Invalid transition: no state can transition to '{to_state}'")

        temp_driver = None
        driver = self.driver
        if driver is None:
            driver = AsyncGraphDatabase.driver(
                self.neo4j_uri,
                auth=(self.neo4j_user, self.neo4j_password),
            )
            temp_driver = driver

        try:
            # Atomic check-and-set: single write transaction (A7-S1).
            # No pre-read — the CAS query is the source of truth.
            async with driver.session() as session:
                result = await session.run(
                    "MATCH (i:Intent {id: $id}) "
                    "WHERE i.status IN $allowed_from "
                    "SET i.status = $state, i.updated_at = datetime() "
                    "RETURN i.id AS id",
                    id=intent_id,
                    allowed_from=allowed_from,
                    state=to_state,
                )
                record = await result.single()

            if record is not None:
                return True

            # CAS failed — read current state for diagnostic error reporting only.
            # This read does not influence any mutation, so no TOCTOU risk.
            async with driver.session() as session:
                result = await session.run(
                    "MATCH (i:Intent {id: $id}) RETURN i.status AS status",
                    id=intent_id,
                )
                record = await result.single()

            if record is None:
                raise ValueError(f"Intent '{intent_id}' not found")

            raise ValueError(
                f"Invalid transition: {record['status']} → {to_state}"
            )
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

        # Transition to running (verify claimed_by to prevent hijacking — A7-S4)
        start_time = time.monotonic()
        async with self.driver.session() as session:
            result = await session.run(
                "MATCH (i:Intent {id: $id, status: 'claimed', claimed_by: $daemon_id}) "
                "SET i.status = 'running', i.updated_at = datetime() "
                "RETURN i.id AS id",
                id=intent_id,
                daemon_id=self.instance_id,
            )
            record = await result.single()
        if record is None:
            log.warning("Intent %s not claimed by this daemon, skipping", intent_id)
            return

        # Step 2: Validate params + Step 3: Execute via capability module
        # MVP: single capability handler. For multi-capability dispatch:
        #   handlers = {"exec-ls": exec_ls_handler, "graph-query": ...}
        #   handler = self.handlers[capability_id]
        if capability_id == "exec-ls":
            path = params.get("path", "")
            try:
                resolved_path = validate_exec_ls_path(path)
            except CapabilityParamError as e:
                # IL-04: defense-in-depth sanitization at the daemon boundary.
                # exec_ls also sanitizes upstream, but we generate a fresh cid
                # here so the cid travels with the intent_id and any leaky
                # exception from a future capability handler is still scrubbed
                # before it reaches Intent.error.
                cid = uuid.uuid4().hex
                log.error("Intent %s validation failed [cid: %s]: %s", intent_id, cid, e)
                elapsed_ms = int((time.monotonic() - start_time) * 1000)
                await self._fail_intent(
                    intent_id,
                    f"Parameter validation failed [cid: {cid}]",
                    elapsed_ms,
                )
                return

            # Execute via capability module (Q1: single source of truth)
            try:
                stdout = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: execute_ls(resolved_path, self.intent_timeout_sec)
                )
            except subprocess.TimeoutExpired as e:
                # IL-04: execute_ls lets TimeoutExpired propagate so the daemon
                # can attach its own intent-scoped context (configured timeout
                # value, intent_id) while still emitting a sanitized message.
                cid = uuid.uuid4().hex
                log.error(
                    "Intent %s execution timed out after %ds [cid: %s]: %s",
                    intent_id, self.intent_timeout_sec, cid, e,
                )
                elapsed_ms = int((time.monotonic() - start_time) * 1000)
                await self._fail_intent(
                    intent_id,
                    f"Execution timed out [cid: {cid}]",
                    elapsed_ms,
                )
                return
            except RuntimeError as e:
                # IL-04: defense-in-depth sanitization at the daemon boundary.
                # Mirror the validation path: any RuntimeError (from exec_ls
                # or a future capability) gets a fresh cid here so raw stderr
                # like "ls: cannot access ...: Permission denied" never
                # reaches Intent.error.
                cid = uuid.uuid4().hex
                log.error("Intent %s execution failed [cid: %s]: %s", intent_id, cid, e)
                elapsed_ms = int((time.monotonic() - start_time) * 1000)
                await self._fail_intent(
                    intent_id,
                    f"Execution failed [cid: {cid}]",
                    elapsed_ms,
                )
                return

            elapsed_ms = int((time.monotonic() - start_time) * 1000)

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
