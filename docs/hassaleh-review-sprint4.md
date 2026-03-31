# Hassaleh — Architecture Review Request (Sprints 2-4)

You are reviewing the Hassaleh project — a graph-native agentic framework for AI agent orchestration where all state lives in Neo4j.

The project has completed 4 sprints:
- Sprint 1: MVP Daemon (Intent processing, async workers, systemd, health endpoint)
- Sprint 2: GSL-Ops Rule Engine (grammar, compiler, runtime, resolver)
- Sprint 3: CLI (hassaleh status/init/agent/rule/intent/approve/heartbeat)
- Sprint 4: OpenClaw Bridge (HTTP client for Gateway API, notification dispatch, heartbeat)

Below is the complete source code and concept document.

PLEASE REVIEW:
1. Is the GSL-Ops grammar well-designed? Is FOREACH + list literal implementation correct?
2. Is the compiler's GSL-Ops → Python transpilation sound? Any edge cases?
3. Is the resolver's SET → ADD/SUB → MUL ordering correct?
4. Is the OpenClaw Bridge architecture sound? (async HTTP client in sync rule evaluation context)
5. Is the Daemon's rule evaluation pipeline correct? (load → compile → cache → evaluate → resolve → apply)
6. Security: exec() uses restricted builtins, ctx.match() uses execute_read(). Sufficient?
7. Is the CLI well-designed for operators?
8. Is CONCEPT.md v1.3 consistent with the actual implementation?
9. Any bugs, race conditions, or architectural issues?
10. Overall: is Hassaleh on a good trajectory for a production agent orchestration framework?

Structure your response as:
1. Architecture Assessment
2. GSL-Ops Language Review (grammar + compiler + runtime)
3. Daemon & Rule Pipeline Review
4. OpenClaw Bridge Review
5. CLI Review
6. Security Analysis
7. Bugs & Issues Found
8. CONCEPT.md Consistency
9. Recommendations & Trajectory

---


---
## FILE: docs/CONCEPT.md
```
# Hassaleh — Concept Document

*Created: 2026-03-29 by Ingo Giebel + Dione 🌙*
*Revised: 2026-03-29 — v0.2: Gemini Deep Think review incorporated*
*Revised: 2026-03-29 — v0.3: Daemon enforcement model, DB access separation, CronJob-driven rule evaluation*
*Revised: 2026-03-29 — v0.4: Continuous async Daemon, Intent feedback, workspace fast-path, all config in DB*
*Revised: 2026-03-29 — v0.5: Third Gemini DT review: async subprocess, zombie recovery, HITL, SDK safety, missing features*
*Revised: 2026-03-29 — v1.0: Final review: Capability merge, consistency fixes, graceful shutdown, UTC mandate, Docker dev-env*
*Revised: 2026-03-29 — v1.1: Consistency audit: 15 fixes (renumbering, cross-refs, terminology, datetime UTC, relationships)*
*Revised: 2026-03-29 — v1.2: Final 8 fixes from audit + version renaming*
*Revised: 2026-03-31 — v1.3: GSL-Ops language reference (FOREACH, lists, functions), OpenClaw Bridge, conflict resolution details*
*Status: v1.3 — Sprint 4 complete*

---

## 1. Vision

Hassaleh is a **graph-native agentic framework** where the entire configuration, state, and coordination of AI agents is stored in a **Neo4j graph database**.

A capable AI agent (e.g. Claude Opus, steered via OpenClaw) can read the graph to:
- Generate reports on agent activity and project progress
- Understand the full system topology (who does what, with which capabilities, on which projects)
- Audit rule execution and agent decisions

The graph is the **single source of truth** — no YAML files, no scattered configs, no hidden state.

---

## 2. Core Principles

1. **The graph IS the runtime configuration** — Agents, capabilities, schedules, and rules live as nodes and edges in Neo4j.
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
│  • Direct r/w to assigned Workspace directories     │
│  • Submit intents via Daemon API for privileged ops  │
│  • Query graph via hassaleh.query() SDK (read-only)  │
└─────────┬───────────────────────┬───────────────────┘
          │ Intent API            │ Read-Proxy SDK
          │ (write requests)      │ (guarded read-only)
          ▼                       ▼
┌─────────────────────┐  ┌───────────────────────────┐
│  Hassaleh Daemon    │  │  Neo4j Graph Database      │
│  (systemd service)  │  │                             │
│                     │  │  Two database users:        │
│  • Persistent async │  │  • hassaleh_daemon (r/w)    │
│    event loop       │  │  • hassaleh_reader (r/o)    │
│    (1-second ticks) │  │                             │
│  • Runs as          │  │  ALL configuration stored   │
│    hassaleh-svc     │──│  here (no external configs) │
│  • Neo4j r/w access │  │                             │
│  • Compiles GSL-Ops │  │  ACID transactions           │
│    rules on boot +  │  │  Single source of truth      │
│    on-change        │  └───────────────────────────┘
│  • Async action     │
│    workers (never   │
│    blocks on I/O)   │
│  • Intent feedback  │
│    (stdout/stderr)  │
│  • SecretRef        │
│    resolution       │
└─────────────────────┘
```

### 3.1 OS-Level Enforcement

The Daemon does not merely *describe* permissions — it **enforces** them at the operating system level:

| Component | OS User | Neo4j User | Capabilities |
|-----------|---------|------------|-------------|
| **Hassaleh Daemon** | `hassaleh-svc` | `hassaleh_daemon` (read/write) | Full DB writes, rule evaluation, agent lifecycle, delegates capability execution to per-capability OS users |
| **AI Agents** | `hassaleh-agent` | `hassaleh_reader` (read-only) | Direct read-only graph queries, direct r/w to assigned Workspaces, submit Intents to Daemon API |
| **Capability Users** | `hassaleh-fs`, `hassaleh-writer`, `hassaleh-exec`, `hassaleh-net`, `hassaleh-pkg` | — | Per-capability-class OS users with minimal permissions (invoked by Daemon via `sudo -n -u`) |
| **Human Admin** | user account (e.g. `uranus`) | `neo4j` (admin) | Full DB access, Daemon management, manual overrides |

The `hassaleh-agent` OS user has no `sudo`, no write access outside assigned Workspace directories, and no ability to start privileged processes. All privileged operations are proxied through the Daemon, which validates `[:HAS_CAPABILITY]` edges and then executes via `sudo -n -u` to the appropriate capability-specific OS user.

### 3.2 Daemon Execution Model

The Daemon is a **persistent asyncio service** managed by systemd, with a fast internal event loop:

**Hot Path (1-second ticks):**
1. Read pending Intents submitted by agents
2. Validate each Intent against Capability permissions
3. For approved Intents: execute via async action workers (never block the loop)
4. Write Intent results (stdout, stderr, error_reason) back to the Intent node
5. Evaluate GSL-Ops rules against current graph state
6. Check for triggered events (new Messages, Milestone completions, etc.)
7. Dispatch notifications to affected agents

**Background Tasks (periodic, run inside the same Daemon process):**

| Task | Interval | Purpose |
|------|----------|---------|
| `sweep` | Every 15 min | Archive expired TimeBuckets. Clean up stale Memory nodes. Decay circuit breaker counters (subtract `circuit_breaker_decay_per_sweep` from `restart_count_1h`, creating a rolling recovery window). |
| `audit` | Daily | Generate daily activity summary. Check rule consistency. Verify graph integrity. |

**Key design decisions:**
- **Never blocks on I/O:** System actions use `asyncio.create_subprocess_exec()` (not `subprocess.run`!). The worker `await`s `process.communicate()`, yielding control back to the tick loop while the OS works. The Daemon updates the Intent to `running` and immediately returns to process other agents.
- **Zombie Intent recovery:** On boot, the Daemon queries for all Intents with `lifecycle: "running"` and transitions them to `failed` with `error_reason: "Daemon restarted during execution"`. This allows agents to detect the failure and cleanly retry.
- **Rules compiled once:** GSL-Ops rules are compiled to Python on Daemon boot (from `compiled_python` cache or fresh from `rule_text`) and recompiled only when Rule nodes are modified. No per-tick recompilation.
- **Managed by systemd:** Automatic restart on crash, journald logging, resource limits via cgroup. The Daemon pings the systemd watchdog (`sd_notify`) on every tick — if the asyncio loop hangs, systemd automatically restarts the service.
- **Health endpoint:** The Daemon exposes a minimal HTTP health endpoint (configurable port in DaemonConfig) for monitoring tools (uptime, tick count, pending intents, active workers).
- **All configuration from the graph:** The Daemon reads its own configuration (tick interval, sweep schedule, timeout defaults) from a `(:DaemonConfig)` singleton node in Neo4j. No external config files.
- **Graceful shutdown:** On SIGTERM/SIGINT, the Daemon stops pulling new Intents, sends SIGTERM to all active subprocess workers, awaits their exit (up to 30s timeout), writes `failed` with `error_reason: "Daemon shutdown"` to any still-running Intents, then exits cleanly. This prevents orphaned background processes.
- **Neo4j read-only fallback:** If Neo4j enters read-only mode (e.g., disk full), the Daemon catches `TransientError` exceptions, pauses the hot-path, and emits a loud alert rather than crash-looping.
- **UTC everywhere:** All timestamps in Python use `datetime.now(datetime.UTC)`. All Cypher uses `datetime({timezone: 'UTC'})`. No local timezone assumptions.

### 3.3 Agent Read Access

Agents query the graph through the **`hassaleh.query()` SDK** — a lightweight Python library that:
- Connects to Neo4j as `hassaleh_reader` (read-only)
- **Forces parameterized queries** — agents pass query templates + parameters, never raw string concatenation. This prevents Cypher injection.
- Applies **per-query timeouts** (loaded from `(:QueryConfig)` node in the graph, passed with each query call)
- Enforces **result-set limits** (configurable per query type, stored in DB)
- Provides convenience methods for common queries (`hassaleh.my_tasks()`, `hassaleh.project_status()`, etc.)
- Provides `hassaleh.submit_intent()` for submitting write requests to the Daemon

**Credential bootstrapping:** When the Daemon (or systemd) spawns an agent process, it injects `NEO4J_URI`, `NEO4J_USER=hassaleh_reader`, and `NEO4J_PASSWORD` as **environment variables**. Agents never query the graph for their own DB credentials.

**Security model:** No regex-based Cypher linting. The `hassaleh_reader` Neo4j role physically cannot write, create, or delete. Combined with per-query timeouts and memory limits, this provides robust protection without brittle pattern matching.

**What agents can read:**
- Project status, task assignments, sprint progress
- Messages and Discussion threads
- Their own health state and model assignments
- All graph data (shared knowledge — "swarm brain")
- Intent results (stdout, stderr, error_reason)

**What agents cannot do:**
- Write, create, update, or delete any nodes or relationships
- Access actual secrets (SecretRef nodes contain only vault references)
- Run queries that exceed the configured timeout or memory limits

**Query guardrails are stored in the graph** (not in neo4j.conf):
```
(:QueryConfig {
    id: "default",
    default_timeout_ms: 3000,          # per-query timeout (passed with each call)
    max_result_rows: 10000,            # hard limit on returned rows
    max_transaction_memory_mb: 1024,   # per-transaction memory limit
    report_query_timeout_ms: 30000     # longer timeout for reporting queries
})
```

This avoids a single global DBMS timeout that would be either too strict for reports or too lenient for routine queries.

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

**Relationships:**
```
(Model)-[:AUTHENTICATES_VIA]->(SecretRef)
```

**Secrets:** Model authentication credentials are **never stored in the graph**. See SecretRef (4.13).

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
    last_heartbeat: datetime({timezone: "UTC"}),
    os_pid: 12345,                      # Linux PID for process liveness verification
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
(Agent)-[:HAS_CAPABILITY]->(Capability) # tools, skills, system actions — all unified
(Agent)-[:LAST_READ]->(Message)         # cursor for message queue
```

### 4.3 Capability

A unified abstraction for anything an agent can do: MCP server calls, CLI tools, skills, filesystem operations, network access, package management. From the Daemon's perspective, they are all **parameterized system executions**.

```
(:Capability {
    id: "firecrawl-search",
    name: "Firecrawl Search",
    kind: "mcp_server",                 # mcp_server | cli | api | skill | filesystem | network | package
    description: "Web search with full page content extraction via Firecrawl API",
    
    # Invocation (executed by Daemon, not agent)
    invoke_command: "mcporter call firecrawl.firecrawl_search",
    invoke_params_schema: '{"query": "string", "limit": "int"}',
    
    # Skill metadata (for kind: "skill")
    version: null,                      # e.g. "1.0.0" for skills
    source: null,                       # clawhub | local | github
    
    # OS execution context
    exec_as_user: "hassaleh-net",       # dedicated OS user for this capability
    requires_auth: true,
    requires_confirmation: false,       # true → Intent goes to awaiting_approval
    
    # Operational constraints
    rate_limit: "500 credits/month",
    cost_model: "per_call",             # per_call | free | subscription
    
    lifecycle: "available"              # → LifecycleStatus (available | deprecated)
})
```

**Per-capability OS user isolation:** Each Capability specifies an `exec_as_user` — a dedicated Ubuntu user with **only** the permissions needed for this capability class. The Daemon executes via `asyncio.create_subprocess_exec("sudo", "-n", "-u", exec_as_user, ...)`:

| Capability Kind | OS User | Permissions |
|----------------|---------|------------|
| `filesystem` (read) | `hassaleh-fs` | Read-only access to workspace dirs |
| `filesystem` (write) | `hassaleh-writer` | Write access to specific output dirs |
| `cli` / `skill` (scripts) | `hassaleh-exec` | Execute scripts in whitelisted paths |
| `mcp_server` / `api` / `network` | `hassaleh-net` | Outbound HTTP only, no listeners |
| `package` | `hassaleh-pkg` | `apt` with restricted package list |

**Safe async subprocess execution:** The Daemon **never** uses `shell=True`, `subprocess.run`, or string concatenation. All delegated actions use `asyncio.create_subprocess_exec` with strict argument arrays:
```python
proc = await asyncio.create_subprocess_exec(
    "sudo", "-n", "-u", exec_as_user,
    "/path/to/script", safe_arg1, safe_arg2,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
)
stdout, stderr = await proc.communicate()
```

**Relationships:**
```
(Agent)-[:HAS_CAPABILITY {granted_at: datetime({timezone: "UTC"})}]->(Capability)
(Capability)-[:CONFIGURED_IN]->(ConfigFile)
(Capability)-[:AUTHENTICATES_VIA]->(SecretRef)
(Capability)-[:LOCATED_AT]->(Artifact)    # for skills: SKILL.md file
(Capability)-[:DEPENDS_ON]->(Capability)  # skill depends on MCP server, etc.
(Project)-[:REQUIRES_CAPABILITY]->(Capability)
```

### 4.4 Workspace

A filesystem directory where agents and projects operate. Agents have **direct OS-level read/write access** to their assigned Workspace directories (no need to route file I/O through the Daemon).

```
(:Workspace {
    id: "gww3-workspace",
    path: "/home/uranus/moltbot-workspace/projects/games-of-ww3",
    os: "ubuntu",
    writable: true,                     # agents get r/w access
    description: "GWW3 project workspace"
})
```

Workspaces are **project-specific**. An agent working on GWW3 gets access to the GWW3 workspace, not the entire filesystem. The `hassaleh-agent` OS user is granted group-level r/w access to assigned workspace directories.

**Relationships:**
```
(Agent)-[:OPERATES_IN {since: datetime({timezone: "UTC"})}]->(Workspace)
(Project)-[:LOCATED_IN]->(Workspace)
```

**Access enforcement:** When the Daemon assigns an agent to a project, it ensures the `hassaleh-agent` user has OS-level group permissions on that project's Workspace directory. When unassigned, access is revoked.

### 4.5 ConfigFile

A configuration file referenced by capabilities or agents.

```
(:ConfigFile {
    id: "mcporter-config",
    path: "config/mcporter.json",
    format: "json",
    description: "MCP server configuration for mcporter"
})
```

**Relationships:** None (referenced by Capability via `[:CONFIGURED_IN]`).

### 4.6 CronJob

A scheduled recurring or one-shot task.

```
(:CronJob {
    id: "daily-maintenance",
    name: "Daily System Maintenance",
    schedule: "0 4 * * *",
    timezone: "Europe/Berlin",
    command: "bash scripts/daily-maintenance.sh",
    
    enabled: true,
    last_run: datetime({timezone: "UTC"}),
    lifecycle: "success",               # last run status → universal LifecycleStatus
    next_run: datetime({timezone: "UTC"}),
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

### 4.7 Project

The central organizing node. Projects form a **hierarchy** (parent/child) and can represent anything from a long-running endeavor to a recurring task.

```
(:Project {
    id: "gww3",
    name: "Games of World War 3",
    description: "AI-agent-based geopolitical real-time simulation...",
    type: "development",               # development | operations | recurring | research
    
    # Timeline
    started_at: datetime({timezone: "UTC"}),
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
(Project)-[:REQUIRES_CAPABILITY]->(Capability)
(Project)-[:HAS_CRONJOB]->(CronJob)
(Project)-[:HAS_SPRINT]->(Sprint)
(Project)-[:HAS_ARTIFACT]->(Artifact)
(Project)-[:GOVERNED_BY]->(Rule)
(Project)-[:LOCATED_IN]->(Workspace)
(Project)-[:HAS_REPO {url: "https://github.com/..."}]->(Artifact)
```

### 4.8 Sprint

A time-boxed work phase within a project.

```
(:Sprint {
    id: "gww3-sprint-4",
    name: "Sprint 4: Rules Engine (GSL)",
    description: "Build the GSL parser, evaluator, and intent reducer",
    
    started_at: datetime({timezone: "UTC"}),
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

### 4.9 Task

A concrete unit of work within a sprint.

```
(:Task {
    id: "gww3-s4-parser-v3",
    name: "GSL Parser v3 — Python-style colon blocks",
    description: "Rewrite .lark grammar with MATCH …: / IF …: syntax",
    
    lifecycle: "success",
    completed_at: datetime({timezone: "UTC"}),
    expires_at: datetime({timezone: "UTC"}),             # timeout — auto-reset if exceeded
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

### 4.10 Milestone

A checkpoint with verifiable acceptance criteria.

```
(:Milestone {
    id: "gww3-s4-parser-passes",
    name: "GSL parser passes all 26 tests",
    check_command: "PYTHONPATH=src python3.13 -m pytest tests/engine/ -q",
    check_expected: "26 passed",
    
    lifecycle: "success",
    reached_at: datetime({timezone: 'UTC'})
})
```

**Relationships:** None (referenced by Sprint via `[:HAS_MILESTONE]`).

### 4.11 Artifact

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

**Relationships:** None (referenced by Capability via `[:LOCATED_AT]`, Project via `[:HAS_ARTIFACT]`, Message via `[:REFERENCES]`).

### 4.12 Memory

Ephemeral working memory for agents during task execution. Separated from the audit trail.

```
(:Memory {
    id: uuid(),
    created_at: datetime({timezone: "UTC"}),
    content: '{"step": 3, "intermediate_results": [...]}',
    ttl_hours: 72                       # auto-expire after N hours
})
```

**Relationships:**
```
(Task)-[:HAS_MEMORY]->(Memory)
(Agent)-[:OWNS_MEMORY]->(Memory)
```

### 4.13 SecretRef

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
(Capability)-[:AUTHENTICATES_VIA]->(SecretRef)
```

### 4.14 Rule

A declarative rule governing agent behavior within a project. Uses GSL-Ops (deterministic subset of GSL).

```
(:Rule {
    id: "agent-health-check",
    version: 1,
    category: ["operations"],
    priority: 10,                      # lower = higher priority (for conflict resolution)
    
    rule_text: "...",                  # GSL-Ops source (compiled by Daemon at runtime)
    
    description: "Check agent health every 5 min. Restart if unresponsive. Circuit breaker after 5 failures.",
    author: "ingo",
    lifecycle: "available"
})
```

**Compilation:** The Daemon compiles `rule_text` → `compiled_python` on boot and whenever a Rule node is modified, then writes the result back to the Rule node. At runtime, the Daemon executes the cached `compiled_python` directly — no per-tick recompilation.

```
(:Rule {
    ...
    rule_text: "MATCH (a:Agent):\n    ...",      # GSL-Ops source (authoritative)
    compiled_python: "def evaluate(ctx):\n ...",  # cached compiled output
    compiled_at: datetime({timezone: "UTC"}),
    compiler_version: "gsl-ops-0.4",
    ...
})
```

**Security:** Since agents only have read-only DB access (`hassaleh_reader`), they cannot inject malicious rule text or tamper with compiled code. Only the human admin (via `neo4j` user) can create or modify Rule nodes. The Daemon validates that `compiler_version` matches its own version before executing — stale compiled code triggers automatic recompilation from `rule_text`.

**Conflict resolution:** When multiple rules target the same property on the same node, the rule with the **lowest priority number wins** (priority 1 overrides priority 10). For additive/modifier operations (numeric), the GWW3-style commutative reducer is used. For absolute state assignments (enums like `lifecycle`), the highest-priority rule wins.

**Resolution order:** SET → ADD/SUB → MUL. SET operations resolve by priority first, then additive modifications are summed (commutative), then multiplicative modifiers scale the result.

**Relationships:**
```
(Project)-[:GOVERNED_BY]->(Rule)
(Rule)-[:APPLIES_TO]->(Agent)
```

---

### 4.14.1 GSL-Ops Language Reference

GSL-Ops (Graph Symbolic Logic — Operations) is a deterministic subset of GSL, designed for agent orchestration rules. It compiles to Python and executes in a sandboxed `RuleContext`.

**Origin:** Ported from the GWW3 game engine's GSL language (v3), stripped of distributions, truth values, and randomness.

**Parser:** Lark (Earley) with PythonIndenter for Python-style indentation blocks.

#### Block Statements

```
MATCH (a:Agent {lifecycle: 'running'}):
    # Body executes for each matching row from Neo4j
    LET name = a.name

IF condition:
    # Conditional execution
    ...
ELIF other_condition:
    ...
ELSE:
    ...

EVERY "PT5M":
    # Schedule-triggered block — fires when interval has elapsed since last run
    # Supports ISO 8601 ("PT5M", "P1D") and shorthands (5m, 1h, 30s)
    ...

FOREACH item IN collection:
    # Iterate over a list, property, or function result
    # collection can be: list literal, node property, RANGE(), KEYS()
    ...
```

#### Simple Statements

```
LET x = expression                     # Variable binding

target.property = expression            # SET (direct assignment)
target.property += expression           # ADD (additive)
target.property -= expression           # SUB (subtractive)
target.property *= expression           # MUL (multiplicative)

SUBMIT_INTENT "capability_id" ON target                      # Create Intent
SUBMIT_INTENT "capability_id" ON target WITH {key: value}    # With arguments

LOG "message"                           # Structured log (default: info)
LOG "message" LEVEL "warning"           # With explicit level

ALERT "message"                         # Notification (dispatched via bridge)
ALERT "message" ON target               # With target context
```

#### Expressions

```
# Arithmetic: +, -, *, /, %
# Comparison: ==, !=, >, <, >=, <=, in, not in
# Logical: &&, ||, !, and, or, not
# Literals: 42, 3.14, "string", true, false, null, None
# Property access: node.property
# List literals: [a, b, c], []
# Function calls: NOW(), DURATION("PT5M"), MIN(a, b), MAX(a, b)
#                 ABS(x), CLAMP(x, lo, hi), LEN(x)
#                 STR(x), INT(x), FLOAT(x)
#                 RANGE(start, end), KEYS(node), SORTED(list), LIST(x)
```

#### Example: Agent Health Check with Circuit Breaker

```
EVERY "PT5M":
    MATCH (a:Agent {lifecycle: 'running'}):
        IF a.last_heartbeat < NOW() - DURATION("PT5M"):
            IF a.restart_count_1h < 5:
                a.restart_count_1h += 1
                SUBMIT_INTENT "restart_agent" ON a
                LOG "Restarting unresponsive agent" LEVEL "warning"
            ELSE:
                a.lifecycle = "circuit_broken"
                ALERT "Circuit breaker: agent exceeded restart limit" ON a
                LOG "Circuit breaker triggered" LEVEL "error"
```

#### Example: Bulk Retry Failed Tasks

```
EVERY "PT10M":
    MATCH (t:Task {lifecycle: 'failed'}):
        FOREACH reason IN ["timeout", "crash", "oom"]:
            IF t.error_reason == reason:
                IF t.retry_count < 3:
                    t.lifecycle = "pending"
                    t.retry_count += 1
                    LOG "Retrying task" LEVEL "info"
```

#### Compilation Pipeline

```
GSL-Ops text → Lark parse (Earley + PythonIndenter) → AST
    → GSLOpsCompiler → Python source → compile() → exec(code, safe_builtins)
    → evaluate(ctx: RuleContext)
```

- Compiled Python is cached in the Rule node's `compiled_python` property
- `compiler_version` tracks staleness — version mismatch triggers recompilation
- `exec()` runs in a restricted namespace (no `open`, `eval`, `exec`)
- `ctx.match()` uses Neo4j read-only transactions (`execute_read()`)

---

### 4.14.2 OpenClaw Bridge

The Hassaleh Daemon integrates with an OpenClaw Gateway running on the same host.

**Communication:** HTTP API (localhost, bearer token authentication).

**Capabilities:**
- **Notifications:** Rule ALERT actions are dispatched as Telegram/Discord messages via OpenClaw's messaging infrastructure
- **Session Management:** Daemon can list, spawn, and communicate with OpenClaw agent sessions
- **Wake Events:** Daemon can trigger system events to notify the main agent

**Configuration:** Gateway URL, token, and notification target are stored in DaemonConfig or environment variables (`OPENCLAW_GATEWAY_URL`, `OPENCLAW_GATEWAY_TOKEN`, `HASSALEH_NOTIFY_TARGET`).

**Graceful degradation:** The bridge is optional. If not configured or unreachable, the Daemon operates normally — alerts are logged but not dispatched to messaging channels.

---

### 4.15 Message

Inter-agent communication node. Part of a linked-list message queue.

```
(:Message {
    id: uuid(),
    timestamp: datetime({timezone: "UTC"}),
    content: "I've completed the GSL parser rewrite. 26 tests pass."
})
```

**Relationships:**
```
(Agent)-[:SENT]->(Message)
(Message)-[:NEXT]->(Message)            # linked-list for O(1) cursor traversal
(Message)-[:IN_CONTEXT_OF]->(Task)
(Message)-[:IN_CONTEXT_OF]->(Discussion)
(Message)-[:REFERENCES]->(Artifact)     # explicit edge, not JSON metadata
(Agent)-[:LAST_READ]->(Message)         # cursor — agent's read position
```

**Efficient retrieval:** Agents don't poll by timestamp. They follow their `LAST_READ` cursor forward through the `NEXT` chain. Empty check = O(1).

### 4.16 Intent

A proposed state change or action submitted by an agent, processed by the Daemon. Includes full feedback loop so agents can read results.

```
(:Intent {
    id: uuid(),
    submitted_at: datetime({timezone: "UTC"}),
    action: "update_property",          # update_property | create_node | create_edge | execute_capability
    property: "lifecycle",              # target identified via [:TARGETS] edge, not string FK
    value: "failed",
    idempotency_key: "health-check-dione-2026-03-29T13:00",
    
    # ── Feedback (written by Daemon) ──
    lifecycle: "pending",              # pending | awaiting_approval | running | success | failed | rejected
    started_at: null,
    completed_at: null,
    stdout: null,                      # captured output (for execute_capability)
    stderr: null,                      # captured errors
    error_reason: null,                # why it was rejected/failed
    exit_code: null                    # for capability execution
})
```

Agents poll their submitted Intents (via read-only SDK) to retrieve capability outputs and detect rejections. This closes the feedback loop — no agent hangs waiting for a result that never comes.

**Relationships:**
```
(Agent)-[:PROPOSED]->(Intent)
(Intent)-[:TARGETS]->(Node)
(Rule)-[:GENERATED]->(Intent)
```

### 4.17 SystemTrace

Operational log entries (errors, restarts, performance). Separated from agent communication.

```
(:SystemTrace {
    id: uuid(),
    timestamp: datetime({timezone: "UTC"}),
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

### 4.18 TimeBucket

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

### 4.19 DaemonConfig

Singleton node — all Daemon runtime settings. No external config files.

```
(:DaemonConfig {
    id: "default",
    tick_interval_ms: 1000,            # hot-path loop interval
    sweep_interval_min: 15,
    audit_schedule: "0 3 * * *",       # daily at 03:00
    intent_timeout_default_sec: 300,   # max time for an Intent to complete
    max_concurrent_actions: 10,        # async action worker pool size
    circuit_breaker_window_min: 60,    # time window for restart counting
    circuit_breaker_decay_per_sweep: 1,# subtract from restart_count each sweep
    notification_channel: "openclaw",  # how to wake agents
    health_endpoint_port: 9100         # systemd watchdog + monitoring
})
```

**Relationships:** None (singleton, read by Daemon on boot).

### 4.20 QueryConfig

Singleton node — read-access guardrails for agents. Loaded by the hassaleh.query() SDK.

```
(:QueryConfig {
    id: "default",
    default_timeout_ms: 3000,
    max_result_rows: 10000,
    max_transaction_memory_mb: 1024,
    blocked_patterns: ["MATCH (a), (b), (c)", "DETACH DELETE", "CALL db."],
    report_query_timeout_ms: 30000
})
```

**Relationships:** None (singleton, read by Agent SDK on init).

### 4.21 SystemVersion

Singleton node — tracks the graph schema version for safe migrations.

```
(:SystemVersion {
    id: "hassaleh",
    schema_version: "0.4",
    last_migration: datetime({timezone: "UTC"}),
    compatible_daemon_versions: ["1.0", "0.5"]
})
```

**Relationships:** None (singleton, checked by Daemon on boot).

The Daemon checks this on boot and refuses to start (or applies migration scripts) if the code version is incompatible.

### 4.22 Discussion

A structured multi-agent discussion for collaborative decision-making.

```
(:Discussion {
    id: uuid(),
    topic: "Should we use LALR or Earley parser for GSL?",
    lifecycle: "success",              # resolved
    resolution: "Earley — handles ambiguity, performance is sufficient",
    resolved_at: datetime({timezone: "UTC"})
})
```

**Relationships:**
```
(Discussion)-[:IN_CONTEXT_OF]->(Project)
(Discussion)-[:IN_CONTEXT_OF]->(Task)
(Agent)-[:CONTRIBUTED {position: "pro-earley", reasoning: "...", timestamp: datetime({timezone: "UTC"})}]->(Discussion)
(Discussion)-[:DECIDED_BY]->(Agent)
```

---

## 5. Universal Lifecycle Status

All stateful nodes use a consistent lifecycle enum:

| Status | Meaning |
|--------|---------|
| `available` | Static resource ready for use (Model, Capability) |
| `deprecated` | Static resource still functional but scheduled for removal |
| `pending` | Created, not yet started |
| `awaiting_approval` | Parked — requires human confirmation before proceeding |
| `running` | Actively executing |
| `success` | Completed successfully |
| `failed` | Completed with failure |
| `rejected` | Denied by Daemon (permission check failed, rule conflict, etc.) |
| `suspended` | Paused (by rule or human) — can be resumed |
| `archived` | Retained for history, no longer active |

Used by: Agent, Model, Capability, Rule, Project, Sprint, Task, Milestone, CronJob, Discussion, Intent.

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
    IF a.last_heartbeat < datetime({timezone: 'UTC'}) - duration("PT15M"):
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
    IF t.expires_at < datetime({timezone: 'UTC'}):
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

## 8. Human-in-the-Loop (HITL)

For sensitive actions (Capabilities with `requires_confirmation: true`), the Daemon does **not** execute immediately:

1. Agent submits an Intent targeting a Capability that requires confirmation
2. Daemon sets the Intent to `awaiting_approval`
3. Human admin is notified (via OpenClaw, Telegram, or Daemon health endpoint)
4. Admin reviews the Intent in the graph (via CLI `hassaleh approve <intent-id>` or direct Cypher as `neo4j` admin user)
5. Admin sets Intent to `pending` (approved) or `rejected` (denied with reason)
6. Daemon processes the Intent on its next tick

This keeps humans in the loop for irreversible or dangerous operations without blocking the entire orchestration pipeline.

---

## 9. Observability

### 9.1 Daemon Health Endpoint

The Daemon exposes a minimal HTTP health endpoint (port configured in `DaemonConfig.health_endpoint_port`):

```json
GET /health
{
    "status": "healthy",
    "uptime_seconds": 3600,
    "tick_count": 3592,
    "pending_intents": 2,
    "active_workers": 1,
    "last_tick_ms": 12,
    "schema_version": "0.5",
    "agents_running": 3,
    "agents_failed": 0
}
```

### 9.2 SystemTrace + TimeBucket

All operational events are logged as SystemTrace nodes, partitioned by TimeBucket (daily). See sections 4.17 and 4.18.

### 9.3 Alerting

Alerts are dispatched via the Daemon's notification channel (configured in DaemonConfig):
- Agent failures (circuit breaker triggered)
- Intent rejections (permission denied)
- Intents awaiting human approval
- Schema version mismatches
- Daemon health anomalies (tick latency > threshold)

---

## 10. Agent Registration & Discovery

New agents register by having the human admin create an `(:Agent)` node in the graph with appropriate `[:USES_MODEL]`, `[:HAS_CAPABILITY]`, and `[:OPERATES_IN]` edges.

The Daemon discovers agents by querying for `(:Agent)` nodes with `lifecycle: "pending"` and initiates their startup sequence (spawning the process, injecting credentials, setting `os_pid`).

Self-registration by agents is **not supported** — this is a deliberate security decision. All agent creation flows through the human admin.

---

## 11. File Coordination

When agents work on shared workspaces, file coordination uses the Intent mechanism:

1. Agent A finishes writing a file and submits an Intent: `{action: "artifact_ready", path: "reports/analysis.md"}`
2. Daemon creates/updates an `(:Artifact)` node and links it to the Task
3. Agent B (or a rule) can query for `(:Artifact)` nodes in the `success` state before reading the file

This avoids race conditions where one agent reads a file that another is still writing.

---

## 12. Multi-Host Considerations

The initial version is **single-host** (Ubuntu). However, the architecture supports multi-host extension:
- Neo4j can be accessed remotely (Bolt protocol)
- Agents on remote hosts use the `hassaleh.query()` SDK over the network
- Intents are submitted via HTTP API to the Daemon
- Workspace access would require shared filesystems (NFS, SSHFS) or artifact transfer via the graph

Multi-host support is **deferred** to post-MVP.

---

## 13. Testing Strategy

| Level | What | How |
|-------|------|-----|
| **Unit** | SDK query guards, Intent validation, GSL-Ops compiler | pytest, mock Neo4j driver |
| **Integration** | Daemon tick loop, Intent processing, async workers | Real Neo4j test instance, test agent |
| **End-to-End** | Full loop: agent → Intent → Daemon → action → feedback | Dedicated test DB, dummy agent, real systemd service |
| **Rule Testing** | GSL-Ops rules produce correct Intents | Parse → compile → evaluate against test graph |
| **Chaos** | Daemon crash recovery, zombie Intents, circuit breakers | Kill Daemon mid-tick, verify recovery on restart |

The GWW3 test database infrastructure (multiple Neo4j instances on different ports) is reused for Hassaleh testing.

---

## 14. Reporting

A reporting agent queries the graph to generate:

- **Project status reports:** Sprint progress, task completion, blockers
- **Agent activity reports:** Actions per agent, uptime, error rates
- **Discussion summaries:** What was debated, who said what, what was decided
- **Resource usage:** Model costs, API calls, credit burn rate
- **Timeline analysis:** Planned vs actual dates, velocity trends

All reports generated from **graph queries only** — no external state needed.

---

## 15. Technology Stack

| Component | Technology |
|-----------|-----------|
| Database | Neo4j 2026.x (Community Edition) |
| Language | Python 3.13 |
| Rule Engine | GSL-Ops (deterministic subset of GSL from GWW3) |
| Parser | Lark 1.3.1 (Earley + PythonIndenter) |
| Agent Runtime | OpenClaw, Google ADK, standalone |
| Daemon | Python asyncio (systemd-managed, persistent) |
| Agent SDK | `hassaleh.query()` — guarded read-only Neo4j access |
| CLI | `hassaleh` (planned) |
| Platform | Ubuntu 24.04+ (initial) |

---

## 16. Relationship to GWW3

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

## 17. Roadmap

| Phase | Goal |
|-------|------|
| **0 — Concept** ✅ | Architecture document, GitHub repo |
| **1 — Schema + Seed** | Neo4j schema (constraints, indexes), DaemonConfig/QueryConfig/SystemVersion singletons, seed data for 1 project + 1 agent |
| **2 — Daemon MVP** | Persistent async service (systemd), Intent processing loop, Capability enforcement via `sudo -n -u`, SecretRef resolution, Intent feedback (stdout/stderr) |
| **2.5 — Agent SDK** | `hassaleh.query()` read-proxy with Cypher linting, per-query timeouts from DB, convenience methods |
| **3 — Blackboard Spike** | 1 real agent (Dione) submitting Intents + reading results. Prove the full loop: agent → Intent → Daemon → action → feedback → agent reads result |

### MVP Sprint (Phase 1–3): First 6 Files

| File | Purpose |
|------|---------|
| `schema.cypher` | CREATE CONSTRAINT/INDEX for MVP node types (Agent, Capability, Workspace, Intent, Task, DaemonConfig, QueryConfig, SystemVersion) |
| `seed.cypher` | Create initial Agent, Capability (`ls` command), Task, Workspace, DaemonConfig, QueryConfig, SystemVersion nodes |
| `setup_os.sh` | Create OS users (`hassaleh-svc`, `hassaleh-agent`, `hassaleh-fs`, `hassaleh-writer`, `hassaleh-exec`, `hassaleh-net`, `hassaleh-pkg`), configure `/etc/sudoers.d/hassaleh`, install `hassaleh-daemon.service` systemd unit file |
| `daemon.py` | Asyncio event loop, Neo4j polling for pending Intents, `create_subprocess_exec` async workers, zombie recovery on boot, graceful shutdown, systemd watchdog |
| `sdk.py` | `hassaleh.query()` (parameterized read-only) + `hassaleh.submit_intent()` |
| `agent_dummy.py` | Test agent: claim a Task, write a file to Workspace, submit Intent to execute `ls -la` via Capability, read result |

### Subsequent Phases

| Phase | Goal |
|-------|------|
| **4 — Rule Engine** | Port GSL-Ops from GWW3, compile on boot, priority-based conflict resolution |
| **5 — CLI** | `hassaleh init`, `hassaleh status`, `hassaleh report` |
| **6 — Integration** | OpenClaw integration, first operational rules (health checks, task assignment) |
| **7 — Reporting** | Graph-based project/agent reporting |
| **8 — Multi-Agent** | Discussion protocol, consensus mechanism, workspace permission management |
| **9 — Docker Dev-Env** | Dockerized development environment: Neo4j instance + simulated OS-level isolation (hassaleh-svc/agent/exec users) + Daemon + sample agent. Enables `docker compose up` onboarding for open-source contributors |

---

## Changelog

| Date | Version | Changes |
|------|---------|---------|
| 2026-03-29 | v0.1 | Initial concept: 14 node types, orchestration rules, roadmap |
| 2026-03-29 | v0.2 | Gemini Deep Think review: +7 node types (Workspace, ConfigFile, Memory, SecretRef, Message, Intent, SystemTrace, TimeBucket, Discussion split). Trusted Daemon architecture. GSL-Ops subset. Priority-based conflict resolution. Circuit breakers. Task timeouts. Idempotency keys. Edge-first modeling (no string FKs). Message cursor pattern. TimeBucket log partitioning. Universal LifecycleStatus enum. Roadmap reordered (Daemon before Rule Engine). |
| 2026-03-29 | v0.3 | OS-level enforcement: dedicated OS users (`hassaleh-svc`, `hassaleh-agent`). Dual Neo4j users (`hassaleh_daemon` r/w, `hassaleh_reader` r/o). Per-action OS user isolation (`hassaleh-fs`, `hassaleh-writer`, `hassaleh-exec`, `hassaleh-net`, `hassaleh-pkg`). Triple-layered enforcement (graph + daemon + OS). `exec_as_user` field on SystemAction nodes. |
| 2026-03-29 | v0.4 | **Second Gemini DT review.** CronJob Daemon → persistent asyncio service (systemd). 1-second ticks + async action workers (never blocks). Intent feedback loop (lifecycle, stdout, stderr, error_reason). `hassaleh.query()` read-proxy SDK with Cypher linting. Per-query timeouts from DB (not global DBMS timeout). QueryConfig + DaemonConfig + SystemVersion singleton nodes. All configuration in graph (no external config files). Workspaces project-specific with direct OS-level agent r/w access. Safe subprocess execution (`sudo -n -u`, no `shell=True`). Rules compiled on boot + on-change (not per-tick). Memory limits 1 GB (not 256 MB). Roadmap reordered: Daemon → SDK → Blackboard Spike → Rule Engine. |
| 2026-03-29 | v0.5 | **Third Gemini DT review.** async subprocess, zombie recovery, systemd watchdog, HITL, SDK safety, observability, testing, file coordination, agent registration, multi-host, MVP sprint scope. |
| 2026-03-29 | v1.0 | **Fourth Gemini DT review (final — GO).** Merged Tool + Skill + SystemAction → **Capability** (single unified node). Fixed string FK in Intent (`target_node_id` → `[:TARGETS]` edge). Added `available` + `deprecated` to LifecycleStatus. Fixed CronJob `last_status` → `lifecycle`. Message metadata → `[:REFERENCES]` edge. Graceful Daemon shutdown (SIGTERM handler, orphan process cleanup). Neo4j read-only fallback handling. UTC mandate for all timestamps. `setup_os.sh` added to MVP (6th file). Docker dev-env added to roadmap (Phase 9). 17 sections, 22 node types. |
| 2026-03-29 | v1.1 | **Final consistency audit (15 fixes).** Removed orphaned SystemAction text block from ConfigFile section. Fixed all `datetime()` → `datetime({timezone: 'UTC'})`. Renumbered sections 4.4–4.22 (no gaps). Fixed cross-references (SecretRef 4.13, SystemTrace/TimeBucket 4.17/4.18). Replaced all stale terminology (SystemAction→Capability, Tool→Capability, execute_command→execute_capability). Added `Rule` to LifecycleStatus "Used by" list. Added Relationships sections to Model, ConfigFile, Milestone, Artifact, DaemonConfig, QueryConfig, SystemVersion. Fixed SecretRef relationship (Tool→Capability). Added all 7 OS users to setup_os.sh description. Fixed node type count to 22. |
| 2026-03-29 | v1.2 | **Second consistency audit (8 fixes).** "with which tools"→"capabilities" (Sec 1). `datetime()` → UTC in GSL pseudocode (Sec 7.1, 7.3). Added Relationships: None to QueryConfig (Sec 4.20). Renamed v1.01→v1.1, v1.02→v1.2 for cleaner versioning. |

---

*This document has been reviewed through 4 iterations of Gemini Deep Think analysis plus 2 consistency audits. It is internally consistent and ready for implementation.*
```

---
## FILE: src/hassaleh/engine/gsl_ops.lark
```
// ═══════════════════════════════════════════════════════════════
// GSL-Ops (Hassaleh Deterministic Rule Language) — Lark EBNF Grammar
// ═══════════════════════════════════════════════════════════════
//
// A deterministic subset of GSL (GWW3 Symbolic Logic).
// No distributions, no truth values, no randomness.
// Designed for agent orchestration rules.
//
// Parser:  Earley + PythonIndenter
// Syntax:  Python-style colon blocks
//
//   MATCH (a:Agent):
//       IF a.last_heartbeat < NOW() - duration("PT5M"):
//           IF a.restart_count_1h < 5:
//               SUBMIT_INTENT "restart_agent" ON a
//           ELSE:
//               ALERT "Circuit breaker: agent unresponsive" ON a
//
// Reference: docs/CONCEPT.md v1.2, Section 4.14
// Origin: Ported from GWW3 gsl.lark v3, stripped for determinism.

// ──────────────────────────────────────────
// Program structure
// ──────────────────────────────────────────

start: (_NEWLINE | statement)*

?statement: match_block
          | if_block
          | elif_block
          | else_block
          | every_block
          | foreach_block
          | let_stmt
          | effect_stmt
          | action_stmt
          | comment_line

// ──────────────────────────────────────────
// Block-opening statements (colon + indent)
// ──────────────────────────────────────────

// MATCH …:  →  Graph pattern query. Entire line captured as terminal.
match_block: MATCH_LINE _NEWLINE _INDENT body _DEDENT

// IF / ELIF / ELSE  →  Conditional branching
if_block: "IF" expr ":" _NEWLINE _INDENT body _DEDENT
elif_block: "ELIF" expr ":" _NEWLINE _INDENT body _DEDENT
else_block: "ELSE" ":" _NEWLINE _INDENT body _DEDENT

// EVERY …:  →  Time-triggered rule (evaluated by Daemon scheduler)
every_block: "EVERY" duration ":" _NEWLINE _INDENT body _DEDENT

// FOREACH var IN collection:  →  Iterate over a list/collection
foreach_block: "FOREACH" NAME "IN" expr ":" _NEWLINE _INDENT body _DEDENT

// Body: sequence of statements inside an indented block
body: (_NEWLINE | statement)*

// ──────────────────────────────────────────
// Simple statements (no block)
// ──────────────────────────────────────────

// LET — variable binding (deterministic only, no sampling)
let_stmt: "LET" NAME "=" expr

// Effects — property modifications (deterministic, no truth values)
effect_stmt: PROP_ACCESS "+=" expr            -> effect_add
           | PROP_ACCESS "-=" expr            -> effect_sub
           | PROP_ACCESS "*=" expr            -> effect_mul
           | PROP_ACCESS "=" expr             -> effect_set

// Hassaleh-specific actions
action_stmt: submit_intent
           | log_stmt
           | alert_stmt

// SUBMIT_INTENT — create an Intent node for the Daemon to process
//   SUBMIT_INTENT "capability_id" ON target_var
//   SUBMIT_INTENT "capability_id" ON target_var WITH {key: value, ...}
submit_intent: "SUBMIT_INTENT" STRING "ON" NAME with_clause?

// LOG — structured logging
//   LOG "message" LEVEL "info"
//   LOG "message"
log_stmt: "LOG" STRING log_level?

log_level: "LEVEL" STRING

// ALERT — notification trigger (dispatched via Daemon)
//   ALERT "message" ON target_var
//   ALERT "message"
alert_stmt: "ALERT" STRING ("ON" NAME)?

// WITH clause for SUBMIT_INTENT
with_clause: "WITH" "{" kv_pair ("," kv_pair)* "}"
kv_pair: NAME ":" expr

// Comment line (standalone, not inline)
comment_line: COMMENT

// ──────────────────────────────────────────
// Duration literals
// ──────────────────────────────────────────

// ISO 8601 durations: "PT5M", "PT1H", "P1D"
// Also support human-readable: 5m, 1h, 30s, 1d
duration: STRING
        | DURATION_LITERAL

DURATION_LITERAL.3: /\d+[smhd]/

// ──────────────────────────────────────────
// Expressions (precedence: low → high)
// ──────────────────────────────────────────

?expr: or_expr

?or_expr: and_expr (OR_OP and_expr)*

?and_expr: not_expr (AND_OP not_expr)*

?not_expr: NOT_OP not_expr                -> negation
         | comparison

?comparison: arith (COMP_OP arith)?

?arith: term ((PLUS | MINUS) term)*

?term: factor ((STAR | SLASH | PERCENT) factor)*

?factor: MINUS atom                       -> neg
       | atom

?atom: NUMBER                             -> number
     | STRING                             -> string_literal
     | PROP_ACCESS                        -> prop_access
     | NAME                               -> var_ref
     | func_call
     | list_literal
     | "(" expr ")"
     | "true"                             -> bool_true
     | "false"                            -> bool_false
     | "null"                             -> null_literal
     | "None"                             -> null_literal

// List literal: [expr, expr, ...]
list_literal: "[" list_items? "]"
list_items: expr ("," expr)*

// ──────────────────────────────────────────
// Built-in functions (deterministic only)
// ──────────────────────────────────────────

func_call: NAME "(" func_args? ")"

func_args: expr ("," expr)*

// Available functions (resolved in compiler/runtime):
//   NOW()              — current UTC datetime
//   DURATION("PT5M")   — parse ISO 8601 duration
//   ABS(x)             — absolute value
//   MIN(a, b)          — minimum
//   MAX(a, b)          — maximum
//   CLAMP(x, lo, hi)   — clamp value
//   LEN(x)             — length/count
//   STR(x)             — string conversion
//   INT(x)             — integer conversion
//   FLOAT(x)           — float conversion

// ──────────────────────────────────────────
// Operators
// ──────────────────────────────────────────

OR_OP.2:    "||" | "or"
AND_OP.2:   "&&" | "and"
NOT_OP.2:   "!" | "not"
COMP_OP.2:  ">=" | "<=" | ">" | "<" | "==" | "!=" | "not in" | "in"
PLUS:     "+"
MINUS:    "-"
STAR:     "*"
SLASH:    "/"
PERCENT:  "%"

// ──────────────────────────────────────────
// Terminals
// ──────────────────────────────────────────

// MATCH line: captures entire "MATCH ... :" as one token.
// Cypher patterns contain colons (e.g. :Agent), so we capture the whole line.
MATCH_LINE.3: /MATCH\s+.+:/

// Property access: Node.property (e.g. a.lifecycle, agent.last_heartbeat)
PROP_ACCESS.2: /[a-zA-Z_]\w*\.[a-zA-Z_]\w*/

// Identifiers
NAME: /[a-zA-Z_]\w*/

// Numbers: 42, 3.14, 1_000, 5e9
NUMBER: /\-?\d[\d_]*(\.\d[\d_]*)?([eE][\-+]?\d+)?/

// Strings: "hello", 'world'
STRING: /\"[^\"]*\"/ | /'[^']*'/

// Comments
COMMENT: /#[^\n]*/

// ──────────────────────────────────────────
// Whitespace
// ──────────────────────────────────────────

_NEWLINE: /(\r?\n[\t ]*)+/

%ignore /[\t \f]+/
%ignore COMMENT

%declare _INDENT _DEDENT
```

---
## FILE: src/hassaleh/engine/parser.py
```
"""GSL-Ops Parser — Parse deterministic rule text into Lark AST.

Uses Lark with PythonIndenter for Python-style colon blocks.

Usage:
    from hassaleh.engine.parser import parse_rule
    tree = parse_rule(rule_text)
"""

from __future__ import annotations

from pathlib import Path

from lark import Lark
from lark.indenter import PythonIndenter

# Grammar file lives next to this module
_GRAMMAR_PATH = Path(__file__).parent / "gsl_ops.lark"

# Singleton parser (thread-safe for reads, Lark is immutable after construction)
_parser: Lark | None = None


def _get_parser() -> Lark:
    """Lazy-initialize the Lark parser."""
    global _parser
    if _parser is None:
        _parser = Lark(
            _GRAMMAR_PATH.read_text(),
            parser="earley",
            postlex=PythonIndenter(),
            propagate_positions=True,
        )
    return _parser


def parse_rule(rule_text: str) -> "Tree":
    """Parse a GSL-Ops rule into a Lark Tree.

    Args:
        rule_text: GSL-Ops source code

    Returns:
        Lark Tree (AST)

    Raises:
        lark.exceptions.UnexpectedInput: Parse error
    """
    parser = _get_parser()
    # Ensure trailing newline (PythonIndenter needs it)
    if not rule_text.endswith("\n"):
        rule_text += "\n"
    return parser.parse(rule_text)
```

---
## FILE: src/hassaleh/engine/compiler.py
```
"""GSL-Ops Compiler — Transpile GSL-Ops parse trees to executable Python.

Takes a Lark Tree (from parser.parse_rule) and generates a Python
function that, when called with a RuleContext, evaluates the rule.

Generated code signature:
    def evaluate(ctx: RuleContext) -> None:
        # ... emits intents, logs, alerts to ctx

The generated source is human-readable and stored in the Rule node's
compiled_python property.

Reference: docs/CONCEPT.md v1.2, Section 4.14
"""

from __future__ import annotations

import re
from lark import Tree, Token
from hassaleh.engine.parser import parse_rule

COMPILER_VERSION = "gsl-ops-0.1"


class GSLOpsCompiler:
    """Compile a GSL-Ops parse tree to Python source code."""

    def __init__(self, rule_id: str = "unknown"):
        self.rule_id = rule_id
        self._indent = 0
        self._lines: list[str] = []

    def compile(self, tree: Tree) -> str:
        """Compile a Lark Tree (start node) to Python source."""
        self._indent = 0
        self._lines = []

        # Header
        self._emit("from hassaleh.engine.runtime import RuleContext")
        self._emit("")
        self._emit("")
        self._emit("def evaluate(ctx: RuleContext) -> None:")
        self._indent = 1
        self._emit(f'"""Compiled from GSL-Ops rule: {self.rule_id}"""')

        # Process top-level statements
        has_statements = False
        for child in tree.children:
            if isinstance(child, Tree):
                self._compile_node(child)
                has_statements = True

        if not has_statements:
            self._emit("pass")

        return "\n".join(self._lines) + "\n"

    # ── Dispatch ──

    def _compile_node(self, node: Tree) -> None:
        handler = getattr(self, f"_compile_{node.data}", None)
        if handler:
            handler(node)
        else:
            for child in node.children:
                if isinstance(child, Tree):
                    self._compile_node(child)

    # ── MATCH block ──

    def _compile_match_block(self, node: Tree) -> None:
        match_line = self._get_token(node, "MATCH_LINE")
        # Strip "MATCH " prefix and trailing ":"
        cypher = match_line[6:].rstrip().rstrip(":")

        self._emit(f'for _row in ctx.match({cypher!r}):')
        self._indent += 1

        # Extract variable names from Cypher pattern
        var_names = self._extract_cypher_vars(cypher)
        for var in var_names:
            self._emit(f'{var} = _row["{var}"]')

        # Compile body
        body = self._find_child(node, "body")
        if body:
            self._compile_body(body)
        else:
            self._emit("pass")

        self._indent -= 1

    # ── IF / ELIF / ELSE ──

    def _compile_if_block(self, node: Tree) -> None:
        cond = self._extract_condition(node)
        self._emit(f"if {cond}:")
        self._indent += 1
        body = self._find_child(node, "body")
        if body:
            self._compile_body(body)
        else:
            self._emit("pass")
        self._indent -= 1

    def _compile_elif_block(self, node: Tree) -> None:
        cond = self._extract_condition(node)
        self._emit(f"elif {cond}:")
        self._indent += 1
        body = self._find_child(node, "body")
        if body:
            self._compile_body(body)
        else:
            self._emit("pass")
        self._indent -= 1

    def _compile_else_block(self, node: Tree) -> None:
        self._emit("else:")
        self._indent += 1
        body = self._find_child(node, "body")
        if body:
            self._compile_body(body)
        else:
            self._emit("pass")
        self._indent -= 1

    # ── FOREACH block ──

    def _compile_foreach_block(self, node: Tree) -> None:
        name = self._get_token(node, "NAME")
        # Expression is everything between NAME and body
        expr_children = []
        body = None
        found_name = False
        for child in node.children:
            if isinstance(child, Tree) and child.data == "body":
                body = child
            elif isinstance(child, Token) and child.type == "NAME" and not found_name:
                found_name = True
            elif found_name and not (isinstance(child, Tree) and child.data == "body"):
                expr_children.append(child)

        collection_expr = self._compile_expr_list(expr_children)
        self._emit(f"for {name} in {collection_expr}:")
        self._indent += 1

        if body:
            self._compile_body(body)
        else:
            self._emit("pass")

        self._indent -= 1

    # ── EVERY block ──

    def _compile_every_block(self, node: Tree) -> None:
        # Extract duration
        duration = self._find_child(node, "duration")
        dur_str = self._compile_duration(duration) if duration else '"PT1M"'

        self._emit(f'if ctx.should_run_schedule({dur_str}):')
        self._indent += 1

        body = self._find_child(node, "body")
        if body:
            self._compile_body(body)
        else:
            self._emit("pass")

        self._indent -= 1

    # ── LET ──

    def _compile_let_stmt(self, node: Tree) -> None:
        # Grammar: let_stmt: "LET" NAME "=" expr
        # Children: [NAME_token, ...expr_nodes]
        # The first NAME token is the variable name; everything after is the expr.
        name = self._get_token(node, "NAME")
        # Find the index of the first NAME token, take everything after it
        name_idx = 0
        for i, c in enumerate(node.children):
            if isinstance(c, Token) and c.type == "NAME":
                name_idx = i
                break
        # Expr is everything after the NAME (Lark strips "LET" and "=" keywords)
        expr_children = node.children[name_idx + 1:]
        expr = self._compile_expr_list(expr_children)
        self._emit(f"{name} = {expr}")

    # ── Effects ──

    def _compile_effect_set(self, node: Tree) -> None:
        prop, expr = self._extract_effect(node)
        node_var, prop_name = prop.split(".", 1)
        self._emit(f'ctx.set_property({node_var}, "{prop_name}", {expr})')

    def _compile_effect_add(self, node: Tree) -> None:
        prop, expr = self._extract_effect(node)
        node_var, prop_name = prop.split(".", 1)
        self._emit(f'ctx.add_property({node_var}, "{prop_name}", {expr})')

    def _compile_effect_sub(self, node: Tree) -> None:
        prop, expr = self._extract_effect(node)
        node_var, prop_name = prop.split(".", 1)
        self._emit(f'ctx.sub_property({node_var}, "{prop_name}", {expr})')

    def _compile_effect_mul(self, node: Tree) -> None:
        prop, expr = self._extract_effect(node)
        node_var, prop_name = prop.split(".", 1)
        self._emit(f'ctx.mul_property({node_var}, "{prop_name}", {expr})')

    # ── Actions ──

    def _compile_submit_intent(self, node: Tree) -> None:
        cap_id = self._get_token(node, "STRING").strip('"').strip("'")
        target_var = self._get_token(node, "NAME")
        with_clause = self._find_child(node, "with_clause")

        if with_clause:
            kv_pairs = list(with_clause.find_data("kv_pair"))
            args_parts = []
            for kv in kv_pairs:
                key = self._get_token(kv, "NAME")
                val_children = [c for c in kv.children
                                if not (isinstance(c, Token) and c.type == "NAME")]
                val = self._compile_expr_list(val_children)
                args_parts.append(f'"{key}": {val}')
            args_dict = "{" + ", ".join(args_parts) + "}"
            self._emit(f'ctx.submit_intent("{cap_id}", {target_var}, {args_dict})')
        else:
            self._emit(f'ctx.submit_intent("{cap_id}", {target_var})')

    def _compile_log_stmt(self, node: Tree) -> None:
        strings = [c for c in node.children
                   if isinstance(c, Token) and c.type == "STRING"]
        message = strings[0].strip('"').strip("'") if strings else ""
        level_node = self._find_child(node, "log_level")
        if level_node:
            level_str = self._get_token(level_node, "STRING").strip('"').strip("'")
            self._emit(f'ctx.log("{message}", level="{level_str}")')
        else:
            self._emit(f'ctx.log("{message}")')

    def _compile_alert_stmt(self, node: Tree) -> None:
        msg = self._get_token(node, "STRING").strip('"').strip("'")
        target_var = self._get_token(node, "NAME")
        if target_var:
            self._emit(f'ctx.alert("{msg}", {target_var})')
        else:
            self._emit(f'ctx.alert("{msg}")')

    # ── Body ──

    def _compile_body(self, node: Tree) -> None:
        has_stmts = False
        for child in node.children:
            if isinstance(child, Tree):
                self._compile_node(child)
                has_stmts = True
        if not has_stmts:
            self._emit("pass")

    # ── Expression compilation ──

    def _extract_condition(self, node: Tree) -> str:
        """Extract the condition expression from an IF/ELIF node."""
        # Everything that's not the body is the condition
        cond_parts = []
        for child in node.children:
            if isinstance(child, Tree) and child.data == "body":
                continue
            if isinstance(child, Tree):
                cond_parts.append(child)
            elif isinstance(child, Token) and child.type not in (
                "_NEWLINE", "_INDENT", "_DEDENT"
            ):
                cond_parts.append(child)
        return self._compile_expr_list(cond_parts)

    def _extract_effect(self, node: Tree) -> tuple[str, str]:
        """Extract (prop_access, value_expr) from an effect node."""
        prop = self._get_token(node, "PROP_ACCESS")
        value_children = [c for c in node.children
                          if not (isinstance(c, Token) and c.type in
                                  ("PROP_ACCESS", "MOD_OP"))]
        value = self._compile_expr_list(value_children)
        return prop, value

    def _compile_expr_list(self, nodes: list) -> str:
        """Compile a list of AST nodes/tokens into a Python expression."""
        parts = []
        for node in nodes:
            if isinstance(node, Token):
                parts.append(self._compile_token(node))
            elif isinstance(node, Tree):
                parts.append(self._compile_expr_tree(node))
        result = " ".join(parts) if parts else "None"
        return result.strip()

    def _compile_expr_tree(self, node: Tree) -> str:
        """Compile a single expression tree node to Python."""
        d = node.data

        if d == "number":
            return str(node.children[0])

        if d == "string_literal":
            return str(node.children[0])

        if d == "prop_access":
            pa = str(node.children[0])
            var, prop = pa.split(".", 1)
            return f'ctx.prop({var}, "{prop}")'

        if d == "var_ref":
            name = str(node.children[0])
            return name

        if d == "bool_true":
            return "True"

        if d == "bool_false":
            return "False"

        if d == "null_literal":
            return "None"

        if d == "negation":
            # Grammar: not_expr: NOT_OP not_expr -> negation
            # children[0] = NOT_OP token, children[1] = expression tree
            tree_children = [c for c in node.children if isinstance(c, Tree)]
            inner = self._compile_expr_tree(tree_children[0]) if tree_children else "None"
            return f"(not {inner})"

        if d == "neg":
            # Grammar: factor: MINUS atom -> neg
            # children[0] = MINUS token, children[1] = atom tree
            tree_children = [c for c in node.children if isinstance(c, Tree)]
            inner = self._compile_expr_tree(tree_children[0]) if tree_children else "0"
            return f"(-{inner})"

        if d == "list_literal":
            return self._compile_list_literal(node)

        if d == "func_call":
            return self._compile_func_call(node)

        if d in ("comparison", "and_expr", "or_expr", "arith", "term"):
            parts = []
            for ch in node.children:
                if isinstance(ch, Token):
                    parts.append(self._compile_token(ch))
                elif isinstance(ch, Tree):
                    parts.append(self._compile_expr_tree(ch))
            return f"({' '.join(parts)})"

        # Fallback: recurse
        parts = []
        for ch in node.children:
            if isinstance(ch, Token):
                parts.append(self._compile_token(ch))
            elif isinstance(ch, Tree):
                parts.append(self._compile_expr_tree(ch))
        return " ".join(parts)

    def _compile_list_literal(self, node: Tree) -> str:
        """Compile [a, b, c] to a Python list."""
        items_node = self._find_child(node, "list_items")
        if not items_node:
            return "[]"
        items = [self._compile_expr_tree(ch) for ch in items_node.children
                 if isinstance(ch, Tree)]
        return f"[{', '.join(items)}]"

    def _compile_func_call(self, node: Tree) -> str:
        """Compile a function call."""
        func_name = self._get_token(node, "NAME")
        args_node = self._find_child(node, "func_args")

        # Map GSL-Ops function names to runtime methods
        FUNC_MAP = {
            "NOW": "ctx.now()",
            "DURATION": None,  # special handling
            "ABS": "abs",
            "MIN": "min",
            "MAX": "max",
            "CLAMP": "ctx.clamp",
            "LEN": "len",
            "STR": "str",
            "INT": "int",
            "FLOAT": "float",
            "RANGE": "range",
            "KEYS": "ctx.keys",
            "SORTED": "sorted",
            "LIST": "list",
        }

        if func_name == "NOW":
            return "ctx.now()"

        if func_name == "DURATION":
            if args_node:
                arg = self._compile_expr_list(list(args_node.children))
                return f"ctx.duration({arg})"
            return 'ctx.duration("PT0S")'

        mapped = FUNC_MAP.get(func_name)
        if mapped and args_node:
            args = [self._compile_expr_tree(ch) for ch in args_node.children
                    if isinstance(ch, Tree)]
            return f"{mapped}({', '.join(args)})"

        # Unknown function — reject at compile time (security: prevents
        # calling arbitrary ctx methods like ctx.session())
        raise ValueError(
            f"Unknown function '{func_name}' in rule '{self.rule_id}'. "
            f"Allowed: {', '.join(sorted(FUNC_MAP.keys()))}"
        )

    def _compile_token(self, token: Token) -> str:
        t = str(token)
        if token.type == "OR_OP":
            return "or"
        if token.type == "AND_OP":
            return "and"
        if token.type == "NOT_OP":
            return "not"
        if token.type == "COMP_OP":
            return t
        return t

    def _compile_duration(self, node: Tree) -> str:
        """Compile a duration node."""
        # Check for STRING child (ISO 8601)
        string_tok = self._get_token(node, "STRING")
        if string_tok:
            return string_tok

        # Check for DURATION_LITERAL (e.g. "5m", "1h")
        dur_lit = self._get_token(node, "DURATION_LITERAL")
        if dur_lit:
            return f'"{dur_lit}"'

        return '"PT0S"'

    # ── Helpers ──

    def _emit(self, line: str) -> None:
        self._lines.append("    " * self._indent + line)

    def _get_token(self, node: Tree, token_type: str) -> str:
        for ch in node.children:
            if isinstance(ch, Token) and ch.type == token_type:
                return str(ch)
        return ""

    def _find_child(self, node: Tree, data: str) -> Tree | None:
        for ch in node.children:
            if isinstance(ch, Tree) and ch.data == data:
                return ch
        return None

    def _extract_cypher_vars(self, cypher: str) -> list[str]:
        """Extract bound variable names from a Cypher pattern."""
        node_vars = re.findall(r'\((\w+)(?::\w+)?(?:\s*\{[^}]*\})?\)', cypher)
        rel_vars = re.findall(r'\[(\w+):\w+', cypher)
        return list(dict.fromkeys(node_vars + rel_vars))


# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────

def compile_rule(rule_text: str, rule_id: str = "unknown") -> str:
    """Parse and compile a GSL-Ops rule to Python source.

    Args:
        rule_text: GSL-Ops source text
        rule_id: Rule identifier

    Returns:
        Python source code string
    """
    tree = parse_rule(rule_text)
    compiler = GSLOpsCompiler(rule_id=rule_id)
    return compiler.compile(tree)
```

---
## FILE: src/hassaleh/engine/runtime.py
```
"""GSL-Ops Runtime — Execution context for compiled rules.

Compiled GSL-Ops rules call methods on a RuleContext object.
This module provides the runtime environment.

Unlike GWW3's stochastic runtime, this is fully deterministic:
no distributions, no probability gates, no sampling.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("hassaleh.engine.runtime")


# ──────────────────────────────────────────────
# Intent — output of rule evaluation
# ──────────────────────────────────────────────

class PropertyOp(str, Enum):
    """Property modification operations."""
    SET = "set"
    ADD = "add"
    SUB = "sub"
    MUL = "mul"


@dataclass
class RuleIntent:
    """An intent to modify a property, produced by a rule.

    Intents are accumulated per (node_id, property) and resolved
    by the priority-based conflict resolver.
    """
    node_id: str
    node_var: str
    property: str
    op: PropertyOp
    value: Any
    rule_id: str = ""
    priority: int = 100


@dataclass
class SubmitIntentAction:
    """An intent to create a Hassaleh Intent node (capability execution)."""
    capability_id: str
    target_node: Any  # Neo4j node dict
    args: dict[str, Any] = field(default_factory=dict)
    rule_id: str = ""


@dataclass
class LogAction:
    """A structured log entry from a rule."""
    message: str
    level: str = "info"
    rule_id: str = ""


@dataclass
class AlertAction:
    """An alert notification from a rule."""
    message: str
    target_node: Any = None
    rule_id: str = ""


# ──────────────────────────────────────────────
# RuleContext — runtime environment
# ──────────────────────────────────────────────

class RuleContext:
    """Runtime context passed to compiled GSL-Ops rule functions.

    Provides:
    - Neo4j graph queries (read-only)
    - Property access
    - Intent emission (property changes, capability execution, logs, alerts)
    - Time functions
    - Schedule evaluation
    """

    def __init__(
        self,
        neo4j_session,
        rule_id: str,
        priority: int = 100,
        now: datetime | None = None,
        last_run: datetime | None = None,
    ):
        self.session = neo4j_session
        self.rule_id = rule_id
        self.priority = priority
        self._now = now or datetime.now(timezone.utc)
        self._last_run = last_run

        # Accumulated outputs
        self.property_intents: list[RuleIntent] = []
        self.submit_intents: list[SubmitIntentAction] = []
        self.logs: list[LogAction] = []
        self.alerts: list[AlertAction] = []

    # ── Graph Queries ──

    def match(self, cypher_pattern: str, **params) -> list[dict[str, Any]]:
        """Execute a Cypher MATCH and return rows as dicts.

        Uses execute_read() to enforce read-only transactions at the
        Neo4j protocol level, preventing any Cypher injection that
        attempts write operations (CREATE, DELETE, SET, etc.).
        """
        query = f"MATCH {cypher_pattern} RETURN *"

        def _read_tx(tx):
            result = tx.run(query, **params)
            return [dict(record) for record in result]

        return self.session.execute_read(_read_tx)

    # ── Property Access ──

    def prop(self, node: Any, name: str) -> Any:
        """Access a property on a Neo4j node."""
        if isinstance(node, dict):
            return node.get(name)
        if hasattr(node, "__getitem__"):
            try:
                return node[name]
            except (KeyError, IndexError):
                return None
        return getattr(node, name, None)

    def node_id(self, node: Any) -> str:
        """Extract the node ID for intent tracking.

        Raises ValueError if no stable ID can be determined (prevents
        silent mismatches in the conflict resolver).
        """
        if hasattr(node, "element_id"):
            return node.element_id
        if isinstance(node, dict):
            if "_element_id" in node:
                return node["_element_id"]
            if "id" in node:
                return str(node["id"])
        raise ValueError(
            f"Cannot determine stable node ID for {type(node).__name__}. "
            f"Node must have 'element_id', '_element_id', or 'id'."
        )

    # ── Property Modification Intents ──

    def set_property(self, node: Any, prop: str, value: Any) -> None:
        self.property_intents.append(RuleIntent(
            node_id=self.node_id(node),
            node_var="",
            property=prop,
            op=PropertyOp.SET,
            value=value,
            rule_id=self.rule_id,
            priority=self.priority,
        ))

    def add_property(self, node: Any, prop: str, value: Any) -> None:
        self.property_intents.append(RuleIntent(
            node_id=self.node_id(node),
            node_var="",
            property=prop,
            op=PropertyOp.ADD,
            value=value,
            rule_id=self.rule_id,
            priority=self.priority,
        ))

    def sub_property(self, node: Any, prop: str, value: Any) -> None:
        self.property_intents.append(RuleIntent(
            node_id=self.node_id(node),
            node_var="",
            property=prop,
            op=PropertyOp.SUB,
            value=value,
            rule_id=self.rule_id,
            priority=self.priority,
        ))

    def mul_property(self, node: Any, prop: str, value: Any) -> None:
        self.property_intents.append(RuleIntent(
            node_id=self.node_id(node),
            node_var="",
            property=prop,
            op=PropertyOp.MUL,
            value=value,
            rule_id=self.rule_id,
            priority=self.priority,
        ))

    # ── Actions ──

    def submit_intent(self, capability_id: str, target_node: Any,
                      args: dict[str, Any] | None = None) -> None:
        """Create an Intent for the Daemon to execute a capability."""
        self.submit_intents.append(SubmitIntentAction(
            capability_id=capability_id,
            target_node=target_node,
            args=args or {},
            rule_id=self.rule_id,
        ))

    def log(self, message: str, level: str = "info") -> None:
        """Emit a structured log entry."""
        self.logs.append(LogAction(
            message=message,
            level=level,
            rule_id=self.rule_id,
        ))
        # Also log to Python logger
        py_level = getattr(logging, level.upper(), logging.INFO)
        log.log(py_level, f"[rule:{self.rule_id}] {message}")

    def alert(self, message: str, target_node: Any = None) -> None:
        """Emit an alert notification."""
        self.alerts.append(AlertAction(
            message=message,
            target_node=target_node,
            rule_id=self.rule_id,
        ))
        log.warning(f"[rule:{self.rule_id}] ALERT: {message}")

    # ── Time Functions ──

    def now(self) -> datetime:
        """Current UTC time (injectable for testing)."""
        return self._now

    def duration(self, spec: str) -> timedelta:
        """Parse an ISO 8601 duration or shorthand (5m, 1h, 30s, 1d).

        Supports: PT{n}S, PT{n}M, PT{n}H, P{n}D, and shorthands.
        """
        spec = spec.strip('"').strip("'")

        # Shorthand: 5m, 1h, 30s, 1d
        m = re.match(r'^(\d+(?:\.\d+)?)(s|m|h|d)$', spec)
        if m:
            val = float(m.group(1))
            unit = m.group(2)
            if unit == "s":
                return timedelta(seconds=val)
            if unit == "m":
                return timedelta(minutes=val)
            if unit == "h":
                return timedelta(hours=val)
            if unit == "d":
                return timedelta(days=val)

        # ISO 8601: PT5M, PT1H30M, P1D, P1DT12H
        total_seconds = 0.0
        iso = re.match(
            r'^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$',
            spec
        )
        if iso:
            days = int(iso.group(1) or 0)
            hours = int(iso.group(2) or 0)
            minutes = int(iso.group(3) or 0)
            seconds = float(iso.group(4) or 0)
            total_seconds = days * 86400 + hours * 3600 + minutes * 60 + seconds
            return timedelta(seconds=total_seconds)

        raise ValueError(f"Cannot parse duration: {spec!r}")

    # ── Schedule ──

    def should_run_schedule(self, interval_spec: str) -> bool:
        """Check if enough time has passed since last run for EVERY blocks.

        Args:
            interval_spec: Duration string (ISO 8601 or shorthand)

        Returns:
            True if the rule should fire
        """
        if self._last_run is None:
            return True  # never run before
        interval = self.duration(interval_spec)
        return (self._now - self._last_run) >= interval

    # ── Utility ──

    def clamp(self, value: float, lo: float, hi: float) -> float:
        """Clamp a value between lo and hi."""
        return max(lo, min(hi, value))

    def keys(self, node: Any) -> list[str]:
        """Get property keys of a Neo4j node."""
        if isinstance(node, dict):
            return list(node.keys())
        if hasattr(node, "keys"):
            return list(node.keys())
        return []
```

---
## FILE: src/hassaleh/engine/resolver.py
```
"""Priority-based conflict resolver for GSL-Ops rule intents.

When multiple rules target the same property on the same node,
the resolver determines the final value using these strategies:

1. SET operations: lowest priority number wins (priority 1 beats priority 10)
2. ADD/SUB operations: all are applied additively (commutative)
3. MUL operations: all are applied multiplicatively (commutative)
4. Mixed: SET is applied first, then ADD/SUB, then MUL

Reference: docs/CONCEPT.md v1.2, Section 4.14
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from hassaleh.engine.runtime import PropertyOp, RuleIntent


def resolve_intents(
    intents: list[RuleIntent],
    current_values: dict[tuple[str, str], Any] | None = None,
) -> dict[tuple[str, str], Any]:
    """Resolve conflicting property intents into final values.

    Args:
        intents: List of RuleIntents from all evaluated rules
        current_values: Optional dict of (node_id, property) → current value
                        for ADD/SUB/MUL operations

    Returns:
        Dict of (node_id, property) → resolved value
    """
    if current_values is None:
        current_values = {}

    # Group intents by (node_id, property)
    grouped: dict[tuple[str, str], list[RuleIntent]] = defaultdict(list)
    for intent in intents:
        key = (intent.node_id, intent.property)
        grouped[key].append(intent)

    # Resolve each group
    result: dict[tuple[str, str], Any] = {}

    for key, group in grouped.items():
        # Separate by operation type
        sets = [i for i in group if i.op == PropertyOp.SET]
        adds = [i for i in group if i.op == PropertyOp.ADD]
        subs = [i for i in group if i.op == PropertyOp.SUB]
        muls = [i for i in group if i.op == PropertyOp.MUL]

        # Start with current value or 0
        current = current_values.get(key, 0)

        # 1. SET: lowest priority wins
        if sets:
            winner = min(sets, key=lambda i: i.priority)
            current = winner.value

        # 2. ADD/SUB: all applied (commutative)
        for intent in adds:
            current = _safe_add(current, intent.value)
        for intent in subs:
            current = _safe_add(current, -intent.value)

        # 3. MUL: all applied (commutative)
        for intent in muls:
            current = _safe_mul(current, intent.value)

        result[key] = current

    return result


def _safe_add(a: Any, b: Any) -> Any:
    """Add two values, handling type mismatches gracefully."""
    try:
        return a + b
    except TypeError:
        # If current is not numeric, try converting
        try:
            return float(a) + float(b)
        except (ValueError, TypeError):
            return b  # Fall back to replacement


def _safe_mul(a: Any, b: Any) -> Any:
    """Multiply two values, handling type mismatches gracefully."""
    try:
        return a * b
    except TypeError:
        try:
            return float(a) * float(b)
        except (ValueError, TypeError):
            return a  # Keep original on failure
```

---
## FILE: src/hassaleh/bridge/openclaw.py
```
"""OpenClaw Gateway Bridge — HTTP client for the OpenClaw Gateway API.

Provides async methods for:
- Health checks (gateway status)
- Sending messages via channels (Telegram, etc.)
- Session management (list, spawn, send)
- System events (wake)

The OpenClaw Gateway runs on localhost and requires a bearer token
for authentication.

Reference: OpenClaw docs/gateway/protocol.md
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urljoin

import aiohttp

log = logging.getLogger("hassaleh.bridge.openclaw")


class OpenClawBridge:
    """Async HTTP client for OpenClaw Gateway.

    Usage:
        bridge = OpenClawBridge("http://localhost:18789", "your-token")
        async with bridge:
            health = await bridge.health()
            await bridge.send_message("telegram", "5580211068", "Hello!")
    """

    def __init__(self, gateway_url: str, gateway_token: str):
        self.gateway_url = gateway_url.rstrip("/")
        self.gateway_token = gateway_token
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> OpenClawBridge:
        self._session = aiohttp.ClientSession(
            headers={
                "Authorization": f"Bearer {self.gateway_token}",
                "Content-Type": "application/json",
            },
            timeout=aiohttp.ClientTimeout(total=30),
        )
        return self

    async def __aexit__(self, *exc) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    # ── Health ──

    async def health(self) -> dict[str, Any]:
        """Check OpenClaw Gateway health."""
        try:
            async with self._session.get(f"{self.gateway_url}/health") as resp:
                if resp.status == 200:
                    return await resp.json()
                return {"status": "error", "code": resp.status}
        except Exception as e:
            log.warning(f"OpenClaw health check failed: {e}")
            return {"status": "unreachable", "error": str(e)}

    async def is_healthy(self) -> bool:
        """Quick health check."""
        h = await self.health()
        return h.get("status") == "ok" or "version" in h

    # ── Messaging ──

    async def send_message(
        self,
        channel: str,
        target: str,
        message: str,
        silent: bool = False,
    ) -> dict[str, Any]:
        """Send a message via OpenClaw channel plugin.

        Args:
            channel: Channel name (telegram, discord, etc.)
            target: Target chat ID or username
            message: Message text
            silent: Send without notification sound

        Returns:
            Response dict with messageId, ok, etc.
        """
        payload = {
            "action": "send",
            "channel": channel,
            "target": target,
            "message": message,
        }
        if silent:
            payload["silent"] = True

        return await self._tool_call("message", payload)

    # ── Sessions ──

    async def list_sessions(
        self,
        kinds: list[str] | None = None,
        limit: int = 20,
        active_minutes: int | None = None,
    ) -> dict[str, Any]:
        """List active sessions."""
        payload: dict[str, Any] = {"limit": limit}
        if kinds:
            payload["kinds"] = kinds
        if active_minutes:
            payload["activeMinutes"] = active_minutes

        return await self._tool_call("sessions_list", payload)

    async def spawn_session(
        self,
        task: str,
        label: str | None = None,
        runtime: str = "subagent",
        model: str | None = None,
        timeout_seconds: int = 0,
    ) -> dict[str, Any]:
        """Spawn an isolated sub-agent session.

        Args:
            task: Task description for the sub-agent
            label: Optional label for logs/UI
            runtime: "subagent" or "acp"
            model: Optional model override
            timeout_seconds: Run timeout (0 = no timeout)

        Returns:
            Response with runId, childSessionKey, status
        """
        payload: dict[str, Any] = {
            "task": task,
            "runtime": runtime,
        }
        if label:
            payload["label"] = label
        if model:
            payload["model"] = model
        if timeout_seconds:
            payload["runTimeoutSeconds"] = timeout_seconds

        return await self._tool_call("sessions_spawn", payload)

    async def send_to_session(
        self,
        session_key: str,
        message: str,
        timeout_seconds: int = 30,
    ) -> dict[str, Any]:
        """Send a message to another session."""
        return await self._tool_call("sessions_send", {
            "sessionKey": session_key,
            "message": message,
            "timeoutSeconds": timeout_seconds,
        })

    # ── System Events ──

    async def wake(self, text: str, mode: str = "now") -> dict[str, Any]:
        """Send a wake/system event."""
        return await self._tool_call("cron", {
            "action": "wake",
            "text": text,
            "mode": mode,
        })

    # ── Internal ──

    async def _tool_call(self, tool: str, params: dict[str, Any]) -> dict[str, Any]:
        """Execute a tool call via the OpenClaw Gateway API.

        Uses the gateway's tool invocation endpoint.
        """
        url = f"{self.gateway_url}/api/tools/{tool}"

        try:
            async with self._session.post(url, json=params) as resp:
                result = await resp.json()
                if resp.status != 200:
                    log.warning(f"Tool {tool} returned {resp.status}: {result}")
                return result
        except aiohttp.ClientError as e:
            log.error(f"Tool call {tool} failed: {e}")
            return {"ok": False, "error": str(e)}
        except Exception as e:
            log.error(f"Tool call {tool} unexpected error: {e}")
            return {"ok": False, "error": str(e)}
```

---
## FILE: src/hassaleh/bridge/notifications.py
```
"""Notification dispatcher — Routes rule ALERT actions to messaging channels.

Dispatches alerts from GSL-Ops rules through the OpenClaw bridge
to Telegram, Discord, or other configured channels.

Usage:
    dispatcher = NotificationDispatcher(bridge, default_channel, default_target)
    await dispatcher.dispatch_alert(alert_action)
"""

from __future__ import annotations

import logging
from typing import Any

from hassaleh.bridge.openclaw import OpenClawBridge
from hassaleh.engine.runtime import AlertAction, LogAction

log = logging.getLogger("hassaleh.bridge.notifications")


class NotificationDispatcher:
    """Routes rule outputs to messaging channels via OpenClaw.

    Dispatches:
    - ALERT actions → Telegram/Discord messages
    - LOG actions at "error" level → optional error notifications
    """

    def __init__(
        self,
        bridge: OpenClawBridge,
        default_channel: str = "telegram",
        default_target: str = "",
        notify_on_error_logs: bool = True,
    ):
        self.bridge = bridge
        self.default_channel = default_channel
        self.default_target = default_target
        self.notify_on_error_logs = notify_on_error_logs

    async def dispatch_alert(self, alert: AlertAction) -> bool:
        """Send an alert notification via OpenClaw messaging.

        Args:
            alert: AlertAction from a GSL-Ops rule

        Returns:
            True if sent successfully
        """
        if not self.default_target:
            log.warning(f"No notification target configured, dropping alert: {alert.message}")
            return False

        # Format the alert message
        target_info = ""
        if alert.target_node and isinstance(alert.target_node, dict):
            target_name = alert.target_node.get("name", alert.target_node.get("id", "?"))
            target_info = f" [{target_name}]"

        message = f"⚠️ **Hassaleh Alert**{target_info}\n\n{alert.message}\n\n_Rule: {alert.rule_id}_"

        try:
            result = await self.bridge.send_message(
                channel=self.default_channel,
                target=self.default_target,
                message=message,
            )
            if result.get("ok"):
                log.info(f"Alert dispatched: {alert.message[:50]}")
                return True
            else:
                log.warning(f"Alert dispatch failed: {result}")
                return False
        except Exception as e:
            log.error(f"Alert dispatch error: {e}")
            return False

    async def dispatch_error_log(self, log_action: LogAction) -> bool:
        """Send error-level log as notification (if enabled).

        Args:
            log_action: LogAction with level="error"

        Returns:
            True if sent
        """
        if not self.notify_on_error_logs:
            return False
        if log_action.level != "error":
            return False
        if not self.default_target:
            return False

        message = f"🔴 **Hassaleh Error Log**\n\n{log_action.message}\n\n_Rule: {log_action.rule_id}_"

        try:
            result = await self.bridge.send_message(
                channel=self.default_channel,
                target=self.default_target,
                message=message,
                silent=True,  # Don't buzz for log-level errors
            )
            return result.get("ok", False)
        except Exception as e:
            log.error(f"Error log dispatch failed: {e}")
            return False

    async def dispatch_all(
        self,
        alerts: list[AlertAction],
        logs: list[LogAction] | None = None,
    ) -> int:
        """Dispatch all pending alerts and error logs.

        Returns:
            Number of successfully dispatched notifications
        """
        sent = 0

        for alert in alerts:
            if await self.dispatch_alert(alert):
                sent += 1

        if logs and self.notify_on_error_logs:
            for log_action in logs:
                if log_action.level == "error":
                    if await self.dispatch_error_log(log_action):
                        sent += 1

        return sent
```

---
## FILE: src/hassaleh/daemon.py
```
#!/usr/bin/env python3.13
"""Hassaleh Daemon — Persistent async service for agent orchestration.

Runs as a systemd-managed service (hassaleh-svc user).
Processes agent Intents, enforces Capability permissions,
delegates system actions to per-capability OS users.

Reference: docs/CONCEPT.md v1.2, Section 3.2
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import socket
import sys
from datetime import datetime, timezone
from typing import Any

from aiohttp import web
from neo4j import AsyncGraphDatabase, GraphDatabase

from hassaleh.engine.compiler import compile_rule, COMPILER_VERSION
from hassaleh.engine.runtime import RuleContext
from hassaleh.engine.resolver import resolve_intents
from hassaleh.bridge.openclaw import OpenClawBridge
from hassaleh.bridge.notifications import NotificationDispatcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [hassaleh-daemon] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("hassaleh.daemon")

# ──────────────────────────────────────────────
# Configuration (loaded from Neo4j on boot)
# ──────────────────────────────────────────────

DEFAULT_CONFIG = {
    "tick_interval_ms": 1000,
    "intent_timeout_default_sec": 300,
    "max_concurrent_actions": 10,
    "health_endpoint_port": 9100,
    "sweep_interval_min": 15,
    "rule_eval_interval_sec": 60,  # Evaluate rules every 60s (separate from sweep)
    "circuit_breaker_decay_per_sweep": 1,
}


# ──────────────────────────────────────────────
# Daemon
# ──────────────────────────────────────────────

class HassalehDaemon:
    """Main Daemon process — async event loop."""

    def __init__(self, neo4j_uri: str, neo4j_user: str, neo4j_password: str):
        self.neo4j_uri = neo4j_uri
        self.neo4j_user = neo4j_user
        self.neo4j_password = neo4j_password
        self.driver: Any = None
        self.sync_driver: Any = None  # Sync driver for rule evaluation
        self.config: dict = dict(DEFAULT_CONFIG)
        self.running = False
        self.tick_count = 0
        self.active_workers: dict[str, asyncio.Task] = {}
        self._shutdown_event = asyncio.Event()
        self._start_time: float = 0
        self._health_app: web.Application | None = None
        self._health_runner: web.AppRunner | None = None
        self._watchdog_usec: int = 0  # systemd watchdog interval (0 = disabled)
        self._compiled_rules: dict[str, Any] = {}  # rule_id → compiled function
        self._rule_last_run: dict[str, datetime] = {}  # rule_id → last execution time
        self._bridge: OpenClawBridge | None = None  # OpenClaw bridge (optional)
        self._notifier: NotificationDispatcher | None = None
        self._pending_alerts: list = []  # Alerts from rule evaluation (dispatched async)
        self._pending_error_logs: list = []

    # ── Lifecycle ──

    async def start(self) -> None:
        """Boot the Daemon: connect to Neo4j, load config, recover zombies, run loop."""
        log.info("Starting Hassaleh Daemon...")

        # Connect to Neo4j
        self.driver = AsyncGraphDatabase.driver(
            self.neo4j_uri,
            auth=(self.neo4j_user, self.neo4j_password),
        )
        log.info(f"Connected to Neo4j at {self.neo4j_uri}")

        # Verify connectivity
        async with self.driver.session() as session:
            result = await session.run("RETURN 1 AS ping")
            record = await result.single()
            assert record["ping"] == 1
        log.info("Neo4j connection verified")

        # Load configuration from graph
        await self._load_config()

        # Check schema version
        await self._check_schema_version()

        # Create sync driver for rule evaluation (rules use sync Neo4j)
        self.sync_driver = GraphDatabase.driver(
            self.neo4j_uri,
            auth=(self.neo4j_user, self.neo4j_password),
        )
        log.info("Sync Neo4j driver created for rule evaluation")

        # Load and compile rules
        await self._load_rules()

        # Recover zombie Intents
        await self._recover_zombies()

        # Start health endpoint
        await self._start_health_endpoint()

        # Initialize OpenClaw bridge (optional — works without it)
        await self._init_bridge()

        # Initialize watchdog
        self._init_watchdog()

        # Start the main loop
        self.running = True
        self._start_time = asyncio.get_event_loop().time()
        self._sd_notify("READY=1")
        log.info(f"Daemon running (tick interval: {self.config['tick_interval_ms']}ms)")

        await self._main_loop()

    async def shutdown(self) -> None:
        """Graceful shutdown: stop accepting, kill workers, update Intents."""
        log.info("Initiating graceful shutdown...")
        self.running = False
        self._sd_notify("STOPPING=1")

        # Cancel all active workers
        if self.active_workers:
            log.info(f"Terminating {len(self.active_workers)} active workers...")
            for intent_id, task in self.active_workers.items():
                task.cancel()
            # Wait for cancellation (up to 30s)
            await asyncio.gather(*self.active_workers.values(), return_exceptions=True)

            # Mark remaining running Intents as failed
            async with self.driver.session() as session:
                await session.run("""
                    MATCH (i:Intent {lifecycle: 'running'})
                    SET i.lifecycle = 'failed',
                        i.error_reason = 'Daemon shutdown',
                        i.completed_at = datetime({timezone: 'UTC'})
                """)
            log.info("Running Intents marked as failed")

        # Stop health endpoint
        await self._stop_health_endpoint()

        # Close OpenClaw bridge
        if self._bridge:
            await self._bridge.__aexit__(None, None, None)

        # Close Neo4j
        if self.sync_driver:
            self.sync_driver.close()
        if self.driver:
            await self.driver.close()
            log.info("Neo4j connection closed")

        log.info("Daemon shutdown complete")

    # ── Main Loop ──

    async def _main_loop(self) -> None:
        """1-second tick loop: process Intents, evaluate rules."""
        tick_interval = self.config["tick_interval_ms"] / 1000.0
        sweep_counter = 0
        sweep_interval_ticks = int(self.config["sweep_interval_min"] * 60 / tick_interval)
        rule_eval_counter = 0
        rule_eval_interval_ticks = int(self.config["rule_eval_interval_sec"] / tick_interval)

        while self.running:
            tick_start = asyncio.get_event_loop().time()
            self.tick_count += 1

            try:
                # Watchdog ping
                self._sd_notify("WATCHDOG=1")

                # Hot path: process pending Intents
                await self._process_pending_intents()

                # Clean up finished workers
                self._reap_workers()

                # Rule evaluation (configurable interval, default 60s)
                rule_eval_counter += 1
                if rule_eval_counter >= rule_eval_interval_ticks and self._compiled_rules:
                    await asyncio.get_event_loop().run_in_executor(
                        None, self._evaluate_rules
                    )
                    rule_eval_counter = 0

                    # Dispatch accumulated alerts/error logs via OpenClaw
                    if self._notifier and (self._pending_alerts or self._pending_error_logs):
                        try:
                            sent = await self._notifier.dispatch_all(
                                self._pending_alerts, self._pending_error_logs
                            )
                            if sent:
                                log.info(f"Dispatched {sent} notification(s)")
                        except Exception as e:
                            log.error(f"Notification dispatch failed: {e}")
                        self._pending_alerts.clear()
                        self._pending_error_logs.clear()

                # Periodic sweep
                sweep_counter += 1
                if sweep_counter >= sweep_interval_ticks:
                    await self._sweep()
                    sweep_counter = 0

            except Exception as e:
                log.error(f"Tick {self.tick_count} error: {e}", exc_info=True)

            # Sleep for remainder of tick
            elapsed = asyncio.get_event_loop().time() - tick_start
            sleep_time = max(0, tick_interval - elapsed)
            if sleep_time > 0:
                try:
                    await asyncio.wait_for(
                        self._shutdown_event.wait(),
                        timeout=sleep_time,
                    )
                    # Shutdown was signaled — initiate graceful shutdown
                    await self.shutdown()
                    return
                except asyncio.TimeoutError:
                    pass  # normal tick sleep expired

    # ── Intent Processing ──

    async def _process_pending_intents(self) -> None:
        """Find and process all pending Intents.

        Uses atomic claim: a single transaction MATCHes pending Intents
        and SETs them to 'claimed' so no other Daemon instance (or fast
        restart) can pick up the same Intent.  After claim, permission
        checks run; if they fail the Intent is rejected/parked, otherwise
        a worker is spawned and the Intent transitions to 'running'.
        """
        max_workers = self.config["max_concurrent_actions"]
        available_slots = max_workers - len(self.active_workers)
        if available_slots <= 0:
            return

        # ── Atomic claim: pending → claimed in one write transaction ──
        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (agent:Agent)-[:PROPOSED]->(i:Intent {lifecycle: 'pending'})
                WITH i, agent
                ORDER BY i.submitted_at ASC
                LIMIT $limit
                SET i.lifecycle = 'claimed'
                RETURN i, agent
            """, limit=available_slots)

            records = [record async for record in result]

        for record in records:
            intent = record["i"]
            agent = record["agent"]
            intent_id = intent["id"]

            # Check capability permission
            if intent["action"] == "execute_capability":
                has_cap = await self._check_capability(agent["id"], intent)
                if not has_cap:
                    await self._reject_intent(intent_id, "Agent lacks required capability")
                    continue

                # Check if capability requires confirmation
                needs_approval = await self._needs_approval(intent)
                if needs_approval:
                    await self._park_intent(intent_id)
                    continue

            # Spawn async worker
            task = asyncio.create_task(
                self._execute_intent(intent_id, intent, agent),
                name=f"intent-{intent_id}",
            )
            self.active_workers[intent_id] = task
            log.info(f"Spawned worker for Intent {intent_id} (action: {intent['action']})")

    async def _execute_intent(self, intent_id: str, intent: dict, agent: dict) -> None:
        """Execute a single Intent asynchronously."""
        try:
            # Mark as running
            async with self.driver.session() as session:
                await session.run("""
                    MATCH (i:Intent {id: $id})
                    SET i.lifecycle = 'running',
                        i.started_at = datetime({timezone: 'UTC'})
                """, id=intent_id)

            action = intent["action"]

            if action == "execute_capability":
                await self._execute_capability(intent_id, intent)
            elif action == "update_property":
                await self._execute_update(intent_id, intent)
            else:
                await self._reject_intent(intent_id, f"Unknown action: {action}")
                return

        except asyncio.CancelledError:
            log.warning(f"Intent {intent_id} cancelled (shutdown)")
            raise
        except Exception as e:
            log.error(f"Intent {intent_id} failed: {e}", exc_info=True)
            await self._fail_intent(intent_id, str(e))

    async def _execute_capability(self, intent_id: str, intent: dict) -> None:
        """Execute a capability via async subprocess."""
        # Look up the capability's invoke_command and exec_as_user
        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (i:Intent {id: $id})-[:TARGETS]->(cap:Capability)
                RETURN cap.invoke_command AS command, cap.exec_as_user AS exec_user
            """, id=intent_id)
            record = await result.single()

        if not record:
            await self._fail_intent(intent_id, "No capability linked to Intent")
            return

        command = record["command"]
        exec_user = record["exec_user"]

        # Parse additional args from Intent value
        args = []
        if intent.get("value"):
            try:
                parsed = json.loads(intent["value"])
                if isinstance(parsed, dict) and "args" in parsed:
                    args = parsed["args"].split() if isinstance(parsed["args"], str) else parsed["args"]
            except (json.JSONDecodeError, TypeError):
                args = str(intent["value"]).split()

        # Build safe command array
        cmd = ["sudo", "-n", "-u", exec_user, command] + args

        log.info(f"Executing: {' '.join(cmd)}")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.config["intent_timeout_default_sec"],
            )

            stdout_str = stdout.decode("utf-8", errors="replace") if stdout else ""
            stderr_str = stderr.decode("utf-8", errors="replace") if stderr else ""

            async with self.driver.session() as session:
                await session.run("""
                    MATCH (i:Intent {id: $id})
                    SET i.lifecycle = $lifecycle,
                        i.stdout = $stdout,
                        i.stderr = $stderr,
                        i.exit_code = $exit_code,
                        i.completed_at = datetime({timezone: 'UTC'})
                """,
                    id=intent_id,
                    lifecycle="success" if proc.returncode == 0 else "failed",
                    stdout=stdout_str[:100000],  # cap at 100KB
                    stderr=stderr_str[:100000],
                    exit_code=proc.returncode,
                )

            log.info(f"Intent {intent_id}: exit={proc.returncode}, stdout={len(stdout_str)} bytes")

        except asyncio.TimeoutError:
            # Kill the orphaned subprocess
            try:
                proc.kill()
                await proc.wait()
            except (ProcessLookupError, OSError):
                pass
            await self._fail_intent(intent_id, "Execution timed out")

    async def _execute_update(self, intent_id: str, intent: dict) -> None:
        """Execute a property update on the target node."""
        prop = intent.get("property")
        value = intent.get("value")

        if not prop:
            await self._fail_intent(intent_id, "Missing property name")
            return

        async with self.driver.session() as session:
            await session.run("""
                MATCH (i:Intent {id: $id})-[:TARGETS]->(target)
                SET target[$prop] = $value
            """, id=intent_id, prop=prop, value=value)

            await session.run("""
                MATCH (i:Intent {id: $id})
                SET i.lifecycle = 'success',
                    i.completed_at = datetime({timezone: 'UTC'})
            """, id=intent_id)

        log.info(f"Intent {intent_id}: updated property '{prop}'")

    # ── Permission Checks ──

    async def _check_capability(self, agent_id: str, intent: dict) -> bool:
        """Verify the agent has the required capability."""
        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (a:Agent {id: $agent_id})-[:HAS_CAPABILITY]->(cap:Capability)
                MATCH (i:Intent {id: $intent_id})-[:TARGETS]->(cap)
                RETURN count(*) AS has_cap
            """, agent_id=agent_id, intent_id=intent["id"])
            record = await result.single()
            return record["has_cap"] > 0

    async def _needs_approval(self, intent: dict) -> bool:
        """Check if the targeted capability requires human confirmation."""
        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (i:Intent {id: $id})-[:TARGETS]->(cap:Capability)
                RETURN cap.requires_confirmation AS needs
            """, id=intent["id"])
            record = await result.single()
            return bool(record and record["needs"])

    # ── Intent State Transitions ──

    async def _reject_intent(self, intent_id: str, reason: str) -> None:
        async with self.driver.session() as session:
            await session.run("""
                MATCH (i:Intent {id: $id})
                SET i.lifecycle = 'rejected',
                    i.error_reason = $reason,
                    i.completed_at = datetime({timezone: 'UTC'})
            """, id=intent_id, reason=reason)
        log.warning(f"Intent {intent_id} rejected: {reason}")

    async def _fail_intent(self, intent_id: str, reason: str) -> None:
        async with self.driver.session() as session:
            await session.run("""
                MATCH (i:Intent {id: $id})
                SET i.lifecycle = 'failed',
                    i.error_reason = $reason,
                    i.completed_at = datetime({timezone: 'UTC'})
            """, id=intent_id, reason=reason)
        log.error(f"Intent {intent_id} failed: {reason}")

    async def _park_intent(self, intent_id: str) -> None:
        async with self.driver.session() as session:
            await session.run("""
                MATCH (i:Intent {id: $id})
                SET i.lifecycle = 'awaiting_approval'
            """, id=intent_id)
        log.info(f"Intent {intent_id} parked for human approval")

    # ── Zombie Recovery ──

    async def _recover_zombies(self) -> None:
        """On boot: transition stale 'running' or 'claimed' Intents to 'failed'."""
        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (i:Intent)
                WHERE i.lifecycle IN ['running', 'claimed']
                SET i.lifecycle = 'failed',
                    i.error_reason = 'Daemon restarted during execution',
                    i.completed_at = datetime({timezone: 'UTC'})
                RETURN count(*) AS recovered
            """)
            record = await result.single()
            count = record["recovered"]
            if count > 0:
                log.warning(f"Recovered {count} zombie Intent(s)")

    # ── Sweep ──

    async def _sweep(self) -> None:
        """Periodic maintenance: decay circuit breakers, evaluate rules."""
        decay = self.config.get("circuit_breaker_decay_per_sweep", 1)
        async with self.driver.session() as session:
            # Decay circuit breaker counters
            await session.run("""
                MATCH (a:Agent)
                WHERE a.restart_count_1h > 0
                SET a.restart_count_1h = CASE
                    WHEN a.restart_count_1h - $decay < 0 THEN 0
                    ELSE a.restart_count_1h - $decay
                END
            """, decay=decay)

        log.info(f"Sweep complete (tick {self.tick_count})")

    # ── Worker Management ──

    def _reap_workers(self) -> None:
        """Remove finished workers from active set."""
        done = [k for k, t in self.active_workers.items() if t.done()]
        for k in done:
            task = self.active_workers.pop(k)
            if task.exception():
                log.error(f"Worker {k} raised: {task.exception()}")

    # ── OpenClaw Bridge ──

    async def _init_bridge(self) -> None:
        """Initialize the OpenClaw Gateway bridge (optional).

        Reads gateway URL and token from DaemonConfig or env vars.
        If not configured, the Daemon works without OpenClaw integration.
        """
        gateway_url = os.environ.get(
            "OPENCLAW_GATEWAY_URL",
            self.config.get("openclaw_gateway_url", ""),
        )
        gateway_token = os.environ.get(
            "OPENCLAW_GATEWAY_TOKEN",
            self.config.get("openclaw_gateway_token", ""),
        )
        notify_target = os.environ.get(
            "HASSALEH_NOTIFY_TARGET",
            self.config.get("notify_target", ""),
        )
        notify_channel = os.environ.get(
            "HASSALEH_NOTIFY_CHANNEL",
            self.config.get("notify_channel", "telegram"),
        )

        if not gateway_url or not gateway_token:
            log.info("OpenClaw bridge not configured (set OPENCLAW_GATEWAY_URL + OPENCLAW_GATEWAY_TOKEN)")
            return

        self._bridge = OpenClawBridge(gateway_url, gateway_token)
        await self._bridge.__aenter__()

        # Test connectivity
        if await self._bridge.is_healthy():
            log.info(f"OpenClaw bridge connected: {gateway_url}")
        else:
            log.warning(f"OpenClaw bridge configured but unreachable: {gateway_url}")

        # Set up notification dispatcher
        if notify_target:
            self._notifier = NotificationDispatcher(
                bridge=self._bridge,
                default_channel=notify_channel,
                default_target=notify_target,
            )
            log.info(f"Notifications → {notify_channel}:{notify_target}")
        else:
            log.info("Notification target not configured (set HASSALEH_NOTIFY_TARGET)")

    # ── Rule Engine ──

    async def _load_rules(self) -> None:
        """Load and compile all available Rule nodes from the graph."""
        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (r:Rule {lifecycle: 'available'})
                RETURN r.id AS id, r.rule_text AS rule_text,
                       r.compiled_python AS compiled_python,
                       r.compiler_version AS compiler_version,
                       r.priority AS priority
            """)
            rules = [dict(record) async for record in result]

        if not rules:
            log.info("No rules found in graph")
            return

        compiled_count = 0
        for rule in rules:
            rule_id = rule["id"]
            rule_text = rule.get("rule_text")
            cached_python = rule.get("compiled_python")
            cached_version = rule.get("compiler_version")

            if not rule_text:
                log.warning(f"Rule {rule_id} has no rule_text, skipping")
                continue

            # Use cached compilation if version matches
            if cached_python and cached_version == COMPILER_VERSION:
                python_source = cached_python
                log.info(f"Rule {rule_id}: using cached compilation")
            else:
                # Compile from source
                try:
                    python_source = compile_rule(rule_text, rule_id=rule_id)
                except Exception as e:
                    log.error(f"Rule {rule_id}: compilation failed: {e}")
                    continue

                # Cache the compiled Python back to the graph
                async with self.driver.session() as session:
                    await session.run("""
                        MATCH (r:Rule {id: $id})
                        SET r.compiled_python = $python,
                            r.compiled_at = datetime({timezone: 'UTC'}),
                            r.compiler_version = $version
                    """, id=rule_id, python=python_source, version=COMPILER_VERSION)
                log.info(f"Rule {rule_id}: compiled and cached (v{COMPILER_VERSION})")

            # Compile to Python code object with restricted namespace
            try:
                code = compile(python_source, f"<rule:{rule_id}>", "exec")
                # Restrict builtins to safe subset (no open, exec, eval)
                # __import__ is needed for the compiled rule's import statement
                safe_builtins = {
                    "__import__": __import__,  # Required for 'from hassaleh.engine.runtime import ...'
                    "True": True, "False": False, "None": None,
                    "abs": abs, "min": min, "max": max, "len": len,
                    "int": int, "float": float, "str": str, "bool": bool,
                    "round": round, "isinstance": isinstance,
                    "range": range, "enumerate": enumerate,
                    "print": print,  # for debugging
                }
                ns: dict[str, Any] = {"__builtins__": safe_builtins}
                exec(code, ns)
                self._compiled_rules[rule_id] = {
                    "func": ns["evaluate"],
                    "priority": rule.get("priority", 100),
                }
                compiled_count += 1
            except Exception as e:
                log.error(f"Rule {rule_id}: exec failed: {e}")

        log.info(f"Loaded {compiled_count}/{len(rules)} rules")

    def _evaluate_rules(self) -> None:
        """Evaluate all compiled rules (runs in thread executor).

        Uses a sync Neo4j session since rules call ctx.match() synchronously.
        """
        now = datetime.now(timezone.utc)
        all_property_intents = []

        with self.sync_driver.session() as session:
            for rule_id, rule_data in self._compiled_rules.items():
                try:
                    ctx = RuleContext(
                        neo4j_session=session,
                        rule_id=rule_id,
                        priority=rule_data.get("priority", 100),
                        now=now,
                        last_run=self._rule_last_run.get(rule_id),
                    )

                    rule_data["func"](ctx)

                    # Collect outputs
                    all_property_intents.extend(ctx.property_intents)

                    # Process submit_intent actions (create Intent nodes)
                    for si in ctx.submit_intents:
                        self._create_rule_intent(session, si, rule_id)

                    # Accumulate alerts and error logs for async dispatch
                    self._pending_alerts.extend(ctx.alerts)
                    for l in ctx.logs:
                        if l.level == "error":
                            self._pending_error_logs.append(l)

                    # Update last run time
                    self._rule_last_run[rule_id] = now

                except Exception as e:
                    log.error(f"Rule {rule_id} evaluation failed: {e}", exc_info=True)

        # Resolve conflicting property intents
        if all_property_intents:
            # Fetch current values from the graph for ADD/SUB/MUL operations
            current_values = self._fetch_current_values(session, all_property_intents)
            self._apply_resolved_intents(all_property_intents, current_values)

    def _create_rule_intent(self, session, submit_action, rule_id: str) -> None:
        """Create an Intent node from a rule's SUBMIT_INTENT action.

        Creates the Intent node, links it to the target Agent via [:PROPOSED]
        (required for the Daemon's _process_pending_intents query), and
        links it to the Capability via [:TARGETS].
        """
        import uuid
        intent_id = str(uuid.uuid4())

        try:
            target_id = None
            if isinstance(submit_action.target_node, dict):
                target_id = submit_action.target_node.get("id")

            args_json = json.dumps(submit_action.args) if submit_action.args else None

            # Create Intent + PROPOSED link to the target agent in one query
            if target_id:
                session.run("""
                    MATCH (agent:Agent {id: $target_id})
                    CREATE (agent)-[:PROPOSED]->(i:Intent {
                        id: $intent_id,
                        submitted_at: datetime({timezone: 'UTC'}),
                        action: 'execute_capability',
                        value: $args,
                        lifecycle: 'pending',
                        source: 'rule',
                        source_rule: $rule_id
                    })
                """, intent_id=intent_id, args=args_json,
                   rule_id=rule_id, target_id=target_id)
            else:
                # No target — create orphaned Intent (will need manual linking)
                session.run("""
                    CREATE (i:Intent {
                        id: $intent_id,
                        submitted_at: datetime({timezone: 'UTC'}),
                        action: 'execute_capability',
                        value: $args,
                        lifecycle: 'pending',
                        source: 'rule',
                        source_rule: $rule_id
                    })
                """, intent_id=intent_id, args=args_json, rule_id=rule_id)

            # Link to capability via TARGETS
            if submit_action.capability_id:
                session.run("""
                    MATCH (i:Intent {id: $intent_id})
                    MATCH (cap:Capability {id: $cap_id})
                    CREATE (i)-[:TARGETS]->(cap)
                """, intent_id=intent_id, cap_id=submit_action.capability_id)

            log.info(f"Rule {rule_id} created Intent {intent_id} "
                     f"(capability: {submit_action.capability_id}, "
                     f"target: {target_id})")

        except Exception as e:
            log.error(f"Failed to create rule Intent: {e}")

    def _fetch_current_values(self, session, intents) -> dict:
        """Fetch current property values from the graph for resolver base values."""
        current = {}
        # Collect unique (node_id, property) pairs
        keys = set()
        for intent in intents:
            keys.add((intent.node_id, intent.property))

        for node_id, prop in keys:
            try:
                if not prop.isidentifier():
                    continue
                if node_id.startswith("4:"):
                    result = session.run(
                        f"MATCH (n) WHERE elementId(n) = $nid RETURN n.{prop} AS val",
                        nid=node_id,
                    )
                else:
                    result = session.run(
                        f"MATCH (n {{id: $nid}}) RETURN n.{prop} AS val",
                        nid=node_id,
                    )
                record = result.single()
                if record and record["val"] is not None:
                    current[(node_id, prop)] = record["val"]
            except Exception as e:
                log.warning(f"Could not fetch current value for {node_id}.{prop}: {e}")

        return current

    def _apply_resolved_intents(self, intents, current_values=None) -> None:
        """Apply resolved property intents to the graph.

        Note: Neo4j does not support parameterized property keys in SET,
        so we use f-string for the property name. The property name comes
        from compiled GSL-Ops rules (validated by Lark grammar, only
        PROP_ACCESS pattern: /[a-zA-Z_]\\w*\\.[a-zA-Z_]\\w*/).
        """
        resolved = resolve_intents(intents, current_values or {})

        with self.sync_driver.session() as session:
            for (node_id, prop), value in resolved.items():
                try:
                    # Validate prop name (defense-in-depth)
                    if not prop.isidentifier():
                        log.error(f"Invalid property name '{prop}', skipping")
                        continue

                    # Use element_id if available, otherwise match by id property
                    if node_id.startswith("4:"):  # Neo4j element_id format
                        session.run(
                            f"MATCH (n) WHERE elementId(n) = $nid SET n.{prop} = $value",
                            nid=node_id, value=value,
                        )
                    else:
                        session.run(
                            f"MATCH (n {{id: $nid}}) SET n.{prop} = $value",
                            nid=node_id, value=value,
                        )
                except Exception as e:
                    log.error(f"Failed to apply property {prop}={value} "
                              f"on node {node_id}: {e}")

        if resolved:
            log.info(f"Applied {len(resolved)} resolved property change(s)")

    # ── Watchdog (sd_notify) ──

    def _init_watchdog(self) -> None:
        """Initialize systemd watchdog if WATCHDOG_USEC is set."""
        usec = os.environ.get("WATCHDOG_USEC")
        if usec:
            self._watchdog_usec = int(usec)
            log.info(f"Watchdog enabled: {self._watchdog_usec / 1_000_000:.1f}s interval")
        else:
            log.info("Watchdog not configured (no WATCHDOG_USEC)")

    def _sd_notify(self, state: str) -> None:
        """Send notification to systemd via NOTIFY_SOCKET.

        Implements the sd_notify protocol without requiring the systemd Python package.
        """
        addr = os.environ.get("NOTIFY_SOCKET")
        if not addr:
            return

        try:
            if addr.startswith("@"):
                addr = "\0" + addr[1:]  # abstract socket

            sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            try:
                sock.sendto(state.encode(), addr)
            finally:
                sock.close()
        except Exception as e:
            log.warning(f"sd_notify failed: {e}")

    # ── Health Endpoint ──

    async def _start_health_endpoint(self) -> None:
        """Start minimal HTTP health endpoint."""
        port = self.config.get("health_endpoint_port", 9100)
        self._health_app = web.Application()
        self._health_app.router.add_get("/health", self._health_handler)

        self._health_runner = web.AppRunner(self._health_app)
        await self._health_runner.setup()
        site = web.TCPSite(self._health_runner, "127.0.0.1", port)
        await site.start()
        log.info(f"Health endpoint listening on http://127.0.0.1:{port}/health")

    async def _health_handler(self, request: web.Request) -> web.Response:
        """Handle GET /health."""
        uptime = asyncio.get_event_loop().time() - self._start_time if self._start_time else 0

        # Count agents and pending intents
        agents_running = 0
        pending_intents = 0
        try:
            async with self.driver.session() as session:
                result = await session.run(
                    "MATCH (a:Agent {lifecycle: 'running'}) RETURN count(a) AS c"
                )
                record = await result.single()
                agents_running = record["c"] if record else 0

                result = await session.run(
                    "MATCH (i:Intent {lifecycle: 'pending'}) RETURN count(i) AS c"
                )
                record = await result.single()
                pending_intents = record["c"] if record else 0
        except Exception as e:
            log.warning(f"Health check DB query failed: {e}")

        data = {
            "status": "healthy" if self.running else "shutting_down",
            "uptime_seconds": round(uptime),
            "tick_count": self.tick_count,
            "pending_intents": pending_intents,
            "active_workers": len(self.active_workers),
            "agents_running": agents_running,
            "rules_loaded": len(self._compiled_rules),
            "openclaw_bridge": "connected" if self._bridge else "not configured",
            "notifications": "enabled" if self._notifier else "disabled",
            "schema_version": "1.2",
        }
        return web.json_response(data)

    async def _stop_health_endpoint(self) -> None:
        """Stop the health endpoint."""
        if self._health_runner:
            await self._health_runner.cleanup()
            log.info("Health endpoint stopped")

    # ── Config Loading ──

    async def _load_config(self) -> None:
        """Load DaemonConfig from Neo4j."""
        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (dc:DaemonConfig {id: 'default'})
                RETURN dc
            """)
            record = await result.single()
            if record:
                node = record["dc"]
                for key in DEFAULT_CONFIG:
                    if key in node:
                        self.config[key] = node[key]
                log.info(f"Config loaded from graph: {self.config}")
            else:
                log.warning("No DaemonConfig found — using defaults")

    async def _check_schema_version(self) -> None:
        """Verify schema version compatibility."""
        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (sv:SystemVersion {id: 'hassaleh'})
                RETURN sv.schema_version AS version,
                       sv.compatible_daemon_versions AS compat
            """)
            record = await result.single()
            if record:
                log.info(f"Schema version: {record['version']}")
            else:
                log.warning("No SystemVersion found — skipping version check")


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

async def main():
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7690")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "hassaleh-dev-2026")

    daemon = HassalehDaemon(uri, user, password)

    # Signal handlers: set the shutdown event (lightweight, no double-shutdown)
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, daemon._shutdown_event.set)

    try:
        await daemon.start()
    except KeyboardInterrupt:
        pass
    finally:
        if daemon.running:
            await daemon.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
```

---
## FILE: src/hassaleh/cli.py
```
#!/usr/bin/env python3.13
"""Hassaleh CLI — Command-line interface for operators and admins.

Usage:
    hassaleh status          Show Daemon health, agents, rules, intents
    hassaleh init            Initialize Neo4j schema + seed data
    hassaleh agent list      List all agents
    hassaleh agent info ID   Show agent details
    hassaleh rule list       List all rules
    hassaleh rule compile ID Force-recompile a rule, show generated Python
    hassaleh intent list     List recent intents
    hassaleh approve ID      Approve an awaiting_approval intent

Reference: docs/CONCEPT.md v1.2
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

# Suppress Neo4j "property does not exist" warnings (noisy but harmless)
logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)

from neo4j import GraphDatabase

from hassaleh.cli_fmt import (
    fmt_table, fmt_header, fmt_ok, fmt_warn, fmt_error,
    fmt_kv, fmt_section, fmt_code,
)


# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────

DEFAULT_URI = "bolt://localhost:7690"
DEFAULT_USER = "neo4j"
DEFAULT_PASSWORD = ""
HEALTH_URL = "http://127.0.0.1:9100/health"
PROJECT_DIR = Path(__file__).parent.parent.parent  # hassaleh project root


def get_connection(args) -> dict:
    """Resolve Neo4j connection params from args → env → config → defaults."""
    uri = args.uri or os.environ.get("NEO4J_URI", DEFAULT_URI)
    user = args.user or os.environ.get("NEO4J_USER", DEFAULT_USER)
    password = args.password or os.environ.get("NEO4J_PASSWORD", DEFAULT_PASSWORD)

    # Try config file
    if not password:
        config_path = Path.home() / ".hassaleh" / "config.toml"
        if config_path.exists():
            try:
                import tomllib
                with open(config_path, "rb") as f:
                    cfg = tomllib.load(f)
                uri = uri or cfg.get("neo4j", {}).get("uri", DEFAULT_URI)
                user = user or cfg.get("neo4j", {}).get("user", DEFAULT_USER)
                password = password or cfg.get("neo4j", {}).get("password", "")
            except Exception:
                pass

    return {"uri": uri, "user": user, "password": password}


def connect(conn: dict):
    """Create a Neo4j driver and verify connectivity."""
    driver = GraphDatabase.driver(conn["uri"], auth=(conn["user"], conn["password"]))
    driver.verify_connectivity()
    return driver


# ──────────────────────────────────────────────
# Commands
# ──────────────────────────────────────────────

def cmd_status(args) -> int:
    """Show Daemon health, agents, rules, pending intents."""
    # 1. Daemon health (via HTTP)
    fmt_header("Hassaleh Status")

    try:
        import urllib.request
        with urllib.request.urlopen(HEALTH_URL, timeout=3) as resp:
            health = json.loads(resp.read())
        print(fmt_section("Daemon"))
        print(fmt_kv("Status", fmt_ok(health["status"])))
        print(fmt_kv("Uptime", f"{health['uptime_seconds']}s"))
        print(fmt_kv("Ticks", str(health["tick_count"])))
        print(fmt_kv("Active Workers", str(health["active_workers"])))
        print(fmt_kv("Schema", health.get("schema_version", "?")))
        print(fmt_kv("Rules loaded", str(health.get("rules_loaded", "?"))))
        print(fmt_kv("OpenClaw Bridge", health.get("openclaw_bridge", "?")))
        print(fmt_kv("Notifications", health.get("notifications", "?")))
    except Exception as e:
        print(fmt_section("Daemon"))
        print(fmt_kv("Status", fmt_error("UNREACHABLE")))
        print(fmt_kv("Error", str(e)))

    # 2. Neo4j connection
    conn = get_connection(args)
    try:
        driver = connect(conn)
    except Exception as e:
        print(fmt_section("Neo4j"))
        print(fmt_kv("Status", fmt_error(f"Connection failed: {e}")))
        return 1

    with driver.session() as session:
        # 3. Agents
        result = session.run("""
            MATCH (a:Agent)
            RETURN a.id AS id, a.name AS name, a.lifecycle AS lifecycle,
                   a.last_heartbeat AS hb
            ORDER BY a.name
        """)
        agents = [dict(r) for r in result]

        print(fmt_section("Agents"))
        if agents:
            rows = [[a["id"], a.get("name", ""), a.get("lifecycle", "?"),
                      str(a.get("hb", "—"))] for a in agents]
            print(fmt_table(["ID", "Name", "Lifecycle", "Last Heartbeat"], rows))
        else:
            print("  No agents found")

        # 4. Rules
        result = session.run("""
            MATCH (r:Rule)
            RETURN r.id AS id, r.name AS name, r.lifecycle AS lifecycle,
                   r.priority AS priority, r.compiler_version AS cv
            ORDER BY r.priority ASC
        """)
        rules = [dict(r) for r in result]

        print(fmt_section("Rules"))
        if rules:
            rows = [[r["id"], r.get("name", ""), r.get("lifecycle", "?"),
                      str(r.get("priority", "?")), r.get("cv", "—")] for r in rules]
            print(fmt_table(["ID", "Name", "Lifecycle", "Priority", "Compiler"], rows))
        else:
            print("  No rules found")

        # 5. Pending Intents
        result = session.run("""
            MATCH (i:Intent)
            WHERE i.lifecycle IN ['pending', 'claimed', 'running', 'awaiting_approval']
            RETURN i.id AS id, i.lifecycle AS lifecycle, i.action AS action,
                   i.submitted_at AS submitted, i.source AS source
            ORDER BY i.submitted_at DESC
            LIMIT 10
        """)
        intents = [dict(r) for r in result]

        print(fmt_section("Active Intents"))
        if intents:
            rows = [[i["id"][:12] + "…", i.get("lifecycle", "?"),
                      i.get("action", "?"), str(i.get("submitted", "?")),
                      i.get("source", "agent")] for i in intents]
            print(fmt_table(["ID", "Lifecycle", "Action", "Submitted", "Source"], rows))
        else:
            print("  No active intents")

    driver.close()
    return 0


def cmd_init(args) -> int:
    """Initialize Neo4j schema + seed data."""
    fmt_header("Hassaleh Init")
    conn = get_connection(args)

    try:
        driver = connect(conn)
    except Exception as e:
        print(fmt_error(f"Connection failed: {e}"))
        return 1

    files = [
        ("Schema", PROJECT_DIR / "schema.cypher"),
        ("Seed Data", PROJECT_DIR / "seed.cypher"),
        ("Seed Rules", PROJECT_DIR / "seed_rules.cypher"),
    ]

    errors = 0
    with driver.session() as session:
        for label, path in files:
            if not path.exists():
                print(fmt_warn(f"{label}: {path} not found, skipping"))
                continue

            cypher = path.read_text()
            # Split on semicolons and execute each statement
            statements = [s.strip() for s in cypher.split(";") if s.strip()]
            file_errors = 0
            for stmt in statements:
                # Skip comments-only blocks
                lines = [l for l in stmt.split("\n") if l.strip() and not l.strip().startswith("//")]
                if not lines:
                    continue
                try:
                    session.run(stmt)
                except Exception as e:
                    print(fmt_warn(f"{label}: {e}"))
                    file_errors += 1

            if file_errors:
                print(fmt_warn(f"{label}: {file_errors} error(s) in {len(statements)} statements"))
                errors += file_errors
            else:
                print(fmt_ok(f"{label}: applied ({len(statements)} statements)"))

    driver.close()
    if errors:
        print(fmt_error(f"Init completed with {errors} error(s)"))
        return 1
    print(fmt_ok("Init complete"))
    return 0


def cmd_agent_list(args) -> int:
    """List all agents."""
    conn = get_connection(args)
    driver = connect(conn)

    with driver.session() as session:
        result = session.run("""
            MATCH (a:Agent)
            OPTIONAL MATCH (a)-[:HAS_CAPABILITY]->(c:Capability)
            RETURN a.id AS id, a.name AS name, a.emoji AS emoji,
                   a.lifecycle AS lifecycle, a.last_heartbeat AS hb,
                   a.runtime AS runtime, count(c) AS caps
            ORDER BY a.name
        """)
        agents = [dict(r) for r in result]

    driver.close()

    if not agents:
        print("No agents found")
        return 0

    fmt_header("Agents")
    rows = [[
        a.get("emoji", "") + " " + a.get("name", a["id"]),
        a["id"], a.get("lifecycle", "?"), str(a["caps"]),
        a.get("runtime", "—"), str(a.get("hb", "—"))
    ] for a in agents]
    print(fmt_table(["Name", "ID", "Lifecycle", "Caps", "Runtime", "Last HB"], rows))
    return 0


def cmd_agent_info(args) -> int:
    """Show detailed agent info."""
    conn = get_connection(args)
    driver = connect(conn)

    with driver.session() as session:
        # Agent details
        result = session.run("""
            MATCH (a:Agent {id: $id})
            RETURN a
        """, id=args.agent_id)
        record = result.single()

        if not record:
            print(fmt_error(f"Agent '{args.agent_id}' not found"))
            driver.close()
            return 1

        agent = dict(record["a"])
        fmt_header(f"Agent: {agent.get('name', agent.get('id'))}")
        for k, v in sorted(agent.items()):
            print(fmt_kv(k, str(v)))

        # Capabilities
        result = session.run("""
            MATCH (a:Agent {id: $id})-[:HAS_CAPABILITY]->(c:Capability)
            RETURN c.id AS id, c.name AS name, c.kind AS kind
            ORDER BY c.name
        """, id=args.agent_id)
        caps = [dict(r) for r in result]

        if caps:
            print(fmt_section("Capabilities"))
            rows = [[c["id"], c.get("name", ""), c.get("kind", "?")] for c in caps]
            print(fmt_table(["ID", "Name", "Kind"], rows))

        # Recent Intents
        result = session.run("""
            MATCH (a:Agent {id: $id})-[:PROPOSED]->(i:Intent)
            RETURN i.id AS id, i.lifecycle AS lifecycle, i.action AS action,
                   i.submitted_at AS submitted
            ORDER BY i.submitted_at DESC
            LIMIT 5
        """, id=args.agent_id)
        intents = [dict(r) for r in result]

        if intents:
            print(fmt_section("Recent Intents"))
            rows = [[i["id"][:12] + "…", i.get("lifecycle", "?"),
                      i.get("action", "?"), str(i.get("submitted", "?"))] for i in intents]
            print(fmt_table(["ID", "Lifecycle", "Action", "Submitted"], rows))

    driver.close()
    return 0


def cmd_rule_list(args) -> int:
    """List all rules."""
    conn = get_connection(args)
    driver = connect(conn)

    with driver.session() as session:
        result = session.run("""
            MATCH (r:Rule)
            RETURN r.id AS id, r.name AS name, r.lifecycle AS lifecycle,
                   r.priority AS priority, r.compiler_version AS cv,
                   r.compiled_at AS compiled_at, r.author AS author
            ORDER BY r.priority ASC
        """)
        rules = [dict(r) for r in result]

    driver.close()

    if not rules:
        print("No rules found")
        return 0

    fmt_header("Rules")
    rows = [[
        r["id"], r.get("name", ""), r.get("lifecycle", "?"),
        str(r.get("priority", "?")), r.get("author", "—"),
        r.get("cv", "—"), str(r.get("compiled_at", "—"))
    ] for r in rules]
    print(fmt_table(["ID", "Name", "Lifecycle", "Priority", "Author", "Compiler", "Compiled"], rows))
    return 0


def cmd_rule_compile(args) -> int:
    """Force-recompile a rule and show generated Python."""
    from hassaleh.engine.compiler import compile_rule, COMPILER_VERSION

    conn = get_connection(args)
    driver = connect(conn)

    with driver.session() as session:
        result = session.run("""
            MATCH (r:Rule {id: $id})
            RETURN r.rule_text AS rule_text, r.name AS name
        """, id=args.rule_id)
        record = result.single()

        if not record:
            print(fmt_error(f"Rule '{args.rule_id}' not found"))
            driver.close()
            return 1

        rule_text = record["rule_text"]
        if not rule_text:
            print(fmt_error(f"Rule '{args.rule_id}' has no rule_text"))
            driver.close()
            return 1

        fmt_header(f"Compiling: {record.get('name', args.rule_id)}")

        print(fmt_section("GSL-Ops Source"))
        print(fmt_code(rule_text))

        try:
            python_source = compile_rule(rule_text, rule_id=args.rule_id)
        except Exception as e:
            print(fmt_error(f"Compilation failed: {e}"))
            driver.close()
            return 1

        print(fmt_section(f"Generated Python (compiler v{COMPILER_VERSION})"))
        print(fmt_code(python_source))

        # Update cache in graph
        if not args.dry_run:
            session.run("""
                MATCH (r:Rule {id: $id})
                SET r.compiled_python = $python,
                    r.compiled_at = datetime({timezone: 'UTC'}),
                    r.compiler_version = $version
            """, id=args.rule_id, python=python_source, version=COMPILER_VERSION)
            print(fmt_ok("Compiled and cached in graph"))
        else:
            print(fmt_warn("Dry run — not saved to graph"))

    driver.close()
    return 0


def cmd_intent_list(args) -> int:
    """List recent intents."""
    conn = get_connection(args)
    driver = connect(conn)

    with driver.session() as session:
        filters = []
        params: dict[str, Any] = {"limit": args.limit or 20}

        if args.lifecycle:
            filters.append("i.lifecycle = $lifecycle")
            params["lifecycle"] = args.lifecycle
        if args.source:
            filters.append("i.source = $source")
            params["source"] = args.source

        where = "WHERE " + " AND ".join(filters) if filters else ""

        result = session.run(f"""
            MATCH (i:Intent)
            {where}
            RETURN i.id AS id, i.lifecycle AS lifecycle, i.action AS action,
                   i.submitted_at AS submitted, i.completed_at AS completed,
                   i.source AS source, i.exit_code AS exit_code,
                   i.error_reason AS error
            ORDER BY i.submitted_at DESC
            LIMIT $limit
        """, **params)
        intents = [dict(r) for r in result]

    driver.close()

    if not intents:
        print("No intents found")
        return 0

    fmt_header("Intents")
    rows = [[
        i["id"][:12] + "…", i.get("lifecycle", "?"), i.get("action", "?"),
        str(i.get("submitted", "?")), i.get("source", "agent"),
        str(i.get("exit_code", "—")), (i.get("error", "") or "")[:40]
    ] for i in intents]
    print(fmt_table(["ID", "Status", "Action", "Submitted", "Source", "Exit", "Error"], rows))
    return 0


def cmd_heartbeat(args) -> int:
    """Send agent heartbeat — updates last_heartbeat + lifecycle in graph."""
    conn = get_connection(args)
    driver = connect(conn)

    with driver.session() as session:
        result = session.run("""
            MATCH (a:Agent {id: $id})
            SET a.last_heartbeat = datetime({timezone: 'UTC'}),
                a.lifecycle = 'running'
            RETURN a.id AS id, a.name AS name
        """, id=args.agent_id)
        record = result.single()

        if not record:
            print(fmt_error(f"Agent '{args.agent_id}' not found"))
            driver.close()
            return 1

        print(fmt_ok(f"Heartbeat: {record.get('name', args.agent_id)} @ UTC now"))

    driver.close()
    return 0


def cmd_approve(args) -> int:
    """Approve an awaiting_approval intent."""
    conn = get_connection(args)
    driver = connect(conn)

    with driver.session() as session:
        result = session.run("""
            MATCH (i:Intent {id: $id})
            RETURN i.lifecycle AS lifecycle
        """, id=args.intent_id)
        record = result.single()

        if not record:
            print(fmt_error(f"Intent '{args.intent_id}' not found"))
            driver.close()
            return 1

        if record["lifecycle"] != "awaiting_approval":
            print(fmt_error(f"Intent is '{record['lifecycle']}', not 'awaiting_approval'"))
            driver.close()
            return 1

        session.run("""
            MATCH (i:Intent {id: $id})
            SET i.lifecycle = 'pending',
                i.approved_at = datetime({timezone: 'UTC'})
        """, id=args.intent_id)

        print(fmt_ok(f"Intent {args.intent_id} approved → pending"))

    driver.close()
    return 0


# ──────────────────────────────────────────────
# Argument Parser
# ──────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hassaleh",
        description="Hassaleh — Graph-native agent orchestration CLI",
    )

    # Global connection flags
    parser.add_argument("--uri", help="Neo4j URI (default: bolt://localhost:7690)")
    parser.add_argument("--user", help="Neo4j user (default: neo4j)")
    parser.add_argument("--password", help="Neo4j password")

    sub = parser.add_subparsers(dest="command", help="Available commands")

    # status
    sub.add_parser("status", help="Show system status")

    # init
    sub.add_parser("init", help="Initialize Neo4j schema + seed data")

    # agent
    agent_parser = sub.add_parser("agent", help="Agent management")
    agent_sub = agent_parser.add_subparsers(dest="agent_command")
    agent_sub.add_parser("list", help="List all agents")
    info_parser = agent_sub.add_parser("info", help="Show agent details")
    info_parser.add_argument("agent_id", help="Agent ID")

    # rule
    rule_parser = sub.add_parser("rule", help="Rule management")
    rule_sub = rule_parser.add_subparsers(dest="rule_command")
    rule_sub.add_parser("list", help="List all rules")
    compile_parser = rule_sub.add_parser("compile", help="Recompile a rule")
    compile_parser.add_argument("rule_id", help="Rule ID")
    compile_parser.add_argument("--dry-run", action="store_true", help="Don't save to graph")

    # intent
    intent_parser = sub.add_parser("intent", help="Intent management")
    intent_sub = intent_parser.add_subparsers(dest="intent_command")
    list_parser = intent_sub.add_parser("list", help="List intents")
    list_parser.add_argument("--lifecycle", help="Filter by lifecycle",
                             choices=["pending", "claimed", "running", "success",
                                      "failed", "rejected", "awaiting_approval"])
    list_parser.add_argument("--source", help="Filter by source",
                             choices=["agent", "rule"])
    list_parser.add_argument("--limit", type=int, default=20, help="Max results")

    # approve
    approve_parser = sub.add_parser("approve", help="Approve an intent")
    approve_parser.add_argument("intent_id", help="Intent ID")

    # heartbeat (for agents to report they're alive)
    hb_parser = sub.add_parser("heartbeat", help="Send agent heartbeat to graph")
    hb_parser.add_argument("agent_id", help="Agent ID")

    return parser


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    commands = {
        "status": cmd_status,
        "init": cmd_init,
        "approve": cmd_approve,
        "heartbeat": cmd_heartbeat,
    }

    if args.command in commands:
        try:
            return commands[args.command](args)
        except Exception as e:
            print(fmt_error(str(e)))
            return 1

    if args.command == "agent":
        handler = {"list": cmd_agent_list, "info": cmd_agent_info}.get(
            args.agent_command)
        if not handler:
            print("Usage: hassaleh agent {list|info}")
            return 1
    elif args.command == "rule":
        handler = {"list": cmd_rule_list, "compile": cmd_rule_compile}.get(
            args.rule_command)
        if not handler:
            print("Usage: hassaleh rule {list|compile}")
            return 1
    elif args.command == "intent":
        handler = {"list": cmd_intent_list}.get(args.intent_command)
        if not handler:
            print("Usage: hassaleh intent {list}")
            return 1
    else:
        parser.print_help()
        return 0

    try:
        return handler(args)
    except Exception as e:
        print(fmt_error(str(e)))
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

---
## FILE: src/hassaleh/cli_fmt.py
```
"""CLI output formatting for Hassaleh.

Provides consistent, colorful terminal output without heavy dependencies.
Uses ANSI escape codes directly (no click/rich dependency).
"""

from __future__ import annotations

import os
import sys


# ──────────────────────────────────────────────
# Color support
# ──────────────────────────────────────────────

def _supports_color() -> bool:
    """Check if stdout supports ANSI colors."""
    if os.environ.get("NO_COLOR"):
        return False
    if not hasattr(sys.stdout, "isatty"):
        return False
    return sys.stdout.isatty()


_COLOR = _supports_color()


def _c(code: str, text: str) -> str:
    """Apply ANSI color code if supported."""
    if not _COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"


# ──────────────────────────────────────────────
# Color helpers
# ──────────────────────────────────────────────

def green(text: str) -> str:
    return _c("32", text)

def red(text: str) -> str:
    return _c("31", text)

def yellow(text: str) -> str:
    return _c("33", text)

def cyan(text: str) -> str:
    return _c("36", text)

def bold(text: str) -> str:
    return _c("1", text)

def dim(text: str) -> str:
    return _c("2", text)


# ──────────────────────────────────────────────
# Semantic formatters
# ──────────────────────────────────────────────

def fmt_ok(text: str) -> str:
    return green(f"✅ {text}")

def fmt_warn(text: str) -> str:
    return yellow(f"⚠️  {text}")

def fmt_error(text: str) -> str:
    return red(f"❌ {text}")

def fmt_header(text: str) -> None:
    print()
    print(bold(f"⭐ {text}"))
    print(dim("─" * (len(text) + 2)))

def fmt_section(title: str) -> str:
    return "\n" + cyan(f"  ── {title} ──")

def fmt_kv(key: str, value: str, indent: int = 4) -> str:
    return " " * indent + f"{bold(key)}: {value}"

def fmt_code(code: str) -> str:
    """Format a code block with line numbers."""
    lines = code.rstrip().split("\n")
    numbered = []
    for i, line in enumerate(lines, 1):
        num = dim(f"{i:4d} │ ")
        numbered.append(f"    {num}{line}")
    return "\n".join(numbered)


# ──────────────────────────────────────────────
# Table formatter
# ──────────────────────────────────────────────

def fmt_table(headers: list[str], rows: list[list[str]], indent: int = 4) -> str:
    """Format data as an aligned ASCII table.

    Args:
        headers: Column headers
        rows: List of row data (each row is a list of strings)
        indent: Left indent spaces

    Returns:
        Formatted table string
    """
    if not rows:
        return " " * indent + "(empty)"

    # Calculate column widths
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(str(cell)))

    # Format header
    prefix = " " * indent
    header_line = prefix + "  ".join(
        bold(h.ljust(widths[i])) for i, h in enumerate(headers)
    )
    separator = prefix + "  ".join("─" * w for w in widths)

    # Format rows
    data_lines = []
    for row in rows:
        cells = []
        for i, cell in enumerate(row):
            if i < len(widths):
                cells.append(str(cell).ljust(widths[i]))
        data_lines.append(prefix + "  ".join(cells))

    return "\n".join([header_line, separator] + data_lines)
```

---
## FILE: schema.cypher
```
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

CREATE CONSTRAINT rule_id IF NOT EXISTS
  FOR (r:Rule) REQUIRE r.id IS UNIQUE;

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

// Rule lifecycle for loading available rules
CREATE INDEX rule_lifecycle IF NOT EXISTS
  FOR (r:Rule) ON (r.lifecycle);
```

---
## FILE: seed_rules.cypher
```
// ═══════════════════════════════════════════════════════════════
// Hassaleh — Seed Rules (Sprint 2)
// ═══════════════════════════════════════════════════════════════
//
// Operational rules for agent orchestration.
// Apply after schema.cypher and seed.cypher.

// ──────────────────────────────────────────
// Rule 1: Agent Health Check
// ──────────────────────────────────────────
// Checks running agents for stale heartbeats.
// Restarts up to 5 times, then circuit-breaks.

MERGE (r1:Rule {id: "agent-health-check"})
SET r1 += {
  name: "Agent Health Check",
  version: 1,
  category: "operations",
  priority: 10,
  lifecycle: "available",
  description: "Check agent health every 5 min. Restart if unresponsive. Circuit breaker after 5 failures.",
  author: "dione",
  rule_text: 'EVERY "PT5M":
    MATCH (a:Agent {lifecycle: \'running\'}):
        IF a.last_heartbeat < NOW() - DURATION("PT5M"):
            IF a.restart_count_1h < 5:
                a.restart_count_1h += 1
                SUBMIT_INTENT "restart_agent" ON a
                LOG "Restarting unresponsive agent" LEVEL "warning"
            ELSE:
                a.lifecycle = "circuit_broken"
                ALERT "Circuit breaker: agent exceeded restart limit" ON a
                LOG "Circuit breaker triggered" LEVEL "error"'
};

// ──────────────────────────────────────────
// Rule 2: Task Timeout Sweep
// ──────────────────────────────────────────
// Fails tasks that have exceeded their expiry time.

MERGE (r2:Rule {id: "task-timeout-sweep"})
SET r2 += {
  name: "Task Timeout Sweep",
  version: 1,
  category: "operations",
  priority: 20,
  lifecycle: "available",
  description: "Fail tasks that have exceeded their expires_at time.",
  author: "dione",
  rule_text: 'EVERY "PT15M":
    MATCH (t:Task {lifecycle: \'running\'}):
        IF t.expires_at < NOW():
            t.lifecycle = "failed"
            t.error_reason = "Task timed out"
            LOG "Task timed out" LEVEL "warning"'
};
```
