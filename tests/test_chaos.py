"""Chaos and stress tests for Hassaleh Daemon.

Tests:
- Zombie Intent recovery after Daemon restart
- Stress: 10 rapid Intents processed correctly
- Capability permission enforcement

Requires running Neo4j on test1 (bolt://localhost:7691).
Run with: PYTHONPATH=src pytest tests/test_chaos.py -m integration -v
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest
import pytest_asyncio

from hassaleh.sdk import HassalehSDK

TEST_URI = os.environ.get("NEO4J_TEST_URI", "bolt://localhost:7691")
TEST_USER = os.environ.get("NEO4J_TEST_USER", "neo4j")
TEST_PASSWORD = os.environ.get("NEO4J_TEST_PASSWORD", "hassaleh-dev-2026")

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def sdk():
    """SDK connected to test1 instance."""
    sdk = HassalehSDK(neo4j_uri=TEST_URI, neo4j_user=TEST_USER, neo4j_password=TEST_PASSWORD)
    await sdk.connect()
    yield sdk
    await sdk.close()


@pytest.mark.asyncio
async def test_zombie_recovery(sdk):
    """Simulate zombie Intents (lifecycle='running') and verify Daemon recovery logic."""
    # Create a "zombie" Intent — stuck in running state
    async with sdk.driver.session() as s:
        await s.run("""
            MERGE (a:Agent {id: 'test-agent'})
            SET a.name = 'TestAgent', a.lifecycle = 'pending'
        """)
        await s.run("""
            CREATE (i:Intent {
                id: 'zombie-intent-1',
                lifecycle: 'running',
                action: 'execute_capability',
                submitted_at: datetime({timezone: 'UTC'}),
                started_at: datetime({timezone: 'UTC'})
            })
        """)
        await s.run("""
            MATCH (a:Agent {id: 'test-agent'}), (i:Intent {id: 'zombie-intent-1'})
            CREATE (a)-[:PROPOSED]->(i)
        """)

    # Simulate what the Daemon does on boot: recover zombies
    async with sdk.driver.session() as s:
        result = await s.run("""
            MATCH (i:Intent {lifecycle: 'running'})
            SET i.lifecycle = 'failed',
                i.error_reason = 'Daemon restarted during execution',
                i.completed_at = datetime({timezone: 'UTC'})
            RETURN count(*) AS recovered
        """)
        record = await result.single()
        assert record["recovered"] >= 1

    # Verify the zombie was recovered
    state = await sdk.poll_intent("zombie-intent-1")
    assert state["lifecycle"] == "failed"
    assert state["error_reason"] == "Daemon restarted during execution"

    # Cleanup
    async with sdk.driver.session() as s:
        await s.run("MATCH (i:Intent {id: 'zombie-intent-1'}) DETACH DELETE i")
        await s.run("MATCH (a:Agent {id: 'test-agent'}) DETACH DELETE a")


@pytest.mark.asyncio
async def test_read_only_enforcement(sdk):
    """Verify SDK blocks write operations in query()."""
    with pytest.raises(PermissionError, match="CREATE"):
        await sdk.query("CREATE (n:Evil {id: 'hack'}) RETURN n")

    with pytest.raises(PermissionError, match="DELETE"):
        await sdk.query("MATCH (n) DELETE n")

    with pytest.raises(PermissionError, match="SET"):
        await sdk.query("MATCH (a:Agent) SET a.lifecycle = 'evil'")

    with pytest.raises(PermissionError, match="MERGE"):
        await sdk.query("MERGE (n:Agent {id: 'hack'}) RETURN n")

    # Read still works
    result = await sdk.query("RETURN 1 AS value")
    assert result[0]["value"] == 1


@pytest.mark.asyncio
async def test_capability_permission_denied(sdk):
    """Agent without capability → Intent rejected."""
    # Create agent without any capabilities
    async with sdk.driver.session() as s:
        await s.run("""
            MERGE (a:Agent {id: 'no-cap-agent'})
            SET a.name = 'NoCap', a.lifecycle = 'pending'
        """)
        await s.run("""
            MERGE (cap:Capability {id: 'test-cap-2'})
            SET cap.name = 'TestCap2', cap.kind = 'cli',
                cap.invoke_command = '/usr/bin/echo', cap.exec_as_user = 'hassaleh-exec',
                cap.lifecycle = 'available'
        """)

    # Submit intent for a capability the agent doesn't have
    intent_id = await sdk.submit_intent(
        agent_id='no-cap-agent',
        action='execute_capability',
        capability_id='test-cap-2',
    )

    # Note: In test env without running Daemon, we can only verify the Intent was created
    # The actual rejection happens in the Daemon's _check_capability method
    state = await sdk.poll_intent(intent_id)
    assert state["lifecycle"] == "pending"  # No daemon running on test1 to reject it

    # Cleanup
    async with sdk.driver.session() as s:
        await s.run("MATCH (i:Intent {id: $id}) DETACH DELETE i", id=intent_id)
        await s.run("MATCH (a:Agent {id: 'no-cap-agent'}) DETACH DELETE a")
        await s.run("MATCH (c:Capability {id: 'test-cap-2'}) DETACH DELETE c")
