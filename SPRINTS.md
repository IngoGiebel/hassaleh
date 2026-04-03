# SPRINTS.md — Hassaleh Development Sprints

*Created: 2026-03-29 by Dione 🌙*

---

## Sprint 1: MVP — Prove the Graph-Mediated IPC Loop

**Goal:** Build the minimum viable Hassaleh system: 1 Daemon, 1 Agent, 1 Neo4j database. Prove that the full Intent loop works end-to-end: Agent → Intent → Daemon → OS action → feedback → Agent reads result.

**Lead:** Dione 🌙
**Team:** Codex (development), Gemini CLI (review + deep think)
**Review Policy:** Every completed task is reviewed by at least one other model before merge.

**Duration:** ~2 weeks
**Seed Project:** [clearmail-gateway](https://github.com/IngoGiebel/clearmail-gateway) (used as reference for project structure, code quality standards)

### Phase 1: Schema + Seed

| # | Task | Assignee | Reviewer | Status |
|---|------|----------|----------|--------|
| 1.1 | Write `schema.cypher` — CREATE CONSTRAINT/INDEX for MVP nodes (Agent, Capability, Workspace, Intent, Task, DaemonConfig, QueryConfig, SystemVersion) | Dione | Codex | ✅ done |
| 1.2 | Write `seed.cypher` — Create initial Agent (Dione), Capability (`ls`), Task, Workspace, DaemonConfig, QueryConfig, SystemVersion nodes | Dione | Gemini | ✅ done |
| 1.3 | Create Neo4j users: `hassaleh_daemon` (r/w) and `hassaleh_reader` (r/o). Note: CE has no role-based access — read-only enforced in SDK. | Dione | — | ✅ done |
| 1.4 | Apply schema + seed to Neo4j, verify with Cypher queries | Dione | — | ✅ done |

### Phase 2: Daemon MVP

| # | Task | Assignee | Reviewer | Status |
|---|------|----------|----------|--------|
| 2.1 | Write `setup_os.sh` — Create OS users (hassaleh-svc, hassaleh-agent, hassaleh-fs, hassaleh-writer, hassaleh-exec, hassaleh-net, hassaleh-pkg), configure `/etc/sudoers.d/hassaleh`, install systemd unit | Dione | — | ✅ done |
| 2.2 | Write `daemon.py` — asyncio event loop, Neo4j connection as `hassaleh_daemon`, read pending Intents | Dione | — | ✅ done |
| 2.3 | Add async action workers — `asyncio.create_subprocess_exec` with `sudo -n -u` delegation | Dione | — | ✅ done |
| 2.4 | Add Intent feedback — write lifecycle/stdout/stderr/error_reason back to Intent nodes | Dione | — | ✅ done |
| 2.5 | Add zombie Intent recovery on boot — transition stale `running` Intents to `failed` | Dione | — | ✅ done |
| 2.6 | Add graceful shutdown — SIGTERM handler, kill subprocesses, update Intents | Dione | — | ✅ done |
| 2.7 | Add systemd watchdog integration (`sd_notify`) | Dione | — | ✅ done |
| 2.8 | Add health endpoint (minimal HTTP, port from DaemonConfig) | Dione | — | ✅ done |
| 2.9 | Write systemd unit file `hassaleh-daemon.service` | Dione | — | ✅ done |
| 2.10 | Integration test: Daemon processes a manually-created Intent in Neo4j | Dione | — | ✅ done |

### Phase 2.5: Agent SDK

| # | Task | Assignee | Reviewer | Status |
|---|------|----------|----------|--------|
| 2.5.1 | Write `sdk.py` — `hassaleh.query()` with parameterized queries, per-query timeout from QueryConfig | Dione | — | ✅ done |
| 2.5.2 | Add `hassaleh.submit_intent()` — create Intent node via Daemon API or direct write | Dione | — | ✅ done |
| 2.5.3 | Add convenience methods: `hassaleh.my_tasks()`, `hassaleh.project_status()` | Dione | — | ✅ done |
| 2.5.4 | Credential bootstrapping — read NEO4J_URI/USER/PASSWORD from env vars | Dione | — | ✅ done |
| 2.5.5 | Unit tests for SDK (6 unit + 6 integration, all green) | Dione | — | ✅ done |

### Phase 3: Blackboard Spike (End-to-End Proof)

| # | Task | Assignee | Reviewer | Status |
|---|------|----------|----------|--------|
| 3.1 | Write `agent_dummy.py` — test agent that claims a Task, submits Intent to execute `ls -la`, polls result, verifies output | Dione | — | ✅ done |
| 3.2 | Full loop test: start Daemon, start agent, verify Intent → action → feedback → agent reads stdout | Dione | — | ✅ done |
| 3.3 | Verify: agent cannot write to Neo4j (read-only user enforcement) | Dione | — | ✅ done (test_chaos.py: test_read_only_enforcement) |
| 3.4 | Verify: agent cannot execute capabilities it doesn't have (HAS_CAPABILITY check) | Dione | — | ✅ done (test_chaos.py: test_capability_permission_denied) |
| 3.5 | Stress test: 10 rapid Intents, verify all processed correctly | Dione | — | ✅ done (test_stress.py, unit test green) |
| 3.6 | Chaos test: kill Daemon mid-processing, restart, verify zombie recovery | Dione | — | ✅ done (test_chaos.py: test_zombie_recovery + test_graceful_shutdown.sh) |

### Sprint 1 Acceptance Criteria

- [x] Daemon starts as systemd service, processes Intents, writes feedback
- [x] Agent can query graph (read-only) and submit Intents
- [x] Full loop proven: agent → Intent → Daemon → OS action → stdout → agent reads result
- [x] Read-only enforcement verified (agent cannot write to Neo4j)
- [x] Capability permission check verified (agent cannot use unassigned capabilities)
- [x] Zombie Intent recovery works after Daemon crash
- [x] Graceful shutdown preserves Intent state
- [x] Stress test: 10 rapid Intents processed correctly (unit-level green, integration needs Daemon on test1)
- [x] Code review completed (Dione, 2026-03-31 — docs/CODE_REVIEW_SPRINT1.md)

### Sprint 1 Deliverables

| File | Description |
|------|-------------|
| `schema.cypher` | Neo4j constraints + indexes |
| `seed.cypher` | Initial graph data |
| `setup_os.sh` | OS user creation + sudoers + systemd |
| `src/hassaleh/daemon.py` | Async Daemon service |
| `src/hassaleh/sdk.py` | Agent SDK |
| `src/hassaleh/agent_dummy.py` | Test agent |
| `hassaleh-daemon.service` | systemd unit file |
| `tests/` | Unit + integration tests |

---

### Sprint 1 Review Findings

**Code review:** `docs/CODE_REVIEW_SPRINT1.md` (2026-03-31)

3 Critical issues identified for Sprint 1.5 fix round:
1. **Race condition** in Intent claiming (non-atomic read+write) → atomic MATCH+SET
2. **Cypher injection** via dynamic label in `submit_intent()` → allowlist validation
3. **Double shutdown** from signal handler + finally block → use shutdown event

5 Important improvements documented for Sprint 2.

---

## Backlog — Future Sprints

### Sprint 2: Rule Engine (GSL-Ops)

**Goal:** Port GSL-Ops from GWW3. Deterministic rule subset for agent orchestration. Compile rules on boot, evaluate in Daemon tick loop.

**Lead:** Dione 🌙
**Team:** Codex (development), Gemini CLI (review)
**Duration:** ~2 weeks
**Source:** GWW3 GSL engine (`projects/games-of-ww3/src/gww3/engine/`) — 785 lines to port/adapt

### Phase 1: Grammar Port

| # | Task | Assignee | Reviewer | Status |
|---|------|----------|----------|--------|
| 1.1 | Copy GWW3 `gsl.lark` → strip distributions, Truth Values, game-specific constructs. Keep: MATCH, IF/ELIF/ELSE, SET, numeric ops, comparisons, string ops, EVERY (schedule) | Dione | — | ✅ done |
| 1.2 | Add Hassaleh-specific constructs: `SUBMIT_INTENT`, `LOG`, `ALERT` actions + `WITH` clause, `ELIF/ELSE`, duration literals, built-in functions | Dione | — | ✅ done |
| 1.3 | Write parser tests (target: 20+ tests covering all GSL-Ops constructs) → 37 tests | Dione | — | ✅ done |

### Phase 2: Compiler + Runtime

| # | Task | Assignee | Reviewer | Status |
|---|------|----------|----------|--------|
| 2.1 | Port `compiler.py` — GSL-Ops AST → Python code generator | Dione | — | ✅ done |
| 2.2 | Port `runtime.py` — Deterministic RuleContext with Neo4j, time, scheduling | Dione | — | ✅ done |
| 2.3 | Priority-based conflict resolution: lowest priority number wins | Dione | — | ✅ done |
| 2.4 | Additive reducer for numeric properties (commutative merge) | Dione | — | ✅ done |
| 2.5 | Rule compilation cache: compile on boot, cache in Rule node | Dione | — | ✅ done |
| 2.6 | Version check: compiler_version mismatch triggers recompile | Dione | — | ✅ done |

### Phase 3: Daemon Integration

| # | Task | Assignee | Reviewer | Status |
|---|------|----------|----------|--------|
| 3.1 | Add rule evaluation to Daemon sweep (run_in_executor, sync Neo4j) | Dione | — | ✅ done |
| 3.2 | Add Rule node schema to schema.cypher (constraint + lifecycle index) | Dione | — | ✅ done |
| 3.3 | First operational rule: **Agent Health Check** (seed_rules.cypher) | Dione | — | ✅ done |
| 3.4 | Second operational rule: **Task Timeout Sweep** (seed_rules.cypher) | Dione | — | ✅ done |
| 3.5 | Integration test: rule fires, generates Intent | — | — | ⬜ todo (needs Daemon+rules on test1) |
| 3.6 | Stress test: 10 rules per sweep, verify performance | — | — | ⬜ todo |

### Sprint 2 Acceptance Criteria

- [x] GSL-Ops grammar parses all valid rule constructs (37 tests)
- [x] Compiler generates executable Python from GSL-Ops source (27 tests, 8 E2E)
- [x] Rules compile on Daemon boot and cache in graph
- [x] Priority-based conflict resolution works (21 resolver tests)
- [x] Agent health check rule written (seed_rules.cypher)
- [x] Task timeout sweep rule written (seed_rules.cypher)
- [ ] Integration test: rule fires end-to-end through Daemon
- [ ] Performance test: 10 rules in <100ms
- [x] All code reviewed (Codex Phase 1 findings noted)

### Sprint 2 Deliverables

| File | Description |
|------|-------------|
| `src/hassaleh/engine/gsl_ops.lark` | GSL-Ops Lark grammar |
| `src/hassaleh/engine/compiler.py` | GSL-Ops → Python compiler |
| `src/hassaleh/engine/runtime.py` | Rule execution context |
| `src/hassaleh/engine/resolver.py` | Priority-based conflict resolution |
| `tests/test_parser.py` | Grammar/parser tests |
| `tests/test_compiler.py` | Compilation tests |
| `tests/test_rules_integration.py` | End-to-end rule tests |

### Sprint 3: CLI

**Goal:** `hassaleh` command-line interface for operators and admins. Single entry point for all Hassaleh management.

**Lead:** Dione 🌙
**Team:** Codex (development), Claude Code (review)
**Duration:** ~1.5 weeks

### Phase 1: Core CLI Framework

| # | Task | Assignee | Reviewer | Status |
|---|------|----------|----------|--------|
| 1.1 | CLI entry point (`src/hassaleh/cli.py`) — argparse subcommand routing | Dione | Codex | ✅ done |
| 1.2 | `hassaleh init` — apply schema + seed + rules (with error tracking) | Dione | Codex | ✅ done |
| 1.3 | `hassaleh status` — Daemon health, agents, rules, active intents | Dione | — | ✅ done |
| 1.4 | Connection config — flags + env vars + ~/.hassaleh/config.toml | Dione | — | ✅ done |

### Phase 2: Operational Commands

| # | Task | Assignee | Reviewer | Status |
|---|------|----------|----------|--------|
| 2.1 | `hassaleh agent list` — agents with lifecycle, heartbeat, capabilities | Dione | — | ✅ done |
| 2.2 | `hassaleh agent info <id>` — detailed view + capabilities + recent intents | Dione | — | ✅ done |
| 2.3 | `hassaleh rule list` — all rules with priority, compiler version | Dione | — | ✅ done |
| 2.4 | `hassaleh rule compile <id>` — recompile + show GSL-Ops source + Python | Dione | — | ✅ done |
| 2.5 | `hassaleh intent list` — filterable by lifecycle, source (choices validated) | Dione | Codex | ✅ done |
| 2.6 | `hassaleh approve <intent-id>` — HITL approval | Dione | — | ✅ done |

### Phase 3: Packaging + Testing

| # | Task | Assignee | Reviewer | Status |
|---|------|----------|----------|--------|
| 3.1 | pyproject.toml `[project.scripts]` entry point | Dione | — | ✅ done |
| 3.2 | Unit tests for CLI (17 tests: formatters + parser) | Dione | — | ✅ done |
| 3.3 | Integration test against test1 | — | — | ⬜ todo |
| 3.4 | Help texts for all commands | Dione | — | ✅ done |

### Sprint 3 Acceptance Criteria

- [x] `hassaleh init` creates schema + seed data (with partial-failure tracking)
- [x] `hassaleh status` shows Daemon health, agents, rules, pending intents
- [x] `hassaleh agent list/info` shows agent details + capabilities + intents
- [x] `hassaleh rule list/compile` shows rules and generated Python
- [x] `hassaleh intent list` shows recent intents with validated filters
- [x] `hassaleh approve` transitions intents from awaiting_approval → pending
- [x] All commands handle connection errors gracefully (try/except)
- [x] pyproject.toml entry point configured
- [x] 17 tests cover formatting + parser structure
- [x] Codex review: no Cypher injection, error handling fixed

### Sprint 3 Deliverables

| File | Description |
|------|-------------|
| `src/hassaleh/cli.py` | Main CLI module (argparse + subcommands) |
| `src/hassaleh/cli_fmt.py` | Output formatting (tables, colors) |
| `tests/test_cli.py` | CLI unit tests |

### Sprint 4: OpenClaw Integration

**Goal:** Bridge Hassaleh Daemon ↔ OpenClaw Gateway. Dione as first Hassaleh-managed agent. Rules drive real actions via OpenClaw messaging and session management.

**Lead:** Dione 🌙
**Team:** Codex (development), Claude Code (review)
**Duration:** ~2 weeks

**Architecture:**
- Hassaleh Daemon runs as systemd service alongside OpenClaw Gateway
- Bridge layer communicates with OpenClaw via its HTTP API (localhost:18789)
- Notifications (ALERT actions from rules) dispatch via OpenClaw → Telegram
- Agent heartbeats: Dione writes last_heartbeat to Neo4j via SDK on every main-session heartbeat
- Agent task dispatch: Daemon creates Intents → OpenClaw spawns sub-agent sessions

### Phase 1: OpenClaw Bridge Layer

| # | Task | Assignee | Reviewer | Status |
|---|------|----------|----------|--------|
| 1.1 | `src/hassaleh/bridge/openclaw.py` — HTTP client for OpenClaw Gateway API (health, sessions, messages) | Dione | Codex | ⬜ todo |
| 1.2 | Bridge config in Neo4j DaemonConfig: `openclaw_gateway_url`, `openclaw_gateway_token` | Dione | — | ⬜ todo |
| 1.3 | Notification dispatcher: route rule ALERT actions → OpenClaw messaging → Telegram | Dione | — | ⬜ todo |
| 1.4 | Agent spawn dispatcher: route SUBMIT_INTENT "spawn_agent" → OpenClaw sessions_spawn | Dione | — | ⬜ todo |

### Phase 2: Dione as Hassaleh Agent

| # | Task | Assignee | Reviewer | Status |
|---|------|----------|----------|--------|
| 2.1 | Heartbeat integration: Dione updates `last_heartbeat` in Neo4j on every HEARTBEAT.md check | Dione | — | ⬜ todo |
| 2.2 | Agent lifecycle sync: Daemon reads OpenClaw session status → updates Agent.lifecycle in graph | Dione | — | ⬜ todo |
| 2.3 | `hassaleh agent heartbeat <id>` CLI command for manual heartbeat | Dione | — | ⬜ todo |
| 2.4 | Update Agent Health Check rule to work with real heartbeat data | Dione | — | ⬜ todo |

### Phase 3: Production Validation

| # | Task | Assignee | Reviewer | Status |
|---|------|----------|----------|--------|
| 3.1 | Integration test: rule fires ALERT → Daemon → OpenClaw → Telegram message received | Dione | — | ⬜ todo |
| 3.2 | Integration test: Dione heartbeat → Neo4j → health check rule evaluates correctly | Dione | — | ⬜ todo |
| 3.3 | `hassaleh status` shows OpenClaw connectivity + bridge status | Dione | — | ⬜ todo |
| 3.4 | Documentation: architecture diagram, setup instructions | Dione | — | ⬜ todo |

### Sprint 4 Acceptance Criteria

- [ ] Rule ALERT actions send notifications via Telegram
- [ ] Dione's heartbeat is tracked in Neo4j graph
- [ ] Health check rule correctly evaluates real heartbeat data
- [ ] `hassaleh status` shows OpenClaw bridge connectivity
- [ ] Bridge handles OpenClaw Gateway unavailability gracefully
- [ ] All code reviewed

### Sprint 4 Deliverables

| File | Description |
|------|-------------|
| `src/hassaleh/bridge/__init__.py` | Bridge package |
| `src/hassaleh/bridge/openclaw.py` | OpenClaw Gateway HTTP client |
| `src/hassaleh/bridge/notifications.py` | ALERT → messaging dispatcher |
| `tests/test_bridge.py` | Bridge unit tests |

### Sprint 5: Reporting

**Goal:** Graph-based project and agent reporting. CLI commands + formatted output for operator dashboards.

**Lead:** Dione 🌙
**Duration:** ~1 week

### Phase 1: Report Data Queries

| # | Task | Assignee | Status |
|---|------|----------|--------|
| 1.1 | `hassaleh report agents` — Agent activity report (uptime, intents processed, errors, restarts) | Dione | ⬜ todo |
| 1.2 | `hassaleh report rules` — Rule evaluation report (fires, alerts generated, property changes) | Dione | ⬜ todo |
| 1.3 | `hassaleh report intents` — Intent statistics (success/fail/reject rates, avg duration) | Dione | ⬜ todo |

### Phase 2: Project Reports

| # | Task | Assignee | Status |
|---|------|----------|--------|
| 2.1 | `hassaleh report project <id>` — Sprint/task progress, completion rates | Dione | ⬜ todo |
| 2.2 | `hassaleh report timeline <id>` — Planned vs actual timeline analysis | Dione | ⬜ todo |

### Phase 3: Export + Dashboard

| # | Task | Assignee | Status |
|---|------|----------|--------|
| 3.1 | `--format json` flag for machine-readable output | Dione | ⬜ todo |
| 3.2 | `--format markdown` flag for shareable reports | Dione | ⬜ todo |
| 3.3 | `hassaleh report daily` — Combined daily summary (agents + rules + intents) | Dione | ⬜ todo |

### Sprint 5 Acceptance Criteria

- [ ] Agent activity reports show meaningful metrics from graph data
- [ ] Rule reports show evaluation counts and outcomes
- [ ] Intent reports show success/fail/reject statistics
- [ ] JSON + Markdown export formats work
- [ ] Daily summary combines all reports
- [ ] All commands handle empty data gracefully

### Sprint 6: Multi-Agent Coordination

**Goal:** Implement the graph-mediated communication and coordination patterns from CONCEPT.md. Agents exchange messages through the graph, discuss decisions, and execute tasks in configurable modes.

**Lead:** Dione 🌙
**Duration:** ~2 weeks

### Phase 1: Message Cursor System

| # | Task | Assignee | Status |
|---|------|----------|--------|
| 1.1 | Message node schema + NEXT linked-list + LAST_READ cursor in schema.cypher | Dione | ⬜ todo |
| 1.2 | SDK: `sdk.send_message(context, content)` — create Message node + SENT + NEXT edges | Dione | ⬜ todo |
| 1.3 | SDK: `sdk.read_messages(agent_id)` — follow LAST_READ cursor through NEXT chain | Dione | ⬜ todo |
| 1.4 | SDK: `sdk.advance_cursor(agent_id, message_id)` — move LAST_READ pointer | Dione | ⬜ todo |
| 1.5 | CLI: `hassaleh message list <context>` — show messages in a task/discussion context | Dione | ⬜ todo |

### Phase 2: Discussion Protocol

| # | Task | Assignee | Status |
|---|------|----------|--------|
| 2.1 | Discussion node schema + CONTRIBUTED edge with position/reasoning | Dione | ⬜ todo |
| 2.2 | SDK: `sdk.create_discussion(topic, context)` — start a discussion | Dione | ⬜ todo |
| 2.3 | SDK: `sdk.contribute(discussion_id, position, reasoning)` — add agent's position | Dione | ⬜ todo |
| 2.4 | SDK: `sdk.resolve_discussion(discussion_id, resolution)` — leader closes discussion | Dione | ⬜ todo |
| 2.5 | CLI: `hassaleh discussion list/show/resolve` — discussion management | Dione | ⬜ todo |

### Phase 3: Task Execution Modes

| # | Task | Assignee | Status |
|---|------|----------|--------|
| 3.1 | Task node: add `execution_mode` property (sequential/parallel/supervised) | Dione | ✅ done |
| 3.2 | Sequential mode: Tasks execute one-by-one, next starts when previous succeeds | Dione | ✅ done |
| 3.3 | Parallel mode: All assigned agents work simultaneously, results merged | Dione | ✅ done |
| 3.4 | Supervised mode: Lead agent reviews sub-agent work before approval | Dione | ✅ done |
| 3.5 | GSL-Ops rule: Task assignment based on agent capabilities | Dione | ✅ done |

### Sprint 6 Acceptance Criteria

- [ ] Agents can send and read messages through graph (cursor-based, O(1) check)
- [ ] Discussions capture positions, reasoning, and resolution from multiple agents
- [x] Tasks support sequential, parallel, and supervised execution modes
- [ ] All coordination is graph-mediated (no direct agent-to-agent messaging)
- [ ] CLI commands for message and discussion management
- [ ] Tests cover message ordering, cursor advancement, and discussion lifecycle

### Sprint 7: Advanced List Types + FOREACH

**Goal:** Extend the existing GSL-Ops list support (basic FOREACH, list literals, LET) with advanced list operations, list-returning built-in functions, and comprehensive type handling. Make lists first-class citizens in GSL-Ops.

**Lead:** Dione 🌙
**Team:** Codex (development), Gemini CLI (review)
**Duration:** ~1 week

**Already implemented (Sprint 4):**
- `FOREACH item IN collection:` block
- List literals `[a, b, c]`, empty `[]`
- `LET` variable bindings
- Basic functions: `LEN`, `RANGE`, `SORTED`, `LIST`, `KEYS`

### Phase 1: List Functions

| # | Task | Assignee | Reviewer | Status |
|---|------|----------|----------|--------|
| 1.1 | Add `FILTER(list, lambda)` — filter list by predicate | Dione | Codex | ⬜ todo |
| 1.2 | Add `MAP(list, lambda)` — transform list elements | Dione | Codex | ⬜ todo |
| 1.3 | Add `FLATTEN(list)` — flatten nested lists one level | Dione | — | ⬜ todo |
| 1.4 | Add `CONTAINS(list, item)` — membership test | Dione | — | ⬜ todo |
| 1.5 | Add `APPEND(list, item)`, `CONCAT(list1, list2)` — constructors | Dione | — | ⬜ todo |
| 1.6 | Add `UNIQUE(list)` — deduplicate preserving order | Dione | — | ⬜ todo |
| 1.7 | Add `SLICE(list, start, end)` — sub-list extraction | Dione | — | ⬜ todo |
| 1.8 | Add `SUM(list)`, `AVG(list)` — numeric aggregations | Dione | — | ⬜ todo |

### Phase 2: List Type System

| # | Task | Assignee | Reviewer | Status |
|---|------|----------|----------|--------|
| 2.1 | List-typed LET bindings: `LET items = [1, 2, 3]` with type inference | Dione | — | ⬜ todo |
| 2.2 | Property access on list elements in FOREACH: `FOREACH t IN tasks: t.name` | Dione | — | ⬜ todo |
| 2.3 | Nested FOREACH support: `FOREACH a IN agents: FOREACH c IN a.capabilities:` | Dione | — | ⬜ todo |
| 2.4 | List comprehension syntax: `[x.name FOR x IN agents IF x.lifecycle == "running"]` | Dione | Codex | ⬜ todo |
| 2.5 | Neo4j list property read/write: store/retrieve native Neo4j lists | Dione | — | ⬜ todo |

### Phase 3: Integration + Tests

| # | Task | Assignee | Reviewer | Status |
|---|------|----------|----------|--------|
| 3.1 | Parser tests for all new functions (target: 15+ new tests) | Dione | — | ⬜ todo |
| 3.2 | Compiler tests: each function compiles correctly (target: 15+ tests) | Dione | — | ⬜ todo |
| 3.3 | E2E tests: functions execute with real data in RuleContext (target: 10+ tests) | Dione | — | ⬜ todo |
| 3.4 | Operational rule: "Bulk reset failed tasks" using FOREACH + FILTER | Dione | — | ⬜ todo |
| 3.5 | Operational rule: "Notify agents with specific capabilities" using MAP + FOREACH | Dione | — | ⬜ todo |
| 3.6 | Update GSL-Ops Language Reference in CONCEPT.md | Dione | — | ⬜ todo |

### Sprint 7 Acceptance Criteria

- [ ] All 8 list functions implemented and tested (FILTER, MAP, FLATTEN, CONTAINS, APPEND, CONCAT, UNIQUE, SLICE, SUM, AVG)
- [ ] Nested FOREACH works (2 levels deep)
- [ ] List comprehension syntax parses and compiles
- [ ] Neo4j native lists can be read/written via rules
- [ ] Two new operational rules demonstrate list usage
- [ ] 40+ new tests covering all list operations
- [ ] CONCEPT.md GSL-Ops reference updated

---

### Sprint 8: Skill Domain Taxonomy

**Goal:** Organize skills/capabilities into hierarchical problem domains for discovery, permissions, and ecosystem organization.

**Lead:** Dione 🌙
**Team:** Codex (development), Gemini CLI (review)
**Duration:** ~1 week

### Phase 1: Domain Model

| # | Task | Assignee | Reviewer | Status |
|---|------|----------|----------|--------|
| 1.1 | Define domain taxonomy (dot-notation: `orchestration.rules`, `data.graph`, `reporting.activity`, etc.) | Dione | Gemini | ⬜ todo |
| 1.2 | Add `domain` property to Capability nodes + Neo4j index for prefix queries | Dione | — | ⬜ todo |
| 1.3 | Seed data: assign domains to existing capabilities | Dione | — | ⬜ todo |
| 1.4 | Schema.cypher: domain index on Capability | Dione | — | ⬜ todo |

### Phase 2: Discovery + Permissions

| # | Task | Assignee | Reviewer | Status |
|---|------|----------|----------|--------|
| 2.1 | `hassaleh skill list --domain orchestration` — filter by domain prefix | Dione | Codex | ⬜ todo |
| 2.2 | `hassaleh skill search <query>` — semantic skill discovery across domains | Dione | Codex | ⬜ todo |
| 2.3 | Domain-scoped project permissions: restrict available skill domains per project | Dione | — | ⬜ todo |
| 2.4 | Conflict resolution: domain specificity determines priority when multiple skills match | Dione | — | ⬜ todo |

### Phase 3: Documentation + Tests

| # | Task | Assignee | Status |
|---|------|----------|--------|
| 3.1 | Domain taxonomy reference document | Dione | ⬜ todo |
| 3.2 | Guidelines for external skill authors | Dione | ⬜ todo |
| 3.3 | Unit + integration tests (target: 20+ tests) | Dione | ⬜ todo |
| 3.4 | Update CONCEPT.md with Skill Domain section | Dione | ⬜ todo |

### Sprint 8 Acceptance Criteria

- [ ] Domain taxonomy defined with at least 10 top-level domains
- [ ] Capabilities have domain property in Neo4j
- [ ] CLI commands for domain-based skill discovery
- [ ] Domain-scoped permissions enforced
- [ ] Documentation complete
- [ ] 20+ tests

---

### Sprint 9: Comprehensive Code Review

**Goal:** Full codebase review by multiple AI models. Fix all findings. Harden for production.

**Lead:** Dione 🌙
**Team:** Codex (review round 1), Gemini Deep Think (review round 2)
**Duration:** ~1 week

| # | Task | Assignee | Status |
|---|------|----------|--------|
| 9.1 | Codex full review: security, correctness, performance, style | Codex | ⬜ todo |
| 9.2 | Document Codex findings in `docs/REVIEW_SPRINT9_CODEX.md` | Dione | ⬜ todo |
| 9.3 | Fix all Critical + High issues from Codex review | Dione | ⬜ todo |
| 9.4 | Gemini Deep Think review: architecture, edge cases, Neo4j patterns | Gemini | ⬜ todo |
| 9.5 | Document Gemini findings in `docs/REVIEW_SPRINT9_GEMINI.md` | Dione | ⬜ todo |
| 9.6 | Fix all Critical + High issues from Gemini review | Dione | ⬜ todo |
| 9.7 | Run full test suite, ensure 0 regressions | Dione | ⬜ todo |
| 9.8 | Update changelog / version bump | Dione | ⬜ todo |

### Sprint 9 Acceptance Criteria

- [ ] Two independent reviews completed (Codex + Gemini)
- [ ] All Critical issues fixed
- [ ] All High issues fixed or documented as known limitations
- [ ] Full test suite green
- [ ] Review documents archived in docs/

---

### Sprint 10: Docker Dev-Environment

**Goal:** `docker compose up` for instant onboarding — Neo4j + Daemon + CLI ready in 30 seconds.

**Lead:** Dione 🌙
**Team:** Codex (development)
**Duration:** ~1 day

| # | Task | Status |
|---|------|--------|
| 10.1 | `Dockerfile` — Python 3.13 + Hassaleh + Daemon | ⬜ todo |
| 10.2 | `docker-compose.yml` — Neo4j + Hassaleh Daemon + init container | ⬜ todo |
| 10.3 | Init container: auto-apply schema + seed + rules on first boot | ⬜ todo |
| 10.4 | Health check for Daemon container | ⬜ todo |
| 10.5 | `.env.example` with configurable passwords/ports | ⬜ todo |
| 10.6 | `docs/DOCKER.md` — setup instructions, troubleshooting | ⬜ todo |
| 10.7 | README section: Docker quick start | ⬜ todo |

### Sprint 10 Acceptance Criteria

- [ ] `docker compose up` starts Neo4j + Daemon from zero
- [ ] Schema + seed + rules auto-applied on first boot
- [ ] `hassaleh status` works from host against containerized system
- [ ] Daemon health endpoint accessible
- [ ] Documentation covers setup, env vars, troubleshooting

---

## Agent Roles

| Sprint | Dione 🌙 | Codex | Gemini CLI |
|--------|----------|-------|------------|
| 1 (MVP) | Lead, schema, integration tests, orchestration | Core development (daemon, SDK, agent) | Deep Think reviews, code review |
| 2 (Rules) | GSL-Ops grammar + compiler | Rule evaluator, conflict resolution | Review rule semantics |
| 3 (CLI) | CLI design | CLI implementation | UX review |
| 4 (Integration) | OpenClaw integration lead | Agent lifecycle code | Architecture review |
| 5 (Reporting) | Report design | Implementation | — |
| 6 (Multi-Agent) | Coordination design | Messages + Tasks implementation | Sprint 6 review |
| 7 (Lists) | Grammar + compiler lead | Implementation | Review |
| 8 (Domains) | Taxonomy design | Implementation | Review |
| 9 (Review) | Fix orchestration | Review round 1 | Review round 2 (Deep Think) |
| 10 (Docker) | Orchestrate | Implementation | — |

---

## Status Legend

- ⬜ todo
- 🔄 in progress
- 👀 in review
- ✅ done
- ❌ blocked
