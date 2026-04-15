# Hassaleh Architecture Documentation

**Version:** 1.0
**Author:** Gemini (D1 — Sprint 10)
**Date:** 2026-04-15
**Status:** Draft — pending D2 review

---

## 1. System Overview

Hassaleh is a **graph-native agentic framework** where all state — agents, tasks, capabilities, intents, rules, messages, and configuration — lives in a Neo4j graph database. There is no relational database, no Redis, no message queue. Neo4j is the single source of truth.

The system orchestrates AI agents (Dione, Inanna, Gemini, Codex) by expressing their permissions, assignments, and interactions as graph relationships. Agents interact with the system exclusively through the SDK; the Daemon is the only process that performs writes to the graph (aside from admin CLI operations).

```
┌─────────────────────────────────────────────────────────────────┐
│                        Operator / Admin                         │
│                      hassaleh CLI (cli.py)                      │
└──────────┬──────────────────────────────────────────────────────┘
           │ Direct Neo4j (sync)
           ▼
┌──────────────────────────────────────────────────────────────────┐
│                        Neo4j Graph DB                            │
│        bolt://localhost:7690 — Schema v1.2                       │
│                                                                  │
│  Agents ── Capabilities ── SkillDomains ── Tasks ── Intents     │
│  Rules ── Messages ── Discussions ── DaemonConfig ── Workspaces │
└──────────▲──────────────────────────────────▲───────────────────┘
           │ Async Neo4j (read+write)         │ Async Neo4j (read)
           │                                  │ + Intent submission
┌──────────┴──────────┐          ┌────────────┴──────────────────┐
│   Hassaleh Daemon   │          │        Agent SDK              │
│    (daemon.py)      │          │         (sdk.py)              │
│                     │          │                                │
│ • Intent processor  │          │ • Read-only graph queries     │
│ • Rule engine       │          │ • Intent submission           │
│ • Task orchestrator │          │ • Intent polling              │
│ • Capability exec   │          │ • Messaging (cursor-based)    │
│ • Health endpoint   │          │ • Discussion management       │
│ • OpenClaw bridge   │          │                                │
└─────────┬───────────┘          └────────────┬──────────────────┘
          │                                   │
          ▼                                   ▼
┌─────────────────────┐          ┌────────────────────────────────┐
│  OpenClaw Gateway   │          │         Agent Processes        │
│  (optional bridge)  │          │  (Dione, Inanna, Codex, etc.) │
│                     │          │                                │
│ • Telegram alerts   │          │  Uses SDK for all graph access │
│ • Discord messages  │          │  Submits Intents for actions   │
│ • Session mgmt      │          │  Polls for results             │
└─────────────────────┘          └────────────────────────────────┘
```

### Design Principles

1. **Graph-native**: All state is nodes and relationships. No ORM, no separate caches.
2. **Intent-mediated writes**: Agents cannot write to the graph directly. They submit Intents; the Daemon processes them after permission checks.
3. **Deterministic rules**: The GSL-Ops rule engine is fully deterministic — no probability, no sampling, no randomness. Rules are compiled to Python and executed on a schedule.
4. **Least privilege**: Capabilities run under dedicated OS users via `sudo -n -u`. Agents only access capabilities they're granted via `HAS_CAPABILITY` edges.
5. **Pluggable integration**: The OpenClaw bridge is optional. Hassaleh works standalone; OpenClaw adds messaging, notifications, and session management.

---

## 2. Component Architecture

### 2.1 Daemon (`daemon.py`)

The Daemon is the central long-running process. It runs as a systemd service under the `hassaleh-svc` user.

**Responsibilities:**

| Subsystem | Tick Interval | Description |
|-----------|--------------|-------------|
| Intent processor | 1s (every tick) | Claims pending Intents, checks permissions, spawns async workers |
| Task orchestrator | 1s (every tick) | Advances sequential/parallel task groups, syncs parent state |
| Rule engine | 60s (configurable) | Evaluates compiled GSL-Ops rules, emits property changes and alerts |
| Sweep | 15min (configurable) | Decays circuit breaker counters |
| Health endpoint | Always on | HTTP GET `/health` on port 9100 |
| Notification dispatch | After rule eval | Routes alerts to OpenClaw channels |

**Boot Sequence:**

1. Connect to Neo4j (async driver for intents, sync driver for rules)
2. Load `DaemonConfig` from graph
3. Verify `SystemVersion` schema compatibility
4. Load and compile all `Rule` nodes with `lifecycle: "available"`
5. Recover zombie Intents (mark stale `running`/`claimed` as `failed`)
6. Start HTTP health endpoint
7. Initialize OpenClaw bridge (if configured)
8. Initialize systemd watchdog (if `WATCHDOG_USEC` set)
9. Enter main tick loop

**Shutdown Sequence:**

1. Stop accepting new work (`running = False`)
2. Send `STOPPING=1` to systemd
3. Cancel all active worker tasks (30s grace period)
4. Mark remaining `running` Intents as `failed`
5. Stop health endpoint
6. Close OpenClaw bridge
7. Close both Neo4j drivers

### 2.2 SDK (`sdk.py`)

The SDK is the agent-facing API. It provides guarded, read-only graph access plus Intent submission. Agents never write to the graph directly.

**Key Design Decisions:**

- **Write blocking**: The SDK scans all Cypher queries for write keywords (`CREATE`, `MERGE`, `DELETE`, `SET`, etc.) and raises `PermissionError` if detected. This is enforced at the SDK level before the query reaches Neo4j.
- **Automatic LIMIT injection**: If a query doesn't contain `LIMIT`, the SDK appends one (default: 10,000 rows) to prevent runaway result sets.
- **Configurable timeouts**: Per-query timeout defaults to 3s, loaded from the `QueryConfig` graph node.
- **Label allowlist**: The `ALLOWED_TARGET_LABELS` set prevents Cypher injection through `target_label` parameters by restricting which node labels can appear in dynamic queries.

**API Surface:**

| Method | Description |
|--------|-------------|
| `query(cypher, params)` | Parameterized read-only query |
| `submit_intent(agent_id, action, ...)` | Submit an Intent for Daemon processing |
| `poll_intent(intent_id)` | Get current Intent state |
| `wait_for_intent(intent_id, timeout)` | Block until Intent reaches terminal state |
| `my_tasks(agent_id)` | Get tasks assigned to an agent |
| `project_status(project_id)` | Get project overview with task counts |
| `task_group_status(parent_task_id)` | Get ordered status for task group |
| `review_task(task_id, agent_id, approved)` | Submit supervised task review |
| `send_message(agent_id, content, context_id)` | Send message with cursor-based threading |
| `read_messages(agent_id, context_id)` | Read unread messages from cursor position |
| `advance_cursor(agent_id, message_id)` | Move agent's read cursor |
| `create_discussion(topic, context_id)` | Create collaborative discussion |
| `contribute_to_discussion(...)` | Add position + reasoning |
| `resolve_discussion(...)` | Leader resolves with decision |

### 2.3 CLI (`cli.py`)

The CLI provides operator and admin commands. It uses synchronous Neo4j access and directly queries the graph.

**Commands:**

| Command | Description |
|---------|-------------|
| `hassaleh status` | Show Daemon health, agents, rules, active intents |
| `hassaleh init` | Apply schema, seed data, and seed rules |
| `hassaleh agent list` | List all agents with capability counts |
| `hassaleh agent info <id>` | Detailed agent info with capabilities and recent intents |
| `hassaleh rule list` | List all rules with priority and compiler version |
| `hassaleh rule compile <id>` | Force-recompile a rule, show generated Python |
| `hassaleh intent list` | List recent intents with filters |
| `hassaleh approve <id>` | Approve an `awaiting_approval` intent |
| `hassaleh skill search <query>` | Search capabilities by name/description |
| `hassaleh skill domain` | Show skill domain taxonomy tree |

The CLI reads connection parameters from: CLI args → environment variables → `~/.hassaleh/config.toml` → defaults.

### 2.4 Rule Engine (`engine/`)

The rule engine is a three-stage pipeline: **Parse → Compile → Execute**.

#### 2.4.1 GSL-Ops Language

GSL-Ops (Graph Symbolic Logic — Operations) is a deterministic rule language derived from GWW3's stochastic GSL, with all probabilistic features removed. It uses Python-style colon blocks.

**Example Rule:**

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
```

**Language Features:**

| Feature | Syntax | Description |
|---------|--------|-------------|
| Graph query | `MATCH (pattern):` | Runs Cypher MATCH, binds variables |
| Scheduling | `EVERY "PT5M":` | Time-based rule trigger (ISO 8601 or shorthand) |
| Conditionals | `IF` / `ELIF` / `ELSE` | Standard branching |
| Iteration | `FOREACH var IN expr:` | Loop over collections |
| Variables | `LET name = expr` | Local variable binding |
| Property ops | `node.prop = val` / `+= -= *=` | SET, ADD, SUB, MUL operations |
| Intent creation | `SUBMIT_INTENT "cap" ON node` | Create an Intent for Daemon processing |
| Logging | `LOG "msg" LEVEL "info"` | Structured log entry |
| Alerting | `ALERT "msg" ON node` | Notification trigger |
| List comp | `[expr FOR x IN list IF cond]` | List comprehensions |

**Built-in Functions:** `NOW()`, `DURATION()`, `ABS()`, `MIN()`, `MAX()`, `CLAMP()`, `LEN()`, `STR()`, `INT()`, `FLOAT()`, `RANGE()`, `KEYS()`, `SORTED()`, `CONTAINS()`, `APPEND()`, `CONCAT()`, `FLATTEN()`, `UNIQUE()`, `SLICE()`, `SUM()`, `AVG()`, `FIRST()`, `LAST()`, `COUNT()`, `ZIP()`, `ENUMERATE()`.

Unknown function names are **rejected at compile time** — this is a security measure that prevents calling arbitrary methods on the runtime context.

#### 2.4.2 Parser (`engine/parser.py`)

Uses the **Lark** parsing library with the **Earley** algorithm and `PythonIndenter` for indent-based blocks. The grammar is defined in `engine/gsl_ops.lark` (220 lines).

The parser is a lazy-initialized singleton (thread-safe since Lark parsers are immutable after construction).

#### 2.4.3 Compiler (`engine/compiler.py`)

The `GSLOpsCompiler` transforms the Lark AST into executable Python source code. The generated code has a standard signature:

```python
def evaluate(ctx: RuleContext) -> None:
    """Compiled from GSL-Ops rule: <rule_id>"""
    ...
```

Key compiler behaviors:

- `MATCH` blocks become `for _row in ctx.match(...)` loops with variable extraction from Cypher patterns.
- `EVERY` blocks become `if ctx.should_run_schedule(...)` guards.
- Property accesses (`a.lifecycle`) compile to `ctx.prop(a, "lifecycle")`.
- Property modifications compile to `ctx.set_property()`, `ctx.add_property()`, etc.
- `SUBMIT_INTENT` compiles to `ctx.submit_intent(cap_id, target_node, args)`.

The compiled Python is cached in the Rule node's `compiled_python` property with a `compiler_version` tag. On Daemon boot, cached compilations are reused if the compiler version matches.

#### 2.4.4 Runtime (`engine/runtime.py`)

The `RuleContext` provides the runtime environment for compiled rules:

- **Graph queries**: `ctx.match(cypher_pattern)` uses `execute_read()` to enforce read-only transactions at the Neo4j protocol level.
- **Intent accumulation**: Property intents are collected (not applied immediately) into lists. After all rules evaluate, the resolver processes them.
- **Time injection**: `ctx.now()` and `ctx._last_run` are injectable for testing.

**Security Model:**

The Daemon runs compiled rule code in a restricted namespace:
- `__builtins__` is replaced with a safe subset (no `__import__`, no `open`, no `eval`).
- `RuleContext` is pre-injected; rules cannot import arbitrary modules.
- `ctx.match()` uses `execute_read()` — even if injected Cypher contains write operations, Neo4j rejects them at the protocol level.

#### 2.4.5 Resolver (`engine/resolver.py`)

When multiple rules target the same `(node_id, property)`, the resolver determines the final value:

1. **SET operations**: Lowest priority number wins (priority 1 beats priority 10).
2. **ADD/SUB operations**: All applied additively (commutative).
3. **MUL operations**: All applied multiplicatively (commutative).
4. **Mixed**: SET first, then ADD/SUB, then MUL.

Same-priority SET conflicts are logged as warnings; the first rule (by sort order) wins.

### 2.5 OpenClaw Bridge (`bridge/`)

#### 2.5.1 Gateway Client (`bridge/openclaw.py`)

An async HTTP client for the OpenClaw Gateway API (`aiohttp`-based). The Gateway runs on `localhost:18789` and requires bearer token auth.

**Capabilities:**

| Method | Endpoint | Description |
|--------|----------|-------------|
| `health()` | `GET /health` | Gateway health check |
| `send_message(channel, target, msg)` | `POST /api/tools/message` | Send via Telegram, Discord, etc. |
| `list_sessions()` | `POST /api/tools/sessions_list` | List active sessions |
| `spawn_session(task)` | `POST /api/tools/sessions_spawn` | Spawn isolated sub-agent |
| `send_to_session(key, msg)` | `POST /api/tools/sessions_send` | Cross-session messaging |
| `wake(text)` | `POST /api/tools/cron` | Trigger system event |

The bridge is **optional**. If `OPENCLAW_GATEWAY_URL` and `OPENCLAW_GATEWAY_TOKEN` are not set, the Daemon operates without OpenClaw integration (no notifications, no session spawning).

#### 2.5.2 Notification Dispatcher (`bridge/notifications.py`)

Routes rule outputs to messaging channels:

- **ALERT actions** → formatted Telegram/Discord messages with rule ID and target info.
- **Error-level LOG actions** → optional silent notifications (no buzz).

The dispatcher batches alerts and dispatches them after each rule evaluation cycle. Alert messages include the rule ID and target node name for traceability.

### 2.6 Domain Taxonomy (`domain.py`)

Provides dot-notation domain matching for capability scoping. Agents can be restricted to specific skill domains (e.g., `analysis.finance`) via the `allowed_domains` property.

**Functions:**

- `normalize_domain(value)` — Strips whitespace and dots.
- `domain_matches_prefix(domain, prefix)` — Hierarchical prefix match (`"data.graph"` matches `"data"`).
- `domain_matches_any(domain, prefixes)` — Match against a list of allowed prefixes.
- `domain_hierarchy(domain)` — Expands `"data.graph"` to `["data", "data.graph"]`.

The Daemon uses domain matching during capability permission checks to enforce agent-level domain restrictions.

---

## 3. Neo4j Schema

### 3.1 Node Types

| Label | Key Property | Description |
|-------|-------------|-------------|
| `Agent` | `id` (unique) | An AI agent registered in the system |
| `Capability` | `id` (unique) | An executable action (CLI command, skill, bridge call) |
| `SkillDomain` | `id` (unique) | Hierarchical skill category (dot-notation taxonomy) |
| `Workspace` | `id` (unique) | A filesystem workspace an agent operates in |
| `Intent` | `id` (unique, UUID) | A requested action submitted by an agent or rule |
| `Task` | `id` (unique) | A unit of work assigned to an agent |
| `Rule` | `id` (unique) | A GSL-Ops rule evaluated by the Daemon |
| `Message` | `id` (unique, UUID) | A message in a cursor-based linked list |
| `Discussion` | `id` (unique, UUID) | A collaborative decision-making thread |
| `DaemonConfig` | `id: "default"` | Singleton configuration node |
| `QueryConfig` | `id: "default"` | Singleton query limits configuration |
| `SystemVersion` | `id: "hassaleh"` | Schema version and compatibility info |

### 3.2 Key Properties

#### Agent

| Property | Type | Description |
|----------|------|-------------|
| `id` | string | Unique identifier (e.g., `"dione"`) |
| `name` | string | Display name |
| `emoji` | string | Agent emoji (e.g., `"🌙"`) |
| `lifecycle` | string | `pending` / `active` / `running` / `stale` / `inactive` / `circuit_broken` / `disabled` |
| `runtime` | string | Runtime type (e.g., `"openclaw"`) |
| `allowed_domains` | list/null | Skill domain restrictions (null = all allowed) |
| `last_heartbeat` | datetime | Last heartbeat timestamp |
| `restart_count_1h` | int | Circuit breaker counter (decayed by sweep) |
| `max_restarts_1h` | int | Circuit breaker threshold |
| `health_check_interval_sec` | int | Expected heartbeat interval |

#### Intent

| Property | Type | Description |
|----------|------|-------------|
| `id` | string (UUID) | Unique identifier |
| `action` | string | `execute_capability` / `update_property` / `review_task` / `assign_task` |
| `lifecycle` | string | `pending` / `claimed` / `running` / `success` / `failed` / `rejected` / `awaiting_approval` |
| `submitted_at` | datetime | Creation timestamp |
| `started_at` | datetime | When worker began |
| `completed_at` | datetime | When terminal state reached |
| `stdout` / `stderr` | string | Captured process output (capped at 100KB) |
| `exit_code` | int | Process exit code |
| `error_reason` | string | Failure/rejection reason |
| `source` | string | `"agent"` or `"rule"` |
| `source_rule` | string | Rule ID if source is rule |

#### Capability

| Property | Type | Description |
|----------|------|-------------|
| `id` | string | Unique identifier (e.g., `"exec-ls"`) |
| `kind` | string | `cli` / `skill` / `bridge` |
| `domain` | string | Dot-notation skill domain |
| `invoke_command` | string | Command or module path |
| `exec_as_user` | string | OS user for subprocess invocation |
| `requires_confirmation` | bool | Whether Intent needs human approval |

#### Task

| Property | Type | Description |
|----------|------|-------------|
| `id` | string | Unique identifier |
| `lifecycle` | string | `pending` / `ready` / `running` / `success` / `failed` / `awaiting_review` |
| `execution_mode` | string | `sequential` / `parallel` |
| `execution_order` | int | Order within a sequential group |
| `parent_task_id` | string | Parent task for grouped execution |
| `expires_at` | datetime | Task timeout deadline |

#### Rule

| Property | Type | Description |
|----------|------|-------------|
| `id` | string | Unique identifier |
| `lifecycle` | string | `available` / `disabled` |
| `priority` | int | Lower number = higher priority (for conflict resolution) |
| `rule_text` | string | GSL-Ops source code |
| `compiled_python` | string | Cached compiled Python source |
| `compiler_version` | string | Compiler version tag for cache invalidation |

### 3.3 Relationships

```
(Agent)-[:HAS_CAPABILITY]->(Capability)         # Permission grant
(Agent)-[:OPERATES_IN]->(Workspace)              # Workspace assignment
(Agent)-[:PROPOSED]->(Intent)                    # Agent submitted an Intent
(Agent)-[:SENT]->(Message)                       # Agent authored a message
(Agent)-[:LAST_READ]->(Message)                  # Cursor position for message reads
(Agent)-[:CONTRIBUTED]->(Discussion)             # Agent participated in discussion

(Intent)-[:TARGETS]->(Capability|Task|Node)      # What the Intent acts on
(Task)-[:ASSIGNED_TO]->(Agent)                   # Task assignment
(Task)-[:REQUIRES_CAPABILITY]->(Capability)      # Capability needed for the task
(Task)-[:SUPERVISED_BY]->(Agent)                 # Supervising agent for reviews

(Capability)-[:IN_DOMAIN]->(SkillDomain)         # Domain classification

(Message)-[:HEAD_OF]->(Context)                  # First message in a context
(Message)-[:TAIL_OF]->(Context)                  # Last message in a context
(Message)-[:NEXT]->(Message)                     # Linked-list ordering
(Message)-[:IN_CONTEXT_OF]->(Task|Discussion)    # Message context

(Discussion)-[:IN_CONTEXT_OF]->(Project)         # Discussion scope
(Discussion)-[:DECIDED_BY]->(Agent)              # Who resolved it
```

### 3.4 Indexes

| Index | On | Purpose |
|-------|----|---------|
| `intent_lifecycle` | `Intent.lifecycle` | Daemon hot path: find pending Intents |
| `agent_lifecycle` | `Agent.lifecycle` | Health checks, running agent queries |
| `task_lifecycle` | `Task.lifecycle` | Task assignment and orchestration |
| `task_execution_mode` | `Task.execution_mode` | Sequential/parallel group queries |
| `task_parent_group` | `Task.parent_task_id` | Group membership queries |
| `task_execution_order` | `Task.execution_order` | Sequential ordering |
| `capability_kind` | `Capability.kind` | Capability type filtering |
| `capability_domain` | `Capability.domain` | Domain-scoped lookups |
| `rule_lifecycle` | `Rule.lifecycle` | Loading available rules |
| `message_timestamp` | `Message.timestamp` | Message ordering |
| `discussion_lifecycle` | `Discussion.lifecycle` | Active discussion queries |

---

## 4. Intent Resolution Pipeline

The Intent pipeline is the core write path for the system. Every state mutation goes through this pipeline.

### 4.1 Submission Phase

```
Agent Process
    │
    ▼
SDK.submit_intent(agent_id, action, capability_id, ...)
    │
    ├── Validate target_label against ALLOWED_TARGET_LABELS
    ├── Generate UUID
    ├── Single write transaction:
    │     CREATE (Intent {lifecycle: "pending"})
    │     CREATE (Agent)-[:PROPOSED]->(Intent)
    │     CREATE (Intent)-[:TARGETS]->(Capability|Target)
    │
    └── Return intent_id
```

### 4.2 Processing Phase (Daemon)

```
Daemon Tick Loop (every 1s)
    │
    ▼
_process_pending_intents()
    │
    ├── Atomic Claim: MATCH pending Intents → SET lifecycle = "claimed"
    │   (Single write transaction — Neo4j write lock prevents double-claim)
    │
    ├── Per claimed Intent:
    │     ├── execute_capability:
    │     │     ├── _check_capability() — verify HAS_CAPABILITY edge + domain
    │     │     ├── _needs_approval() — check requires_confirmation flag
    │     │     │     └── If yes → _park_intent() → "awaiting_approval"
    │     │     └── Spawn async worker
    │     │
    │     ├── update_property:
    │     │     ├── Normalize lifecycle for supervised tasks
    │     │     └── SET target[$prop] = $value
    │     │
    │     ├── review_task:
    │     │     ├── Verify agent is SUPERVISED_BY lead
    │     │     ├── Verify task is "awaiting_review"
    │     │     └── Approve → "success" / Reject → "failed"
    │     │
    │     └── assign_task:
    │           ├── Parse payload for task_id
    │           ├── Check if already assigned
    │           └── MERGE (Task)-[:ASSIGNED_TO]->(Agent)
    │
    └── Worker (capability) flow:
          ├── SET lifecycle = "running"
          ├── Look up invoke_command and exec_as_user from Capability
          ├── Build command: sudo -n -u <exec_user> <command> <args>
          ├── asyncio.create_subprocess_exec (list form, no shell)
          ├── Timeout: configurable (default 300s)
          ├── Capture stdout/stderr (capped at 100KB each)
          └── SET lifecycle = "success"|"failed" + results
```

### 4.3 Intent Lifecycle State Machine

```
                  ┌──────────────────────────┐
                  │                          │
                  ▼                          │
pending ──→ claimed ──→ running ──→ success  │
     │         │           │                 │
     │         │           └──→ failed ◄─────┘
     │         │                  ▲
     │         ├──→ rejected      │ (timeout, error, shutdown,
     │         │                  │  zombie recovery)
     │         └──→ awaiting_approval
     │                    │
     │                    └──→ (claimed on approval)
     │
     └──→ rejected (rule-generated, immediate)
```

**Terminal states:** `success`, `failed`, `rejected`
**Parking state:** `awaiting_approval` (for capabilities with `requires_confirmation = true`)

### 4.4 Zombie Recovery

On Daemon boot, all Intents in `running` or `claimed` state are immediately transitioned to `failed` with the reason `"Daemon restarted during execution"`. This prevents orphaned Intents from blocking the system after a crash.

---

## 5. Agent Lifecycle

### 5.1 Current Implementation

Agents have a `lifecycle` property that tracks their operational state. The Daemon and rules manage transitions:

```
pending → active/running (on first heartbeat or task assignment)
running → circuit_broken (agent-health-check rule: >5 restarts in 1h)
```

The `restart_count_1h` counter is incremented by the health-check rule and decayed by the periodic sweep (every 15 minutes).

### 5.2 Planned Heartbeat System (Sprint 10, Task B)

The heartbeat system (specified in `docs/spec-heartbeat.md`) adds a formal lifecycle state machine:

```
pending → active (first heartbeat)
active → active (heartbeat within threshold)
active → stale (no heartbeat for >2h)
stale → active (heartbeat received, token chain reset)
stale → inactive (no heartbeat for >24h)
inactive → active (heartbeat received, token chain reset)
disabled [terminal] (admin only — heartbeats rejected)
```

Key design features:
- **API key authentication**: `agent_id` derived server-side from key hash (no impersonation).
- **Chained heartbeat tokens**: Each heartbeat response includes a token required for the next heartbeat (replay protection).
- **Rate limiting**: Server-side 60s minimum interval enforced in Cypher (prevents Neo4j write flooding).
- **`disabled` terminal state**: Admin-only; cannot be overridden by heartbeats.

---

## 6. Task Orchestration

The Daemon orchestrates grouped tasks through execution modes.

### 6.1 Execution Modes

| Mode | Behavior |
|------|----------|
| `sequential` | Tasks within a group run one at a time, ordered by `execution_order`. The next `pending` task transitions to `ready` only after the previous completes with `success`. |
| `parallel` | All `pending` tasks in a group transition to `ready` simultaneously. With `fail_fast: true` (default), no new tasks activate if any have `failed`. |

### 6.2 Supervised Tasks

Tasks with `execution_mode: "supervised"` follow a review workflow:

```
pending → ready → running → awaiting_review → success (approved)
                                            → failed (rejected)
```

The review is performed by the agent linked via `SUPERVISED_BY`. The `review_task` action in the Intent pipeline handles approval/rejection with comment.

### 6.3 Parent State Sync

Parent tasks aggregate their children's lifecycle:
- All children `success` → parent becomes `success`.
- Any child `failed` (with `fail_fast` or all terminal) → parent becomes `failed`.
- Any child `awaiting_review` → parent state unchanged (waiting).

---

## 7. Messaging System

Hassaleh uses a **cursor-based linked-list** model for messages, stored entirely in the graph.

### 7.1 Data Model

```
(Message)─[:HEAD_OF]─→(Context)  ← first message
(Message)─[:TAIL_OF]─→(Context)  ← last message
(Message)─[:NEXT]─→(Message)     ← linked list ordering
(Agent)─[:LAST_READ]─→(Message)  ← per-agent read cursor
(Agent)─[:SENT]─→(Message)       ← authorship
```

### 7.2 Operations

**Send Message:**
1. Create `Message` node with content and timestamp.
2. Link `(Agent)-[:SENT]->(Message)`.
3. If context exists:
   - First message: set as both `HEAD_OF` and `TAIL_OF`.
   - Subsequent: link old tail → new message via `NEXT`, move `TAIL_OF`.

**Read Messages:**
1. Find agent's `LAST_READ` cursor.
2. If cursor exists: follow `NEXT` chain from cursor.
3. If no cursor: start from `HEAD_OF` the context.
4. Cross-context reads fall back to timestamp ordering.

**Advance Cursor:**
- Delete old `LAST_READ` edge, create new one pointing to the specified message.

### 7.3 Discussions

Discussions add collaborative decision-making on top of messaging:
- Agents `CONTRIBUTE` positions with reasoning.
- A leader agent (who has contributed) can `resolve` with a decision.
- Discussions have their own lifecycle: `pending` → `running` → `success`.

---

## 8. Seed Data and Rules

### 8.1 Seed Data (`seed.cypher`)

The seed data establishes the minimum graph:

**Singletons:**
- `DaemonConfig {id: "default"}` — tick interval, timeouts, concurrency limits, circuit breaker settings.
- `QueryConfig {id: "default"}` — query timeouts, max result rows.
- `SystemVersion {id: "hassaleh"}` — schema version `1.2`.

**Agents:**
- `Agent {id: "dione"}` — First managed agent with `exec-ls`, `graph-query-inspector`, and `metrics-daily-summary` capabilities.

**Skill Domain Taxonomy:** 10 top-level domains with ~3 children each (~30 SkillDomain nodes) covering: `orchestration`, `data`, `security`, `communication`, `reporting`, `system`, `development`, `integration`, `analysis`, `automation`.

**Capabilities:** 8 seeded capabilities spanning CLI commands, skills, and bridge calls. Each has `exec_as_user` for privilege separation and optional `requires_confirmation`.

### 8.2 Seed Rules (`seed_rules.cypher`)

| Rule | Priority | Schedule | Description |
|------|----------|----------|-------------|
| `agent-health-check` | 10 | Every 5m | Restart unresponsive agents; circuit-break after 5 failures |
| `task-timeout-sweep` | 20 | Every 15m | Fail expired tasks |
| `task-assignment` | 30 | Every 1m | Auto-assign pending tasks to capable agents |
| `bulk-reset-failed-tasks` | 25 | Every 10m | Re-queue retryable failed tasks (up to 3 retries) |
| `capability-alert` | 15 | Every 5m | Alert on capability mismatches between tasks and agents |

---

## 9. Deployment Model

### 9.1 System Requirements

- **Python** >= 3.13
- **Neo4j** 5.x (bolt protocol on port 7690)
- **Dependencies:** `neo4j` (async driver), `aiohttp` (HTTP server + client), `lark` (parser)

### 9.2 Process Model

```
┌─────────────────────────────────────────┐
│  systemd unit: hassaleh-daemon.service  │
│  User: hassaleh-svc                     │
│  Watchdog: WATCHDOG_USEC enabled        │
│                                         │
│  hassaleh-daemon (Python asyncio)       │
│  ├── Neo4j async driver (intents)       │
│  ├── Neo4j sync driver (rules)          │
│  ├── aiohttp health server (:9100)      │
│  └── aiohttp OpenClaw client (optional) │
└─────────────────────────────────────────┘

┌─────────────────────────────────────────┐
│  Neo4j (Docker or native)               │
│  bolt://localhost:7690                   │
│  User: neo4j / hassaleh                 │
└─────────────────────────────────────────┘

┌─────────────────────────────────────────┐
│  OpenClaw Gateway (optional)            │
│  http://localhost:18789                  │
│  Bearer token auth                      │
└─────────────────────────────────────────┘
```

### 9.3 OS User Privilege Separation

Capability invocation uses `sudo -n -u <user> <command>` for privilege separation. Each capability kind runs under a dedicated low-privilege user:

| OS User | Purpose |
|---------|---------|
| `hassaleh-svc` | Daemon process itself |
| `hassaleh-fs` | Filesystem operations (`exec-ls`) |
| `hassaleh-audit` | Audit log operations |
| `hassaleh-ci` | CI/test invocation |
| `hassaleh-daemon` | Skill-type capabilities (in-process) |

The `sudo -n` flag ensures non-interactive invocation — no password prompts.

### 9.4 Configuration Hierarchy

All runtime configuration is stored in the Neo4j graph as singleton nodes:

| Node | Key Settings |
|------|-------------|
| `DaemonConfig` | `tick_interval_ms`, `max_concurrent_actions`, `intent_timeout_default_sec`, `sweep_interval_min`, `rule_eval_interval_sec`, `health_endpoint_port`, `parallel_fail_fast` |
| `QueryConfig` | `default_timeout_ms`, `max_result_rows`, `report_query_timeout_ms` |

Environment variables override graph config for sensitive values:

| Variable | Default | Description |
|----------|---------|-------------|
| `NEO4J_URI` | `bolt://localhost:7690` | Neo4j connection |
| `NEO4J_USER` | `neo4j` | Neo4j auth |
| `NEO4J_PASSWORD` | (from config) | Neo4j auth |
| `OPENCLAW_GATEWAY_URL` | — | OpenClaw Gateway URL |
| `OPENCLAW_GATEWAY_TOKEN` | — | OpenClaw bearer token |
| `HASSALEH_NOTIFY_TARGET` | — | Notification chat ID |
| `HASSALEH_NOTIFY_CHANNEL` | `telegram` | Notification channel |
| `HASSALEH_HEALTH_URL` | `http://127.0.0.1:9100/health` | Health endpoint URL |

### 9.5 Initialization

```bash
# 1. Start Neo4j
docker compose up -d neo4j

# 2. Apply schema + seed data
hassaleh init --uri bolt://localhost:7690 --user neo4j --password hassaleh

# 3. Verify
hassaleh status

# 4. Start Daemon
NEO4J_URI=bolt://localhost:7690 NEO4J_PASSWORD=hassaleh hassaleh-daemon
```

---

## 10. Security Model

### 10.1 Layers of Defense

| Layer | Mechanism |
|-------|-----------|
| **SDK write blocking** | Regex scan for write keywords before query runs |
| **Intent mediation** | All writes go through Daemon via Intent pipeline |
| **Capability permissions** | `HAS_CAPABILITY` edges checked before invocation |
| **Domain scoping** | `allowed_domains` restricts agent to specific skill domains |
| **Human approval** | `requires_confirmation` flag parks Intents for review |
| **OS privilege separation** | `sudo -n -u` per capability kind |
| **Rule sandboxing** | Restricted `__builtins__` (no `__import__`); `execute_read()` for graph queries |
| **Label allowlist** | `ALLOWED_TARGET_LABELS` prevents Cypher injection via dynamic labels |
| **API key auth** (planned) | Agent identity derived server-side from key hash (see section 5.2) |
| **Heartbeat token chain** (planned) | Replay protection via rotating tokens (see section 5.2) |

### 10.2 Known Limitations (MVP)

Per the security review in `docs/spec-mvp-test.md` section 9:

- Intent immutability not enforced at DB layer (application-level only).
- No API key expiry, rotation, or rate limiting per agent.
- No Intent cancellation once submitted.
- Error messages may leak internal filesystem paths.
- Claim atomicity relies on Neo4j write-lock behavior (verified by pattern, not formally proven).

---

## 11. Data Flow Diagrams

### 11.1 Agent Submits Intent (Happy Path)

```
Agent         SDK            Neo4j           Daemon          OS
  │            │               │               │              │
  ├─submit─────►               │               │              │
  │            ├─CREATE Intent──►               │              │
  │            ├─CREATE edges───►               │              │
  │            ◄─intent_id─────┤               │              │
  ◄─intent_id──┤               │               │              │
  │            │               │               │              │
  │            │               │  ◄──tick poll──┤              │
  │            │               │──pending list──►              │
  │            │               │  ◄──claim SET──┤              │
  │            │               │               ├─check cap────►│
  │            │               │  ◄──SET running┤              │
  │            │               │               ├─sudo -n -u───►
  │            │               │               │              ├─run
  │            │               │               │  ◄──stdout───┤
  │            │               │  ◄──SET success┤              │
  │            │               │               │              │
  ├─poll───────►               │               │              │
  │            ├──MATCH Intent──►               │              │
  │            ◄──success+stdout┤               │              │
  ◄─result─────┤               │               │              │
```

### 11.2 Rule Evaluation Cycle

```
Daemon                    Neo4j (sync)           Notifications
  │                           │                       │
  ├─rule eval interval────────►                       │
  │                           │                       │
  │  for each compiled rule:  │                       │
  │  ├─RuleContext(session)    │                       │
  │  ├─rule.evaluate(ctx)     │                       │
  │  │  ├─ctx.match(cypher)───►                       │
  │  │  │  ◄─results──────────┤                       │
  │  │  ├─ctx.set_property()  │  (accumulated)        │
  │  │  ├─ctx.submit_intent() │  (accumulated)        │
  │  │  └─ctx.alert()         │  (accumulated)        │
  │  │                        │                       │
  │  ├─create rule Intents────►                       │
  │  │                        │                       │
  │  resolve_intents()        │                       │
  │  ├─fetch current values───►                       │
  │  │  ◄─current values──────┤                       │
  │  ├─apply resolved SETs────►                       │
  │  │                        │                       │
  │  dispatch notifications───────────────────────────►
  │                           │                 (Telegram/Discord)
```

---

## 12. Project Structure

```
hassaleh/
├── docs/
│   ├── architecture.md          ← This document
│   ├── sprint-10-plan.md        ← Sprint 10 task breakdown
│   ├── spec-mvp-test.md         ← MVP Intent pipeline spec + security review
│   └── spec-heartbeat.md        ← Heartbeat system spec + security review
├── src/hassaleh/
│   ├── __init__.py
│   ├── sdk.py                   ← Agent SDK (read-only queries + Intent submission)
│   ├── daemon.py                ← Daemon service (Intent processing, rules, orchestration)
│   ├── cli.py                   ← Operator CLI (status, init, agent/rule/intent management)
│   ├── cli_fmt.py               ← ANSI terminal formatting helpers
│   ├── domain.py                ← Skill domain taxonomy matching
│   ├── agent_dummy.py           ← End-to-end proof-of-concept agent
│   ├── engine/
│   │   ├── __init__.py
│   │   ├── gsl_ops.lark         ← GSL-Ops grammar (Lark EBNF)
│   │   ├── parser.py            ← Lark parser (Earley + PythonIndenter)
│   │   ├── compiler.py          ← AST → Python transpiler
│   │   ├── resolver.py          ← Priority-based property conflict resolver
│   │   └── runtime.py           ← RuleContext runtime environment
│   └── bridge/
│       ├── __init__.py
│       ├── openclaw.py          ← OpenClaw Gateway HTTP client
│       └── notifications.py     ← Alert/log → messaging dispatcher
├── schema.cypher                ← Neo4j constraints and indexes
├── seed.cypher                  ← Initial agents, capabilities, domains, config
├── seed_rules.cypher            ← Operational GSL-Ops rules
├── pyproject.toml               ← Build config (hatchling), dependencies, entry points
└── tests/                       ← pytest test suite
```

---

## Appendix A: Entry Points

Defined in `pyproject.toml`:

| Command | Module | Description |
|---------|--------|-------------|
| `hassaleh` | `hassaleh.cli:main` | Operator CLI |
| `hassaleh-daemon` | `hassaleh.daemon:main` | Daemon service |

---

## Appendix B: Glossary

| Term | Definition |
|------|-----------|
| **Intent** | A declarative request for the Daemon to perform an action. The only way agents write to the graph. |
| **Capability** | A registered action (CLI command, skill, bridge call) that agents can request via Intents. |
| **GSL-Ops** | Graph Symbolic Logic — Operations. The deterministic rule language compiled to Python. |
| **Sweep** | Periodic Daemon maintenance cycle (circuit breaker decay, housekeeping). |
| **Reaper** | Process that recovers orphaned Intents stuck in non-terminal states after Daemon crashes. |
| **Circuit Breaker** | Per-agent restart counter that prevents infinite restart loops. Decayed by sweeps. |
| **Skill Domain** | Hierarchical dot-notation category for capabilities (e.g., `analysis.finance`). |
| **OpenClaw Bridge** | Optional HTTP integration layer for messaging and session management via OpenClaw Gateway. |
