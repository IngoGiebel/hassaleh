# Sprint 12 Track E — Inanna Round-2 review

**VERDICT: CLEAN (with carry-over advisories)**

Reviewer: Inanna (worker-opus, security + API perspective)
Date: 2026-04-21
Scope: `~/projects/observability-stack/` at HEAD `3a7bdbb` (base `0c36c17`),
reviewed against `docs/sprint-12-plan.md` v1.0.2 (FROZEN, `c2aea90`),
Round-1 review `docs/sprint-12-track-e-review.md`, and the author's
blocker-fix note `docs/sprint-12-track-e-fix.md`.

---

## Summary

All four Round-1 blockers (B1 secrets, B2 promtail host-log scrape, B3
missing cAdvisor, B4 wrong scrape port) are resolved in code, not just in
the fix doc. `docker compose config` parses cleanly with the shipped
`.env.example` and renders every `${VAR}` substitution. No new blockers
introduced. Three of the Round-1 advisories that the fix doc claims to
have addressed (A3 neo4j-exporter pin, A10 healthchecks, A12 runbook URLs)
are addressed, with minor qualifications captured below. One new advisory
(N1 — cAdvisor's host-kernel surface) is worth recording for the file,
and five Round-1 advisories remain open but were already non-blocking
and are explicitly out of scope for this round.

Track E clears S4 (stack boots), S2b (cAdvisor target exists and will
report `up==1` once running), and the boundary/security invariants. Track
E is mergeable behind Track D.

---

## Per-blocker confirmation

### B1 — Secrets (Grafana + Neo4j) — **RESOLVED**

Verified in `docker-compose.yml`:
- Grafana env (lines 66–69): `GF_SECURITY_ADMIN_USER: ${GRAFANA_ADMIN_USER}`,
  `GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD}`,
  `GF_USERS_ALLOW_SIGN_UP: "false"`.
- Neo4j-exporter env (lines 107–111): `NEO4J_URI`, `NEO4J_USERNAME`,
  `NEO4J_PASSWORD` all `${VAR}`-substituted; no plaintext literals.
- `.env.example` lists `GRAFANA_ADMIN_USER`, `GRAFANA_ADMIN_PASSWORD`,
  `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `HASSALEH_LOG_DIR`
  with `change-me-*` placeholders (no real values).
- `.gitignore` contains `.env` on its own line. `git ls-files | grep env`
  returns only `.env.example`; the live `.env` (if present) is not tracked.

`docker compose --env-file .env.example config` rendered (excerpt, edited for brevity):

```yaml
grafana:
  environment:
    GF_SECURITY_ADMIN_USER: change-me-admin
    GF_SECURITY_ADMIN_PASSWORD: change-me-strong-password
    GF_USERS_ALLOW_SIGN_UP: "false"
neo4j-exporter:
  environment:
    NEO4J_URI: http://host.docker.internal:7487
    NEO4J_USERNAME: neo4j
    NEO4J_PASSWORD: change-me-neo4j-password
```

Substitution works end-to-end.

Residual note (not a blocker): the `.env.example` placeholders are
themselves self-documenting "change-me-\*" strings. A user who skips the
`cp .env.example .env` step and starts the stack with no env vars set
will get Grafana admin/admin-style exposure only if they *also* point
`--env-file` at `.env.example`. The README's quickstart already covers
this, so it's operator-discipline, not a design gap.

### B2 — Promtail host-log scrape — **RESOLVED**

Verified in:
- `docker-compose.yml` promtail volumes (lines 89–92): no `/var/log:/var/log:ro`;
  only `${HASSALEH_LOG_DIR}:/var/log/hassaleh:ro` is bound (rendered as
  `/home/uranus/projects/hassaleh/logs → /var/log/hassaleh` read-only in
  `docker compose config`).
- `promtail/config.yml`: single scrape job `hassaleh-daemon-logfile` with
  `__path__: /var/log/hassaleh/*.log`. No `system-log-fallback`, no
  `/var/log/*.log` glob, no `docker_sd_configs`.

Host `auth.log`, `syslog`, `dpkg.log`, etc. are no longer reachable from
the Promtail container. §3.1 PII policy is restored to Hassaleh-only
scope.

### B3 — cAdvisor — **RESOLVED**

Verified in:
- `docker-compose.yml` (lines 117–136): `cadvisor` service, image
  `gcr.io/cadvisor/cadvisor:v0.49.1`, pinned; mounts
  `/:/rootfs:ro`, `/var/run:/var/run:ro`, `/sys:/sys:ro`,
  `/var/lib/docker:/var/lib/docker:ro`, `/dev/disk:/dev/disk:ro`; device
  `/dev/kmsg`; **`privileged: false`** (explicit, good); healthcheck on
  `/healthz`; joined to the `observability` bridge.
- `prometheus/prometheus.yml`: new `job_name: cadvisor` scraping
  `cadvisor:8080` with `service: cadvisor` label. Matches S2b's assertion
  target.
- Prometheus `depends_on` now includes `cadvisor`.

S2b's "`up==1`" assertion is satisfiable as soon as the stack boots.

### B4 — Hassaleh scrape port — **RESOLVED**

`prometheus/prometheus.yml` now targets `host.docker.internal:9100` for
the `hassaleh-daemon` job. Matches §2.4 `HASSALEH_METRICS_PORT=9100` and
§2.3 topology. Dashboard queries will populate.

---

## Spot-check of advisories claimed in fix doc

### A3 — neo4j-exporter pin — **ADDRESSED**

Image now `neo4jcommunity/neo4j-exporter:2024.2.6`. Reproducible and
supply-chain-audit-friendly.

### A10 — Healthchecks — **PARTIALLY ADDRESSED**

Healthchecks present on: `loki`, `prometheus`, `tempo`, `grafana`,
`promtail`, `cadvisor`. Good.

Qualifications (carried as non-blocking):
- `neo4j-exporter` has no healthcheck block.
- `depends_on` still uses the implicit `condition: service_started`
  everywhere (confirmed in `docker compose config` output). The
  healthchecks improve observability of the stack's own health but
  don't yet gate startup order — Grafana may still attempt provisioning
  reads before Loki is actually ready. Low risk for single-host dev;
  leave as Round-1 advisory A10 partial.

### A12 — `file://` runbook URLs — **ADDRESSED, WITH A PRAGMATIC CAVEAT**

`prometheus/rules/hassaleh-alerts.yml` now annotates each of the four
alerts with
`https://github.com/IngoGiebel/hassaleh/blob/trunk/docs/runbooks/<AlertName>.md`.
The four expected runbook stubs exist in the Hassaleh repo.

Caveat (new advisory N2): the Hassaleh repo is private. A browser
clicking the runbook link from Grafana will hit GitHub's login/404 page
unless the operator is signed in and has repo access. That's fine for
Ingo today, but if the stack is later shared with other operators, the
URL needs to be either an authenticated deep-link, a local mounted
static-file route, or the repo needs to be made public for runbook
documents. Downgrade of Round-1 A12 from "runbook URL doesn't resolve at
all" to "resolves only for repo members".

---

## Security regression check — post-fix

I looked explicitly for new secrets, new host mounts, new privileged
flags, and new inbound surfaces introduced by the fix commit. Findings:

- **No new secrets committed.** `git diff 0c36c17..3a7bdbb` removes two
  plaintext credentials (`admin/admin`, `hassaleh`) and adds no replacements.
- **No new privileged flags.** cAdvisor is explicitly `privileged: false`.
  No other service gained capabilities.
- **No new host-network mode.** All services still on the
  `observability-bridge` user network.
- **Promtail host mount strictly narrowed.** The old `/var/log:/var/log:ro`
  → new `${HASSALEH_LOG_DIR}:/var/log/hassaleh:ro`. Scope reduced, not
  widened.
- **cAdvisor introduces new host-surface**, captured as N1 below. This
  is a necessary-by-design expansion, not a mistake.

---

## New advisories from Round-2 review

### N1 — cAdvisor inherently expands host-surface

cAdvisor mounts the host root filesystem (`/` → `/rootfs:ro`), the Docker
state directory (`/var/lib/docker:ro`), `/sys`, `/var/run`, `/dev/disk`,
and receives the `/dev/kmsg` device. These mounts are read-only and
cAdvisor is `privileged: false`, but the union of them means that a
compromise of the cAdvisor image or its scrape clients could read every
image layer on the host (including any Neo4j data files, any SSH keys
under `/home`, and so on).

This is cAdvisor's documented required surface; there is no "less
privileged" supported mode for per-container metrics. Classification:
**non-blocking** — it was already implied by §2.1 R8 choosing cAdvisor.
Mitigation recommendations (v1.1+):
- Consider dropping `/dev/kmsg` if kernel-log metrics aren't consumed by
  any dashboard (removes one source of host-kernel leakage).
- Ensure `/rootfs` stays read-only (it does).
- Leave the cAdvisor HTTP UI bound only where needed; per Round-1 A2, it
  should ultimately be `127.0.0.1:8081` on this host.

### N2 — GitHub runbook URL visibility

Covered above under A12; listing it here so the advisory table stays
complete.

### N3 — `HASSALEH_LOG_DIR` is required at substitution time

If the operator starts the stack without `HASSALEH_LOG_DIR` set in the
environment or `.env`, `docker compose` renders an empty source path
for the promtail bind mount and the stack fails to start (or binds the
current working directory as `/var/log/hassaleh`, depending on the
daemon version). Not a security issue; it is a first-run reliability
gotcha. Fix options: default-value syntax
`${HASSALEH_LOG_DIR:-./data/hassaleh-logs}` in the compose, or a
preflight check in the README quickstart. Non-blocking.

---

## New blockers

**None.**

---

## Carry-over advisories from Round-1 (still open, still non-blocking)

The fix doc did not claim these; they remain as previously-logged
advisories. Listing for completeness so Round-3 / post-v1 triage has a
single source of truth:

| ID | Topic | Status |
|----|-------|--------|
| A1 | README source-of-truth / re-sync note for rules + dashboards | Partially addressed — README now has a one-liner ("Rule files and dashboard JSONs are copied from the Hassaleh repo; if the source of truth changes there, re-sync this stack copy"). Acceptable as-is. |
| A2 | Bind service ports to `127.0.0.1` | Not addressed; still `0.0.0.0`. Environmental — low risk on single-host. |
| A5 | `editable: false` on Grafana dashboards provisioning | Not addressed; still `editable: true`. |
| A6 | `grafana/provisioning/alerting/` unified alerts + `telegram-ingo` | Not addressed; directory still missing. Alert path remains Prometheus-rules-only, with no notifier. |
| A7 | Loki ruler references `http://localhost:9093` Alertmanager that doesn't exist | Not addressed. Pair with A6. |
| A8 | Retention: Loki 7d, Prometheus 7d, Tempo 168h | Partially addressed — Prometheus now has `--storage.tsdb.retention.time=7d` (new in this commit, good). Loki has no retention stanza. Tempo still `block_retention: 24h` (spec wants 168h). |
| A9 | Share `observability-bridge` with Hassaleh compose OR document `host.docker.internal` | Not addressed; `networking/README-bridge-network.md` still absent. |
| A11 | Stable panel UIDs inside dashboard JSONs (Track D concern) | Track D follow-up; not Track E. |

Reminder: none of these block the Sprint-12 merge. A6/A7 matter before
we rely on alerts for real; A8 matters before we rely on retention.

---

## Re-verification checklist (per Round-1 Recommendation section)

| Check | Result |
|-------|--------|
| `prometheus.yml` targets `:9100` | ✓ `host.docker.internal:9100` |
| `cadvisor` scrape job present, target `cadvisor:8080` | ✓ |
| `grep -r "admin" docker-compose.yml` shows nothing secret | ✓ (only `${GRAFANA_ADMIN_USER}` / `${GRAFANA_ADMIN_PASSWORD}`) |
| Promtail does not publish `/var/log/*.log` | ✓ |
| `.env` is gitignored | ✓ (`.gitignore` contains `.env`; `git ls-files` confirms only `.env.example` tracked) |
| `editable: false` on dashboards | ✗ (carry-over advisory A5) |

Five of six checks pass; the sixth (A5) was already non-blocking.

---

## Recommendation

**Clear Track E to merge** behind Track D. The four blockers are
genuinely resolved and the fix doc accurately describes the code. The
carry-over advisories are real but all non-blocking and well-scoped for
v1.1 / post-merge follow-up.

Next actions I'd suggest, in order of importance, before v1 is declared
"done":
1. Close A6+A7 together (Grafana unified alerts + decide Alertmanager).
   Today the alert path is a no-op.
2. Close A8 (Loki + Tempo retention) so v1's observability data survives
   the first quiet week.
3. Address N3 with a default value for `HASSALEH_LOG_DIR` or a preflight
   check — first-run UX.
4. A2, A5, A9, A11 can roll into a single "hardening" patch.

— Inanna
