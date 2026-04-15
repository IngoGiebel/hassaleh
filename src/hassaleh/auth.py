"""Agent authentication helpers — API key generation, hashing, verification.

Uses bcrypt for hashing. Keys are generated via secrets.token_urlsafe(32).

Reference: docs/spec-mvp-test.md v1.1, Section 3B
"""

from __future__ import annotations

import secrets

import bcrypt


def generate_api_key() -> str:
    """Generate a new API key (returned once at registration)."""
    return secrets.token_urlsafe(32)


def hash_api_key(key: str) -> str:
    """Hash an API key with bcrypt for storage."""
    return bcrypt.hashpw(key.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_api_key(key: str, hashed: str) -> bool:
    """Verify a plaintext API key against a bcrypt hash."""
    return bcrypt.checkpw(key.encode("utf-8"), hashed.encode("utf-8"))
