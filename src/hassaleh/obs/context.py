from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Iterator
from typing import Any

_REQUEST_CONTEXT: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "hassaleh_obs_request_context",
    default={},
)


def _normalize_context_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


@contextlib.contextmanager
def bind_request_context(**values: Any) -> Iterator[dict[str, str]]:
    """Bind request/intent identifiers for the current async context."""
    current = dict(_REQUEST_CONTEXT.get())
    for key, value in values.items():
        normalized = _normalize_context_value(value)
        if normalized is not None:
            current[key] = normalized

    token = _REQUEST_CONTEXT.set(current)
    try:
        yield current
    finally:
        _REQUEST_CONTEXT.reset(token)


def clear_request_context() -> None:
    _REQUEST_CONTEXT.set({})


def get_request_context() -> dict[str, str]:
    return dict(_REQUEST_CONTEXT.get())


def get_obs_context() -> dict[str, str]:
    return get_request_context()


@contextlib.contextmanager
def obs_context(**values: Any) -> Iterator[dict[str, str]]:
    with bind_request_context(**values) as current:
        yield current


def get_request_value(key: str, default: str | None = None) -> str | None:
    return _REQUEST_CONTEXT.get().get(key, default)


def current_agent_id(default: str | None = None) -> str | None:
    return get_request_value("agent_id", default)


def current_intent_id(default: str | None = None) -> str | None:
    return get_request_value("intent_id", default)
