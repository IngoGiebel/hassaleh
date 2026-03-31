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
    """Test that submit_intent calls correct Cypher via write transaction."""
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

    intent_id = await sdk.submit_intent(
        agent_id="dione",
        action="execute_capability",
        capability_id="exec-ls",
        value='{"args": "-la"}',
    )

    # Should have made 2 calls inside the transaction
    assert len(tx_calls) == 2

    # First call: create Intent + PROPOSED edge
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
async def test_poll_intent_returns_state():
    """Test poll_intent returns lifecycle/stdout/stderr."""
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

    state = await sdk.poll_intent("test-intent-123")

    assert state["lifecycle"] == "success"
    assert "file1.txt" in state["stdout"]
    assert state["exit_code"] == 0


@pytest.mark.asyncio
async def test_poll_intent_not_found():
    """Test poll_intent raises on unknown Intent."""
    driver, session = make_mock_driver()
    session.run = AsyncMock(return_value=make_mock_result([]))

    sdk = HassalehSDK()
    sdk.driver = driver
    sdk.query_config = {"default_timeout_ms": 3000, "max_result_rows": 10000}

    with pytest.raises(ValueError, match="not found"):
        await sdk.poll_intent("nonexistent-intent")
