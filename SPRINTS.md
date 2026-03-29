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
| 1.1 | Write `schema.cypher` — CREATE CONSTRAINT/INDEX for MVP nodes (Agent, Capability, Workspace, Intent, Task, DaemonConfig, QueryConfig, SystemVersion) | Dione | Codex | ⬜ todo |
| 1.2 | Write `seed.cypher` — Create initial Agent (Dione), Capability (`ls`), Task, Workspace, DaemonConfig, QueryConfig, SystemVersion nodes | Dione | Gemini | ⬜ todo |
| 1.3 | Create `hassaleh` Neo4j database + `hassaleh_daemon` (r/w) and `hassaleh_reader` (r/o) users | Dione | — | ⬜ todo |
| 1.4 | Apply schema + seed to Neo4j, verify with Cypher queries | Dione | — | ⬜ todo |

### Phase 2: Daemon MVP

| # | Task | Assignee | Reviewer | Status |
|---|------|----------|----------|--------|
| 2.1 | Write `setup_os.sh` — Create OS users (hassaleh-svc, hassaleh-agent, hassaleh-fs, hassaleh-writer, hassaleh-exec, hassaleh-net, hassaleh-pkg), configure `/etc/sudoers.d/hassaleh`, install systemd unit | Codex | Dione | ⬜ todo |
| 2.2 | Write `daemon.py` — asyncio event loop, Neo4j connection as `hassaleh_daemon`, read pending Intents | Codex | Dione + Gemini | ⬜ todo |
| 2.3 | Add async action workers — `asyncio.create_subprocess_exec` with `sudo -n -u` delegation | Codex | Dione | ⬜ todo |
| 2.4 | Add Intent feedback — write lifecycle/stdout/stderr/error_reason back to Intent nodes | Codex | Dione | ⬜ todo |
| 2.5 | Add zombie Intent recovery on boot — transition stale `running` Intents to `failed` | Codex | Gemini | ⬜ todo |
| 2.6 | Add graceful shutdown — SIGTERM handler, kill subprocesses, update Intents | Codex | Dione | ⬜ todo |
| 2.7 | Add systemd watchdog integration (`sd_notify`) | Codex | Dione | ⬜ todo |
| 2.8 | Add health endpoint (minimal HTTP, port from DaemonConfig) | Codex | Gemini | ⬜ todo |
| 2.9 | Write systemd unit file `hassaleh-daemon.service` | Dione | Codex | ⬜ todo |
| 2.10 | Integration test: Daemon processes a manually-created Intent in Neo4j | Dione | — | ⬜ todo |

### Phase 2.5: Agent SDK

| # | Task | Assignee | Reviewer | Status |
|---|------|----------|----------|--------|
| 2.5.1 | Write `sdk.py` — `hassaleh.query()` with parameterized queries, per-query timeout from QueryConfig | Codex | Dione + Gemini | ⬜ todo |
| 2.5.2 | Add `hassaleh.submit_intent()` — create Intent node via Daemon API or direct write | Codex | Dione | ⬜ todo |
| 2.5.3 | Add convenience methods: `hassaleh.my_tasks()`, `hassaleh.project_status()` | Codex | Dione | ⬜ todo |
| 2.5.4 | Credential bootstrapping — read NEO4J_URI/USER/PASSWORD from env vars | Codex | Dione | ⬜ todo |
| 2.5.5 | Unit tests for SDK (mock Neo4j driver) | Codex | Gemini | ⬜ todo |

### Phase 3: Blackboard Spike (End-to-End Proof)

| # | Task | Assignee | Reviewer | Status |
|---|------|----------|----------|--------|
| 3.1 | Write `agent_dummy.py` — test agent that claims a Task, writes a file to Workspace, submits Intent to execute `ls -la`, reads result | Codex | Dione | ⬜ todo |
| 3.2 | Full loop test: start Daemon, start agent, verify Intent → action → feedback → agent reads stdout | Dione | Gemini | ⬜ todo |
| 3.3 | Verify: agent cannot write to Neo4j (read-only user enforcement) | Dione | — | ⬜ todo |
| 3.4 | Verify: agent cannot execute capabilities it doesn't have (HAS_CAPABILITY check) | Dione | — | ⬜ todo |
| 3.5 | Stress test: 10 rapid Intents, verify all processed correctly | Dione | Codex | ⬜ todo |
| 3.6 | Chaos test: kill Daemon mid-processing, restart, verify zombie recovery | Dione | Gemini | ⬜ todo |

### Sprint 1 Acceptance Criteria

- [ ] Daemon starts as systemd service, processes Intents, writes feedback
- [ ] Agent can query graph (read-only) and submit Intents
- [ ] Full loop proven: agent → Intent → Daemon → OS action → stdout → agent reads result
- [ ] Read-only enforcement verified (agent cannot write to Neo4j)
- [ ] Capability permission check verified (agent cannot use unassigned capabilities)
- [ ] Zombie Intent recovery works after Daemon crash
- [ ] Graceful shutdown preserves Intent state
- [ ] All code reviewed by at least one other model

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

## Backlog — Future Sprints

### Sprint 2: Rule Engine (GSL-Ops)

**Goal:** Port GSL-Ops from GWW3. Compile rules on boot. Priority-based conflict resolution.

- Port Lark grammar (stripped of distributions/Truth Values)
- GSL-Ops → Python compiler
- Priority-based rule conflict resolution (enums)
- Additive reduction for numeric properties
- Rule evaluation in Daemon tick loop
- First operational rules: agent health check, task timeout sweep

### Sprint 3: CLI

**Goal:** `hassaleh` command-line interface for operators and admins.

- `hassaleh init` — initialize Neo4j schema + seed data
- `hassaleh status` — show Daemon health, agent states, pending Intents
- `hassaleh report` — generate project/agent activity report
- `hassaleh approve <intent-id>` — HITL approval for sensitive actions
- `hassaleh agent list/start/stop`

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
