"""Heartbeat System Tests — Phase B3 (TDD).

Tests define expected behavior per spec-heartbeat.md v1.1 (post security review).
Tests are written BEFORE implementation — they should fail clearly on missing
modules/classes/methods, guiding the implementation.

Covers:
- SDK: heartbeat(api_key, heartbeat_token=None)
- Agent authentication: API key → agent_id derived server-side
- Replay protection: chained heartbeat_token
- Lifecycle transitions: pending → active → stale → inactive → disabled
- Rate limiting: 60s minimum interval enforced server-side
- Heartbeat source tracking: "agent" vs "cron"
- Recovery from stale/inactive states (token chain reset)
- Disabled agent rejection
- Concurrent heartbeats across multiple agents
- Edge cases: missing key, invalid key, token mismatch

Reference: docs/spec-heartbeat.md v1.1
Neo4j dev: bolt://localhost:7690, user neo4j, password hassaleh
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Imports from modules that DO NOT YET EXIST.
# TDD: these imports define the target module structure.  Tests will fail
# with ImportError until the modules are created.
# ---------------------------------------------------------------------------
from hassaleh.errors import (
    AgentDisabledError,
    AuthenticationError,
    HeartbeatTokenMismatchError,
)
from hassaleh.heartbeat_sdk import HeartbeatSDK
from hassaleh.auth import (
    hash_api_key,
    generate_api_key,
    lookup_hash,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NEO4J_URI = os.environ.get("NEO4J_TEST_URI", "bolt://localhost:7690")
NEO4J_USER = os.environ.get("NEO4J_TEST_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_TEST_PASSWORD", "hassaleh")

HEARTBEAT_STALE_SECONDS = 7200       # 2h — spec §4
HEARTBEAT_INACTIVE_SECONDS = 86400   # 24h — spec §4
HEARTBEAT_MIN_INTERVAL_SECONDS = 60  # 1m — spec §4


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def api_key_pair():
    """Generate a fresh API key + its bcrypt hash + SHA-256 lookup for a test agent."""
    raw_key = generate_api_key()
    hashed = hash_api_key(raw_key)
    lookup = lookup_hash(raw_key)
    return raw_key, hashed, lookup


@pytest.fixture
def second_api_key_pair():
    """Second agent's API key pair — for multi-agent tests."""
    raw_key = generate_api_key()
    hashed = hash_api_key(raw_key)
    lookup = lookup_hash(raw_key)
    return raw_key, hashed, lookup


@pytest.fixture
def third_api_key_pair():
    """Third agent's API key pair — for concurrent heartbeat tests."""
    raw_key = generate_api_key()
    hashed = hash_api_key(raw_key)
    lookup = lookup_hash(raw_key)
    return raw_key, hashed, lookup


@pytest.fixture
def fourth_api_key_pair():
    """Fourth agent's API key pair — for concurrent heartbeat tests."""
    raw_key = generate_api_key()
    hashed = hash_api_key(raw_key)
    lookup = lookup_hash(raw_key)
    return raw_key, hashed, lookup


# ── Integration fixtures (require running Neo4j) ─────────────────────────

@pytest_asyncio.fixture
async def heartbeat_sdk(api_key_pair):
    """HeartbeatSDK connected to test Neo4j with a single registered agent.

    Seeds agent "hb-test-agent" in lifecycle=pending, no prior heartbeats.
    """
    raw_key, hashed, lookup = api_key_pair
    sdk = HeartbeatSDK(
        neo4j_uri=NEO4J_URI,
        neo4j_user=NEO4J_USER,
        neo4j_password=NEO4J_PASSWORD,
    )
    await sdk.connect()

    async with sdk.driver.session() as session:
        await session.run("""
            MERGE (a:Agent {id: 'hb-test-agent'})
            SET a.name = 'Heartbeat Test Agent',
                a.api_key_hash = $hash,
                a.api_key_lookup = $lookup,
                a.lifecycle = 'pending',
                a.last_heartbeat = null,
                a.previous_heartbeat = null,
                a.heartbeat_count = 0,
                a.heartbeat_token = null,
                a.heartbeat_source = null
        """, hash=hashed, lookup=lookup)

    yield sdk, raw_key

    # Cleanup
    async with sdk.driver.session() as session:
        await session.run("""
            MATCH (a:Agent {id: 'hb-test-agent'}) DETACH DELETE a
        """)
    await sdk.close()


@pytest_asyncio.fixture
async def active_agent_sdk(api_key_pair):
    """HeartbeatSDK with an agent already in lifecycle=active with a known token.

    Useful for subsequent-heartbeat and rate-limiting tests.
    """
    raw_key, hashed, lookup = api_key_pair
    sdk = HeartbeatSDK(
        neo4j_uri=NEO4J_URI,
        neo4j_user=NEO4J_USER,
        neo4j_password=NEO4J_PASSWORD,
    )
    await sdk.connect()

    # Seed an active agent with a known heartbeat token and a timestamp >60s ago
    # so the first test heartbeat won't be rate-limited.
    async with sdk.driver.session() as session:
        await session.run("""
            MERGE (a:Agent {id: 'hb-active-agent'})
            SET a.name = 'Active Heartbeat Agent',
                a.api_key_hash = $hash,
                a.api_key_lookup = $lookup,
                a.lifecycle = 'active',
                a.last_heartbeat = datetime() - duration('PT120S'),
                a.previous_heartbeat = null,
                a.heartbeat_count = 5,
                a.heartbeat_token = 'known-token-abc',
                a.heartbeat_source = 'agent'
        """, hash=hashed, lookup=lookup)

    yield sdk, raw_key, "known-token-abc"

    async with sdk.driver.session() as session:
        await session.run("""
            MATCH (a:Agent {id: 'hb-active-agent'}) DETACH DELETE a
        """)
    await sdk.close()


@pytest_asyncio.fixture
async def multi_agent_sdk(api_key_pair, second_api_key_pair,
                          third_api_key_pair, fourth_api_key_pair):
    """Four agents registered — for concurrent heartbeat tests (§7.7)."""
    keys = [api_key_pair, second_api_key_pair,
            third_api_key_pair, fourth_api_key_pair]
    agent_ids = ["hb-multi-a", "hb-multi-b", "hb-multi-c", "hb-multi-d"]

    sdk = HeartbeatSDK(
        neo4j_uri=NEO4J_URI,
        neo4j_user=NEO4J_USER,
        neo4j_password=NEO4J_PASSWORD,
    )
    await sdk.connect()

    async with sdk.driver.session() as session:
        for agent_id, (raw_key, hashed, lookup) in zip(agent_ids, keys):
            await session.run("""
                MERGE (a:Agent {id: $id})
                SET a.name = $id,
                    a.api_key_hash = $hash,
                    a.api_key_lookup = $lookup,
                    a.lifecycle = 'pending',
                    a.last_heartbeat = null,
                    a.heartbeat_count = 0,
                    a.heartbeat_token = null
            """, id=agent_id, hash=hashed, lookup=lookup)

    raw_keys = [k[0] for k in keys]
    yield sdk, agent_ids, raw_keys

    async with sdk.driver.session() as session:
        await session.run("""
            MATCH (a:Agent) WHERE a.id STARTS WITH 'hb-multi-'
            DETACH DELETE a
        """)
    await sdk.close()


@pytest_asyncio.fixture
async def disabled_agent_sdk(api_key_pair):
    """HeartbeatSDK with an agent in lifecycle=disabled (terminal state)."""
    raw_key, hashed, lookup = api_key_pair
    sdk = HeartbeatSDK(
        neo4j_uri=NEO4J_URI,
        neo4j_user=NEO4J_USER,
        neo4j_password=NEO4J_PASSWORD,
    )
    await sdk.connect()

    async with sdk.driver.session() as session:
        await session.run("""
            MERGE (a:Agent {id: 'hb-disabled-agent'})
            SET a.name = 'Disabled Agent',
                a.api_key_hash = $hash,
                a.api_key_lookup = $lookup,
                a.lifecycle = 'disabled',
                a.last_heartbeat = datetime() - duration('P2D'),
                a.heartbeat_count = 100,
                a.heartbeat_token = null,
                a.heartbeat_source = null
        """, hash=hashed, lookup=lookup)

    yield sdk, raw_key

    async with sdk.driver.session() as session:
        await session.run("""
            MATCH (a:Agent {id: 'hb-disabled-agent'}) DETACH DELETE a
        """)
    await sdk.close()


@pytest_asyncio.fixture
async def stale_agent_sdk(api_key_pair):
    """HeartbeatSDK with an agent in lifecycle=stale, token cleared."""
    raw_key, hashed, lookup = api_key_pair
    sdk = HeartbeatSDK(
        neo4j_uri=NEO4J_URI,
        neo4j_user=NEO4J_USER,
        neo4j_password=NEO4J_PASSWORD,
    )
    await sdk.connect()

    async with sdk.driver.session() as session:
        await session.run("""
            MERGE (a:Agent {id: 'hb-stale-agent'})
            SET a.name = 'Stale Agent',
                a.api_key_hash = $hash,
                a.api_key_lookup = $lookup,
                a.lifecycle = 'stale',
                a.last_heartbeat = datetime() - duration('PT3H'),
                a.heartbeat_count = 20,
                a.heartbeat_token = null,
                a.heartbeat_source = 'agent'
        """, hash=hashed, lookup=lookup)

    yield sdk, raw_key

    async with sdk.driver.session() as session:
        await session.run("""
            MATCH (a:Agent {id: 'hb-stale-agent'}) DETACH DELETE a
        """)
    await sdk.close()


@pytest_asyncio.fixture
async def inactive_agent_sdk(api_key_pair):
    """HeartbeatSDK with an agent in lifecycle=inactive, token cleared."""
    raw_key, hashed, lookup = api_key_pair
    sdk = HeartbeatSDK(
        neo4j_uri=NEO4J_URI,
        neo4j_user=NEO4J_USER,
        neo4j_password=NEO4J_PASSWORD,
    )
    await sdk.connect()

    async with sdk.driver.session() as session:
        await session.run("""
            MERGE (a:Agent {id: 'hb-inactive-agent'})
            SET a.name = 'Inactive Agent',
                a.api_key_hash = $hash,
                a.api_key_lookup = $lookup,
                a.lifecycle = 'inactive',
                a.last_heartbeat = datetime() - duration('P2D'),
                a.heartbeat_count = 10,
                a.heartbeat_token = null,
                a.heartbeat_source = null
        """, hash=hashed, lookup=lookup)

    yield sdk, raw_key

    async with sdk.driver.session() as session:
        await session.run("""
            MATCH (a:Agent {id: 'hb-inactive-agent'}) DETACH DELETE a
        """)
    await sdk.close()


# ── Helper ────────────────────────────────────────────────────────────────

async def get_agent_node(sdk, agent_id: str) -> dict:
    """Read an Agent node's properties from Neo4j.

    Neo4j removes null-valued properties from nodes, so we explicitly query
    all heartbeat-relevant fields and let Cypher return null for missing ones.
    """
    async with sdk.driver.session() as session:
        result = await session.run("""
            MATCH (a:Agent {id: $id})
            RETURN a.id AS id,
                   a.name AS name,
                   a.lifecycle AS lifecycle,
                   a.last_heartbeat AS last_heartbeat,
                   a.previous_heartbeat AS previous_heartbeat,
                   a.heartbeat_count AS heartbeat_count,
                   a.heartbeat_token AS heartbeat_token,
                   a.heartbeat_source AS heartbeat_source,
                   a.api_key_hash AS api_key_hash,
                   a.api_key_lookup AS api_key_lookup
        """, id=agent_id)
        record = await result.single()
    assert record is not None, f"Agent {agent_id} not found in graph"
    return dict(record)


# ═══════════════════════════════════════════════════════════════════════════
# §7.1 — First Heartbeat
# ═══════════════════════════════════════════════════════════════════════════

class TestFirstHeartbeat:
    """Spec §7.1 — First heartbeat from a pending agent."""

    @pytest.mark.integration
    async def test_pending_to_active(self, heartbeat_sdk):
        """First heartbeat transitions lifecycle from pending → active."""
        sdk, api_key = heartbeat_sdk
        result = await sdk.heartbeat(api_key, heartbeat_token=None)

        assert result["lifecycle"] == "active"

    @pytest.mark.integration
    async def test_last_heartbeat_set(self, heartbeat_sdk):
        """First heartbeat sets last_heartbeat timestamp."""
        sdk, api_key = heartbeat_sdk
        result = await sdk.heartbeat(api_key, heartbeat_token=None)

        assert result["last_heartbeat"] is not None

    @pytest.mark.integration
    async def test_heartbeat_count_is_one(self, heartbeat_sdk):
        """First heartbeat sets heartbeat_count to 1."""
        sdk, api_key = heartbeat_sdk
        result = await sdk.heartbeat(api_key, heartbeat_token=None)

        assert result["heartbeat_count"] == 1

    @pytest.mark.integration
    async def test_heartbeat_token_returned(self, heartbeat_sdk):
        """First heartbeat returns a non-null heartbeat_token."""
        sdk, api_key = heartbeat_sdk
        result = await sdk.heartbeat(api_key, heartbeat_token=None)

        assert result["heartbeat_token"] is not None
        assert isinstance(result["heartbeat_token"], str)
        assert len(result["heartbeat_token"]) > 0

    @pytest.mark.integration
    async def test_agent_id_in_response(self, heartbeat_sdk):
        """Response includes the agent_id derived from the API key."""
        sdk, api_key = heartbeat_sdk
        result = await sdk.heartbeat(api_key, heartbeat_token=None)

        assert result["agent_id"] == "hb-test-agent"

    @pytest.mark.integration
    async def test_neo4j_state_after_first_heartbeat(self, heartbeat_sdk):
        """Verify the Agent node in Neo4j has correct state after first heartbeat."""
        sdk, api_key = heartbeat_sdk
        await sdk.heartbeat(api_key, heartbeat_token=None)

        node = await get_agent_node(sdk, "hb-test-agent")
        assert node["lifecycle"] == "active"
        assert node["last_heartbeat"] is not None
        assert node["heartbeat_count"] == 1
        assert node["heartbeat_token"] is not None


# ═══════════════════════════════════════════════════════════════════════════
# §7.2 — Subsequent Heartbeat
# ═══════════════════════════════════════════════════════════════════════════

class TestSubsequentHeartbeat:
    """Spec §7.2 — Subsequent heartbeat with valid token chain."""

    @pytest.mark.integration
    async def test_previous_heartbeat_updated(self, active_agent_sdk):
        """previous_heartbeat should equal the old last_heartbeat."""
        sdk, api_key, token = active_agent_sdk
        node_before = await get_agent_node(sdk, "hb-active-agent")
        old_last = node_before["last_heartbeat"]

        result = await sdk.heartbeat(api_key, heartbeat_token=token)

        assert result["previous_heartbeat"] is not None
        # The previous_heartbeat in the response should reflect the old last_heartbeat
        node_after = await get_agent_node(sdk, "hb-active-agent")
        assert node_after["previous_heartbeat"] == old_last

    @pytest.mark.integration
    async def test_last_heartbeat_updated(self, active_agent_sdk):
        """last_heartbeat should be more recent than before."""
        sdk, api_key, token = active_agent_sdk
        node_before = await get_agent_node(sdk, "hb-active-agent")

        result = await sdk.heartbeat(api_key, heartbeat_token=token)

        assert result["last_heartbeat"] is not None
        node_after = await get_agent_node(sdk, "hb-active-agent")
        assert node_after["last_heartbeat"] > node_before["last_heartbeat"]

    @pytest.mark.integration
    async def test_heartbeat_count_incremented(self, active_agent_sdk):
        """heartbeat_count should increment by 1."""
        sdk, api_key, token = active_agent_sdk
        result = await sdk.heartbeat(api_key, heartbeat_token=token)

        assert result["heartbeat_count"] == 6  # was 5, now 6

    @pytest.mark.integration
    async def test_new_token_differs(self, active_agent_sdk):
        """New heartbeat_token should differ from the previous one."""
        sdk, api_key, token = active_agent_sdk
        result = await sdk.heartbeat(api_key, heartbeat_token=token)

        assert result["heartbeat_token"] != token

    @pytest.mark.integration
    async def test_lifecycle_stays_active(self, active_agent_sdk):
        """Active agent stays active after subsequent heartbeat."""
        sdk, api_key, token = active_agent_sdk
        result = await sdk.heartbeat(api_key, heartbeat_token=token)

        assert result["lifecycle"] == "active"


# ═══════════════════════════════════════════════════════════════════════════
# §7.3 — Stale Detection (Rule Engine)
# ═══════════════════════════════════════════════════════════════════════════

class TestStaleDetection:
    """Spec §7.3 — Agent marked stale after 2h without heartbeat.

    These tests verify the Cypher-level rule logic by directly manipulating
    timestamps and running the rule query.
    """

    @pytest.mark.integration
    async def test_active_agent_goes_stale(self, heartbeat_sdk):
        """Agent with last_heartbeat >2h ago transitions to stale."""
        sdk, api_key = heartbeat_sdk

        # First make the agent active
        await sdk.heartbeat(api_key, heartbeat_token=None)

        # Backdate last_heartbeat to 3 hours ago
        async with sdk.driver.session() as session:
            await session.run("""
                MATCH (a:Agent {id: 'hb-test-agent'})
                SET a.last_heartbeat = datetime() - duration('PT3H')
            """)

        # Run the stale-detection rule query (simulates rule engine evaluation)
        async with sdk.driver.session() as session:
            await session.run("""
                MATCH (a:Agent)
                WHERE a.lifecycle = 'active'
                  AND a.lifecycle <> 'disabled'
                  AND a.last_heartbeat < datetime() - duration('PT2H')
                SET a.lifecycle = 'stale',
                    a.heartbeat_token = null
            """)

        node = await get_agent_node(sdk, "hb-test-agent")
        assert node["lifecycle"] == "stale"

    @pytest.mark.integration
    async def test_stale_clears_heartbeat_token(self, heartbeat_sdk):
        """Transition to stale clears the heartbeat_token (spec §5, B2.5)."""
        sdk, api_key = heartbeat_sdk
        result = await sdk.heartbeat(api_key, heartbeat_token=None)
        assert result["heartbeat_token"] is not None

        # Backdate and run rule
        async with sdk.driver.session() as session:
            await session.run("""
                MATCH (a:Agent {id: 'hb-test-agent'})
                SET a.last_heartbeat = datetime() - duration('PT3H')
            """)
            await session.run("""
                MATCH (a:Agent)
                WHERE a.lifecycle = 'active'
                  AND a.last_heartbeat < datetime() - duration('PT2H')
                SET a.lifecycle = 'stale',
                    a.heartbeat_token = null
            """)

        node = await get_agent_node(sdk, "hb-test-agent")
        assert node["heartbeat_token"] is None

    @pytest.mark.integration
    async def test_recently_heartbeated_agent_not_stale(self, heartbeat_sdk):
        """Agent with recent heartbeat should NOT be marked stale."""
        sdk, api_key = heartbeat_sdk
        await sdk.heartbeat(api_key, heartbeat_token=None)

        # Run stale rule — should not affect this agent
        async with sdk.driver.session() as session:
            await session.run("""
                MATCH (a:Agent)
                WHERE a.lifecycle = 'active'
                  AND a.last_heartbeat < datetime() - duration('PT2H')
                SET a.lifecycle = 'stale',
                    a.heartbeat_token = null
            """)

        node = await get_agent_node(sdk, "hb-test-agent")
        assert node["lifecycle"] == "active"


# ═══════════════════════════════════════════════════════════════════════════
# §7.4 — Recovery from Stale
# ═══════════════════════════════════════════════════════════════════════════

class TestRecoveryFromStale:
    """Spec §7.4 — Stale agent recovers to active via heartbeat."""

    @pytest.mark.integration
    async def test_stale_to_active(self, stale_agent_sdk):
        """Stale agent transitions back to active on heartbeat."""
        sdk, api_key = stale_agent_sdk
        # Token is null after going stale, so pass None (chain resets)
        result = await sdk.heartbeat(api_key, heartbeat_token=None)

        assert result["lifecycle"] == "active"

    @pytest.mark.integration
    async def test_new_token_issued_on_recovery(self, stale_agent_sdk):
        """New heartbeat_token is issued when recovering from stale."""
        sdk, api_key = stale_agent_sdk
        result = await sdk.heartbeat(api_key, heartbeat_token=None)

        assert result["heartbeat_token"] is not None
        assert isinstance(result["heartbeat_token"], str)

    @pytest.mark.integration
    async def test_heartbeat_count_incremented_on_recovery(self, stale_agent_sdk):
        """heartbeat_count increments even on recovery."""
        sdk, api_key = stale_agent_sdk
        result = await sdk.heartbeat(api_key, heartbeat_token=None)

        assert result["heartbeat_count"] == 21  # was 20


# ═══════════════════════════════════════════════════════════════════════════
# §7.5 — Inactive Detection
# ═══════════════════════════════════════════════════════════════════════════

class TestInactiveDetection:
    """Spec §7.5 — Stale agent marked inactive after 24h without heartbeat."""

    @pytest.mark.integration
    async def test_stale_to_inactive(self, stale_agent_sdk):
        """Stale agent with last_heartbeat >24h ago transitions to inactive."""
        sdk, _ = stale_agent_sdk

        # Backdate last_heartbeat to 25 hours ago
        async with sdk.driver.session() as session:
            await session.run("""
                MATCH (a:Agent {id: 'hb-stale-agent'})
                SET a.last_heartbeat = datetime() - duration('PT25H')
            """)

        # Run the inactive-detection rule query
        async with sdk.driver.session() as session:
            await session.run("""
                MATCH (a:Agent)
                WHERE a.lifecycle = 'stale'
                  AND a.last_heartbeat < datetime() - duration('P1D')
                SET a.lifecycle = 'inactive',
                    a.heartbeat_token = null
            """)

        node = await get_agent_node(sdk, "hb-stale-agent")
        assert node["lifecycle"] == "inactive"

    @pytest.mark.integration
    async def test_recently_stale_agent_not_inactive(self, stale_agent_sdk):
        """Stale agent with last_heartbeat <24h ago should NOT go inactive."""
        sdk, _ = stale_agent_sdk
        # Fixture sets last_heartbeat to 3h ago — well within 24h

        async with sdk.driver.session() as session:
            await session.run("""
                MATCH (a:Agent)
                WHERE a.lifecycle = 'stale'
                  AND a.last_heartbeat < datetime() - duration('P1D')
                SET a.lifecycle = 'inactive',
                    a.heartbeat_token = null
            """)

        node = await get_agent_node(sdk, "hb-stale-agent")
        assert node["lifecycle"] == "stale"  # unchanged


# ═══════════════════════════════════════════════════════════════════════════
# §7.5+ — Recovery from Inactive
# ═══════════════════════════════════════════════════════════════════════════

class TestRecoveryFromInactive:
    """Spec §4 — inactive → active on heartbeat (token chain resets)."""

    @pytest.mark.integration
    async def test_inactive_to_active(self, inactive_agent_sdk):
        """Inactive agent transitions back to active on heartbeat."""
        sdk, api_key = inactive_agent_sdk
        result = await sdk.heartbeat(api_key, heartbeat_token=None)

        assert result["lifecycle"] == "active"

    @pytest.mark.integration
    async def test_new_token_after_inactive_recovery(self, inactive_agent_sdk):
        """New heartbeat_token issued on recovery from inactive."""
        sdk, api_key = inactive_agent_sdk
        result = await sdk.heartbeat(api_key, heartbeat_token=None)

        assert result["heartbeat_token"] is not None


# ═══════════════════════════════════════════════════════════════════════════
# §7.6 — Unknown Agent / Invalid Key
# ═══════════════════════════════════════════════════════════════════════════

class TestUnknownAgent:
    """Spec §7.6 — Authentication failures."""

    @pytest.mark.integration
    async def test_invalid_api_key_raises_auth_error(self, heartbeat_sdk):
        """Heartbeat with a bogus API key raises AuthenticationError."""
        sdk, _ = heartbeat_sdk
        with pytest.raises(AuthenticationError):
            await sdk.heartbeat("totally-invalid-key-xyz", heartbeat_token=None)

    @pytest.mark.integration
    async def test_empty_api_key_raises_auth_error(self, heartbeat_sdk):
        """Heartbeat with empty string API key raises AuthenticationError."""
        sdk, _ = heartbeat_sdk
        with pytest.raises(AuthenticationError):
            await sdk.heartbeat("", heartbeat_token=None)


# ═══════════════════════════════════════════════════════════════════════════
# §7.7 — Concurrent Heartbeats
# ═══════════════════════════════════════════════════════════════════════════

class TestConcurrentHeartbeats:
    """Spec §7.7 — Simultaneous heartbeats for multiple agents."""

    @pytest.mark.integration
    async def test_four_concurrent_heartbeats(self, multi_agent_sdk):
        """Send heartbeats for 4 agents simultaneously; all succeed."""
        sdk, agent_ids, raw_keys = multi_agent_sdk

        results = await asyncio.gather(
            sdk.heartbeat(raw_keys[0], heartbeat_token=None),
            sdk.heartbeat(raw_keys[1], heartbeat_token=None),
            sdk.heartbeat(raw_keys[2], heartbeat_token=None),
            sdk.heartbeat(raw_keys[3], heartbeat_token=None),
        )

        for i, result in enumerate(results):
            assert result["lifecycle"] == "active", (
                f"Agent {agent_ids[i]} not active after concurrent heartbeat"
            )
            assert result["heartbeat_count"] == 1
            assert result["heartbeat_token"] is not None

    @pytest.mark.integration
    async def test_no_data_corruption_across_agents(self, multi_agent_sdk):
        """Concurrent heartbeats don't corrupt other agents' data."""
        sdk, agent_ids, raw_keys = multi_agent_sdk

        await asyncio.gather(*(
            sdk.heartbeat(key, heartbeat_token=None) for key in raw_keys
        ))

        # Verify each agent has its own distinct token
        tokens = set()
        for agent_id in agent_ids:
            node = await get_agent_node(sdk, agent_id)
            assert node["lifecycle"] == "active"
            assert node["heartbeat_count"] == 1
            tokens.add(node["heartbeat_token"])

        # All tokens should be unique
        assert len(tokens) == 4, "Heartbeat tokens not unique across agents"


# ═══════════════════════════════════════════════════════════════════════════
# §7.8 — Authentication Required
# ═══════════════════════════════════════════════════════════════════════════

class TestAuthenticationRequired:
    """Spec §7.8 — Heartbeat requires valid API key authentication."""

    @pytest.mark.integration
    async def test_no_api_key_raises_auth_error(self, heartbeat_sdk):
        """Calling heartbeat with no API key raises AuthenticationError."""
        sdk, _ = heartbeat_sdk
        with pytest.raises(AuthenticationError):
            await sdk.heartbeat(None, heartbeat_token=None)

    @pytest.mark.integration
    async def test_invalid_key_does_not_modify_agent(self, heartbeat_sdk):
        """Failed authentication must not modify any Agent node."""
        sdk, api_key = heartbeat_sdk
        node_before = await get_agent_node(sdk, "hb-test-agent")

        with pytest.raises(AuthenticationError):
            await sdk.heartbeat("completely-wrong-key", heartbeat_token=None)

        node_after = await get_agent_node(sdk, "hb-test-agent")
        assert node_after["lifecycle"] == node_before["lifecycle"]
        assert node_after["heartbeat_count"] == node_before["heartbeat_count"]
        assert node_after["last_heartbeat"] == node_before["last_heartbeat"]


# ═══════════════════════════════════════════════════════════════════════════
# §7.9 — Heartbeat Token Chain (Replay Protection)
# ═══════════════════════════════════════════════════════════════════════════

class TestHeartbeatTokenChain:
    """Spec §7.9 — Chained token replay protection."""

    @pytest.mark.integration
    async def test_valid_chain(self, heartbeat_sdk):
        """First → second heartbeat using chained tokens succeeds."""
        sdk, api_key = heartbeat_sdk

        r1 = await sdk.heartbeat(api_key, heartbeat_token=None)
        token_1 = r1["heartbeat_token"]

        # Need to wait or backdate to avoid rate-limiting
        async with sdk.driver.session() as session:
            await session.run("""
                MATCH (a:Agent {id: 'hb-test-agent'})
                SET a.last_heartbeat = datetime() - duration('PT120S')
            """)

        r2 = await sdk.heartbeat(api_key, heartbeat_token=token_1)
        token_2 = r2["heartbeat_token"]

        assert token_2 != token_1
        assert r2["heartbeat_count"] == 2

    @pytest.mark.integration
    async def test_replay_old_token_rejected(self, heartbeat_sdk):
        """Replaying an old token after it's been rotated raises error."""
        sdk, api_key = heartbeat_sdk

        r1 = await sdk.heartbeat(api_key, heartbeat_token=None)
        token_1 = r1["heartbeat_token"]

        # Backdate to avoid rate limiting
        async with sdk.driver.session() as session:
            await session.run("""
                MATCH (a:Agent {id: 'hb-test-agent'})
                SET a.last_heartbeat = datetime() - duration('PT120S')
            """)

        # Second heartbeat rotates the token
        r2 = await sdk.heartbeat(api_key, heartbeat_token=token_1)
        assert r2["heartbeat_token"] != token_1

        # Backdate again
        async with sdk.driver.session() as session:
            await session.run("""
                MATCH (a:Agent {id: 'hb-test-agent'})
                SET a.last_heartbeat = datetime() - duration('PT120S')
            """)

        # Replay token_1 — should fail
        with pytest.raises(HeartbeatTokenMismatchError):
            await sdk.heartbeat(api_key, heartbeat_token=token_1)

    @pytest.mark.integration
    async def test_replay_does_not_modify_state(self, heartbeat_sdk):
        """Failed replay attempt must not modify Agent node state."""
        sdk, api_key = heartbeat_sdk

        r1 = await sdk.heartbeat(api_key, heartbeat_token=None)
        token_1 = r1["heartbeat_token"]

        async with sdk.driver.session() as session:
            await session.run("""
                MATCH (a:Agent {id: 'hb-test-agent'})
                SET a.last_heartbeat = datetime() - duration('PT120S')
            """)

        r2 = await sdk.heartbeat(api_key, heartbeat_token=token_1)
        token_2 = r2["heartbeat_token"]
        node_before_replay = await get_agent_node(sdk, "hb-test-agent")

        async with sdk.driver.session() as session:
            await session.run("""
                MATCH (a:Agent {id: 'hb-test-agent'})
                SET a.last_heartbeat = datetime() - duration('PT120S')
            """)

        with pytest.raises(HeartbeatTokenMismatchError):
            await sdk.heartbeat(api_key, heartbeat_token=token_1)

        node_after_replay = await get_agent_node(sdk, "hb-test-agent")
        assert node_after_replay["heartbeat_count"] == node_before_replay["heartbeat_count"]
        assert node_after_replay["heartbeat_token"] == token_2

    @pytest.mark.integration
    async def test_wrong_token_on_first_heartbeat(self, heartbeat_sdk):
        """Passing a non-None token on a pending agent with no stored token.

        Spec says first heartbeat token should be None. Passing a random token
        when the stored token is null should raise HeartbeatTokenMismatchError.
        """
        sdk, api_key = heartbeat_sdk
        with pytest.raises(HeartbeatTokenMismatchError):
            await sdk.heartbeat(api_key, heartbeat_token="fabricated-token")


# ═══════════════════════════════════════════════════════════════════════════
# §7.10 — Disabled Agent Rejection
# ═══════════════════════════════════════════════════════════════════════════

class TestDisabledAgentRejection:
    """Spec §7.10 — Heartbeats from disabled agents are rejected."""

    @pytest.mark.integration
    async def test_disabled_agent_raises_error(self, disabled_agent_sdk):
        """Heartbeat to disabled agent raises AgentDisabledError."""
        sdk, api_key = disabled_agent_sdk
        with pytest.raises(AgentDisabledError):
            await sdk.heartbeat(api_key, heartbeat_token=None)

    @pytest.mark.integration
    async def test_disabled_agent_lifecycle_unchanged(self, disabled_agent_sdk):
        """Disabled agent remains disabled after rejected heartbeat."""
        sdk, api_key = disabled_agent_sdk

        with pytest.raises(AgentDisabledError):
            await sdk.heartbeat(api_key, heartbeat_token=None)

        node = await get_agent_node(sdk, "hb-disabled-agent")
        assert node["lifecycle"] == "disabled"

    @pytest.mark.integration
    async def test_disabled_agent_last_heartbeat_unchanged(self, disabled_agent_sdk):
        """Disabled agent's last_heartbeat is NOT updated on rejected heartbeat."""
        sdk, api_key = disabled_agent_sdk
        node_before = await get_agent_node(sdk, "hb-disabled-agent")

        with pytest.raises(AgentDisabledError):
            await sdk.heartbeat(api_key, heartbeat_token=None)

        node_after = await get_agent_node(sdk, "hb-disabled-agent")
        assert node_after["last_heartbeat"] == node_before["last_heartbeat"]
        assert node_after["heartbeat_count"] == node_before["heartbeat_count"]


# ═══════════════════════════════════════════════════════════════════════════
# §7.11 — Rate Limiting
# ═══════════════════════════════════════════════════════════════════════════

class TestRateLimiting:
    """Spec §7.11 — 60s minimum interval between effective heartbeats."""

    @pytest.mark.integration
    async def test_rapid_heartbeat_silently_accepted(self, heartbeat_sdk):
        """Second heartbeat within 60s returns success but doesn't write."""
        sdk, api_key = heartbeat_sdk

        r1 = await sdk.heartbeat(api_key, heartbeat_token=None)
        t1 = r1["last_heartbeat"]
        count_1 = r1["heartbeat_count"]

        # Immediately send another — should be silently deduplicated
        r2 = await sdk.heartbeat(api_key, heartbeat_token=r1["heartbeat_token"])

        # No error, but data unchanged
        assert r2["last_heartbeat"] == t1
        assert r2["heartbeat_count"] == count_1

    @pytest.mark.integration
    async def test_rate_limited_heartbeat_no_error(self, heartbeat_sdk):
        """Rate-limited heartbeats return success, not an error."""
        sdk, api_key = heartbeat_sdk

        r1 = await sdk.heartbeat(api_key, heartbeat_token=None)
        # This should not raise — silent dedup
        r2 = await sdk.heartbeat(api_key, heartbeat_token=r1["heartbeat_token"])
        assert "agent_id" in r2

    @pytest.mark.integration
    async def test_heartbeat_accepted_after_interval(self, heartbeat_sdk):
        """Heartbeat succeeds (with write) once the 60s interval has passed."""
        sdk, api_key = heartbeat_sdk

        r1 = await sdk.heartbeat(api_key, heartbeat_token=None)
        count_1 = r1["heartbeat_count"]

        # Simulate passage of time by backdating last_heartbeat
        async with sdk.driver.session() as session:
            await session.run("""
                MATCH (a:Agent {id: 'hb-test-agent'})
                SET a.last_heartbeat = datetime() - duration('PT120S')
            """)

        r2 = await sdk.heartbeat(api_key, heartbeat_token=r1["heartbeat_token"])
        assert r2["heartbeat_count"] == count_1 + 1

    @pytest.mark.integration
    async def test_rate_limited_token_not_rotated(self, heartbeat_sdk):
        """Token should NOT rotate on a rate-limited (no-write) heartbeat."""
        sdk, api_key = heartbeat_sdk

        r1 = await sdk.heartbeat(api_key, heartbeat_token=None)
        token_1 = r1["heartbeat_token"]

        # Immediate retry — should be rate limited
        r2 = await sdk.heartbeat(api_key, heartbeat_token=token_1)

        # Token unchanged because no write occurred
        assert r2["heartbeat_token"] == token_1

    @pytest.mark.integration
    async def test_wrong_token_rejected_even_when_rate_limited(self, heartbeat_sdk):
        """Token validation must happen BEFORE rate limiting (F8).

        Sending a wrong token during the rate-limit window must raise
        HeartbeatTokenMismatchError, not silently succeed. Otherwise the
        rate-limiting window becomes a replay window.
        """
        sdk, api_key = heartbeat_sdk

        r1 = await sdk.heartbeat(api_key, heartbeat_token=None)

        # Immediate retry with WRONG token — within rate-limit window
        with pytest.raises(HeartbeatTokenMismatchError):
            await sdk.heartbeat(api_key, heartbeat_token="fabricated-wrong-token")


# ═══════════════════════════════════════════════════════════════════════════
# §7.12 — Cron-Validated Heartbeat (Source Tracking)
# ═══════════════════════════════════════════════════════════════════════════

class TestHeartbeatSource:
    """Spec §7.12 — heartbeat_source field tracks origin."""

    @pytest.mark.integration
    async def test_default_source_is_agent(self, heartbeat_sdk):
        """Heartbeat without explicit source defaults to 'agent'."""
        sdk, api_key = heartbeat_sdk
        await sdk.heartbeat(api_key, heartbeat_token=None)

        node = await get_agent_node(sdk, "hb-test-agent")
        assert node["heartbeat_source"] == "agent"

    @pytest.mark.integration
    async def test_cron_source_recorded(self, heartbeat_sdk):
        """Heartbeat with source='cron' records 'cron' on the Agent node."""
        sdk, api_key = heartbeat_sdk
        await sdk.heartbeat(api_key, heartbeat_token=None, source="cron")

        node = await get_agent_node(sdk, "hb-test-agent")
        assert node["heartbeat_source"] == "cron"

    @pytest.mark.integration
    async def test_invalid_source_rejected(self, heartbeat_sdk):
        """Invalid heartbeat_source values must raise ValueError (F5)."""
        sdk, api_key = heartbeat_sdk
        with pytest.raises(ValueError):
            await sdk.heartbeat(api_key, heartbeat_token=None, source="admin")

    @pytest.mark.integration
    async def test_empty_source_rejected(self, heartbeat_sdk):
        """Empty string source must raise ValueError (F5)."""
        sdk, api_key = heartbeat_sdk
        with pytest.raises(ValueError):
            await sdk.heartbeat(api_key, heartbeat_token=None, source="")

    @pytest.mark.integration
    async def test_source_overwritten_on_subsequent_heartbeat(self, heartbeat_sdk):
        """Source field is overwritten by subsequent heartbeats."""
        sdk, api_key = heartbeat_sdk
        r1 = await sdk.heartbeat(api_key, heartbeat_token=None, source="cron")

        node = await get_agent_node(sdk, "hb-test-agent")
        assert node["heartbeat_source"] == "cron"

        # Backdate to allow next heartbeat through
        async with sdk.driver.session() as session:
            await session.run("""
                MATCH (a:Agent {id: 'hb-test-agent'})
                SET a.last_heartbeat = datetime() - duration('PT120S')
            """)

        await sdk.heartbeat(api_key, heartbeat_token=r1["heartbeat_token"],
                            source="agent")

        node = await get_agent_node(sdk, "hb-test-agent")
        assert node["heartbeat_source"] == "agent"


# ═══════════════════════════════════════════════════════════════════════════
# Edge Cases
# ═══════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Additional edge cases beyond the numbered spec scenarios."""

    @pytest.mark.integration
    async def test_heartbeat_chain_survives_stale_recovery(self, heartbeat_sdk):
        """Full lifecycle: active → stale → recovered → active with new chain."""
        sdk, api_key = heartbeat_sdk

        # 1. First heartbeat → active
        r1 = await sdk.heartbeat(api_key, heartbeat_token=None)
        assert r1["lifecycle"] == "active"

        # 2. Simulate going stale (backdate + run rule)
        async with sdk.driver.session() as session:
            await session.run("""
                MATCH (a:Agent {id: 'hb-test-agent'})
                SET a.last_heartbeat = datetime() - duration('PT3H')
            """)
            await session.run("""
                MATCH (a:Agent)
                WHERE a.lifecycle = 'active'
                  AND a.last_heartbeat < datetime() - duration('PT2H')
                SET a.lifecycle = 'stale',
                    a.heartbeat_token = null
            """)

        node = await get_agent_node(sdk, "hb-test-agent")
        assert node["lifecycle"] == "stale"
        assert node["heartbeat_token"] is None

        # 3. Recover — pass None token since chain was reset
        r2 = await sdk.heartbeat(api_key, heartbeat_token=None)
        assert r2["lifecycle"] == "active"
        assert r2["heartbeat_token"] is not None

        # 4. Continue chain with new token
        async with sdk.driver.session() as session:
            await session.run("""
                MATCH (a:Agent {id: 'hb-test-agent'})
                SET a.last_heartbeat = datetime() - duration('PT120S')
            """)

        r3 = await sdk.heartbeat(api_key, heartbeat_token=r2["heartbeat_token"])
        assert r3["lifecycle"] == "active"
        assert r3["heartbeat_token"] != r2["heartbeat_token"]

    @pytest.mark.integration
    async def test_old_token_invalid_after_stale_recovery(self, heartbeat_sdk):
        """Token from before stale transition is invalid after recovery."""
        sdk, api_key = heartbeat_sdk

        r1 = await sdk.heartbeat(api_key, heartbeat_token=None)
        old_token = r1["heartbeat_token"]

        # Go stale
        async with sdk.driver.session() as session:
            await session.run("""
                MATCH (a:Agent {id: 'hb-test-agent'})
                SET a.last_heartbeat = datetime() - duration('PT3H')
            """)
            await session.run("""
                MATCH (a:Agent)
                WHERE a.lifecycle = 'active'
                  AND a.last_heartbeat < datetime() - duration('PT2H')
                SET a.lifecycle = 'stale',
                    a.heartbeat_token = null
            """)

        # Recover with None
        r2 = await sdk.heartbeat(api_key, heartbeat_token=None)

        # Backdate for next attempt
        async with sdk.driver.session() as session:
            await session.run("""
                MATCH (a:Agent {id: 'hb-test-agent'})
                SET a.last_heartbeat = datetime() - duration('PT120S')
            """)

        # Try to use the old (pre-stale) token — must fail
        with pytest.raises(HeartbeatTokenMismatchError):
            await sdk.heartbeat(api_key, heartbeat_token=old_token)

    @pytest.mark.integration
    async def test_disabled_cannot_transition_via_any_state(self, disabled_agent_sdk):
        """Disabled is truly terminal — can't sneak past via token=None."""
        sdk, api_key = disabled_agent_sdk

        with pytest.raises(AgentDisabledError):
            await sdk.heartbeat(api_key, heartbeat_token=None)

        with pytest.raises(AgentDisabledError):
            await sdk.heartbeat(api_key, heartbeat_token="some-token")

        node = await get_agent_node(sdk, "hb-disabled-agent")
        assert node["lifecycle"] == "disabled"
