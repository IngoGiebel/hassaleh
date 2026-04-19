"""Unit tests for Hassaleh SDK."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hassaleh.sdk import HassalehSDK


def make_mock_driver(session_mock=None):
    """Create a properly mocked Neo4j async driver.
    
    The real AsyncGraphDatabase.driver.session() returns an async context manager
    synchronously (not a coroutine). We need to match that behavior.
    """
    driver = MagicMock()  # Not AsyncMock — session() is a regular method
    session = session_mock or AsyncMock()
    
    # session() returns an async context manager synchronously
    ctx = AsyncMock()
    ctx.__aenter__.return_value = session
    ctx.__aexit__.return_value = False
    driver.session.return_value = ctx
    driver.close = AsyncMock()
    
    return driver, session


class AsyncResultMock:
    """Mock for Neo4j async result that supports `async for`."""

    def __init__(self, records_data: list[dict]):
        self._records = []
        for data in records_data:
            record = MagicMock()
            record.__getitem__ = lambda self, key, d=data: d[key]
            record.get = lambda key, default=None, d=data: d.get(key, default)
            record.keys = lambda d=data: d.keys()
            record.items = lambda d=data: d.items()
            record.data = lambda d=data: d
            self._records.append(record)
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._records):
            raise StopAsyncIteration
        record = self._records[self._index]
        self._index += 1
        return record

    async def single(self):
        return self._records[0] if self._records else None


def make_mock_result(records_data: list[dict]):
    """Create a mock async result that yields records."""
    return AsyncResultMock(records_data)


@pytest.mark.asyncio
async def test_query_parameterized():
    """Test that query uses parameterized Cypher."""
    driver, session = make_mock_driver()
    session.run = AsyncMock(return_value=make_mock_result([
        {"id": "dione", "name": "Dione"}
    ]))

    sdk = HassalehSDK()
    sdk.driver = driver
    sdk.query_config = {"default_timeout_ms": 3000, "max_result_rows": 10000}

    records = await sdk.query(
        "MATCH (a:Agent {id: $id}) RETURN a.id AS id, a.name AS name",
        {"id": "dione"},
    )

    # Verify parameterized call
    session.run.assert_called_once()
    call_args = session.run.call_args
    cypher_arg = call_args[0][0]
    params_arg = call_args[0][1] if len(call_args[0]) > 1 else call_args[1]
    assert "$id" in cypher_arg
    assert params_arg["id"] == "dione"
    assert len(records) == 1
    assert records[0]["id"] == "dione"


@pytest.mark.asyncio
async def test_connect_omits_auth_when_credentials_are_blank():
    """Test connect() disables auth when both credentials are blank."""
    driver, session = make_mock_driver()
    session.run = AsyncMock(return_value=make_mock_result([{"ping": 1}]))

    sdk = HassalehSDK(
        neo4j_uri="bolt://localhost:7687",
        neo4j_user="",
        neo4j_password="",
    )
    sdk._load_query_config = AsyncMock()

    with patch("hassaleh.sdk.AsyncGraphDatabase.driver", return_value=driver) as driver_factory:
        await sdk.connect()

    driver_factory.assert_called_once_with("bolt://localhost:7687", auth=None)
    sdk._load_query_config.assert_awaited_once()


@pytest.mark.asyncio
async def test_query_adds_limit():
    """Test that query adds LIMIT if not present."""
    driver, session = make_mock_driver()
    session.run = AsyncMock(return_value=make_mock_result([]))

    sdk = HassalehSDK()
    sdk.driver = driver
    sdk.query_config = {"default_timeout_ms": 3000, "max_result_rows": 500}

    await sdk.query("MATCH (n) RETURN n")

    cypher = session.run.call_args[0][0]
    assert "LIMIT 500" in cypher


@pytest.mark.asyncio
async def test_query_respects_existing_limit():
    """Test that query doesn't add LIMIT if already present."""
    driver, session = make_mock_driver()
    session.run = AsyncMock(return_value=make_mock_result([]))

    sdk = HassalehSDK()
    sdk.driver = driver
    sdk.query_config = {"default_timeout_ms": 3000, "max_result_rows": 500}

    await sdk.query("MATCH (n) RETURN n LIMIT 10")

    cypher = session.run.call_args[0][0]
    assert cypher.count("LIMIT") == 1


@pytest.mark.asyncio
async def test_submit_intent_creates_node():
    """submit_intent derives agent_id from api_key and writes correct Cypher."""
    driver, session = make_mock_driver()

    # Track calls made inside the transaction function
    tx_calls = []
    tx_mock = AsyncMock()
    async def _fake_run(cypher, **kwargs):
        tx_calls.append((cypher, kwargs))
    tx_mock.run = _fake_run

    async def _fake_execute_write(func):
        await func(tx_mock)
    session.execute_write = _fake_execute_write

    sdk = HassalehSDK()
    sdk.driver = driver
    sdk._authenticate = AsyncMock(return_value="dione")

    intent_id = await sdk.submit_intent(
        api_key="sk-test-dione",
        action="execute_capability",
        capability_id="exec-ls",
        value='{"args": "-la"}',
    )

    # Authentication happens server-side from api_key (IL-01)
    sdk._authenticate.assert_awaited_once_with("sk-test-dione")

    # Should have made 2 calls inside the transaction
    assert len(tx_calls) == 2

    # First call: create Intent + PROPOSED edge from the *derived* agent
    assert "Intent" in tx_calls[0][0]
    assert "PROPOSED" in tx_calls[0][0]
    assert tx_calls[0][1]["agent_id"] == "dione"
    assert tx_calls[0][1]["action"] == "execute_capability"

    # Second call: TARGETS edge to capability
    assert "TARGETS" in tx_calls[1][0]
    assert tx_calls[1][1]["cap_id"] == "exec-ls"

    # Returns a UUID string
    assert len(intent_id) == 36


@pytest.mark.asyncio
async def test_submit_intent_rejects_legacy_agent_id_kwarg():
    """IL-01: the caller-supplied agent_id kwarg must no longer be accepted.

    Security regression guard — the signature change itself is the enforcement:
    passing agent_id= must raise TypeError so no code path can forge identity.
    """
    sdk = HassalehSDK()

    with pytest.raises(TypeError):
        await sdk.submit_intent(
            agent_id="victim-agent",  # type: ignore[call-arg]
            action="execute_capability",
            capability_id="exec-ls",
        )


@pytest.mark.asyncio
async def test_submit_intent_ignores_mismatched_agent_id_in_graph():
    """IL-01: even if the graph has a different agent id, the authenticated
    one is what gets written to the PROPOSED edge. The api_key is the only
    source of identity."""
    driver, session = make_mock_driver()

    tx_calls = []
    tx_mock = AsyncMock()
    async def _fake_run(cypher, **kwargs):
        tx_calls.append((cypher, kwargs))
    tx_mock.run = _fake_run

    async def _fake_execute_write(func):
        await func(tx_mock)
    session.execute_write = _fake_execute_write

    sdk = HassalehSDK()
    sdk.driver = driver
    # Authenticated identity is "alice"; the attacker cannot override this.
    sdk._authenticate = AsyncMock(return_value="alice")

    await sdk.submit_intent(
        api_key="sk-alice",
        action="execute_capability",
        capability_id="exec-ls",
    )

    # Every Cypher parameter block must bind agent_id='alice'
    agent_ids = [kw.get("agent_id") for _, kw in tx_calls if "agent_id" in kw]
    assert agent_ids, "expected at least one Cypher call bound to agent_id"
    assert all(aid == "alice" for aid in agent_ids)


@pytest.mark.asyncio
async def test_submit_intent_propagates_auth_failure():
    """IL-01: no Intent is created when authentication fails."""
    from hassaleh.errors import AuthenticationError

    driver, session = make_mock_driver()
    session.execute_write = AsyncMock()

    sdk = HassalehSDK()
    sdk.driver = driver
    sdk._authenticate = AsyncMock(side_effect=AuthenticationError("Invalid API key"))

    with pytest.raises(AuthenticationError):
        await sdk.submit_intent(
            api_key="sk-forged",
            action="execute_capability",
            capability_id="exec-ls",
        )

    session.execute_write.assert_not_called()


@pytest.mark.asyncio
async def test_my_tasks_rejects_legacy_agent_id_kwarg():
    """IL-01: my_tasks() signature no longer accepts agent_id."""
    sdk = HassalehSDK()

    with pytest.raises(TypeError):
        await sdk.my_tasks(agent_id="victim-agent")  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_review_task_rejects_legacy_agent_id_kwarg():
    """IL-01: review_task() signature no longer accepts agent_id."""
    sdk = HassalehSDK()

    with pytest.raises(TypeError):
        await sdk.review_task(
            "task-1",
            agent_id="victim-agent",  # type: ignore[call-arg]
            approved=True,
        )


@pytest.mark.asyncio
async def test_send_message_rejects_legacy_agent_id_kwarg():
    """IL-01: send_message() signature no longer accepts agent_id."""
    sdk = HassalehSDK()

    with pytest.raises(TypeError):
        await sdk.send_message(agent_id="victim-agent", content="forged")  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_agent_info_rejects_legacy_agent_id_kwarg():
    """IL-01: agent_info() signature no longer accepts agent_id."""
    sdk = HassalehSDK()

    with pytest.raises(TypeError):
        await sdk.agent_info(agent_id="victim-agent")  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_my_tasks_derives_agent_from_api_key():
    """my_tasks() derives agent_id server-side from api_key (IL-01)."""
    driver, session = make_mock_driver()
    session.run = AsyncMock(return_value=make_mock_result([
        {"id": "task-1", "name": "T", "lifecycle": "pending",
         "description": "", "expires_at": None}
    ]))

    sdk = HassalehSDK()
    sdk.driver = driver
    sdk.query_config = {"default_timeout_ms": 3000, "max_result_rows": 10000}
    sdk._authenticate = AsyncMock(return_value="bob")

    await sdk.my_tasks("sk-bob")

    sdk._authenticate.assert_awaited_once_with("sk-bob")
    params_arg = session.run.call_args[0][1]
    assert params_arg["agent_id"] == "bob"


@pytest.mark.asyncio
async def test_poll_intent_returns_state():
    """poll_intent returns lifecycle/stdout/stderr when the caller owns the Intent."""
    driver, session = make_mock_driver()
    session.run = AsyncMock(return_value=make_mock_result([{
        "lifecycle": "success",
        "stdout": "file1.txt\nfile2.txt",
        "stderr": "",
        "error_reason": None,
        "exit_code": 0,
        "submitted_at": None,
        "completed_at": None,
    }]))

    sdk = HassalehSDK()
    sdk.driver = driver
    sdk.query_config = {"default_timeout_ms": 3000, "max_result_rows": 10000}
    sdk._authenticate = AsyncMock(return_value="dione")

    state = await sdk.poll_intent("test-intent-123", "sk-dione")

    sdk._authenticate.assert_awaited_once_with("sk-dione")
    # The ownership-scoped query must bind the authenticated agent_id.
    params = session.run.call_args[0][1]
    assert params["agent_id"] == "dione"
    assert params["id"] == "test-intent-123"
    assert state["lifecycle"] == "success"
    assert "file1.txt" in state["stdout"]
    assert state["exit_code"] == 0


@pytest.mark.asyncio
async def test_poll_intent_requires_api_key():
    """IL-03: missing/invalid api_key raises AuthenticationError before any graph read."""
    from hassaleh.errors import AuthenticationError

    driver, session = make_mock_driver()
    session.run = AsyncMock(return_value=make_mock_result([]))

    sdk = HassalehSDK()
    sdk.driver = driver
    sdk.query_config = {"default_timeout_ms": 3000, "max_result_rows": 10000}
    sdk._authenticate = AsyncMock(side_effect=AuthenticationError("Invalid API key"))

    with pytest.raises(AuthenticationError):
        await sdk.poll_intent("test-intent-123", "sk-forged")

    # No Intent query should have been attempted.
    session.run.assert_not_called()


@pytest.mark.asyncio
async def test_poll_intent_rejects_other_agents_intent():
    """IL-03: agent B polling agent A's Intent raises a sanitized PermissionError."""
    driver, session = make_mock_driver()
    # Ownership-scoped Cypher returns zero rows when the agent isn't the owner.
    session.run = AsyncMock(return_value=make_mock_result([]))

    sdk = HassalehSDK()
    sdk.driver = driver
    sdk.query_config = {"default_timeout_ms": 3000, "max_result_rows": 10000}
    # Agent B is authenticated; Intent is owned by agent A.
    sdk._authenticate = AsyncMock(return_value="bob")

    with pytest.raises(PermissionError) as excinfo:
        await sdk.poll_intent("alice-owned-intent", "sk-bob")

    # Sanitized: does not reveal whether the Intent exists.
    msg = str(excinfo.value)
    assert "alice-owned-intent" not in msg
    assert "not found or not authorized" in msg


@pytest.mark.asyncio
async def test_poll_intent_allows_owner():
    """IL-03: the owning agent can read its own Intent."""
    driver, session = make_mock_driver()
    session.run = AsyncMock(return_value=make_mock_result([{
        "lifecycle": "running",
        "stdout": None,
        "stderr": None,
        "error_reason": None,
        "exit_code": None,
        "submitted_at": None,
        "completed_at": None,
    }]))

    sdk = HassalehSDK()
    sdk.driver = driver
    sdk.query_config = {"default_timeout_ms": 3000, "max_result_rows": 10000}
    sdk._authenticate = AsyncMock(return_value="alice")

    state = await sdk.poll_intent("alice-owned-intent", "sk-alice")

    assert state["lifecycle"] == "running"
    # Verify the ownership predicate made it into the Cypher.
    cypher = session.run.call_args[0][0]
    assert "(a:Agent {id: $agent_id})-[:PROPOSED]->(i:Intent {id: $id})" in cypher


@pytest.mark.asyncio
async def test_poll_intent_sanitizes_not_found_vs_not_owned():
    """IL-03: error message is byte-identical for 'not found' and 'not owned'.

    An attacker must not be able to distinguish 'Intent ID does not exist'
    from 'Intent exists but you don't own it' via the error surface.
    """
    driver, session = make_mock_driver()
    # Both paths collapse to zero rows on the ownership-scoped query.
    session.run = AsyncMock(return_value=make_mock_result([]))

    sdk = HassalehSDK()
    sdk.driver = driver
    sdk.query_config = {"default_timeout_ms": 3000, "max_result_rows": 10000}
    sdk._authenticate = AsyncMock(return_value="bob")

    # Case 1: Intent does not exist in graph at all.
    with pytest.raises(PermissionError) as not_found:
        await sdk.poll_intent("ghost-intent", "sk-bob")

    # Case 2: Intent exists but is owned by someone else.
    with pytest.raises(PermissionError) as not_owned:
        await sdk.poll_intent("alice-owned-intent", "sk-bob")

    assert str(not_found.value) == str(not_owned.value)
    assert str(not_found.value) == "intent not found or not authorized"


@pytest.mark.asyncio
async def test_wait_for_intent_enforces_ownership():
    """IL-03: wait_for_intent inherits poll_intent's ownership check."""
    driver, session = make_mock_driver()
    session.run = AsyncMock(return_value=make_mock_result([]))

    sdk = HassalehSDK()
    sdk.driver = driver
    sdk.query_config = {"default_timeout_ms": 3000, "max_result_rows": 10000}
    sdk._authenticate = AsyncMock(return_value="bob")

    with pytest.raises(PermissionError, match="not found or not authorized"):
        await sdk.wait_for_intent(
            "alice-owned-intent",
            "sk-bob",
            timeout_sec=0.5,
            poll_interval=0.05,
        )

    # The api_key must be forwarded to the internal poll_intent call.
    sdk._authenticate.assert_awaited_with("sk-bob")


@pytest.mark.asyncio
async def test_wait_for_intent_sanitizes_not_found_vs_not_owned():
    """IL-03: wait_for_intent must not leak ownership vs existence by error shape."""
    driver, session = make_mock_driver()
    session.run = AsyncMock(return_value=make_mock_result([]))

    sdk = HassalehSDK()
    sdk.driver = driver
    sdk.query_config = {"default_timeout_ms": 3000, "max_result_rows": 10000}
    sdk._authenticate = AsyncMock(return_value="bob")

    with pytest.raises(PermissionError) as not_found:
        await sdk.wait_for_intent(
            "ghost-intent",
            "sk-bob",
            timeout_sec=0.5,
            poll_interval=0.05,
        )

    with pytest.raises(PermissionError) as not_owned:
        await sdk.wait_for_intent(
            "alice-owned-intent",
            "sk-bob",
            timeout_sec=0.5,
            poll_interval=0.05,
        )

    assert str(not_found.value) == str(not_owned.value)
    assert str(not_found.value) == "intent not found or not authorized"


@pytest.mark.asyncio
async def test_poll_intent_empty_api_key_raises_without_graph_touch():
    """IL-03: malformed/empty api_key must fail before any Intent read."""
    from hassaleh.errors import AuthenticationError

    driver, session = make_mock_driver()
    session.run = AsyncMock(return_value=make_mock_result([]))

    sdk = HassalehSDK()
    sdk.driver = driver
    sdk.query_config = {"default_timeout_ms": 3000, "max_result_rows": 10000}
    sdk._authenticate = AsyncMock(side_effect=AuthenticationError("Invalid API key"))

    with pytest.raises(AuthenticationError, match="Invalid API key"):
        await sdk.poll_intent("test-intent-123", "")

    session.run.assert_not_called()


@pytest.mark.asyncio
async def test_wait_for_intent_empty_api_key_raises_without_graph_touch():
    """IL-03: wait_for_intent must also fail auth before any Intent read."""
    from hassaleh.errors import AuthenticationError

    driver, session = make_mock_driver()
    session.run = AsyncMock(return_value=make_mock_result([]))

    sdk = HassalehSDK()
    sdk.driver = driver
    sdk.query_config = {"default_timeout_ms": 3000, "max_result_rows": 10000}
    sdk._authenticate = AsyncMock(side_effect=AuthenticationError("Invalid API key"))

    with pytest.raises(AuthenticationError, match="Invalid API key"):
        await sdk.wait_for_intent(
            "test-intent-123",
            "",
            timeout_sec=0.5,
            poll_interval=0.05,
        )

    session.run.assert_not_called()


@pytest.mark.asyncio
async def test_poll_intent_inactive_agent_cannot_read_prior_intent():
    """IL-03: inactive/revoked agents cannot poll even for previously submitted Intents."""
    from hassaleh.errors import AuthenticationError

    driver, session = make_mock_driver()
    session.run = AsyncMock(return_value=make_mock_result([{
        "lifecycle": "success",
        "stdout": "secret",
        "stderr": "",
        "error_reason": None,
        "exit_code": 0,
        "submitted_at": None,
        "completed_at": None,
    }]))

    sdk = HassalehSDK()
    sdk.driver = driver
    sdk.query_config = {"default_timeout_ms": 3000, "max_result_rows": 10000}
    sdk._authenticate = AsyncMock(side_effect=AuthenticationError("Agent is inactive"))

    with pytest.raises(AuthenticationError, match="inactive"):
        await sdk.poll_intent("previously-owned-intent", "sk-revoked")

    session.run.assert_not_called()


@pytest.mark.asyncio
async def test_wait_for_intent_inactive_agent_cannot_read_prior_intent():
    """IL-03: inactive/revoked agents cannot wait on previously submitted Intents."""
    from hassaleh.errors import AuthenticationError

    driver, session = make_mock_driver()
    session.run = AsyncMock(return_value=make_mock_result([{
        "lifecycle": "success",
        "stdout": "secret",
        "stderr": "",
        "error_reason": None,
        "exit_code": 0,
        "submitted_at": None,
        "completed_at": None,
    }]))

    sdk = HassalehSDK()
    sdk.driver = driver
    sdk.query_config = {"default_timeout_ms": 3000, "max_result_rows": 10000}
    sdk._authenticate = AsyncMock(side_effect=AuthenticationError("Agent is inactive"))

    with pytest.raises(AuthenticationError, match="inactive"):
        await sdk.wait_for_intent(
            "previously-owned-intent",
            "sk-revoked",
            timeout_sec=0.5,
            poll_interval=0.05,
        )

    session.run.assert_not_called()


@pytest.mark.asyncio
async def test_wait_for_intent_requires_api_key_positional():
    """IL-03 signature guard: api_key must be required, not optional.

    Calling wait_for_intent without api_key must fail at the function
    signature layer — this prevents legacy callers from silently reading
    other agents' Intents.
    """
    sdk = HassalehSDK()

    with pytest.raises(TypeError):
        await sdk.wait_for_intent("some-intent")  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_wait_for_intent_timeout_message_is_sanitized():
    """IL-03 follow-up: TimeoutError must not leak intent_id or timeout_sec.

    The intent exists and is owned by the caller, but its lifecycle stays
    non-terminal, so the deadline fires. The resulting TimeoutError message
    must be a fixed static string with no caller-supplied identifiers and
    no internal timeout value.
    """
    driver, session = make_mock_driver()

    # Each poll cycle needs a fresh async result — AsyncResultMock's
    # iterator is single-use, so side_effect (factory) is required here.
    def pending_record(*_args, **_kwargs):
        return make_mock_result([{
            "lifecycle": "pending",
            "stdout": None,
            "stderr": None,
            "error_reason": None,
            "exit_code": None,
            "submitted_at": None,
            "completed_at": None,
        }])

    session.run = AsyncMock(side_effect=pending_record)

    sdk = HassalehSDK()
    sdk.driver = driver
    sdk.query_config = {"default_timeout_ms": 3000, "max_result_rows": 10000}
    sdk._authenticate = AsyncMock(return_value="bob")

    intent_id = "alice-owned-but-stuck-intent-7f3a"
    timeout_sec = 0.1

    with pytest.raises(TimeoutError) as exc_info:
        await sdk.wait_for_intent(
            intent_id,
            "sk-bob",
            timeout_sec=timeout_sec,
            poll_interval=0.05,
        )

    msg = str(exc_info.value)
    assert msg == "intent did not complete before deadline"
    assert intent_id not in msg
    assert str(timeout_sec) not in msg
