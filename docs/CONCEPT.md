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

**Rule Governance:**

Rules are authored and modified interactively by the responsible human operator, with the agent acting as an intelligent assistant in the process. The key principles:

1. **Human authority:** Only the human admin (via `neo4j` user or `hassaleh rule` CLI) can create, modify, or deactivate Rule nodes. Agents cannot modify rules — they have read-only graph access.
2. **Interactive authoring:** Hassaleh includes a **Rule Authoring Skill** that guides the operator through rule creation in natural language. The agent helps translate operational intent ("restart agents that haven't checked in for 5 minutes, but stop after 5 retries") into valid GSL-Ops syntax, compiles it, and shows the generated Python for review before committing to the graph.
3. **Auditability:** Every Rule node tracks `author`, `version`, `compiled_at`, and `compiler_version`. The `hassaleh rule compile --dry-run` command lets operators inspect generated code without affecting the running system.
4. **Runtime modifiability:** Rules can be updated at any time. The Daemon detects version/compiler mismatches on the next evaluation cycle and automatically recompiles from the authoritative `rule_text`.
5. **HITL safety net:** Capabilities can be marked `requires_confirmation: true`, which parks Intents in `awaiting_approval` state until a human approves them via `hassaleh approve <id>`. This provides a circuit breaker for sensitive operations that rules might trigger.
6. **Graceful override:** Any rule can be deactivated by setting `lifecycle: 'disabled'` without deleting it. The `hassaleh rule list` CLI shows all rules including disabled ones for full transparency.

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

### 4.14.3 Built-in Skills

Hassaleh ships with a set of built-in Skills that agents can invoke via their capabilities. Skills are stored as Capability nodes in the graph with `kind: 'skill'` and provide structured interaction patterns.

| Skill | ID | Description |
|-------|-----|-------------|
| **Rules Author** | `hassaleh-rules-author` | Interactive GSL-Ops rule authoring. Guides the operator through translating operational intent into valid rules: clarifies trigger conditions, actions, and safety constraints in natural language, generates GSL-Ops syntax, compiles and previews the generated Python (`--dry-run`), and commits to the graph only after explicit human approval. Default skill for all rule creation requests. |
| **Graph Explorer** | `hassaleh-graph-explorer` | Interactive graph querying and visualization. Translates natural language questions ("which agents have capabilities for file access?") into Cypher, executes read-only, and formats results. |
| **Report Builder** | `hassaleh-report-builder` | Generates structured reports from graph data. Supports agent activity, rule evaluation, intent statistics, and project progress reports in text, JSON, or Markdown format. |
| **Health Auditor** | `hassaleh-health-auditor` | Analyzes system health: agent heartbeat freshness, rule evaluation success rates, Intent failure patterns, and Neo4j connectivity. Generates recommendations for rule adjustments. |

**Skill invocation:** When an agent receives a request that matches a skill's domain (e.g., "create a rule that..."), it automatically invokes the corresponding skill. The skill provides the structured interaction pattern; the agent provides the conversational interface. Skills are implemented as Python modules in `src/hassaleh/skills/` and registered as Capability nodes during `hassaleh init`.

**Rules Author as default:** Any request to create, modify, or review a GSL-Ops rule is routed through the `hassaleh-rules-author` skill unless the operator explicitly bypasses it. This ensures consistent rule quality and human-in-the-loop validation.

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
