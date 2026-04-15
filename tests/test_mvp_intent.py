"""MVP Intent Pipeline Tests — Phase A4 (TDD).

Tests define expected behavior per spec-mvp-test.md v1.1 (post security review).
Tests are written BEFORE implementation — they should fail clearly on missing
modules/classes/methods, guiding the implementation.

Covers:
- SDK: submit_intent(), get_intent_status(), get_intent_result()
- Agent authentication: pre-shared API keys, bcrypt hash verification
- Capability enforcement: exec-ls path validation (allowlist, traversal, canonicalization)
- Neo4j state transitions: pending → claimed → running → success/failed/rejected
- Daemon processing loop, reaper/orphan recovery
- Edge cases: concurrency, TOCTOU, impersonation, output truncation

Reference: docs/spec-mvp-test.md v1.1
Neo4j dev: bolt://localhost:7690, user neo4j, password hassaleh
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import tempfile
import time
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Imports from modules that DO NOT YET EXIST.
# TDD: these imports define the target module structure.  Tests will fail
# with ImportError until the modules are created.
# ---------------------------------------------------------------------------
from hassaleh.errors import (
    AccessDeniedError,
    AuthenticationError,
    CapabilityDeniedError,
    CapabilityNotFoundError,
    CapabilityParamError,
)
from hassaleh.intent_sdk import IntentSDK          # New SDK class per spec §4
from hassaleh.capabilities.exec_ls import (
    EXEC_LS_ALLOWED_BASES,
    validate_exec_ls_path,
)
from hassaleh.intent_daemon import IntentDaemon     # Daemon processing per spec §5
from hassaleh.auth import (
    hash_api_key,
    verify_api_key,
    generate_api_key,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NEO4J_URI = os.environ.get("NEO4J_TEST_URI", "bolt://localhost:7690")
NEO4J_USER = os.environ.get("NEO4J_TEST_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_TEST_PASSWORD", "hassaleh")

OUTPUT_TRUNCATION_LIMIT = 64 * 1024  # 64 KB per spec §3A
MAX_DIR_ENTRIES = 10_000             # per spec §3A
INTENT_TIMEOUT_SEC = 30              # per spec §5.3
REAPER_STALE_SEC = 60                # 2× timeout, per spec §5.5


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def api_key_pair():
    """Generate a fresh API key + its bcrypt hash for test agents."""
    raw_key = generate_api_key()
    hashed = hash_api_key(raw_key)
    return raw_key, hashed


@pytest.fixture
def second_api_key_pair():
    """Second agent's API key pair — for cross-agent access tests."""
    raw_key = generate_api_key()
    hashed = hash_api_key(raw_key)
    return raw_key, hashed


@pytest.fixture
def allowed_tmp_dir(tmp_path):
    """Create a temporary directory that simulates an allowed base path.

    Populates it with a few test files so exec-ls has something to list.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "file1.txt").write_text("hello")
    (data_dir / "file2.py").write_text("# code")
    (data_dir / "subdir").mkdir()
    return data_dir


@pytest.fixture
def outside_tmp_dir(tmp_path):
    """Directory that is NOT in the allowlist — should be rejected."""
    secret_dir = tmp_path / "secret"
    secret_dir.mkdir()
    (secret_dir / "passwords.txt").write_text("hunter2")
    return secret_dir


@pytest.fixture
def symlink_escape(tmp_path, allowed_tmp_dir, outside_tmp_dir):
    """Symlink inside allowed dir that points outside — must be caught."""
    link = allowed_tmp_dir / "escape_link"
    link.symlink_to(outside_tmp_dir)
    return link


# ── Integration fixtures (require running Neo4j) ─────────────────────────

pytestmark_integration = pytest.mark.integration


@pytest_asyncio.fixture
async def intent_sdk(api_key_pair):
    """IntentSDK connected to test Neo4j with a registered test agent."""
    raw_key, hashed = api_key_pair
    sdk = IntentSDK(
        neo4j_uri=NEO4J_URI,
        neo4j_user=NEO4J_USER,
        neo4j_password=NEO4J_PASSWORD,
    )
    await sdk.connect()

    # Seed: agent "test-dione" with exec-ls capability and API key hash
    async with sdk.driver.session() as session:
        await session.run("""
            MERGE (a:Agent {id: 'test-dione'})
            SET a.name = 'Test Dione',
                a.api_key_hash = $hash
        """, hash=hashed)
        await session.run("""
            MERGE (cap:Capability {id: 'exec-ls'})
            SET cap.name = 'List Directory',
                cap.kind = 'cli'
        """)
        await session.run("""
            MATCH (a:Agent {id: 'test-dione'}), (cap:Capability {id: 'exec-ls'})
            MERGE (a)-[:HAS_CAPABILITY]->(cap)
        """)

    yield sdk, raw_key

    # Cleanup
    async with sdk.driver.session() as session:
        await session.run("""
            MATCH (i:Intent)-[:SUBMITTED_BY]->(:Agent {id: 'test-dione'})
            DETACH DELETE i
        """)
        await session.run("""
            MATCH (a:Agent {id: 'test-dione'}) DETACH DELETE a
        """)
    await sdk.close()


@pytest_asyncio.fixture
async def two_agent_sdk(api_key_pair, second_api_key_pair):
    """Two agents registered — for cross-agent access tests (§8.3)."""
    key_a, hash_a = api_key_pair
    key_b, hash_b = second_api_key_pair

    sdk = IntentSDK(
        neo4j_uri=NEO4J_URI,
        neo4j_user=NEO4J_USER,
        neo4j_password=NEO4J_PASSWORD,
    )
    await sdk.connect()

    async with sdk.driver.session() as session:
        await session.run("""
            MERGE (a:Agent {id: 'agent-alpha'})
            SET a.api_key_hash = $hash
        """, hash=hash_a)
        await session.run("""
            MERGE (b:Agent {id: 'agent-beta'})
            SET b.api_key_hash = $hash
        """, hash=hash_b)
        await session.run("""
            MERGE (cap:Capability {id: 'exec-ls'})
            SET cap.name = 'List Directory', cap.kind = 'cli'
        """)
        await session.run("""
            MATCH (a:Agent {id: 'agent-alpha'}), (cap:Capability {id: 'exec-ls'})
            MERGE (a)-[:HAS_CAPABILITY]->(cap)
        """)
        # agent-beta intentionally has NO exec-ls capability

    yield sdk, key_a, key_b

    async with sdk.driver.session() as session:
        await session.run("""
            MATCH (i:Intent)-[:SUBMITTED_BY]->(a:Agent)
            WHERE a.id IN ['agent-alpha', 'agent-beta']
            DETACH DELETE i
        """)
        await session.run("""
            MATCH (a:Agent) WHERE a.id IN ['agent-alpha', 'agent-beta']
            DETACH DELETE a
        """)
    await sdk.close()


# ═══════════════════════════════════════════════════════════════════════════
# 1. SDK API — submit_intent (§4.1)
# ═══════════════════════════════════════════════════════════════════════════

class TestSubmitIntent:
    """Spec §4.1 — submit_intent(api_key, capability_id, params) -> str."""

    @pytest.mark.integration
    async def test_happy_path_returns_uuid(self, intent_sdk):
        """§6.1 — Submit valid intent, get UUID back."""
        sdk, api_key = intent_sdk
        intent_id = await sdk.submit_intent(
            api_key=api_key,
            capability_id="exec-ls",
            params={"path": "/app"},
        )
        # Returns a valid UUID string
        uuid.UUID(intent_id)  # raises if not valid

    @pytest.mark.integration
    async def test_creates_pending_intent_in_neo4j(self, intent_sdk):
        """§6.1 step 2 — Intent appears in graph with status=pending."""
        sdk, api_key = intent_sdk
        intent_id = await sdk.submit_intent(
            api_key=api_key,
            capability_id="exec-ls",
            params={"path": "/app"},
        )

        async with sdk.driver.session() as session:
            result = await session.run(
                "MATCH (i:Intent {id: $id}) RETURN i",
                id=intent_id,
            )
            record = await result.single()

        assert record is not None
        node = record["i"]
        assert node["status"] == "pending"
        assert node["capability_id"] == "exec-ls"
        assert json.loads(node["params"]) == {"path": "/app"}
        assert node["result"] is None
        assert node["error"] is None

    @pytest.mark.integration
    async def test_creates_submitted_by_edge(self, intent_sdk):
        """§4.1 — SUBMITTED_BY relationship links intent to authenticated agent."""
        sdk, api_key = intent_sdk
        intent_id = await sdk.submit_intent(
            api_key=api_key,
            capability_id="exec-ls",
            params={"path": "/app"},
        )

        async with sdk.driver.session() as session:
            result = await session.run("""
                MATCH (i:Intent {id: $id})-[:SUBMITTED_BY]->(a:Agent)
                RETURN a.id AS agent_id
            """, id=intent_id)
            record = await result.single()

        assert record is not None
        assert record["agent_id"] == "test-dione"

    @pytest.mark.integration
    async def test_creates_requires_edge(self, intent_sdk):
        """§4.1 — REQUIRES relationship links intent to capability."""
        sdk, api_key = intent_sdk
        intent_id = await sdk.submit_intent(
            api_key=api_key,
            capability_id="exec-ls",
            params={"path": "/app"},
        )

        async with sdk.driver.session() as session:
            result = await session.run("""
                MATCH (i:Intent {id: $id})-[:REQUIRES]->(c:Capability)
                RETURN c.id AS cap_id
            """, id=intent_id)
            record = await result.single()

        assert record is not None
        assert record["cap_id"] == "exec-ls"

    @pytest.mark.integration
    async def test_timestamps_are_set(self, intent_sdk):
        """§6.1 step 6 — created_at and updated_at are set on creation."""
        sdk, api_key = intent_sdk
        intent_id = await sdk.submit_intent(
            api_key=api_key,
            capability_id="exec-ls",
            params={"path": "/app"},
        )

        async with sdk.driver.session() as session:
            result = await session.run(
                "MATCH (i:Intent {id: $id}) RETURN i.created_at AS c, i.updated_at AS u",
                id=intent_id,
            )
            record = await result.single()

        assert record["c"] is not None
        assert record["u"] is not None

    @pytest.mark.integration
    async def test_unknown_capability_raises(self, intent_sdk):
        """§6.4 — Non-existent capability_id raises CapabilityNotFoundError."""
        sdk, api_key = intent_sdk
        with pytest.raises(CapabilityNotFoundError):
            await sdk.submit_intent(
                api_key=api_key,
                capability_id="nonexistent-cap",
                params={},
            )

    @pytest.mark.integration
    async def test_agent_lacks_capability_raises(self, two_agent_sdk):
        """§6.2 — Agent without capability gets CapabilityDeniedError."""
        sdk, _key_a, key_b = two_agent_sdk
        # agent-beta does NOT have exec-ls
        with pytest.raises(CapabilityDeniedError):
            await sdk.submit_intent(
                api_key=key_b,
                capability_id="exec-ls",
                params={"path": "/app"},
            )

    @pytest.mark.integration
    async def test_params_stored_as_json_string(self, intent_sdk):
        """§4.1 — params field is a JSON-serialized string in Neo4j."""
        sdk, api_key = intent_sdk
        params = {"path": "/app", "extra": "ignored"}
        intent_id = await sdk.submit_intent(
            api_key=api_key,
            capability_id="exec-ls",
            params=params,
        )

        async with sdk.driver.session() as session:
            result = await session.run(
                "MATCH (i:Intent {id: $id}) RETURN i.params AS p",
                id=intent_id,
            )
            record = await result.single()

        stored = json.loads(record["p"])
        assert stored == params


# ═══════════════════════════════════════════════════════════════════════════
# 2. SDK API — get_intent_status / get_intent_result (§4.2, §4.3)
# ═══════════════════════════════════════════════════════════════════════════

class TestGetIntentStatus:
    """Spec §4.2 — get_intent_status(api_key, intent_id) -> dict."""

    @pytest.mark.integration
    async def test_returns_pending_status(self, intent_sdk):
        """Newly submitted intent has status=pending."""
        sdk, api_key = intent_sdk
        intent_id = await sdk.submit_intent(
            api_key=api_key,
            capability_id="exec-ls",
            params={"path": "/app"},
        )

        status = await sdk.get_intent_status(api_key, intent_id)

        assert status["id"] == intent_id
        assert status["status"] == "pending"
        assert status["created_at"] is not None
        assert status["updated_at"] is not None
        assert status["claimed_at"] is None
        assert status["completed_at"] is None

    @pytest.mark.integration
    async def test_nonexistent_intent_raises(self, intent_sdk):
        """Unknown intent_id raises ValueError."""
        sdk, api_key = intent_sdk
        with pytest.raises(ValueError):
            await sdk.get_intent_status(api_key, "nonexistent-uuid")


class TestGetIntentResult:
    """Spec §4.3 — get_intent_result(api_key, intent_id) -> dict."""

    @pytest.mark.integration
    async def test_still_processing_returns_none_result(self, intent_sdk):
        """§4.3 — running intent has result=None, error=None."""
        sdk, api_key = intent_sdk
        intent_id = await sdk.submit_intent(
            api_key=api_key,
            capability_id="exec-ls",
            params={"path": "/app"},
        )

        result = await sdk.get_intent_result(api_key, intent_id)

        assert result["status"] == "pending"
        assert result["result"] is None
        assert result["error"] is None
        assert result["duration_ms"] is None


# ═══════════════════════════════════════════════════════════════════════════
# 3. Agent Authentication (§3B)
# ═══════════════════════════════════════════════════════════════════════════

class TestAuthentication:
    """Spec §3B — pre-shared API keys with bcrypt hash verification."""

    def test_generate_api_key_is_url_safe(self):
        """Keys are generated via secrets.token_urlsafe(32)."""
        key = generate_api_key()
        assert isinstance(key, str)
        assert len(key) >= 32  # token_urlsafe(32) produces ~43 chars

    def test_hash_and_verify_round_trip(self):
        """Hashing + verification works as a round-trip."""
        key = generate_api_key()
        hashed = hash_api_key(key)
        assert verify_api_key(key, hashed) is True

    def test_verify_rejects_wrong_key(self):
        """Wrong key does not verify."""
        key = generate_api_key()
        hashed = hash_api_key(key)
        assert verify_api_key("wrong-key", hashed) is False

    def test_hash_is_bcrypt_format(self):
        """Hash should be bcrypt ($2b$ prefix)."""
        key = generate_api_key()
        hashed = hash_api_key(key)
        assert hashed.startswith("$2b$")

    def test_different_keys_produce_different_hashes(self):
        """Two different keys produce different hashes."""
        k1, k2 = generate_api_key(), generate_api_key()
        assert k1 != k2
        assert hash_api_key(k1) != hash_api_key(k2)

    @pytest.mark.integration
    async def test_invalid_api_key_raises(self, intent_sdk):
        """§8.2 — Invalid API key raises AuthenticationError."""
        sdk, _valid_key = intent_sdk
        with pytest.raises(AuthenticationError, match="Invalid API key"):
            await sdk.submit_intent(
                api_key="completely-bogus-key",
                capability_id="exec-ls",
                params={"path": "/app"},
            )

    @pytest.mark.integration
    async def test_invalid_key_creates_no_intent(self, intent_sdk):
        """§8.2 — Failed auth must not leave any Intent node in Neo4j."""
        sdk, _valid_key = intent_sdk

        try:
            await sdk.submit_intent(
                api_key="bogus-key",
                capability_id="exec-ls",
                params={"path": "/app"},
            )
        except AuthenticationError:
            pass

        async with sdk.driver.session() as session:
            result = await session.run("""
                MATCH (i:Intent)-[:SUBMITTED_BY]->(:Agent {id: 'test-dione'})
                WHERE i.status = 'pending'
                RETURN count(i) AS cnt
            """)
            record = await result.single()

        # No intents should have been created by the failed auth attempt
        # (only intents from OTHER tests in this session would exist)
        assert record["cnt"] == 0

    @pytest.mark.integration
    async def test_agent_id_derived_server_side(self, intent_sdk):
        """§3B — agent_id is NOT a parameter; it's derived from api_key."""
        sdk, api_key = intent_sdk
        intent_id = await sdk.submit_intent(
            api_key=api_key,
            capability_id="exec-ls",
            params={"path": "/app"},
        )

        async with sdk.driver.session() as session:
            result = await session.run("""
                MATCH (i:Intent {id: $id})-[:SUBMITTED_BY]->(a:Agent)
                RETURN a.id AS agent_id
            """, id=intent_id)
            record = await result.single()

        # The agent_id comes from the DB lookup, not from any caller input
        assert record["agent_id"] == "test-dione"


# ═══════════════════════════════════════════════════════════════════════════
# 4. Capability Enforcement — exec-ls Path Validation (§3A)
# ═══════════════════════════════════════════════════════════════════════════

class TestPathValidation:
    """Spec §3A — validate_exec_ls_path() unit tests.

    These test the validator in isolation (no Neo4j needed).
    The validator is expected at hassaleh.capabilities.exec_ls.validate_exec_ls_path.
    """

    # ── Step 1: Character validation ──────────────────────────────────────

    def test_rejects_null_bytes(self):
        """§3A step 1 — Null bytes are rejected before any filesystem call."""
        with pytest.raises(CapabilityParamError, match="invalid character"):
            validate_exec_ls_path("/app/\x00etc")

    def test_rejects_non_printable(self):
        """§3A step 1 — Non-printable characters are rejected."""
        with pytest.raises(CapabilityParamError, match="invalid character"):
            validate_exec_ls_path("/app/\x01hidden")

    def test_rejects_control_sequences(self):
        """§3A step 1 — Control sequences (escape chars) are rejected."""
        with pytest.raises(CapabilityParamError, match="invalid character"):
            validate_exec_ls_path("/app/\x1b[31mred")

    def test_rejects_spaces(self):
        """§3A step 1 — Spaces fail the regex ^[a-zA-Z0-9/_.-]+$."""
        with pytest.raises(CapabilityParamError, match="invalid character"):
            validate_exec_ls_path("/app/my dir")

    def test_rejects_semicolons(self):
        """§3A step 1 — Shell metacharacters rejected by regex."""
        with pytest.raises(CapabilityParamError, match="invalid character"):
            validate_exec_ls_path("/app;ls")

    def test_rejects_pipes(self):
        """§3A step 1 — Pipe characters rejected by regex."""
        with pytest.raises(CapabilityParamError, match="invalid character"):
            validate_exec_ls_path("/app|cat")

    def test_rejects_backticks(self):
        """§3A step 1 — Backtick injection rejected."""
        with pytest.raises(CapabilityParamError, match="invalid character"):
            validate_exec_ls_path("/app/`whoami`")

    def test_accepts_valid_characters(self):
        """§3A step 1 — Valid path chars are accepted."""
        # Should not raise (but may fail later steps if path doesn't exist)
        # We only test the character validation here
        try:
            validate_exec_ls_path("/app/valid-dir_name.2026")
        except CapabilityParamError as e:
            # Only character errors should not happen
            assert "invalid character" not in str(e).lower()

    # ── Step 2: Raw traversal rejection ───────────────────────────────────

    def test_rejects_dotdot_segment(self):
        """§3A step 2 — Raw '..' as path segment rejected before realpath."""
        with pytest.raises(CapabilityParamError, match="traversal"):
            validate_exec_ls_path("/app/../etc")

    def test_rejects_leading_dotdot(self):
        """§3A step 2 — Leading traversal rejected."""
        with pytest.raises(CapabilityParamError, match="traversal"):
            validate_exec_ls_path("/../../../etc/passwd")

    def test_rejects_trailing_dotdot(self):
        """§3A step 2 — Trailing '..' rejected."""
        with pytest.raises(CapabilityParamError, match="traversal"):
            validate_exec_ls_path("/app/subdir/..")

    def test_dotdot_embedded_in_name_is_ok(self):
        """Names like 'foo..bar' are fine — '..' must be a path SEGMENT."""
        # "foo..bar" does not contain ".." as a path segment
        # This test may fail on step 3/4/5 (path doesn't exist), but
        # step 2 specifically should NOT reject it.
        try:
            validate_exec_ls_path("/app/foo..bar")
        except CapabilityParamError as e:
            assert "traversal" not in str(e).lower()

    # ── Step 3+4: Canonicalization + prefix check ─────────────────────────

    def test_rejects_path_outside_allowlist(self, outside_tmp_dir):
        """§3A step 4 — Path not under any allowed base is rejected."""
        with patch(
            "hassaleh.capabilities.exec_ls.EXEC_LS_ALLOWED_BASES",
            ["/app", "/data"],
        ):
            with pytest.raises(CapabilityParamError, match="not in allowed scope"):
                validate_exec_ls_path("/etc/passwd")

    def test_accepts_path_under_allowed_base(self, allowed_tmp_dir):
        """§3A step 4 — Path under allowed base passes prefix check."""
        with patch(
            "hassaleh.capabilities.exec_ls.EXEC_LS_ALLOWED_BASES",
            [str(allowed_tmp_dir.parent)],  # allow the parent
        ):
            # Should pass validation (dir exists and is under allowed base)
            resolved = validate_exec_ls_path(str(allowed_tmp_dir))
            assert resolved == str(allowed_tmp_dir.resolve())

    def test_allowed_base_itself_is_permitted(self, allowed_tmp_dir):
        """§3A step 4 — resolved == base is allowed (not just children)."""
        base = str(allowed_tmp_dir.resolve())
        with patch(
            "hassaleh.capabilities.exec_ls.EXEC_LS_ALLOWED_BASES",
            [base],
        ):
            resolved = validate_exec_ls_path(base)
            assert resolved == base

    def test_symlink_escape_rejected(self, symlink_escape, allowed_tmp_dir):
        """§3A step 3+4 — Symlink resolving outside allowlist is caught."""
        with patch(
            "hassaleh.capabilities.exec_ls.EXEC_LS_ALLOWED_BASES",
            [str(allowed_tmp_dir.resolve())],
        ):
            with pytest.raises(CapabilityParamError, match="not in allowed scope"):
                validate_exec_ls_path(str(symlink_escape))

    # ── Step 5: Existence + directory check ───────────────────────────────

    def test_rejects_nonexistent_path(self, allowed_tmp_dir):
        """§3A step 5 — Path that doesn't exist is rejected."""
        with patch(
            "hassaleh.capabilities.exec_ls.EXEC_LS_ALLOWED_BASES",
            [str(allowed_tmp_dir.parent)],
        ):
            with pytest.raises(CapabilityParamError):
                validate_exec_ls_path(str(allowed_tmp_dir / "no_such_dir"))

    def test_rejects_file_not_directory(self, allowed_tmp_dir):
        """§3A step 5 — Files are not valid targets for exec-ls."""
        file_path = str(allowed_tmp_dir / "file1.txt")
        with patch(
            "hassaleh.capabilities.exec_ls.EXEC_LS_ALLOWED_BASES",
            [str(allowed_tmp_dir.parent)],
        ):
            with pytest.raises(CapabilityParamError, match="not a directory"):
                validate_exec_ls_path(file_path)

    # ── F1 attack vectors ─────────────────────────────────────────────────

    def test_f1_etc_passwd_traversal(self):
        """F1 — Classic /etc/passwd traversal blocked."""
        with pytest.raises(CapabilityParamError):
            validate_exec_ls_path("/../../../etc/passwd")

    def test_f1_etc_shadow_traversal(self):
        """F1 — /etc/shadow traversal blocked."""
        with pytest.raises(CapabilityParamError):
            validate_exec_ls_path("/etc/shadow")

    def test_f1_root_directory_blocked(self):
        """F1 — Listing / (resource exhaustion) blocked by allowlist."""
        with patch(
            "hassaleh.capabilities.exec_ls.EXEC_LS_ALLOWED_BASES",
            ["/app", "/data"],
        ):
            with pytest.raises(CapabilityParamError, match="not in allowed scope"):
                validate_exec_ls_path("/")

    def test_f1_proc_blocked(self):
        """F1 — /proc (unpredictable behavior) blocked by allowlist."""
        with patch(
            "hassaleh.capabilities.exec_ls.EXEC_LS_ALLOWED_BASES",
            ["/app", "/data"],
        ):
            with pytest.raises(CapabilityParamError, match="not in allowed scope"):
                validate_exec_ls_path("/proc")


# ═══════════════════════════════════════════════════════════════════════════
# 5. Neo4j State Transitions (§5.1)
# ═══════════════════════════════════════════════════════════════════════════

class TestStateTransitions:
    """Spec §5.1 — Intent lifecycle state machine."""

    VALID_TRANSITIONS = [
        ("pending", "claimed"),
        ("claimed", "rejected"),
        ("claimed", "running"),
        ("running", "success"),
        ("running", "failed"),
    ]

    INVALID_TRANSITIONS = [
        ("success", "pending"),   # no rollback
        ("failed", "running"),    # no automatic retry
        ("claimed", "pending"),   # no unclaiming
        ("success", "running"),   # terminal is terminal
        ("rejected", "pending"),  # terminal is terminal
    ]

    @pytest.mark.parametrize("from_state,to_state", VALID_TRANSITIONS)
    @pytest.mark.integration
    async def test_valid_transition_accepted(
        self, intent_sdk, from_state, to_state
    ):
        """Valid state transitions should succeed."""
        sdk, api_key = intent_sdk

        # Create an intent and force it to from_state
        intent_id = await sdk.submit_intent(
            api_key=api_key,
            capability_id="exec-ls",
            params={"path": "/app"},
        )
        async with sdk.driver.session() as session:
            await session.run(
                "MATCH (i:Intent {id: $id}) SET i.status = $state",
                id=intent_id, state=from_state,
            )

        # Attempt transition — should work via daemon's transition method
        daemon = IntentDaemon(
            neo4j_uri=NEO4J_URI,
            neo4j_user=NEO4J_USER,
            neo4j_password=NEO4J_PASSWORD,
        )
        result = await daemon.transition_intent(intent_id, to_state)
        assert result is True

    @pytest.mark.parametrize("from_state,to_state", INVALID_TRANSITIONS)
    @pytest.mark.integration
    async def test_invalid_transition_rejected(
        self, intent_sdk, from_state, to_state
    ):
        """Invalid state transitions must be rejected."""
        sdk, api_key = intent_sdk

        intent_id = await sdk.submit_intent(
            api_key=api_key,
            capability_id="exec-ls",
            params={"path": "/app"},
        )
        async with sdk.driver.session() as session:
            await session.run(
                "MATCH (i:Intent {id: $id}) SET i.status = $state",
                id=intent_id, state=from_state,
            )

        daemon = IntentDaemon(
            neo4j_uri=NEO4J_URI,
            neo4j_user=NEO4J_USER,
            neo4j_password=NEO4J_PASSWORD,
        )
        with pytest.raises(ValueError, match="Invalid transition"):
            await daemon.transition_intent(intent_id, to_state)

    @pytest.mark.integration
    async def test_terminal_states_are_immutable(self, intent_sdk):
        """Once in a terminal state, no further transitions are allowed."""
        sdk, api_key = intent_sdk
        terminal_states = ["success", "failed", "rejected"]

        for terminal in terminal_states:
            intent_id = await sdk.submit_intent(
                api_key=api_key,
                capability_id="exec-ls",
                params={"path": "/app"},
            )
            async with sdk.driver.session() as session:
                await session.run(
                    "MATCH (i:Intent {id: $id}) SET i.status = $state",
                    id=intent_id, state=terminal,
                )

            daemon = IntentDaemon(
                neo4j_uri=NEO4J_URI,
                neo4j_user=NEO4J_USER,
                neo4j_password=NEO4J_PASSWORD,
            )
            # No valid next state from a terminal state
            for target in ["pending", "claimed", "running", "success", "failed"]:
                if target == terminal:
                    continue
                with pytest.raises(ValueError):
                    await daemon.transition_intent(intent_id, target)


# ═══════════════════════════════════════════════════════════════════════════
# 6. Daemon Processing (§5.2 - §5.5)
# ═══════════════════════════════════════════════════════════════════════════

class TestDaemonProcessing:
    """Spec §5.2 — Daemon claim + capability check + execution."""

    @pytest.mark.integration
    async def test_atomic_claim_with_capability_check(self, intent_sdk):
        """§5.2 step 2 — Claim + capability verified in single Cypher txn."""
        sdk, api_key = intent_sdk
        intent_id = await sdk.submit_intent(
            api_key=api_key,
            capability_id="exec-ls",
            params={"path": "/app"},
        )

        daemon = IntentDaemon(
            neo4j_uri=NEO4J_URI,
            neo4j_user=NEO4J_USER,
            neo4j_password=NEO4J_PASSWORD,
        )
        await daemon.connect()

        claimed = await daemon.claim_intent(intent_id)
        assert claimed is True

        # Verify status is now "claimed"
        status = await sdk.get_intent_status(api_key, intent_id)
        assert status["status"] == "claimed"
        assert status["claimed_at"] is not None

        await daemon.close()

    @pytest.mark.integration
    async def test_claim_records_daemon_instance_id(self, intent_sdk):
        """§5.4 — claimed_by field records the daemon instance ID."""
        sdk, api_key = intent_sdk
        intent_id = await sdk.submit_intent(
            api_key=api_key,
            capability_id="exec-ls",
            params={"path": "/app"},
        )

        daemon = IntentDaemon(
            neo4j_uri=NEO4J_URI,
            neo4j_user=NEO4J_USER,
            neo4j_password=NEO4J_PASSWORD,
            instance_id="daemon-test-001",
        )
        await daemon.connect()
        await daemon.claim_intent(intent_id)

        async with sdk.driver.session() as session:
            result = await session.run(
                "MATCH (i:Intent {id: $id}) RETURN i.claimed_by AS cb",
                id=intent_id,
            )
            record = await result.single()

        assert record["cb"] == "daemon-test-001"
        await daemon.close()

    @pytest.mark.integration
    async def test_claim_fails_for_missing_capability(self, two_agent_sdk):
        """§5.2 — Claim attempt for agent without capability → rejected."""
        sdk, _key_a, key_b = two_agent_sdk

        # agent-beta submits (beta has no exec-ls, but let's create intent
        # via direct DB to test the daemon's claim path)
        intent_id = str(uuid.uuid4())
        async with sdk.driver.session() as session:
            await session.run("""
                MATCH (a:Agent {id: 'agent-beta'})
                CREATE (i:Intent {
                    id: $id, status: 'pending', capability_id: 'exec-ls',
                    params: '{"path": "/app"}',
                    created_at: datetime(), updated_at: datetime()
                })
                CREATE (i)-[:SUBMITTED_BY]->(a)
                CREATE (i)-[:REQUIRES]->(:Capability {id: 'exec-ls'})
            """, id=intent_id)

        daemon = IntentDaemon(
            neo4j_uri=NEO4J_URI,
            neo4j_user=NEO4J_USER,
            neo4j_password=NEO4J_PASSWORD,
        )
        await daemon.connect()
        await daemon.claim_intent(intent_id)

        # Intent should be rejected (agent-beta lacks exec-ls)
        async with sdk.driver.session() as session:
            result = await session.run(
                "MATCH (i:Intent {id: $id}) RETURN i.status AS s, i.error AS e",
                id=intent_id,
            )
            record = await result.single()

        assert record["s"] == "rejected"
        assert "capability" in record["e"].lower()
        await daemon.close()

    @pytest.mark.integration
    async def test_execution_timeout(self, intent_sdk):
        """§5.3 — Intent exceeding 30s timeout fails with timeout error."""
        sdk, api_key = intent_sdk
        intent_id = await sdk.submit_intent(
            api_key=api_key,
            capability_id="exec-ls",
            params={"path": "/app"},
        )

        daemon = IntentDaemon(
            neo4j_uri=NEO4J_URI,
            neo4j_user=NEO4J_USER,
            neo4j_password=NEO4J_PASSWORD,
            intent_timeout_sec=1,  # 1s for test speed
        )
        await daemon.connect()

        # Mock the subprocess to simulate a long-running command
        with patch(
            "hassaleh.intent_daemon.asyncio.wait_for",
            side_effect=asyncio.TimeoutError(),
        ):
            await daemon.process_intent(intent_id)

        status = await sdk.get_intent_status(api_key, intent_id)
        assert status["status"] == "failed"

        result = await sdk.get_intent_result(api_key, intent_id)
        assert "timed out" in result["error"].lower()
        await daemon.close()

    @pytest.mark.integration
    async def test_max_concurrent_intents(self, intent_sdk):
        """§5.4 — Daemon processes at most 3 intents simultaneously."""
        sdk, api_key = intent_sdk
        daemon = IntentDaemon(
            neo4j_uri=NEO4J_URI,
            neo4j_user=NEO4J_USER,
            neo4j_password=NEO4J_PASSWORD,
            max_concurrent=3,
        )
        assert daemon.max_concurrent == 3


class TestDaemonReaper:
    """Spec §5.5 — Orphan recovery / reaper process."""

    @pytest.mark.integration
    async def test_reaper_fails_stale_claimed_intents(self, intent_sdk):
        """§5.5 — Intents stuck in 'claimed' beyond 2x timeout are failed."""
        sdk, api_key = intent_sdk
        intent_id = await sdk.submit_intent(
            api_key=api_key,
            capability_id="exec-ls",
            params={"path": "/app"},
        )

        # Manually set to claimed with an old timestamp (simulating orphan)
        async with sdk.driver.session() as session:
            await session.run("""
                MATCH (i:Intent {id: $id})
                SET i.status = 'claimed',
                    i.claimed_at = datetime() - duration({seconds: 120}),
                    i.updated_at = datetime() - duration({seconds: 120}),
                    i.claimed_by = 'dead-daemon'
            """, id=intent_id)

        daemon = IntentDaemon(
            neo4j_uri=NEO4J_URI,
            neo4j_user=NEO4J_USER,
            neo4j_password=NEO4J_PASSWORD,
        )
        await daemon.connect()
        reaped = await daemon.run_reaper()
        await daemon.close()

        assert reaped >= 1

        status = await sdk.get_intent_status(api_key, intent_id)
        assert status["status"] == "failed"

    @pytest.mark.integration
    async def test_reaper_fails_stale_running_intents(self, intent_sdk):
        """§5.5 — Intents stuck in 'running' are also reaped."""
        sdk, api_key = intent_sdk
        intent_id = await sdk.submit_intent(
            api_key=api_key,
            capability_id="exec-ls",
            params={"path": "/app"},
        )

        async with sdk.driver.session() as session:
            await session.run("""
                MATCH (i:Intent {id: $id})
                SET i.status = 'running',
                    i.updated_at = datetime() - duration({seconds: 120}),
                    i.claimed_by = 'dead-daemon'
            """, id=intent_id)

        daemon = IntentDaemon(
            neo4j_uri=NEO4J_URI,
            neo4j_user=NEO4J_USER,
            neo4j_password=NEO4J_PASSWORD,
        )
        await daemon.connect()
        await daemon.run_reaper()
        await daemon.close()

        status = await sdk.get_intent_status(api_key, intent_id)
        assert status["status"] == "failed"

    @pytest.mark.integration
    async def test_reaper_error_message_is_informative(self, intent_sdk):
        """§5.5 — Reaper sets a descriptive error, not a silent failure."""
        sdk, api_key = intent_sdk
        intent_id = await sdk.submit_intent(
            api_key=api_key,
            capability_id="exec-ls",
            params={"path": "/app"},
        )

        async with sdk.driver.session() as session:
            await session.run("""
                MATCH (i:Intent {id: $id})
                SET i.status = 'claimed',
                    i.updated_at = datetime() - duration({seconds: 120}),
                    i.claimed_by = 'dead-daemon'
            """, id=intent_id)

        daemon = IntentDaemon(
            neo4j_uri=NEO4J_URI,
            neo4j_user=NEO4J_USER,
            neo4j_password=NEO4J_PASSWORD,
        )
        await daemon.connect()
        await daemon.run_reaper()
        await daemon.close()

        result = await sdk.get_intent_result(api_key, intent_id)
        assert "reaper" in result["error"].lower() or "daemon" in result["error"].lower()

    @pytest.mark.integration
    async def test_startup_recovery_fails_own_orphans(self, intent_sdk):
        """§5.5 — On startup, daemon fails intents claimed by its own prior run."""
        sdk, api_key = intent_sdk
        instance_id = "daemon-restart-test"

        intent_id = await sdk.submit_intent(
            api_key=api_key,
            capability_id="exec-ls",
            params={"path": "/app"},
        )
        async with sdk.driver.session() as session:
            await session.run("""
                MATCH (i:Intent {id: $id})
                SET i.status = 'running',
                    i.claimed_by = $instance_id,
                    i.updated_at = datetime()
            """, id=intent_id, instance_id=instance_id)

        # New daemon with same instance_id — simulates restart
        daemon = IntentDaemon(
            neo4j_uri=NEO4J_URI,
            neo4j_user=NEO4J_USER,
            neo4j_password=NEO4J_PASSWORD,
            instance_id=instance_id,
        )
        await daemon.connect()
        recovered = await daemon.recover_own_orphans()
        await daemon.close()

        assert recovered >= 1

        status = await sdk.get_intent_status(api_key, intent_id)
        assert status["status"] == "failed"

    @pytest.mark.integration
    async def test_reaper_does_not_touch_fresh_intents(self, intent_sdk):
        """§5.5 — Reaper must NOT fail intents that are actively being processed."""
        sdk, api_key = intent_sdk
        intent_id = await sdk.submit_intent(
            api_key=api_key,
            capability_id="exec-ls",
            params={"path": "/app"},
        )

        # Set to running with a RECENT timestamp (actively processing)
        async with sdk.driver.session() as session:
            await session.run("""
                MATCH (i:Intent {id: $id})
                SET i.status = 'running',
                    i.updated_at = datetime(),
                    i.claimed_by = 'alive-daemon'
            """, id=intent_id)

        daemon = IntentDaemon(
            neo4j_uri=NEO4J_URI,
            neo4j_user=NEO4J_USER,
            neo4j_password=NEO4J_PASSWORD,
        )
        await daemon.connect()
        await daemon.run_reaper()
        await daemon.close()

        # Should still be running — reaper didn't touch it
        status = await sdk.get_intent_status(api_key, intent_id)
        assert status["status"] == "running"


# ═══════════════════════════════════════════════════════════════════════════
# 7. Edge Cases
# ═══════════════════════════════════════════════════════════════════════════

class TestCrossAgentAccess:
    """Spec §8.3 / F5 — Cross-agent read access must be blocked."""

    @pytest.mark.integration
    async def test_status_blocked_for_other_agent(self, two_agent_sdk):
        """§8.3 — Agent B cannot read Agent A's intent status."""
        sdk, key_a, key_b = two_agent_sdk

        # Alpha submits an intent
        intent_id = await sdk.submit_intent(
            api_key=key_a,
            capability_id="exec-ls",
            params={"path": "/app"},
        )

        # Beta tries to read it
        with pytest.raises(AccessDeniedError, match="does not belong"):
            await sdk.get_intent_status(key_b, intent_id)

    @pytest.mark.integration
    async def test_result_blocked_for_other_agent(self, two_agent_sdk):
        """§8.3 — Agent B cannot read Agent A's intent result."""
        sdk, key_a, key_b = two_agent_sdk

        intent_id = await sdk.submit_intent(
            api_key=key_a,
            capability_id="exec-ls",
            params={"path": "/app"},
        )

        with pytest.raises(AccessDeniedError, match="does not belong"):
            await sdk.get_intent_result(key_b, intent_id)


class TestConcurrency:
    """Spec §6.6, §8.5 — Concurrent intent handling."""

    @pytest.mark.integration
    async def test_concurrent_submissions_all_processed(self, intent_sdk):
        """§6.6 — 5 concurrent submissions all create pending intents."""
        sdk, api_key = intent_sdk

        tasks = [
            sdk.submit_intent(
                api_key=api_key,
                capability_id="exec-ls",
                params={"path": "/app"},
            )
            for _ in range(5)
        ]
        intent_ids = await asyncio.gather(*tasks)

        assert len(intent_ids) == 5
        assert len(set(intent_ids)) == 5  # all unique

        # All should be pending
        for iid in intent_ids:
            status = await sdk.get_intent_status(api_key, iid)
            assert status["status"] == "pending"

    @pytest.mark.integration
    async def test_atomic_claim_prevents_double_processing(self, intent_sdk):
        """§8.5 / F7 — Only one daemon can claim a given intent."""
        sdk, api_key = intent_sdk
        intent_id = await sdk.submit_intent(
            api_key=api_key,
            capability_id="exec-ls",
            params={"path": "/app"},
        )

        # Two daemons try to claim simultaneously
        daemon_a = IntentDaemon(
            neo4j_uri=NEO4J_URI,
            neo4j_user=NEO4J_USER,
            neo4j_password=NEO4J_PASSWORD,
            instance_id="daemon-a",
        )
        daemon_b = IntentDaemon(
            neo4j_uri=NEO4J_URI,
            neo4j_user=NEO4J_USER,
            neo4j_password=NEO4J_PASSWORD,
            instance_id="daemon-b",
        )
        await daemon_a.connect()
        await daemon_b.connect()

        results = await asyncio.gather(
            daemon_a.claim_intent(intent_id),
            daemon_b.claim_intent(intent_id),
        )

        await daemon_a.close()
        await daemon_b.close()

        # Exactly one should succeed
        assert results.count(True) == 1
        assert results.count(False) == 1

        # Verify claimed_by is set to exactly one daemon
        async with sdk.driver.session() as session:
            result = await session.run(
                "MATCH (i:Intent {id: $id}) RETURN i.claimed_by AS cb",
                id=intent_id,
            )
            record = await result.single()

        assert record["cb"] in ("daemon-a", "daemon-b")


class TestOutputTruncation:
    """Spec §3A — Output limits for exec-ls."""

    @pytest.mark.integration
    async def test_stdout_truncated_at_64kb(self, intent_sdk):
        """§3A — stdout capped at 64 KB with truncation marker."""
        sdk, api_key = intent_sdk
        intent_id = await sdk.submit_intent(
            api_key=api_key,
            capability_id="exec-ls",
            params={"path": "/app"},
        )

        # Simulate a very large ls output via daemon processing
        huge_output = "x" * (OUTPUT_TRUNCATION_LIMIT + 1000)

        daemon = IntentDaemon(
            neo4j_uri=NEO4J_URI,
            neo4j_user=NEO4J_USER,
            neo4j_password=NEO4J_PASSWORD,
        )
        await daemon.connect()

        # Mock subprocess to return huge output
        mock_result = MagicMock()
        mock_result.stdout = huge_output
        mock_result.stderr = ""
        mock_result.returncode = 0

        with patch("hassaleh.intent_daemon.subprocess.run", return_value=mock_result):
            await daemon.process_intent(intent_id)

        await daemon.close()

        result = await sdk.get_intent_result(api_key, intent_id)
        assert len(result["result"]) <= OUTPUT_TRUNCATION_LIMIT + 100  # margin for marker
        assert result["result"].endswith("[output truncated at 64KB]")


class TestExecutionSafety:
    """Spec §3A — Execution context safety checks."""

    def test_no_shell_true(self):
        """§3A — subprocess.run MUST NOT use shell=True.

        This is a code-level assertion: when the exec-ls handler is implemented,
        it must use list-form invocation. We verify by inspecting the handler.
        """
        import inspect
        from hassaleh.capabilities.exec_ls import execute_ls

        source = inspect.getsource(execute_ls)
        assert "shell=True" not in source, "exec-ls MUST NOT use shell=True"
        assert "shell = True" not in source, "exec-ls MUST NOT use shell=True"

    def test_uses_list_form_invocation(self):
        """§3A — Command must be passed as a list, not a string."""
        import inspect
        from hassaleh.capabilities.exec_ls import execute_ls

        source = inspect.getsource(execute_ls)
        # Should contain something like ["ls", "-la", resolved_path]
        assert '["ls"' in source or "['ls'" in source


class TestIntentStatusPolling:
    """Spec §6.7 — Status polling through lifecycle."""

    @pytest.mark.integration
    async def test_updated_at_changes_with_transitions(self, intent_sdk):
        """§6.7 step 4 — updated_at changes with each state transition."""
        sdk, api_key = intent_sdk
        intent_id = await sdk.submit_intent(
            api_key=api_key,
            capability_id="exec-ls",
            params={"path": "/app"},
        )

        status1 = await sdk.get_intent_status(api_key, intent_id)
        first_updated = status1["updated_at"]

        # Force transition to claimed
        async with sdk.driver.session() as session:
            await session.run("""
                MATCH (i:Intent {id: $id})
                SET i.status = 'claimed',
                    i.updated_at = datetime(),
                    i.claimed_at = datetime()
            """, id=intent_id)

        status2 = await sdk.get_intent_status(api_key, intent_id)
        assert status2["status"] == "claimed"
        assert status2["updated_at"] != first_updated
        assert status2["claimed_at"] is not None


class TestPathTraversalIntegration:
    """Spec §8.1 — Path traversal via full pipeline (not just validator unit)."""

    @pytest.mark.integration
    async def test_traversal_intent_fails_with_param_error(self, intent_sdk):
        """§8.1 — Intent with traversal path is rejected at param validation."""
        sdk, api_key = intent_sdk

        # The spec says this should fail — either at submit time
        # (if params are validated eagerly) or at processing time.
        # Either way, the intent must not succeed.
        with pytest.raises(CapabilityParamError):
            await sdk.submit_intent(
                api_key=api_key,
                capability_id="exec-ls",
                params={"path": "/../../../etc/passwd"},
            )

    @pytest.mark.integration
    async def test_no_listing_returned_on_traversal(self, intent_sdk):
        """§8.1 step 3 — No directory listing data leaks on traversal attempt."""
        sdk, api_key = intent_sdk

        try:
            intent_id = await sdk.submit_intent(
                api_key=api_key,
                capability_id="exec-ls",
                params={"path": "/../../../etc"},
            )
        except CapabilityParamError:
            return  # Correctly rejected at submission — test passes

        # If it wasn't rejected at submit, it should fail during processing
        result = await sdk.get_intent_result(api_key, intent_id)
        assert result["result"] is None
        assert result["status"] in ("failed", "rejected")
