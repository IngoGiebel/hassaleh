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
  parallel_fail_fast: true,
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
  allowed_domains: null,
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
// Skill Domain Taxonomy
// ──────────────────────────────────────────

MERGE (sd:SkillDomain {id: "orchestration"})
SET sd += {name: "Orchestration", description: "Control-plane coordination and execution flow.", parent: null};
MERGE (sd:SkillDomain {id: "orchestration.rules"})
SET sd += {name: "Rule Management", description: "Authoring, compiling, and managing rules.", parent: "orchestration"};
MERGE (sd:SkillDomain {id: "orchestration.scheduling"})
SET sd += {name: "Scheduling", description: "Coordinating execution windows and timing.", parent: "orchestration"};
MERGE (sd:SkillDomain {id: "orchestration.lifecycle"})
SET sd += {name: "Lifecycle Control", description: "Managing state transitions for agents and tasks.", parent: "orchestration"};

MERGE (sd:SkillDomain {id: "data"})
SET sd += {name: "Data", description: "Graph, ETL, and query-oriented work.", parent: null};
MERGE (sd:SkillDomain {id: "data.graph"})
SET sd += {name: "Graph Operations", description: "Neo4j graph modeling and traversal tasks.", parent: "data"};
MERGE (sd:SkillDomain {id: "data.etl"})
SET sd += {name: "ETL", description: "Moving and reshaping data into the graph.", parent: "data"};
MERGE (sd:SkillDomain {id: "data.query"})
SET sd += {name: "Querying", description: "Read-oriented graph queries and reporting lookups.", parent: "data"};

MERGE (sd:SkillDomain {id: "security"})
SET sd += {name: "Security", description: "Authentication, auditing, and permission controls.", parent: null};
MERGE (sd:SkillDomain {id: "security.auth"})
SET sd += {name: "Authentication", description: "Identity checks and trust boundaries.", parent: "security"};
MERGE (sd:SkillDomain {id: "security.audit"})
SET sd += {name: "Audit", description: "Audit logging, traceability, and review paths.", parent: "security"};
MERGE (sd:SkillDomain {id: "security.permissions"})
SET sd += {name: "Permissions", description: "Access policies and scoped authorization.", parent: "security"};

MERGE (sd:SkillDomain {id: "communication"})
SET sd += {name: "Communication", description: "Messaging, notifications, and alerts.", parent: null};
MERGE (sd:SkillDomain {id: "communication.messaging"})
SET sd += {name: "Messaging", description: "Inter-agent or operator messaging flows.", parent: "communication"};
MERGE (sd:SkillDomain {id: "communication.notifications"})
SET sd += {name: "Notifications", description: "Outbound notifications to operators and systems.", parent: "communication"};
MERGE (sd:SkillDomain {id: "communication.alerts"})
SET sd += {name: "Alerts", description: "Urgent alerting and escalation paths.", parent: "communication"};

MERGE (sd:SkillDomain {id: "reporting"})
SET sd += {name: "Reporting", description: "Summaries, metrics, and exports.", parent: null};
MERGE (sd:SkillDomain {id: "reporting.activity"})
SET sd += {name: "Activity Reporting", description: "Operational activity summaries and audit views.", parent: "reporting"};
MERGE (sd:SkillDomain {id: "reporting.metrics"})
SET sd += {name: "Metrics", description: "Quantitative summaries and KPI views.", parent: "reporting"};
MERGE (sd:SkillDomain {id: "reporting.export"})
SET sd += {name: "Export", description: "Exporting reports to external formats.", parent: "reporting"};

MERGE (sd:SkillDomain {id: "system"})
SET sd += {name: "System", description: "Health, maintenance, and configuration tasks.", parent: null};
MERGE (sd:SkillDomain {id: "system.health"})
SET sd += {name: "System Health", description: "Health checks and operational diagnostics.", parent: "system"};
MERGE (sd:SkillDomain {id: "system.maintenance"})
SET sd += {name: "Maintenance", description: "Maintenance and housekeeping operations.", parent: "system"};
MERGE (sd:SkillDomain {id: "system.config"})
SET sd += {name: "Configuration", description: "Configuration inspection and changes.", parent: "system"};

MERGE (sd:SkillDomain {id: "development"})
SET sd += {name: "Development", description: "Testing, review, and CI operations.", parent: null};
MERGE (sd:SkillDomain {id: "development.testing"})
SET sd += {name: "Testing", description: "Running automated tests and validation checks.", parent: "development"};
MERGE (sd:SkillDomain {id: "development.review"})
SET sd += {name: "Review", description: "Code or graph review workflows.", parent: "development"};
MERGE (sd:SkillDomain {id: "development.ci"})
SET sd += {name: "Continuous Integration", description: "Build and CI pipeline operations.", parent: "development"};

MERGE (sd:SkillDomain {id: "integration"})
SET sd += {name: "Integration", description: "External integrations and adapters.", parent: null};
MERGE (sd:SkillDomain {id: "integration.openclaw"})
SET sd += {name: "OpenClaw", description: "OpenClaw bridge and messaging integration.", parent: "integration"};
MERGE (sd:SkillDomain {id: "integration.api"})
SET sd += {name: "API", description: "External API integration capabilities.", parent: "integration"};
MERGE (sd:SkillDomain {id: "integration.webhook"})
SET sd += {name: "Webhook", description: "Webhook producers and consumers.", parent: "integration"};

MERGE (sd:SkillDomain {id: "analysis"})
SET sd += {name: "Analysis", description: "Domain-specific analysis work.", parent: null};
MERGE (sd:SkillDomain {id: "analysis.finance"})
SET sd += {name: "Finance Analysis", description: "Financial data analysis and summaries.", parent: "analysis"};
MERGE (sd:SkillDomain {id: "analysis.security"})
SET sd += {name: "Security Analysis", description: "Threat, audit, and policy analysis.", parent: "analysis"};
MERGE (sd:SkillDomain {id: "analysis.performance"})
SET sd += {name: "Performance Analysis", description: "Latency, throughput, and efficiency analysis.", parent: "analysis"};

MERGE (sd:SkillDomain {id: "automation"})
SET sd += {name: "Automation", description: "Task, workflow, and scheduled automation.", parent: null};
MERGE (sd:SkillDomain {id: "automation.tasks"})
SET sd += {name: "Task Automation", description: "Automating operational tasks.", parent: "automation"};
MERGE (sd:SkillDomain {id: "automation.workflows"})
SET sd += {name: "Workflow Automation", description: "Multi-step automated workflows.", parent: "automation"};
MERGE (sd:SkillDomain {id: "automation.cron"})
SET sd += {name: "Scheduled Jobs", description: "Cron-style scheduled execution.", parent: "automation"};

// ──────────────────────────────────────────
// Capabilities
// ──────────────────────────────────────────

MERGE (cap:Capability {id: "exec-ls"})
SET cap += {
  name: "List directory contents",
  kind: "cli",
  description: "Execute ls command to list files in a directory",
  domain: "system.health",
  invoke_command: "/usr/bin/ls",
  invoke_params_schema: '{"args": "string"}',
  version: null,
  source: null,
  exec_as_user: "hassaleh-fs",
  requires_auth: false,
  requires_confirmation: false,
  rate_limit: null,
  cost_model: "free",
  lifecycle: "available"
};

MERGE (cap:Capability {id: "graph-query-inspector"})
SET cap += {
  name: "Graph Query Inspector",
  kind: "skill",
  description: "Inspect graph data with safe, read-only query patterns.",
  domain: "data.query",
  invoke_command: "hassaleh.graph_query_inspector",
  invoke_params_schema: '{"query": "string"}',
  version: "1.0",
  source: "builtin",
  exec_as_user: "hassaleh-daemon",
  requires_auth: false,
  requires_confirmation: false,
  rate_limit: null,
  cost_model: "free",
  lifecycle: "available"
};

MERGE (cap:Capability {id: "rule-author-basic"})
SET cap += {
  name: "Rule Author",
  kind: "skill",
  description: "Guide operators through creating and reviewing orchestration rules.",
  domain: "orchestration.rules",
  invoke_command: "hassaleh.rule_author",
  invoke_params_schema: '{"prompt": "string"}',
  version: "1.0",
  source: "builtin",
  exec_as_user: "hassaleh-daemon",
  requires_auth: false,
  requires_confirmation: true,
  rate_limit: null,
  cost_model: "free",
  lifecycle: "available"
};

MERGE (cap:Capability {id: "audit-log-export"})
SET cap += {
  name: "Audit Log Export",
  kind: "cli",
  description: "Export audit-oriented summaries for review and compliance checks.",
  domain: "security.audit",
  invoke_command: "/usr/bin/printf",
  invoke_params_schema: '{"args": "string"}',
  version: "1.0",
  source: "builtin",
  exec_as_user: "hassaleh-audit",
  requires_auth: false,
  requires_confirmation: false,
  rate_limit: null,
  cost_model: "free",
  lifecycle: "available"
};

MERGE (cap:Capability {id: "metrics-daily-summary"})
SET cap += {
  name: "Metrics Daily Summary",
  kind: "skill",
  description: "Build a daily metrics summary across agents, rules, and intents.",
  domain: "reporting.metrics",
  invoke_command: "hassaleh.metrics_daily_summary",
  invoke_params_schema: '{"days": "integer"}',
  version: "1.0",
  source: "builtin",
  exec_as_user: "hassaleh-daemon",
  requires_auth: false,
  requires_confirmation: false,
  rate_limit: null,
  cost_model: "free",
  lifecycle: "available"
};

MERGE (cap:Capability {id: "ci-test-runner"})
SET cap += {
  name: "CI Test Runner",
  kind: "cli",
  description: "Run project test suites in a controlled execution context.",
  domain: "development.testing",
  invoke_command: "/usr/bin/env",
  invoke_params_schema: '{"args": "string"}',
  version: "1.0",
  source: "builtin",
  exec_as_user: "hassaleh-ci",
  requires_auth: false,
  requires_confirmation: false,
  rate_limit: null,
  cost_model: "free",
  lifecycle: "available"
};

MERGE (cap:Capability {id: "openclaw-alert-dispatch"})
SET cap += {
  name: "OpenClaw Alert Dispatch",
  kind: "bridge",
  description: "Dispatch notifications and alerts through the OpenClaw bridge.",
  domain: "integration.openclaw",
  invoke_command: "hassaleh.openclaw_alert_dispatch",
  invoke_params_schema: '{"text": "string"}',
  version: "1.0",
  source: "builtin",
  exec_as_user: "hassaleh-daemon",
  requires_auth: false,
  requires_confirmation: false,
  rate_limit: null,
  cost_model: "free",
  lifecycle: "available"
};

MERGE (cap:Capability {id: "workflow-cron-maintenance"})
SET cap += {
  name: "Workflow Cron Maintenance",
  kind: "skill",
  description: "Coordinate scheduled maintenance and recurring workflow tasks.",
  domain: "automation.cron",
  invoke_command: "hassaleh.workflow_cron_maintenance",
  invoke_params_schema: '{"schedule": "string"}',
  version: "1.0",
  source: "builtin",
  exec_as_user: "hassaleh-daemon",
  requires_auth: false,
  requires_confirmation: false,
  rate_limit: null,
  cost_model: "free",
  lifecycle: "available"
};

// Agent → Capability
MATCH (a:Agent {id: "dione"}), (cap:Capability {id: "exec-ls"})
MERGE (a)-[:HAS_CAPABILITY {granted_at: datetime({timezone: 'UTC'})}]->(cap);

MATCH (a:Agent {id: "dione"}), (cap:Capability {id: "graph-query-inspector"})
MERGE (a)-[:HAS_CAPABILITY {granted_at: datetime({timezone: 'UTC'})}]->(cap);

MATCH (a:Agent {id: "dione"}), (cap:Capability {id: "metrics-daily-summary"})
MERGE (a)-[:HAS_CAPABILITY {granted_at: datetime({timezone: 'UTC'})}]->(cap);

MATCH (cap:Capability {id: "exec-ls"}), (sd:SkillDomain {id: "system.health"})
MERGE (cap)-[:IN_DOMAIN]->(sd);

MATCH (cap:Capability {id: "graph-query-inspector"}), (sd:SkillDomain {id: "data.query"})
MERGE (cap)-[:IN_DOMAIN]->(sd);

MATCH (cap:Capability {id: "rule-author-basic"}), (sd:SkillDomain {id: "orchestration.rules"})
MERGE (cap)-[:IN_DOMAIN]->(sd);

MATCH (cap:Capability {id: "audit-log-export"}), (sd:SkillDomain {id: "security.audit"})
MERGE (cap)-[:IN_DOMAIN]->(sd);

MATCH (cap:Capability {id: "metrics-daily-summary"}), (sd:SkillDomain {id: "reporting.metrics"})
MERGE (cap)-[:IN_DOMAIN]->(sd);

MATCH (cap:Capability {id: "ci-test-runner"}), (sd:SkillDomain {id: "development.testing"})
MERGE (cap)-[:IN_DOMAIN]->(sd);

MATCH (cap:Capability {id: "openclaw-alert-dispatch"}), (sd:SkillDomain {id: "integration.openclaw"})
MERGE (cap)-[:IN_DOMAIN]->(sd);

MATCH (cap:Capability {id: "workflow-cron-maintenance"}), (sd:SkillDomain {id: "automation.cron"})
MERGE (cap)-[:IN_DOMAIN]->(sd);

// ──────────────────────────────────────────
// Task: MVP test task
// ──────────────────────────────────────────

MERGE (t:Task {id: "mvp-test-ls"})
SET t += {
  name: "MVP Test: Execute ls and verify output",
  description: "Agent claims this task, submits an Intent to execute ls -la on the workspace, reads the result from the Intent feedback.",
  lifecycle: "pending",
  execution_mode: "sequential",
  execution_order: 1,
  parent_task_id: null,
  completed_at: null,
  expires_at: datetime({timezone: 'UTC'}) + duration('P7D'),
  verification_method: "Intent lifecycle == success AND stdout contains schema.cypher",
  idempotency_key: null
};

// Task → Agent (assigned)
MATCH (t:Task {id: "mvp-test-ls"}), (a:Agent {id: "dione"})
MERGE (t)-[:ASSIGNED_TO]->(a);
