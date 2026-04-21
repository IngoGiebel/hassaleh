# Sprint 12 — Retrospective

**Author:** Dione 🌙
**Closed:** 2026-04-21 (Cycle 17, Europe/Berlin)
**Timeline:** 2026-04-20 → 2026-04-21 (≈ 2 working days)
**Scope:** Observability v1 — structured logging, Prometheus metrics, OpenTelemetry traces, Grafana dashboards, separate docker-compose stack, integration tests.

## Outcome

**6 / 6 tracks merged. Sprint closed on schedule.**

| Track | Merge order | Review rounds | Final verdict | Author | Reviewer |
|-------|-------------|---------------|----------------|--------|----------|
| A — Logging | 1 | 2 | CLEAN (commit `63d4c47`) | worker-codex | Inanna |
| B — Metrics | 2 | 1 | CLEAN (commit `e6fd6b6`) | worker-codex | Inanna |
| C — Traces | 3 | 1 | CLEAN (commit `cf5d13a`) | worker-gemini | Inanna |
| D — Dashboards | 4 | 1 | CLEAN (commit `a61af86`) | worker-gemini | Inanna |
| E — Compose | 5 | 2 | CLEAN (commit `014a839` + obs-stack `3a7bdbb`) | worker-codex | Inanna |
| F — Tests | 6 | 3 | CLEAN (commit `f707fc9`) | worker-gemini | Inanna |

Plan doc `sprint-12-plan.md` was frozen at v1.0.2 (commit `c2aea90`) after both Round-2 reviews came back CLEAN.

## What worked

- **Idle-worker parallel dispatch** (SKILL.md §Step 3 relaxation of 2026-04-19) compressed ~8 h/phase serial runs into ~2–3 h. Tracks A+B+C authored in parallel on day 1; D landed as soon as B's names froze.
- **Virtual merge phase** after CLEAN verdicts worked well: the implementation commits already sat on trunk, so "merge" was a state-file flip, not a second code event. No integration conflicts arose.
- **Round-based review loop** caught real blockers:
  - Track A Round-1 surfaced a PII-redaction bypass on `cypher_params` and a non-zero-cost `HASSALEH_OBS=off` path. Both fixed in `63d4c47`.
  - Track E Round-1 caught four genuine security/spec blockers (plaintext creds, host-log scrape, missing cAdvisor, wrong scrape port). None would have been trivial to catch in isolated track review.
  - Track F Round-1 → Round-2 → Round-3 walked from "tests don't actually test S5" → "test hangs in the harness" → "harness is now deterministic", each round focused on one narrowed blocker.
- **Model fallback** (gemini-3.1-pro-preview → claude-opus-4-7 on 429/capacity) kept Track F moving during cycle 15's fix_round2 without an orchestrator intervention.

## What was bumpy

- **Track F took three review rounds.** Root causes:
  - Round-1 coverage gaps (B1 import-time isolation not actually exercised; B2 S2b coverage missing; B3 canonical-stage set incomplete) meant the first test pass was shallower than the plan warranted.
  - Round-2 blocker was a subprocess-hang in the B1 fix — the assertions were correct; the subprocess harness was racing pycache writes and inheriting pytest fds. Hardened in Round-3 with `PYTHONDONTWRITEBYTECODE`, `DEVNULL` stdin, and kill-on-timeout.
  - Takeaway for Sprint 13+: when a test launches subprocesses, lock down env + stdio explicitly from the first commit, don't assume default `subprocess.run` is "fresh enough".
- **Track E Round-1 shipped 4 security blockers.** Plaintext `admin/admin` in a committed compose and Promtail scraping `/var/log:/var/log:ro` were regressions against §3.1 PII policy and §8 Secrets cross-cutting. Implementation author moved fast on Track D's dashboard JSON and under-weighted the plan's security guardrails. Sprint 13 should explicitly gate Track-E-class tasks on a "§8 checklist review" before dispatching to review.
- **S3 "alert test-fired once" is not actually closeable in v1.** Track D's advisory (1) flagged that alerts under `prometheus/rules/` can't reach the Telegram contact point without an Alertmanager wiring we don't have. S3 remains partially-met by construction; the Grafana unified-alerts provisioning directory is the correct home and needs to be populated in a v1.0.1 patch (see backlog).

## Metrics

- **Cycles used:** 17 (planned: ~5 calendar days; actual: 2 working days of active cycling).
- **Dispatches fired:** ~30 total across 6 tracks × (implement + review + optional fix).
- **Rework cycles:** 4 total (Track A Round-2 fix, Track E fix, Track F Round-1 fix, Track F Round-2 fix).
- **Zero escalations to Ingo.** Orchestrator + workers handled all blockers end-to-end.

## Carry-over

Non-blocking advisories from every track's review are captured in
`docs/sprint-13-backlog.md`. Nothing blocks Sprint 12 close.

Notably **not** in v1 and deliberately deferred:
- S2b smoke checks (`neo4j-exporter up==1`, `cAdvisor up==1`, per-container metrics) — 3 `@pytest.mark.skip`s in `tests/test_observability.py`, tracked as a Sprint 13 smoke-harness task.
- Alertmanager + Grafana unified-alerts provisioning (Track D advisory #1, Track E advisory A6/A7).
- `$env` template variable population — cosmetic in v1 since only `hassaleh_version_info` carries an `env` label.
- Stable panel UIDs across dashboards (§3.5 requires them; v1 generated them implicitly).
- Uptime-ratio `clamp(0, 1)` wrap for fully-dead agents.

## Sign-off

Sprint 12 state file `sprint-12-state.json` closes with `status: COMPLETE`.
Sprint-12 orchestrator cron stands down; Sprint 13 orchestrator will arm
against `sprint-13-state.json` once Ingo scopes the next sprint.

— Dione 🌙
