from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from hassaleh.daemon import CAPABILITY_ALLOWLIST, HassalehDaemon


class _Result:
    def __init__(self, record):
        self._record = record

    async def single(self):
        return self._record


class _Session:
    def __init__(self, record):
        self.record = record

    async def run(self, *args, **kwargs):
        return _Result(self.record)


class _SessionCM:
    def __init__(self, record):
        self.record = record

    async def __aenter__(self):
        return _Session(self.record)

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.fixture
def daemon(monkeypatch):
    d = HassalehDaemon("bolt://unused", "neo4j", "unused")
    d.config = {"intent_timeout_default_sec": 5}
    d._fail_intent = AsyncMock()
    d._complete_intent = AsyncMock()
    d.driver = MagicMock()
    return d


@pytest.mark.asyncio
async def test_graph_injected_shell_metacharacter_in_invoke_command_rejected(daemon, monkeypatch):
    monkeypatch.setitem(CAPABILITY_ALLOWLIST, "exec-ls", ("/usr/bin/ls", "hassaleh-fs"))
    daemon.driver.session.return_value = _SessionCM(
        {
            "cap_id": "exec-ls",
            "command": "/usr/bin/ls; touch /tmp/pwned",
            "exec_user": "hassaleh-fs",
        }
    )

    await daemon._execute_capability("intent-il07-shell", {"action": "execute_capability", "value": None})

    daemon._fail_intent.assert_awaited_once()
    intent_id, error = daemon._fail_intent.await_args.args
    assert intent_id == "intent-il07-shell"
    assert error.startswith("Parameter validation failed [cid: ")
    assert "; touch /tmp/pwned" not in error
    daemon._complete_intent.assert_not_called()


@pytest.mark.asyncio
async def test_graph_injected_non_allowlisted_uid_rejected(daemon, monkeypatch):
    monkeypatch.setitem(CAPABILITY_ALLOWLIST, "exec-ls", ("/usr/bin/ls", "hassaleh-fs"))
    daemon.driver.session.return_value = _SessionCM(
        {
            "cap_id": "exec-ls",
            "command": "/usr/bin/ls",
            "exec_user": "root",
        }
    )

    await daemon._execute_capability("intent-il07-user", {"action": "execute_capability", "value": None})

    daemon._fail_intent.assert_awaited_once()
    intent_id, error = daemon._fail_intent.await_args.args
    assert intent_id == "intent-il07-user"
    assert error.startswith("Parameter validation failed [cid: ")
    assert "root" not in error
    daemon._complete_intent.assert_not_called()


@pytest.mark.asyncio
async def test_allowlisted_values_reach_subprocess_path(daemon, monkeypatch):
    monkeypatch.setitem(CAPABILITY_ALLOWLIST, "exec-ls", ("/usr/bin/ls", "hassaleh-fs"))
    daemon.driver.session.return_value = _SessionCM(
        {
            "cap_id": "exec-ls",
            "command": "/usr/bin/ls",
            "exec_user": "hassaleh-fs",
        }
    )

    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"ok", b""))
    proc.returncode = 0
    create_exec = AsyncMock(return_value=proc)
    monkeypatch.setattr("hassaleh.daemon.asyncio.create_subprocess_exec", create_exec)

    await daemon._execute_capability(
        "intent-il07-happy",
        {"action": "execute_capability", "value": '{"args": ["--version"]}'},
    )

    create_exec.assert_awaited_once()
    cmd = create_exec.await_args.args
    assert cmd[:4] == ("sudo", "-n", "-u", "hassaleh-fs")
    assert cmd[4] == "/usr/bin/ls"
    assert "--version" in cmd
    daemon._fail_intent.assert_not_called()
