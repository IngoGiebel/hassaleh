# Hassaleh — Concept Document

*Created: 2026-03-29 by Ingo Giebel + Dione 🌙*
*Revised: 2026-03-29 — v0.2: Gemini Deep Think review incorporated*
*Revised: 2026-03-29 — v0.3: Daemon enforcement model, DB access separation, CronJob-driven rule evaluation*
*Status: Draft v0.3*

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
2. **Rule-based orchestration** — Agent startup, task assignment, failure recovery, and synchronization are governed by declarative rules (GSL-Ops), not imperative code.
3. **Database-mediated communication** — Agents exchange information through the graph, not through direct messaging. Every contribution and consensus is persisted.
4. **Trusted Daemon architecture** — Agents never hold Neo4j write credentials or raw shell access. The Hassaleh Daemon mediates all write operations (database + system) and enforces permissions via OS-level user separation. Agents receive read-only database credentials for direct graph queries.
5. **Auditable by design** — Every agent action, decision, and discussion is logged as graph nodes. A reporting agent can reconstruct the full history.
6. **Fail-safe operation** — Rules define what happens when an agent fails, runs out of credits, or becomes unresponsive. Circuit breakers and exponential backoff prevent crash loops.
7. **Edge-first modeling** — Relationships between entities are always explicit edges, never string-based foreign keys. If it's a reference, it's an edge.
8. **Ubuntu-first** — Initial platform support is Ubuntu Linux. Other platforms may follow.

---

## 3. Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│  Agents (Dione, Inanna, Codex, Claude Code, ...)    │
│  • Run as unprivileged OS user (hassaleh-agent)     │
│  • Neo4j READ-ONLY access (user: hassaleh_reader)   │
│  • No shell access, no Neo4j writes                  │
│  • Submit intents via Daemon API                     │
│  • Query graph directly for status (read-only)       │
└─────────┬───────────────────────┬───────────────────┘
          │ Intent API            │ Direct Cypher
          │ (write requests)      │ (read-only)
          ▼                       ▼
┌─────────────────────┐  ┌───────────────────────────┐
│  Hassaleh Daemon    │  │  Neo4j Graph Database      │
│                     │  │                             │
│  • Runs as dedicated│  │  Two database users:        │
│    OS user          │  │  • hassaleh_daemon (r/w)    │
│    (hassaleh-svc)   │  │  • hassaleh_reader (r/o)    │
│  • Neo4j WRITE      │  │                             │
│    access (daemon)  │  │  All config, state, logs,   │
│  • Enforces         │──│  rules, messages            │
│    SystemAction     │  │                             │
│    permissions      │  │  ACID transactions           │
│  • Compiles & evals │  │  Single source of truth      │
│    GSL-Ops rules    │  └───────────────────────────┘
│  • Manages agent    │
│    lifecycle        │
│  • Resolves secrets │
│  • Triggered by     │
│    CronJobs at      │
│    regular intervals│
└─────────────────────┘
```

### 3.1 OS-Level Enforcement

The Daemon does not merely *describe* permissions — it **enforces** them at the operating system level:

| Component | OS User | Neo4j User | Capabilities |
|-----------|---------|------------|-------------|
| **Hassaleh Daemon** | `hassaleh-svc` | `hassaleh_daemon` (read/write) | Full DB writes, shell execution (via SystemAction checks), rule evaluation, agent lifecycle |
| **AI Agents** | `hassaleh-agent` | `hassaleh_reader` (read-only) | Direct read-only graph queries, submit intents to Daemon API |
| **Human Admin** | user account (e.g. `uranus`) | `neo4j` (admin) | Full DB access, Daemon management, manual overrides |

The `hassaleh-agent` OS user has no `sudo`, no write access outside designated directories, and no ability to start processes. All system operations are proxied through the Daemon, which validates them against the SystemAction graph before execution.

### 3.2 Daemon Execution Model

The Daemon is **not a long-running event loop**. It is invoked by **system CronJobs** at configurable intervals:

| CronJob | Interval | Purpose |
|---------|----------|---------|
| `hassaleh-tick` | Every 1–5 min | Evaluate GSL-Ops rules against current graph state. Process pending intents. Check agent health. Fire timed triggers. |
| `hassaleh-sweep` | Every 15 min | Archive expired TimeBuckets. Clean up stale Memory nodes. Reset circuit breaker counters. |
| `hassaleh-audit` | Daily | Generate daily activity summary. Check rule consistency. Verify graph integrity. |

Each invocation:
1. Daemon starts, connects to Neo4j as `hassaleh_daemon`
2. Reads all pending Intents submitted by agents
3. Validates each Intent against SystemAction permissions
4. Evaluates GSL-Ops rules against current graph state
5. Applies approved state changes in a single ACID transaction
6. Checks for triggered events (new Messages, Milestone completions, etc.)
7. Dispatches notifications to affected agents (via OpenClaw or direct wake)
8. Writes SystemTrace entries
9. Exits

This ensures the Daemon is stateless between invocations, recoverable after crashes, and auditable via CronJob logs.

### 3.3 Agent Read Access

Agents can query the graph **directly** using the `hassaleh_reader` Neo4j user:
- Browse project status, task assignments, sprint progress
- Read Messages and Discussion threads
- Check their own health state and model assignments
- Generate reports from graph data

This avoids routing all read traffic through the Daemon, keeping it lightweight and reducing latency for status queries. The read-only user **cannot** create, update, or delete any nodes or relationships.

---

## 4. Node Types

### 4.1 Model

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
    lifecycle: "available"              # → universal LifecycleStatus
})
```

**Secrets:** Model authentication credentials are **never stored in the graph**. See SecretRef (4.15).

### 4.2 Agent

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
    
    # State
    lifecycle: "running",               # → universal LifecycleStatus
    last_heartbeat: datetime(),
    credits_remaining: null,            # null = unlimited (subscription)
    health_check_interval_sec: 300,
    restart_count_1h: 0,                # circuit breaker tracking
    max_restarts_1h: 5                  # suspend after this many
})
```

**Relationships:**
```
(Agent)-[:USES_MODEL {priority: 1, fallback: false}]->(Model)
(Agent)-[:USES_MODEL {priority: 2, fallback: true}]->(Model)
(Agent)-[:OPERATES_IN]->(Workspace)
(Agent)-[:HAS_TOOL]->(Tool)
(Agent)-[:HAS_SKILL]->(Skill)
(Agent)-[:PERMITTED]->(SystemAction)
(Agent)-[:LAST_READ]->(Message)         # cursor for message queue
```

### 4.3 Tool

An MCP server or standalone tool available to agents.

```
(:Tool {
    id: "firecrawl",
    name: "Firecrawl",
    type: "mcp_server",                 # mcp_server | cli | api | builtin
    description: "Web scraping, search, crawling via Firecrawl API",
    
    # Invocation (executed by Daemon, not agent)
    invoke_command: "mcporter call firecrawl.*",
    requires_auth: true,
    
    # Operational constraints
    rate_limit: "500 credits/month",
    cost_model: "per_call",
    lifecycle: "available"
})
```

**Relationships:**
```
(Tool)-[:CONFIGURED_IN]->(ConfigFile)
(Tool)-[:AUTHENTICATES_VIA]->(SecretRef)
```

### 4.4 Skill

A reusable skill package (e.g. OpenClaw skills, ClawHub skills).

```
(:Skill {
    id: "firecrawl-search",
    name: "Firecrawl Search",
    description: "Web search with full page content extraction",
    version: "1.0.0",
    source: "clawhub",                  # clawhub | local | github
    lifecycle: "available"
})
```

**Relationships:**
```
(Skill)-[:LOCATED_AT]->(Artifact)       # SKILL.md file
(Skill)-[:DEPENDS_ON]->(Tool)
```

### 4.5 Workspace

A filesystem location where agents and projects operate.

```
(:Workspace {
    id: "moltbot-workspace",
    path: "/home/uranus/moltbot-workspace",
    os: "ubuntu",
    description: "Primary workspace for all agents"
})
```

**Relationships:**
```
(Agent)-[:OPERATES_IN]->(Workspace)
(Project)-[:LOCATED_IN]->(Workspace)
```

### 4.6 ConfigFile

A configuration file referenced by tools or agents.

```
(:ConfigFile {
    id: "mcporter-config",
    path: "config/mcporter.json",
    format: "json",
    description: "MCP server configuration for mcporter"
})
```

### 4.7 SystemAction

A permitted action on the host system. Actions are whitelisted — anything not explicitly permitted is **denied by the Daemon**.

```
(:SystemAction {
    id: "file-read-workspace",
    name: "Read workspace files",
    type: "filesystem",                 # filesystem | process | network | package | sudo
    scope: "/home/uranus/moltbot-workspace/**",
    permission: "read",                 # read | write | execute | admin
    requires_confirmation: false,
    os: "ubuntu"
})
```

**Enforcement:** The Hassaleh Daemon checks SystemAction edges before executing any host operation. Enforcement is **dual-layered**:
1. **OS level:** Agents run as `hassaleh-agent` user with minimal filesystem permissions
2. **Graph level:** The Daemon verifies `[:PERMITTED]` edges before executing any operation on behalf of an agent

**Relationships:**
```
(Agent)-[:PERMITTED {granted_by: "ingo", granted_at: datetime()}]->(SystemAction)
(Project)-[:ALLOWS_ACTION]->(SystemAction)
```

### 4.8 CronJob

A scheduled recurring or one-shot task.

```
(:CronJob {
    id: "daily-maintenance",
    name: "Daily System Maintenance",
    schedule: "0 4 * * *",
    timezone: "Europe/Berlin",
    command: "bash scripts/daily-maintenance.sh",
    
    enabled: true,
    last_run: datetime(),
    last_status: "success",
    next_run: datetime(),
    retry_on_failure: true,
    max_retries: 3
})
```

**Relationships:**
```
(CronJob)-[:EXECUTED_BY]->(Agent)
(CronJob)-[:BELONGS_TO]->(Project)
(CronJob)-[:TRIGGERS]->(CronJob)
```

### 4.9 Project

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
    lifecycle: "running",              # → universal LifecycleStatus
    priority: "high"                   # critical | high | medium | low
})
```

**Relationships:**
```
(Project)-[:HAS_SUBPROJECT]->(Project)
(Project)-[:DEPENDS_ON]->(Project)
(Project)-[:HAS_AGENT {role: "orchestrator"}]->(Agent)
(Project)-[:HAS_AGENT {role: "developer"}]->(Agent)
(Project)-[:HAS_AGENT {role: "reviewer"}]->(Agent)
(Project)-[:REQUIRES_TOOL]->(Tool)
(Project)-[:REQUIRES_SKILL]->(Skill)
(Project)-[:ALLOWS_ACTION]->(SystemAction)
(Project)-[:HAS_CRONJOB]->(CronJob)
(Project)-[:HAS_SPRINT]->(Sprint)
(Project)-[:HAS_ARTIFACT]->(Artifact)
(Project)-[:GOVERNED_BY]->(Rule)
(Project)-[:LOCATED_IN]->(Workspace)
(Project)-[:HAS_REPO {url: "https://github.com/..."}]->(Artifact)
```

### 4.10 Sprint

A time-boxed work phase within a project.

```
(:Sprint {
    id: "gww3-sprint-4",
    name: "Sprint 4: Rules Engine (GSL)",
    description: "Build the GSL parser, evaluator, and intent reducer",
    
    started_at: datetime(),
    target_date: date("2026-04-15"),
    lifecycle: "running"
})
```

**Relationships:**
```
(Sprint)-[:HAS_TASK]->(Task)
(Sprint)-[:HAS_MILESTONE]->(Milestone)
(Sprint)-[:NEXT]->(Sprint)
```

### 4.11 Task

A concrete unit of work within a sprint.

```
(:Task {
    id: "gww3-s4-parser-v3",
    name: "GSL Parser v3 — Python-style colon blocks",
    description: "Rewrite .lark grammar with MATCH …: / IF …: syntax",
    
    lifecycle: "success",
    completed_at: datetime(),
    expires_at: datetime(),             # timeout — auto-reset if exceeded
    verification_method: "pytest tests/engine/test_gsl_parser.py",
    idempotency_key: null               # for external side effects
})
```

**Relationships:**
```
(Task)-[:ASSIGNED_TO]->(Agent)
(Task)-[:DEPENDS_ON]->(Task)
(Task)-[:HAS_MEMORY]->(Memory)          # agent scratchpad for this task
```

### 4.12 Milestone

A checkpoint with verifiable acceptance criteria.

```
(:Milestone {
    id: "gww3-s4-parser-passes",
    name: "GSL parser passes all 26 tests",
    check_command: "PYTHONPATH=src python3.13 -m pytest tests/engine/ -q",
    check_expected: "26 passed",
    
    lifecycle: "success",
    reached_at: datetime()
})
```

### 4.13 Artifact

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

### 4.14 Memory

Ephemeral working memory for agents during task execution. Separated from the audit trail.

```
(:Memory {
    id: uuid(),
    created_at: datetime(),
    content: '{"step": 3, "intermediate_results": [...]}',
    ttl_hours: 72                       # auto-expire after N hours
})
```

**Relationships:**
```
(Task)-[:HAS_MEMORY]->(Memory)
(Agent)-[:OWNS_MEMORY]->(Memory)
```

### 4.15 SecretRef

A reference to a secret stored **outside** the graph (environment variable, vault, encrypted file). The Hassaleh Daemon resolves these at runtime.

```
(:SecretRef {
    id: "anthropic-api-key",
    vault_type: "env",                 # env | file | vault | 1password
    vault_key: "ANTHROPIC_API_KEY",
    description: "Anthropic API key for Claude models"
})
```

**The graph never contains actual secrets.** Only references.

**Relationships:**
```
(Model)-[:AUTHENTICATES_VIA]->(SecretRef)
(Tool)-[:AUTHENTICATES_VIA]->(SecretRef)
```

### 4.16 Rule

A declarative rule governing agent behavior within a project. Uses GSL-Ops (deterministic subset of GSL).

```
(:Rule {
    id: "agent-health-check",
    version: 1,
    category: ["operations"],
    priority: 10,                      # lower = higher priority (for conflict resolution)
    
    rule_text: "...",                  # GSL-Ops source (compiled by Daemon at runtime)
    
    description: "Check agent health every 5 min. Restart if unresponsive. Circuit breaker after 5 failures.",
    author: "ingo"
})
```

**Security:** `compiled_python` is **not stored in the graph**. The Daemon (running as `hassaleh-svc`) compiles `rule_text` to Python in memory during each tick invocation. Since agents only have read-only DB access, they cannot inject malicious rule text. Only the human admin can create or modify Rule nodes.

**Conflict resolution:** When multiple rules target the same property on the same node, the rule with the **lowest priority number wins** (priority 1 overrides priority 10). For additive/modifier operations (numeric), the GWW3-style commutative reducer is used. For absolute state assignments (enums like `lifecycle`), the highest-priority rule wins.

**Relationships:**
```
(Project)-[:GOVERNED_BY]->(Rule)
(Rule)-[:APPLIES_TO]->(Agent)
```

### 4.17 Message

Inter-agent communication node. Part of a linked-list message queue.

```
(:Message {
    id: uuid(),
    timestamp: datetime(),
    content: "I've completed the GSL parser rewrite. 26 tests pass.",
    metadata: '{"artifact": "gsl.lark", "test_count": 26}'
})
```

**Relationships:**
```
(Agent)-[:SENT]->(Message)
(Message)-[:NEXT]->(Message)            # linked-list for O(1) cursor traversal
(Message)-[:IN_CONTEXT_OF]->(Task)
(Message)-[:IN_CONTEXT_OF]->(Discussion)
(Agent)-[:LAST_READ]->(Message)         # cursor — agent's read position
```

**Efficient retrieval:** Agents don't poll by timestamp. They follow their `LAST_READ` cursor forward through the `NEXT` chain. Empty check = O(1).

### 4.18 Intent

A proposed state change submitted by an agent, awaiting Daemon processing.

```
(:Intent {
    id: uuid(),
    timestamp: datetime(),
    action: "update_property",         # update_property | create_node | create_edge | execute_command
    target_node_id: "...",
    property: "lifecycle",
    value: "failed",
    idempotency_key: "health-check-dione-2026-03-29T13:00"
})
```

**Relationships:**
```
(Agent)-[:PROPOSED]->(Intent)
(Intent)-[:TARGETS]->(Node)
(Rule)-[:GENERATED]->(Intent)
```

### 4.19 SystemTrace

Operational log entries (errors, restarts, performance). Separated from agent communication.

```
(:SystemTrace {
    id: uuid(),
    timestamp: datetime(),
    level: "error",                    # debug | info | warn | error | fatal
    source: "daemon",                  # daemon | agent | rule | cronjob
    message: "Agent dione unresponsive for 15 minutes. Restarting.",
    metadata: '{"restart_count": 3}'
})
```

**Relationships:**
```
(SystemTrace)-[:ABOUT]->(Agent)
(SystemTrace)-[:IN_CONTEXT_OF]->(Project)
(SystemTrace)-[:IN_BUCKET]->(TimeBucket)
```

### 4.20 TimeBucket

Partitioning node for log aggregation. Prevents supernode problem on Project nodes.

```
(:TimeBucket {
    id: "2026-03-29",
    date: date("2026-03-29"),
    type: "daily"
})
```

**Relationships:**
```
(SystemTrace)-[:IN_BUCKET]->(TimeBucket)
(Message)-[:IN_BUCKET]->(TimeBucket)
(TimeBucket)-[:NEXT]->(TimeBucket)
```

**Archival:** A CronJob archives TimeBuckets older than 30 days to JSONL and DETACH DELETEs them.

### 4.21 Discussion

A structured multi-agent discussion for collaborative decision-making.

```
(:Discussion {
    id: uuid(),
    topic: "Should we use LALR or Earley parser for GSL?",
    lifecycle: "success",              # resolved
    resolution: "Earley — handles ambiguity, performance is sufficient",
    resolved_at: datetime()
})
```

**Relationships:**
```
(Discussion)-[:IN_CONTEXT_OF]->(Project)
(Discussion)-[:IN_CONTEXT_OF]->(Task)
(Agent)-[:CONTRIBUTED {position: "pro-earley", reasoning: "...", timestamp: datetime()}]->(Discussion)
(Discussion)-[:DECIDED_BY]->(Agent)
```

---

## 5. Universal Lifecycle Status

All stateful nodes use a consistent lifecycle enum:

| Status | Meaning |
|--------|---------|
| `pending` | Created, not yet started |
| `running` | Actively executing |
| `success` | Completed successfully |
| `failed` | Completed with failure |
| `suspended` | Paused (by rule or human) — can be resumed |
| `archived` | Retained for history, no longer active |

Used by: Agent, Model, Tool, Project, Sprint, Task, Milestone, CronJob, Discussion.

---

## 6. GSL-Ops: Deterministic Rule Subset

Hassaleh uses **GSL-Ops**, a deterministic subset of the GWW3 GSL language:

| GSL Feature | GSL-Ops | Notes |
|-------------|---------|-------|
| `MATCH …:` blocks | ✅ | Graph pattern matching |
| `IF …:` blocks | ✅ | Boolean conditions |
| `LET` bindings | ✅ | Variable computation |
| Effects (`+=`, `-=`, `=`) | ✅ | State modifications |
| Truth Values `⟨p, c⟩` | ❌ Stripped | No probabilistic execution |
| Distributions (`𝒩`, `𝐿𝑁`, `𝛽`) | ❌ Stripped | No Monte Carlo sampling |
| `DELAYED` blocks | ✅ Simplified | Timer-based deferred actions |
| `CONVERGE` operator | ❌ Stripped | Not needed for operations |

**Conflict resolution** uses **priority numbers** on rules (lower = higher priority), not commutative math reduction. For numeric properties, additive reduction is available. For enum properties (like `lifecycle`), the highest-priority rule wins.

---

## 7. Orchestration Rules

Agent coordination is **purely rule-based**. Rules define:

### 7.1 Agent Lifecycle

```
# Health check with circuit breaker
MATCH (a:Agent {lifecycle: "running"}):
    IF a.last_heartbeat < datetime() - duration("PT15M"):
        IF a.restart_count_1h >= a.max_restarts_1h:
            a.lifecycle = "suspended"
            NOTIFY "ingo" "Agent {a.name} suspended after {a.max_restarts_1h} restarts"
        IF a.restart_count_1h < a.max_restarts_1h:
            a.restart_count_1h += 1
            RESTART a

# Credit exhaustion fallback
MATCH (a:Agent)-[:USES_MODEL {priority: 1}]->(m:Model):
    IF a.credits_remaining < 1000 ∧ a.credits_remaining > 0:
        MATCH (a)-[:USES_MODEL {fallback: true}]->(fb:Model):
            SWITCH a TO fb
            LOG "Switched {a.name} to {fb.name} (low credits)"
```

### 7.2 Task Execution Modes

| Mode | Description | Pattern |
|------|-------------|---------|
| **Sequential** | A works → B reviews → merge or redo | `A completes → B reviews → merge` |
| **Discussion** | Agents debate via Message nodes | `All contribute → vote → leader decides` |
| **Parallel** | Independent agents, sync at milestone | `A on task 1, B on task 2 → sync` |
| **Supervised** | Worker + monitor | `Worker runs → Supervisor checks every N ticks` |

### 7.3 Task Timeouts

Every Task has an `expires_at` property. A background rule sweeps for expired tasks:

```
MATCH (t:Task {lifecycle: "running"}):
    IF t.expires_at < datetime():
        t.lifecycle = "failed"
        MATCH (t)-[:ASSIGNED_TO]->(a:Agent):
            a.lifecycle = "pending"
        LOG "Task {t.name} expired — unassigned from agent"
```

### 7.4 Communication Protocol

Agents **never communicate directly**. All exchange flows through Message nodes:

1. Agent submits a Message intent to the Daemon
2. Daemon creates `(:Message)` node, links it to the `NEXT` chain
3. Other agents advance their `LAST_READ` cursor to find new messages
4. Consensus is recorded as a Discussion resolution
5. The project lead agent (or human) can override any decision

### 7.5 Idempotency

For tasks with external side effects (sending emails, API calls, deployments), the Task carries an `idempotency_key`. If an agent crashes after performing the action but before logging it, the Daemon checks the key on restart and skips re-execution.

---

## 8. Reporting

A reporting agent queries the graph to generate:

- **Project status reports:** Sprint progress, task completion, blockers
- **Agent activity reports:** Actions per agent, uptime, error rates
- **Discussion summaries:** What was debated, who said what, what was decided
- **Resource usage:** Model costs, API calls, credit burn rate
- **Timeline analysis:** Planned vs actual dates, velocity trends

All reports generated from **graph queries only** — no external state needed.

---

## 9. Technology Stack

| Component | Technology |
|-----------|-----------|
| Database | Neo4j 2026.x (Community Edition) |
| Language | Python 3.13 |
| Rule Engine | GSL-Ops (deterministic subset of GSL from GWW3) |
| Parser | Lark 1.3.1 (Earley + PythonIndenter) |
| Agent Runtime | OpenClaw, Google ADK, standalone |
| Daemon | Python CLI (CronJob-invoked, stateless) |
| CLI | `hassaleh` (planned) |
| Platform | Ubuntu 24.04+ (initial) |

---

## 10. Relationship to GWW3

Hassaleh extracts and generalizes the **rule engine architecture** developed for Games of World War 3:

| GWW3 Component | Hassaleh Equivalent |
|----------------|-------------------|
| GSL parser + compiler | Reused, stripped to GSL-Ops (no distributions) |
| Neo4j schema | Generalized for agents/projects instead of nations/conflicts |
| Intent accumulator | Simplified — priority-based for enums, additive for numbers |
| Tick engine | Adapted as Daemon orchestration loop |
| Deterministic RNG | Available but optional |
| Confidence fields | Available but optional |

When Hassaleh is operational, it will be used to orchestrate further GWW3 development and all other Alpha Auriga projects.

---

## 11. Roadmap

| Phase | Goal |
|-------|------|
| **0 — Concept** ✅ | Architecture document, GitHub repo |
| **1 — Schema** | Neo4j schema definition, constraints, indexes, seed data |
| **1.5 — Blackboard Spike** | 3 dummy agents reading/writing Neo4j concurrently — prove cursor pattern works |
| **2 — Daemon MVP** | Trusted API gateway, SystemAction enforcement, SecretRef resolution |
| **3 — Rule Engine** | Port GSL-Ops from GWW3, priority-based conflict resolution |
| **4 — CLI** | `hassaleh init`, `hassaleh status`, `hassaleh report` |
| **5 — Integration** | OpenClaw integration, first operational rules |
| **6 — Reporting** | Graph-based project/agent reporting |
| **7 — Multi-Agent** | Discussion protocol, consensus mechanism |

---

## Changelog

| Date | Version | Changes |
|------|---------|---------|
| 2026-03-29 | v0.1 | Initial concept: 14 node types, orchestration rules, roadmap |
| 2026-03-29 | v0.2 | Gemini Deep Think review: +7 node types (Workspace, ConfigFile, Memory, SecretRef, Message, Intent, SystemTrace, TimeBucket, Discussion split). Trusted Daemon architecture. GSL-Ops subset. Priority-based conflict resolution. Circuit breakers. Task timeouts. Idempotency keys. Edge-first modeling (no string FKs). Message cursor pattern. TimeBucket log partitioning. Universal LifecycleStatus enum. Roadmap reordered (Daemon before Rule Engine). |
| 2026-03-29 | v0.3 | OS-level enforcement: dedicated OS users (`hassaleh-svc`, `hassaleh-agent`). Dual Neo4j users (`hassaleh_daemon` r/w, `hassaleh_reader` r/o). Agents get direct read-only DB access for status queries. Daemon is CronJob-invoked (stateless, not long-running). Three CronJob tiers: tick (1-5 min), sweep (15 min), audit (daily). Dual-layered permission enforcement (OS + graph). |

---

*This document is a living draft. It will evolve as Hassaleh develops.*
