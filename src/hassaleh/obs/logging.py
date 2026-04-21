from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import structlog
from structlog.types import EventDict, Processor

SERVICE_ENUM = {
    "hassaleh-daemon",
    "hassaleh-sdk",
    "hassaleh-intent-sdk",
    "hassaleh-heartbeat-sdk",
}

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_BEARER_RE = re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d() -]{7,}\d)(?!\w)")
_SECRET_KEY_RE = re.compile(r"(?:^|_)(?:api_?key(?:_hash|_lookup)?|lookup)(?:$|_)", re.IGNORECASE)
_SOURCE_IP_RE = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.\d{1,3}$")


@dataclass(frozen=True)
class ObsLoggingConfig:
    service_name: str
    env: str
    enabled: bool
    hostname: str
    version: str


class TextRenderer:
    def __call__(self, _: Any, __: str, event_dict: EventDict) -> str:
        ts = event_dict.get("ts", "")
        level = str(event_dict.get("level", "info")).upper()
        logger_name = event_dict.get("logger", "")
        msg = event_dict.get("msg", event_dict.get("event", ""))
        base = f"{ts} [{logger_name}] {level} {msg}".strip()
        extras = {
            k: v
            for k, v in event_dict.items()
            if k not in {"ts", "level", "logger", "msg", "event"}
        }
        return base if not extras else f"{base} {json.dumps(extras, sort_keys=True, default=str)}"


class PlainBoundLogger:
    def __init__(self, logger_name: str, *, bindings: Mapping[str, Any] | None = None):
        self._logger = logging.getLogger(logger_name)
        self._bindings = dict(bindings or {})

    def bind(self, **bindings: Any) -> "PlainBoundLogger":
        merged = dict(self._bindings)
        merged.update(bindings)
        return PlainBoundLogger(self._logger.name, bindings=merged)

    def _log(self, level: int, event: str, **kwargs: Any) -> None:
        merged = dict(self._bindings)
        merged.update(kwargs)
        message = event
        if merged:
            message = f"{event} {json.dumps(merged, sort_keys=True, default=str)}"
        self._logger.log(level, message)

    def debug(self, event: str, **kwargs: Any) -> None:
        self._log(logging.DEBUG, event, **kwargs)

    def info(self, event: str, **kwargs: Any) -> None:
        self._log(logging.INFO, event, **kwargs)

    def warning(self, event: str, **kwargs: Any) -> None:
        self._log(logging.WARNING, event, **kwargs)

    warn = warning

    def error(self, event: str, **kwargs: Any) -> None:
        self._log(logging.ERROR, event, **kwargs)

    def critical(self, event: str, **kwargs: Any) -> None:
        self._log(logging.CRITICAL, event, **kwargs)


def _package_version() -> str:
    pyproject = Path(__file__).resolve().parents[3] / "pyproject.toml"
    if not pyproject.exists():
        return "0.0.0"
    text = pyproject.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else "0.0.0"


def _normalize_level(level: str | None) -> int:
    if not level:
        return logging.INFO
    return getattr(logging, level.upper(), logging.INFO)


def _truncate_message_content(value: Any) -> str:
    text = str(value)
    if len(text) <= 40:
        return f"{text}…[{len(text)} chars]"
    return f"{text[:40]}…[{len(text)} chars]"


def _hash_only(value: Any) -> str:
    text = str(value)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return f"sha256:{digest}"


def _shape_only(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, str):
        return f"str({len(value)})"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, Mapping):
        return f"dict({len(value)})"
    if isinstance(value, (list, tuple, set)):
        return f"list({len(value)})"
    return type(value).__name__


def _redact_string(value: str) -> str:
    redacted = _EMAIL_RE.sub("[REDACTED_EMAIL]", value)
    redacted = _BEARER_RE.sub("Bearer [REDACTED_TOKEN]", redacted)
    redacted = _PHONE_RE.sub("[REDACTED_PHONE]", redacted)
    return redacted


def _redact_mapping(data: Mapping[str, Any], *, debug: bool = False) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in data.items():
        lower = key.lower()
        if lower == "cypher_params" and isinstance(value, Mapping):
            redacted_params: dict[str, Any] = {}
            for param, param_value in value.items():
                if _SECRET_KEY_RE.search(param.lower()):
                    redacted_params[param] = "[REDACTED_SECRET]"
                else:
                    redacted_params[param] = _shape_only(param_value)
            redacted[key] = redacted_params
            continue
        if _SECRET_KEY_RE.search(lower):
            redacted[key] = "[REDACTED_SECRET]"
            continue
        if lower in {"message_content"}:
            redacted[key] = _truncate_message_content(value)
            continue
        if lower == "intent_payload" and isinstance(value, Mapping):
            payload = dict(value)
            if "content" in payload:
                payload["content"] = _hash_only(payload["content"])
            redacted[key] = _redact_mapping(payload, debug=debug)
            continue
        if lower == "source_ip" and debug and isinstance(value, str):
            match = _SOURCE_IP_RE.match(value)
            if match:
                redacted[key] = f"{match.group(1)}.{match.group(2)}.{match.group(3)}.0/24"
                continue
        redacted[key] = _redact_value(value, debug=debug)
    return redacted


def _redact_value(value: Any, *, debug: bool = False) -> Any:
    if isinstance(value, str):
        return _redact_string(value)
    if isinstance(value, Mapping):
        return _redact_mapping(value, debug=debug)
    if isinstance(value, list):
        return [_redact_value(item, debug=debug) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item, debug=debug) for item in value)
    return value


def pii_redaction_processor(_: Any, method_name: str, event_dict: EventDict) -> EventDict:
    debug = str(event_dict.get("level", method_name)).lower() == "debug"
    return _redact_mapping(event_dict, debug=debug)


def rename_event_key(_: Any, __: str, event_dict: EventDict) -> EventDict:
    if "event" in event_dict and "msg" not in event_dict:
        event_dict["msg"] = event_dict.pop("event")
    return event_dict


def configure_logging(service_name: str, env: str | None = None) -> ObsLoggingConfig:
    env_name = (env or os.environ.get("HASSALEH_ENV") or "dev").lower()
    obs_mode = (os.environ.get("HASSALEH_OBS") or "off").lower()
    enabled = obs_mode != "off"
    hostname = socket.gethostname()
    version = _package_version()

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.setLevel(_normalize_level(os.environ.get("HASSALEH_LOG_LEVEL")))

    handler = logging.StreamHandler(sys.stderr)

    if not enabled:
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s [%(name)s] %(levelname)s %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
        root.addHandler(handler)
        if hasattr(structlog, "reset_defaults"):
            structlog.reset_defaults()
        return ObsLoggingConfig(
            service_name=service_name,
            env=env_name,
            enabled=False,
            hostname=hostname,
            version=version,
        )

    shared_processors: list[Processor] = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
        rename_event_key,
        pii_redaction_processor,
    ]

    renderer: Processor = (
        structlog.processors.JSONRenderer(sort_keys=True)
        if env_name == "prod"
        else TextRenderer()
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )
    handler.setFormatter(formatter)
    root.addHandler(handler)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        service=service_name,
        hostname=hostname,
        version=version,
        env=env_name,
        obs_mode=obs_mode,
    )

    return ObsLoggingConfig(
        service_name=service_name,
        env=env_name,
        enabled=enabled,
        hostname=hostname,
        version=version,
    )


def setup(service_name: str, env: str | None = None) -> ObsLoggingConfig:
    if service_name not in SERVICE_ENUM:
        raise ValueError(f"Unknown service for observability logging: {service_name}")
    return configure_logging(service_name, env)


def bind_logger(logger_name: str, **bindings: Any) -> Any:
    obs_mode = (os.environ.get("HASSALEH_OBS") or "off").lower()
    if obs_mode == "off":
        return PlainBoundLogger(logger_name).bind(**bindings)
    return structlog.get_logger(logger_name).bind(**bindings)


def get_logger(logger_name: str) -> Any:
    return bind_logger(logger_name)


def rebind_daemon_logger() -> Any:
    return bind_logger("hassaleh.daemon")
