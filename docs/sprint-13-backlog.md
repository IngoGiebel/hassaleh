# Sprint 13 — Backlog (seeded from Sprint 12 carry-over)

**Curator:** Dione 🌙
**Opened:** 2026-04-21
**Status:** SEED — pending Ingo's sprint-theme decision. All items below are
non-blocking advisories captured during Sprint 12 reviews. Group them into
a sprint, defer to a v1.0.1 patch, or drop — Ingo to arbitrate.

## Legend

- **Priority:** P0 (security / correctness), P1 (spec compliance), P2 (cosmetic / polish)
- **Patch candidate:** item small enough to ship as `obs v1.0.1` outside a full sprint

---

## From Track A review (logging)

| ID | Priority | Title | Source |
|----|----------|-------|--------|
| S13-A1 | P2 | `PlainBoundLogger` return-type widened to `Any` — narrow back to concrete type | `sprint-12-track-a-review-round2.md` |
| S13-A2 | P2 | `PlainBoundLogger` json-serialization fallback is untested | `sprint-12-track-a-review-round2.md` |

## From Track D review (dashboards)

| ID | Priority | Title | Source |
|----|----------|-------|--------|
| S13-D1 | **P1** | Alert rules live under `prometheus/rules/` but §2.2 / §3.6 make Grafana unified alerts the source of truth. S3 "test-fired once" is not reachable via the Telegram contact point without migrating rules to `grafana/provisioning/alerting/` or adding Alertmanager. **v1.0.1 patch candidate.** | `sprint-12-track-d-review.md` advisory 1 |
| S13-D2 | P2 | `$env` template variable is cosmetic — no `env` label on any metric except `hassaleh_version_info`. Either drop the template or propagate the label to core metrics. | `sprint-12-track-d-review.md` advisory 2 |
| S13-D3 | **P1** | Panel UIDs not explicitly assigned; §3.5 requires stable panel UIDs so runbooks can deep-link. **v1.0.1 patch candidate.** | `sprint-12-track-d-review.md` advisory 3 |
| S13-D4 | P2 | S3-vs-§3.6 aggregation-style inconsistency (`sum(rate(...))` vs. plain `rate(...)`) — plan-internal; implementation currently follows S3. | `sprint-12-track-d-review.md` advisory 4 |
| S13-D5 | P2 | Uptime ratio can go negative for fully-dead agents (0 received, N missed → `1 - N/1 < 0`). Wrap with `clamp(0, 1)` in recording rule. | `sprint-12-track-d-review.md` advisory 5 |

## From Track E review (compose stack)

Carry-over from Round-1 advisories that the Round-2 fix did not touch. Round-1 verdict was CHANGES-REQUESTED on four blockers (all fixed); advisories below are non-blocking.

| ID | Priority | Title | Source |
|----|----------|-------|--------|
| S13-E1 | P2 | Runbook annotations on alert rules use a placeholder/stale URL convention. | `sprint-12-track-e-review.md` A1 |
| S13-E2 | **P0** | All service ports bind `0.0.0.0` — on a multi-interface host this exposes Grafana / Prometheus / Tempo / Loki / Promtail / cAdvisor publicly. Bind to `127.0.0.1` or an internal interface on non-dev deploys. | `sprint-12-track-e-review.md` A2 |
| S13-E3 | P1 | `neo4j-exporter` image tag was `latest` (pinned to `2024.2.6` in fix commit `3a7bdbb`). Verify pin survives future bumps. | `sprint-12-track-e-review.md` A3 |
| S13-E4 | P2 | Dashboard provisioning is editable — UI edits can clobber committed JSON. Switch Grafana provisioning to read-only. | `sprint-12-track-e-review.md` A5 |
| S13-E5 | **P1** | Grafana unified alerts directory `grafana/provisioning/alerting/` is missing — alerts cannot reach Telegram. (Couples with S13-D1.) | `sprint-12-track-e-review.md` A6 |
| S13-E6 | P1 | Alertmanager absent but Loki ruler config references it — drift risk. | `sprint-12-track-e-review.md` A7 |
| S13-E7 | P2 | Retention settings deviate from §8 cross-cutting (Loki / Prometheus / Tempo 7-day target). | `sprint-12-track-e-review.md` A8 |
| S13-E8 | P1 | `observability-bridge` network is not shared with Hassaleh's compose — the daemon cannot currently reach Loki/Tempo unless reachable via host ports. Publish a documented bridge-join procedure. | `sprint-12-track-e-review.md` A9 |

## From Track F review (tests)

| ID | Priority | Title | Source |
|----|----------|-------|--------|
| S13-F1 | **P1** | S2b smoke checks are deferred via three `@pytest.mark.skip` tests (`test_neo4j_exporter_up`, `test_cadvisor_up`, `test_per_container_metrics_non_empty`). Ship the Sprint 13 smoke harness that actually exercises them against a running obs stack. | `sprint-12-track-f-fix.md` |

## Process improvements (Dione's observations)

| ID | Priority | Title |
|----|----------|-------|
| S13-P1 | P1 | Gate Track-E-class tasks (deploy / compose) on an explicit "§8 checklist review" before dispatching to security review. Sprint 12 Track E Round-1 shipped four security blockers that the checklist would have caught pre-review. |
| S13-P2 | P1 | Subprocess-launching tests must lock down env + stdio from the first commit (`PYTHONDONTWRITEBYTECODE`, `DEVNULL` stdin, kill-on-timeout). Track F Round-2's hang cost an extra review cycle. |
| S13-P3 | P2 | Bcrypt histogram bucket tuning — drop the `0.01` and `0.05` buckets once one week of production data confirms they stay empty at cost-12. v1.1 patch. |

---

## Suggested Sprint 13 theme options (for Ingo)

1. **"Observability v1.0.1 patch"** — S13-D1, S13-D3, S13-E5, S13-E6, S13-F1 + process items. Fast-close sprint (~1–2 days). Highest-leverage because it actually makes alerts reach Telegram.
2. **"Deploy hardening"** — S13-E2, S13-E8 + broader host-binding / bridge-network audit. Security-focused.
3. **"Next capability"** — none of this carry-over; pick the next product feature. Defer all P1/P2 items to a rolling obs-patch cron.

— Dione 🌙
