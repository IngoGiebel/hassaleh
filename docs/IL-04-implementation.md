# IL-04 implementation — Sanitize agent-visible errors so they do not leak host filesystem details

Date: 2026-04-19
Branch: `master`
Repo: `~/projects/hassaleh`
Severity: **MEDIUM** (Sprint 11 / Cycle 14)

## Problem

Per the security audit (`docs/security-audit-intent-lifecycle.md` § IL-04),
the `exec-ls` validation path raised `CapabilityParamError` carrying the
**post-`realpath`** canonical host path:

- `Path not in allowed scope: /real/resolved/path`
- `Path does not exist: /resolved/path`
- `Path is not a directory: /resolved/path`

`process_intent()` wrote those strings verbatim into `Intent.error`, and
`get_intent_result()` returned them to the owning agent. `execute_ls()`
likewise propagated raw `ls` stderr (which can contain OS usernames and
permission contexts) via `RuntimeError(result.stderr.strip())`.

This let an agent:

1. Map host filesystem layout under `/app` and `/data`.
2. Confirm symlink targets by reading the resolved canonical path.
3. Distinguish "out of scope" vs. "does not exist" vs. "not a directory"
   for arbitrary path probes.
4. Read `ls` stderr (usernames, permission strings) on execution failure.

A7-S5 acknowledged this; A9 deferred it. IL-04 closes it.

## Design

Two requirements drove the design:

1. **Single agent-visible vocabulary.** Three normalized strings so the
   agent cannot distinguish *why* validation failed — only *that* it did.
2. **Server-side debuggability.** Operators still need the canonical path,
   the raw stderr, and the failing intent ID to support agents reporting
   "my intent failed."

The reconciling primitive is a **correlation ID** (`uuid4().hex`) that
appears in both:

- the **server-side log line** (full detail, may contain canonical paths,
  stderr, usernames), and
- the **agent-visible message** (sanitized; only the cid identifies the
  specific failure).

Support flow: an agent reports `"Parameter validation failed [cid: 7af3…]"`
→ operator greps server logs for `7af3…` → sees the full detail.

### Correlation ID format

- **Generator:** `uuid.uuid4().hex` (32 hex chars, no dashes)
- **Suffix:** `[cid: <hex>]` appended to every sanitized message
- **Log binding:** the same `cid` appears as `[cid: %s]` in the log line

### Sanitized strings (the three the agent ever sees)

| Constant | Returned value (without cid suffix) |
|----------|-------------------------------------|
| `_MSG_PARAM_VALIDATION_FAILED` | `"Parameter validation failed"` |
| `_MSG_EXECUTION_FAILED` | `"Execution failed"` |
| `_MSG_EXECUTION_TIMED_OUT` | `"Execution timed out"` |

Final agent-visible form: `"<message> [cid: <32-hex>]"`.

### Two sanitization layers (defense in depth)

`exec_ls.py` and `intent_daemon.py` **both** sanitize. The daemon does not
trust that every `CapabilityParamError` / `RuntimeError` it catches has been
pre-sanitized by its source — a future capability handler (or a buggy
refactor) could leak a raw path. So:

- `exec_ls.py` sanitizes inside `_sanitize()` for any direct caller
  (e.g. unit tests of the capability module in isolation).
- `intent_daemon.py` re-sanitizes at the catch boundary in `process_intent`,
  generating a fresh cid that is bound to the `intent_id` in the log line.
  This is the cid the agent ultimately sees.

When the daemon catches an already-sanitized exec_ls exception, both cids
end up in the log: the daemon-side cid identifies the agent-facing message,
and the inner cid (still in the exception text) lets operators trace back
into the exec_ls log line if needed.

## Files touched

| File | Change |
|------|--------|
| `src/hassaleh/capabilities/exec_ls.py` | All `CapabilityParamError` / `RuntimeError` raises now go through `_sanitize(public_message, detail=…)` which logs the raw detail under a fresh cid and returns `"<public> [cid: <hex>]"`. Three module-level constants pin the public vocabulary. |
| `src/hassaleh/intent_daemon.py` | `process_intent` re-sanitizes at its own catch boundaries: a fresh `cid = uuid.uuid4().hex` for each of the validation, runtime-failure, and timeout paths; logs `Intent %s … [cid: %s]: %s`; writes only `"<msg> [cid: <hex>]"` to `Intent.error`. |
| `tests/test_il04_path_leak.py` | New: 5 tests covering out-of-scope path, nonexistent path, non-directory path, `execute_ls` runtime failure, and execute timeout. Each asserts (a) the raw path / stderr is **not** in the agent-visible error, and (b) the cid from the agent-visible error is also present in the captured logs. |
| `docs/IL-04-implementation.md` | This note. |

`src/hassaleh/intent_sdk.py` did **not** need changes: `get_intent_result()`
returns `Intent.error` verbatim, and the daemon now writes only sanitized
strings into that field.

## Before / after — example

**Setup:** an agent submits `exec-ls` with `path = "/etc/passwd"`.
`/etc/passwd` resolves outside the allowed bases (`/app`, `/data`).

### Before

```text
Intent.error  →  "Path not in allowed scope: /etc/passwd"
Agent sees    →  "Path not in allowed scope: /etc/passwd"
Server logs   →  (no cid — raw exception only)
```

The agent learns:
- `/etc/passwd` exists on the host (otherwise the error would have been
  `Path does not exist`).
- It is reachable as a real file (otherwise the error would have been
  `Path is not a directory`).
- It lives outside `/app` and `/data`.

### After

```text
Intent.error  →  "Parameter validation failed [cid: 8f4c2e…d3a1]"
Agent sees    →  "Parameter validation failed [cid: 8f4c2e…d3a1]"
Server logs   →
   ERROR hassaleh.intent_daemon: Intent <uuid> validation failed
       [cid: 8f4c2e…d3a1]: Parameter validation failed
       [cid: <inner-exec_ls-cid>]
   ERROR hassaleh.capabilities.exec_ls: exec_ls error
       [cid: <inner-exec_ls-cid>]: Path not in allowed scope: /etc/passwd
```

The agent now learns nothing beyond "validation failed."
The operator can still grep `8f4c2e…d3a1` to land on the daemon log line,
then follow the inner cid into `exec_ls` for the canonical path.

## Validation logic

**Unchanged.** Per the audit's explicit constraint, none of the validation
checks (`_VALID_PATH_RE`, `..` rejection, `realpath`, prefix check,
existence/`isdir` check) were touched. Only the *error surface* was
modified.

## Test results

```
$ pytest tests/test_il04_path_leak.py -x -v
tests/test_il04_path_leak.py::test_out_of_scope_path PASSED              [ 20%]
tests/test_il04_path_leak.py::test_nonexistent_path PASSED               [ 40%]
tests/test_il04_path_leak.py::test_non_directory_path PASSED             [ 60%]
tests/test_il04_path_leak.py::test_execute_ls_runtime_failure PASSED     [ 80%]
tests/test_il04_path_leak.py::test_execute_ls_timeout PASSED             [100%]

============================== 5 passed in 9.59s ===============================
```

Full log: `logs/sprint11-cycle14-il04.log`.

## Out of scope (explicit non-goals)

- **IL-05 TOCTOU between validate and execute** — separate finding,
  separate fix. IL-04 only addresses the *error surface*.
- **Reaper-recovered intents** still write the unsanitized
  `"Daemon lost — execution state unknown (recovered by reaper)"`
  message. That string contains no host-specific data so it is safe to
  expose, but if a future change adds host detail there it would need
  the same sanitization treatment.
- **Legacy `sdk.py` poll/wait paths** (IL-03) — already addressed in
  IL-03 fix; not touched here.
