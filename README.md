# Hassaleh ⭐

**A graph-native agentic framework where everything lives in Neo4j.**

> *Named after [Iota Aurigae](https://en.wikipedia.org/wiki/Iota_Aurigae) — a star in the constellation of the Charioteer.*

Hassaleh is an agent orchestration framework where all configuration, state, rules, and coordination live in a Neo4j graph database. A trusted Daemon mediates all system actions, enforcing permissions through OS-level user separation and capability-based access control.

## Architecture

```
┌──────────────────────────────────────────────┐
│  Agents (Dione 🌙, Inanna ⚔️, Codex, ...)     │
│  • Neo4j READ-ONLY access                    │
│  • Submit Intents for privileged operations   │
│  • Query graph via hassaleh.query() SDK       │
└──────────┬───────────────────────────────────┘
           │ Intents (pending → claimed → running → success/failed)
┌──────────▼───────────────────────────────────┐
│  Hassaleh Daemon (systemd, asyncio)          │
│  • Processes Intents via async workers        │
│  • Evaluates GSL-Ops rules (every 60s)        │
│  • Enforces Capability permissions            │
│  • Delegates via sudo -n -u per-capability    │
│  • OpenClaw Bridge for notifications          │
└──────────┬───────────────────────────────────┘
           │ Read/Write (hassaleh_daemon user)
┌──────────▼───────────────────────────────────┐
│  Neo4j Graph Database                         │
│  • Agents, Capabilities, Tasks, Rules         │
│  • Intents with full lifecycle tracking       │
│  • All config in graph (no YAML/JSON files)   │
└──────────────────────────────────────────────┘
```

## Features

- **Graph-native:** Everything is a node or edge. No YAML, no scattered configs.
- **GSL-Ops Rules:** Declarative rule language with MATCH, IF/ELIF/ELSE, FOREACH, EVERY, SUBMIT_INTENT, LOG, ALERT. Compiled to Python, cached in Neo4j.
- **Priority-based Conflict Resolution:** SET → ADD/SUB → MUL ordering. Multiple rules can target the same property; the resolver ensures deterministic outcomes.
- **Triple-layer Security:** Graph permissions → Daemon enforcement → OS-level sudo users per capability.
- **Intent Lifecycle:** `pending → claimed → running → success/failed/rejected` with zombie recovery on crash.
- **OpenClaw Bridge:** Notifications dispatched via Telegram/Discord. Optional — degrades gracefully.
- **CLI:** `hassaleh status/init/agent/rule/intent/skill/domain/report/approve/heartbeat` for operators.
- **Skill Domain Taxonomy:** Hierarchical capability domains enable discovery and domain-scoped execution permissions.
- **systemd-native:** Watchdog, graceful shutdown, journald logging, resource limits.

## Quick Start

### Docker (recommended)

```bash
git clone https://github.com/IngoGiebel/hassaleh.git
cd hassaleh
cp .env.example .env
docker compose up -d
curl http://localhost:9100/health | python3 -m json.tool
```

See [docs/DOCKER.md](docs/DOCKER.md) for details.

### Manual

```bash
# Prerequisites: Neo4j 5+, Python 3.13+
pip install -e .

export NEO4J_URI=bolt://localhost:7690
export NEO4J_PASSWORD=your-password
hassaleh init
hassaleh status
hassaleh rule list
hassaleh report daily
```

## GSL-Ops Rule Language

```python
# Agent health check with circuit breaker
EVERY "PT5M":
    MATCH (a:Agent {lifecycle: 'running'}):
        IF a.last_heartbeat < NOW() - DURATION("PT5M"):
            IF a.restart_count_1h < 5:
                a.restart_count_1h += 1
                SUBMIT_INTENT "restart_agent" ON a
                LOG "Restarting agent" LEVEL "warning"
            ELSE:
                a.lifecycle = "circuit_broken"
                ALERT "Circuit breaker triggered" ON a

# Bulk retry failed tasks
EVERY "PT10M":
    MATCH (t:Task {lifecycle: 'failed'}):
        FOREACH reason IN ["timeout", "crash", "oom"]:
            IF t.error_reason == reason:
                t.lifecycle = "pending"
                t.retry_count += 1
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `hassaleh status` | Daemon health, agents, rules, intents |
| `hassaleh init` | Initialize Neo4j schema + seed data |
| `hassaleh agent list\|info` | Agent management |
| `hassaleh rule list\|compile` | Rule management with code preview |
| `hassaleh intent list` | Intent browser (filterable) |
| `hassaleh skill list\|search\|info` | Capability discovery by domain and text |
| `hassaleh domain list` | Skill taxonomy tree with capability counts |
| `hassaleh approve <id>` | HITL intent approval |
| `hassaleh heartbeat <id>` | Agent heartbeat |
| `hassaleh report daily\|agents\|rules\|intents` | Reporting (text/json/markdown) |

## Project Structure

```
src/hassaleh/
├── daemon.py          # Async Daemon (systemd, Intent processing, rule evaluation)
├── sdk.py             # Agent SDK (read-only graph access + Intent submission)
├── cli.py             # CLI (argparse, all commands)
├── cli_fmt.py         # Output formatting (ANSI colors, tables)
├── engine/
│   ├── gsl_ops.lark   # GSL-Ops Lark grammar
│   ├── parser.py      # Earley parser + PythonIndenter
│   ├── compiler.py    # GSL-Ops → Python transpiler
│   ├── runtime.py     # RuleContext (execution environment)
│   └── resolver.py    # Priority-based conflict resolution
└── bridge/
    ├── openclaw.py    # OpenClaw Gateway HTTP client
    └── notifications.py  # ALERT → messaging dispatcher
```

## Part of the Alpha Auriga Ecosystem

- [Alpha Auriga](https://alpha-auriga.com) — ASI research & AI consciousness
- **Dione** 🌙 — Finance agent
- **Inanna** ⚔️ — Security research agent
- **Hassaleh** ⭐ — Graph-native agentic framework

## License

MIT

---

*Created by [Ingo Giebel](https://github.com/IngoGiebel) + Dione 🌙 (AI-assisted)*
