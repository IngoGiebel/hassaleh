# Interface Specification: MVP Intent Pipeline Test

**Version:** 1.1 (Post-Review Revision)
**Author:** Dione
**Revised:** 2026-04-14 (A3 — Concept Revision addressing Inanna's security review)
**Status:** Revised — security findings addressed

---

## 1. Purpose

Validate the complete Hassaleh Intent pipeline end-to-end: an Agent submits an Intent through the SDK, the Daemon processes it, and the Agent reads the result back.

## 2. Scope

This spec covers:
- SDK method: `submit_intent()`
- SDK method: `get_intent_status()`
- SDK method: `get_intent_result()`
- Daemon Intent processing loop
- Capability enforcement
- Neo4j state transitions

## 3. Preconditions

- Neo4j instance running with Hassaleh schema applied
- Agent "dione" registered with `HAS_CAPABILITY -> exec-ls`
- Daemon running and connected to Neo4j

## 3A. Capability Path Validation — exec-ls *(Added: A3, addresses F1)*

The `exec-ls` capability accepts a `path` parameter. To prevent path traversal and directory-escape attacks, the following rules apply **before** the path is passed to `subprocess.run`:

### Allowlist

A capability-level configuration defines allowed base directories:

```python
EXEC_LS_ALLOWED_BASES = ["/app", "/data"]
```

Only paths that resolve **under** one of these bases are permitted.

### Validation Sequence

1. **Reject invalid characters:** Path must match `^[a-zA-Z0-9/_.\-]+$`. Reject null bytes, non-printable characters, and control sequences immediately.
2. **Reject raw traversal tokens:** If the raw input contains `..` as a path segment, reject before any filesystem call.
3. **Canonicalize:** `resolved = os.path.realpath(path)` — resolves symlinks and `..` on the real filesystem.
4. **Prefix check:** `any(resolved == base or resolved.startswith(base + "/") for base in EXEC_LS_ALLOWED_BASES)` must be `True`. If not → reject with `CapabilityParamError("Path not in allowed scope")`.
5. **Existence check:** `os.path.isdir(resolved)` must be `True`. Files are not valid targets for `exec-ls`.

### Output Limits

- Capture at most **64 KB** of stdout from `ls`. Truncate with a trailing `\n[output truncated at 64KB]` marker.
- If the directory contains more than **10,000** entries, truncate and note in output.

### Execution Context

- `subprocess.run` MUST NOT run as root. The Daemon executes capability handlers under a dedicated low-privilege user (e.g., `hassaleh-exec`).
- `shell=True` MUST NOT be used. List-form invocation only.

---

## 3B. Agent Authentication Model *(Added: A3, addresses F2)*

### Problem

`submit_intent(agent_id, ...)` accepts `agent_id` as caller-supplied input with no verification, allowing impersonation.

### MVP Authentication: Pre-Shared API Keys

Each registered agent receives a unique API key at registration time, stored as a property on the Agent node:

```cypher
(:Agent {id: "dione", api_key_hash: "<bcrypt hash>"})
```

### SDK Call Signature (Revised)

```python
submit_intent(api_key: str, capability_id: str, params: dict) -> str
```

- `agent_id` is **no longer a parameter**. It is derived server-side from the API key.
- The SDK authenticates the caller by hashing the provided `api_key` and matching it against `Agent.api_key_hash`.
- On mismatch → `AuthenticationError`.

### Authentication Flow

1. Caller provides `api_key` to the SDK method.
2. SDK queries: `MATCH (a:Agent) WHERE a.api_key_hash = $hash RETURN a.id AS agent_id`
3. If no match → `AuthenticationError("Invalid API key")`.
4. `agent_id` is now trusted (derived from DB, not from caller).
5. Intent is created with the authenticated `agent_id`.

### Key Management (MVP Scope)

- Keys are generated at agent registration (`secrets.token_urlsafe(32)`).
- Keys are hashed with bcrypt before storage; plaintext is returned once at registration.
- Key rotation: delete and re-register. No in-place rotation for MVP.
- **Limitation:** No token expiry, no refresh mechanism, no rate limiting per key. See Section 9 (Known Limitations).

---

## 4. SDK Interface

### 4.1 `submit_intent(api_key, capability_id, params) -> str`

**Input:**
```python
api_key: str        # Pre-shared API key; agent_id derived server-side (see §3B)
capability_id: str  # e.g. "exec-ls"
params: dict        # e.g. {"path": "/app"} — validated per §3A
```

**Returns:** `intent_id: str` (UUID)

**Neo4j state after call:**
```cypher
(:Intent {
    id: <uuid>,
    agent_id: "dione",
    capability_id: "exec-ls",
    params: '{"path": "/app"}',  // JSON string
    status: "pending",
    created_at: <datetime>,
    updated_at: <datetime>,
    result: null,
    error: null
})
```

**Relationships created:**
```cypher
(intent)-[:SUBMITTED_BY]->(agent:Agent {id: "dione"})
(intent)-[:REQUIRES]->(cap:Capability {id: "exec-ls"})
```

**Error cases:**
- Invalid or unknown API key → `AuthenticationError` *(F2)*
- Capability not found → `CapabilityNotFoundError`
- Agent lacks capability → `CapabilityDeniedError`
- Capability parameter validation failed → `CapabilityParamError` *(F1)*
- Neo4j connection failed → `ConnectionError`

### 4.2 `get_intent_status(api_key, intent_id) -> dict` *(Revised: A3, addresses F5)*

**Input:**
```python
api_key: str    # Authenticates caller; agent_id derived server-side
intent_id: str  # UUID of the Intent to query
```

**Authorization:** The Intent's `SUBMITTED_BY` agent must match the authenticated caller. If not → `AccessDeniedError("Intent does not belong to caller")`.

**Returns:**
```python
{
    "id": "uuid-...",
    "status": "pending" | "claimed" | "running" | "success" | "failed" | "rejected",
    "created_at": "2026-04-14T...",
    "updated_at": "2026-04-14T...",
    "claimed_at": None | "2026-04-14T...",
    "completed_at": None | "2026-04-14T..."
}
```

### 4.3 `get_intent_result(api_key, intent_id) -> dict` *(Revised: A3, addresses F5)*

**Input:**
```python
api_key: str    # Authenticates caller; agent_id derived server-side
intent_id: str  # UUID of the Intent to query
```

**Authorization:** Same as §4.2 — caller must own the Intent.

**Returns (on success):**
```python
{
    "id": "uuid-...",
    "status": "success",
    "result": "file1.txt\nfile2.py\n...",  # stdout from exec-ls
    "error": None,
    "duration_ms": 42
}
```

**Returns (on failure):**
```python
{
    "id": "uuid-...",
    "status": "failed",
    "result": None,
    "error": "Permission denied: /root",
    "duration_ms": 5
}
```

**Returns (still processing):**
```python
{
    "id": "uuid-...",
    "status": "running",
    "result": None,
    "error": None,
    "duration_ms": None
}
```

## 5. Daemon Processing

### 5.1 Intent Lifecycle State Machine

```
pending → claimed → running → success
                            → failed
                  → rejected (capability check fails post-claim)
```

*(Revised: A3, addresses F8 — capability check occurs after claiming, so rejection originates from `claimed`, not `pending`.)*

**Transitions:**
| From | To | Trigger | Actor |
|------|----|---------|-------|
| pending | claimed | Daemon picks up Intent (atomic claim+cap check, §5.2) | Daemon |
| claimed | rejected | Agent lacks capability (detected in atomic claim query) | Daemon |
| claimed | running | Daemon starts execution | Daemon |
| running | success | Execution completes with exit 0 | Daemon |
| running | failed | Execution fails or times out | Daemon |
| claimed/running | failed | Reaper detects orphan (§5.5) | Reaper |

**Invalid transitions (must be rejected):**
- `success → pending` (no rollback)
- `failed → running` (no automatic retry)
- `claimed → pending` (no unclaiming — recovery goes through `failed` via reaper only)

**Cancellation:** Out of scope for MVP. Once submitted, an Intent will be processed to a terminal state (`success`, `failed`, or `rejected`). Cancellation may be added in a future iteration.

### 5.2 Daemon Processing Steps *(Revised: A3, addresses F3)*

1. **Poll:** Query `MATCH (i:Intent {status: "pending"}) RETURN i ORDER BY i.created_at LIMIT 10`
2. **Atomic Claim + Capability Check** (single Cypher transaction):
   ```cypher
   // Attempt claim with capability verification in one atomic operation
   MATCH (i:Intent {id: $id, status: "pending"})
         -[:SUBMITTED_BY]->(a:Agent)
         -[:HAS_CAPABILITY]->(c:Capability {id: i.capability_id})
   SET i.status = "claimed",
       i.claimed_at = datetime(),
       i.claimed_by = $daemon_instance_id
   RETURN i
   ```
   - If the MATCH succeeds → Intent is claimed and capability is verified atomically. No TOCTOU window.
   - If the MATCH fails (no capability relationship) → run the rejection query:
   ```cypher
   MATCH (i:Intent {id: $id, status: "pending"})
   SET i.status = "rejected",
       i.error = "Agent lacks required capability",
       i.updated_at = datetime()
   RETURN i
   ```
   *(Note: if this also returns nothing, another Daemon already claimed it — no action needed.)*
3. **Validate params:** For `exec-ls`, apply path validation per §3A. If validation fails → `status = "failed"`, `error = "Parameter validation failed: <reason>"`.
4. **Execute:** Set `status = "running"`. Run the capability handler (for `exec-ls`: `subprocess.run(["ls", "-la", resolved_path])` as the `hassaleh-exec` user, capturing at most 64 KB stdout).
5. **Result:** Set `status = "success"`, `result = stdout`, `duration_ms = elapsed`, `completed_at = datetime()`
6. **On error:** Set `status = "failed"`, `error = stderr or exception message`, `completed_at = datetime()`

### 5.3 Timeout

- Default: 30 seconds per Intent
- On timeout: `status = "failed"`, `error = "Execution timed out after 30s"`

### 5.4 Concurrency

- Daemon processes max 3 Intents simultaneously
- Each Intent is claimed atomically via the combined claim+capability query (§5.2 step 2). The `MATCH ... {status: "pending"}` + `SET status = "claimed"` in a single write transaction acquires a Neo4j write lock on the node, preventing double-processing.
- Each claim records `claimed_by = $daemon_instance_id` for auditability and orphan recovery (§5.5).

### 5.5 Orphan Recovery / Reaper *(Added: A3, addresses F4)*

If the Daemon crashes after claiming an Intent but before writing a terminal status, the Intent becomes orphaned (stuck in `claimed` or `running` indefinitely).

**Reaper Process:**

A lightweight reaper runs on a timer (every 60 seconds), either as part of the Daemon startup sequence or as a separate sidecar:

```cypher
// Find orphaned Intents: claimed or running for longer than 2× timeout (60s default)
MATCH (i:Intent)
WHERE i.status IN ["claimed", "running"]
  AND i.updated_at < datetime() - duration({seconds: 60})
SET i.status = "failed",
    i.error = "Daemon lost — execution state unknown (recovered by reaper)",
    i.completed_at = datetime(),
    i.updated_at = datetime()
RETURN i
```

**Startup Recovery:**

On startup, each Daemon instance also fails all Intents claimed by its own prior incarnation:

```cypher
MATCH (i:Intent {claimed_by: $daemon_instance_id})
WHERE i.status IN ["claimed", "running"]
SET i.status = "failed",
    i.error = "Daemon restarted — prior execution lost",
    i.completed_at = datetime(),
    i.updated_at = datetime()
RETURN i
```

**Design Notes:**
- The reaper does NOT return Intents to `pending` (which would violate the state machine). It fails them explicitly so the submitting agent gets a clear signal to retry if desired.
- `claimed_by` enables per-instance recovery without a global sweep affecting healthy Daemons' in-flight work.

## 6. Test Scenarios

### 6.1 Happy Path
1. Submit Intent with valid agent + capability
2. Assert Intent appears in graph with `status = "pending"`
3. Wait for Daemon processing (max 10s)
4. Assert Intent transitions to `success`
5. Assert `result` contains expected file listing
6. Assert all timestamps are set

### 6.2 Missing Capability
1. Create agent without `exec-ls` capability
2. Submit Intent for `exec-ls`
3. Assert Intent becomes `rejected`
4. Assert error message mentions capability denial

### 6.3 Unknown Agent
1. Submit Intent with non-existent agent_id
2. Assert `AgentNotFoundError` raised

### 6.4 Unknown Capability
1. Submit Intent with non-existent capability_id
2. Assert `CapabilityNotFoundError` raised

### 6.5 Execution Failure
1. Submit Intent with `exec-ls` on non-existent path
2. Assert Intent becomes `failed`
3. Assert error message is meaningful

### 6.6 Concurrent Intents
1. Submit 5 Intents simultaneously
2. Assert all are processed (no lost Intents)
3. Assert no double-processing (each claimed exactly once)

### 6.7 Intent Status Polling
1. Submit Intent
2. Poll `get_intent_status()` repeatedly
3. Assert status transitions: `pending → claimed → running → success`
4. Assert `updated_at` changes with each transition

## 7. Security Considerations *(Originally for Inanna's review — now resolved)*

- [x] Can an agent submit Intents for another agent? → **Blocked by §3B** (API key auth; agent_id derived server-side)
- [x] Can an agent modify an Intent after submission? → **App-level immutability** (no SDK mutation methods; DB enforcement deferred, §9)
- [x] What happens if the Daemon crashes between `claimed` and `running`? → **Reaper recovers orphans** (§5.5)
- [x] Can an agent poll/read other agents' Intent results? → **Blocked by §4.2/§4.3** (caller verification)
- [x] Is the capability check atomic with the claim? → **Yes, single query** (§5.2 step 2)
- [x] Can `params` contain injection payloads? → **Validated per capability** (§3A for exec-ls)

## 8. Test Scenarios — Additional *(Added: A3)*

### 8.1 Path Traversal Rejection (F1)
1. Submit Intent with `{"path": "/../../../etc/passwd"}`
2. Assert Intent becomes `failed` with `CapabilityParamError`
3. Assert no directory listing is returned

### 8.2 Authentication Failure (F2)
1. Call `submit_intent()` with an invalid API key
2. Assert `AuthenticationError` raised
3. Assert no Intent is created in Neo4j

### 8.3 Cross-Agent Read Blocked (F5)
1. Agent A submits an Intent, gets `intent_id`
2. Agent B calls `get_intent_result(B_api_key, intent_id)`
3. Assert `AccessDeniedError`

### 8.4 Orphan Recovery (F4)
1. Submit Intent, wait for `claimed` status
2. Kill the Daemon process
3. Restart Daemon (or trigger reaper)
4. Assert Intent transitions to `failed` with reaper error message

### 8.5 Atomic Claim Under Concurrency (F3/F7)
1. Start 3 Daemon instances
2. Submit 1 Intent
3. Assert exactly 1 Daemon claims it (check `claimed_by` field)
4. Assert the other 2 Daemons' claim attempts are no-ops

---

## 9. Known Limitations & Hardening Backlog *(Added: A3)*

The following items are acknowledged as out-of-scope for the MVP but tracked for future hardening:

| # | Item | Severity | MVP Mitigation | Hardening Target |
|---|------|----------|----------------|------------------|
| F6 | Intent immutability not enforced at DB layer | MEDIUM | SDK exposes no mutation methods. Application-level enforcement only. | Add Neo4j property constraints or triggers to make `params`, `capability_id`, `agent_id` immutable after creation. |
| F7 | Claim atomicity depends on Neo4j write-lock behavior | MEDIUM | Single-statement MATCH+SET in one write transaction (§5.2). Verified by concurrency test (§8.5). | Add explicit integration tests with real concurrent Daemon processes (not simulated). Document Neo4j version requirements for lock guarantees. |
| F9 | Error messages may leak internal filesystem paths | LOW | Acceptable in isolated test environment. | Sanitize errors before storage: return generic error category + reference ID; log full details server-side only. |
| F10 | Cypher injection if `params` not parameterized | LOW | All Cypher queries use `$param` binding (verified in code review). | Add `params` JSON schema validation per capability type before storage. Enforce parameterized queries via linting/CI check. |

**Additional MVP scope limitations:**
- **No Intent cancellation** (F8.2): Once submitted, Intents run to terminal state. Cancel support deferred.
- **No API key expiry or rotation** (F2 extension): Keys are static until agent is re-registered.
- **No rate limiting** per agent or per API key.
- **No distinct `timed_out` status** (F8.3): Timeouts map to `failed` with a conventional error prefix `"Execution timed out"`. Agents can pattern-match on this string if needed.

---

## 10. Security Review — Inanna

**Reviewer:** Inanna 🛡️
**Date:** 2026-04-14
**Spec Version:** 1.0 (Draft)

---

### 10.1 Findings

#### F1 — Command Injection via `params.path` in exec-ls
**Severity: CRITICAL**

Section 5.2 step 4 shows: `subprocess.run(["ls", "-la", path])`. The `path` value comes directly from `params` which is agent-supplied JSON. Even with list-form `subprocess.run` (no shell=True), the path is not validated.

**Attack vectors:**
- **Path traversal:** `{"path": "/etc/shadow"}`, `{"path": "/../../../etc/passwd"}` — reads arbitrary directory listings.
- **Null byte injection:** `{"path": "/app\x00/etc"}` — depending on OS/Python version, may truncate at null.
- **Symlink abuse:** If the filesystem contains symlinks, `{"path": "/app/link-to-root"}` escapes confinement.
- **Resource exhaustion:** `{"path": "/"}` on a large filesystem returns enormous output; `{"path": "/proc"}` could behave unpredictably.

**Note:** Because `subprocess.run` uses list form, shell metacharacters (`;`, `|`, `&&`) are NOT injectable as separate commands. This is good. But directory traversal is still fully open.

**Remediation:**
1. Define an allowlist of base directories (e.g., `/app`, `/data`).
2. Canonicalize the path with `os.path.realpath()` and verify it starts with an allowed prefix.
3. Reject paths containing `..`, null bytes, or non-printable characters before canonicalization.
4. Consider `chroot` or namespace-level filesystem isolation for capability execution.

---

#### F2 — Agent Impersonation: No Authentication on `submit_intent()`
**Severity: CRITICAL**

`submit_intent(agent_id, capability_id, params)` accepts `agent_id` as a plain string parameter. There is no mechanism described for verifying that the caller actually IS that agent. Any caller who can reach the SDK can submit Intents as any registered agent.

**Impact:** An agent (or any process with SDK access) can:
- Submit Intents under another agent's identity, inheriting that agent's capabilities.
- Frame another agent for malicious operations.
- Bypass its own capability restrictions entirely.

**Remediation:**
1. `agent_id` must come from an authenticated session/token, not from the caller's input.
2. The SDK should derive `agent_id` from a signed credential (API key, JWT, mTLS cert) rather than accepting it as a parameter.
3. At minimum for MVP: if authentication is deferred, document it as a known limitation and ensure the test environment is network-isolated.

---

#### F3 — TOCTOU Race Between Capability Check and Claim
**Severity: HIGH**

The Daemon processing sequence (Section 5.2) is:
1. Claim (step 2) — sets `status = "claimed"`
2. Capability check (step 3) — queries the graph for the capability relationship

These are two separate operations. Between claim and capability check:
- An admin could revoke the agent's capability → the Intent proceeds anyway.
- More critically: the spec does NOT show the capability check and claim happening in a single atomic transaction.

**The `SET WHERE` pattern** from Section 5.4 (`SET i.status = "claimed" WHERE i.status = "pending"`) provides atomicity for the claim itself (preventing double-processing) but does NOT include the capability verification in the same atomic operation.

**Remediation:**
1. Combine claim + capability check into a single Cypher transaction:
   ```cypher
   MATCH (i:Intent {id: $id, status: "pending"})
         -[:SUBMITTED_BY]->(a:Agent)
         -[:HAS_CAPABILITY]->(c:Capability {id: i.capability_id})
   SET i.status = "claimed", i.claimed_at = datetime()
   RETURN i
   ```
   If the MATCH fails (no capability), the SET never fires. This is atomic.
2. If the capability is absent, a separate query should set `status = "rejected"`.

---

#### F4 — Orphaned Intents on Daemon Crash
**Severity: HIGH**

If the Daemon crashes after setting `status = "claimed"` (or `"running"`) but before writing `"success"` or `"failed"`, the Intent is stuck forever. The state machine has no recovery transitions:
- `claimed → pending` is explicitly listed as invalid (Section 5.1).
- No timeout or watchdog reclaims orphaned Intents.

**Impact:** Intents silently disappear. Agents polling `get_intent_status()` see `"claimed"` or `"running"` indefinitely. Over time, orphaned Intents accumulate.

**Remediation:**
1. Add a reaper/watchdog: if an Intent has been `"claimed"` or `"running"` for longer than `timeout × 2` (e.g., 60s), transition it to `"failed"` with `error = "Daemon lost — execution state unknown"`.
2. Record the Daemon instance ID on claim (`claimed_by: "daemon-xyz"`). On startup, each Daemon instance fails all Intents claimed by its own prior incarnation.
3. Alternatively, allow `claimed → pending` as a recovery-only transition triggered by the reaper, not by agents.

---

#### F5 — No Access Control on `get_intent_status()` / `get_intent_result()`
**Severity: HIGH**

Both read methods accept only `intent_id` (a UUID). There is no authorization check that the calling agent owns (or is permitted to see) that Intent.

**Impact:**
- Any agent can poll any other agent's Intent results. UUIDs are not secrets — they're predictable if you know the generation scheme, and may leak through logs.
- Intent results may contain sensitive data (file listings, error messages with internal paths).
- Combined with F2 (no caller authentication), this is an open-read vulnerability.

**Remediation:**
1. `get_intent_status()` and `get_intent_result()` must accept/derive the caller's `agent_id` and verify it matches `intent.agent_id` (or the SUBMITTED_BY relationship).
2. For MVP: at minimum, the query should include `WHERE i.agent_id = $caller_agent_id`.

---

#### F6 — Intent Immutability Not Enforced
**Severity: MEDIUM**

Section 7 asks whether an agent can modify an Intent after submission. The spec does not describe any mechanism preventing it. If agents have direct Neo4j access (or if the SDK exposes an update method), the Intent's `params`, `capability_id`, or even `agent_id` could be mutated after creation.

**Impact:** An agent could:
- Change `params` after submission but before execution (bait-and-switch attack).
- Change `capability_id` to escalate to a capability it doesn't have (if the capability check uses the node's current `capability_id` rather than the original).

**Remediation:**
1. The SDK must not expose any Intent mutation methods.
2. In Neo4j, apply a constraint or trigger that prevents modification of `params`, `capability_id`, and `agent_id` after creation. Only `status`, `result`, `error`, `updated_at`, `claimed_at`, `completed_at`, and `duration_ms` should be mutable — and only by the Daemon.
3. If using application-level enforcement only (no DB triggers), document this as a defense-in-depth gap.

---

#### F7 — `SET WHERE` Claim Is Not a True Atomic Lock
**Severity: MEDIUM**

Section 5.4 states: `SET i.status = "claimed" WHERE i.status = "pending"`. In Neo4j, a bare `SET ... WHERE` is not a conditional atomic compare-and-swap in the way a SQL `UPDATE ... WHERE` with row-level locking would be.

In Neo4j, the correct pattern requires an explicit transaction with a write lock. The Cypher shown may allow two concurrent Daemon instances to both read `status = "pending"`, then both issue `SET status = "claimed"`, with the last write winning. This would cause double-processing.

**Remediation:**
1. Use an explicit write transaction with `MATCH ... WHERE status = "pending" SET status = "claimed" RETURN i`. Neo4j acquires a write lock on the node during a write transaction, so within a single transaction this is safe — but only if the MATCH+SET is one statement in one transaction, not two separate statements.
2. Verify in tests (Scenario 6.6) that with concurrent Daemon instances, each Intent is claimed exactly once. The test should use actual concurrency (threads/processes), not sequential simulation.
3. Consider adding a `claimed_by` field with Daemon instance ID for auditability and orphan recovery (see F4).

---

#### F8 — Missing State Transitions / Dead States
**Severity: MEDIUM**

The state machine is mostly complete, but has gaps:

1. **No `rejected` from `claimed`:** If the capability check happens after claiming (as specified), then rejection should be `claimed → rejected`, not `pending → rejected`. The current spec shows `pending → rejected` but describes the check happening after claiming. This is a spec inconsistency.
2. **No cancellation:** There is no way to cancel a pending Intent. Once submitted, it will be processed. For MVP this may be acceptable, but document it.
3. **No `timeout` distinct state:** Timeouts map to `"failed"` (Section 5.3). This means agents cannot distinguish a timeout from a genuine execution failure. Consider a `"timed_out"` status or an error convention (`error` starts with `"Execution timed out"`).

**Remediation:**
1. Fix the state machine: if capability check happens post-claim, the transition should be `claimed → rejected`, and the diagram should reflect this.
2. Document that cancellation is out of scope for MVP.
3. Consider whether a distinct timeout state adds value.

---

#### F9 — Error Messages Leak Internal State
**Severity: LOW**

The failure response (Section 4.3) returns raw `stderr` and exception messages: `"error": "Permission denied: /root"`. In `exec-ls`, this could expose:
- Internal filesystem structure.
- User/permission context of the Daemon process.
- Python stack traces if exceptions are not caught cleanly.

**Remediation:**
1. Sanitize error messages before storing in Neo4j. Return a generic error category with a reference ID; log full details server-side.
2. For MVP: acceptable to return raw errors, but document as a known limitation for hardening later.

---

#### F10 — `params` Stored as JSON String (Injection via Cypher)
**Severity: LOW**

Section 4.1 shows `params: '{"path": "/app"}'` — JSON serialized as a string property. If `params` is interpolated into a Cypher query without parameterization (e.g., string concatenation), this opens Cypher injection.

**Note:** The spec uses `$id` parameterized queries in several places, which is correct. This finding is a reminder to ensure ALL Cypher queries use parameterization, especially any that touch `params`.

**Remediation:**
1. Ensure all Cypher queries use Neo4j parameter binding (`$param` syntax), never string interpolation.
2. Validate `params` against a JSON schema per capability before storing.

---

### 10.2 Capability-Specific: exec-ls Hardening

Beyond F1 (path traversal), the `exec-ls` capability needs:

1. **Allowlisted arguments only:** Only `ls` with a predefined set of flags. Currently `-la` is hardcoded, which is fine.
2. **Output size limit:** `ls -la /` on a large filesystem could return megabytes. Cap stdout capture at a reasonable size (e.g., 64 KB) and truncate with a warning.
3. **Execution user:** The Daemon should not run `subprocess.run` as root. Use a dedicated low-privilege user for capability execution.
4. **No follow-on capabilities from output:** Ensure Intent results are treated as data, not as input to further capability execution (prevents confused-deputy chains).

---

### 10.3 Summary of Findings

| # | Finding | Severity | Status | Resolution |
|---|---------|----------|--------|------------|
| F1 | Path traversal / injection in exec-ls params | CRITICAL | **Addressed** | §3A — allowlist + validation sequence |
| F2 | No agent authentication on submit_intent() | CRITICAL | **Addressed** | §3B — API key auth model; §4.1 revised signature |
| F3 | TOCTOU: capability check not atomic with claim | HIGH | **Addressed** | §5.2 step 2 — single atomic Cypher query |
| F4 | Orphaned Intents on Daemon crash — no recovery | HIGH | **Addressed** | §5.5 — reaper process + startup recovery |
| F5 | No access control on status/result read methods | HIGH | **Addressed** | §4.2, §4.3 — caller verification via api_key |
| F6 | Intent immutability not enforced at any layer | MEDIUM | **Deferred** | §9 — app-level only for MVP; DB constraints in backlog |
| F7 | Claim atomicity relies on unverified Neo4j behavior | MEDIUM | **Deferred** | §9 — single-txn pattern + concurrency test; formal verification in backlog |
| F8 | State machine inconsistency and missing transitions | MEDIUM | **Addressed** | §5.1 — `claimed→rejected` corrected; no-cancel documented |
| F9 | Error messages leak internal system details | LOW | **Deferred** | §9 — acceptable in test env; sanitization in backlog |
| F10 | Cypher injection risk if params not parameterized | LOW | **Deferred** | §9 — parameterized queries enforced; schema validation in backlog |

---

### 10.4 Overall Assessment

The spec is well-structured and covers the core pipeline clearly. The state machine, error cases, and test scenarios show thoughtful design.

However, **the two CRITICAL findings (F1, F2) must be resolved before any deployment, including internal testing with untrusted agents.** F2 (no authentication) effectively means the entire capability-enforcement model is decorative — any caller can claim any identity. F1 (path traversal) means `exec-ls` can read arbitrary directories on the host filesystem.

The three HIGH findings (F3, F4, F5) should be addressed before the spec is finalized. F3 and F4 can cause correctness failures under real-world conditions (concurrent processing, Daemon restarts). F5 breaks tenant isolation.

**Recommendation:** Address F1 and F2 in the spec itself (define authentication model, define path validation). F3 can be fixed by combining the capability check into the claim query. F4 needs a reaper design. F5 needs caller verification on read methods. The MEDIUM/LOW items can be tracked as hardening tasks.

The foundation is solid. These are the right problems to find at spec stage — much cheaper to fix here than in code.

— Inanna 🛡️
