# Sprint 12 Track E — observability-stack implementation

Date: 2026-04-21
Repo: `~/projects/observability-stack`
Plan reference: `docs/sprint-12-plan.md` §2 and §5 Track E

## What was built

Track E created a separate repository at `~/projects/observability-stack` containing a self-contained
PLG + Tempo stack for Hassaleh observability.

Delivered components:
- `docker-compose.yml`
- `prometheus/prometheus.yml`
- `prometheus/rules/` populated from Hassaleh observability rules
- `loki/config.yml`
- `tempo/config.yml`
- `promtail/config.yml`
- `grafana/provisioning/datasources/datasources.yml`
- `grafana/provisioning/dashboards/dashboards.yml`
- `grafana/dashboards/` with the 5 Hassaleh dashboard JSON files copied in
- `README.md`

## Service topology

The compose stack includes:
- Loki for log aggregation
- Prometheus for scraping metrics
- Tempo for OTLP traces
- Grafana for dashboards and datasource provisioning
- Promtail for log shipping
- Neo4j exporter for Neo4j scrape targets

All persistent data is kept under local project paths:
- `./data/loki`
- `./data/tempo`
- `./data/prometheus`
- `./data/grafana`
- `./data/promtail`

## Ports

Chosen ports avoid collision with Hassaleh dev Neo4j (`7690` / `7487`):
- Grafana: `3000`
- Prometheus: `9091`
- Loki: `3100`
- Tempo UI/API: `3200`
- Tempo OTLP gRPC: `4317`
- Tempo OTLP HTTP: `4318`
- Neo4j exporter: `2004`

## Integration with Hassaleh daemon

The stack assumes the Hassaleh daemon exposes Prometheus metrics on `/metrics` and is reachable from
containers at `host.docker.internal:9090`.

Tempo is prepared to receive traces from Hassaleh via:
- `http://tempo:4318/v1/traces` for OTLP/HTTP
- `tempo:4317` for OTLP/gRPC

Grafana auto-loads the five dashboard JSON files from the Hassaleh repo and provisions three
core datasources:
- Prometheus
- Loki
- Tempo

Prometheus also loads the two rule files copied from the Hassaleh repo:
- `hassaleh-alerts.yml`
- `hassaleh-recording.yml`

The rule annotations still reference the existing Hassaleh runbooks under
`~/projects/hassaleh/docs/runbooks/`.

## Rationale

This implementation follows the Sprint 12 separate-stack boundary directly:
- observability stays outside Hassaleh’s own compose/runtime lifecycle
- Grafana provisioning is self-contained
- data does not escape the repo-local `./data/` tree
- the stack is startable with a single `docker compose up -d`

## Notes / follow-up

- Promtail is configured with a filesystem log fallback at `/var/log/hassaleh/*.log`; if Hassaleh daemon
  logs are emitted only through Docker stdout in a different deployment mode, the promtail scrape config
  may need a Docker-aware refinement later.
- The compose file uses `host.docker.internal:host-gateway` so Prometheus and the Neo4j exporter can reach
  host services from Linux Docker.
- Track D dashboards and the copied Prometheus rules are treated as source assets imported from the Hassaleh repo.
