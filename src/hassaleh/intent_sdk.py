"""IntentSDK — MVP Intent Pipeline SDK with API-key authentication.

Provides submit_intent(), get_intent_status(), get_intent_result() per spec §4.
Agent identity is derived server-side from API key (spec §3B).

Reference: docs/spec-mvp-test.md v1.1, Sections 3B, 4.1–4.3
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from neo4j import AsyncDriver, AsyncGraphDatabase

from hassaleh.auth import lookup_hash, verify_api_key
from hassaleh.capabilities.exec_ls import validate_exec_ls_path_quick
from hassaleh.errors import (
    AccessDeniedError,
    AuthenticationError,
    CapabilityDeniedError,
    CapabilityNotFoundError,
    CapabilityParamError,
)

log = logging.getLogger("hassaleh.intent_sdk")

# Input validation (A7-S6)
_CAPABILITY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

# Max serialized params size in bytes (A7-S7)
MAX_PARAMS_SIZE = 64 * 1024  # 64 KB


class IntentSDK:
    """Agent-facing SDK for the MVP Intent Pipeline.

    Usage:
        async with IntentSDK(neo4j_uri=..., neo4j_user=..., neo4j_password=...) as sdk:
            intent_id = await sdk.submit_intent(api_key, "exec-ls", {"path": "/app"})
            status = await sdk.get_intent_status(api_key, intent_id)
            result = await sdk.get_intent_result(api_key, intent_id)
    """

    def __init__(
        self,
        neo4j_uri: str,
        neo4j_user: str,
        neo4j_password: str,
    ):
        self.neo4j_uri = neo4j_uri
        self.neo4j_user = neo4j_user
        self.neo4j_password = neo4j_password
        self.driver: AsyncDriver | None = None

    async def __aenter__(self) -> IntentSDK:
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
        log.info("IntentSDK connected to %s", self.neo4j_uri)

    async def close(self) -> None:
        """Close the Neo4j connection."""
        if self.driver:
            await self.driver.close()
            self.driver = None

    async def _authenticate(self, api_key: str) -> str:
        """Resolve an API key to an agent_id via deterministic lookup + bcrypt verify.

        IL-02 fix: port of the sdk.py / heartbeat_sdk.py pattern. SHA-256 of
        the raw key selects a single Agent row via the `api_key_lookup` index,
        then bcrypt verifies the stored hash. Caps auth cost at one
        `bcrypt.checkpw` regardless of agent population.

        All three failure branches raise byte-identical
        `AuthenticationError("Invalid API key")` to match sdk.py:147,160,163
        so a timing-only attacker cannot distinguish missing-key, no-such-agent,
        and wrong-bcrypt outcomes.

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

    async def _get_owned_intent(self, api_key: str, intent_id: str) -> dict[str, Any]:
        """Authenticate, fetch intent, verify ownership. Returns raw node dict.

        Shared by get_intent_status() and get_intent_result() (A8-Q3).
        Uses MATCH (not OPTIONAL MATCH) for ownership edge (A7-S11).
        """
        agent_id = await self._authenticate(api_key)

        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (i:Intent {id: $id})-[:SUBMITTED_BY]->(a:Agent)
                RETURN i, a.id AS owner_id
            """, id=intent_id)
            record = await result.single()

        if record is None:
            raise ValueError(f"Intent '{intent_id}' not found")

        if record["owner_id"] != agent_id:
            raise AccessDeniedError("Intent does not belong to caller")

        return dict(record["i"])

    async def submit_intent(
        self,
        api_key: str,
        capability_id: str,
        params: dict[str, Any],
    ) -> str:
        """Submit an Intent for Daemon processing.

        Args:
            api_key: Pre-shared API key (agent_id derived server-side).
            capability_id: e.g. "exec-ls"
            params: e.g. {"path": "/app"}

        Returns:
            intent_id (UUID string)
        """
        # Validate capability_id format (A7-S6)
        if not _CAPABILITY_ID_RE.match(capability_id):
            raise ValueError(
                f"Invalid capability_id format: {capability_id!r} "
                "(must match ^[a-z0-9][a-z0-9_-]{0,63}$)"
            )

        # Validate params size (A7-S7)
        params_json = json.dumps(params)
        if len(params_json) > MAX_PARAMS_SIZE:
            raise ValueError(
                f"Serialized params exceed {MAX_PARAMS_SIZE} byte limit "
                f"({len(params_json)} bytes)"
            )

        # Authenticate — derive agent_id from API key
        agent_id = await self._authenticate(api_key)

        # Verify capability exists and agent has permission (A8-Q5: single query)
        async with self.driver.session() as session:
            result = await session.run(
                "OPTIONAL MATCH (c:Capability {id: $cap_id}) "
                "OPTIONAL MATCH (a:Agent {id: $agent_id})"
                "  -[:HAS_CAPABILITY]->(c2:Capability {id: $cap_id}) "
                "RETURN c IS NOT NULL AS cap_exists, "
                "       c2 IS NOT NULL AS has_cap",
                agent_id=agent_id,
                cap_id=capability_id,
            )
            record = await result.single()

            if not record["cap_exists"]:
                raise CapabilityNotFoundError(
                    f"Capability '{capability_id}' not found"
                )
            if not record["has_cap"]:
                raise CapabilityDeniedError(
                    f"Agent '{agent_id}' lacks capability '{capability_id}'"
                )

        # Quick param validation at submit time (character + traversal only)
        if capability_id == "exec-ls":
            path = params.get("path", "")
            validate_exec_ls_path_quick(path)

        # Create Intent node + relationships in a single transaction
        intent_id = str(uuid.uuid4())

        async with self.driver.session() as session:
            async def _create(tx):
                await tx.run("""
                    MATCH (a:Agent {id: $agent_id})
                    MATCH (c:Capability {id: $cap_id})
                    CREATE (i:Intent {
                        id: $intent_id,
                        agent_id: $agent_id,
                        capability_id: $cap_id,
                        params: $params,
                        status: 'pending',
                        created_at: datetime(),
                        updated_at: datetime(),
                        result: null,
                        error: null,
                        claimed_at: null,
                        claimed_by: null,
                        completed_at: null,
                        duration_ms: null
                    })
                    CREATE (i)-[:SUBMITTED_BY]->(a)
                    CREATE (i)-[:REQUIRES]->(c)
                """,
                    agent_id=agent_id,
                    cap_id=capability_id,
                    intent_id=intent_id,
                    params=params_json,
                )

            await session.execute_write(_create)

        log.info("Intent %s submitted (agent=%s, cap=%s)", intent_id, agent_id, capability_id)
        return intent_id

    async def get_intent_status(self, api_key: str, intent_id: str) -> dict:
        """Get Intent status (spec §4.2).

        Authorization: caller must own the Intent.
        """
        node = await self._get_owned_intent(api_key, intent_id)
        return {
            "id": node["id"],
            "status": node["status"],
            "created_at": node["created_at"],
            "updated_at": node["updated_at"],
            "claimed_at": node.get("claimed_at"),
            "completed_at": node.get("completed_at"),
        }

    async def get_intent_result(self, api_key: str, intent_id: str) -> dict:
        """Get Intent result (spec §4.3).

        Authorization: caller must own the Intent.
        """
        node = await self._get_owned_intent(api_key, intent_id)
        return {
            "id": node["id"],
            "status": node["status"],
            "result": node.get("result"),
            "error": node.get("error"),
            "duration_ms": node.get("duration_ms"),
        }
