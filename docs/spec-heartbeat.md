# Interface Specification: Agent Heartbeat System

**Version:** 1.1 (Post-Review Revision)
**Author:** Dione
**Revised:** 2026-04-14 (B2.5 — Security Revision addressing Inanna's security review)
**Status:** Revised — security findings addressed

---

## 1. Purpose

Enable agents to signal liveness to the Hassaleh Daemon. The Daemon uses heartbeat data to track which agents are active, detect failures, and trigger alerts via the existing Rule engine.

## 2. Scope

- SDK method: `heartbeat(api_key)` *(Revised: B2.5, addresses F1 — agent_id derived server-side)*
- Neo4j Agent node updates
- Agent lifecycle transitions (pending → active → stale → inactive → disabled)
- Replay protection via chained `heartbeat_token` *(Added: B2.5, addresses F2)*
- Server-side rate limiting *(Added: B2.5, addresses F4)*
- Rule: `agent-health-check` (already seeded, needs implementation binding)
- OpenClaw Cron integration for validated heartbeats *(Revised: B2.5, addresses F3)*

## 3. SDK Interface

### 3.1 `heartbeat(api_key, heartbeat_token=None) -> dict` *(Revised: B2.5, addresses F1, F2, F4)*

**Input:**
```python
api_key: str              # Pre-shared API key; agent_id derived server-side (see spec-mvp-test.md §3B)
heartbeat_token: str|None # Token from previous heartbeat response (None for first heartbeat)
```

**Authentication Flow** *(Added: B2.5, addresses F1)*

1. Caller provides `api_key` to the SDK method.
2. SDK hashes the key and queries: `MATCH (a:Agent) WHERE a.api_key_hash = $hash RETURN a.id AS agent_id`
3. If no match → `AuthenticationError("Invalid API key")`.
4. `agent_id` is now trusted (derived from DB, not from caller). This is the same model as `submit_intent()` (spec-mvp-test.md §3B).

**Replay Protection** *(Added: B2.5, addresses F2)*

The server issues a `heartbeat_token` with each successful heartbeat response. The next heartbeat must include this token, creating a chained proof-of-liveness:

1. **First heartbeat** (agent in `pending` state): `heartbeat_token` parameter is `None`. The server generates an initial token.
2. **Subsequent heartbeats**: `heartbeat_token` must match the value stored on the Agent node (`a.heartbeat_token`). If it does not match → `HeartbeatTokenMismatchError("Invalid heartbeat token — possible replay or concurrent sender")`.
3. On each successful heartbeat, the server generates a new token (`secrets.token_urlsafe(32)`) and returns it. The agent must use this token for its next heartbeat.
4. **Token reset**: If an agent transitions to `stale` or `inactive`, the stored `heartbeat_token` is cleared (`null`). The next heartbeat may pass `None` (like a first heartbeat), re-establishing the chain.

This ensures that only the process holding the current token — i.e., the process that sent the last successful heartbeat — can send the next one. A replayed heartbeat will carry a stale token and be rejected.

**Rate Limiting** *(Added: B2.5, addresses F4)*

The server-side Cypher enforces a minimum interval of 60 seconds between heartbeats per agent. Heartbeats arriving more frequently are silently accepted but perform no write — the previous heartbeat data is returned unchanged.

**Returns:**
```python
{
    "agent_id": "dione",
    "lifecycle": "active",
    "last_heartbeat": "2026-04-14T14:00:00Z",
    "previous_heartbeat": "2026-04-14T12:00:00Z",
    "heartbeat_count": 42,
    "heartbeat_token": "Uf3kL9x..."  # Use this in the next heartbeat call
}
```

**Neo4j state after call:** *(Revised: B2.5, addresses F4, F6)*
```cypher
MATCH (a:Agent {id: $agent_id})
WHERE a.lifecycle <> "disabled"
  AND (a.last_heartbeat IS NULL
       OR a.last_heartbeat < datetime() - duration("PT60S"))
SET a.last_heartbeat = datetime(),
    a.previous_heartbeat = a.last_heartbeat,
    a.heartbeat_count = coalesce(a.heartbeat_count, 0) + 1,
    a.lifecycle = CASE WHEN a.lifecycle IN ["pending", "stale", "inactive"] THEN "active" ELSE a.lifecycle END,
    a.heartbeat_token = $new_token,
    a.heartbeat_source = $source
```

**Behavior notes:**
- The `WHERE a.lifecycle <> "disabled"` guard prevents heartbeats from resurrecting disabled agents (F6).
- The `WHERE ... last_heartbeat < datetime() - duration("PT60S")` clause enforces rate limiting (F4). If the condition does not match, no write occurs — the heartbeat is silently deduplicated and the current state is returned.
- The `CASE` on lifecycle ensures that an already-`active` agent stays `active` without a pointless state "transition" (supports F5 race condition mitigation).
- `heartbeat_source` records whether this heartbeat originated from the agent process or a cron validator (see §6).

**Error cases:**
- Invalid or unknown API key → `AuthenticationError` *(F1)*
- Agent not found → `AgentNotFoundError`
- Agent in `disabled` state → `AgentDisabledError("Agent is disabled — heartbeat rejected")` *(F6)*
- Heartbeat token mismatch → `HeartbeatTokenMismatchError` *(F2)*
- Neo4j connection failed → `ConnectionError`

### 3.2 CLI Command: `hassaleh heartbeat` *(Revised: B2.5, addresses F1, F7)*

Wrapper around `sdk.heartbeat()`. Used by agents and (with validation) by OpenClaw cron jobs.

```bash
# Credentials sourced from environment or config file (see §6)
hassaleh heartbeat
```

The CLI reads `HASSALEH_API_KEY` from the environment. The `agent_id` is derived server-side from the key — it is not a CLI argument. The CLI also reads and writes the `heartbeat_token` to a local state file (`~/.hassaleh/heartbeat_token`) to maintain the chain across invocations.

**Exit codes:**
- 0: heartbeat sent successfully
- 1: agent not found, disabled, or connection error
- 2: authentication failure (invalid API key)
- 3: heartbeat token mismatch (possible replay or concurrent sender)

## 4. Agent Lifecycle State Machine *(Revised: B2.5, addresses F6)*

```
pending → active (first heartbeat received)
active → active (heartbeat within threshold)
active → stale (no heartbeat for >2 hours)
stale → active (heartbeat received, token chain reset)
stale → inactive (no heartbeat for >24 hours)
inactive → active (heartbeat received, token chain reset)
disabled [terminal] (admin action only — heartbeats rejected)
```

**The `disabled` state** *(Added: B2.5, addresses F6)*:
- `disabled` is a terminal state that cannot be exited via heartbeat. Heartbeats from disabled agents return `AgentDisabledError`.
- Only admin-level operations (direct Neo4j mutation or a future admin SDK method) can set or unset `disabled`. The heartbeat SDK method cannot transition an agent to or from `disabled`.
- Use case: decommissioned agents, compromised agents, agents under investigation. Prevents zombie resurrection via stale cron jobs or replay attacks.

**Thresholds (configurable via DaemonConfig node):**
| Threshold | Default | Meaning |
|-----------|---------|---------|
| `heartbeat_stale_seconds` | 7200 (2h) | Agent marked stale |
| `heartbeat_inactive_seconds` | 86400 (24h) | Agent marked inactive |
| `heartbeat_min_interval_seconds` | 60 (1m) | Minimum interval between heartbeats *(Added: B2.5)* |

## 5. Rule Integration: agent-health-check

The existing seeded rule `agent-health-check` should evaluate:

```
EVERY 60s:
  MATCH (a:Agent)
  WHERE a.lifecycle = "active"
    AND a.lifecycle <> "disabled"
    AND a.last_heartbeat < datetime() - duration("PT2H")
  SET a.lifecycle = "stale",
      a.heartbeat_token = null
  ALERT "Agent {a.name} has gone stale (no heartbeat for >2h)"
```

And:
```
EVERY 300s:
  MATCH (a:Agent)
  WHERE a.lifecycle = "stale"
    AND a.last_heartbeat < datetime() - duration("P1D")
  SET a.lifecycle = "inactive",
      a.heartbeat_token = null
  ALERT "Agent {a.name} is now inactive (no heartbeat for >24h)"
```

**Note:** On transition to `stale` or `inactive`, the `heartbeat_token` is cleared. This allows an agent that recovers to re-establish the token chain by sending a heartbeat with `heartbeat_token=None`, similar to the first-heartbeat flow. *(Added: B2.5, addresses F2)*

## 6. OpenClaw Cron Integration *(Revised: B2.5, addresses F3, F7)*

### 6.1 Design Principle

A heartbeat must prove agent liveness. An unconditional cron job that heartbeats regardless of agent state defeats this purpose — it becomes a life-support machine that masks failures (F3).

**Two valid integration patterns:**

### 6.2 Pattern A: Agent-Originated Heartbeats (Recommended)

The heartbeat originates from within the agent's own process as a periodic async task. If the process dies, the heartbeat stops — that's the entire point.

```python
# Inside the agent's event loop / main process:
async def heartbeat_loop(sdk, api_key):
    token = None
    while True:
        result = await sdk.heartbeat(api_key, heartbeat_token=token)
        token = result["heartbeat_token"]
        await asyncio.sleep(1800)  # 30 minutes
```

This is the preferred pattern. The `heartbeat_source` is recorded as `"agent"`.

### 6.3 Pattern B: Cron as Health Validator (If Cron Is Retained)

If cron integration is retained for operational reasons (e.g., agents that don't have long-running processes), the cron job **must validate agent health before heartbeating**. The cron is a *validator*, not a *forger*.

```bash
# Cron job that validates before heartbeating
# Credentials from environment — NEVER on the command line
export HASSALEH_API_KEY="$HASSALEH_API_KEY"  # Set in cron environment

# Step 1: Validate agent is actually responsive
if hassaleh agent-check dione --timeout 10; then
    # Step 2: Send heartbeat only if agent responded
    hassaleh heartbeat --source cron
else
    echo "Agent dione failed health check — skipping heartbeat"
    exit 1
fi
```

**Cron setup (credentials via environment):**
```bash
openclaw cron add \
  --name "hassaleh-heartbeat-dione" \
  --every 30m \
  --agent worker-opus \
  --message "Validate and heartbeat: HASSALEH_API_KEY from env, hassaleh agent-check dione && hassaleh heartbeat --source cron"
```

### 6.4 Heartbeat Source Field *(Added: B2.5, addresses F3)*

Every heartbeat records its origin:

| `heartbeat_source` | Meaning |
|---------------------|---------|
| `"agent"` | Heartbeat originated from within the agent's own process |
| `"cron"` | Heartbeat originated from a cron job after health validation |

The Rule engine can use this field to apply different trust levels:
- Agent-originated heartbeats are full proof-of-liveness.
- Cron-originated heartbeats prove the agent responded to a health check at that time, but are weaker evidence (the health check and heartbeat are not atomic).

### 6.5 Credential Handling *(Added: B2.5, addresses F7)*

**Plaintext passwords and API keys MUST NOT appear in command-line arguments, cron definitions, or shell history.** This includes Neo4j credentials and Hassaleh API keys.

**Required approach:**
- `HASSALEH_API_KEY`: Set as an environment variable in the cron environment or sourced from a config file with restricted permissions (`chmod 0600`).
- Neo4j credentials: The SDK reads `NEO4J_URI`, `NEO4J_USER`, and `NEO4J_PASSWORD` from environment variables. The CLI uses the same environment variables or a config file at `~/.hassaleh/config` (permissions `0600`).
- Command-line `--password` flags are **not acceptable**, even in development.

## 7. Test Scenarios

### 7.1 First Heartbeat
1. Agent with `lifecycle: "pending"`, `last_heartbeat: null`
2. Call `heartbeat(api_key, heartbeat_token=None)`
3. Assert `lifecycle = "active"`
4. Assert `last_heartbeat` is set
5. Assert `heartbeat_count = 1`
6. Assert `heartbeat_token` is returned (non-null)

### 7.2 Subsequent Heartbeat
1. Agent already active with known `last_heartbeat` and `heartbeat_token`
2. Call `heartbeat(api_key, heartbeat_token=previous_token)`
3. Assert `previous_heartbeat = old_last_heartbeat`
4. Assert `last_heartbeat` updated
5. Assert `heartbeat_count` incremented
6. Assert new `heartbeat_token` differs from previous

### 7.3 Stale Detection
1. Set agent `last_heartbeat` to 3 hours ago
2. Trigger rule evaluation
3. Assert `lifecycle` changed to `"stale"`
4. Assert ALERT was triggered
5. Assert `heartbeat_token` is cleared (null) *(Added: B2.5)*

### 7.4 Recovery from Stale
1. Agent with `lifecycle: "stale"`, `heartbeat_token: null`
2. Call `heartbeat(api_key, heartbeat_token=None)` (token chain resets)
3. Assert `lifecycle = "active"`
4. Assert new `heartbeat_token` is issued

### 7.5 Inactive Detection
1. Set agent `last_heartbeat` to 25 hours ago, `lifecycle: "stale"`
2. Trigger rule evaluation
3. Assert `lifecycle` changed to `"inactive"`
4. Assert ALERT was triggered

### 7.6 Unknown Agent
1. Call `heartbeat(valid_api_key_for_nonexistent_agent)` — should not be reachable if key hashes are unique
2. Call `heartbeat(invalid_api_key)`
3. Assert `AuthenticationError`

### 7.7 Concurrent Heartbeats
1. Send heartbeats for 4 agents simultaneously (each with own API key)
2. Assert all agents updated correctly
3. Assert no data corruption

### 7.8 Authentication Required *(Added: B2.5, addresses F1)*
1. Call `heartbeat()` with no API key → `AuthenticationError`
2. Call `heartbeat(invalid_key)` → `AuthenticationError`
3. Assert no Agent node was modified

### 7.9 Heartbeat Token Chain *(Added: B2.5, addresses F2)*
1. Send first heartbeat → get `token_1`
2. Send second heartbeat with `token_1` → get `token_2` (success)
3. Replay first heartbeat with `token_1` again → `HeartbeatTokenMismatchError`
4. Assert Agent state unchanged after replay attempt

### 7.10 Disabled Agent Rejection *(Added: B2.5, addresses F6)*
1. Set agent `lifecycle: "disabled"` via admin operation
2. Call `heartbeat(api_key)` for that agent
3. Assert `AgentDisabledError`
4. Assert agent remains `disabled` — lifecycle was NOT changed
5. Assert `last_heartbeat` was NOT updated

### 7.11 Rate Limiting *(Added: B2.5, addresses F4)*
1. Send heartbeat for agent → success, note `last_heartbeat = T1`
2. Immediately send another heartbeat (within 60s)
3. Assert response is successful (no error) but `last_heartbeat` is still `T1` (no write occurred)
4. Assert `heartbeat_count` was NOT incremented
5. Wait 60 seconds, send another heartbeat → `last_heartbeat` updated

### 7.12 Cron-Validated Heartbeat *(Added: B2.5, addresses F3)*
1. Send heartbeat with `source: "cron"`
2. Assert `heartbeat_source = "cron"` on Agent node
3. Send heartbeat with `source: "agent"`
4. Assert `heartbeat_source = "agent"` (overwritten)

## 8. Security Considerations *(Originally for Inanna's review — now resolved)*

- [x] Can agent A send a heartbeat for agent B? → **Blocked by §3.1** (API key auth; agent_id derived server-side) *(F1)*
- [x] Can a heartbeat be replayed? → **Blocked by §3.1** (chained heartbeat_token) *(F2)*
- [x] Can the heartbeat mechanism be used for timing attacks? → **Low risk.** Heartbeat timing is not security-sensitive data. Rate limiting (§3.1) reduces observability of patterns.
- [x] What happens if heartbeat floods the graph? → **Rate limited** (§3.1, 60s minimum interval in Cypher) *(F4)*
- [x] Should heartbeat require a signed token or session? → **Yes** (§3.1, API key + chained heartbeat_token) *(F1, F2)*

---

## 9. Security Review — Inanna

**Reviewer:** Inanna 🛡️
**Date:** 2026-04-14
**Spec Version:** 1.0 (Draft)
**Context:** This review references the authentication model established in the MVP Test Spec (spec-mvp-test.md §3B) and the findings from the A2 security review.

---

### 9.1 Findings

#### F1 — Agent Impersonation: No Authentication on `heartbeat()`
**Severity: CRITICAL**

`heartbeat(agent_id)` accepts `agent_id` as a plain string (§3.1). Any caller with SDK access can send heartbeats as any registered agent. This is the same class of vulnerability as A2-F2 (Intent impersonation), but arguably worse because:

- **It bypasses the Intent pipeline entirely.** The MVP test spec added API key authentication to `submit_intent()` (§3B). Heartbeat writes directly to Neo4j via `SET a.lifecycle = "active"`, circumventing all Daemon-mediated access controls.
- **It keeps a compromised/revoked agent appearing alive.** If an agent is decommissioned or compromised, an attacker can heartbeat on its behalf indefinitely, preventing stale/inactive detection.
- **It pollutes operational data.** `heartbeat_count`, `last_heartbeat`, and `previous_heartbeat` become untrustworthy for forensics or incident response.

**Remediation:**
1. Heartbeat must require the same API key authentication as `submit_intent()`. The SDK method should be `heartbeat(api_key)` with `agent_id` derived server-side from the key hash.
2. Alternatively, route heartbeats through the Intent system as a `heartbeat` action type, inheriting existing auth and audit trails.
3. At minimum for MVP: document as a known critical limitation; ensure the SDK is not network-reachable from untrusted processes.

---

#### F2 — Replay Attack: No Freshness Check on Heartbeats
**Severity: HIGH**

A heartbeat has no nonce, timestamp, or sequence number. The operation is purely idempotent: calling `heartbeat("dione")` at any time sets `last_heartbeat = datetime()` and `lifecycle = "active"`.

**Attack scenario:**
1. Attacker captures a valid heartbeat call (or simply knows the agent_id).
2. Agent goes down legitimately.
3. Attacker replays `heartbeat("dione")` every 30 minutes.
4. The agent appears permanently alive despite being dead.
5. The stale→inactive detection (§4, §5) never triggers; alerts never fire.

This is especially dangerous combined with the OpenClaw Cron integration (§6) — a cron job continues heartbeating even if the agent process it's supposed to monitor has crashed.

**Remediation:**
1. Heartbeat should include a proof-of-liveness: a nonce or session token that the agent must regenerate each time from its running process.
2. For the cron integration specifically: the cron job should verify the agent process is actually running (e.g., check PID file, health endpoint) before sending the heartbeat, rather than unconditionally sending one.
3. Consider a `heartbeat_token` that rotates on each heartbeat response — the next heartbeat must include the previous token (chained proof-of-liveness).

---

#### F3 — Cron Integration Defeats Stale Detection
**Severity: HIGH**

Section 6 proposes a cron job that unconditionally sends heartbeats every 30 minutes:
```bash
hassaleh heartbeat dione
```

This design fundamentally undermines the purpose of the heartbeat system. The heartbeat is supposed to prove the agent is alive. But if a cron job sends it regardless of agent state, then:

- A crashed agent is kept "active" indefinitely.
- A hung agent (alive but non-responsive) is masked.
- The stale detection rule (§5) never triggers for cron-heartbeated agents.

The cron job becomes an always-on life-support machine that makes the health monitoring system useless.

**Remediation:**
1. **Agent-side heartbeats only.** The heartbeat must originate from within the agent's own process (e.g., a periodic async task), not from an external cron. If the process dies, the heartbeat stops — that's the entire point.
2. If cron integration is retained for operational reasons, the cron job must perform a real health check (HTTP probe, process existence check, SDK query for recent activity) before sending the heartbeat. The cron should be a *validator*, not a *forger*.
3. Document that `openclaw cron` heartbeats are a monitoring convenience, not a substitute for agent-originated liveness signals. Mark the cron-originated heartbeats distinctly (e.g., `heartbeat_source: "cron"` vs `"agent"`) so the Rule engine can treat them differently.

---

#### F4 — Heartbeat Flood / Neo4j DoS via Write Amplification
**Severity: HIGH**

There is no rate limiting on heartbeat calls. The Cypher in §3.1 performs a write operation on every call:
```cypher
SET a.last_heartbeat = datetime(),
    a.previous_heartbeat = a.last_heartbeat,
    a.heartbeat_count = coalesce(a.heartbeat_count, 0) + 1
```

A malicious or buggy agent calling `heartbeat()` in a tight loop would:
- Generate sustained Neo4j write I/O (each call is a write transaction with WAL flush).
- Monotonically increase `heartbeat_count` (integer overflow after ~2^63 writes, but performance degrades long before).
- Contend for write locks on the Agent node, blocking legitimate operations (Intent claims, Rule evaluations, other heartbeats).
- Fill Neo4j transaction logs, potentially exhausting disk.

**Remediation:**
1. **Server-side rate limiting:** Reject heartbeats arriving more frequently than a minimum interval (e.g., 60 seconds per agent). Use a simple in-memory cooldown or a `MIN_HEARTBEAT_INTERVAL` check:
   ```cypher
   MATCH (a:Agent {id: $agent_id})
   WHERE a.last_heartbeat IS NULL
      OR a.last_heartbeat < datetime() - duration("PT60S")
   SET a.last_heartbeat = datetime(), ...
   ```
   If the WHERE clause doesn't match, no write occurs — the heartbeat is silently deduplicated.
2. **Cap `heartbeat_count`:** Use modular arithmetic or reset periodically. The absolute count is less useful than recency.
3. **Global throughput limit:** If many agents exist, consider batching heartbeats or using a write-coalescing layer.

---

#### F5 — Race Condition: Lifecycle Transition Without Atomic Guard
**Severity: MEDIUM**

The heartbeat Cypher (§3.1) unconditionally sets `lifecycle = "active"`:
```cypher
SET a.lifecycle = "active"
```

The Rule engine (§5) concurrently evaluates:
```cypher
WHERE a.lifecycle = "active" AND a.last_heartbeat < datetime() - duration("PT2H")
SET a.lifecycle = "stale"
```

**Race scenario:**
1. Rule engine reads agent: `lifecycle = "active"`, `last_heartbeat` = 2.5 hours ago → decides to set stale.
2. Between the Rule's read and write, a heartbeat arrives: sets `lifecycle = "active"`, updates `last_heartbeat` to now.
3. Rule engine writes: sets `lifecycle = "stale"`.
4. Result: agent is marked stale despite having just heartbeated. The `last_heartbeat` says "just now" but `lifecycle` says "stale" — an inconsistent state.

Neo4j write transactions do acquire node-level locks, so this depends on whether the Rule and heartbeat execute as separate transactions (likely yes, since they're different code paths).

**Remediation:**
1. The Rule's staleness Cypher must re-check the condition within the write transaction:
   ```cypher
   MATCH (a:Agent)
   WHERE a.lifecycle = "active"
     AND a.last_heartbeat < datetime() - duration("PT2H")
   SET a.lifecycle = "stale"
   ```
   In Neo4j, a single-statement write transaction acquires a lock on `a` at MATCH time, ensuring the WHERE condition holds through the SET. Verify this holds in the Hassaleh Neo4j version (5.x).
2. Heartbeat should similarly guard: only set `lifecycle = "active"` if the agent is not in a terminal state (e.g., don't resurrect a deliberately `disabled` agent — see F6).

---

#### F6 — No Terminal/Administrative States — Zombie Resurrection
**Severity: MEDIUM**

The state machine (§4) allows `inactive → active` on any heartbeat. There is no administrative override state (e.g., `disabled`, `decommissioned`, `suspended`) that prevents reactivation.

**Impact:**
- An admin who intentionally deactivates an agent cannot prevent it from being reactivated by a stale cron job or a replay attack.
- A compromised agent that is "killed" by setting it inactive will come back alive on the next heartbeat.
- There is no way to express "this agent should never be active again" in the state machine.

**Remediation:**
1. Add a `disabled` state that is a terminal state — heartbeats from disabled agents should be rejected (return an error, not silently ignored).
2. The heartbeat Cypher should guard: `WHERE a.lifecycle <> "disabled"`.
3. Only admin-level operations (not SDK calls) should be able to set or unset `disabled`.

---

#### F7 — Credentials Exposed in Cron Command Line
**Severity: MEDIUM**

Section 6 shows:
```bash
hassaleh --uri bolt://localhost:7690 --user neo4j --password hassaleh heartbeat dione
```

The password appears in:
- The cron job definition (stored in OpenClaw's cron system, potentially logged).
- `/proc/<pid>/cmdline` on Linux (visible to any user on the system via `ps aux`).
- Shell history if entered manually.

**Remediation:**
1. Use environment variables instead: `NEO4J_PASSWORD` (the SDK already supports this — §3.1 of the code).
2. The cron example should be:
   ```bash
   hassaleh heartbeat dione
   ```
   with credentials sourced from environment or a config file with restricted permissions (0600).
3. Document that command-line passwords are not acceptable, even in development.

---

#### F8 — No Audit Trail for Lifecycle Transitions
**Severity: LOW**

Heartbeats overwrite `last_heartbeat` and `previous_heartbeat`, retaining only the last two timestamps. The `heartbeat_count` increments but provides no timeline. Lifecycle transitions (`stale → active`, `inactive → active`) are not logged.

**Impact:**
- No forensic record of when an agent was stale, for how long, or how often it recovered.
- Cannot distinguish between "agent had a brief network blip" and "agent was dead for 23 hours then came back."
- Cannot detect patterns of suspicious reactivation (e.g., an agent that goes stale and is immediately reactivated repeatedly — possible indicator of a spoofed heartbeat).

**Remediation:**
1. Log lifecycle transitions as separate nodes or relationship events:
   ```cypher
   CREATE (:LifecycleEvent {
       agent_id: $id, from: "stale", to: "active",
       timestamp: datetime(), source: "heartbeat"
   })
   ```
2. For MVP: at minimum, add a `lifecycle_changed_at` timestamp property on the Agent node. This costs one extra property per agent and provides basic forensics.

---

#### F9 — Health-Check Rule Timing Allows Extended Blind Spots
**Severity: LOW**

The stale-detection Rule runs every 60 seconds (§5), but the stale threshold is 2 hours. This means an agent could be dead for up to 2 hours before any alert fires. For the inactive threshold (24 hours, checked every 300 seconds), the delay is less significant.

Whether 2 hours is acceptable depends on the operational context, but the spec should explicitly acknowledge the detection latency and make the Rule interval configurable alongside the threshold.

**Remediation:**
1. Document the detection latency explicitly: "An agent failure will be detected within `heartbeat_stale_seconds + rule_interval` (worst case: 2h + 60s)."
2. Consider an `expected_heartbeat_interval` per agent (e.g., 30 minutes for cron-heartbeated agents) and alert when `now - last_heartbeat > expected_heartbeat_interval × 2`. This provides earlier warning without changing the lifecycle transitions.

---

### 9.2 Answers to §8 Security Questions

| Question | Answer |
|----------|--------|
| Can agent A heartbeat for agent B? | **Yes — F1.** No authentication; `agent_id` is caller-supplied. |
| Can a heartbeat be replayed? | **Yes — F2.** No nonce, token, or freshness mechanism. |
| Timing attacks? | **Low risk.** Heartbeat timing is not security-sensitive data. However, observing heartbeat patterns could reveal agent schedules. |
| Heartbeat flooding the graph? | **Yes — F4.** No rate limiting; each call is a Neo4j write. |
| Should heartbeat require a signed token? | **Yes — F1.** Must use the API key model from spec-mvp-test.md §3B, or route through the Intent system. |

---

### 9.3 Summary of Findings

| # | Finding | Severity | Remediation Priority |
|---|---------|----------|---------------------|
| F1 | No authentication — agent impersonation via heartbeat | CRITICAL | **Must fix before implementation** |
| F2 | No replay protection — dead agents kept alive | HIGH | **Must fix before implementation** |
| F3 | Cron integration defeats stale detection entirely | HIGH | **Must fix before implementation** |
| F4 | No rate limiting — heartbeat flood can DoS Neo4j | HIGH | **Should fix in implementation** |
| F5 | Race condition between heartbeat and Rule engine | MEDIUM | **Should fix in implementation** |
| F6 | No terminal state — disabled agents can be resurrected | MEDIUM | **Should fix in implementation** |
| F7 | Neo4j password exposed in cron command line | MEDIUM | **Easy fix — update examples** |
| F8 | No audit trail for lifecycle transitions | LOW | **Track for hardening** |
| F9 | 2-hour detection latency not documented | LOW | **Track for hardening** |

---

### 9.4 Overall Assessment

The heartbeat spec is well-structured and the state machine design is sound in principle. The integration with the Rule engine for automated staleness detection is the right architectural choice.

However, **F1 (no authentication) is a CRITICAL gap that must be closed before implementation.** This is a regression from the progress made in the MVP test spec, where A2-F2 was addressed by adding API key authentication to `submit_intent()`. The heartbeat endpoint bypasses that entire auth model by writing directly to Neo4j. This creates a backdoor that undermines the security posture of the system as a whole.

**F3 (cron integration) is the most architecturally concerning finding.** A heartbeat system exists to detect agent failures. If an external process unconditionally sends heartbeats regardless of agent state, the system cannot detect failures — it can only detect cron failures. This is a design-level issue, not a bug: the cron integration as specified makes the heartbeat system unreliable by construction.

**Recommendations for Dione:**
1. Extend the API key auth model from spec-mvp-test.md to cover heartbeats (F1). This is non-negotiable.
2. Redesign the cron integration: cron should validate agent health before heartbeating, or heartbeats should be agent-originated only (F3).
3. Add a minimum heartbeat interval in the server-side Cypher to prevent flooding (F4). This is a one-line fix.
4. Add a `disabled` lifecycle state that heartbeats cannot override (F6).
5. Remove the plaintext password from the cron examples (F7). Use env vars.

The foundation is solid. The state machine, threshold configurability, and Rule integration are well-designed. These findings are about closing the gap between "monitoring system" and "trustworthy monitoring system" — ensuring the heartbeat actually proves what it claims to prove.

— Inanna 🛡️

---

## 10. Revision Notes — B2.5 *(Added: 2026-04-14)*

This section documents changes made in version 1.1 in response to Inanna's security review (§9).

### 10.1 Findings Addressed

| # | Finding | Severity | Status | Resolution |
|---|---------|----------|--------|------------|
| F1 | No authentication — agent impersonation | CRITICAL | **Addressed** | §3.1 — API key auth model from spec-mvp-test.md §3B applied to heartbeats; `agent_id` derived server-side from key hash |
| F2 | No replay protection — dead agents kept alive | HIGH | **Addressed** | §3.1 — chained `heartbeat_token` rotated on each successful heartbeat; token cleared on stale/inactive transition |
| F3 | Cron defeats stale detection | HIGH | **Addressed** | §6 — redesigned: agent-originated heartbeats recommended (§6.2); cron retained only as health validator with mandatory pre-check (§6.3); `heartbeat_source` field distinguishes origin (§6.4) |
| F4 | No rate limiting — heartbeat flood | HIGH | **Addressed** | §3.1 — 60s minimum interval enforced in server-side Cypher WHERE clause; no-write deduplication |
| F6 | No terminal state — zombie resurrection | MEDIUM | **Addressed** | §4 — `disabled` terminal state added; heartbeats return `AgentDisabledError`; only admin operations can set/unset |
| F7 | Credentials exposed in cron command line | MEDIUM | **Addressed** | §3.2, §6.5 — all examples use environment variables; plaintext passwords explicitly prohibited |

### 10.2 Findings Deferred

| # | Finding | Severity | MVP Mitigation | Hardening Target |
|---|---------|----------|----------------|------------------|
| F5 | Race condition between heartbeat and Rule engine | MEDIUM | Single-statement MATCH+WHERE+SET in Cypher (§3.1, §5) provides node-level locking within a write transaction. | Add explicit integration tests with concurrent heartbeats and Rule evaluations. Document Neo4j version requirements for lock guarantees. |
| F8 | No audit trail for lifecycle transitions | LOW | `heartbeat_source` field (§6.4) provides partial forensics. `previous_heartbeat` retains one prior timestamp. | Add `LifecycleEvent` nodes or `lifecycle_changed_at` property in hardening phase. |
| F9 | 2-hour detection latency not documented | LOW | Thresholds are configurable (§4). Operators can reduce `heartbeat_stale_seconds` for faster detection. | Add per-agent `expected_heartbeat_interval` and alert on `2×` overrun. Document detection latency formula in operator guide. |

### 10.3 Design Decisions

1. **Token chain reset on stale/inactive (F2):** When an agent goes stale, the token is cleared. This means a recovering agent can re-establish the chain without manual intervention. The alternative — requiring admin token reset — was rejected as too operationally burdensome for legitimate recovery scenarios (e.g., network outage resolved).

2. **Silent deduplication vs. error for rate-limited heartbeats (F4):** Rate-limited heartbeats return success with unchanged data rather than an error. This prevents well-intentioned agents with slightly aggressive heartbeat intervals from entering error-handling loops. The protection is against *write amplification*, not against the heartbeat itself.

3. **`disabled` as error vs. silent ignore (F6):** Heartbeats to disabled agents return `AgentDisabledError` rather than being silently dropped. This gives the calling process a clear signal to stop heartbeating, rather than silently continuing to make calls that do nothing.

4. **Cron retained as Pattern B (F3):** Rather than removing cron integration entirely, it was redesigned as a validated health-check pattern. Some agents (e.g., batch processors, periodic workers) don't have persistent processes and genuinely need external liveness validation. The `heartbeat_source` field ensures the Rule engine can differentiate.
