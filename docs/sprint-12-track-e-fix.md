# Sprint 12 Track E — blocker fix note
Date: 2026-04-21
Plan: `docs/sprint-12-plan.md` (`c2aea90`)
Review: `docs/sprint-12-track-e-review.md`
Stack repo: `~/projects/observability-stack`
New stack HEAD: `3a7bdbba9456`

## B1 — secrets
Files changed:
- `docker-compose.yml`
- `.env.example`
- `.gitignore`
- `README.md`

Fixes:
- Removed committed plaintext Grafana and Neo4j credentials from compose.
- Compose now uses `${GRAFANA_ADMIN_USER}`, `${GRAFANA_ADMIN_PASSWORD}`, `${NEO4J_URI}`, `${NEO4J_USERNAME}`, `${NEO4J_PASSWORD}`.
- Added `.env` to `.gitignore`.
- Expanded `.env.example` with Grafana admin vars, Neo4j vars, and `HASSALEH_LOG_DIR` placeholder.
- Updated README to require `cp .env.example .env`, edit real credentials, then start the stack.

## B2 — promtail host-log scrape removed
Files changed:
- `docker-compose.yml`
- `promtail/config.yml`

Fixes:
- Removed `/var/log:/var/log:ro` mount.
- Removed `system-log-fallback` scraping `/var/log/*.log`.
- Kept only Hassaleh-labelled log scraping at `/var/log/hassaleh/*.log`.
- Narrowed mount to `${HASSALEH_LOG_DIR}:/var/log/hassaleh:ro`.

## B3 — cAdvisor added
Files changed:
- `docker-compose.yml`
- `prometheus/prometheus.yml`

Fixes:
- Added `cadvisor` service with pinned image `gcr.io/cadvisor/cadvisor:v0.49.1`.
- Added expected mounts for rootfs / docker / sys / disk metrics.
- Published host port `8081` -> container `8080`.
- Added Prometheus scrape job `cadvisor` targeting `cadvisor:8080`.

## B4 — Hassaleh metrics port fixed
Files changed:
- `prometheus/prometheus.yml`
- `README.md`

Fixes:
- Changed scrape target from `host.docker.internal:9090` to `host.docker.internal:9100`.
- Updated README note to match `:9100/metrics`.

## Advisories also addressed
- Pinned `neo4jcommunity/neo4j-exporter` from `latest` to `2024.2.6`.
- Added healthchecks to Loki, Prometheus, Tempo, Grafana, Promtail, and cAdvisor.
- Replaced `file://` runbook URLs in `prometheus/rules/hassaleh-alerts.yml` with GitHub HTTPS URLs.

## Stack commit
- `3a7bdbba9456` — `obs-stack(track-e): address review blockers B1-B4`

## docker compose config
Command run:
```bash
cd ~/projects/observability-stack
cp -f .env.example .env
docker compose config
```
Output:
```yaml
name: observability-stack
services:
  cadvisor:
    image: gcr.io/cadvisor/cadvisor:v0.49.1
    ports:
      - mode: ingress
        target: 8080
        published: "8081"
        protocol: tcp
  grafana:
    environment:
      GF_SECURITY_ADMIN_PASSWORD: change-me-strong-password
      GF_SECURITY_ADMIN_USER: change-me-admin
    image: grafana/grafana:11.2.0
  neo4j-exporter:
    environment:
      NEO4J_PASSWORD: change-me-neo4j-password
      NEO4J_URI: http://host.docker.internal:7487
      NEO4J_USERNAME: neo4j
    image: neo4jcommunity/neo4j-exporter:2024.2.6
  prometheus:
    image: prom/prometheus:v2.55.1
  promtail:
    image: grafana/promtail:3.2.1
    volumes:
      - type: bind
        source: /home/uranus/projects/hassaleh/logs
        target: /var/log/hassaleh
        read_only: true
        bind: {}
  tempo:
    image: grafana/tempo:2.6.1
networks:
  observability:
    name: observability-bridge
    driver: bridge
```
