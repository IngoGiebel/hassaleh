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


# ── Heartbeat errors (spec-heartbeat.md v1.1) ──


class AgentNotFoundError(Exception):
    """Raised when the authenticated agent_id does not match any Agent node."""


class AgentDisabledError(Exception):
    """Raised when a heartbeat targets an agent in the 'disabled' lifecycle state."""


class HeartbeatTokenMismatchError(Exception):
    """Raised when the provided heartbeat_token does not match the stored token."""
