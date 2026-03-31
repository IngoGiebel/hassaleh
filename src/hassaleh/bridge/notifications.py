"""Notification dispatcher — Routes rule ALERT actions to messaging channels.

Dispatches alerts from GSL-Ops rules through the OpenClaw bridge
to Telegram, Discord, or other configured channels.

Usage:
    dispatcher = NotificationDispatcher(bridge, default_channel, default_target)
    await dispatcher.dispatch_alert(alert_action)
"""

from __future__ import annotations

import logging
from typing import Any

from hassaleh.bridge.openclaw import OpenClawBridge
from hassaleh.engine.runtime import AlertAction, LogAction

log = logging.getLogger("hassaleh.bridge.notifications")


class NotificationDispatcher:
    """Routes rule outputs to messaging channels via OpenClaw.

    Dispatches:
    - ALERT actions → Telegram/Discord messages
    - LOG actions at "error" level → optional error notifications
    """

    def __init__(
        self,
        bridge: OpenClawBridge,
        default_channel: str = "telegram",
        default_target: str = "",
        notify_on_error_logs: bool = True,
    ):
        self.bridge = bridge
        self.default_channel = default_channel
        self.default_target = default_target
        self.notify_on_error_logs = notify_on_error_logs

    async def dispatch_alert(self, alert: AlertAction) -> bool:
        """Send an alert notification via OpenClaw messaging.

        Args:
            alert: AlertAction from a GSL-Ops rule

        Returns:
            True if sent successfully
        """
        if not self.default_target:
            log.warning(f"No notification target configured, dropping alert: {alert.message}")
            return False

        # Format the alert message
        target_info = ""
        if alert.target_node and isinstance(alert.target_node, dict):
            target_name = alert.target_node.get("name", alert.target_node.get("id", "?"))
            target_info = f" [{target_name}]"

        message = f"⚠️ **Hassaleh Alert**{target_info}\n\n{alert.message}\n\n_Rule: {alert.rule_id}_"

        try:
            result = await self.bridge.send_message(
                channel=self.default_channel,
                target=self.default_target,
                message=message,
            )
            if result.get("ok"):
                log.info(f"Alert dispatched: {alert.message[:50]}")
                return True
            else:
                log.warning(f"Alert dispatch failed: {result}")
                return False
        except Exception as e:
            log.error(f"Alert dispatch error: {e}")
            return False

    async def dispatch_error_log(self, log_action: LogAction) -> bool:
        """Send error-level log as notification (if enabled).

        Args:
            log_action: LogAction with level="error"

        Returns:
            True if sent
        """
        if not self.notify_on_error_logs:
            return False
        if log_action.level != "error":
            return False
        if not self.default_target:
            return False

        message = f"🔴 **Hassaleh Error Log**\n\n{log_action.message}\n\n_Rule: {log_action.rule_id}_"

        try:
            result = await self.bridge.send_message(
                channel=self.default_channel,
                target=self.default_target,
                message=message,
                silent=True,  # Don't buzz for log-level errors
            )
            return result.get("ok", False)
        except Exception as e:
            log.error(f"Error log dispatch failed: {e}")
            return False

    async def dispatch_all(
        self,
        alerts: list[AlertAction],
        logs: list[LogAction] | None = None,
    ) -> int:
        """Dispatch all pending alerts and error logs.

        Returns:
            Number of successfully dispatched notifications
        """
        sent = 0

        for alert in alerts:
            if await self.dispatch_alert(alert):
                sent += 1

        if logs and self.notify_on_error_logs:
            for log_action in logs:
                if log_action.level == "error":
                    if await self.dispatch_error_log(log_action):
                        sent += 1

        return sent
