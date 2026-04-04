"""Skill domain taxonomy helpers."""

from __future__ import annotations

from collections.abc import Iterable


def normalize_domain(value: str | None) -> str:
    """Return a normalized dot-notation domain string."""
    if value is None:
        return ""
    return str(value).strip().strip(".")


def domain_matches_prefix(domain: str | None, prefix: str | None) -> bool:
    """Return True when domain equals the prefix or is nested under it."""
    normalized_domain = normalize_domain(domain)
    normalized_prefix = normalize_domain(prefix)
    if not normalized_prefix:
        return True
    if not normalized_domain:
        return False
    return (
        normalized_domain == normalized_prefix
        or normalized_domain.startswith(f"{normalized_prefix}.")
    )


def domain_matches_any(
    domain: str | None,
    prefixes: Iterable[str] | None,
    *,
    allow_unscoped: bool = False,
) -> bool:
    """Return True if domain matches at least one allowed prefix."""
    normalized_domain = normalize_domain(domain)
    normalized_prefixes = [
        normalize_domain(prefix)
        for prefix in (prefixes or [])
        if normalize_domain(prefix)
    ]
    if not normalized_prefixes:
        return True
    if not normalized_domain:
        return allow_unscoped
    return any(domain_matches_prefix(normalized_domain, prefix) for prefix in normalized_prefixes)


def domain_hierarchy(domain: str | None) -> list[str]:
    """Expand a dot-notation domain into cumulative hierarchy parts."""
    normalized_domain = normalize_domain(domain)
    if not normalized_domain:
        return []

    parts = normalized_domain.split(".")
    hierarchy: list[str] = []
    for index in range(1, len(parts) + 1):
        hierarchy.append(".".join(parts[:index]))
    return hierarchy


def format_domain_hierarchy(domain: str | None) -> str:
    """Format a domain hierarchy for human-readable CLI output."""
    hierarchy = domain_hierarchy(domain)
    if not hierarchy:
        return "unscoped"
    return " > ".join(part.split(".")[-1] for part in hierarchy)
