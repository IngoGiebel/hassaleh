"""Hassaleh custom exceptions for the MVP Intent Pipeline.

Reference: docs/spec-mvp-test.md v1.1, Section 4.1
"""


class AuthenticationError(Exception):
    """Raised when API key authentication fails (invalid or unknown key)."""


class CapabilityNotFoundError(Exception):
    """Raised when the requested capability_id does not exist in the graph."""


class CapabilityDeniedError(Exception):
    """Raised when the authenticated agent lacks the requested capability."""


class CapabilityParamError(Exception):
    """Raised when capability parameter validation fails (e.g., path traversal)."""


class AccessDeniedError(Exception):
    """Raised when a caller tries to access an Intent they don't own."""
