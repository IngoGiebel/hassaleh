# Interface Specification: Agent Heartbeat System

**Version:** 1.0 (Draft)
**Author:** Dione
**Status:** Awaiting security review (Inanna)

---

## 1. Purpose

Enable agents to signal liveness to the Hassaleh Daemon. The Daemon uses heartbeat data to track which agents are active, detect failures, and trigger alerts via the existing Rule engine.

## 2. Scope

- SDK method: `heartbeat(agent_id)`
- Neo4j Agent node updates
- Agent lifecycle transitions (pending → active → stale → inactive)
- Rule: `agent-health-check` (already seeded, needs implementation binding)
- OpenClaw Cron integration for automatic heartbeats

## 3. SDK Interface

### 3.1 `heartbeat(agent_id) -> dict`

**Input:**
```python
agent_id: str  # e.g. "dione"
```

**Returns:**
```python
{
    "agent_id": "dione",
    "lifecycle": "active",
    "last_heartbeat": "2026-04-14T14:00:00Z",
    "previous_heartbeat": "2026-04-14T12:00:00Z",
    "heartbeat_count": 42
}
```

**Neo4j state after call:**
```cypher
MATCH (a:Agent {id: "dione"})
SET a.last_heartbeat = datetime(),
    a.previous_heartbeat = a.last_heartbeat,
    a.heartbeat_count = coalesce(a.heartbeat_count, 0) + 1,
    a.lifecycle = "active"
```

**Error cases:**
- Agent not found → `AgentNotFoundError`
- Neo4j connection failed → `ConnectionError`

### 3.2 CLI Command: `hassaleh heartbeat <agent_id>`

Wrapper around `sdk.heartbeat()`. Used by OpenClaw cron jobs.

**Exit codes:**
- 0: heartbeat sent successfully
- 1: agent not found or connection error

## 4. Agent Lifecycle State Machine

```
pending → active (first heartbeat received)
active → active (heartbeat within threshold)
active → stale (no heartbeat for >2 hours)
stale → active (heartbeat received)
stale → inactive (no heartbeat for >24 hours)
inactive → active (heartbeat received)
```

**Thresholds (configurable via DaemonConfig node):**
| Threshold | Default | Meaning |
|-----------|---------|---------|
| `heartbeat_stale_seconds` | 7200 (2h) | Agent marked stale |
| `heartbeat_inactive_seconds` | 86400 (24h) | Agent marked inactive |

## 5. Rule Integration: agent-health-check

The existing seeded rule `agent-health-check` should evaluate:

```
EVERY 60s:
  MATCH (a:Agent)
  WHERE a.lifecycle = "active"
    AND a.last_heartbeat < datetime() - duration("PT2H")
  SET a.lifecycle = "stale"
  ALERT "Agent {a.name} has gone stale (no heartbeat for >2h)"
```

And:
```
EVERY 300s:
  MATCH (a:Agent)
  WHERE a.lifecycle = "stale"
    AND a.last_heartbeat < datetime() - duration("P1D")
  SET a.lifecycle = "inactive"
  ALERT "Agent {a.name} is now inactive (no heartbeat for >24h)"
```

## 6. OpenClaw Cron Integration

Each OpenClaw agent cron job should include a heartbeat call:

```bash
# At the start of every cron job:
hassaleh --uri bolt://localhost:7690 --user neo4j --password hassaleh heartbeat dione
```

This can be prepended to existing cron job messages or implemented as a separate cron:

```bash
openclaw cron add \
  --name "hassaleh-heartbeat-dione" \
  --every 30m \
  --agent worker-opus \
  --message "Send heartbeat: hassaleh --uri bolt://localhost:7690 --user neo4j --password hassaleh heartbeat dione"
```

## 7. Test Scenarios

### 7.1 First Heartbeat
1. Agent with `lifecycle: "pending"`, `last_heartbeat: null`
2. Call `heartbeat("dione")`
3. Assert `lifecycle = "active"`
4. Assert `last_heartbeat` is set
5. Assert `heartbeat_count = 1`

### 7.2 Subsequent Heartbeat
1. Agent already active with known `last_heartbeat`
2. Call `heartbeat("dione")`
3. Assert `previous_heartbeat = old_last_heartbeat`
4. Assert `last_heartbeat` updated
5. Assert `heartbeat_count` incremented

### 7.3 Stale Detection
1. Set agent `last_heartbeat` to 3 hours ago
2. Trigger rule evaluation
3. Assert `lifecycle` changed to `"stale"`
4. Assert ALERT was triggered

### 7.4 Recovery from Stale
1. Agent with `lifecycle: "stale"`
2. Call `heartbeat("dione")`
3. Assert `lifecycle = "active"`

### 7.5 Inactive Detection
1. Set agent `last_heartbeat` to 25 hours ago, `lifecycle: "stale"`
2. Trigger rule evaluation
3. Assert `lifecycle` changed to `"inactive"`
4. Assert ALERT was triggered

### 7.6 Unknown Agent
1. Call `heartbeat("nonexistent")`
2. Assert `AgentNotFoundError`

### 7.7 Concurrent Heartbeats
1. Send heartbeats for 4 agents simultaneously
2. Assert all agents updated correctly
3. Assert no data corruption

## 8. Security Considerations (for Inanna's review)

- [ ] Can agent A send a heartbeat for agent B? (Authentication/authorization)
- [ ] Can a heartbeat be replayed? (Idempotency, freshness checks)
- [ ] Can the heartbeat mechanism be used for timing attacks?
- [ ] What happens if heartbeat floods the graph? (Rate limiting)
- [ ] Should heartbeat require a signed token or session?
