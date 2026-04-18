# Security Audit — Intent Lifecycle

**Reviewer:** Inanna 🛡️
**Date:** 2026-04-17
**Project:** Hassaleh
**Scope:**
- `src/hassaleh/intent_daemon.py`, `src/hassaleh/intent_sdk.py`
- `src/hassaleh/capabilities/`, `src/hassaleh/auth.py`
- `src/hassaleh/daemon.py`, `src/hassaleh/errors.py`
- `src/hassaleh/heartbeat_sdk.py`, `src/hassaleh/sdk.py`

**Prior context reviewed:**
- `docs/review-a7-security-code-review.md`
- A9 fixes in commit `b0b4c08` (TOCTOU, dead-code removal, auth scope hardening)
- B6 heartbeat approved with no outstanding security issues
- `docs/security-audit-intent-lifecycle-codex.md` (peer audit — consulted but not copied)

---

## 1. Executive Summary

The MVP intent pipeline in `intent_sdk.py` + `intent_daemon.py` is materially stronger than the A7 baseline: the transition TOCTOU is closed, `claimed_by` hijack is prevented, input validation is enforced, and the execution path goes through a single capability module.

However, a deep audit across the full requested scope — **including the legacy `sdk.py`** — surfaces one **critical** issue that undermines the hardened path entirely: `sdk.py` still exposes an intent-submission and polling API that accepts a caller-controlled `agent_id` with no API-key authentication and no ownership check on reads. This splits the intent lifecycle into two parallel surfaces, one hardened and one not.

The Intent SDK also retains the O(N) bcrypt scan from A7-S2; this was *scoped* in A9 but not *restructured*, while the heartbeat path already uses the correct lookup-hash pattern.

### Severity counts

- **CRITICAL:** 1
- **HIGH:** 2
- **MEDIUM:** 4
- **LOW:** 4
- **INFO:** 1

### Verdict

**PASS_WITH_NOTES** — conditional on treating the legacy `sdk.py` surface as internal-only *and* documenting that constraint. If `sdk.py` is reachable from anything less trusted than the daemon itself, the verdict drops to **FAIL** until IL-01 is fixed.

---

## 2. Threat Model for the Intent Lifecycle

**Assets:** capability authorization (`Agent-[HAS_CAPABILITY]->Capability`); intent state integrity across `pending → claimed → running → {success, failed, rejected}`; daemon execution authority (graph entries become `sudo -u exec_user` subprocesses); agent credentials (API keys, bcrypt hashes, heartbeat token chain); host metadata via errors/output/health; per-intent stdout/stderr.

**Trust boundaries:**
1. Agent → Intent SDK — API key required; identity derived server-side.
2. Neo4j → Intent Daemon — graph state is the queue and authorization source.
3. Intent Daemon → OS — most sensitive; capability invocation via `sudo`.
4. Agent → Heartbeat SDK — separate auth with chained replay token.
5. Operators → health/logs/errors — information-leak boundary.

**Attackers considered:**
- Compromised or buggy agent with a valid API key (privilege escalation, cross-agent reads, DoS).
- Unauthenticated caller with bolt-level reach into SDK modules (relevant to `sdk.py`).
- Concurrent daemon or reaper racing claim/transition flows.
- Malicious graph writer altering `Capability.invoke_command` / `exec_as_user`.
- Information-gathering actor mapping host filesystem via error strings and results.

**Security goals:** only authenticated agents may submit/read their own intents; only authorized capabilities are targetable; only one daemon legitimately drives execution; terminal states reflect what actually ran; OS execution is constrained to approved capabilities; errors do not broadcast internal host state.

---

## 3. Findings

### IL-01 — Legacy `HassalehSDK.submit_intent()` accepts caller-supplied `agent_id` with no authentication
**Severity:** CRITICAL
**Location:** `src/hassaleh/sdk.py:188-269`

`HassalehSDK.submit_intent(agent_id, action, ...)` creates an `Intent` node and an `(:Agent)-[:PROPOSED]->(:Intent)` edge keyed *entirely on caller-provided `agent_id`*. There is no API-key verification, no ownership proof, and no capability check at submission time — capability enforcement happens later in `HassalehDaemon._check_capability()`, which uses the PROPOSED edge as authority.

This directly contradicts the hardened model in `intent_sdk.py`, where `agent_id` is derived server-side from the API key and capability linkage is verified before write.

**Impact:**
- Any caller on this SDK surface can forge intents on behalf of *any* agent node in the graph.
- Submitted intents then inherit the target agent's full capability set: `_check_capability()` matches `Agent -[HAS_CAPABILITY]-> Capability` using the *forged* agent_id.
- The daemon will execute these intents via `sudo -u cap.exec_as_user cap.invoke_command`. That is privileged OS execution driven by an unauthenticated graph write.

**Why this matters for the lifecycle verdict:**
The intent lifecycle cannot be considered safe while two entry points disagree on authentication. `intent_sdk.py`'s hardening is effectively undone if `sdk.py` remains callable from the same trust zone.

**Recommendation:**
- Either (a) remove intent submission from `sdk.py` entirely and route all agent writes through `IntentSDK`, or (b) require the same API-key-based authentication in `HassalehSDK.submit_intent()` and derive `agent_id` server-side.
- In the meantime, document `HassalehSDK` as **daemon-internal** and enforce that technically (unix socket, local-only binding, or a separate privileged user).
- Add a schema-level invariant: intents created without a `SUBMITTED_BY` edge whose agent matches an authenticated identity are rejected by the daemon.

---

### IL-02 — `IntentSDK._authenticate()` performs O(N) bcrypt scan across active agents
**Severity:** HIGH
**Location:** `src/hassaleh/intent_sdk.py:85-104`, `src/hassaleh/auth.py:26-33`

A9 scoped the authentication query to `lifecycle IN ['active', 'running']` (fixing A7-S3), but left the underlying loop unchanged: the SDK pulls every active agent's bcrypt hash and iterates them with `bcrypt.checkpw` until a match.

- **Timing side channel:** bcrypt is deliberately slow (~100 ms). With N active agents, invalid keys cost N × 100 ms, and valid keys leak their position in the result set.
- **CPU DoS:** a hostile caller sending invalid keys forces O(N) bcrypt work per request — trivially saturating CPU at scale.
- **Inconsistency:** `HeartbeatSDK.heartbeat()` already implements the correct pattern using `lookup_hash()` for O(1) candidate resolution followed by a single bcrypt verification.

**Recommendation:**
- Move `IntentSDK._authenticate()` to the `HeartbeatSDK` pattern: store `api_key_lookup = sha256(key)` on each `Agent`, query by `api_key_lookup`, then verify one bcrypt hash.
- Backfill `api_key_lookup` for existing agents as part of deployment.
- Add rate limiting or exponential backoff on authentication failures if the SDK is ever externally reachable.

---

### IL-03 — `HassalehSDK.poll_intent()` / `wait_for_intent()` return results to any caller
**Severity:** HIGH
**Location:** `src/hassaleh/sdk.py:273-323`

Both methods go through `HassalehSDK.query()`, which blocks write keywords but does not authenticate the caller and does not verify ownership of the returned intent. Any caller holding (or guessing, though UUIDs are not guessable) an intent ID can read lifecycle, `stdout`, `stderr`, `error_reason`, and `exit_code`.

This is not merely symmetric to IL-01: it independently exposes **execution results** — which may contain host paths, command output, and internal errors — without an auth gate.

**Recommendation:**
- Remove polling from `sdk.py`, or add API-key authentication + ownership verification equivalent to `IntentSDK._get_owned_intent()`.
- Treat intent IDs as non-secret identifiers; never rely on ID unguessability as an authorization boundary.

---

### IL-04 — `exec-ls` validation and daemon error paths leak host filesystem details
**Severity:** MEDIUM
**Location:** `src/hassaleh/capabilities/exec_ls.py:67-73`, `src/hassaleh/intent_daemon.py:262,280`, `src/hassaleh/intent_sdk.py:242-254`

`validate_exec_ls_path()` raises `CapabilityParamError` with the **canonicalized** (post-realpath) host path:
- `Path not in allowed scope: /real/resolved/path`
- `Path does not exist: /resolved/path`
- `Path is not a directory: /resolved/path`

`process_intent()` stores these verbatim in `Intent.error`, and `get_intent_result()` returns `error` to the owning agent. `execute_ls()` also propagates raw `ls` stderr via `RuntimeError(result.stderr.strip())`, which may include OS usernames and permission contexts.

This leak:
- discloses host filesystem structure
- confirms symlink targets by reporting the resolved canonical path
- aids path-probing by returning distinguishable errors for "not in scope", "does not exist", "not a directory"

A7-S5 acknowledged this; it remains unfixed in A9.

**Recommendation:**
- Return normalized agent-visible errors: `"Parameter validation failed"`, `"Execution failed"`, `"Execution timed out"`.
- Log full detail server-side with a correlation ID.

---

### IL-05 — TOCTOU between `validate_exec_ls_path()` and `execute_ls()`
**Severity:** MEDIUM
**Location:** `src/hassaleh/intent_daemon.py:259-269`, `src/hassaleh/capabilities/exec_ls.py:43-99`

`validate_exec_ls_path()` resolves the path via `os.path.realpath()` and checks prefix + existence. The resolved path is then handed to `execute_ls()`, which runs `ls -la resolved_path`. Between validation and execution, the filesystem can change:

- A component of `resolved_path` can be replaced with a symlink pointing outside the allowed bases. `ls` follows symlinks on its argument by default, and `-la` will list the link target's contents.
- A regular directory can be swapped for a symlink to `/etc`, `/root`, or anywhere the daemon user can read.

This is a classic path-validation TOCTOU. Exploitability depends on who has write access to `/app` and `/data`. If agents or other low-privileged users can write anywhere under those bases, escape is practical.

**Recommendation:**
- Pass `ls -la --` plus a directory file descriptor opened with `O_NOFOLLOW` / `O_DIRECTORY` (Linux) so symlink swaps cannot redirect the listing.
- Alternatively, run `ls` under a restricted user whose writable paths don't overlap the listed bases, and mount the allowed bases `nosymfollow` where supported.
- At minimum, re-run `os.path.realpath()` on the argument immediately before exec and verify prefix again.

---

### IL-06 — Heartbeat `previous_heartbeat` sequencing is ambiguous
**Severity:** MEDIUM
**Location:** `src/hassaleh/heartbeat_sdk.py:159-181`

The Cypher write sets:
```cypher
SET a.last_heartbeat = datetime(),
    a.previous_heartbeat = a.last_heartbeat,
    ...
```
Under Neo4j's `SET` semantics in a single clause the assignments happen in order against the same snapshot — but the code reads as "set new, then copy new to previous", which would break replay/forensic chaining. Even if current behavior is correct, the intent is not obvious and depends on semantics a reviewer should not have to rederive.

**Recommendation:**
- Capture the old value explicitly, e.g. `WITH a, a.last_heartbeat AS old_last` then `SET a.previous_heartbeat = old_last, a.last_heartbeat = datetime()`.
- Add a test asserting that after N ≥ 2 heartbeats, `previous_heartbeat` equals the timestamp of heartbeat N-1.

---

### IL-07 — Daemon capability execution trusts `cap.invoke_command` / `cap.exec_as_user` without a local allowlist
**Severity:** MEDIUM
**Location:** `src/hassaleh/daemon.py:408-468`

`_execute_capability()` reads `invoke_command` and `exec_as_user` from the graph and runs:
```python
cmd = ["sudo", "-n", "-u", exec_user, command] + args
```
List-form exec eliminates shell injection, but the security boundary depends entirely on **graph integrity**. Any actor able to write to `Capability` nodes can pick the binary and the target user. Combined with the loose write boundary in IL-01, this is a meaningful lateral-movement path.

**Recommendation:**
- Maintain a local, immutable allowlist mapping `capability_id → (binary, allowed_user)`. Reject any `Capability` whose `invoke_command`/`exec_as_user` doesn't match.
- Or sign `Capability` nodes with a provisioning key and verify on read.
- Audit who currently has write access to `Capability`.

---

### IL-08 — SDK readers don't validate `intent_id` as UUID at the boundary
**Severity:** LOW
**Location:** `src/hassaleh/intent_sdk.py:106-127`, `:227-254`

`_get_owned_intent()` forwards arbitrary strings to Neo4j. Parameterized queries neutralize injection, but there is no bound on ID length or format, giving callers a cheap way to waste DB cycles and leaving boundary hygiene inconsistent with the UUID-generating write path.

**Recommendation:**
- Add `uuid.UUID(intent_id)` at entry; raise a domain-specific error on failure.

---

### IL-09 — Generic `ValueError` used for intent "not found" / transition errors
**Severity:** LOW
**Location:** `src/hassaleh/intent_daemon.py:149,152`, `src/hassaleh/intent_sdk.py:122`, `src/hassaleh/errors.py`

Python built-in `ValueError` is too broad for domain errors. Callers that catch `ValueError` (often unavoidable when also handling JSON/UUID parsing) will silently swallow unrelated bugs. `errors.py` already defines a domain hierarchy for auth and heartbeat; intents should match.

**Recommendation:**
- Add `IntentNotFoundError` and `InvalidIntentTransitionError` to `errors.py` and use them in place of `ValueError` in Intent lifecycle APIs.

---

### IL-10 — Health endpoint binds `0.0.0.0` by default
**Severity:** LOW
**Location:** `src/hassaleh/daemon.py:1230-1242`

Default `bind = "0.0.0.0"`. The response discloses uptime, pending intent count, active worker count, rule count, bridge status, and schema version — useful fingerprinting signal on any routable interface.

**Recommendation:**
- Default to loopback; expose externally only behind a reverse proxy with auth.

---

### IL-11 — Development default Neo4j credentials remain in runtime entrypoints
**Severity:** LOW
**Location:** `src/hassaleh/daemon.py:1325-1327`, `src/hassaleh/sdk.py:69-71`, `src/hassaleh/heartbeat_sdk.py:47-49`

Defaults like `hassaleh` / `hassaleh-dev-2026` are baked into the modules when env vars are missing. A misconfigured environment comes up with known credentials.

**Recommendation:**
- Fail-closed in non-dev mode. Require explicit credentials. Gate dev defaults behind an explicit `HASSALEH_DEV_MODE=1` with a loud warning log.

---

### IL-INFO — `process_intent()` capability dispatch is hardcoded to `exec-ls`
**Severity:** INFO
**Location:** `src/hassaleh/intent_daemon.py:252-295`

The dispatch is `if capability_id == "exec-ls":` with no `else`. If any other capability_id passes submission (and `submit_intent()` only special-cases `exec-ls` for quick validation), the intent is claimed, transitioned to `running`, then silently left in that state until the reaper reaps it at 60 s. Not a security flaw, but a DoS-adjacent silent-failure surface once a second capability is registered in the MVP graph.

**Recommendation:**
- Replace with a handler registry; fail-fast with a `rejected` transition on unknown capability in `process_intent()`.

---

## 4. What A9 Already Fixed vs. Still Open

### Confirmed fixed
| A7 ID | Fix | Evidence |
|-------|-----|----------|
| S1 | Atomic CAS in `transition_intent()` (diagnostic read is post-failure, cannot influence mutation) | `intent_daemon.py:99-157` |
| S3 | Auth query scoped to `lifecycle IN ['active','running']` | `intent_sdk.py:92-98` |
| S4 | `SET status='running'` now requires `claimed_by=$daemon_id` | `intent_daemon.py:239-250` |
| S6 | `capability_id` regex `^[a-z0-9][a-z0-9_-]{0,63}$` | `intent_sdk.py:32,146-150` |
| S7 | Serialized params capped at 64 KB | `intent_sdk.py:35,153-158` |
| S11 | Ownership edge uses `MATCH` not `OPTIONAL MATCH` | `intent_sdk.py:115-117` |
| Q1 | Daemon calls `execute_ls()` from capability module (no inline duplicate) | `intent_daemon.py:267-281` |

### Still open after A9
- **IL-01:** legacy `sdk.py` impersonation (CRITICAL) — scoped out of A9 entirely.
- **IL-02 (A7-S2):** O(N) bcrypt scan in `IntentSDK._authenticate()` — scoped, not restructured.
- **IL-03:** legacy `sdk.py` unauthenticated result polling.
- **IL-04 (A7-S5):** host-path leakage in error messages — acknowledged and deferred.
- **IL-05:** `exec-ls` validation/execution TOCTOU (new finding).
- **IL-06:** heartbeat sequencing ambiguity (new finding).
- **IL-07:** daemon trusts graph-sourced `invoke_command`/`exec_as_user` (new finding).
- **IL-08 (A7-S9):** UUID validation at SDK boundary.
- **IL-09 (A7-S10):** generic `ValueError` for domain errors.
- **IL-10 / IL-11:** deployment-default exposure.

### B6 heartbeat context
B6 approval remains defensible for the core token-chain and rate-limit logic. IL-06 is a **correctness/forensics** refinement, not an auth-breaking flaw; it does not reopen the B6 approval.

---

## 5. Positive Security Observations

- `intent_sdk.py` and `intent_daemon.py` use parameterized Cypher consistently; no string interpolation of user data into queries.
- `exec_ls.execute_ls()` uses list-form `subprocess.run` with no `shell=True`.
- Path validation is layered: character → traversal → realpath → prefix → existence/type.
- `claim_intent()` is atomic with capability linkage in a single Cypher pattern.
- Ownership checks on both `get_intent_status` and `get_intent_result`.
- `HeartbeatSDK` uses the correct deterministic-lookup + single-bcrypt pattern (the target for IL-02).
- Reaper + per-instance orphan recovery close the daemon-crash class of leaks.
- Read-only SDK `query()` blocks write keywords, enforces timeouts and result-row caps.

---

## 6. Verdict

## PASS_WITH_NOTES (conditional)

### Why this passes
- The A9 hardening genuinely closes the A7 HIGH findings inside the dedicated intent pipeline.
- Claim, transition, and result-read paths are now race-safe and ownership-checked.
- Heartbeat auth uses the stronger pattern already and is not a blocker.

### The condition
PASS_WITH_NOTES holds **only if** `sdk.py` is strictly daemon-internal and technically prevented from being called by agent processes. If that is not enforced, IL-01 alone is sufficient to move the verdict to **FAIL**, because it reduces the entire authenticated intent pipeline to a suggestion.

### Recommended remediation order
1. **IL-01** — decommission or authenticate `HassalehSDK.submit_intent()`.
2. **IL-02** — bring Intent auth to parity with Heartbeat (`api_key_lookup` + single bcrypt).
3. **IL-03** — authenticate or remove `HassalehSDK.poll_intent()` / `wait_for_intent()`.
4. **IL-04 / IL-05** — sanitize agent-visible errors; close the exec-ls TOCTOU with `O_NOFOLLOW` or equivalent.
5. **IL-07** — add local allowlist for capability `invoke_command` / `exec_as_user`.
6. **IL-06, IL-08, IL-09** — correctness/observability cleanups.
7. **IL-10 / IL-11** — deployment hardening before broader rollout.

— Inanna 🛡️
