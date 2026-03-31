"""Messaging + Discussion tests — Sprint 6.

Tests the cursor-based message system and discussion protocol.
Requires Neo4j on test1 (bolt://localhost:7691).
Run with: PYTHONPATH=src pytest tests/test_messaging.py -v
"""

from __future__ import annotations

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
    """SDK connected to test1 with test agents."""
    sdk = HassalehSDK(neo4j_uri=TEST_URI, neo4j_user=TEST_USER, neo4j_password=TEST_PASSWORD)
    await sdk.connect()

    # Create test agents + task
    async with sdk.driver.session() as s:
        await s.run("""
            MERGE (a:Agent {id: 'agent-alice'}) SET a.name = 'Alice', a.lifecycle = 'running'
        """)
        await s.run("""
            MERGE (b:Agent {id: 'agent-bob'}) SET b.name = 'Bob', b.lifecycle = 'running'
        """)
        await s.run("""
            MERGE (t:Task {id: 'task-test'}) SET t.name = 'Test Task', t.lifecycle = 'pending'
        """)

    yield sdk

    # Cleanup
    async with sdk.driver.session() as s:
        await s.run("MATCH (m:Message) DETACH DELETE m")
        await s.run("MATCH (d:Discussion) DETACH DELETE d")
        await s.run("MATCH (a:Agent) WHERE a.id IN ['agent-alice', 'agent-bob'] DETACH DELETE a")
        await s.run("MATCH (t:Task {id: 'task-test'}) DETACH DELETE t")

    await sdk.close()


@pytest.mark.asyncio
async def test_send_and_read_message(sdk):
    """Send a message and read it back."""
    msg_id = await sdk.send_message("agent-alice", "Hello Bob!", "task-test", "Task")

    messages = await sdk.read_messages("agent-bob", "task-test")
    assert len(messages) >= 1
    assert any(m["content"] == "Hello Bob!" for m in messages)


@pytest.mark.asyncio
async def test_message_ordering(sdk):
    """Messages maintain NEXT chain ordering."""
    await sdk.send_message("agent-alice", "First", "task-test", "Task")
    await sdk.send_message("agent-bob", "Second", "task-test", "Task")
    await sdk.send_message("agent-alice", "Third", "task-test", "Task")

    messages = await sdk.read_messages("agent-bob", "task-test")
    contents = [m["content"] for m in messages]
    assert contents == ["First", "Second", "Third"]


@pytest.mark.asyncio
async def test_cursor_advancement(sdk):
    """LAST_READ cursor filters already-read messages."""
    msg1 = await sdk.send_message("agent-alice", "Msg 1", "task-test", "Task")
    await sdk.send_message("agent-alice", "Msg 2", "task-test", "Task")

    # Bob reads all
    messages = await sdk.read_messages("agent-bob", "task-test")
    assert len(messages) == 2

    # Bob advances cursor to msg1
    await sdk.advance_cursor("agent-bob", msg1)

    # Bob should only see msg2 now
    messages = await sdk.read_messages("agent-bob", "task-test")
    assert len(messages) == 1
    assert messages[0]["content"] == "Msg 2"


@pytest.mark.asyncio
async def test_discussion_lifecycle(sdk):
    """Create discussion, contribute, resolve."""
    disc_id = await sdk.create_discussion("Should we use Earley?", "task-test", "Task")

    await sdk.contribute_to_discussion(
        "agent-alice", disc_id, "pro-earley", "Handles ambiguity well"
    )
    await sdk.contribute_to_discussion(
        "agent-bob", disc_id, "pro-lalr", "Faster for simple grammars"
    )

    await sdk.resolve_discussion(
        "agent-alice", disc_id, "Earley — ambiguity handling is critical"
    )

    # Verify via query
    records = await sdk.query("""
        MATCH (d:Discussion {id: $id})
        RETURN d.lifecycle AS lifecycle, d.resolution AS resolution
    """, {"id": disc_id})

    assert records[0]["lifecycle"] == "success"
    assert "Earley" in records[0]["resolution"]
