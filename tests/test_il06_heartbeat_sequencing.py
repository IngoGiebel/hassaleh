from __future__ import annotations

import asyncio
import os

import pytest
import pytest_asyncio

from hassaleh.auth import generate_api_key, hash_api_key, lookup_hash
from hassaleh.heartbeat_sdk import HeartbeatSDK

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7690")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "hassaleh")


@pytest_asyncio.fixture
async def il06_sdk():
    api_key = generate_api_key()
    sdk = HeartbeatSDK(
        neo4j_uri=NEO4J_URI,
        neo4j_user=NEO4J_USER,
        neo4j_password=NEO4J_PASSWORD,
    )
    await sdk.connect()

    async with sdk.driver.session() as session:
        await session.run(
            """
            MERGE (a:Agent {id: 'hb-il06-agent'})
            SET a.name = 'IL06 Heartbeat Agent',
                a.api_key_hash = $hash,
                a.api_key_lookup = $lookup,
                a.lifecycle = 'pending',
                a.last_heartbeat = null,
                a.previous_heartbeat = null,
                a.heartbeat_count = 0,
                a.heartbeat_token = null,
                a.heartbeat_source = null
            """,
            hash=hash_api_key(api_key),
            lookup=lookup_hash(api_key),
        )

    yield sdk, api_key

    async with sdk.driver.session() as session:
        await session.run("MATCH (a:Agent {id: 'hb-il06-agent'}) DETACH DELETE a")
    await sdk.close()


@pytest.mark.asyncio
async def test_first_heartbeat_keeps_previous_heartbeat_null(il06_sdk):
    sdk, api_key = il06_sdk

    result = await sdk.heartbeat(api_key)

    assert result["heartbeat_count"] == 1
    assert result["last_heartbeat"] is not None
    assert result["previous_heartbeat"] is None


@pytest.mark.asyncio
async def test_previous_heartbeat_equals_prior_last_heartbeat(il06_sdk):
    sdk, api_key = il06_sdk

    first = await sdk.heartbeat(api_key)
    first_last = first["last_heartbeat"]
    first_token = first["heartbeat_token"]

    # Use a real gap large enough to pass the server-side 60s rate limit so we
    # can assert against the genuine prior last_heartbeat value.
    await asyncio.sleep(62.5)

    second = await sdk.heartbeat(api_key, heartbeat_token=first_token)

    assert second["heartbeat_count"] == 2
    assert second["last_heartbeat"] is not None
    assert second["previous_heartbeat"] is not None
    assert second["previous_heartbeat"] == first_last
    assert second["last_heartbeat"] != first_last
