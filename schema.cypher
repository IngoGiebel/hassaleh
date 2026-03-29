// ═══════════════════════════════════════════════════════════════
// Hassaleh — Neo4j Schema (MVP)
// ═══════════════════════════════════════════════════════════════
//
// Node types: Agent, Capability, Workspace, Intent, Task,
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

// Capability lookup by kind
CREATE INDEX capability_kind IF NOT EXISTS
  FOR (c:Capability) ON (c.kind);
