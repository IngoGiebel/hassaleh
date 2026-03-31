"""Bridge tests for Hassaleh — Sprint 4.

Tests the OpenClaw Bridge and Notification Dispatcher.
Run with: PYTHONPATH=src pytest tests/test_bridge.py -v
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hassaleh.bridge.openclaw import OpenClawBridge
from hassaleh.bridge.notifications import NotificationDispatcher
from hassaleh.engine.runtime import AlertAction, LogAction


# ══════════════════════════════════════════════
# 1. OpenClawBridge
# ══════════════════════════════════════════════

@pytest.mark.asyncio
async def test_bridge_health_success():
    """Health check returns parsed JSON."""
    bridge = OpenClawBridge("http://localhost:18789", "test-token")
    bridge._session = MagicMock()

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={"status": "ok", "version": "1.0"})
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock()

    bridge._session.get = MagicMock(return_value=mock_resp)

    result = await bridge.health()
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_bridge_health_unreachable():
    """Health check returns unreachable on exception."""
    bridge = OpenClawBridge("http://localhost:18789", "test-token")
    bridge._session = MagicMock()
    bridge._session.get = MagicMock(side_effect=Exception("Connection refused"))

    result = await bridge.health()
    assert result["status"] == "unreachable"


@pytest.mark.asyncio
async def test_bridge_is_healthy():
    """is_healthy returns True when version in response."""
    bridge = OpenClawBridge("http://localhost:18789", "test-token")
    bridge._session = MagicMock()

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={"version": "2026.3.28"})
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock()

    bridge._session.get = MagicMock(return_value=mock_resp)

    assert await bridge.is_healthy() is True


@pytest.mark.asyncio
async def test_bridge_send_message():
    """send_message calls the correct tool endpoint."""
    bridge = OpenClawBridge("http://localhost:18789", "test-token")
    bridge._session = MagicMock()

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={"ok": True, "messageId": "123"})
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock()

    bridge._session.post = MagicMock(return_value=mock_resp)

    result = await bridge.send_message("telegram", "5580211068", "Hello!")
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_bridge_tool_call_error():
    """Tool call returns error dict on exception."""
    bridge = OpenClawBridge("http://localhost:18789", "test-token")
    bridge._session = MagicMock()
    bridge._session.post = MagicMock(side_effect=Exception("timeout"))

    result = await bridge._tool_call("message", {"test": True})
    assert result["ok"] is False
    assert "timeout" in result["error"]


# ══════════════════════════════════════════════
# 2. NotificationDispatcher
# ══════════════════════════════════════════════

@pytest.mark.asyncio
async def test_dispatcher_alert():
    """Alert dispatched as formatted message."""
    bridge = MagicMock(spec=OpenClawBridge)
    bridge.send_message = AsyncMock(return_value={"ok": True})

    dispatcher = NotificationDispatcher(bridge, "telegram", "5580211068")

    alert = AlertAction(
        message="Agent unresponsive",
        target_node={"id": "dione", "name": "Dione"},
        rule_id="health-check",
    )

    result = await dispatcher.dispatch_alert(alert)
    assert result is True

    # Verify message format
    call_args = bridge.send_message.call_args
    msg = call_args.kwargs.get("message") or call_args[1].get("message") or call_args[0][2]
    assert "Hassaleh Alert" in msg
    assert "Agent unresponsive" in msg
    assert "Dione" in msg


@pytest.mark.asyncio
async def test_dispatcher_no_target():
    """Alert dropped when no target configured."""
    bridge = MagicMock(spec=OpenClawBridge)
    dispatcher = NotificationDispatcher(bridge, "telegram", "")

    alert = AlertAction(message="test", rule_id="test")
    result = await dispatcher.dispatch_alert(alert)

    assert result is False
    bridge.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_dispatcher_error_log():
    """Error-level log dispatched as notification."""
    bridge = MagicMock(spec=OpenClawBridge)
    bridge.send_message = AsyncMock(return_value={"ok": True})

    dispatcher = NotificationDispatcher(bridge, "telegram", "5580211068")

    log_action = LogAction(message="Critical failure", level="error", rule_id="test")
    result = await dispatcher.dispatch_error_log(log_action)
    assert result is True


@pytest.mark.asyncio
async def test_dispatcher_info_log_not_sent():
    """Info-level log NOT dispatched."""
    bridge = MagicMock(spec=OpenClawBridge)
    dispatcher = NotificationDispatcher(bridge, "telegram", "5580211068")

    log_action = LogAction(message="All good", level="info", rule_id="test")
    result = await dispatcher.dispatch_error_log(log_action)
    assert result is False


@pytest.mark.asyncio
async def test_dispatcher_dispatch_all():
    """dispatch_all handles multiple alerts + error logs."""
    bridge = MagicMock(spec=OpenClawBridge)
    bridge.send_message = AsyncMock(return_value={"ok": True})

    dispatcher = NotificationDispatcher(bridge, "telegram", "5580211068")

    alerts = [
        AlertAction(message="Alert 1", rule_id="r1"),
        AlertAction(message="Alert 2", rule_id="r2"),
    ]
    logs = [
        LogAction(message="Error!", level="error", rule_id="r1"),
        LogAction(message="Info", level="info", rule_id="r2"),  # Should be skipped
    ]

    sent = await dispatcher.dispatch_all(alerts, logs)
    assert sent == 3  # 2 alerts + 1 error log
    assert bridge.send_message.call_count == 3
