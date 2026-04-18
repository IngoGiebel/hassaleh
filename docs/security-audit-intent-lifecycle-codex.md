# E1-codex — Security Audit: Intent Lifecycle

**Reviewer:** Inanna  
**Date:** 2026-04-17  
**Scope:**
- `src/hassaleh/intent_daemon.py`, `src/hassaleh/intent_sdk.py`
- `src/hassaleh/capabilities/`, `src/hassaleh/auth.py`
- `src/hassaleh/daemon.py`, `src/hassaleh/errors.py`
- `src/hassaleh/heartbeat_sdk.py`, `src/hassaleh/sdk.py`

**Prior context reviewed:**
- `docs/review-a7-security-code-review.md`
- A9 fixes in commit `b0b4c08`
- B6 heartbeat review status: APPROVED

---

## 1. Executive Summary

The MVP intent pipeline is materially stronger than the A7-reviewed version: the TOCTOU transition bug is fixed, the `claimed_by` hijack path is closed, input validation is tighter, and the `exec-ls` execution path is cleaner.

However, the audit found one **critical** issue in the legacy `sdk.py` path: it still allows **unauthenticated intent submission and polling with caller-controlled `agent_id`**, which bypasses the stronger authentication model implemented in `intent_sdk.py`. This creates a split-brain security model where one SDK is hardened and another still permits impersonation and cross-agent result access.

The second major issue is that `IntentSDK._authenticate()` still performs an **O(N) bcrypt scan across active agents** instead of using the deterministic lookup helper already present in `auth.py` and used by `heartbeat_sdk.py`. That leaves a practical CPU-DoS and timing side channel in the main intent submission/read path.

### Severity counts

- **CRITICAL:** 1
- **HIGH:** 1
- **MEDIUM:** 2
- **LOW:** 1

---

## 2. Threat Model

### Assets
- Agent-scoped authority to submit Intents
- Intent status/results, including command output and error strings
- Capability boundaries (`HAS_CAPABILITY`)
- Neo4j-resident workflow state (`Intent`, `Agent`, `Capability`)
- API keys and heartbeat tokens

### Trust boundaries
- **Trusted daemon boundary:** `intent_daemon.py` and `daemon.py` act with database write authority
- **Agent boundary:** agents should only act via authenticated, least-privilege SDK calls
- **Capability boundary:** only agents with explicit `HAS_CAPABILITY` edges should trigger execution
- **Result boundary:** only the submitting/owning agent should read an Intent’s status/result

### Main attacker models
1. **Malicious or compromised agent** trying to impersonate another agent, read another agent’s Intent result, or flood auth/intent paths.
2. **Caller with access to the legacy SDK interface** but not meant to have full graph authority.
3. **Concurrent sender / replay actor** attempting to exploit state races or token reuse.
4. **Information-gathering actor** using validation errors and result payloads to map host filesystem details.

---

## 3. Findings

### HLI-SEC-001 — Legacy SDK permits unauthenticated agent impersonation for Intent submission
**Severity:** CRITICAL  
**Files:** `src/hassaleh/sdk.py:188-269`  

`HassalehSDK.submit_intent()` accepts a caller-supplied `agent_id` and writes an `(:Agent)-[:PROPOSED]->(:Intent)` edge with no authentication, no ownership proof, and no API-key verification. In contrast, `IntentSDK.submit_intent()` derives agent identity server-side from the API key.

**Impact:** Any caller with access to this SDK surface can impersonate any `Agent` node ID and enqueue Intents on that agent’s behalf. This is an authorization bypass on the intent lifecycle itself.

**Why this matters:** The hardened `intent_sdk.py` model is undermined if `sdk.py` remains available to agents or automation as an alternate path.

**Recommendation:**
- Deprecate or remove legacy intent submission from `sdk.py`, or
- Require the same auth model as `intent_sdk.py` (API key → server-side identity derivation), and
- Enforce capability/ownership checks before Intent creation.

---

### HLI-SEC-002 — IntentSDK authentication still does O(N) bcrypt verification across all active agents
**Severity:** HIGH  
**Files:** `src/hassaleh/intent_sdk.py:91-104`, `src/hassaleh/auth.py:26-33`  

`IntentSDK._authenticate()` fetches all active/running agents with `api_key_hash` and loops over them, calling `verify_api_key()` until one matches. The codebase already contains `lookup_hash()` for deterministic O(1) candidate lookup, and `heartbeat_sdk.py` uses that pattern correctly.

**Impact:**
- **CPU DoS:** invalid keys force one bcrypt check per active agent
- **Timing side channel:** request latency leaks approximate agent population and potentially match position
- **Inconsistent auth design:** heartbeat path is hardened, intent path is not

**Recommendation:**
- Add/use `a.api_key_lookup = lookup_hash(api_key)` in `IntentSDK._authenticate()`
- Then verify exactly one bcrypt hash
- Add per-agent and/or per-source rate limiting on auth failures

---

### HLI-SEC-003 — Legacy SDK exposes unauthenticated Intent result polling
**Severity:** MEDIUM  
**Files:** `src/hassaleh/sdk.py:273-323`  

`HassalehSDK.poll_intent()` and `wait_for_intent()` return lifecycle, stdout, stderr, and error details for any `intent_id` without authenticating the caller and without verifying ownership.

**Impact:** A caller who can guess or obtain an Intent ID can read execution results, including command output and failure messages, across agent boundaries.

**Recommendation:**
- Remove polling from `sdk.py`, or
- Require authenticated ownership checks equivalent to `IntentSDK._get_owned_intent()`
- Treat Intent IDs as non-secret identifiers, not authorization tokens

---

### HLI-SEC-004 — `exec-ls` validation and daemon error paths still leak internal filesystem details
**Severity:** MEDIUM  
**Files:** `src/hassaleh/capabilities/exec_ls.py:67-73`, `src/hassaleh/intent_daemon.py:260-262`, `src/hassaleh/intent_daemon.py:278-280`  

Validation failures and execution failures are stored verbatim in the Intent `error` field. These strings may expose:
- canonicalized host paths
- symlink targets
- permission-denied locations
- host layout details useful for follow-on attacks

This issue was already identified in A7 and remains present.

**Recommendation:**
- Return sanitized user-facing errors such as `Parameter validation failed` / `Execution failed`
- Log detailed path/OS errors server-side only
- If detailed errors are retained, gate them behind privileged debug access

---

### HLI-SEC-005 — Hard-coded development password defaults remain in daemon/SDK entrypoints
**Severity:** LOW  
**Files:** `src/hassaleh/daemon.py:783-785`, `src/hassaleh/sdk.py:69-71`  

The main daemon and legacy SDK still default to local Neo4j credentials when environment variables are absent. This is acceptable for local dev, but it is operationally risky if the code is reused outside the intended dev environment.

**Impact:** Misconfiguration can silently fall back to known credentials.

**Recommendation:**
- Fail closed in non-dev environments
- Require explicit env vars or config files for production
- If dev defaults must remain, guard them behind a `DEV_MODE` flag and document them clearly

---

## 4. Status of A9 Fixes

### Confirmed fixed
1. **A7-S1 TOCTOU in `transition_intent()` — FIXED**  
   `src/hassaleh/intent_daemon.py:99-157` now performs an atomic compare-and-set write first, with a diagnostic read only after CAS failure.

2. **A7-S4 claimed-intent hijack in `process_intent()` — FIXED**  
   `src/hassaleh/intent_daemon.py:237-250` now requires `claimed_by = $daemon_id` before transitioning to `running`.

3. **A7-S6 unsafe `capability_id` input — FIXED**  
   `src/hassaleh/intent_sdk.py:145-150` enforces a bounded identifier regex.

4. **A7-S7 unbounded `params` size — FIXED**  
   `src/hassaleh/intent_sdk.py:152-158` enforces a 64 KB serialized params limit.

5. **A8/Q1 duplicated exec-ls logic — FIXED**  
   `intent_daemon.py` now calls `execute_ls()` from `capabilities/exec_ls.py`, making the capability module the execution source of truth.

### Partially fixed / not fully closed
1. **A7-S3 authentication hardening — PARTIALLY FIXED**  
   `IntentSDK._authenticate()` is now scoped to `active` / `running` agents, but it still performs a full bcrypt scan. The stronger lookup-hash pattern exists in `auth.py` and is used in `heartbeat_sdk.py`, but not in `intent_sdk.py`.

2. **A7-S5 path/error leakage — NOT FIXED**  
   Detailed validation/execution errors still flow back to Intent readers.

### No regression observed in reviewed A9 fixes
I did **not** find a regression reopening the A9 TOCTOU or `claimed_by` issues.

---

## 5. Positive Security Observations

- `intent_daemon.py` and `intent_sdk.py` continue to use **parameterized Cypher** rather than string-built query values.
- `exec_ls.execute_ls()` uses **list-form subprocess invocation** with no `shell=True`.
- `validate_exec_ls_path()` still does layered validation: characters → traversal → `realpath()` → prefix check → existence/type check.
- `heartbeat_sdk.py` uses a much better auth pattern than `intent_sdk.py`: deterministic lookup plus bcrypt verification.

---

## 6. Verdict

## NEEDS_REVISION

The A9 hardening work meaningfully improved the dedicated MVP intent pipeline, but the overall **intent lifecycle** is still not safe enough to approve because the legacy `sdk.py` path bypasses the authenticated model entirely.

### Approval blockers
- **HLI-SEC-001** must be fixed: unauthenticated/caller-controlled agent impersonation in `sdk.py`
- **HLI-SEC-002** should be fixed before broader exposure: O(N) bcrypt auth in `intent_sdk.py`

### Can be deferred if explicitly accepted
- **HLI-SEC-004** path/error information leakage
- **HLI-SEC-005** dev credential defaults

If `sdk.py` is strictly internal and never exposed to agents, that should be documented and enforced technically; otherwise it remains the dominant risk in this audit.
