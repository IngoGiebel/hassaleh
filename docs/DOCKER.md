# Docker Setup

Get Hassaleh running in 30 seconds with Docker Compose.

## Quick Start

```bash
# Clone
git clone https://github.com/IngoGiebel/hassaleh.git
cd hassaleh

# Copy environment file
cp .env.example .env

# Start everything (Neo4j + Schema Init + Daemon)
docker compose up -d

# Check status
docker compose ps
curl http://localhost:9100/health | python3 -m json.tool
```

## What Starts

| Container | Description | Ports |
|-----------|-------------|-------|
| `hassaleh-neo4j` | Neo4j 5 Community | 7690 (Bolt), 7477 (HTTP) |
| `hassaleh-init` | One-shot: applies schema + seed + rules | — |
| `hassaleh-daemon` | Hassaleh Daemon (async, rule eval) | 9100 (health) |

The `init` container runs once, applies `schema.cypher`, `seed.cypher`, and `seed_rules.cypher`, then exits.

## Using the CLI

From the host (with Python 3.13 + Hassaleh installed):

```bash
pip install -e .
export NEO4J_URI=bolt://localhost:7690
export NEO4J_PASSWORD=hassaleh-dev-2026

hassaleh status
hassaleh agent list
hassaleh rule compile agent-health-check --dry-run
hassaleh report daily
```

Or from inside the Daemon container:

```bash
docker exec hassaleh-daemon hassaleh status
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `NEO4J_PASSWORD` | `hassaleh-dev-2026` | Neo4j password |
| `NEO4J_BOLT_PORT` | `7690` | Neo4j Bolt port on host |
| `NEO4J_HTTP_PORT` | `7477` | Neo4j Browser port on host |
| `DAEMON_HEALTH_PORT` | `9100` | Daemon health endpoint on host |
| `OPENCLAW_GATEWAY_URL` | (empty) | OpenClaw Gateway URL (optional) |
| `OPENCLAW_GATEWAY_TOKEN` | (empty) | OpenClaw Gateway auth token |
| `HASSALEH_NOTIFY_TARGET` | (empty) | Telegram/Discord chat ID for alerts |
| `HASSALEH_NOTIFY_CHANNEL` | `telegram` | Notification channel |

## Neo4j Browser

Open http://localhost:7477 in your browser. Connect with:
- URI: `bolt://localhost:7690`
- User: `neo4j`
- Password: (from `.env`)

## Troubleshooting

### Neo4j won't start
```bash
docker compose logs neo4j
```
Common: port conflict. Change `NEO4J_BOLT_PORT` in `.env`.

### Daemon crashes on boot
```bash
docker compose logs daemon
```
Usually: Neo4j not ready yet. The `depends_on` + healthcheck should handle this.

### Reset everything
```bash
docker compose down -v  # removes volumes too
docker compose up -d    # fresh start
```

## Development

For development, run Neo4j in Docker but the Daemon locally:

```bash
# Start only Neo4j
docker compose up -d neo4j

# Run Daemon locally
export NEO4J_URI=bolt://localhost:7690
export NEO4J_PASSWORD=hassaleh-dev-2026
PYTHONPATH=src python3.13 -m hassaleh.daemon

# Run tests
PYTHONPATH=src pytest tests/ -v
```
