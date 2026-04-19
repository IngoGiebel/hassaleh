# IL-05 — TOCTOU fix between `validate_exec_ls_path()` and `execute_ls()`

**Severity:** MEDIUM
**Spec:** `docs/security-audit-intent-lifecycle.md` § IL-05
**Files touched:** `src/hassaleh/capabilities/exec_ls.py`, `tests/test_il05_toctou.py`

## Problem

`validate_exec_ls_path()` resolves the user-supplied path via `os.path.realpath()`,
checks the prefix against `EXEC_LS_ALLOWED_BASES` (`/app`, `/data`), and verifies
existence/type. The resolved path is then handed to `execute_ls()`, which used
to invoke `ls -la <resolved_path>`.

Between the validation and the `subprocess.run()` call, a path component can be
swapped on disk:

- **Final-component swap.** `resolved_path` itself is replaced with a symlink
  pointing outside the allowed bases. `ls -la` would follow it and list the
  link target's contents.
- **Intermediate-component swap.** Any interior segment of `resolved_path` is
  replaced with a symlink to an attacker-controlled directory whose final
  segment happens to share the same name.

Either variant lets a low-privilege writer in `/app` or `/data` escape the
allowed scope and read directories like `/etc` or `/root` via the daemon's
file descriptor.

## Fix

Implements **Option A** from the spec — atomically bind a file descriptor to
the validated directory and then operate on the FD, never on the pathname.

In `src/hassaleh/capabilities/exec_ls.py`, `execute_ls()` now does, in order:

1. `os.open(resolved_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)`.
   - `O_DIRECTORY` ensures the inode is a directory.
   - `O_NOFOLLOW` causes the open to fail with `ELOOP` if the **final**
     component is now a symlink — closes the final-component variant.
2. `os.readlink(f"/proc/self/fd/{fd}")` to get the kernel's canonical path for
   the inode the FD is bound to. If an **intermediate** component was swapped
   for a symlink during pathname resolution, the FD now references some other
   directory and its canonical path no longer matches `resolved_path`. The
   mismatch is detected here and execution is refused. This closes the
   intermediate-component variant that `O_NOFOLLOW` alone cannot catch.
3. `subprocess.run(["ls", "-la", "--", f"/proc/self/fd/{fd}/"], pass_fds=(fd,))`.
   The kernel-resident FD is passed to the child; `ls` walks the inode the FD
   already names. The possibly-tampered pathname is never re-resolved by `ls`.
4. `os.close(fd)` in a `finally` block to prevent FD leaks on any error path.

Failures at any step raise `RuntimeError(_sanitize(_MSG_EXECUTION_FAILED, ...))`,
preserving IL-04's agent-visible message shape (`"Execution failed [cid: <hex>]"`)
and ensuring no host paths or attacker-supplied filenames leak back to the
caller.

## Why both guards

`O_NOFOLLOW` only inspects the last component of the path argument. During
`open("/app/middle/valid_dir", O_NOFOLLOW)`, the kernel still follows symlinks
on `/app/middle` if `middle` itself is a symlink — `O_NOFOLLOW` would not
fire. The post-open `readlink(/proc/self/fd/<N>)` check is therefore necessary
to cover intermediate-component swaps.

## Tests

`tests/test_il05_toctou.py` exercises both threat variants and the happy path.
The race is simulated in the test body by performing the swap between the
`validate_exec_ls_path()` return and the `execute_ls()` call — the observable
invariant (resolved_path's canonical inode must not change before
execution) is identical to the live-race case.

| Test | Variant | Expected outcome |
|------|---------|------------------|
| `test_final_component_symlink_swap_rejected` | Last segment becomes a symlink to a sensitive directory | `os.open()` fails (`ELOOP`); sanitized `RuntimeError` with `"Execution failed [cid:"`; no leak of secret filename |
| `test_intermediate_component_symlink_swap_rejected` | Interior segment becomes a symlink to an attacker dir whose tail name matches | `O_NOFOLLOW` does not fire; the readlink check detects the canonical-path mismatch; sanitized `RuntimeError`; no leak of attacker path or secret filename |
| `test_no_swap_happy_path_still_works` | No swap | Normal `ls -la` output, including a marker file |

Run:

```sh
python -m pytest tests/test_il05_toctou.py -v
```

## Notes / Limitations

- Linux-specific: `/proc/self/fd/<N>` is a procfs feature. The code lives in
  the daemon, which the project already targets at Linux containers.
- The post-open readlink check is sufficient for the threat model above
  (single readable inode hand-off). It is not sufficient if an attacker can
  *also* mutate `/proc` mounts or the `/proc/self/fd` symlink semantics — out
  of scope for IL-05.
- `EXEC_LS_ALLOWED_BASES` continues to be the authoritative scope check at
  validation time; the FD discipline ensures execution operates on the
  inode that *passed* that scope check.
