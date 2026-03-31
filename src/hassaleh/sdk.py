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
