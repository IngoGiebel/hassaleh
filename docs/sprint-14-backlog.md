# Sprint 14 — Backlog

**Curator:** Dione 🌙
**Opened:** 2026-05-03
**Status:** SEED — items below are non-blocking carry-overs from
Sprint 13 plus newly-surfaced advisories from Track A / D Round-1
that did not make it into Sprint-14's primary track scope. Group,
defer to a v1.0.2 patch, or drop — Ingo / plan-reviewers to arbitrate.

## Legend

- **Priority:** P0 (security / correctness), P1 (spec compliance), P2 (cosmetic / polish)
- **Patch candidate:** item small enough to ship as `obs v1.0.2` outside a full sprint
- **Source:** the review or doc this item originates from

---

## Carry-over from Sprint 13 backlog (untouched, still open)

The Sprint-13 backlog (`docs/sprint-13-backlog.md`) collected
non-blocking advisories from Sprint-12 reviews. Sprint 13 did not
absorb them, so they remain open. Their priority and source are
unchanged.

### From Sprint-12 Track A review (logging)

| ID | Priority | Title | Source |
|----|----------|-------|--------|
| S13-A1 | P2 | `PlainBoundLogger` return-type widened to `Any` — narrow back to concrete type | `sprint-12-track-a-review-round2.md` |
| S13-A2 | P2 | `PlainBoundLogger` json-serialization fallback is untested | `sprint-12-track-a-review-round2.md` |

### From Sprint-12 Track D review (dashboards)

| ID | Priority | Title | Source |
|----|----------|-------|--------|
| S13-D1 | **P1** | Alert rules under `prometheus/rules/` not reachable via Telegram contact point — migrate to `grafana/provisioning/alerting/` or add Alertmanager. **v1.0.2 patch candidate.** | `sprint-12-track-d-review.md` advisory 1 |
| S13-D2 | P2 | `$env` template variable cosmetic — drop or propagate label | `sprint-12-track-d-review.md` advisory 2 |
| S13-D3 | **P1** | Panel UIDs not explicitly assigned. **v1.0.2 patch candidate.** | `sprint-12-track-d-review.md` advisory 3 |
| S13-D4 | P2 | S3-vs-§3.6 aggregation-style inconsistency | `sprint-12-track-d-review.md` advisory 4 |
| S13-D5 | P2 | Uptime ratio negative for fully-dead agents | `sprint-12-track-d-review.md` advisory 5 |

### From Sprint-12 Track E review (compose stack)

| ID | Priority | Title | Source |
|----|----------|-------|--------|
| S13-E1 | P2 | Runbook annotations use placeholder/stale URL | `sprint-12-track-e-review.md` A1 |
| S13-E2 | **P0** | All service ports bind `0.0.0.0` — exposes obs stack publicly on multi-interface hosts | `sprint-12-track-e-review.md` A2 |
| S13-E3 | P1 | `neo4j-exporter` image tag pin survival | `sprint-12-track-e-review.md` A3 |
| S13-E4 | P2 | Dashboard provisioning editable — UI clobbers committed JSON | `sprint-12-track-e-review.md` A5 |
| S13-E5 | **P1** | Grafana unified alerts directory missing — alerts cannot reach Telegram (couples with S13-D1) | `sprint-12-track-e-review.md` A6 |
| S13-E6 | P1 | Alertmanager absent but Loki ruler config references it | `sprint-12-track-e-review.md` A7 |
| S13-E7 | P2 | Retention settings deviate from §8 cross-cutting | `sprint-12-track-e-review.md` A8 |
| S13-E8 | P1 | `observability-bridge` network not shared with Hassaleh's compose | `sprint-12-track-e-review.md` A9 |

### From Sprint-12 Track F review (tests)

| ID | Priority | Title | Source |
|----|----------|-------|--------|
| S13-F1 | **P1** | S2b smoke checks deferred via three `@pytest.mark.skip` tests; Sprint 13 smoke harness still missing | `sprint-12-track-f-fix.md` |

### Process improvements

| ID | Priority | Title |
|----|----------|-------|
| S13-P1 | P1 | Gate Track-E-class tasks (deploy / compose) on explicit "§8 checklist review" before security review |
| S13-P2 | P1 | Subprocess-launching tests must lock down env + stdio from first commit (`PYTHONDONTWRITEBYTECODE`, `DEVNULL` stdin, kill-on-timeout) |
| S13-P3 | P2 | Bcrypt histogram bucket tuning — drop `0.01`/`0.05` buckets after one week of cost-12 production data |

---

## New: Sprint-13 Track-A non-blocking findings (deferred)

From `docs/sprint-13-track-a-review.md` §3 (non-blocking). Track-A's
Round-1 fix commit `1499117` addressed the three blockers; these were
deferred to "Track F's hardening pass" in the Round-1 reviewer's note.

| ID | Priority | Title | Source |
|----|----------|-------|--------|
| S14-A1 | P2 | `Handler` and `SessionFactory` use `Any` for `tx` and session — replace with `typing.Protocol` for `Tx.run` and `Session.begin_transaction` | `sprint-13-track-a-review.md` §3 finding 1 |
| S14-A2 | P2 | `HassalehRuntime` does not own its registry — accept `registry: Registry \| None = None` on `__init__`, default to module singleton | `sprint-13-track-a-review.md` §3 finding 2 |
| S14-A3 | P2 | `register_handler` accepts empty `requires_capability` — fail fast at registration time | `sprint-13-track-a-review.md` §3 finding 3 |
| S14-A4 | P2 | `ResultKind` literals untyped at construction — add `Result.ok(...)` / `Result.precondition_failed(...)` factories OR `assert result.kind in get_args(ResultKind)` | `sprint-13-track-a-review.md` §3 finding 4 |
| S14-A5 | P1 | `_REGISTRY` exported in `__all__` — drop from public surface, expose narrow `_reset_registry_for_tests()` helper | `sprint-13-track-a-review.md` §2 finding 2 |
| S14-A6 | P2 | Test 7 catches generic `Exception` instead of specific `dataclasses.FrozenInstanceError` | `sprint-13-track-a-review.md` §5 |
| S14-A7 | P2 | Test 5 (canonical order) is weak — assert `session_factory.assert_not_called()` AND `requires_capability` was never read | `sprint-13-track-a-review.md` §5 |
| S14-A8 | P2 | No test that handler receives same `tx` returned by `begin_transaction()` — add recording-handler `is`-check | `sprint-13-track-a-review.md` §5 |

---

## New: Sprint-14 Track-G out-of-scope items

These surfaced during Track-G scope freezing but were judged too
disruptive for a v1.0.1 patch (would expand the diff beyond seven
advisories). Defer to v1.1+.

| ID | Priority | Title | Rationale |
|----|----------|-------|-----------|
| S14-G1 | P2 | Add Sprint-15-style namespace prefix to all Sprint-12+ metrics (e.g. `hassaleh_v1_intent_total`) — would close D-A3 family-collision class permanently rather than per-family | Larger surface change, breaks dashboards; v1.1 candidate |
| S14-G2 | P2 | Move `is_obs_enabled()` check into a decorator pattern (`@obs_gated`) shared across all observability emitters — DRY for current and future emitters | DRY refactor; out of v1.0.1 scope |

---

## Suggested Sprint-15 theme options (for Ingo)

These are sketches only — actual Sprint-15 scope decision is post-
Sprint-14 merge.

1. **"Observability v1.0.2 patch"** — S13-D1, S13-D3, S13-E5, S13-F1
   + process items. Fast-close sprint (~1–2 days). Highest leverage:
   actually makes alerts reach Telegram (still missing after Sprint-14
   too — Sprint-14 Track G does not touch this surface).
2. **"Deploy hardening"** — S13-E2, S13-E8 + broader audit. Security-
   focused. Same theme suggested for Sprint-13; still relevant.
3. **"Rabt Intent types"** — first concrete v2+ Intent surface beyond
   Market Pipeline. Touches messaging-graph (`hassaleh-rabt`).
4. **"Nexus Intent types"** — observation ingest into the financial
   graph. Touches `hassaleh-nexus`.
5. **Track-A hardening pass** — pull S14-A1..S14-A8 forward. Small
   sprint (~1 day).

— Dione 🌙
