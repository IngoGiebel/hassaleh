"""Task execution mode tests for Sprint 6 Phase 3."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from hassaleh.cli import build_parser, cmd_task_review
from hassaleh.daemon import (
    HassalehDaemon,
    _next_sequential_task_id,
    _normalize_task_lifecycle_update,
    _parallel_group_should_activate,
    _parent_group_lifecycle,
)
from hassaleh.sdk import HassalehSDK


class AsyncResultMock:
    """Mock for Neo4j async result that supports async iteration."""

    def __init__(self, records_data: list[dict]):
        self._records = []
        for data in records_data:
            record = MagicMock()
            record.__getitem__ = lambda self, key, d=data: d[key]
            record.get = lambda key, default=None, d=data: d.get(key, default)
            record.keys = lambda d=data: d.keys()
            record.items = lambda d=data: d.items()
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


def make_async_result(records_data: list[dict]) -> AsyncResultMock:
    return AsyncResultMock(records_data)


def make_async_driver(session_mock=None):
    driver = MagicMock()
    session = session_mock or AsyncMock()
    ctx = AsyncMock()
    ctx.__aenter__.return_value = session
    ctx.__aexit__.return_value = False
    driver.session.return_value = ctx
    return driver, session


class SyncResult:
    def __init__(self, records_data: list[dict]):
        self._records = records_data

    def single(self):
        return self._records[0] if self._records else None

    def __iter__(self):
        return iter(self._records)


def make_sync_driver(session_mock=None):
    driver = MagicMock()
    session = session_mock or MagicMock()
    ctx = MagicMock()
    ctx.__enter__.return_value = session
    ctx.__exit__.return_value = False
    driver.session.return_value = ctx
    driver.close = MagicMock()
    return driver, session


def test_next_sequential_task_id_returns_first_pending():
    task_id = _next_sequential_task_id([
        {"id": "a", "lifecycle": "success"},
        {"id": "b", "lifecycle": "pending"},
        {"id": "c", "lifecycle": "pending"},
    ])
    assert task_id == "b"


def test_next_sequential_task_id_stops_on_running_task():
    task_id = _next_sequential_task_id([
        {"id": "a", "lifecycle": "success"},
        {"id": "b", "lifecycle": "running"},
        {"id": "c", "lifecycle": "pending"},
    ])
    assert task_id is None


def test_next_sequential_task_id_returns_none_when_complete():
    assert _next_sequential_task_id([
        {"id": "a", "lifecycle": "success"},
        {"id": "b", "lifecycle": "success"},
    ]) is None


def test_parallel_group_should_activate_with_pending_tasks():
    assert _parallel_group_should_activate(
        ["pending", "running"],
        fail_fast=True,
    ) is True


def test_parallel_group_should_not_activate_after_failure_in_fail_fast():
    assert _parallel_group_should_activate(
        ["pending", "failed"],
        fail_fast=True,
    ) is False


def test_parallel_group_can_activate_after_failure_when_not_fail_fast():
    assert _parallel_group_should_activate(
        ["pending", "failed"],
        fail_fast=False,
    ) is True


def test_parent_group_lifecycle_returns_success_when_all_children_succeed():
    assert _parent_group_lifecycle(
        ["success", "success"],
        fail_fast=True,
    ) == "success"


def test_parent_group_lifecycle_returns_failed_on_fail_fast_failure():
    assert _parent_group_lifecycle(
        ["success", "failed", "running"],
        fail_fast=True,
    ) == "failed"


def test_parent_group_lifecycle_waits_for_review():
    assert _parent_group_lifecycle(
        ["success", "awaiting_review"],
        fail_fast=True,
    ) is None


def test_normalize_task_lifecycle_update_routes_supervised_success_to_review():
    assert _normalize_task_lifecycle_update("supervised", "success") == "awaiting_review"


@pytest.mark.asyncio
async def test_sdk_task_group_status_queries_parent_group():
    driver, session = make_async_driver()
    session.run = AsyncMock(return_value=make_async_result([
        {
            "id": "task-1",
            "name": "First",
            "lifecycle": "ready",
            "execution_mode": "sequential",
            "execution_order": 1,
            "parent_task_id": "parent-1",
        }
    ]))

    sdk = HassalehSDK()
    sdk.driver = driver
    sdk.query_config = {"default_timeout_ms": 3000, "max_result_rows": 100}

    records = await sdk.task_group_status("parent-1")

    assert records[0]["id"] == "task-1"
    assert session.run.call_args[0][1]["parent_task_id"] == "parent-1"


@pytest.mark.asyncio
async def test_sdk_review_task_creates_review_intent():
    driver, session = make_async_driver()
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

    await sdk.review_task("task-7", "lead-1", approved=False, comment="redo")

    assert len(tx_calls) == 2
    assert "action: 'review_task'" in tx_calls[0][0]
    payload = json.loads(tx_calls[0][1]["payload"])
    assert payload == {"approved": False, "comment": "redo"}
    assert tx_calls[1][1]["task_id"] == "task-7"


def test_parser_task_review_approve():
    parser = build_parser()
    args = parser.parse_args(["task", "review", "task-1", "--approve"])
    assert args.command == "task"
    assert args.task_command == "review"
    assert args.task_id == "task-1"
    assert args.approve is True
    assert args.reject is False


def test_parser_task_review_reject():
    parser = build_parser()
    args = parser.parse_args(["task", "review", "task-2", "--reject", "--agent-id", "lead-1"])
    assert args.command == "task"
    assert args.task_command == "review"
    assert args.reject is True
    assert args.agent_id == "lead-1"


def test_cmd_task_review_approves_with_inferred_lead(monkeypatch, capsys):
    driver, session = make_sync_driver()
    session.run.side_effect = [
        SyncResult([{
            "execution_mode": "supervised",
            "lifecycle": "awaiting_review",
            "lead_agent_ids": ["lead-1"],
        }]),
        SyncResult([]),
    ]
    monkeypatch.setattr("hassaleh.cli.get_connection", lambda args: {})
    monkeypatch.setattr("hassaleh.cli.connect", lambda conn: driver)

    parser = build_parser()
    args = parser.parse_args(["task", "review", "task-5", "--approve"])
    rc = cmd_task_review(args)

    out = capsys.readouterr().out
    assert rc == 0
    assert "task-5" in out
    assert session.run.call_args_list[1].kwargs["reviewer_id"] == "lead-1"
    assert session.run.call_args_list[1].kwargs["lifecycle"] == "success"


def test_cmd_task_review_rejects_non_supervised_task(monkeypatch, capsys):
    driver, session = make_sync_driver()
    session.run.side_effect = [
        SyncResult([{
            "execution_mode": "parallel",
            "lifecycle": "awaiting_review",
            "lead_agent_ids": ["lead-1"],
        }]),
    ]
    monkeypatch.setattr("hassaleh.cli.get_connection", lambda args: {})
    monkeypatch.setattr("hassaleh.cli.connect", lambda conn: driver)

    parser = build_parser()
    args = parser.parse_args(["task", "review", "task-5", "--approve"])
    rc = cmd_task_review(args)

    out = capsys.readouterr().out
    assert rc == 1
    assert "not supervised" in out


@pytest.mark.asyncio
async def test_daemon_activate_sequential_task_groups_promotes_next_task():
    driver, session = make_async_driver()
    session.run = AsyncMock(side_effect=[
        make_async_result([{"parent_task_id": "parent-1"}]),
        make_async_result([
            {"id": "task-1", "lifecycle": "success", "execution_order": 1},
            {"id": "task-2", "lifecycle": "pending", "execution_order": 2},
        ]),
        make_async_result([]),
    ])

    daemon = HassalehDaemon("bolt://x", "neo4j", "pw")
    daemon.driver = driver

    await daemon._activate_sequential_task_groups()

    assert session.run.call_args_list[2].kwargs["task_id"] == "task-2"


@pytest.mark.asyncio
async def test_daemon_activate_parallel_task_groups_promotes_all_pending():
    driver, session = make_async_driver()
    session.run = AsyncMock(side_effect=[
        make_async_result([{"parent_task_id": "parent-2"}]),
        make_async_result([
            {"lifecycle": "pending"},
            {"lifecycle": "success"},
        ]),
        make_async_result([]),
    ])

    daemon = HassalehDaemon("bolt://x", "neo4j", "pw")
    daemon.driver = driver
    daemon.config["parallel_fail_fast"] = True

    await daemon._activate_parallel_task_groups()

    assert session.run.call_args_list[2].kwargs["parent_task_id"] == "parent-2"


@pytest.mark.asyncio
async def test_daemon_sync_parent_task_states_marks_parent_success():
    driver, session = make_async_driver()
    session.run = AsyncMock(side_effect=[
        make_async_result([{"parent_task_id": "parent-3"}]),
        make_async_result([
            {"lifecycle": "success"},
            {"lifecycle": "success"},
        ]),
        make_async_result([]),
    ])

    daemon = HassalehDaemon("bolt://x", "neo4j", "pw")
    daemon.driver = driver

    await daemon._sync_parent_task_states()

    assert session.run.call_args_list[2].kwargs["parent_task_id"] == "parent-3"
    assert session.run.call_args_list[2].kwargs["lifecycle"] == "success"


@pytest.mark.asyncio
async def test_daemon_execute_update_routes_supervised_completion_to_awaiting_review():
    driver, session = make_async_driver()
    session.run = AsyncMock(side_effect=[
        make_async_result([{"labels": ["Task"], "execution_mode": "supervised"}]),
        make_async_result([]),
        make_async_result([]),
    ])

    daemon = HassalehDaemon("bolt://x", "neo4j", "pw")
    daemon.driver = driver

    await daemon._execute_update("intent-1", {"property": "lifecycle", "value": "success"})

    assert session.run.call_args_list[1].kwargs["value"] == "awaiting_review"


@pytest.mark.asyncio
async def test_daemon_execute_task_review_approves_task():
    driver, session = make_async_driver()
    session.run = AsyncMock(side_effect=[
        make_async_result([{
            "task_id": "task-9",
            "lifecycle": "awaiting_review",
            "lead_agent_ids": ["lead-9"],
            "review_payload": json.dumps({"approved": True, "comment": "looks good"}),
        }]),
        make_async_result([]),
        make_async_result([]),
    ])

    daemon = HassalehDaemon("bolt://x", "neo4j", "pw")
    daemon.driver = driver

    await daemon._execute_task_review("intent-9", {"id": "lead-9"})

    assert session.run.call_args_list[1].kwargs["lifecycle"] == "success"
    assert session.run.call_args_list[1].kwargs["comment"] == "looks good"
