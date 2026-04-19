# IL-05 — Security Review

- **Reviewer:** Inanna 🛡️
- **Date:** 2026-04-19
- **Target:** TOCTOU fix in `src/hassaleh/capabilities/exec_ls.py`
  (currently uncommitted on branch `trunk`)
- **Spec:** `docs/security-audit-intent-lifecycle.md` § IL-05
- **Impl note reviewed:** `docs/IL-05-implementation.md`
- **Test validation reviewed:** `docs/IL-05-test-validation.md`
- **Tests reviewed:** `tests/test_il05_toctou.py` (3/3 PASS, validated twice)
- **Position in bundle:** last security-review before the IL-04 / IL-05 / IL-06
  merge bundle.

This review is strictly scoped to the IL-05 diff. IL-04 and IL-06 are reviewed
separately in the bundle review document.

---

## 1. TOCTOU closure — both variants

### 1.1 Final-component swap

**Attack.** Between `validate_exec_ls_path()` returning `resolved_path` and
`execute_ls()` running, the attacker replaces the final component of
`resolved_path` with a symlink pointing outside `EXEC_LS_ALLOWED_BASES`.

**Closure.** `os.open(resolved_path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW)`.
`O_NOFOLLOW` instructs the kernel to return `ELOOP` if the *last* component of
the pathname is a symlink at the moment of resolution. The `OSError` is caught
and sanitized to `"Execution failed [cid: <hex>]"` — no leak of filenames or
host paths back to the agent.

**Verification.** `test_final_component_symlink_swap_rejected` performs
exactly this swap and asserts:

- `RuntimeError` is raised with the IL-04 sanitized shape.
- The secret filename (`SENSITIVE`) does not appear in the error message.

Variant closed. ✅

### 1.2 Intermediate-component swap

**Attack.** An *interior* segment of `resolved_path` (e.g., `middle/` in
`/app/middle/valid_dir`) is replaced with a symlink to an attacker-controlled
directory whose final segment happens to share the target name
(`attacker/valid_dir`). `O_NOFOLLOW` does **not** fire here: it only inspects
the final component during pathname resolution. The FD opens successfully,
but is bound to the attacker's inode.

**Closure.** After the open, `os.readlink(f"/proc/self/fd/{fd}")` yields the
kernel's canonical path for the inode the FD is bound to. If the intermediate
swap happened, the canonical path no longer matches `resolved_path`, and
`execute_ls()` refuses to proceed.

**Why this is sound.** The magic procfs symlink at `/proc/self/fd/<N>` is
maintained by the kernel and reflects the *current* canonical path of the
inode the FD names. An attacker who can only manipulate directory entries in
`/app` or `/data` (the threat model) cannot influence what `readlink` returns
for an FD already held by the daemon.

**Secondary invariant.** When `subprocess.run` executes
`ls -la -- /proc/self/fd/<N>/`, the trailing slash in the magic-symlink path
does *not* cause the kernel to re-parse the readlink string as a pathname.
Magic procfs symlinks use `nd_jump_link` to redirect the path walk directly
to the FD-bound dentry (see `fs/proc/fd.c`). So even if the attacker reverts
the filesystem layout between our readlink check and `ls`'s `opendir`, `ls`
still operates on the inode we validated. This eliminates any residual TOCTOU
between the check and the subprocess execution.

**Verification.** `test_intermediate_component_symlink_swap_rejected` performs
exactly this swap and asserts:

- `RuntimeError` with the IL-04 sanitized shape.
- Neither the attacker path nor the secret filename appears in the error
  message.

Variant closed. ✅

---

## 2. New attack surface

### 2.1 FD leaks on error paths — **no leak**

The FD lifetime is structured as:

```python
try:
    fd = os.open(...)          # may fail → no FD, nothing to close
except OSError:
    raise RuntimeError(...)     # sanitized

try:                            # FD is open from here on
    ...readlink + check + subprocess.run...
finally:
    os.close(fd)                # runs on every exit path
```

Every post-open failure — `readlink` OSError, canonical-path mismatch,
`subprocess.TimeoutExpired`, `subprocess` OSError (e.g., `ls` missing),
non-zero return code, or an uncaught exception — traverses the outer
`finally` and closes the FD. Only a `KeyboardInterrupt`/`SystemExit`
landing between `os.open` returning and the `try:` opening could in
theory leak an FD; that is a cpython interpreter-level concern and not a
new IL-05 surface.

### 2.2 `/proc` symlink race — **out of scope, acceptable**

The `readlink` check assumes standard procfs semantics. An attacker who can
remount `/proc`, alter `/proc/self/fd` semantics, or inject a fake procfs in
the daemon's mount namespace has already achieved enough privilege to bypass
any userspace TOCTOU mitigation. The impl note explicitly declares this
out-of-scope, which matches the threat model (low-privilege writers in
`/app` / `/data`).

### 2.3 Linux-only brittleness — **documented, appropriate**

`/proc/self/fd/<N>` is a Linux procfs feature. The daemon targets Linux
containers (see `Dockerfile`, `docker-compose.yml`). On non-Linux hosts,
`readlink("/proc/self/fd/<N>")` raises `ENOENT`, and `execute_ls()`
conservatively refuses execution with the sanitized error. That is a
**safe-fail** posture, not a security regression. The note in
`docs/IL-05-implementation.md` §Notes records this limitation.

### 2.4 Cross-mount / bind-mount edges — **no exploitable divergence**

Bind mounts do not cause `realpath()` and `readlink(/proc/self/fd/N)` to
diverge in the normal daemon runtime: both return the path within the mount
namespace the daemon executes in. If `/app` is a bind mount, `realpath("/app")`
stays `/app`, and the FD's canonical path reported by procfs remains `/app/…`.
No attacker-reachable state can split these for directories inside
`EXEC_LS_ALLOWED_BASES`.

One correctness nuance (not a security issue): if a validated directory is
*renamed* between validate and execute by legitimate administrative action,
`readlink` returns the new path, mismatch fires, execution is refused. This
is the correct conservative default — the renamed location may or may not
still be in scope, and refusing is safer than guessing.

### 2.5 DoS / resource surface — **flat**

Each call adds one `open`, one `readlink`, one `close`. Constant per-call
cost, no new allocations proportional to input, no new retry loops. Not a
DoS amplifier.

### 2.6 Information leakage via sanitized errors — **clean**

All new error sites (`open` failure, `readlink` failure, canonical-path
mismatch) route through `_sanitize(_MSG_EXECUTION_FAILED, detail=...)`.
Agent-visible text is fixed to `"Execution failed [cid: <hex>]"`. The
`resolved_path` and `fd_target` strings are server-side logged under the
same `cid`, which is the IL-04 design. No path, filename, or inode info
leaks to the agent.

---

## 3. Test coverage sufficiency

`tests/test_il05_toctou.py` provides three tests that correspond 1:1 to the
spec's required validation points:

| # | Test | Variant | Verdict |
|---|------|---------|---------|
| 1 | `test_final_component_symlink_swap_rejected` | Final-component swap | ✅ covers |
| 2 | `test_intermediate_component_symlink_swap_rejected` | Intermediate-component swap | ✅ covers |
| 3 | `test_no_swap_happy_path_still_works` | No swap, baseline | ✅ covers |

Test validation doc reports 3/3 PASS twice with no flakiness (0.08–0.20 s per
run), plus 29/29 PASS in the broader regression (`test_sdk.py`,
`test_chaos.py`).

**On the "`/app` / `/data` literal happy-path" gap flagged in the test
validation doc:** not a blocker. The `EXEC_LS_ALLOWED_BASES` mechanism is a
string-prefix check that is already exercised by the synthetic
`tmp_path/"app"` base in the fixture. The TOCTOU closure mechanism — the
FD-discipline + readlink check — is independent of the literal prefix
strings. Adding explicit `/app` and `/data` happy-path tests would be a
completeness enhancement but would not change the security claim.

Negative-case hardening coverage note (non-blocking): neither test explicitly
asserts that the sanitized error message is independent of the secret's
*existence* vs *non-existence* (e.g., CID is minted regardless). Behavior is
correct; assertion is implicit. Not worth holding the bundle on.

Coverage sufficient to ship. ✅

---

## 4. Auth / authz regressions vs trunk

The IL-05 diff is strictly confined to `execute_ls()`. No changes to:

- `validate_exec_ls_path()` or `validate_exec_ls_path_quick()`
- `EXEC_LS_ALLOWED_BASES`
- Any authentication or API-key derivation code
- Any ownership or capability-claim logic

Other uncommitted changes in the tree (`agent_dummy.py`, `heartbeat_sdk.py`)
are the IL-04 API-key-required agent flow and the IL-06 heartbeat
`previous_heartbeat` sequencing fix. These belong to their own tickets and
are reviewed separately in the bundle review. They are **not** IL-05
regressions and do not weaken auth.

No authn regression. No authz regression. ✅

---

## 5. Minor observations (non-blocking)

1. **Belt-and-suspenders hardening (future work):** after the `fd_target`
   equals `resolved_path` check, it would be cheap to additionally re-verify
   that `fd_target` still matches `EXEC_LS_ALLOWED_BASES`. Today this is
   implied by the string equality and the earlier validation, but an explicit
   post-open prefix recheck would harden against any future refactor that
   loosens either end. Nice-to-have, not required.
2. **`--` separator before the procfs path** is defensive; the path never
   starts with `-`, but the habit is correct. Keep.
3. **Readlink vs `/proc/self/fdinfo/`:** using `readlink` on `/proc/self/fd/<N>`
   is the standard idiom and is correct here. No change recommended.
4. **Rename-while-executing:** if a legitimate operator renames a
   `/app/**` directory at exactly the wrong moment, `execute_ls()` will
   refuse with a sanitized error. This is acceptable — conservative fail is
   the right default — but worth knowing if operators file flaky-ls reports.

None of these block the merge bundle.

---

## 6. Verdict

**Verdict: CLEAN**

Both TOCTOU variants are closed. The new surface (FD lifetime, procfs
symlink semantics, subprocess FD hand-off) has been audited and contains
no new exploitable defects within the stated threat model. Tests cover both
attack variants plus the happy path with zero flakiness across two
consecutive runs plus a broader regression. No auth or authz regressions
versus trunk.

IL-05 is cleared for inclusion in the IL-04 / IL-05 / IL-06 merge bundle.

— Inanna 🛡️
