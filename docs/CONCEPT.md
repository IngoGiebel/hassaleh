# Hassaleh — Concept Document

*Created: 2026-03-29 by Ingo Giebel + Dione 🌙*
*Status: Draft v0.1 — Initial Architecture*

---

## 1. Vision

Hassaleh is a **graph-native agentic framework** where the entire configuration, state, and coordination of AI agents is stored in a **Neo4j graph database**.

A capable AI agent (e.g. Claude Opus, steered via OpenClaw) can read the graph to:
- Generate reports on agent activity and project progress
- Understand the full system topology (who does what, with which tools, on which projects)
- Audit rule execution and agent decisions

The graph is the **single source of truth** — no YAML files, no scattered configs, no hidden state.

---

## 2. Core Principles

1. **The graph IS the runtime configuration** — Agents, tools, permissions, schedules, and rules live as nodes and edges in Neo4j.
2. **Rule-based orchestration** — Agent startup, task assignment, failure recovery, and synchronization are governed by declarative rules (GSL or similar), not imperative code.
3. **Database-mediated communication** — Agents exchange information through the graph, not through direct messaging. Every contribution and consensus is persisted.
4. **Auditable by design** — Every agent action, decision, and discussion is logged as graph nodes. A reporting agent can reconstruct the full history.
5. **Fail-safe operation** — Rules define what happens when an agent fails, runs out of credits, or becomes unresponsive. Recovery is automatic and rule-driven.
6. **Ubuntu-first** — Initial platform support is Ubuntu Linux. Other platforms may follow.

---

## 3. Node Types

### 3.1 Model

An AI model that can be instantiated to power an agent.

```
(:Model {
    id: "claude-opus-4-6",
    name: "Claude Opus 4.6",
    provider: "anthropic",
    type: "llm",                        # llm | embedding | vision | tts | stt
    context_window: 200000,
    supports_tools: true,
    supports_vision: true,
    
    # Instantiation methods (how to start this model)
    invoke_cli: "claude --model opus ...",
    invoke_adk: "anthropic.messages.create(...)",
    invoke_openclaw: "sessions_spawn runtime=acp ...",
    
    # Operational
    cost_per_mtok_input: 15.0,
    cost_per_mtok_output: 75.0,
    rate_limit_rpm: 50,
    auth_method: "oauth",               # oauth | api_key | local
    status: "available"                  # available | degraded | unavailable
})
```

### 3.2 Agent

An AI agent with defined capabilities. Each agent has one or more models assigned (with priority for fallback).

```
(:Agent {
    id: "dione",
    name: "Dione",
    emoji: "🌙",
    description: "Finance agent — portfolio monitoring, market analysis, reporting",
    personality: "Sachlich-präzise, aber locker und gerne mit Humor",
    
    # Runtime
    runtime: "openclaw",                # openclaw | adk | standalone | docker
    workspace: "/home/uranus/moltbot-workspace",
    config_path: "~/.openclaw/openclaw.json",
    
    # State
    status: "active",                   # active | idle | failed | suspended
    last_heartbeat: datetime(),
    credits_remaining: null,            # null = unlimited (subscription)
    health_check_interval_sec: 300
})
```

**Relationships:**
```
(Agent)-[:USES_MODEL {priority: 1, fallback: false}]->(Model)
(Agent)-[:USES_MODEL {priority: 2, fallback: true}]->(Model)
```

### 3.3 Tool

An MCP server or standalone tool available to agents.

```
(:Tool {
    id: "firecrawl",
    name: "Firecrawl",
    type: "mcp_server",                 # mcp_server | cli | api | builtin
    description: "Web scraping, search, crawling via Firecrawl API",
    
    # Invocation
    invoke_command: "mcporter call firecrawl.*",
    config_path: "config/mcporter.json",
    requires_auth: true,
    auth_type: "api_key",
    
    # Operational constraints
    rate_limit: "500 credits/month",
    cost_model: "per_call",
    status: "available"
})
```

**Relationships:**
```
(Agent)-[:HAS_TOOL {granted_at: datetime()}]->(Tool)
(Project)-[:REQUIRES_TOOL]->(Tool)
```

### 3.4 Skill

A reusable skill package (e.g. OpenClaw skills, ClawHub skills).

```
(:Skill {
    id: "firecrawl-search",
    name: "Firecrawl Search",
    description: "Web search with full page content extraction",
    skill_path: "~/.agents/skills/firecrawl-search/SKILL.md",
    version: "1.0.0",
    source: "clawhub",                  # clawhub | local | github
    status: "installed"
})
```

**Relationships:**
```
(Agent)-[:HAS_SKILL]->(Skill)
(Skill)-[:DEPENDS_ON]->(Tool)
(Project)-[:REQUIRES_SKILL]->(Skill)
```

### 3.5 SystemAction

A permitted action on the host system. Actions are whitelisted — anything not explicitly permitted is denied.

```
(:SystemAction {
    id: "file-read-workspace",
    name: "Read workspace files",
    type: "filesystem",                 # filesystem | process | network | package | sudo
    scope: "/home/uranus/moltbot-workspace/**",
    permission: "read",                 # read | write | execute | admin
    requires_confirmation: false,
    os: "ubuntu"                        # ubuntu (initially only)
})
```

**Relationships:**
```
(Agent)-[:PERMITTED {granted_by: "ingo", granted_at: datetime()}]->(SystemAction)
(Project)-[:ALLOWS_ACTION]->(SystemAction)
```

### 3.6 CronJob

A scheduled recurring or one-shot task.

```
(:CronJob {
    id: "daily-maintenance",
    name: "Daily System Maintenance",
    schedule: "0 4 * * *",             # cron expression
    timezone: "Europe/Berlin",
    command: "bash scripts/daily-maintenance.sh",
    
    enabled: true,
    last_run: datetime(),
    last_status: "success",            # success | failed | skipped | running
    next_run: datetime(),
    retry_on_failure: true,
    max_retries: 3
})
```

**Relationships:**
```
(CronJob)-[:ASSIGNED_TO]->(Agent)
(CronJob)-[:BELONGS_TO]->(Project)
(CronJob)-[:TRIGGERS]->(CronJob)       # chained jobs
```

### 3.7 Project

The central organizing node. Projects form a **hierarchy** (parent/child) and can represent anything from a long-running endeavor to a recurring task.

```
(:Project {
    id: "gww3",
    name: "Games of World War 3",
    description: "AI-agent-based geopolitical real-time simulation...",
    type: "development",               # development | operations | recurring | research
    
    # Timeline
    started_at: datetime(),
    target_date: null,
    status: "active",                  # active | paused | completed | failed | archived
    
    # Artifacts
    workspace_path: "projects/games-of-ww3/",
    repo_url: "https://github.com/IngoGiebel/games-of-ww3",
    
    # Context
    context_memory: "projects/games-of-ww3/AGENTS.md",
    priority: "high"                   # critical | high | medium | low
})
```

**Hierarchical relationships:**
```
(Project)-[:HAS_SUBPROJECT]->(Project)
(Project)-[:DEPENDS_ON]->(Project)
```

**Agent assignment:**
```
(Project)-[:HAS_AGENT {role: "orchestrator", since: datetime()}]->(Agent)
(Project)-[:HAS_AGENT {role: "developer", since: datetime()}]->(Agent)
(Project)-[:HAS_AGENT {role: "reviewer", since: datetime()}]->(Agent)
```

**Other relationships:**
```
(Project)-[:REQUIRES_TOOL]->(Tool)
(Project)-[:REQUIRES_SKILL]->(Skill)
(Project)-[:ALLOWS_ACTION]->(SystemAction)
(Project)-[:HAS_CRONJOB]->(CronJob)
(Project)-[:HAS_SPRINT]->(Sprint)
(Project)-[:HAS_ARTIFACT]->(Artifact)
(Project)-[:GOVERNED_BY]->(Rule)
```

### 3.8 Sprint

A time-boxed work phase within a project.

```
(:Sprint {
    id: "gww3-sprint-4",
    name: "Sprint 4: Rules Engine (GSL)",
    description: "Build the GSL parser, evaluator, and intent reducer",
    
    started_at: datetime(),
    target_date: date("2026-04-15"),
    status: "active"                   # planned | active | completed | failed
})
```

**Relationships:**
```
(Sprint)-[:HAS_TASK]->(Task)
(Sprint)-[:HAS_MILESTONE]->(Milestone)
(Sprint)-[:NEXT]->(Sprint)             # ordered sequence
```

### 3.9 Task

A concrete unit of work within a sprint.

```
(:Task {
    id: "gww3-s4-parser-v3",
    name: "GSL Parser v3 — Python-style colon blocks",
    description: "Rewrite .lark grammar with MATCH …: / IF …: syntax",
    
    status: "completed",               # todo | in_progress | review | completed | failed | blocked
    assigned_agent: "dione",
    completed_at: datetime(),
    verification_method: "pytest tests/engine/test_gsl_parser.py"
})
```

### 3.10 Milestone

A checkpoint with verifiable acceptance criteria.

```
(:Milestone {
    id: "gww3-s4-parser-passes",
    name: "GSL parser passes all 26 tests",
    check_command: "PYTHONPATH=src python3.13 -m pytest tests/engine/ -q",
    check_expected: "26 passed",
    
    status: "reached",                 # pending | reached | failed
    reached_at: datetime()
})
```

### 3.11 Artifact

A file, directory, or external resource linked to a project.

```
(:Artifact {
    id: "gww3-gsl-grammar",
    name: "GSL Grammar v3",
    type: "file",                      # file | directory | database | url | docker_image
    path: "src/gww3/engine/parser/gsl.lark",
    description: "Lark EBNF grammar for the GSL rule language"
})
```

### 3.12 Rule

A declarative rule governing agent behavior within a project. Syntax: GSL or a simplified subset.

```
(:Rule {
    id: "agent-health-check",
    version: 1,
    category: ["operations"],
    
    rule_text: "...",                  # GSL source
    compiled_python: "...",            # Transpiled Python
    compiler_version: "gsl-0.3",
    
    description: "Check agent health every 5 minutes. If unresponsive for 3 checks, restart. If restart fails, notify Ingo.",
    author: "ingo"
})
```

**Relationships:**
```
(Project)-[:GOVERNED_BY]->(Rule)
(Rule)-[:APPLIES_TO]->(Agent)
```

### 3.13 AgentLog

An immutable log entry for agent actions and communications.

```
(:AgentLog {
    id: uuid(),
    timestamp: datetime(),
    type: "action",                    # action | message | decision | error | consensus
    content: "Compiled GSL rule sanctions-economic-impact to Python",
    metadata: '{"rule_id": "...", "duration_ms": 370}'
})
```

**Relationships:**
```
(Agent)-[:LOGGED]->(AgentLog)
(AgentLog)-[:IN_CONTEXT_OF]->(Project)
(AgentLog)-[:IN_CONTEXT_OF]->(Task)
```

### 3.14 Discussion

A structured multi-agent discussion node for collaborative decision-making.

```
(:Discussion {
    id: uuid(),
    topic: "Should we use LALR or Earley parser for GSL?",
    status: "resolved",                # open | voting | resolved | deadlocked
    resolution: "Earley — handles ambiguity, performance is sufficient",
    resolved_at: datetime()
})
```

**Relationships:**
```
(Discussion)-[:IN_CONTEXT_OF]->(Project)
(Discussion)-[:IN_CONTEXT_OF]->(Task)
(Agent)-[:CONTRIBUTED {position: "pro-earley", reasoning: "...", timestamp: datetime()}]->(Discussion)
(Discussion)-[:DECIDED_BY]->(Agent)    # the project lead who made the call
```

---

## 4. Orchestration Rules

Agent coordination is **purely rule-based**. Rules define:

### 4.1 Agent Lifecycle
- **Health monitoring:** How often to check if an agent is alive, how many missed heartbeats before intervention
- **Credit tracking:** Monitor remaining credits/tokens, warn at threshold, switch to fallback model
- **Startup:** Which agents to start for a task, in which order
- **Failure recovery:** What to do when an agent fails (retry, switch model, escalate to human)

### 4.2 Task Execution Modes

| Mode | Description | Rule Pattern |
|------|-------------|-------------|
| **Sequential** | Agent A works, then Agent B reviews | `A completes → B reviews → merge or redo` |
| **Discussion** | Multiple agents debate via database | `All contribute → vote → leader decides` |
| **Parallel** | Independent agents work on separate tasks | `A on task 1, B on task 2 → sync at milestone` |
| **Supervised** | One agent works, another monitors | `Worker runs → Supervisor checks every N ticks` |

### 4.3 Communication Protocol

Agents **never communicate directly**. All exchange flows through the graph:

1. Agent writes a `(:AgentLog {type: "message"})` node
2. Links it to the relevant `(:Discussion)` or `(:Task)`
3. Other agents query for new messages on their next tick
4. Consensus is recorded as a `(:AgentLog {type: "consensus"})` node
5. The project lead agent (or human) can override any decision

### 4.4 Example Rules (Pseudocode)

```
# Agent health check
MATCH (a:Agent {status: "active"}):
    IF a.last_heartbeat < datetime() - duration("PT15M"):
        IF a.missed_checks >= 3:
            a.status = "failed"
            NOTIFY "ingo" "Agent {a.name} unresponsive for 15+ minutes"
        IF a.missed_checks < 3:
            a.missed_checks += 1
            RESTART a

# Credit exhaustion fallback
MATCH (a:Agent)-[u:USES_MODEL {priority: 1}]->(m:Model):
    IF a.credits_remaining < 1000 ∧ a.credits_remaining > 0:
        MATCH (a)-[fb:USES_MODEL {fallback: true}]->(fallback_model:Model):
            SWITCH a TO fallback_model
            LOG "Switched {a.name} from {m.name} to {fallback_model.name} (low credits)"

# Task assignment
MATCH (t:Task {status: "todo"})-[:PART_OF]->(s:Sprint {status: "active"}):
    MATCH (s)-[:PART_OF]->(p:Project)-[:HAS_AGENT {role: "developer"}]->(a:Agent):
        IF a.status == "idle":
            t.status = "in_progress"
            t.assigned_agent = a.id
            START a WITH CONTEXT t
```

---

## 5. Reporting

A reporting agent (e.g. Dione via OpenClaw) can query the graph to generate:

- **Project status reports:** Sprint progress, task completion, blockers
- **Agent activity reports:** Actions per agent, uptime, error rates
- **Discussion summaries:** What was debated, who said what, what was decided
- **Resource usage:** Model costs, API calls, credit burn rate
- **Timeline analysis:** Planned vs actual dates, velocity trends

All reports are generated from **graph queries only** — no external state needed.

---

## 6. Technology Stack

| Component | Technology |
|-----------|-----------|
| Database | Neo4j 2026.x (Community Edition) |
| Language | Python 3.13 |
| Rule Engine | GSL (from GWW3) — parse with Lark, compile to Python |
| Agent Runtime | OpenClaw, Google ADK, standalone |
| CLI | `hassaleh` (planned) |
| Platform | Ubuntu 24.04+ (initial) |

---

## 7. Relationship to GWW3

Hassaleh extracts and generalizes the **rule engine architecture** developed for Games of World War 3:

| GWW3 Component | Hassaleh Equivalent |
|----------------|-------------------|
| GSL parser + compiler | Reused directly (Lark grammar + transpiler) |
| Neo4j schema | Generalized for agents/projects instead of nations/conflicts |
| Intent accumulator | Simplified — no probabilistic distributions initially |
| Tick engine | Adapted as agent orchestration loop |
| Deterministic RNG | Optional — available when stochastic rules are needed |
| Confidence fields | Optional — useful for data quality tracking |

The probabilistic machinery (distributions, confidence widening, commutative reducer) remains available but is not required for basic agent orchestration. Simple boolean rules suffice for most operational scenarios.

---

## 8. Roadmap

| Phase | Goal |
|-------|------|
| **0 — Concept** ✅ | Architecture document, GitHub repo |
| **1 — Schema** | Neo4j schema definition, constraints, seed data |
| **2 — CLI** | `hassaleh init`, `hassaleh status`, `hassaleh report` |
| **3 — Rule Engine** | Port GSL from GWW3, adapt for agent orchestration |
| **4 — Integration** | OpenClaw integration, first operational rules |
| **5 — Reporting** | Graph-based project/agent reporting |
| **6 — Multi-Agent** | Discussion protocol, consensus mechanism |

---

*This document is a living draft. It will evolve as Hassaleh develops.*
