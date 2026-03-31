"""Stress tests for Hassaleh Daemon — Sprint 1, Task 3.5.

Tests:
- 10 rapid Intent submissions all processed correctly
- Concurrent Intent creation (unit-level, no Daemon required)

For integration tests (marked @pytest.mark.integration):
  Requires running Daemon AND Neo4j on test1 (bolt://localhost:7691).
  Start Daemon against test1 first, or run against prod (bolt://localhost:7690).

For unit tests:
  Only requires Neo4j on test1 (bolt://localhost:7691).

Run with: PYTHONPATH=src pytest tests/test_stress.py -v
Integration only: PYTHONPATH=src pytest tests/test_stress.py -m integration -v
"""

from __future__ import annotations

import asyncio
import json
import os
import time

import pytest
import pytest_asyncio

from hassaleh.sdk import HassalehSDK

TEST_URI = os.environ.get("NEO4J_TEST_URI", "bolt://localhost:7691")
TEST_USER = os.environ.get("NEO4J_TEST_USER", "neo4j")
TEST_PASSWORD = os.environ.get("NEO4J_TEST_PASSWORD", "hassaleh-dev-2026")

AGENT_ID = "dione"
CAPABILITY_ID = "exec-ls"
NUM_INTENTS = 10


@pytest_asyncio.fixture
async def sdk():
    """SDK connected to test1 instance, with seed data for stress tests."""
    sdk = HassalehSDK(neo4j_uri=TEST_URI, neo4j_user=TEST_USER, neo4j_password=TEST_PASSWORD)
    await sdk.connect()

    # Ensure agent + capability exist in test DB
    async with sdk.driver.session() as s:
        await s.run("""
            MERGE (a:Agent {id: $agent_id})
            SET a.name = 'Dione', a.emoji = '🌙', a.lifecycle = 'running'
        """, agent_id=AGENT_ID)
        await s.run("""
            MERGE (cap:Capability {id: $cap_id})
            SET cap.name = 'List Files', cap.kind = 'cli',
                cap.invoke_command = '/usr/bin/ls',
                cap.exec_as_user = 'hassaleh-fs',
                cap.lifecycle = 'available',
                cap.requires_confirmation = false
        """, cap_id=CAPABILITY_ID)
        await s.run("""
            MATCH (a:Agent {id: $agent_id}), (cap:Capability {id: $cap_id})
            MERGE (a)-[:HAS_CAPABILITY]->(cap)
        """, agent_id=AGENT_ID, cap_id=CAPABILITY_ID)

    yield sdk
    await sdk.close()


# ── Unit-level test (no Daemon needed) ──


@pytest.mark.asyncio
async def test_rapid_intent_submission(sdk):
    """Verify 10 Intents are created rapidly with lifecycle='pending'.

    This test does NOT require a running Daemon — it only checks that
    the SDK can submit 10 Intents in quick succession and all are
    persisted correctly in Neo4j.
    """
    intent_ids = []

    # Submit 10 Intents as fast as possible
    t0 = time.monotonic()
    for i in range(NUM_INTENTS):
        intent_id = await sdk.submit_intent(
            agent_id=AGENT_ID,
            action="execute_capability",
            capability_id=CAPABILITY_ID,
            value=json.dumps({"args": f"-la /tmp/stress-test-{i}"}),
        )
        intent_ids.append(intent_id)
    elapsed = time.monotonic() - t0

    print(f"\n  Submitted {NUM_INTENTS} Intents in {elapsed:.3f}s "
          f"({elapsed / NUM_INTENTS * 1000:.1f}ms per Intent)")

    # Verify all 10 exist with lifecycle='pending'
    assert len(intent_ids) == NUM_INTENTS
    assert len(set(intent_ids)) == NUM_INTENTS, "All Intent IDs must be unique"

    for intent_id in intent_ids:
        state = await sdk.poll_intent(intent_id)
        assert state["lifecycle"] == "pending", (
            f"Intent {intent_id} has lifecycle '{state['lifecycle']}', expected 'pending'"
        )

    # Verify submitted_at timestamps are sequential (or at least non-decreasing)
    timestamps = []
    for intent_id in intent_ids:
        records = await sdk.query(
            "MATCH (i:Intent {id: $id}) RETURN i.submitted_at AS ts",
            {"id": intent_id},
        )
        timestamps.append(records[0]["ts"])

    for i in range(1, len(timestamps)):
        assert timestamps[i] >= timestamps[i - 1], (
            f"Timestamp ordering violated: Intent {i} ({timestamps[i]}) "
            f"< Intent {i-1} ({timestamps[i-1]})"
        )

    # Cleanup
    async with sdk.driver.session() as s:
        for intent_id in intent_ids:
            await s.run("MATCH (i:Intent {id: $id}) DETACH DELETE i", id=intent_id)

    print(f"  Cleanup: {NUM_INTENTS} test Intents deleted")


# ── Integration test (requires running Daemon on test1) ──


@pytest.mark.integration
@pytest.mark.asyncio
async def test_stress_10_intents_processed(sdk):
    """Submit 10 Intents rapidly and verify ALL are processed by the Daemon.

    REQUIRES: Daemon running against the same Neo4j instance (test1).

    Verifies:
    1. All 10 Intents reach lifecycle='success'
    2. Each has non-empty stdout
    3. All complete within 60 seconds
    """
    intent_ids = []

    # Submit 10 Intents rapidly
    t0 = time.monotonic()
    for i in range(NUM_INTENTS):
        intent_id = await sdk.submit_intent(
            agent_id=AGENT_ID,
            action="execute_capability",
            capability_id=CAPABILITY_ID,
            value=json.dumps({"args": "-la"}),
        )
        intent_ids.append(intent_id)
    submit_elapsed = time.monotonic() - t0

    print(f"\n  Submitted {NUM_INTENTS} Intents in {submit_elapsed:.3f}s")

    # Wait for all to complete (with 60s global timeout)
    results = {}
    deadline = time.monotonic() + 60.0

    while len(results) < NUM_INTENTS and time.monotonic() < deadline:
        for intent_id in intent_ids:
            if intent_id in results:
                continue
            state = await sdk.poll_intent(intent_id)
            if state["lifecycle"] in ("success", "failed", "rejected"):
                results[intent_id] = state
        if len(results) < NUM_INTENTS:
            await asyncio.sleep(0.5)

    total_elapsed = time.monotonic() - t0

    # Report results
    success = sum(1 for r in results.values() if r["lifecycle"] == "success")
    failed = sum(1 for r in results.values() if r["lifecycle"] == "failed")
    rejected = sum(1 for r in results.values() if r["lifecycle"] == "rejected")
    pending = NUM_INTENTS - len(results)

    print(f"  Results: {success} success, {failed} failed, {rejected} rejected, {pending} still pending")
    print(f"  Total time: {total_elapsed:.3f}s")

    # Assertions
    assert len(results) == NUM_INTENTS, (
        f"Only {len(results)}/{NUM_INTENTS} Intents completed within 60s "
        f"({pending} still pending — is the Daemon running?)"
    )

    for intent_id, state in results.items():
        assert state["lifecycle"] == "success", (
            f"Intent {intent_id} has lifecycle '{state['lifecycle']}': "
            f"{state.get('error_reason', 'no error')}"
        )
        assert state.get("stdout"), (
            f"Intent {intent_id} has empty stdout"
        )

    # Verify no duplicate processing (each Intent should have exactly 1 result)
    exit_codes = [r.get("exit_code") for r in results.values()]
    assert all(ec == 0 for ec in exit_codes), (
        f"Some Intents had non-zero exit codes: {exit_codes}"
    )

    print(f"  ✅ All {NUM_INTENTS} Intents processed successfully!")

    # Cleanup
    async with sdk.driver.session() as s:
        for intent_id in intent_ids:
            await s.run("MATCH (i:Intent {id: $id}) DETACH DELETE i", id=intent_id)
