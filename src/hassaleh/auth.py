"""Agent authentication helpers — API key generation, hashing, verification.

Uses bcrypt for hashing. Keys are generated via secrets.token_urlsafe(32).

Reference: docs/spec-mvp-test.md v1.1, Section 3B
"""

from __future__ import annotations

import hashlib
import secrets

import bcrypt


def generate_api_key() -> str:
    """Generate a new API key (returned once at registration)."""
    return secrets.token_urlsafe(32)


def hash_api_key(key: str) -> str:
    """Hash an API key with bcrypt for storage (verification)."""
    return bcrypt.hashpw(key.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def lookup_hash(key: str) -> str:
    """Deterministic SHA-256 hash of an API key for Cypher-level lookup.

    Bcrypt is non-deterministic (random salt), so it can't be used in a
    WHERE clause. This SHA-256 digest enables O(1) agent lookup by key;
    bcrypt then verifies the key against the stored hash.
    """
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def verify_api_key(key: str, hashed: str) -> bool:
    """Verify a plaintext API key against a bcrypt hash."""
    return bcrypt.checkpw(key.encode("utf-8"), hashed.encode("utf-8"))
