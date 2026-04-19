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

from hassaleh.auth import lookup_hash, verify_api_key
from hassaleh.errors import AuthenticationError

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
    "Message", "Discussion",
})


class HassalehSDK:
    """Agent-facing SDK for Hassaleh graph access.

    Usage:
        async with HassalehSDK() as sdk:
            tasks = await sdk.my_tasks(api_key)
            intent_id = await sdk.submit_intent(api_key, ...)
            result = await sdk.poll_intent(intent_id, api_key)
    """

    def __init__(
        self,
        neo4j_uri: str | None = None,
        neo4j_user: str | None = None,
        neo4j_password: str | None = None,
    ):
        self.neo4j_uri = (
            neo4j_uri
            if neo4j_uri is not None
            else os.environ.get("NEO4J_URI", "bolt://localhost:7690")
        )
        self.neo4j_user = (
            neo4j_user
            if neo4j_user is not None
            else os.environ.get("NEO4J_USER", "neo4j")
        )
        self.neo4j_password = (
            neo4j_password
            if neo4j_password is not None
            else os.environ.get("NEO4J_PASSWORD", "hassaleh-dev-2026")
        )
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
        auth = None if not self.neo4j_user and not self.neo4j_password else (
            self.neo4j_user,
            self.neo4j_password,
        )
        self.driver = AsyncGraphDatabase.driver(
            self.neo4j_uri,
            auth=auth,
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

    # ── Authentication ──

    async def _authenticate(self, api_key: str) -> str:
        """Resolve an API key to an agent_id via deterministic lookup + bcrypt verify.

        Uses the lookup_hash pattern (spec-heartbeat §3B): a SHA-256 digest
        indexes a single Agent row, then bcrypt verifies the stored hash.
        Avoids the O(N) bcrypt scan called out in IL-02.

        Raises:
            AuthenticationError: api_key is missing, malformed, or does not
                match a known agent.
        """
        if not isinstance(api_key, str) or not api_key:
            raise AuthenticationError("Invalid API key")

        key_lookup = lookup_hash(api_key)

        async with self.driver.session() as session:
            result = await session.run(
                "MATCH (a:Agent) WHERE a.api_key_lookup = $lookup "
                "RETURN a.id AS agent_id, a.api_key_hash AS api_key_hash",
                lookup=key_lookup,
            )
            record = await result.single()

        if record is None or not record["api_key_hash"]:
            raise AuthenticationError("Invalid API key")

        if not verify_api_key(api_key, record["api_key_hash"]):
            raise AuthenticationError("Invalid API key")

        return record["agent_id"]

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
        api_key: str,
        action: str,
        capability_id: str | None = None,
        target_id: str | None = None,
        target_label: str | None = None,
        property_name: str | None = None,
        value: str | None = None,
    ) -> str:
        """Submit an Intent for the Daemon to process.

        The submitting agent is derived server-side from ``api_key`` (IL-01).

        Args:
            api_key: Pre-shared API key; agent_id derived server-side.
            action: Intent action (execute_capability, update_property, etc.)
            capability_id: Target capability ID (for execute_capability)
            target_id: Target node ID (for update_property)
            target_label: Target node label (for update_property)
            property_name: Property to update
            value: Value to set or args JSON

        Returns:
            The generated Intent ID
        """
        agent_id = await self._authenticate(api_key)
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

    async def poll_intent(self, intent_id: str, api_key: str) -> dict[str, Any]:
        """Poll an Intent for its current state.

        The caller is authenticated via ``api_key`` (IL-03); only the
        submitting agent (owner of the ``PROPOSED`` edge) may read the
        Intent's state, stdout, or stderr.

        Args:
            intent_id: Intent to poll.
            api_key: Pre-shared API key; caller's agent_id is derived
                server-side.

        Returns:
            Dict with lifecycle, stdout, stderr, error_reason, exit_code.

        Raises:
            AuthenticationError: ``api_key`` is missing or invalid.
            PermissionError: The Intent does not exist OR the authenticated
                agent is not its owner. A single sanitized message is used
                for both cases so callers cannot probe for Intent IDs.
        """
        agent_id = await self._authenticate(api_key)

        # Ownership-scoped match: if the authenticated agent does not own
        # the Intent (or the Intent doesn't exist), zero rows come back.
        # We intentionally do NOT branch on "intent exists but not owned"
        # vs "intent missing" — both yield the same PermissionError so an
        # attacker cannot enumerate Intent IDs.
        records = await self.query("""
            MATCH (a:Agent {id: $agent_id})-[:PROPOSED]->(i:Intent {id: $id})
            RETURN i.lifecycle AS lifecycle,
                   i.stdout AS stdout,
                   i.stderr AS stderr,
                   i.error_reason AS error_reason,
                   i.exit_code AS exit_code,
                   i.submitted_at AS submitted_at,
                   i.completed_at AS completed_at
        """, {"agent_id": agent_id, "id": intent_id})

        if not records:
            raise PermissionError("intent not found or not authorized")
        return records[0]

    async def wait_for_intent(
        self,
        intent_id: str,
        api_key: str,
        timeout_sec: float = 60.0,
        poll_interval: float = 1.0,
    ) -> dict[str, Any]:
        """Wait for an Intent to reach a terminal state.

        Applies the same authentication + ownership check as
        :meth:`poll_intent` on every internal poll (IL-03).

        Args:
            intent_id: Intent to wait for.
            api_key: Pre-shared API key; caller's agent_id is derived
                server-side.
            timeout_sec: Maximum wait time.
            poll_interval: Seconds between polls.

        Returns:
            Final Intent state dict.

        Raises:
            AuthenticationError: ``api_key`` is missing or invalid.
            PermissionError: Caller is not the Intent's owner (sanitized).
            TimeoutError: Intent did not complete within ``timeout_sec``.
        """
        deadline = asyncio.get_event_loop().time() + timeout_sec

        while asyncio.get_event_loop().time() < deadline:
            state = await self.poll_intent(intent_id, api_key)
            if state["lifecycle"] in ("success", "failed", "rejected"):
                return state
            await asyncio.sleep(poll_interval)

        raise TimeoutError("intent did not complete before deadline")

    # ── Convenience Methods ──

    async def my_tasks(self, api_key: str) -> list[dict[str, Any]]:
        """Get all tasks assigned to the authenticated agent.

        Args:
            api_key: Pre-shared API key; agent_id derived server-side (IL-01).

        Returns:
            List of task dicts with id, name, lifecycle, description
        """
        agent_id = await self._authenticate(api_key)
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

    async def review_task(
        self,
        task_id: str,
        api_key: str,
        approved: bool,
        comment: str = "",
    ) -> None:
        """Submit a supervised task review intent.

        The reviewing agent is derived server-side from ``api_key`` (IL-01).
        """
        agent_id = await self._authenticate(api_key)
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
        api_key: str,
        content: str,
        context_id: str | None = None,
        context_label: str = "Task",
    ) -> str:
        """Send a message to a context (Task, Discussion, etc.).

        The sender is derived server-side from ``api_key`` (IL-01).
        Creates a Message node, links it via SENT from the authenticated agent,
        and appends it to the NEXT linked-list for the context.

        Args:
            api_key: Pre-shared API key; sender agent_id derived server-side.
            content: Message content
            context_id: Optional context node ID (Task, Discussion)
            context_label: Label of the context node

        Returns:
            The generated Message ID
        """
        agent_id = await self._authenticate(api_key)
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
        api_key: str,
        context_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Read unread messages for the authenticated agent (cursor-based).

        The reader is derived server-side from ``api_key`` (IL-01).
        Follows the NEXT chain from the agent's LAST_READ cursor.
        If no cursor exists, starts from the HEAD of the context.

        Args:
            api_key: Pre-shared API key; agent_id derived server-side.
            context_id: Optional context node ID (Task, Discussion).
            limit: Max messages to return.

        Returns:
            List of message dicts with id, content, timestamp, sender
        """
        agent_id = await self._authenticate(api_key)
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

    async def advance_cursor(self, api_key: str, message_id: str) -> None:
        """Move the authenticated agent's LAST_READ cursor to a specific message.

        The owning agent is derived server-side from ``api_key`` (IL-01).
        Deletes the old LAST_READ edge and creates a new one.
        """
        agent_id = await self._authenticate(api_key)
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
        api_key: str,
        discussion_id: str,
        position: str,
        reasoning: str,
    ) -> None:
        """Add the authenticated agent's position to a discussion.

        The contributor is derived server-side from ``api_key`` (IL-01).
        """
        agent_id = await self._authenticate(api_key)
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
        api_key: str,
        discussion_id: str,
        resolution: str,
    ) -> None:
        """Resolve a discussion (leader decision).

        The resolver is derived server-side from ``api_key`` (IL-01).
        Only agents who have CONTRIBUTED to the discussion can resolve it.
        """
        agent_id = await self._authenticate(api_key)
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

    async def agent_info(self, api_key: str) -> dict[str, Any] | None:
        """Get details for the authenticated agent.

        The target is derived server-side from ``api_key`` (IL-01);
        callers can no longer query other agents via this helper.
        Use :meth:`query` for read-only lookups of peer agents.
        """
        agent_id = await self._authenticate(api_key)
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
