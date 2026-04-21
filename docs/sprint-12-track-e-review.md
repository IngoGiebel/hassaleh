# Sprint 12 Track E — Inanna security + spec review

**VERDICT: CHANGES-REQUESTED**

Reviewer: Inanna (worker-opus, security + API perspective)
Date: 2026-04-21
Scope: `~/projects/observability-stack/` as delivered by Track E, reviewed
against `docs/sprint-12-plan.md` §2 and §5 Track E, and
`docs/sprint-12-track-e-implement.md`.

---

## Summary

Track E delivers a runnable PLG+Tempo stack in the right place (separate
repo, outside Hassaleh's own compose) with the expected service skeleton,
the five dashboard JSONs, and the two rule files in parity with the
Hassaleh source. However there are **four blockers** that will prevent
Sprint-12 Success Criteria (S2b, S4, S1) from going green and one that
materially increases the stack's attack surface. Ten additional advisories
are worth fixing before v1 freeze but are not strict blockers.

Blockers, headline form:

1. `prometheus.yml` scrapes `host.docker.internal:9090`, spec says `:9100`.
2. `cAdvisor` service is completely missing from the compose; R8 and S2b
   cannot be satisfied.
3. Grafana admin password and Neo4j password are committed in plaintext
   in `docker-compose.yml`; `.env.example` is defined but never referenced
   by the compose file. Violates §8 "Secrets handling".
4. Promtail mounts `/var/log:/var/log:ro` from the host *and* scrapes
   `/var/log/*.log` as a `system-log-fallback` job, shipping arbitrary
   host system logs (auth.log, syslog, etc.) into Loki. Violates §3.1
   PII policy and §2.3 topology.

The rest are advisories below.

---

## Criterion 1 — Boundary (§2 separate-stack)

**Finding:** OK with one observation.

- Stack lives at `~/projects/observability-stack/` outside the Hassaleh
  repo/compose. ✓
- No Hassaleh service is imported; no runtime call-back into Hassaleh.
- Rule files and dashboard JSONs were **copied** from
  `~/projects/hassaleh/observability/` (per the Track E implement doc)
  and are byte-identical to the source — verified with `diff -q`. That
  satisfies the "no cross-import" rule at the compose level but creates
  drift risk: any future change in the Hassaleh repo's copy won't
  propagate automatically. Not a blocker; worth a one-line note in the
  stack's `README.md` naming the source of truth and how to re-sync.
- **Advisory A1:** the runbook annotations on the four alert rules
  reference `file:///home/uranus/projects/hassaleh/docs/runbooks/...`,
  which is a filesystem coupling back into the Hassaleh repo from
  within the observability stack. See Criterion 6.

## Criterion 2 — Security

### Blocker B1 — Plaintext credentials in committed compose
`docker-compose.yml` sets:
- `GF_SECURITY_ADMIN_USER: admin`
- `GF_SECURITY_ADMIN_PASSWORD: admin`
- `NEO4J_PASSWORD: hassaleh`

directly as literal environment values (lines 50–51 and 85). The
`.env.example` file exists with placeholders for `GRAFANA_ADMIN_USER` /
`GRAFANA_ADMIN_PASSWORD`, but **nothing in the compose uses them** —
no `${GRAFANA_ADMIN_PASSWORD}` substitution, and no `env_file:` directive.
Spec §8 "Secrets handling" requires secrets in gitignored `.env` with
`.env.example` committed. Committing `admin/admin` makes the default
Grafana install unprompted-admin on first boot.

Required fix:
- Move `GF_SECURITY_ADMIN_USER`, `GF_SECURITY_ADMIN_PASSWORD`,
  `NEO4J_USERNAME`, `NEO4J_PASSWORD` to `.env`.
- Use `environment:` with `${VAR}` substitution **and/or** `env_file: .env`.
- Add `.env` to `.gitignore` (verify it's not already tracked).
- Expand `.env.example` to include `NEO4J_USERNAME` / `NEO4J_PASSWORD`
  placeholders.
- Consider generating a random admin password at first boot
  (`GF_SECURITY_ADMIN_PASSWORD__FILE`) rather than hardcoding even
  in `.env`.

### Blocker B2 — Promtail host-log scrape ships arbitrary system logs
`docker-compose.yml` line 72 mounts `/var/log:/var/log:ro` and
`promtail/config.yml` defines `system-log-fallback` with
`__path__: /var/log/*.log`. On this WSL host `/var/log` includes
`auth.log`, `syslog`, `dpkg.log`, and potentially more — all unrelated
to Hassaleh. Consequences:

- PII / secrets leak into Loki from outside Hassaleh's boundary (the
  §3.1 redaction pipeline only applies to Hassaleh-emitted JSON; raw
  syslog is not sanitized).
- Failed SSH logins, sudo invocations, package manager activity, etc.
  are persisted to Loki's filesystem chunks.
- Spec §2.3 explicitly scopes Promtail to Hassaleh's **container**
  stdout/err, not host system logs.

Required fix:
- Remove the `system-log-fallback` job from `promtail/config.yml`.
- Narrow the mount: drop `/var/log:/var/log:ro`; keep only a specific
  Hassaleh log path if the daemon is file-logging, e.g.
  `~/projects/hassaleh/logs:/var/log/hassaleh:ro`. Otherwise, rely on
  Promtail's Docker service-discovery (`docker_sd_configs`) to scrape
  container stdout with `com.docker.compose.service=hassaleh-*`
  filters. The implement doc already anticipates this ("may need a
  Docker-aware refinement later") — make it a prerequisite for
  Sprint-12 merge, not a follow-up.

### Advisory A2 — All service ports bind to 0.0.0.0
Grafana (`3000`), Prometheus (`9091`), Loki (`3100`), Tempo
(`3200`/`4317`/`4318`), and the Neo4j exporter (`2004`) all publish to
all interfaces. On a shared or tailnet-reachable host this widens the
attack surface. Grafana admin UI in particular has no reason to be on
the network.

Recommendation: bind to `127.0.0.1` for Grafana, Prometheus, Loki, Tempo
HTTP UI, and the Neo4j exporter. Leave Tempo OTLP (`4317`, `4318`) open
only if SDKs need to push from outside the host; on a single-host dev
setup, these can also be `127.0.0.1`-only.

### Advisory A3 — `neo4j-exporter:latest` image tag
`neo4jcommunity/neo4j-exporter:latest` is unpinned. Every other image
is version-pinned; this one is not. Supply-chain risk and
reproducibility. Pin a specific tag.

### Advisory A4 — No privileged containers / no host networking
Positive finding — neither `privileged: true` nor `network_mode: host`
is used. ✓

### Advisory A5 — Dashboard provisioning is editable
`grafana/provisioning/dashboards/dashboards.yml` has `editable: true`.
Spec §8 "Dashboard versioning" wants JSON-as-code with UI edits not
clobbering committed files. Flip to `editable: false` and, optionally,
`disableDeletion: true`.

## Criterion 3 — Spec compliance (§5 Track E)

### Blocker B3 — cAdvisor service missing
Plan §2.1 (R8) and §2.2 stack layout list cAdvisor as a required
service. §1 S2b explicitly asserts that Prometheus target `cadvisor`
has `up==1`. The compose has no cAdvisor container and
`prometheus.yml` has no `cadvisor` scrape job — verified with
`grep -c "cadvisor" docker-compose.yml prometheus.yml` → 0 matches in
both.

Required fix: add a `cadvisor` service (e.g.
`gcr.io/cadvisor/cadvisor:v0.49.1`) mounting
`/:/rootfs:ro,/var/run:/var/run:ro,/sys:/sys:ro,/var/lib/docker/:/var/lib/docker:ro,/dev/disk/:/dev/disk:ro`,
expose `:8080`, and add a scrape job in `prometheus.yml`.

### Blocker B4 — Prometheus scrapes wrong port for Hassaleh daemon
`prometheus.yml` scrapes `host.docker.internal:9090`. Plan §2.4 declares
`HASSALEH_METRICS_PORT=9100` and §2.3 topology shows `:9100/metrics`.
The Track E implement doc also uses `9090`, so this is an inherited
error, not a typo.

Consequences: S4 smoke fails (Hassaleh metrics target `up==0`); R2–R8
metric ingest does not happen; every dashboard query returns empty.

Required fix: change the target to `host.docker.internal:9100` (and
confirm the daemon's default port aligns). Update the Track E implement
doc accordingly.

### Services present, per spec §5
- Loki ✓
- Prometheus ✓
- Tempo ✓
- Grafana ✓
- Promtail ✓
- neo4j-exporter ✓
- cAdvisor ✗ (see Blocker B3)

### Provisioning auto-loads datasources + dashboards
- `grafana/provisioning/datasources/datasources.yml` provisions
  Prometheus, Loki, Tempo with stable UIDs and
  `tracesToLogsV2` configured from Tempo → Loki. ✓
- `grafana/provisioning/dashboards/dashboards.yml` points at
  `/var/lib/grafana/dashboards` with a 30s update interval. ✓
- **Advisory A6 — Grafana unified alerts directory is missing.** Spec
  §2.2 declares `grafana/provisioning/alerting/*` as the SOURCE OF
  TRUTH for alert rules + contact points (including the
  `telegram-ingo` contact point). The delivered tree has only
  `datasources/` and `dashboards/` under provisioning. The four S3
  alerts currently live in Prometheus rules (copied from Hassaleh),
  not in Grafana unified alerting. Net effect: alerts will fire from
  Prometheus without a configured notifier, since the stack contains
  no Alertmanager either (see Advisory A7). This is a spec drift
  inherited across Track D/E.

### Advisory A7 — Alertmanager is absent and Loki ruler references it
`loki/config.yml` contains
`ruler: { alertmanager_url: http://localhost:9093 }` but no
Alertmanager container runs. With no Loki rules defined today this is
a no-op; still, either remove the ruler stanza or fold it into the
future Alertmanager addition. Combined with Advisory A6, the net alert
path is dead: Prometheus evaluates rules but has nowhere to route
them, and Grafana has no unified-alerts contact point.

### Advisory A8 — Retention settings deviate from §8 cross-cutting
Spec §8: Loki `retention_period: 7d`, Prometheus
`--storage.tsdb.retention.time=7d`, Tempo `retention: 168h`.

- `loki/config.yml` has no retention configuration — implicit default.
- `prometheus` command has no `--storage.tsdb.retention.time` flag.
- `tempo/config.yml` sets `compactor.compaction.block_retention: 24h`
  (only 1 day; spec wants 7).

Fix all three to match spec before v1 freeze.

### Advisory A9 — `observability-bridge` network is not shared with Hassaleh
Spec §2.3 describes a shared docker bridge so Hassaleh's daemon can
reach Loki/Tempo. The compose declares the bridge *internal* to this
stack (no `external: true` variant for the Hassaleh side), and
Prometheus reaches the daemon via `host.docker.internal` rather than
via the shared bridge. Spec §2.2 also expects a
`networking/README-bridge-network.md` document describing how Hassaleh
joins the bridge — absent. Either:
- Declare the bridge as `external: true` in Hassaleh's compose and in
  a future variant here, or
- Document that we're intentionally using `host.docker.internal` as
  the integration pattern and remove the bridge-network artefacts from
  spec §2.3.

### Advisory A10 — No healthchecks
None of the services have a `healthcheck` block. `depends_on` without
`condition: service_healthy` only controls start order, not readiness.
Grafana, for instance, may start issuing provisioning reads before
Loki is live. Not a security blocker; adds to smoke-test flakiness.

## Criterion 4 — Dashboard parity

All five JSONs present with the UIDs fixed by §3.5:

| File | UID | Template vars |
|------|-----|---------------|
| hassaleh-overview.json | `ha-overview-v1` | `env` |
| hassaleh-intent-deep-dive.json | `ha-intent-v1` | `env`, `agent_id` |
| hassaleh-auth-security.json | `ha-auth-v1` | `env` |
| hassaleh-graph-performance.json | `ha-graph-v1` | `env`, `pattern` |
| hassaleh-agent-per-id.json | `ha-agent-v1` | `env`, `agent_id` |

UID parity ✓. Template-variable parity ✓.

- **Advisory A11 — Panel UIDs are not stable.** §3.5 requires stable
  panel UIDs for runbook deep-linking. The delivered JSONs have no
  panel `id` or `uid` fields at all; Grafana will auto-assign on import
  and they will change. This is mostly a Track D authorial concern
  that Track E inherited; flagging it here so it doesn't slip past
  merge.

## Criterion 5 — Rules parity

Verified byte-identical with `diff -q`:

- `prometheus/rules/hassaleh-alerts.yml` ≡
  `~/projects/hassaleh/observability/prometheus/rules/hassaleh-alerts.yml` ✓
- `prometheus/rules/hassaleh-recording.yml` ≡
  `~/projects/hassaleh/observability/prometheus/rules/hassaleh-recording.yml` ✓

All four S3 alerts present (`HassalehAuthFailureRateHigh`,
`HassalehHeartbeatMissed`, `HassalehSlowQueries`,
`HassalehInternalErrors`). Recording rules
(`hassaleh:agent_uptime_ratio:1h`, `hassaleh:intent_duration_seconds:p99_1h`)
present.

## Criterion 6 — Runbook URL resolution

All four alert annotations use
`file:///home/uranus/projects/hassaleh/docs/runbooks/<AlertName>.md`.

The runbook files do exist on the host (`HassalehAuthFailureRateHigh.md`,
`HassalehHeartbeatMissed.md`, `HassalehInternalErrors.md`,
`HassalehSlowQueries.md`). But:

- **Grafana runs in a container** and has no view into that host path.
- **The browser** (Grafana's frontend) following a `file://` link is
  subject to the browser's same-origin and filesystem-access rules;
  most modern browsers refuse to navigate from `http://localhost:3000`
  to `file:///home/...`.

Advisory A12: either (a) mount the runbooks directory into the Grafana
container and serve them via a simple static file route, (b) host the
runbooks in a lightweight sidecar (e.g. `nginx:alpine` on the bridge
network) and rewrite the annotations to `http://runbooks:8080/...`, or
(c) move runbooks to a GitHub URL once the Hassaleh repo is pushed
remote. Option (a) stays offline-friendly for the MINISFORUM; option
(c) future-proofs for multi-host deploys.

---

## Summary of required changes (blockers)

| ID | File | Change |
|----|------|--------|
| B1 | `docker-compose.yml`, `.env.example` | Remove hardcoded `admin`/`admin` and `hassaleh` password; wire `${VAR}` + `env_file: .env`; expand example. |
| B2 | `docker-compose.yml`, `promtail/config.yml` | Drop `/var/log` host mount and `system-log-fallback` scrape; scope Promtail to Hassaleh only. |
| B3 | `docker-compose.yml`, `prometheus/prometheus.yml` | Add cAdvisor service + scrape job to satisfy R8 / S2b. |
| B4 | `prometheus/prometheus.yml`, `sprint-12-track-e-implement.md` | Fix Hassaleh scrape target port `9090` → `9100` per §2.4. |

## Summary of advisories (non-blocking, address before v1 freeze)

| ID | Topic |
|----|-------|
| A1 | README should name Hassaleh-source-of-truth for rules + dashboards and a re-sync process. |
| A2 | Bind Grafana/Prometheus/Loki/Tempo-UI/neo4j-exporter ports to `127.0.0.1`. |
| A3 | Pin `neo4jcommunity/neo4j-exporter` version (remove `:latest`). |
| A5 | Flip `grafana/provisioning/dashboards/dashboards.yml` to `editable: false`. |
| A6 | Add `grafana/provisioning/alerting/` (unified alerts + `telegram-ingo` contact point) per §2.2. |
| A7 | Either add Alertmanager or drop the Loki ruler stanza; today alerts go nowhere. |
| A8 | Wire retention: Loki 7d, Prometheus `--storage.tsdb.retention.time=7d`, Tempo `168h`. |
| A9 | Share `observability-bridge` with Hassaleh compose *or* delete it from §2.3 and document `host.docker.internal`. Add `networking/README-bridge-network.md`. |
| A10 | Add `healthcheck` blocks + `depends_on: { condition: service_healthy }`. |
| A11 | Track D follow-up: stable panel UIDs for runbook deep-links. |
| A12 | Resolve runbook URLs so they work from the Grafana container / browser. |

---

## What's good (so Dione knows it doesn't need to regress)

- Stack location and boundary are correct (§2.2). ✓
- Image versions are pinned for Loki, Prometheus, Tempo, Grafana,
  Promtail. ✓
- Data persists under `./data/<service>/` — no escape from the repo.
- Rule files are exact copies of the Hassaleh source (parity is
  trivial to re-verify). ✓
- Grafana datasources are provisioned with stable UIDs and
  trace→log correlation is wired. ✓
- Prometheus rule loading path is correct; no `promtool check rules`
  errors would be expected. ✓
- All five dashboards are present with correct top-level UIDs and
  template variables.

---

## Recommendation

Send this review back to Track E (worker-codex) for one revision round.
Blockers B1–B4 are small concrete edits; advisories A1, A2, A3, A5, A8
are each one-to-three lines. A6, A9, A12 require small design decisions
but none reopen the architecture.

On re-submit, re-verify:
- `prometheus.yml` targets `:9100`,
- `cadvisor` target is `up==1` after `docker compose up -d`,
- `grep -r "admin" docker-compose.yml` returns nothing secret,
- `promtail` does not publish `/var/log/*.log`,
- `.env` is gitignored,
- `editable: false` on dashboards.

If those pass, the stack clears **S4** and Track E can merge behind
Track D.

— Inanna
