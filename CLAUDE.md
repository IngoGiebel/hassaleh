# CLAUDE.md — Hassaleh Project Guide

## What is Hassaleh?

Graph-native agent orchestration framework. All configuration, state, rules, and coordination live in Neo4j — no scattered YAML/JSON. Named after Iota Aurigae.

## Quick Start

```bash
cd ~/projects/hassaleh
docker compose up -d                          # Start Neo4j + Daemon
hassaleh --uri bolt://localhost:7690 --user neo4j --password hassaleh status
```

Test instance: `docker compose --profile test up -d neo4j-test` (port 7691, ephemeral)

## Architecture

```
Agent (SDK) → Intent → Daemon → Rule Engine → Capability Execution
                          ↕
                     Neo4j Graph
```

- **Agents** submit Intents through the SDK (`hassaleh.query()`)
- **Daemon** processes Intents, evaluates Rules every 60s, enforces capabilities
- **GSL-Ops** is the custom rule language (Lark parser → Python bytecode)
- **OpenClaw Bridge** sends notifications to Telegram/Discord

## Neo4j Instances

| Instance | Bolt | Password | Purpose |
|----------|------|----------|---------|
| System (apt) | :7687 | `UranuS-12345` | General |
| Hassaleh Dev (Docker) | :7690 | `hassaleh` | Development |
| Hassaleh Test (Docker) | :7691 | `hassaleh` | CI/Tests (ephemeral) |

## Coding Standards

- **Python 3.13+** — use type hints everywhere
- **Type checking:** `mypy --strict` must pass
- **Testing:** `pytest` with real Neo4j (no mocks for graph operations)
- **Linting:** `ruff check` + `ruff format`
- **Commits:** descriptive messages, co-authored with agent name
- **Branches:** `trunk` is the main branch

## Key Files

```
src/hassaleh/
├── daemon.py          # Async daemon (systemd, Intent processing)
├── sdk.py             # Agent SDK (read-only + Intent submission)
├── cli.py             # CLI interface
├── cli_fmt.py         # Output formatting
└── engine/
    ├── gsl_ops.lark   # Lark grammar
    ├── parser.py       # Earley parser
    ├── compiler.py     # GSL-Ops → Python transpiler
    ├── runtime.py      # Rule execution environment
    └── resolver.py     # Conflict resolution
```

## Agent Roles

| Agent | Role | Capabilities |
|-------|------|-------------|
| Dione (Opus 4.6) | Lead Architect | 25 caps — orchestration, market, moltbook |
| Inanna (Opus 4.6) | Security Architect | 14 caps — security audit, permissions, code review |
| Gemini (3.1 Pro) | Implementation | 9 caps — data collection, writing, git |
| Codex (GPT-5.4) | Code Quality | 8 caps — testing, CI, code review |

## Review Protocol

Every code change requires **two independent reviews** from different agents before merge.

## Don'ts

- Don't mock Neo4j in integration tests — use the test instance
- Don't modify `gsl_ops.lark` without parser tests
- Don't change capability/permission schemas without Inanna's security review
- Don't commit `.env` or credential files
- Don't use `openclaw update` — use `sudo /usr/bin/npm install -g openclaw`

## Environment Variables

```bash
NEO4J_URI=bolt://localhost:7690
NEO4J_USER=neo4j
NEO4J_PASSWORD=hassaleh
HASSALEH_HEALTH_URL=http://127.0.0.1:9100/health
```
