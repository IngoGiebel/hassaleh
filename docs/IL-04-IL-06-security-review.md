# Security Review: IL-04 & IL-06

**Date:** 2026-04-19
**Reviewer:** Inanna
**Scope:** `c57dde3` (IL-04 implementation) and IL-06 local implementation

## 1. IL-04 — `exec-ls` validation and daemon error paths leak host filesystem details

**Finding Summary:** The system previously leaked post-`realpath` host paths and raw `ls` stderr back to agents, allowing them to map host filesystems, confirm symlinks, and probe for path existence.

### Review

1. **Closes the Documented Attack:**
   - Yes. The `_sanitize` function normalizes all `exec-ls` validation errors into generic strings (`"Parameter validation failed"`, `"Execution failed"`, `"Execution timed out"`) paired with a random correlation ID (`cid`).
   - The daemon's `process_intent()` provides a defense-in-depth layer, catching exceptions and regenerating the `cid` to ensure any accidentally leaked data from the capability handler is scrubbed before being written to `Intent.error`.
   - Operators can still debug issues by matching the agent-provided `cid` with the full details logged server-side.

2. **No New Attack Surface Introduced:**
   - **Info Leak:** The correlation ID uses `uuid.uuid4().hex`, which relies on os.urandom. It is statistically unpredictable and does not leak state, timing, or path information.
   - **Timing Side-Channels:** While small timing differences still exist between different validation failures (e.g., regex failing instantly vs `os.path.realpath` accessing the filesystem), they are standard I/O variations and do not constitute a critical leak in this context. The core data leak is successfully closed.

3. **Adequate Test Coverage:**
   - Excellent coverage in `tests/test_il04_path_leak.py`.
   - The tests handle adversarial cases explicitly: probing out-of-scope paths, non-existent directories, files (instead of directories), simulated `ls` permission errors, and timeouts.
   - They assert that the returned `error` strictly contains the public message and the `cid`, and that the sensitive substrings (`/tmp`, `does-not-exist`, `Permission denied`) are absent.

4. **No Auth/Authz Regressions:**
   - The changes are localized to error handling and do not affect the capability claim process (`claim_intent`), the lifecycle state machine, or execution scopes. `EXEC_LS_ALLOWED_BASES` enforcement is fully intact.

**Verdict:** **CLEAN**

---

## 2. IL-06 — Heartbeat `previous_heartbeat` sequencing is ambiguous

**Finding Summary:** The heartbeat Cypher query relied on implicit `SET` order (`SET a.last_heartbeat = datetime(), a.previous_heartbeat = a.last_heartbeat`) to preserve the prior heartbeat timestamp, making the sequencing hard to read and dependent on undocumented/unstable Neo4j snapshot behavior.

### Review

1. **Closes the Documented Attack:**
   - Yes. The addition of `WITH a, a.last_heartbeat AS old_last` explicitly binds the pre-write timestamp to a temporary variable. The query then writes `a.previous_heartbeat = old_last`, making the intention and execution order mathematically sound and obvious to reviewers.

2. **No New Attack Surface Introduced:**
   - **Cypher Injection:** No string formatting was added. All inputs (`$agent_id`, `$new_token`, `$source`) remain parameterized.
   - **Race Conditions:** The explicit `WITH` binding and subsequent `SET` occur within a single implicit transaction. The query logic maintains the `duration('PT60S')` rate-limiting clause correctly.

3. **Adequate Test Coverage:**
   - `tests/test_il06_heartbeat_sequencing.py` validates the two critical paths:
     1. The first heartbeat (verifying `previous_heartbeat` correctly remains `null`).
     2. Subsequent heartbeats (verifying `previous_heartbeat` matches the exact `last_heartbeat` of the prior call).
   - The use of `asyncio.sleep(62.5)` to naturally clear the server-side rate limit ensures the test validates the exact production query logic instead of mocking it.

4. **No Auth/Authz Regressions:**
   - Pre-shared API key validation, token-chain replay protection (`heartbeat_token` matching), and `AgentDisabledError` enforcement remain completely unaffected.

**Verdict:** **CLEAN**

---

## Overall Recommendation

**MERGE BOTH.**

Both IL-04 and IL-06 are thoroughly implemented. The architectural decisions (using a `cid` for IL-04; using explicit `WITH` assignments for IL-06) cleanly solve the security and auditability concerns without introducing regressions. No new vulnerabilities were found during this review.