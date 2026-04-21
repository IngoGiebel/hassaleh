from __future__ import annotations

import json

from hassaleh.obs import logging as obs_logging
from hassaleh.obs.logging import get_logger, setup


def test_json_output_schema(monkeypatch, capsys):
    monkeypatch.setenv("HASSALEH_OBS", "on")
    monkeypatch.setenv("HASSALEH_ENV", "prod")
    monkeypatch.setenv("HASSALEH_LOG_LEVEL", "INFO")

    config = setup("hassaleh-daemon", "prod")
    logger = get_logger("hassaleh.daemon.intent")
    logger.info(
        "intent submitted",
        agent_id="agent-a1",
        intent_id="intent-42",
        result="accepted",
        extra={"action": "submit", "target": "agent-b2"},
    )

    captured = capsys.readouterr().err.strip()
    payload = json.loads(captured)

    assert config.enabled is True
    assert payload["msg"] == "intent submitted"
    assert payload["level"] == "info"
    assert payload["logger"] == "hassaleh.daemon.intent"
    assert payload["service"] == "hassaleh-daemon"
    assert payload["env"] == "prod"
    assert payload["version"]
    assert payload["hostname"]
    assert payload["agent_id"] == "agent-a1"
    assert payload["intent_id"] == "intent-42"
    assert payload["result"] == "accepted"
    assert payload["extra"] == {"action": "submit", "target": "agent-b2"}
    assert "ts" in payload


def test_pii_redaction(monkeypatch, capsys):
    monkeypatch.setenv("HASSALEH_OBS", "on")
    monkeypatch.setenv("HASSALEH_ENV", "prod")

    setup("hassaleh-daemon", "prod")
    logger = get_logger("hassaleh.daemon.audit")
    logger.warning(
        "payload check",
        contact="alice@example.com",
        auth_header="Bearer secret-token-123",
        phone="+49 30 1234 5678",
        api_key="abc123",
        api_key_hash="def456",
        api_key_lookup="ghi789",
        lookup="jkl000",
        cypher_params={"lookup": "secret", "api_key_lookup": "hashed-secret", "agent_id": "agent-007", "count": 3},
        message_content="x" * 64,
        intent_payload={"content": "top secret body", "other": "alice@example.com"},
    )

    payload = json.loads(capsys.readouterr().err.strip())
    assert payload["contact"] == "[REDACTED_EMAIL]"
    assert payload["auth_header"] == "Bearer [REDACTED_TOKEN]"
    assert payload["phone"] == "[REDACTED_PHONE]"
    assert payload["api_key"] == "[REDACTED_SECRET]"
    assert payload["api_key_hash"] == "[REDACTED_SECRET]"
    assert payload["api_key_lookup"] == "[REDACTED_SECRET]"
    assert payload["lookup"] == "[REDACTED_SECRET]"
    assert payload["cypher_params"] == {
        "lookup": "[REDACTED_SECRET]",
        "api_key_lookup": "[REDACTED_SECRET]",
        "agent_id": "str(9)",
        "count": "int",
    }
    assert payload["message_content"].endswith("…[64 chars]")
    assert payload["intent_payload"]["content"].startswith("sha256:")
    assert payload["intent_payload"]["other"] == "[REDACTED_EMAIL]"


def test_obs_off_uses_text_fallback(monkeypatch, capsys):
    monkeypatch.setenv("HASSALEH_OBS", "off")
    monkeypatch.setenv("HASSALEH_ENV", "dev")

    calls = {"count": 0}
    original = obs_logging.pii_redaction_processor

    def wrapped(*args, **kwargs):
        calls["count"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(obs_logging, "pii_redaction_processor", wrapped)

    config = setup("hassaleh-daemon", "dev")
    logger = get_logger("hassaleh.daemon")
    logger.info("plain startup", source_ip="192.168.1.44")

    captured = capsys.readouterr().err.strip()
    assert config.enabled is False
    assert calls["count"] == 0
    assert captured
    assert not captured.startswith("{")
    assert "plain startup" in captured
    assert "192.168.1.44" in captured
