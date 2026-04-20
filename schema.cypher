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

// IL-02: indexed auth lookup + hash-collision guard.
// Implicitly indexes api_key_lookup so sdk.py / heartbeat_sdk.py / intent_sdk.py
// MATCH (a:Agent) WHERE a.api_key_lookup = $lookup uses a range index instead of
// a label scan, and enforces a 1:1 key→agent invariant (a backfill bug that
// duplicated a lookup hash would now fail at write time instead of returning an
// ambiguous record at read time).
CREATE CONSTRAINT agent_api_key_lookup IF NOT EXISTS
  FOR (a:Agent) REQUIRE a.api_key_lookup IS UNIQUE;

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

// ──────────────────────────────────────────
// Sprint 12 / Track C — distributed tracing
// ──────────────────────────────────────────
//
// Intent.traceparent (optional, nullable string)
//   W3C Trace Context header value, format:
//     "00-<trace-id-32hex>-<parent-id-16hex>-<flags-2hex>"
//   Written by the SDK (`submit_intent`) when HASSALEH_OBS=on so the daemon
//   can continue the caller's trace via TraceContextTextMapPropagator.extract.
//   Absent / null → daemon starts a fresh trace, linked only via intent_id.
//   See docs/sprint-12-plan.md §2.4 (SDK↔daemon propagation) and §3.3.
//
// Neo4j does not require a DDL statement for new properties on existing
// node labels (properties are schemaless per node). This block is the
// contract-of-record for the property; no constraint/index is created
// because traceparent is (a) nullable, (b) not queried by value.

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
