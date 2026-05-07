"""Capability helpers for Sprint-13/Sprint-14 Intent dispatch.

The runtime capability model is intentionally small in v1: API keys carry a
list of exact scope strings, and handlers declare one required scope at
registration time. There are no prefix wildcards, glob patterns, or ``any_of``
semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .types import Principal

ALLOWED_SCOPES: tuple[str, ...] = (
    "market.analyst.write",
    "market.reviewer.write",
    "market.writer.write",
    "market.publisher.write",
)
_ALLOWED_SCOPE_SET = frozenset(ALLOWED_SCOPES)


class HasScopes(Protocol):
    scopes: list[str] | tuple[str, ...]


@dataclass(frozen=True)
class ApiKey:
    """Authenticated API key projection used by the runtime boundary.

    Neo4j stores ``ApiKey.scopes`` as a list property; this dataclass mirrors
    that schema field for callers/tests that do not need a live database row.
    """

    id: str
    scopes: list[str]


def check_scope(api_key: HasScopes, required_scope: str) -> bool:
    """Return True only for exact grants of a known capability scope.

    Unknown required scopes are denied even if present on the API key. This
    keeps the capability surface closed to Sprint-13/Sprint-14's declared set
    and prevents accidental prefix/wildcard semantics such as
    ``market.*.write`` or ``market``.
    """

    if required_scope not in _ALLOWED_SCOPE_SET:
        return False
    return required_scope in api_key.scopes


def capability_api_key(
    *scopes: str, id: str = "test-api-key"
) -> ApiKey:
    """Small test fixture factory shared by Track B/F tests."""

    return ApiKey(id=id, scopes=list(scopes))


def capability_principal(
    *scopes: str, id: str = "test-principal"
) -> Principal:
    """Principal fixture factory matching the runtime ``Ctx`` surface."""

    return Principal(id=id, scopes=tuple(scopes))


__all__ = [
    "ALLOWED_SCOPES",
    "ApiKey",
    "capability_api_key",
    "capability_principal",
    "check_scope",
]
