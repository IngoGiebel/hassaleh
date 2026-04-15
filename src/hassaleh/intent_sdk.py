"""IntentSDK — MVP Intent Pipeline SDK with API-key authentication.

Provides submit_intent(), get_intent_status(), get_intent_result() per spec §4.
Agent identity is derived server-side from API key (spec §3B).

Reference: docs/spec-mvp-test.md v1.1, Sections 3B, 4.1–4.3
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from neo4j import AsyncGraphDatabase

from hassaleh.auth import verify_api_key
from hassaleh.capabilities.exec_ls import validate_exec_ls_path_quick
from hassaleh.errors import (
    AccessDeniedError,
    AuthenticationError,
    CapabilityDeniedError,
    CapabilityNotFoundError,
    CapabilityParamError,
)

log = logging.getLogger("hassaleh.intent_sdk")


class IntentSDK:
    """Agent-facing SDK for the MVP Intent Pipeline.

    Usage:
        sdk = IntentSDK(neo4j_uri=..., neo4j_user=..., neo4j_password=...)
        await sdk.connect()
        intent_id = await sdk.submit_intent(api_key, "exec-ls", {"path": "/app"})
        status = await sdk.get_intent_status(api_key, intent_id)
        result = await sdk.get_intent_result(api_key, intent_id)
        await sdk.close()
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
        log.info("IntentSDK connected to %s", self.neo4j_uri)

    async def close(self) -> None:
        """Close the Neo4j connection."""
        if self.driver:
            await self.driver.close()
            self.driver = None

    async def _authenticate(self, api_key: str) -> str:
        """Authenticate caller by API key, return agent_id.

        Queries all Agent nodes and verifies the bcrypt hash.
        Raises AuthenticationError if no match found.
        """
        async with self.driver.session() as session:
            result = await session.run(
                "MATCH (a:Agent) WHERE a.api_key_hash IS NOT NULL "
                "RETURN a.id AS agent_id, a.api_key_hash AS hash"
            )
            records = [record async for record in result]

        for record in records:
            if verify_api_key(api_key, record["hash"]):
                return record["agent_id"]

        raise AuthenticationError("Invalid API key")

    async def submit_intent(
        self,
        api_key: str,
        capability_id: str,
        params: dict,
    ) -> str:
        """Submit an Intent for Daemon processing.

        Args:
            api_key: Pre-shared API key (agent_id derived server-side).
            capability_id: e.g. "exec-ls"
            params: e.g. {"path": "/app"}

        Returns:
            intent_id (UUID string)
        """
        # Authenticate — derive agent_id from API key
        agent_id = await self._authenticate(api_key)

        # Verify capability exists
        async with self.driver.session() as session:
            result = await session.run(
                "MATCH (c:Capability {id: $cap_id}) RETURN c.id AS id",
                cap_id=capability_id,
            )
            record = await result.single()
            if record is None:
                raise CapabilityNotFoundError(
                    f"Capability '{capability_id}' not found"
                )

        # Verify agent has this capability
        async with self.driver.session() as session:
            result = await session.run(
                "MATCH (a:Agent {id: $agent_id})-[:HAS_CAPABILITY]->"
                "(c:Capability {id: $cap_id}) RETURN c.id AS id",
                agent_id=agent_id,
                cap_id=capability_id,
            )
            record = await result.single()
            if record is None:
                raise CapabilityDeniedError(
                    f"Agent '{agent_id}' lacks capability '{capability_id}'"
                )

        # Quick param validation at submit time (character + traversal only)
        if capability_id == "exec-ls":
            path = params.get("path", "")
            validate_exec_ls_path_quick(path)

        # Create Intent node + relationships in a single transaction
        intent_id = str(uuid.uuid4())
        params_json = json.dumps(params)

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
        agent_id = await self._authenticate(api_key)

        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (i:Intent {id: $id})
                OPTIONAL MATCH (i)-[:SUBMITTED_BY]->(a:Agent)
                RETURN i, a.id AS owner_id
            """, id=intent_id)
            record = await result.single()

        if record is None:
            raise ValueError(f"Intent '{intent_id}' not found")

        owner_id = record["owner_id"]
        if owner_id != agent_id:
            raise AccessDeniedError("Intent does not belong to caller")

        node = record["i"]
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
        agent_id = await self._authenticate(api_key)

        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (i:Intent {id: $id})
                OPTIONAL MATCH (i)-[:SUBMITTED_BY]->(a:Agent)
                RETURN i, a.id AS owner_id
            """, id=intent_id)
            record = await result.single()

        if record is None:
            raise ValueError(f"Intent '{intent_id}' not found")

        owner_id = record["owner_id"]
        if owner_id != agent_id:
            raise AccessDeniedError("Intent does not belong to caller")

        node = record["i"]
        return {
            "id": node["id"],
            "status": node["status"],
            "result": node.get("result"),
            "error": node.get("error"),
            "duration_ms": node.get("duration_ms"),
        }
