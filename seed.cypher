// ═══════════════════════════════════════════════════════════════
// Hassaleh — Seed Data (MVP)
// ═══════════════════════════════════════════════════════════════
//
// Creates the minimum graph needed for Sprint 1:
// 1 Agent, 1 Capability, 1 Workspace, 1 Task, 3 singletons
//
// Reference: docs/CONCEPT.md v1.2
// Created: 2026-03-29 by Dione 🌙

// ──────────────────────────────────────────
// Singletons
// ──────────────────────────────────────────

MERGE (dc:DaemonConfig {id: "default"})
SET dc += {
  tick_interval_ms: 1000,
  sweep_interval_min: 15,
  audit_schedule: "0 3 * * *",
  intent_timeout_default_sec: 300,
  max_concurrent_actions: 10,
  circuit_breaker_window_min: 60,
  circuit_breaker_decay_per_sweep: 1,
  notification_channel: "openclaw",
  health_endpoint_port: 9100
};

MERGE (qc:QueryConfig {id: "default"})
SET qc += {
  default_timeout_ms: 3000,
  max_result_rows: 10000,
  max_transaction_memory_mb: 1024,
  report_query_timeout_ms: 30000
};

MERGE (sv:SystemVersion {id: "hassaleh"})
SET sv += {
  schema_version: "1.2",
  last_migration: datetime({timezone: 'UTC'}),
  compatible_daemon_versions: ["1.2"]
};

// ──────────────────────────────────────────
// Workspace
// ──────────────────────────────────────────

MERGE (ws:Workspace {id: "hassaleh-workspace"})
SET ws += {
  path: "/home/uranus/moltbot-workspace/projects/hassaleh",
  os: "ubuntu",
  writable: true,
  description: "Hassaleh project workspace"
};

// ──────────────────────────────────────────
// Agent: Dione (first managed agent)
// ──────────────────────────────────────────

MERGE (a:Agent {id: "dione"})
SET a += {
  name: "Dione",
  emoji: "🌙",
  description: "Finance agent — portfolio monitoring, market analysis, reporting. Sprint 1 lead for Hassaleh.",
  personality: "Sachlich-präzise, aber locker und gerne mit Humor",
  runtime: "openclaw",
  lifecycle: "pending",
  last_heartbeat: null,
  os_pid: null,
  credits_remaining: null,
  health_check_interval_sec: 300,
  restart_count_1h: 0,
  max_restarts_1h: 5
};

// Agent → Workspace
MATCH (a:Agent {id: "dione"}), (ws:Workspace {id: "hassaleh-workspace"})
MERGE (a)-[:OPERATES_IN {since: datetime({timezone: 'UTC'})}]->(ws);

// ──────────────────────────────────────────
// Capability: Execute ls (MVP test capability)
// ──────────────────────────────────────────

MERGE (cap:Capability {id: "exec-ls"})
SET cap += {
  name: "List directory contents",
  kind: "cli",
  description: "Execute ls command to list files in a directory",
  invoke_command: "/usr/bin/ls",
  invoke_params_schema: '{"args": "string"}',
  version: null,
  source: null,
  exec_as_user: "hassaleh-exec",
  requires_auth: false,
  requires_confirmation: false,
  rate_limit: null,
  cost_model: "free",
  lifecycle: "available"
};

// Agent → Capability
MATCH (a:Agent {id: "dione"}), (cap:Capability {id: "exec-ls"})
MERGE (a)-[:HAS_CAPABILITY {granted_at: datetime({timezone: 'UTC'})}]->(cap);

// ──────────────────────────────────────────
// Task: MVP test task
// ──────────────────────────────────────────

MERGE (t:Task {id: "mvp-test-ls"})
SET t += {
  name: "MVP Test: Execute ls and verify output",
  description: "Agent claims this task, submits an Intent to execute ls -la on the workspace, reads the result from the Intent feedback.",
  lifecycle: "pending",
  completed_at: null,
  expires_at: datetime({timezone: 'UTC'}) + duration('P7D'),
  verification_method: "Intent lifecycle == success AND stdout contains schema.cypher",
  idempotency_key: null
};

// Task → Agent (assigned)
MATCH (t:Task {id: "mvp-test-ls"}), (a:Agent {id: "dione"})
MERGE (t)-[:ASSIGNED_TO]->(a);
