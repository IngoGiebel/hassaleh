// ═══════════════════════════════════════════════════════════════
// Hassaleh — Neo4j Schema (MVP)
// ═══════════════════════════════════════════════════════════════
//
// Node types: Agent, Capability, SkillDomain, Workspace, Intent, Task,
//             DaemonConfig, QueryConfig, SystemVersion
//
// Reference: docs/CONCEPT.md v1.2
// Created: 2026-03-29 by Dione 🌙

// ──────────────────────────────────────────
// Uniqueness constraints
// ──────────────────────────────────────────

CREATE CONSTRAINT agent_id IF NOT EXISTS
  FOR (a:Agent) REQUIRE a.id IS UNIQUE;

CREATE CONSTRAINT capability_id IF NOT EXISTS
  FOR (c:Capability) REQUIRE c.id IS UNIQUE;

CREATE CONSTRAINT skilldomain_id IF NOT EXISTS
  FOR (sd:SkillDomain) REQUIRE sd.id IS UNIQUE;

CREATE CONSTRAINT workspace_id IF NOT EXISTS
  FOR (w:Workspace) REQUIRE w.id IS UNIQUE;

CREATE CONSTRAINT intent_id IF NOT EXISTS
  FOR (i:Intent) REQUIRE i.id IS UNIQUE;

CREATE CONSTRAINT task_id IF NOT EXISTS
  FOR (t:Task) REQUIRE t.id IS UNIQUE;

CREATE CONSTRAINT daemonconfig_id IF NOT EXISTS
  FOR (dc:DaemonConfig) REQUIRE dc.id IS UNIQUE;

CREATE CONSTRAINT queryconfig_id IF NOT EXISTS
  FOR (qc:QueryConfig) REQUIRE qc.id IS UNIQUE;

CREATE CONSTRAINT systemversion_id IF NOT EXISTS
  FOR (sv:SystemVersion) REQUIRE sv.id IS UNIQUE;

CREATE CONSTRAINT rule_id IF NOT EXISTS
  FOR (r:Rule) REQUIRE r.id IS UNIQUE;

CREATE CONSTRAINT message_id IF NOT EXISTS
  FOR (m:Message) REQUIRE m.id IS UNIQUE;

CREATE CONSTRAINT discussion_id IF NOT EXISTS
  FOR (d:Discussion) REQUIRE d.id IS UNIQUE;

// ──────────────────────────────────────────
// Indexes for frequent queries
// ──────────────────────────────────────────

// Daemon hot-path: find pending Intents
CREATE INDEX intent_lifecycle IF NOT EXISTS
  FOR (i:Intent) ON (i.lifecycle);

// Daemon hot-path: find running agents for health checks
CREATE INDEX agent_lifecycle IF NOT EXISTS
  FOR (a:Agent) ON (a.lifecycle);

// Task assignment queries
CREATE INDEX task_lifecycle IF NOT EXISTS
  FOR (t:Task) ON (t.lifecycle);

CREATE INDEX task_execution_mode IF NOT EXISTS
  FOR (t:Task) ON (t.execution_mode);

CREATE INDEX task_parent_group IF NOT EXISTS
  FOR (t:Task) ON (t.parent_task_id);

CREATE INDEX task_execution_order IF NOT EXISTS
  FOR (t:Task) ON (t.execution_order);

// Capability lookup by kind
CREATE INDEX capability_kind IF NOT EXISTS
  FOR (c:Capability) ON (c.kind);

// Capability lookup by problem domain
CREATE INDEX capability_domain IF NOT EXISTS
  FOR (c:Capability) ON (c.domain);

// Rule lifecycle for loading available rules
CREATE INDEX rule_lifecycle IF NOT EXISTS
  FOR (r:Rule) ON (r.lifecycle);

// Message timestamp for ordering queries
CREATE INDEX message_timestamp IF NOT EXISTS
  FOR (m:Message) ON (m.timestamp);

// Discussion lifecycle
CREATE INDEX discussion_lifecycle IF NOT EXISTS
  FOR (d:Discussion) ON (d.lifecycle);
