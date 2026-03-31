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

**Goal:** Connect Hassaleh to OpenClaw for real agent orchestration.

- Daemon spawns agents via OpenClaw ACP runtime
- Notification dispatch via OpenClaw messaging
- Dione as first real Hassaleh-managed agent
- First operational rules running in production

### Sprint 5: Reporting

**Goal:** Graph-based project and agent reporting.

- Reporting agent queries graph for status
- Project progress reports (sprint/task/milestone)
- Agent activity reports (uptime, errors, costs)
- Timeline analysis (planned vs actual)

### Sprint 6: Multi-Agent Coordination

**Goal:** Discussion protocol, consensus mechanism, multi-agent task execution.

- Message cursor pattern for inter-agent communication
- Discussion nodes with voting + leader decision
- Sequential/parallel/supervised task execution modes
- Workspace permission management (auto-assign on project join)

### Sprint N: List Types + FOREACH

**Goal:** Add list/collection types to GSL-Ops and a `FOREACH var:` iteration construct.

- Define list literal syntax (e.g. `[a, b, c]`) and list-returning functions
- `FOREACH item IN collection:` block — iterates over lists from MATCH results, properties, or expressions
- Type system extension: lists as first-class values in LET bindings
- Prerequisite: Decide on list representation in Neo4j (native lists vs. relationship chains)
- Use cases: "Reset all failed tasks", "Notify all agents with capability X", "Iterate agent capabilities"

### Sprint 7: Docker Dev-Environment

**Goal:** `docker compose up` for easy onboarding.

- Neo4j container with schema + seed
- Simulated OS-level isolation (hassaleh-svc/agent/exec users)
- Daemon container
- Sample agent container
- Documentation for contributors

---

## Agent Roles

| Sprint | Dione 🌙 | Codex | Gemini CLI |
|--------|----------|-------|------------|
| 1 (MVP) | Lead, schema, integration tests, orchestration | Core development (daemon, SDK, agent) | Deep Think reviews, code review |
| 2 (Rules) | GSL-Ops grammar + compiler | Rule evaluator, conflict resolution | Review rule semantics |
| 3 (CLI) | CLI design | CLI implementation | UX review |
| 4 (Integration) | OpenClaw integration lead | Agent lifecycle code | Architecture review |
| 5+ | Orchestrate | Develop | Review |

---

## Status Legend

- ⬜ todo
- 🔄 in progress
- 👀 in review
- ✅ done
- ❌ blocked
