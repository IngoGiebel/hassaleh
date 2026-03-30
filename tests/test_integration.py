"""Integration tests for Hassaleh SDK — requires running Neo4j.

Uses test1 instance: bolt://localhost:7691
Schema + seed must be applied before running.

Run with: pytest tests/test_integration.py -m integration -v
"""

from __future__ import annotations

import json
import os

import pytest
import pytest_asyncio

from hassaleh.sdk import HassalehSDK

# Test instance connection
TEST_URI = os.environ.get("NEO4J_TEST_URI", "bolt://localhost:7691")
TEST_USER = os.environ.get("NEO4J_TEST_USER", "neo4j")
TEST_PASSWORD = os.environ.get("NEO4J_TEST_PASSWORD", "hassaleh-dev-2026")


pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def sdk():
    """Create and connect an SDK instance to the test database."""
    sdk = HassalehSDK(
        neo4j_uri=TEST_URI,
        neo4j_user=TEST_USER,
        neo4j_password=TEST_PASSWORD,
    )
    await sdk.connect()
    yield sdk
    await sdk.close()


@pytest_asyncio.fixture
async def seeded_sdk(sdk):
    """SDK with seed data applied to test database."""
    # Apply seed data (idempotent via MERGE)
    async with sdk.driver.session() as session:
        # Minimal seed for tests
        await session.run("""
            MERGE (dc:DaemonConfig {id: 'default'})
            SET dc.tick_interval_ms = 1000,
                dc.intent_timeout_default_sec = 300,
                dc.max_concurrent_actions = 10
        """)
        await session.run("""
            MERGE (qc:QueryConfig {id: 'default'})
            SET qc.default_timeout_ms = 3000,
                qc.max_result_rows = 10000,
                qc.report_query_timeout_ms = 30000
        """)
        await session.run("""
            MERGE (a:Agent {id: 'test-agent'})
            SET a.name = 'TestAgent', a.lifecycle = 'pending'
        """)
        await session.run("""
            MERGE (cap:Capability {id: 'test-cap'})
            SET cap.name = 'Test Capability', cap.kind = 'cli',
                cap.invoke_command = '/usr/bin/echo', cap.exec_as_user = 'hassaleh-exec',
                cap.lifecycle = 'available'
        """)
        await session.run("""
            MATCH (a:Agent {id: 'test-agent'}), (cap:Capability {id: 'test-cap'})
            MERGE (a)-[:HAS_CAPABILITY]->(cap)
        """)
        await session.run("""
            MERGE (t:Task {id: 'test-task'})
            SET t.name = 'Integration Test Task', t.lifecycle = 'pending'
        """)
        await session.run("""
            MATCH (t:Task {id: 'test-task'}), (a:Agent {id: 'test-agent'})
            MERGE (t)-[:ASSIGNED_TO]->(a)
        """)

    yield sdk

    # Cleanup test data
    async with sdk.driver.session() as session:
        await session.run("""
            MATCH (n) WHERE n.id IN ['test-agent', 'test-cap', 'test-task']
            DETACH DELETE n
        """)
        # Clean up any test Intents
        await session.run("""
            MATCH (i:Intent) WHERE i.id STARTS WITH 'test-' OR
                  EXISTS { MATCH (:Agent {id: 'test-agent'})-[:PROPOSED]->(i) }
            DETACH DELETE i
        """)


@pytest.mark.asyncio
async def test_connect_and_query(sdk):
    """Test basic connection and query."""
    result = await sdk.query("RETURN 1 AS value")
    assert len(result) == 1
    assert result[0]["value"] == 1


@pytest.mark.asyncio
async def test_load_query_config(sdk):
    """Test that QueryConfig is loaded from graph."""
    # QueryConfig should be loaded during connect (if seed data exists)
    assert sdk.query_config["default_timeout_ms"] > 0
    assert sdk.query_config["max_result_rows"] > 0


@pytest.mark.asyncio
async def test_my_tasks(seeded_sdk):
    """Test querying tasks assigned to an agent."""
    tasks = await seeded_sdk.my_tasks("test-agent")
    assert len(tasks) >= 1

    task_ids = [t["id"] for t in tasks]
    assert "test-task" in task_ids


@pytest.mark.asyncio
async def test_submit_intent(seeded_sdk):
    """Test Intent creation in the graph."""
    intent_id = await seeded_sdk.submit_intent(
        agent_id="test-agent",
        action="execute_capability",
        capability_id="test-cap",
        value=json.dumps({"args": "hello"}),
    )

    # Verify Intent exists in graph
    state = await seeded_sdk.poll_intent(intent_id)
    assert state["lifecycle"] == "pending"

    # Cleanup
    async with seeded_sdk.driver.session() as session:
        await session.run(
            "MATCH (i:Intent {id: $id}) DETACH DELETE i",
            id=intent_id,
        )


@pytest.mark.asyncio
async def test_submit_intent_creates_targets_edge(seeded_sdk):
    """Test that submit_intent creates TARGETS edge to capability."""
    intent_id = await seeded_sdk.submit_intent(
        agent_id="test-agent",
        action="execute_capability",
        capability_id="test-cap",
    )

    # Verify TARGETS edge
    records = await seeded_sdk.query("""
        MATCH (i:Intent {id: $id})-[:TARGETS]->(cap:Capability)
        RETURN cap.id AS cap_id
    """, {"id": intent_id})

    assert len(records) == 1
    assert records[0]["cap_id"] == "test-cap"

    # Cleanup
    async with seeded_sdk.driver.session() as session:
        await session.run(
            "MATCH (i:Intent {id: $id}) DETACH DELETE i",
            id=intent_id,
        )


@pytest.mark.asyncio
async def test_agent_info(seeded_sdk):
    """Test agent info retrieval."""
    agent = await seeded_sdk.agent_info("test-agent")
    assert agent is not None
    assert agent["name"] == "TestAgent"
    assert agent["lifecycle"] == "pending"
