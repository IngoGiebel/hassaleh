# Interface Specification: MVP Intent Pipeline Test

**Version:** 1.0 (Draft)
**Author:** Dione
**Status:** Awaiting security review (Inanna)

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

## 4. SDK Interface

### 4.1 `submit_intent(agent_id, capability_id, params) -> str`

**Input:**
```python
agent_id: str       # e.g. "dione"
capability_id: str  # e.g. "exec-ls"
params: dict        # e.g. {"path": "/app"}
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
- Agent not found → `AgentNotFoundError`
- Capability not found → `CapabilityNotFoundError`
- Agent lacks capability → `CapabilityDeniedError`
- Neo4j connection failed → `ConnectionError`

### 4.2 `get_intent_status(intent_id) -> dict`

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

### 4.3 `get_intent_result(intent_id) -> dict`

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
pending → rejected (capability check fails)
```

**Transitions:**
| From | To | Trigger | Actor |
|------|----|---------|-------|
| pending | claimed | Daemon picks up Intent | Daemon |
| pending | rejected | Agent lacks capability | Daemon |
| claimed | running | Daemon starts execution | Daemon |
| running | success | Execution completes with exit 0 | Daemon |
| running | failed | Execution fails or times out | Daemon |

**Invalid transitions (must be rejected):**
- `success → pending` (no rollback)
- `failed → running` (no automatic retry)
- `claimed → pending` (no unclaiming)

### 5.2 Daemon Processing Steps

1. **Poll:** Query `MATCH (i:Intent {status: "pending"}) RETURN i ORDER BY i.created_at LIMIT 10`
2. **Claim:** Atomically set `status = "claimed"`, `claimed_at = now()`
3. **Capability Check:**
   ```cypher
   MATCH (i:Intent {id: $id})-[:SUBMITTED_BY]->(a:Agent)-[:HAS_CAPABILITY]->(c:Capability {id: i.capability_id})
   RETURN c
   ```
   If no match → `status = "rejected"`, `error = "Agent lacks capability"`
4. **Execute:** Run the capability handler (for `exec-ls`: `subprocess.run(["ls", "-la", path])`)
5. **Result:** Set `status = "success"`, `result = stdout`, `duration_ms = elapsed`
6. **On error:** Set `status = "failed"`, `error = stderr or exception message`

### 5.3 Timeout

- Default: 30 seconds per Intent
- On timeout: `status = "failed"`, `error = "Execution timed out after 30s"`

### 5.4 Concurrency

- Daemon processes max 3 Intents simultaneously
- Each Intent is claimed atomically (no double-processing)
- Claim uses Neo4j transaction: `SET i.status = "claimed" WHERE i.status = "pending"`

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

## 7. Security Considerations (for Inanna's review)

- [ ] Can an agent submit Intents for another agent? (Should be blocked)
- [ ] Can an agent modify an Intent after submission? (Should be immutable)
- [ ] What happens if the Daemon crashes between `claimed` and `running`?
- [ ] Can an agent poll/read other agents' Intent results? (Access control)
- [ ] Is the capability check atomic with the claim? (TOCTOU risk)
- [ ] Can `params` contain injection payloads? (e.g., path traversal in exec-ls)
