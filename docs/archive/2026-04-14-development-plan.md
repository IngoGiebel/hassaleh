> ⚠️ **ARCHIVED — superseded.** This is the original 14 April 2026 development
> proposal. It pre-dates the actual sprint history captured in
> [`SPRINTS.md`](../../SPRINTS.md) and the family-split into separate repos
> (`hassaleh`, `hassaleh-nexus`, `hassaleh-rabt`). The phased roadmap below
> was never executed in this form — the project ran as a numbered-sprint
> series instead, and is at v1.1 (frozen Sprint-13) at time of archive
> (2026-05-03). Kept for historical context; do NOT follow as a current plan.
>
> **Imported on 2026-05-03** from
> `~/.openclaw/workspace/reports/hassaleh-development-plan.md` per the
> "all project artifacts live in `~/projects/hassaleh`" rule.

---

# Hassaleh Development Plan — Agent-Orchestrated Continuous Development

**Version:** 1.0
**Date:** 2026-04-14
**Author:** Dione
**Project:** https://github.com/IngoGiebel/hassaleh

---

## 1. Project Summary

Hassaleh is a **graph-native agent orchestration framework** where all configuration, state, rules, and coordination live in Neo4j. It replaces scattered YAML/JSON configs with a unified graph model.

### Core Components

| Component | Status | Description |
|-----------|--------|-------------|
| Daemon (`daemon.py`) | Alpha | Async Intent processor, rule evaluator, systemd integration |
| SDK (`sdk.py`) | Alpha | Read-only graph access + Intent submission for agents |
| CLI (`cli.py`) | Alpha | Operational interface (status, init, agent/rule/intent/skill management) |
| GSL-Ops Engine | Alpha | Custom rule language → Lark parser → Python bytecode compiler |
| OpenClaw Bridge | Alpha | Telegram/Discord notifications via Gateway |
| Docker Compose | Working | Neo4j 5+ containerized deployment |

### Technology Stack
- Python 3.13+, Lark parser, Neo4j 5+, asyncio, Docker Compose
- License: MIT

---

## 2. Agent Roles for Development

### 2.1 Assignment Matrix

| Agent | Role | Responsibilities |
|-------|------|-----------------|
| **Dione** (Claude Opus 4.6) | Lead Architect & Orchestrator | Architecture decisions, GSL-Ops language design, overall coordination, Moltbook posts |
| **Inanna** (Claude Opus 4.6) | Security Architect & Reviewer | Capability model, Intent lifecycle security, permission graph design, penetration review |
| **Gemini Worker** | Implementation & Testing | Feature implementation, test writing, documentation, Neo4j query optimization |
| **Codex Worker** | Code Quality & CI | Linting, type checking, test runner, CI/CD pipeline, dependency management |

### 2.2 Review Protocol

Every code change goes through **two independent reviews** before merge:

```
Author (any agent) → Reviewer 1 (different agent) → Reviewer 2 (different agent) → Merge
```

| Review Type | Reviewer | Focus |
|-------------|----------|-------|
| **Architecture Review** | Dione | Graph schema design, API surface, GSL-Ops grammar changes |
| **Security Review** | Inanna | Capability boundaries, privilege escalation paths, Intent validation |
| **Code Quality Review** | Codex | Type safety, test coverage, performance, Python best practices |
| **Integration Review** | Gemini | Neo4j query correctness, Docker compatibility, OpenClaw bridge |

---

## 3. Development Cadence

### 3.1 Daily Cycle

| Time | Activity | Agent |
|------|----------|-------|
| 03:00 | System maintenance | Dione (cron) |
| 09:00 | Sprint standup: review overnight work, pick next tasks | Dione (orchestrator) |
| 09:15-12:00 | Morning development sprint (2-3 parallel tasks) | Gemini + Codex |
| 12:00 | Mid-day review checkpoint | Dione + Inanna |
| 13:00-17:00 | Afternoon development sprint | Gemini + Codex |
| 15:00 | Market analysis pipeline (existing) | Dione |
| 17:00 | End-of-day review, PR creation, summary to Ingo | Dione |
| Overnight | Long-running tests, Neo4j query benchmarks | Codex (cron) |

### 3.2 Weekly Cycle

| Day | Focus |
|-----|-------|
| Monday | Sprint planning, architecture decisions |
| Tue-Thu | Implementation sprints |
| Friday | Integration testing, documentation, Moltbook technical post |
| Saturday | Bug fixes, technical debt |
| Sunday | Exploration, prototyping, research |

---

## 4. Development Phases

### Phase 1: Foundation (Week 1-2)

**Goal:** Get Hassaleh running locally, establish CI, verify all existing components.

| Task | Agent | Priority |
|------|-------|----------|
| Clone repo, set up local dev environment | Dione | P0 |
| Docker Compose: Neo4j + Hassaleh daemon | Gemini | P0 |
| Run existing test suite, fix failures | Codex | P0 |
| Security audit of Intent lifecycle | Inanna | P0 |
| Set up GitHub Actions CI (lint, type-check, test) | Codex | P1 |
| Document current architecture in `docs/` | Gemini | P1 |
| Create `CLAUDE.md` for the project | Dione | P1 |

### Phase 2: Core Hardening (Week 3-4)

**Goal:** Stabilize the daemon, harden the SDK, complete GSL-Ops.

| Task | Agent | Priority |
|------|-------|----------|
| Daemon crash recovery: Intent state machine robustness | Gemini | P0 |
| SDK: error handling, retry logic, connection pooling | Gemini | P0 |
| GSL-Ops: complete grammar coverage (WHEN, EMIT, CHAIN) | Dione | P0 |
| Permission graph: capability inheritance, domain scoping | Inanna | P0 |
| Neo4j query optimization (index strategy, query plans) | Codex | P1 |
| Integration tests with real Neo4j (not mocked) | Codex | P1 |
| OpenClaw Bridge: bidirectional communication | Gemini | P1 |

### Phase 3: OpenClaw Integration (Week 5-6)

**Goal:** Hassaleh becomes usable by OpenClaw agents in production.

| Task | Agent | Priority |
|------|-------|----------|
| Agent registration flow (Dione/Inanna register in graph) | Dione | P0 |
| Skill taxonomy: define initial domain tree | Dione + Inanna | P0 |
| Intent-based tool execution: agent submits Intent, daemon executes | Gemini | P0 |
| GSL-Ops rule library: starter rules for common patterns | Dione | P1 |
| Monitoring dashboard (Neo4j Browser queries) | Codex | P1 |
| Production Docker config (resource limits, volumes, backups) | Codex | P1 |

### Phase 4: Self-Orchestration (Week 7-8)

**Goal:** Hassaleh orchestrates its own development agents.

| Task | Agent | Priority |
|------|-------|----------|
| Dog-fooding: Hassaleh manages Dione/Inanna/Gemini tasks | Dione | P0 |
| Rule-based code review triggers | Inanna | P0 |
| Automated test execution via Intents | Codex | P1 |
| Performance benchmarks: 100+ concurrent Intents | Gemini | P1 |
| Public release preparation (docs, examples, README) | Dione | P1 |

---

## 5. Neo4j Instance Strategy

### Recommended Setup

| Instance | Purpose | Port | Data Location |
|----------|---------|------|---------------|
| **System** (apt) | General purpose, experimentation | 7687/7474 | `/var/lib/neo4j/data/` |
| **Hassaleh Dev** (Docker) | Development database | 7690/7476 | `~/projects/hassaleh/neo4j/data/` |
| **Hassaleh Test** (Docker) | CI/test database (ephemeral) | 7691/7477 | tmpfs (destroyed after tests) |

### Docker Compose for Hassaleh

```yaml
services:
  neo4j-dev:
    image: neo4j:5-community
    ports:
      - "7690:7687"
      - "7476:7474"
    environment:
      NEO4J_AUTH: neo4j/hassaleh-dev
      NEO4J_PLUGINS: '["apoc"]'
    volumes:
      - ./neo4j/data:/data
      - ./neo4j/logs:/logs

  neo4j-test:
    image: neo4j:5-community
    ports:
      - "7691:7687"
      - "7477:7474"
    environment:
      NEO4J_AUTH: neo4j/hassaleh-test
    tmpfs:
      - /data
    profiles:
      - test
```

---

## 6. Orchestration Architecture

### How Agents Interact with Hassaleh

```
┌─────────────────────────────────────────┐
│ OpenClaw Gateway                         │
│                                         │
│  ┌───────┐  ┌───────┐  ┌───────┐       │
│  │Dione  │  │Inanna │  │Gemini │       │
│  │(Opus) │  │(Opus) │  │(Pro)  │       │
│  └──┬────┘  └──┬────┘  └──┬────┘       │
│     │          │          │             │
│     └──────────┼──────────┘             │
│                │                        │
│         hassaleh.query()                │
│         hassaleh.submit_intent()        │
│                │                        │
└────────────────┼────────────────────────┘
                 │
         ┌───────▼───────┐
         │ Hassaleh      │
         │ Daemon        │
         │ (Python async)│
         └───────┬───────┘
                 │
         ┌───────▼───────┐
         │ Neo4j 5+      │
         │ (Graph DB)    │
         └───────────────┘
```

### Development Task Flow

```
1. Dione creates Task node in Neo4j
2. Assigns Agent + Priority + Dependencies
3. Agent picks up Task via SDK
4. Agent submits Intent (code change)
5. Daemon routes to Reviewers
6. Two approvals → merge
7. Codex runs tests → updates Task status
8. Dione reports to Ingo
```

---

## 7. Cron Jobs for Hassaleh Development

| Job | Schedule | Agent | Purpose |
|-----|----------|-------|---------|
| `hassaleh-standup` | 09:00 CEST | worker-opus | Review tasks, pick sprint items |
| `hassaleh-review` | 12:00, 17:00 CEST | worker-opus | Code review checkpoint |
| `hassaleh-tests` | 02:00 CEST | worker-codex | Nightly full test suite + benchmarks |
| `hassaleh-report` | 17:30 CEST | worker-opus | Daily summary to Ingo via Telegram |

These should be created once the repo is cloned and the dev environment is set up.

---

## 8. Quality Gates

### Every PR Must Pass

- [ ] Two independent agent reviews (different agents)
- [ ] All tests green
- [ ] Type checking passes (`mypy --strict`)
- [ ] Security review for any capability/permission changes (Inanna)
- [ ] Neo4j schema migration documented
- [ ] GSL-Ops grammar changes include parser tests

### Weekly Quality Report

Dione compiles a weekly report:
- Lines changed, test coverage delta
- Open issues, closed PRs
- Architecture decision log updates
- Moltbook community feedback on Hassaleh posts

---

## 9. Moltbook Integration

Hassaleh development updates get posted to Moltbook:

| Post Type | Submolt | Author | Frequency |
|-----------|---------|--------|-----------|
| Technical architecture deep-dives | `engineering` | Dione | Weekly (Friday) |
| Security analysis of graph permissions | `powsec` | Inanna | Bi-weekly |
| Progress updates | `openclaw-explorers` | Dione | Major milestones |

---

## 10. First Steps (Tomorrow)

1. **Clone the repo:** `git clone https://github.com/IngoGiebel/hassaleh.git ~/projects/hassaleh`
2. **Start Neo4j:** `cd ~/projects/hassaleh && docker compose up -d`
3. **Install Hassaleh:** `pip3.14 install -e .`
4. **Run `hassaleh init` and `hassaleh status`**
5. **Create `CLAUDE.md`** with project conventions
6. **Set up GitHub CLI auth** (`gh auth login`) for PR workflows
7. **Create first cron jobs** for development cadence

Awaiting Ingo's go-ahead to proceed with Phase 1.
