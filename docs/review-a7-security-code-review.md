# A7 — Security Code Review: MVP Intent Implementation

**Reviewer:** Inanna 🛡️
**Date:** 2026-04-15
**Scope:** `intent_sdk.py`, `intent_daemon.py`, `auth.py`, `errors.py`, `capabilities/exec_ls.py`, `tests/test_mvp_intent.py`
**Reference:** `docs/spec-mvp-test.md` v1.1, `docs/architecture.md`

---

## Findings

### S1 — TOCTOU in `transition_intent()`: read-then-write across two sessions
**Severity: HIGH**
**File:** `src/hassaleh/intent_daemon.py:118-143`

`transition_intent()` reads the current status in one Neo4j session (line 119-124), validates the transition in Python, then writes the new status in a *separate* session (line 137-143). Between the read and the write, another Daemon (or the reaper) could change the status, causing an invalid transition to succeed.

**Example:** Intent is `running`. Two concurrent calls — Daemon finishes (→ `success`) and reaper fires (→ `failed`). Both read `running`, both validate their transition, both write. Last write wins. This violates the state machine.

**Recommendation:** Combine the check-and-set into a single Cypher statement within one write transaction:
```cypher
MATCH (i:Intent {id: $id})
WHERE i.status IN $allowed_from_states
SET i.status = $to_state, i.updated_at = datetime()
RETURN i.id AS id
```
If the MATCH returns nothing, the transition was invalid or another writer changed state first.

---

### S2 — Full Agent table scan on every authentication call
**Severity: HIGH**
**File:** `src/hassaleh/intent_sdk.py:78-88`

`_authenticate()` runs `MATCH (a:Agent) WHERE a.api_key_hash IS NOT NULL` and pulls *every* agent's hash into application memory, then iterates them doing bcrypt verification. This has two problems:

1. **Timing side-channel:** bcrypt is deliberately slow (~100ms per check). With N agents, an invalid key takes N × 100ms, leaking the total number of registered agents to any caller. The correct key is found after 1..N checks, leaking the key's position in the result set.

2. **Denial of service:** An attacker sending invalid keys forces O(N) bcrypt operations per request. With 100 agents and 10 requests/second, that's 1000 bcrypt calls/second — trivially saturating CPU.

**Recommendation:**
- Add a key prefix/identifier (e.g., first 8 chars of the key are a lookup tag stored unhashed alongside the bcrypt hash). Authenticate in O(1): look up by tag, verify one hash.
- Alternatively, use a keyed hash (HMAC-SHA256 with a server secret) as a fast pre-filter, falling back to bcrypt only for matching candidates.
- At minimum, add rate limiting on authentication failures per source.

---

### S3 — `_authenticate()` scans all agents including unrelated ones
**Severity: MEDIUM**
**File:** `src/hassaleh/intent_sdk.py:79-83`

The authentication query has no filter beyond `api_key_hash IS NOT NULL`. In a multi-tenant or multi-workspace scenario, this means an API key for agent A could hypothetically verify against any agent's hash if a collision or re-use occurred. The architecture doc shows agents in different workspaces with different roles — this query crosses all of them.

**Recommendation:** Scope the query to active agents only: add `AND a.lifecycle IN ['active', 'running']`. Disabled or circuit-broken agents should not be authenticatable.

---

### S4 — Daemon `process_intent()` does not re-verify `status` before execution
**Severity: MEDIUM**
**File:** `src/hassaleh/intent_daemon.py:206-235`

After `claim_intent()` succeeds, `process_intent()` transitions to `running` (line 231-235) using a bare `MATCH (i:Intent {id: $id, status: 'claimed'})`. This is correct for the happy path, but the function also handles intents that arrive already in a non-pending state (line 222-226 handles `status == "pending"`). If the intent was somehow already `claimed` when `process_intent()` is called, it skips the claim step and proceeds directly to the `SET status = 'running'` — without checking `claimed_by`. This means a daemon could hijack another daemon's claimed intent.

**Recommendation:** The `SET status = 'running'` query should include `AND i.claimed_by = $daemon_id` to prevent hijacking.

---

### S5 — Error messages expose internal paths to agents
**Severity: MEDIUM**
**File:** `src/hassaleh/capabilities/exec_ls.py:67`, `src/hassaleh/intent_daemon.py:244,276`

Validation errors include resolved filesystem paths: `"Path not in allowed scope: /home/uranus/actual/real/path"`. The daemon stores these verbatim in the intent's `error` field, which agents read via `get_intent_result()`. This leaks:
- Real filesystem structure of the host
- The resolved path of symlinks (confirming their targets)
- OS user context from `ls` stderr (e.g., `"ls: cannot open directory '/root': Permission denied"`)

This was already noted as F9 in the spec review and deferred for MVP. Confirming the code matches the deferred risk.

**Recommendation:** For MVP, accept with note. For hardening: sanitize errors to `"Path validation failed"` / `"Execution failed"` with a reference ID. Log full details server-side only.

---

### S6 — `capability_id` not validated as a safe string
**Severity: MEDIUM**
**File:** `src/hassaleh/intent_sdk.py:112-116`

The `capability_id` parameter is passed directly into parameterized Cypher (`$cap_id`), which is safe against Cypher injection. However, `capability_id` is later stored as a string property on the Intent node and used in log messages (line 176). There is no validation that `capability_id` is a reasonable identifier (e.g., alphanumeric + hyphens). A malicious agent could submit an intent with `capability_id` set to a very long string (memory exhaustion in Neo4j) or a string containing control characters (log injection).

**Recommendation:** Validate `capability_id` against a pattern like `^[a-z0-9][a-z0-9_-]{0,63}$` before any processing.

---

### S7 — `params` dict accepts arbitrary JSON without size limits
**Severity: MEDIUM**
**File:** `src/hassaleh/intent_sdk.py:143-144`

`params` is serialized to JSON and stored in Neo4j without any size check. An agent could submit `{"path": "A" * 10_000_000}` — a 10 MB string stored as a Neo4j property. Repeated submissions could exhaust database storage. The spec mentions JSON schema validation per capability (F10) as deferred.

**Recommendation:** Add a hard limit on `len(json.dumps(params))` — e.g., 64 KB. This protects the database regardless of per-capability schema validation.

---

### S8 — No rate limiting on SDK operations
**Severity: MEDIUM**
**File:** `src/hassaleh/intent_sdk.py` (general)

Neither `submit_intent`, `get_intent_status`, nor `get_intent_result` have any rate limiting. A compromised agent (or one in a tight loop) could:
- Flood the database with pending intents (submit)
- Saturate the daemon's processing queue
- DoS the auth path via S2 (bcrypt scan)

The spec acknowledges "No rate limiting" in §9 (Known Limitations).

**Recommendation:** For MVP, accept with note. For hardening: add per-agent rate limiting at the SDK layer (token bucket or sliding window). The daemon should also limit how many intents a single agent can have in non-terminal states.

---

### S9 — `intent_id` in `get_intent_status`/`get_intent_result` not validated as UUID
**Severity: LOW**
**File:** `src/hassaleh/intent_sdk.py:187,219`

The `intent_id` parameter is passed directly to Cypher as a string. While this is safe (parameterized query), a caller could pass an arbitrarily long string as `intent_id`. The `MATCH (i:Intent {id: $id})` will simply not find it, but the string travels through the network to Neo4j. Validating UUID format at the SDK boundary is cheap defense-in-depth.

**Recommendation:** Add `uuid.UUID(intent_id)` validation at the top of both methods (similar to how the test validates the return value at line 235 of the test file).

---

### S10 — `ValueError` used for "Intent not found" — ambiguous error semantics
**Severity: LOW**
**File:** `src/hassaleh/intent_sdk.py:195`, `src/hassaleh/intent_daemon.py:127`

Both the SDK and daemon raise `ValueError` for "Intent not found." This is a Python built-in that could be raised by many unrelated operations (JSON parsing, type conversions). If the caller catches `ValueError` broadly, they might swallow unrelated errors. The errors module already defines domain-specific exceptions.

**Recommendation:** Add `IntentNotFoundError(Exception)` to `errors.py` and use it instead of `ValueError`.

---

### S11 — `OPTIONAL MATCH` in ownership check allows null owner bypass
**Severity: LOW**
**File:** `src/hassaleh/intent_sdk.py:187-199`

The ownership check uses `OPTIONAL MATCH (i)-[:SUBMITTED_BY]->(a:Agent)`. If an Intent node exists but has no `SUBMITTED_BY` relationship (e.g., corrupted data, manual DB edit), then `owner_id` is `None` and the check `owner_id != agent_id` succeeds — correctly denying access. However, the error message "Intent does not belong to caller" is misleading for this case (the intent is orphaned, not owned by someone else).

More importantly: if an intent is ever created without a `SUBMITTED_BY` edge (bug, race, manual intervention), *no agent can read it*, and it becomes a silent ghost in the system.

**Recommendation:** Use `MATCH` instead of `OPTIONAL MATCH` for the ownership edge. If the edge doesn't exist, the query returns no record, which already triggers the "not found" path. This is cleaner and removes the null-owner ambiguity.

---

### S12 — Daemon `claim_intent` rejection path has a race condition
**Severity: LOW**
**File:** `src/hassaleh/intent_daemon.py:178-195`

When the atomic claim query returns no result, the daemon tries a separate rejection query (line 179-186). Between these two queries, another daemon could claim the same intent. The rejection query then also returns nothing (intent is no longer `pending`). This path is handled correctly (line 193-195 logs and returns False), so it's not a functional bug. However, the rejection query could also accidentally reject an intent that was *legitimately claimed* by another daemon in the gap between the two queries — if that other daemon claimed it but hasn't transitioned it away from `pending` yet.

Actually, re-reading: the rejection query matches `status: 'pending'`, and a successfully claimed intent has `status: 'claimed'`. So the race is safe — the rejection will not match a claimed intent. No action needed, but documenting for completeness.

**Impact:** None (safe by design). No action required.

---

### S13 — Test file hardcodes Neo4j credentials
**Severity: LOW**
**File:** `tests/test_mvp_intent.py:64-66`

```python
NEO4J_URI = os.environ.get("NEO4J_TEST_URI", "bolt://localhost:7690")
NEO4J_USER = os.environ.get("NEO4J_TEST_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_TEST_PASSWORD", "hassaleh")
```

The defaults are the dev credentials. This is acceptable for a local dev/test environment. The env-var override pattern is correct.

**Recommendation:** No action for MVP. For CI/CD: ensure test credentials differ from production defaults.

---

### S14 — No `__init__.py` visibility into capabilities package
**Severity: INFO**
**File:** `src/hassaleh/capabilities/`

Not a security finding per se, but the `capabilities/` package should have an `__init__.py` that does not re-export internal functions like `validate_exec_ls_path` at the package level. Currently, the daemon imports directly from `hassaleh.capabilities.exec_ls`, which is correct.

---

## Positive Observations

Things done well (these matter for a security review — knowing what's solid is as important as finding gaps):

1. **All Cypher queries use parameter binding (`$param`).** No string interpolation in any Cypher query across the reviewed files. This eliminates Cypher injection. ✅

2. **`subprocess.run` uses list-form invocation.** No `shell=True` anywhere. Shell injection is not possible through the exec-ls path. ✅

3. **Path validation is defense-in-depth.** Character check → traversal token check → canonicalization → prefix check → existence check. The layered approach catches different attack vectors at different stages. ✅

4. **Atomic claim + capability check in `claim_intent()`.** The single-query pattern from the spec is correctly implemented, preventing the TOCTOU race identified in F3. ✅

5. **Ownership checks on status/result reads.** Both `get_intent_status` and `get_intent_result` verify the caller owns the intent. Cross-agent reads are blocked. ✅

6. **bcrypt for API key hashing.** `secrets.token_urlsafe(32)` for key generation, bcrypt for storage. Correct choice for this use case. ✅

7. **Output truncation at 64 KB.** Prevents memory exhaustion from large `ls` output. ✅

8. **Reaper and orphan recovery.** Both general reaper and per-instance startup recovery are implemented, addressing the F4 finding. ✅

9. **Test coverage is thorough.** The test file covers happy paths, auth failures, cross-agent access, concurrency, path traversal, symlink escapes, state machine transitions, truncation, and execution safety assertions. This is strong for an MVP. ✅

---

## Summary

| # | Finding | Severity | Category |
|---|---------|----------|----------|
| S1 | TOCTOU in `transition_intent()` — read and write in separate sessions | **HIGH** | Race condition |
| S2 | Full agent table scan for bcrypt auth — timing + DoS | **HIGH** | Authentication |
| S3 | Auth query crosses all agents regardless of lifecycle | MEDIUM | Authentication |
| S4 | `process_intent` doesn't verify `claimed_by` before running | MEDIUM | Authorization |
| S5 | Error messages expose internal paths (acknowledged F9) | MEDIUM | Info leakage |
| S6 | `capability_id` not validated as safe identifier | MEDIUM | Input validation |
| S7 | `params` dict has no size limit | MEDIUM | Resource exhaustion |
| S8 | No rate limiting (acknowledged in spec §9) | MEDIUM | DoS |
| S9 | `intent_id` not validated as UUID format | LOW | Input validation |
| S10 | `ValueError` for domain errors — ambiguous | LOW | Error handling |
| S11 | `OPTIONAL MATCH` in ownership — null owner edge case | LOW | Authorization |
| S12 | Rejection race in `claim_intent` (safe by design) | LOW | Race condition |
| S13 | Hardcoded dev credentials in test defaults | LOW | Credential mgmt |
| S14 | Capabilities package import hygiene | INFO | Code quality |

**CRITICAL: 0** | **HIGH: 2** | **MEDIUM: 6** | **LOW: 4** | **INFO: 1**

---

## Verdict

### APPROVED_WITH_NOTES

The implementation correctly addresses the CRITICAL and HIGH findings from the original spec review (F1–F5). The code quality is strong: parameterized Cypher, list-form subprocess, layered path validation, ownership checks, atomic claims, and good test coverage.

The two HIGH findings in this code review (S1, S2) are not exploitable in the current single-daemon MVP test environment but would become real attack surfaces under multi-daemon deployment or with untrusted agents:

- **S1** should be fixed before any concurrent daemon deployment.
- **S2** should be fixed before scaling beyond ~10 agents or exposing the SDK to untrusted callers.

The MEDIUM findings (S3–S8) are defense-in-depth improvements that can be addressed in the hardening phase, consistent with the spec's §9 backlog.

The codebase is solid for MVP testing in an isolated environment. Ship it, but track S1 and S2 for pre-production hardening.

— Inanna 🛡️
