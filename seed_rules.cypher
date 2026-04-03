// ═══════════════════════════════════════════════════════════════
// Hassaleh — Seed Rules (Sprint 2)
// ═══════════════════════════════════════════════════════════════
//
// Operational rules for agent orchestration.
// Apply after schema.cypher and seed.cypher.

// ──────────────────────────────────────────
// Rule 1: Agent Health Check
// ──────────────────────────────────────────
// Checks running agents for stale heartbeats.
// Restarts up to 5 times, then circuit-breaks.

MERGE (r1:Rule {id: "agent-health-check"})
SET r1 += {
  name: "Agent Health Check",
  version: 1,
  category: "operations",
  priority: 10,
  lifecycle: "available",
  description: "Check agent health every 5 min. Restart if unresponsive. Circuit breaker after 5 failures.",
  author: "dione",
  rule_text: 'EVERY "PT5M":
    MATCH (a:Agent {lifecycle: \'running\'}):
        IF a.last_heartbeat < NOW() - DURATION("PT5M"):
            IF a.restart_count_1h < 5:
                a.restart_count_1h += 1
                SUBMIT_INTENT "restart_agent" ON a
                LOG "Restarting unresponsive agent" LEVEL "warning"
            ELSE:
                a.lifecycle = "circuit_broken"
                ALERT "Circuit breaker: agent exceeded restart limit" ON a
                LOG "Circuit breaker triggered" LEVEL "error"'
};

// ──────────────────────────────────────────
// Rule 2: Task Timeout Sweep
// ──────────────────────────────────────────
// Fails tasks that have exceeded their expiry time.

MERGE (r2:Rule {id: "task-timeout-sweep"})
SET r2 += {
  name: "Task Timeout Sweep",
  version: 1,
  category: "operations",
  priority: 20,
  lifecycle: "available",
  description: "Fail tasks that have exceeded their expires_at time.",
  author: "dione",
  rule_text: 'EVERY "PT15M":
    MATCH (t:Task {lifecycle: \'running\'}):
        IF t.expires_at < NOW():
            t.lifecycle = "failed"
            t.error_reason = "Task timed out"
            LOG "Task timed out" LEVEL "warning"'
};

// ──────────────────────────────────────────
// Rule 3: Task Assignment
// ──────────────────────────────────────────
// Assigns pending tasks to agents whose capabilities match the task's
// required capability edge. The assign_task intent is idempotent and
// will no-op if the task is already assigned.

MERGE (r3:Rule {id: "task-assignment"})
SET r3 += {
  name: "Task Assignment",
  version: 1,
  category: "operations",
  priority: 30,
  lifecycle: "available",
  description: "Assign pending unclaimed tasks to agents with matching capabilities.",
  author: "dione",
  rule_text: 'EVERY "PT1M":
    MATCH (t:Task {lifecycle: \'pending\'})-[:REQUIRES_CAPABILITY]->(c:Capability)<-[:HAS_CAPABILITY]-(a:Agent):
        SUBMIT_INTENT "assign-task" ON a WITH {task_id: t.id}
        LOG "Queued task assignment" LEVEL "info"'
};
