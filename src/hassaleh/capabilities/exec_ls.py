"""exec-ls capability — list directory contents securely.

Implements path validation per spec Section 3A and execution per Section 5.2.

Reference: docs/spec-mvp-test.md v1.1, Sections 3A, 5.2
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import uuid
from pathlib import PurePosixPath

from hassaleh.errors import CapabilityParamError

log = logging.getLogger(__name__)

# Allowed base directories for exec-ls (spec §3A)
EXEC_LS_ALLOWED_BASES: list[str] = ["/app", "/data"]

# Output limits (spec §3A)
OUTPUT_TRUNCATION_LIMIT = 64 * 1024  # 64 KB
MAX_DIR_ENTRIES = 10_000

# Path character validation regex (spec §3A step 1)
_VALID_PATH_RE = re.compile(r"^[a-zA-Z0-9/_.\-]+$")

# IL-04: agent-visible messages. Full detail is logged server-side with `cid`.
_MSG_PARAM_VALIDATION_FAILED = "Parameter validation failed"
_MSG_EXECUTION_FAILED = "Execution failed"
_MSG_EXECUTION_TIMED_OUT = "Execution timed out"


def _sanitize(public_message: str, *, detail: str) -> str:
    """Log full detail server-side and return a sanitized agent-visible message.

    The correlation ID (uuid4 hex) is embedded in both the log line and the
    returned message so operators can cross-reference a reported error with
    the server-side log. See docs/IL-04-implementation.md.
    """
    cid = uuid.uuid4().hex
    log.error("exec_ls error [cid: %s]: %s", cid, detail)
    return f"{public_message} [cid: {cid}]"


def validate_exec_ls_path_quick(path: str) -> None:
    """Quick validation for submit-time (steps 1-2 only, no filesystem access).

    Raises CapabilityParamError for character or traversal violations.

    IL-04: raised exceptions use a sanitized, agent-safe message. The raw
    input path is considered agent-supplied and therefore *not* sensitive to
    leak back, but we still log it server-side for forensic correlation.
    """
    # Step 1: Character validation
    if not _VALID_PATH_RE.match(path):
        raise CapabilityParamError(
            _sanitize(
                _MSG_PARAM_VALIDATION_FAILED,
                detail=f"Path contains invalid character: {path!r}",
            )
        )

    # Step 2: Reject raw '..' as a path segment (before any filesystem call)
    parts = PurePosixPath(path).parts
    if ".." in parts:
        raise CapabilityParamError(
            _sanitize(
                _MSG_PARAM_VALIDATION_FAILED,
                detail=f"Path traversal detected: {path!r}",
            )
        )


def validate_exec_ls_path(path: str) -> str:
    """Validate and canonicalize a path for exec-ls (full check).

    Returns the resolved (canonical) path on success.
    Raises CapabilityParamError on any validation failure.

    Validation sequence per spec §3A:
    1. Reject invalid characters
    2. Reject raw '..' traversal tokens
    3. Canonicalize with os.path.realpath
    4. Prefix check against allowed bases
    5. Existence + directory check

    IL-04: on failure, agent-visible message is normalized to
    'Parameter validation failed [cid: <hex>]'. The canonical resolved path
    is logged server-side keyed by the same cid.
    """
    # Steps 1-2: character + traversal
    validate_exec_ls_path_quick(path)

    # Step 3: Canonicalize (resolves symlinks and relative components)
    resolved = os.path.realpath(path)

    # Step 4: Prefix check against allowed bases
    if not any(
        resolved == base or resolved.startswith(base + "/")
        for base in EXEC_LS_ALLOWED_BASES
    ):
        raise CapabilityParamError(
            _sanitize(
                _MSG_PARAM_VALIDATION_FAILED,
                detail=f"Path not in allowed scope: {resolved}",
            )
        )

    # Step 5: Existence check — must be a directory
    if not os.path.exists(resolved):
        raise CapabilityParamError(
            _sanitize(
                _MSG_PARAM_VALIDATION_FAILED,
                detail=f"Path does not exist: {resolved}",
            )
        )
    if not os.path.isdir(resolved):
        raise CapabilityParamError(
            _sanitize(
                _MSG_PARAM_VALIDATION_FAILED,
                detail=f"Path is not a directory: {resolved}",
            )
        )

    return resolved


def execute_ls(resolved_path: str, timeout_sec: int = 30) -> str:
    """Execute ls -la on a validated path. Returns stdout.

    Uses list-form invocation (no shell injection). Truncates output at 64 KB.

    IL-04: on non-zero exit, the raw `ls` stderr (which may contain usernames,
    permission strings, and host paths) is logged server-side and the raised
    RuntimeError carries only 'Execution failed [cid: <hex>]'. Timeouts are
    re-raised unchanged (subprocess.TimeoutExpired) so the daemon can sanitize
    them in the async boundary where it has timeout-specific context.
    """
    # IL-05: Close the TOCTOU between validate_exec_ls_path() and execution.
    #
    # Threat model: validate_exec_ls_path() resolved the path with
    # os.path.realpath() and checked the prefix. Between that check and this
    # call, any component of `resolved_path` may have been swapped for a
    # symlink pointing outside the allowed bases.
    #
    # Two guards, applied atomically with respect to each other:
    #   1. O_NOFOLLOW rejects the open if the *final* component is now a
    #      symlink (kernel returns ELOOP).
    #   2. After open, readlink(/proc/self/fd/<N>) yields the kernel's
    #      canonical path for the inode the FD is bound to. If an
    #      *intermediate* component was swapped for a symlink during path
    #      resolution, the FD now references some other directory whose
    #      canonical path no longer matches resolved_path — we detect that
    #      here and refuse to execute. The FD itself is the source of truth
    #      for ls; we never pass the possibly-tampered pathname to ls.
    try:
        fd = os.open(resolved_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as e:
        detail = f"Failed to securely open directory: {e}"
        raise RuntimeError(_sanitize(_MSG_EXECUTION_FAILED, detail=detail))

    try:
        try:
            fd_target = os.readlink(f"/proc/self/fd/{fd}")
        except OSError as e:
            detail = f"Failed to verify fd canonical path: {e}"
            raise RuntimeError(_sanitize(_MSG_EXECUTION_FAILED, detail=detail))

        if fd_target != resolved_path:
            detail = (
                "Path changed between validation and execution "
                f"(expected {resolved_path!r}, fd resolves to {fd_target!r})"
            )
            raise RuntimeError(_sanitize(_MSG_EXECUTION_FAILED, detail=detail))

        result = subprocess.run(
            ["ls", "-la", "--", f"/proc/self/fd/{fd}/"],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            pass_fds=(fd,),
        )
    finally:
        os.close(fd)

    if result.returncode != 0:
        detail = result.stderr.strip() or f"ls exited with code {result.returncode}"
        raise RuntimeError(_sanitize(_MSG_EXECUTION_FAILED, detail=detail))

    stdout = result.stdout

    # Truncate at 64 KB if needed (spec §3A output limits)
    if len(stdout) > OUTPUT_TRUNCATION_LIMIT:
        stdout = stdout[:OUTPUT_TRUNCATION_LIMIT] + "\n[output truncated at 64KB]"

    return stdout
