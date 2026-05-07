"""Sprint-13 Intent runtime (Track A skeleton).

Public surface:

    from hassaleh.runtime import (
        Intent, Result, Principal, Ctx,
        HassalehRuntime, register_handler,
    )

See docs/sprint-13-plan.md §2 for architecture.
"""

from .capabilities import ALLOWED_SCOPES, ApiKey, check_scope
from .core import HassalehRuntime, register_handler
from .types import Ctx, Intent, Principal, Result, ResultKind

__all__ = [
    "ALLOWED_SCOPES",
    "ApiKey",
    "Ctx",
    "HassalehRuntime",
    "Intent",
    "Principal",
    "Result",
    "ResultKind",
    "check_scope",
    "register_handler",
]
