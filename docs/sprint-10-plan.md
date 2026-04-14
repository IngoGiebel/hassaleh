# Sprint 10 — Foundation & First End-to-End Validation

**Start:** 2026-04-14 16:00 CEST
**End:** 2026-04-21
**Orchestrator:** Dione (Claude Opus 4.6) via OpenClaw Cron, every 2 hours
**Orchestrator Reports:** Telegram after each cycle

---

## Goals

1. Validate the complete Hassaleh Intent pipeline (MVP Test)
2. Establish agent liveness tracking (Heartbeat)
3. Set up CI/CD pipeline
4. Document architecture
5. Security audit of the core Intent lifecycle

---

## Task Breakdown with Agent Scheduling

### Task A: MVP Test — Intent Pipeline Validation

| Phase | Agent | Worker | What | Output | Duration |
|-------|-------|--------|------|--------|----------|
| A1. Concept + Interface Spec | Dione | worker-opus | Write detailed spec: SDK methods, Intent schema, Daemon processing steps, expected Neo4j state transitions | `docs/spec-mvp-test.md` | 2h |
| A2. Concept Review | Inanna | worker-opus | Security review: Can a malicious agent abuse the Intent pipeline? Race conditions? Unvalidated state transitions? | Review comments in spec | 2h |
| A3. Concept Revision | Dione | worker-opus | Address Inanna's feedback, finalize spec | Updated spec | 1h |
| A4. Write Tests | Codex | worker-codex | Write pytest tests against the spec (TDD). Tests define expected behavior before implementation exists. | `tests/test_mvp_intent.py` | 2h |
| A5. Test Review | Inanna | worker-opus | Review tests: Are edge cases covered? Security scenarios? | Review comments | 1h |
| A6. Implementation | Gemini | worker-gemini | Implement against the tests. Make all tests pass. | Code changes in `src/` | 4h |
| A7. Code Review (Security) | Inanna | worker-opus | Security review of implementation | Review comments | 2h |
| A8. Code Review (Quality) | Codex | worker-codex | Quality review: types, style, test coverage | Review comments | 1h |
| A9. Revision Cycle | Gemini | worker-gemini | Fix issues from reviews. Repeat A7-A8 if needed. | Updated code | 2h |
| A10. Final OK | Dione | worker-opus | Run tests, verify spec compliance, merge decision | Commit or reject | 1h |

### Task B: Agent Heartbeat via OpenClaw

| Phase | Agent | Worker | What | Output | Duration |
|-------|-------|--------|------|--------|----------|
| B1. Concept + Interface Spec | Dione | worker-opus | Write spec: heartbeat SDK method, graph updates, Rule trigger, OpenClaw cron integration | `docs/spec-heartbeat.md` | 2h |
| B2. Concept Review | Inanna | worker-opus | Can heartbeats be spoofed? Replay attacks? Stale agent marked active? | Review comments | 1h |
| B3. Write Tests | Codex | worker-codex | pytest: heartbeat updates graph, lifecycle transitions, alert on missing heartbeat | `tests/test_heartbeat.py` | 2h |
| B4. Implementation | Gemini | worker-gemini | Implement SDK heartbeat method + OpenClaw cron integration | Code changes | 3h |
| B5. Code Review | Inanna + Codex | worker-opus + worker-codex | Parallel reviews | Review comments | 2h |
| B6. Revision + Final OK | Gemini → Dione | worker-gemini → worker-opus | Fix, re-test, merge | Commit | 2h |

### Task C: GitHub Actions CI

| Phase | Agent | Worker | What | Output | Duration |
|-------|-------|--------|------|--------|----------|
| C1. Setup | Codex | worker-codex | Create `.github/workflows/ci.yml`: lint (ruff), typecheck (mypy), test (pytest + Neo4j service container) | CI config | 3h |
| C2. Review | Dione | worker-opus | Review CI config, verify it runs | Review + test | 1h |
| C3. Fix | Codex | worker-codex | Address review feedback | Updated config | 1h |

### Task D: Architecture Documentation

| Phase | Agent | Worker | What | Output | Duration |
|-------|-------|--------|------|--------|----------|
| D1. Write | Gemini | worker-gemini | Create `docs/architecture.md` covering all components | Documentation | 3h |
| D2. Review | Dione | worker-opus | Technical accuracy review | Review comments | 1h |
| D3. Revision | Gemini | worker-gemini | Address feedback | Updated docs | 1h |

### Task E: Security Audit — Intent Lifecycle

| Phase | Agent | Worker | What | Output | Duration |
|-------|-------|--------|------|--------|----------|
| E1. Audit | Inanna | worker-opus | Deep security analysis of Intent state machine, capability enforcement, daemon privilege model | `docs/security-audit-intent-lifecycle.md` | 4h |
| E2. Review | Dione | worker-opus | Review findings, prioritize issues | Prioritized issue list | 1h |
| E3. Fix Critical | Gemini | worker-gemini | Fix any critical security issues found | Code changes | 2h |

---

## Daily Schedule

### Day 1 — Monday 2026-04-14

| Time | Agent | Worker | Task |
|------|-------|--------|------|
| 16:00 | **Orchestrator** | worker-opus | Sprint kickoff. Dispatch A1 + B1 |
| 16:00-18:00 | Dione | worker-opus | A1: Write MVP Test spec |
| 16:00-18:00 | Gemini | worker-gemini | D1: Start architecture docs (parallel, independent) |
| 18:00 | **Orchestrator** | worker-opus | Check A1 status. If done, dispatch A2 (Inanna) |
| 18:00-20:00 | Inanna | worker-opus | A2: Security review of MVP spec |
| 18:00-20:00 | Dione | worker-opus | B1: Write Heartbeat spec |
| 20:00 | **Orchestrator** | worker-opus | Status report. Queue overnight tasks. |

### Day 2 — Tuesday 2026-04-15

| Time | Agent | Worker | Task |
|------|-------|--------|------|
| 08:00 | **Orchestrator** | — | Check overnight results |
| 08:00-10:00 | Dione | worker-opus | A3: Revise MVP spec based on Inanna's review |
| 08:00-10:00 | Inanna | worker-opus | B2: Security review of Heartbeat spec |
| 10:00-12:00 | Codex | worker-codex | A4: Write MVP tests (TDD) |
| 10:00-12:00 | Gemini | worker-gemini | D1: Continue architecture docs |
| 12:00 | **Orchestrator** | — | Midday check. Dispatch next phases. |
| 12:00-14:00 | Inanna | worker-opus | A5: Review MVP tests |
| 12:00-14:00 | Codex | worker-codex | B3: Write Heartbeat tests |
| 14:00-18:00 | Gemini | worker-gemini | A6: Implement MVP Test (make tests pass) |
| 14:00-16:00 | Codex | worker-codex | C1: Set up GitHub Actions CI |
| 16:00 | **Orchestrator** | — | Status report |
| 18:00-20:00 | Inanna | worker-opus | A7: Security review of MVP implementation |
| 18:00 | **Orchestrator** | — | Evening report |

### Day 3 — Wednesday 2026-04-16

| Time | Agent | Worker | Task |
|------|-------|--------|------|
| 08:00-10:00 | Codex | worker-codex | A8: Quality review of MVP implementation |
| 08:00-10:00 | Gemini | worker-gemini | B4: Implement Heartbeat |
| 10:00-12:00 | Gemini | worker-gemini | A9: Fix MVP review issues |
| 10:00-12:00 | Inanna | worker-opus | E1: Start Intent lifecycle security audit |
| 12:00 | **Orchestrator** | — | Midday check |
| 12:00-14:00 | Dione | worker-opus | A10: Final MVP OK + merge |
| 14:00-16:00 | Inanna + Codex | worker-opus + codex | B5: Parallel Heartbeat reviews |
| 16:00-18:00 | Gemini | worker-gemini | B6: Fix Heartbeat review issues |
| 18:00 | Dione | worker-opus | B6: Final Heartbeat OK + merge |

### Day 4-5 — Thursday/Friday 2026-04-17-18

| Task | Agent | Notes |
|------|-------|-------|
| D2-D3: Architecture docs review + revision | Dione + Gemini | |
| C2-C3: CI review + fixes | Dione + Codex | |
| E1-E2: Security audit completion + review | Inanna + Dione | |
| E3: Critical security fixes | Gemini | If needed |
| Sprint retrospective | Dione | Friday 17:00, Telegram report |

---

## Orchestrator Behavior

The Sprint Orchestrator runs every 2 hours and:

1. **Reads** the task graph from Neo4j (all Sprint 10 tasks + their phases)
2. **Checks** which phases are completed, in progress, or blocked
3. **Dispatches** the next ready phase to the correct worker
4. **Monitors** running phases for timeout/failure
5. **Reports** to Ingo via Telegram:
   - Which agent is now working on what
   - What was completed since last cycle
   - Any blockers or issues
   - ETA for current sprint

### Dispatch Rules

- Never dispatch two tasks to the same worker simultaneously
- Prefer parallel work: if Gemini is busy, dispatch to Codex or Inanna
- If a phase fails, retry once. On second failure, escalate to Ingo.
- If a review has more than 3 revision cycles, escalate to Ingo.

### State Tracking

Each task phase is tracked in Neo4j:

```cypher
(:TaskPhase {
  id: "mvp-test-a1",
  task_id: "mvp-test",
  phase: "concept",
  agent: "dione",
  worker: "worker-opus",
  status: "pending|dispatched|running|completed|failed",
  dispatched_at: datetime,
  completed_at: datetime,
  output_path: "docs/spec-mvp-test.md",
  review_comments: "..."
})
```

---

## Worker Utilization Target

| Worker | Model | Subscription | Target Utilization |
|--------|-------|-------------|-------------------|
| worker-opus (Claude) | Opus 4.6 | Max 20x | High — Dione + Inanna share this worker |
| worker-gemini (Gemini) | 3.1 Pro | Ultra | High — primary implementer |
| worker-codex (Codex) | GPT-5.4 | Plus | Medium — testing + CI focused |

**Parallel execution targets per day:**
- Morning: 2-3 agents active simultaneously
- Afternoon: 2-3 agents active simultaneously
- Evening: 1-2 agents (lower priority tasks)

---

## Success Criteria

Sprint 10 is complete when:
- [ ] MVP Test: Agent submits Intent → Daemon processes → Result returned (all tests pass)
- [ ] Heartbeat: Agent sends heartbeat → Graph updated → Rule detects missing heartbeat
- [ ] CI: GitHub Actions runs lint + typecheck + tests on every push
- [ ] Architecture docs reviewed and merged
- [ ] Security audit completed, critical issues fixed
