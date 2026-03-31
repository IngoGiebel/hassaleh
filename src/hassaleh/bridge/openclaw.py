"""OpenClaw Gateway Bridge — HTTP client for the OpenClaw Gateway API.

Provides async methods for:
- Health checks (gateway status)
- Sending messages via channels (Telegram, etc.)
- Session management (list, spawn, send)
- System events (wake)

The OpenClaw Gateway runs on localhost and requires a bearer token
for authentication.

Reference: OpenClaw docs/gateway/protocol.md
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urljoin

import aiohttp

log = logging.getLogger("hassaleh.bridge.openclaw")


class OpenClawBridge:
    """Async HTTP client for OpenClaw Gateway.

    Usage:
        bridge = OpenClawBridge("http://localhost:18789", "your-token")
        async with bridge:
            health = await bridge.health()
            await bridge.send_message("telegram", "5580211068", "Hello!")
    """

    def __init__(self, gateway_url: str, gateway_token: str):
        self.gateway_url = gateway_url.rstrip("/")
        self.gateway_token = gateway_token
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> OpenClawBridge:
        self._session = aiohttp.ClientSession(
            headers={
                "Authorization": f"Bearer {self.gateway_token}",
                "Content-Type": "application/json",
            },
            timeout=aiohttp.ClientTimeout(total=30),
        )
        return self

    async def __aexit__(self, *exc) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    # ── Health ──

    async def health(self) -> dict[str, Any]:
        """Check OpenClaw Gateway health."""
        try:
            async with self._session.get(f"{self.gateway_url}/health") as resp:
                if resp.status == 200:
                    return await resp.json()
                return {"status": "error", "code": resp.status}
        except Exception as e:
            log.warning(f"OpenClaw health check failed: {e}")
            return {"status": "unreachable", "error": str(e)}

    async def is_healthy(self) -> bool:
        """Quick health check."""
        h = await self.health()
        return h.get("status") == "ok" or "version" in h

    # ── Messaging ──

    async def send_message(
        self,
        channel: str,
        target: str,
        message: str,
        silent: bool = False,
    ) -> dict[str, Any]:
        """Send a message via OpenClaw channel plugin.

        Args:
            channel: Channel name (telegram, discord, etc.)
            target: Target chat ID or username
            message: Message text
            silent: Send without notification sound

        Returns:
            Response dict with messageId, ok, etc.
        """
        payload = {
            "action": "send",
            "channel": channel,
            "target": target,
            "message": message,
        }
        if silent:
            payload["silent"] = True

        return await self._tool_call("message", payload)

    # ── Sessions ──

    async def list_sessions(
        self,
        kinds: list[str] | None = None,
        limit: int = 20,
        active_minutes: int | None = None,
    ) -> dict[str, Any]:
        """List active sessions."""
        payload: dict[str, Any] = {"limit": limit}
        if kinds:
            payload["kinds"] = kinds
        if active_minutes:
            payload["activeMinutes"] = active_minutes

        return await self._tool_call("sessions_list", payload)

    async def spawn_session(
        self,
        task: str,
        label: str | None = None,
        runtime: str = "subagent",
        model: str | None = None,
        timeout_seconds: int = 0,
    ) -> dict[str, Any]:
        """Spawn an isolated sub-agent session.

        Args:
            task: Task description for the sub-agent
            label: Optional label for logs/UI
            runtime: "subagent" or "acp"
            model: Optional model override
            timeout_seconds: Run timeout (0 = no timeout)

        Returns:
            Response with runId, childSessionKey, status
        """
        payload: dict[str, Any] = {
            "task": task,
            "runtime": runtime,
        }
        if label:
            payload["label"] = label
        if model:
            payload["model"] = model
        if timeout_seconds:
            payload["runTimeoutSeconds"] = timeout_seconds

        return await self._tool_call("sessions_spawn", payload)

    async def send_to_session(
        self,
        session_key: str,
        message: str,
        timeout_seconds: int = 30,
    ) -> dict[str, Any]:
        """Send a message to another session."""
        return await self._tool_call("sessions_send", {
            "sessionKey": session_key,
            "message": message,
            "timeoutSeconds": timeout_seconds,
        })

    # ── System Events ──

    async def wake(self, text: str, mode: str = "now") -> dict[str, Any]:
        """Send a wake/system event."""
        return await self._tool_call("cron", {
            "action": "wake",
            "text": text,
            "mode": mode,
        })

    # ── Internal ──

    async def _tool_call(self, tool: str, params: dict[str, Any]) -> dict[str, Any]:
        """Execute a tool call via the OpenClaw Gateway API.

        Uses the gateway's tool invocation endpoint.
        """
        url = f"{self.gateway_url}/api/tools/{tool}"

        try:
            async with self._session.post(url, json=params) as resp:
                result = await resp.json()
                if resp.status != 200:
                    log.warning(f"Tool {tool} returned {resp.status}: {result}")
                return result
        except aiohttp.ClientError as e:
            log.error(f"Tool call {tool} failed: {e}")
            return {"ok": False, "error": str(e)}
        except Exception as e:
            log.error(f"Tool call {tool} unexpected error: {e}")
            return {"ok": False, "error": str(e)}
