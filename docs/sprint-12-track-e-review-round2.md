# Sprint 12 Track E — Inanna Round-2 review

**VERDICT: CLEAN (blocker scope). Carry-over advisories remain; no new blockers; no new security regressions introduced by the fix.**

Reviewer: Inanna (worker-opus, security + API perspective)
Date: 2026-04-21
Scope: `~/projects/observability-stack` @ `3a7bdbb` (base `0c36c17`), reviewed
against the FROZEN plan `docs/sprint-12-plan.md` v1.0.2 (commit `c2aea90`),
Round-1 verdict `docs/sprint-12-track-e-review.md`, and the fix note
`docs/sprint-12-track-e-fix.md`.

Round-2 is narrow: confirm the four Round-1 blockers B1–B4 are fixed in
*code*, spot-check the advisories the fix note claims to have addressed,
and flag any **new** regressions the fix may have introduced. The
carry-over advisories that the fix did not claim to address are listed at
the bottom so nothing falls through the cracks before v1 freeze.

---

## Per-blocker verification

### B1 — Secrets handling — **RESOLVED ✓**

Verified in code (not just the doc):

- `docker-compose.yml` L67–68: Grafana env is now
  `GF_SECURITY_ADMIN_USER: ${GRAFANA_ADMIN_USER}` /
  `GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD}` — no plaintext
  literals left.
- `docker-compose.yml` L108–110: `NEO4J_URI` / `NEO4J_USERNAME` /
  `NEO4J_PASSWORD` all `${VAR}` substituted; the previous hardcoded
  `hassaleh` password is gone.
- `.gitignore` now contains `.env` (line 2). `git check-ignore -v .env`
  confirms the ignore rule matches:
  `.gitignore:2:.env	.env`.
- `git ls-files` shows only `.env.example` tracked; `.env` is not tracked.
- `.env.example` contains only `change-me-*` placeholders plus a
  non-sensitive `NEO4J_URI` and `HASSALEH_LOG_DIR`. No real credentials.

Environment substitution works end-to-end. Abbreviated excerpt from
`docker compose config` run with a temp `.env` populated from
`.env.example`:

```yaml
  grafana:
    environment:
      GF_SECURITY_ADMIN_PASSWORD: change-me-strong-password
      GF_SECURITY_ADMIN_USER: change-me-admin
      GF_USERS_ALLOW_SIGN_UP: "false"
  neo4j-exporter:
    environment:
      NEO4J_PASSWORD: change-me-neo4j-password
      NEO4J_URI: http://host.docker.internal:7487
      NEO4J_USERNAME: neo4j
```

Placeholders substitute as expected; no unresolved `${…}` tokens; no
warnings about empty variables. Spec §8 "Secrets handling" satisfied.

Minor observations (not blockers):

- The fix uses pure `${VAR}` substitution (compose auto-loads `.env`
  from CWD) rather than an explicit `env_file: .env` directive. Both
  are valid; the current form is fine for the documented workflow of
  "`cd observability-stack && docker compose up -d`". If operators ever
  invoke compose from another directory, they'll need `--env-file`.
- `GF_SECURITY_ADMIN_PASSWORD__FILE` (file-backed secret) was suggested
  in Round-1 as a nicer alternative to even `.env`-level plaintext. Not
  adopted; acceptable for v1 single-host dev.

### B2 — Promtail host-log scrape — **RESOLVED ✓**

Verified:

- `docker-compose.yml` L92: mount narrowed to
  `${HASSALEH_LOG_DIR}:/var/log/hassaleh:ro`. The previous
  `/var/log:/var/log:ro` bind is gone.
- `promtail/config.yml` is now 18 lines, single scrape job
  `hassaleh-daemon-logfile` → `/var/log/hassaleh/*.log`. The
  `system-log-fallback` job scraping `/var/log/*.log` is removed.
- `docker compose config` confirms the rendered bind:
  `source: /home/uranus/projects/hassaleh/logs → target: /var/log/hassaleh (read_only: true)`.

Host `auth.log`, `syslog`, `dpkg.log`, etc. can no longer reach Loki
through this stack. Spec §2.3 topology ("Promtail scopes to Hassaleh
container/file output only") satisfied.

Minor observation: promtail is still file-log-based rather than using
`docker_sd_configs` against the Docker API. That means Hassaleh must
actually file-log into `${HASSALEH_LOG_DIR}` for Loki to see anything —
if the daemon only writes JSON to stdout, a separate docker-log-driver
or a follow-up file-log sink is still needed. Not a regression from
Round-1; flagged so Track A knows this is still open.

### B3 — cAdvisor present, scraped — **RESOLVED ✓**

Verified:

- `docker-compose.yml` L117–136: `cadvisor` service exists, pinned image
  `gcr.io/cadvisor/cadvisor:v0.49.1`, published `8081:8080`, standard
  cAdvisor mount set (`/:/rootfs:ro`, `/var/run:/var/run:ro`, `/sys:/sys:ro`,
  `/var/lib/docker:/var/lib/docker:ro`, `/dev/disk:/dev/disk:ro`),
  `devices: [/dev/kmsg]`, healthcheck on `/healthz`,
  on the `observability` network.
- **`privileged: false` is explicitly set (L127).** This was the
  Round-1 §8 cross-cutting ask and a hard "must not reappear" — good.
- `prometheus/prometheus.yml` L26–30: `cadvisor` scrape job added,
  targeting `cadvisor:8080` with `service: cadvisor` label. Port `:8080`
  is correct (cAdvisor's in-container listen port, not the host-published
  `8081`).
- `prometheus` service now lists `cadvisor` under `depends_on` so it
  doesn't try to scrape before cAdvisor boots.

R8 per-container resource coverage and S2b (`up{job="cadvisor"} == 1`)
are now structurally possible.

### B4 — Hassaleh scrape port `:9100` — **RESOLVED ✓**

Verified:

- `prometheus/prometheus.yml` L16: target is now
  `host.docker.internal:9100` (was `9090`).
- `README.md` updated to match (`…:9100/metrics`).
- Round-1 also flagged that the implement doc carried the wrong port:
  README is now aligned; `docs/sprint-12-track-e-implement.md` remains
  under Track E's ownership — I did not re-check that file this round
  (out of scope for B1–B4 code-level verification).

§2.3 topology (`:9100/metrics` for the daemon) and S4 smoke-path alignment
are in place.

---

## Spot-check on claimed advisory fixes

The fix note claims three Round-1 advisories were also addressed. All
three check out:

| ID | Claim | Status |
|----|-------|--------|
| A3 | Pin `neo4jcommunity/neo4j-exporter` to `2024.2.6` (was `:latest`) | ✓ `docker-compose.yml` L104: `neo4jcommunity/neo4j-exporter:2024.2.6`. No `:latest` anywhere. |
| A10 | Healthchecks on Loki, Prometheus, Tempo, Grafana, Promtail, cAdvisor | ✓ All six services have a `healthcheck:` block with an HTTP probe at 30s/10s/5 cadence. Note: `depends_on` still uses the default `service_started` rather than `service_healthy`, so the healthchecks are currently informational — they don't gate startup order. That's the right trade-off for v1 (avoids boot deadlocks); worth flagging so Track F smoke tests don't rely on readiness-gated boot. |
| A12 | Replace `file://` runbook URLs with remote URLs | ✓ `prometheus/rules/hassaleh-alerts.yml` now points at `https://github.com/IngoGiebel/hassaleh/blob/trunk/docs/runbooks/<Alert>.md` for all four S3 alerts. The `file://` scheme is gone. **Caveat:** the `IngoGiebel/hassaleh` repo is private; anyone following the link from a fired alert needs repo access. For single-operator v1 that's Ingo-only, fine; for future on-call rotation this is a pending carry-over. |

---

## New regressions introduced by the fix — **NONE**

Checked against the diff `0c36c17..3a7bdbb`:

- **New secrets in committed files:** none. `.env.example` placeholders
  only; `.env` gitignored; no hardcoded credentials re-introduced
  elsewhere.
- **New host mounts:**
  - promtail: `${HASSALEH_LOG_DIR} → /var/log/hassaleh:ro` — *narrower*
    than before, intended.
  - cadvisor: the standard cAdvisor `:ro` set (`/`, `/var/run`, `/sys`,
    `/var/lib/docker`, `/dev/disk`). All read-only. `/var/run/docker.sock`
    is therefore reachable but read-only; combined with
    `privileged: false`, cAdvisor cannot control containers — only
    enumerate them — which is its designed surface. No privilege
    escalation introduced.
  - cadvisor: `devices: [/dev/kmsg]` — standard, needed for kernel-event
    metrics; does expose the kernel-log ring buffer to the container.
    Acceptable on a single-host dev box; in a multi-tenant deployment
    this would warrant a conversation, but v1 is single-tenant.
- **New privileged flags:** none. `privileged: true` appears nowhere.
  cAdvisor has `privileged: false` explicitly set.
- **New public-network exposure:** cAdvisor's `8081:8080` binds to
  `0.0.0.0` (inherits the stack's A2 pattern). Not a new regression —
  it follows the same binding style as every other service here — but
  it widens the A2 surface by one more port. Flagged under carry-over.
- **Network topology:** unchanged. `observability-bridge` is still
  declared in-stack only; no `external: true` addition to Hassaleh's
  compose. Carry-over advisory A9.

`docker compose config` (with a temp `.env` copy of `.env.example`)
completes with no warnings about undefined variables and renders all
services cleanly — see the abbreviated excerpt under B1.

---

## Carry-over advisories (unchanged from Round-1, not in the fix's claimed scope)

The fix note did not claim to address these. They remain open and
should be closed before v1 freeze — flagging so Dione / Track E can
schedule them, not to block this round:

| ID | Topic | Current state |
|----|-------|---------------|
| A2 | Bind ports to `127.0.0.1` (Grafana, Prometheus, Loki, Tempo-HTTP, neo4j-exporter, **now also cAdvisor `8081`**) | Still all `0.0.0.0`. cAdvisor adds one more. |
| A5 | `grafana/provisioning/dashboards/dashboards.yml` `editable: false` | Still `editable: true` / `disableDeletion: false`. |
| A6 | Add `grafana/provisioning/alerting/` (source-of-truth per §2.2, §3.6) | Directory absent; only `dashboards/` and `datasources/` provisioned. Spec drift vs §2.2. |
| A7 | Alertmanager missing while Loki ruler still sets `alertmanager_url: http://localhost:9093` | Unchanged. Net alert path still dead. |
| A8 | Retention: Loki 7d, Prometheus 7d, Tempo 168h | **Partially done.** Prometheus now has `--storage.tsdb.retention.time=7d` ✓. Loki `loki/config.yml` still has no retention stanza (implicit default). Tempo `tempo/config.yml` still `block_retention: 24h`, not `168h`. |
| A9 | Share `observability-bridge` with Hassaleh compose *or* delete the bridge artefacts from §2.3 | Unchanged. Missing `networking/README-bridge-network.md`. |
| A11 | Stable panel UIDs for runbook deep-linking | Track D concern; unchanged. |
| A12 (residual) | Runbook links now resolve over HTTPS, but the target repo is private | `file://` scheme gone ✓; audience-access consideration remains. |

---

## Round-1 "Recommendation" re-verify checklist — green where the fix claimed scope

- `prometheus.yml` targets `:9100` — ✓
- `cadvisor` has a scrape job at the correct target — ✓
  (runtime `up == 1` gate belongs to Track F; structurally in place here)
- No secret literals in `docker-compose.yml` — ✓
  (only `ALLOW_SIGN_UP: "false"` and `${GRAFANA_ADMIN_*}` placeholders)
- Promtail does not publish `/var/log/*.log` — ✓
- `.env` is gitignored — ✓ (`git check-ignore -v .env` confirms)
- `editable: false` on dashboards — **✗ carry-over (A5, not in fix scope)**

---

## Recommendation

**Clear Track E to merge for the B1–B4 blocker scope.** The four Round-1
blockers are fixed in code, not just in the fix note. The three
advisories the fix claimed (A3, A10, A12) are also actually in code. No
new secrets, no new privileged flags, no new broad host mounts have been
introduced by the fix.

Before v1 freeze / Track E sign-off, close carry-over advisories A2, A5,
A6, A7, A8 (Loki + Tempo halves), A9. A11 rolls into Track D. A12's
private-repo caveat can wait until on-call rotation is multi-operator.

— Inanna 🛡️
