"""Regression tests for IL-05 — TOCTOU between validate_exec_ls_path() and execute_ls().

These tests simulate the race condition by performing the filesystem swap in the
*test body* between validation and execution. In a real attack the swap would
be racing a separately-running daemon, but the observable invariant is the same:
once validate_exec_ls_path() has returned a resolved_path, execute_ls() must not
operate on anything whose canonical path differs from that resolved_path.

Covers two threat variants:
  1. Final-component swap: the last segment of resolved_path becomes a symlink.
     Caught by O_NOFOLLOW (open fails with ELOOP).
  2. Intermediate-component swap: an interior segment of resolved_path becomes
     a symlink. O_NOFOLLOW does NOT reject this (it only guards the last
     component), so the fix also verifies readlink(/proc/self/fd/<N>) against
     resolved_path after opening.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import hassaleh.capabilities.exec_ls as els
from hassaleh.capabilities.exec_ls import execute_ls, validate_exec_ls_path


@pytest.fixture
def allowed_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create an allowed base under tmp_path and scope EXEC_LS_ALLOWED_BASES to it.

    tmp_path is resolved first so that validate_exec_ls_path()'s realpath()
    call cannot pick up any pytest-tmpdir symlink indirection (e.g.
    /tmp → /private/tmp on some systems) and desync the prefix check.
    """
    base = (tmp_path / "app").resolve()
    base.mkdir()
    monkeypatch.setattr(els, "EXEC_LS_ALLOWED_BASES", [str(base)])
    return base


def test_final_component_symlink_swap_rejected(allowed_base: Path, tmp_path: Path) -> None:
    """Last path component becomes a symlink to an out-of-scope directory.

    Validation succeeds on the real directory; between validate and execute the
    directory is removed and replaced with a symlink to `/etc`-equivalent.
    O_NOFOLLOW must cause os.open() to fail, which execute_ls() wraps into a
    sanitized RuntimeError.
    """
    target = allowed_base / "valid_dir"
    target.mkdir()

    secret = (tmp_path / "secret").resolve()
    secret.mkdir()
    (secret / "SENSITIVE").write_text("SENSITIVE_CONTENT")

    resolved = validate_exec_ls_path(str(target))
    assert resolved == str(target)

    # Swap: last component is now a symlink pointing outside allowed_base.
    target.rmdir()
    target.symlink_to(secret)

    with pytest.raises(RuntimeError) as exc:
        execute_ls(resolved, timeout_sec=5)

    # Must be the sanitized surface (IL-04 message shape), not raw OSError.
    assert "Execution failed [cid:" in str(exc.value)
    # And must not leak the secret filename either.
    assert "SENSITIVE" not in str(exc.value)


def test_intermediate_component_symlink_swap_rejected(
    allowed_base: Path, tmp_path: Path
) -> None:
    """An *interior* component becomes a symlink to an out-of-scope directory.

    This is the case that plain O_NOFOLLOW would silently miss, because
    O_NOFOLLOW only rejects a symlink at the *final* component of the path.
    The post-open readlink(/proc/self/fd/<N>) check catches it: the FD is now
    bound to a directory whose kernel-reported canonical path no longer
    matches the validated resolved_path.
    """
    middle = allowed_base / "middle"
    middle.mkdir()
    target = middle / "valid_dir"
    target.mkdir()

    # A decoy directory outside allowed_base that also has a `valid_dir/`
    # under it — so if the attack succeeded, os.open() would succeed on a
    # non-symlink final component and we'd be listing the wrong directory.
    attacker_root = (tmp_path / "attacker").resolve()
    attacker_root.mkdir()
    (attacker_root / "valid_dir").mkdir()
    (attacker_root / "valid_dir" / "SENSITIVE").write_text("SENSITIVE_CONTENT")

    resolved = validate_exec_ls_path(str(target))
    assert resolved == str(target)

    # Swap: intermediate component (`middle`) is replaced with a symlink
    # pointing outside allowed_base. The final component `valid_dir` still
    # exists as a real directory on the other end of that symlink, so
    # O_NOFOLLOW on its own cannot detect the attack.
    import shutil

    shutil.rmtree(middle)
    middle.symlink_to(attacker_root)

    with pytest.raises(RuntimeError) as exc:
        execute_ls(resolved, timeout_sec=5)

    msg = str(exc.value)
    assert "Execution failed [cid:" in msg
    # Do not leak the attacker path or the secret filename to the caller.
    assert "SENSITIVE" not in msg
    assert str(attacker_root) not in msg


def test_no_swap_happy_path_still_works(allowed_base: Path) -> None:
    """Sanity check: without any swap, execute_ls() lists the directory."""
    target = allowed_base / "valid_dir"
    target.mkdir()
    (target / "marker.txt").write_text("hello")

    resolved = validate_exec_ls_path(str(target))
    output = execute_ls(resolved, timeout_sec=5)

    assert "marker.txt" in output
