"""Heartbeat SDK — privileged write interface for agent liveness signaling.

Separate from HassalehSDK (read-only + Intent submission) because heartbeats
perform direct Neo4j writes and have their own auth model (API key → agent_id
derived server-side with chained heartbeat_token for replay protection).

Reference: docs/spec-heartbeat.md v1.1
"""

from __future__ import annotations

import logging
import os
import secrets
from typing import Any

from neo4j import AsyncGraphDatabase

from hassaleh.auth import lookup_hash, verify_api_key
from hassaleh.errors import (
    AgentDisabledError,
    AuthenticationError,
    HeartbeatTokenMismatchError,
)

log = logging.getLogger("hassaleh.heartbeat_sdk")

VALID_SOURCES = frozenset({"agent", "cron"})


class HeartbeatSDK:
    """Agent heartbeat interface — writes liveness data to Neo4j.

    Usage:
        sdk = HeartbeatSDK(neo4j_uri=..., neo4j_user=..., neo4j_password=...)
        await sdk.connect()
        result = await sdk.heartbeat(api_key, heartbeat_token=None)
        await sdk.close()
    """

    def __init__(
        self,
        neo4j_uri: str | None = None,
        neo4j_user: str | None = None,
        neo4j_password: str | None = None,
    ):
        self.neo4j_uri = neo4j_uri or os.environ.get("NEO4J_URI", "bolt://localhost:7690")
        self.neo4j_user = neo4j_user or os.environ.get("NEO4J_USER", "neo4j")
        self.neo4j_password = neo4j_password or os.environ.get("NEO4J_PASSWORD", "hassaleh")
        self.driver: Any = None

    async def __aenter__(self) -> HeartbeatSDK:
        await self.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def connect(self) -> None:
        """Connect to Neo4j."""
        auth = None if not self.neo4j_user and not self.neo4j_password else (
            self.neo4j_user,
            self.neo4j_password,
        )
        self.driver = AsyncGraphDatabase.driver(self.neo4j_uri, auth=auth)
        async with self.driver.session() as session:
            result = await session.run("RETURN 1 AS ping")
            record = await result.single()
            assert record and record["ping"] == 1
        log.info("HeartbeatSDK connected to %s", self.neo4j_uri)

    async def close(self) -> None:
        """Close the Neo4j connection."""
        if self.driver:
            await self.driver.close()
            self.driver = None
            log.info("HeartbeatSDK disconnected")

    async def heartbeat(
        self,
        api_key: str,
        *,
        heartbeat_token: str | None = None,
        source: str = "agent",
    ) -> dict[str, Any]:
        """Send a heartbeat for the agent identified by api_key.

        Args:
            api_key: Pre-shared API key; agent_id derived server-side.
            heartbeat_token: Token from previous heartbeat (None for first).
            source: Origin of heartbeat — "agent" or "cron".

        Returns:
            Dict with agent_id, lifecycle, last_heartbeat, previous_heartbeat,
            heartbeat_count, heartbeat_token.

        Raises:
            AuthenticationError: Invalid or missing API key.
            AgentDisabledError: Agent is in disabled lifecycle state.
            HeartbeatTokenMismatchError: Token doesn't match stored token.
        """
        # -- Input validation --
        if not isinstance(api_key, str) or not api_key:
            raise AuthenticationError("Invalid API key")

        if source not in VALID_SOURCES:
            raise ValueError(
                f"Invalid heartbeat source '{source}'. Must be one of: {sorted(VALID_SOURCES)}"
            )

        # -- Step 1: Authenticate via deterministic lookup hash --
        key_lookup = lookup_hash(api_key)

        async with self.driver.session() as session:
            result = await session.run(
                "MATCH (a:Agent) WHERE a.api_key_lookup = $lookup "
                "RETURN a.id AS agent_id, a.api_key_hash AS api_key_hash, "
                "a.lifecycle AS lifecycle, a.heartbeat_token AS heartbeat_token",
                lookup=key_lookup,
            )
            record = await result.single()

        if record is None:
            raise AuthenticationError("Invalid API key")

        # Verify bcrypt hash to confirm the key is genuine
        if not verify_api_key(api_key, record["api_key_hash"]):
            raise AuthenticationError("Invalid API key")

        agent_id = record["agent_id"]
        lifecycle = record["lifecycle"]
        stored_token = record["heartbeat_token"]

        # -- Step 2: Check disabled state --
        if lifecycle == "disabled":
            raise AgentDisabledError("Agent is disabled — heartbeat rejected")

        # -- Step 3: Validate heartbeat token chain --
        if stored_token is None:
            # First heartbeat or token was cleared (stale/inactive recovery)
            if heartbeat_token is not None:
                raise HeartbeatTokenMismatchError(
                    "Invalid heartbeat token — possible replay or concurrent sender"
                )
        else:
            # Subsequent heartbeat — token must match
            if heartbeat_token != stored_token:
                raise HeartbeatTokenMismatchError(
                    "Invalid heartbeat token — possible replay or concurrent sender"
                )

        # -- Step 4: Attempt heartbeat write (rate-limited in Cypher) --
        new_token = secrets.token_urlsafe(32)

        async with self.driver.session() as session:
            # This query only writes if the rate-limit interval has elapsed.
            # If last_heartbeat is within 60s, the WHERE clause excludes the
            # node and no SET occurs — the heartbeat is silently deduplicated.
            result = await session.run("""
                MATCH (a:Agent {id: $agent_id})
                WHERE a.lifecycle <> 'disabled'
                  AND (a.last_heartbeat IS NULL
                       OR a.last_heartbeat < datetime() - duration('PT60S'))
                SET a.last_heartbeat = datetime(),
                    a.previous_heartbeat = a.last_heartbeat,
                    a.heartbeat_count = coalesce(a.heartbeat_count, 0) + 1,
                    a.lifecycle = CASE
                        WHEN a.lifecycle IN ['pending', 'stale', 'inactive']
                        THEN 'active'
                        ELSE a.lifecycle
                    END,
                    a.heartbeat_token = $new_token,
                    a.heartbeat_source = $source
                RETURN a.id AS agent_id,
                       a.lifecycle AS lifecycle,
                       a.last_heartbeat AS last_heartbeat,
                       a.previous_heartbeat AS previous_heartbeat,
                       a.heartbeat_count AS heartbeat_count,
                       a.heartbeat_token AS heartbeat_token
            """, agent_id=agent_id, new_token=new_token, source=source)
            write_record = await result.single()

        if write_record is not None:
            # Write succeeded — return fresh data
            return dict(write_record)

        # -- Rate-limited: no write occurred. Read current state. --
        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (a:Agent {id: $agent_id})
                RETURN a.id AS agent_id,
                       a.lifecycle AS lifecycle,
                       a.last_heartbeat AS last_heartbeat,
                       a.previous_heartbeat AS previous_heartbeat,
                       a.heartbeat_count AS heartbeat_count,
                       a.heartbeat_token AS heartbeat_token
            """, agent_id=agent_id)
            read_record = await result.single()

        return dict(read_record)
