# Sprint 12 Plan Review — Round 2 — Gemini (Clarity)

**Reviewer:** gemini-reviewer
**Date:** 2026-04-19
**Target:** docs/sprint-12-plan.md (v1.0.1, commit d7f79e6)
**Scope:** Round-2 verification of Round-1 CRs + regression check

## Verdict: clean

All nine Round-1 CRs from `sprint-12-plan-review-1-gemini.md` are resolved
in v1.0.1. The three v1.0.1 editorial fixes (per §10 change log) land
cleanly and introduce no regressions. Two pre-existing minor
cross-reference inconsistencies are recorded as non-blocking observations
rather than change requests, because (a) neither is a regression from v1,
(b) neither undermines a Round-1 CR resolution, and (c) both can be
resolved in-flight during implementation without re-reviewing the plan.

## CR Resolution Table

| CR | Status | v1 §-ref | Notes |
|----|--------|----------|-------|
| 1. R8 data sources (node_exporter / cAdvisor) | resolved | §2.1, §2.2 (`cadvisor/` dir), §2.5 (+80 MB), §3.2 (cAdvisor metrics paragraph), §1 S2b | cAdvisor chosen over node_exporter for per-container granularity; justification given in §2.1. S2b adds `up==1` assertions for both `neo4j-exporter` and `cadvisor` targets plus non-empty per-container metrics for `hassaleh-daemon`. |
| 2. Compose-stack path inconsistency | resolved | §1 S4 | S4 now reads `~/projects/observability-stack/docker-compose.yml`, matching §2.2 repo-layout and the Track E deliverable row in §5. One remaining path reference — the prose tree in §2.2 heading line `~/projects/observability-stack/` — is consistent. |
| 3. Slow-query log format unspec'd | resolved | §3.1.1 | Full JSON template given with `logger=hassaleh.daemon.graph.slow_query`, `level=WARN`, required fields (`duration_ms`, `pattern`, `param_shape`), explicit PII rule (types/lengths only, no raw statement). Owner = Track A with Track B co-ownership — matches Required-follow-up #3. |
| 4. R5 uptime-SLO undefined | resolved | §3.2.1, §1 R5 "so-what?" column | Prometheus recording rule `hassaleh:agent_uptime_ratio:1h` defined with formula + `clamp_min` guard + 1m evaluation interval. Numeric target 99.0 % stated in §3.2.1 header comment and cross-referenced from R5 row. See observation O1 for a minor cross-ref nit about the alert that's *referenced* but not *listed*. |
| 5. S6 drift-lint mechanism missing | resolved | §1 S6, §5 Track F, §7 Done criteria, §8 | `scripts/check-observability-drift.py` named as the concrete mechanism; introspects `obs/metrics.py` / `obs/logging.py` / `obs/tracing.py` / dashboard JSONs and diffs against §3.1–§3.5 tables. CI-wired, exits non-zero on drift. Owner = Track F. |
| 6. S1 ↔ §3.4 sampling contradiction | resolved | §1 S1 | S1 now scopes "every intent" to "the observability-smoke harness (run with `HASSALEH_TRACE_FORCE=1` to defeat the 10 % baseline sampling)". Production 10 % baseline explicitly preserved; sampled-out intents still counted in metrics. Matches Required-follow-up #6 verbatim. |
| 7. Artefact ↔ plan relationship unclear | resolved | Front-matter "Artefact ↔ plan relationship (clarified)" paragraph | Explicitly states "§1, §2, §3 of this plan **are** the three spec artefacts" and frames Day 1 as polishing plus (if requested) a pure-file-move split into three separate docs. Matches Required-follow-up #7 option A. |
| 8. Second reviewer (gemini) not in §4 | resolved | §4 reviewer table | Table now has explicit "Secondary reviewer (clarity)" column listing `gemini-reviewer` for all three artefacts. Prose immediately below reinforces "Every artefact gets both reviewer perspectives". |
| 9. SDK log/trace delivery unclear | resolved | §2.4 "SDK log/trace delivery" subsection | Four-rule spec given: SDK always writes JSON to stderr; Loki push only if `HASSALEH_LOKI_ENDPOINT` set; OTLP only if `HASSALEH_OTLP_ENDPOINT` reachable from SDK host; external-agent hosts responsible for further shipping. Service enum documented as deployment-dependent reach. |

## Regression check

The three v1.0.1 deltas (per §10) were spot-checked against the rest of
the document:

- **(a) §3.2.2 case consistency `Internal` → `internal`.** §3.2.2 line
  "Everything else (unhandled) → `internal`" is lowercase. The §3.4
  "Boost to 100 %" bullet cross-references `type ∈ {internal, graph,
  timeout}` — also lowercase. Consistent end-to-end.
- **(b) §3.2.2 prose on head-based sampling.** The clarifying paragraph
  now reads "the sampling decision is made at span start, not at
  exception time: callers that *know* an operation is likely to raise
  `type ∈ {internal, graph, timeout}` … call
  `obs.tracing.hint_error_prone()` to force 100 % sampling for that
  span." This aligns with §3.4's "strictly head-based" framing and the
  §3.4 "Boost to 100 %" bullet; no drift.
- **(c) §8 heartbeat-miss instrumentation row.** New row pins the
  increment site to `heartbeat_sdk.py:168` and narrates the
  `active → stale → inactive` transition semantics. Consistent with §3.2
  (`hassaleh_heartbeat_missed_total` counter, label `agent_id`) and with
  R5 (missed-heartbeat counter is the numerator of the uptime-SLO
  recording rule). No conflict with Track B's §5 deliverables row.

No regressions detected. The ~400 new lines introduced in v1 (not
v1.0.1) were also spot-checked for internal consistency; see
observations below for two minor pre-existing nits that were not
regressions.

## Non-blocking observations

- **O1 — §3.2.1 references an alert that is not listed in §1 S3.**
  §3.2.1 header comment says "Target: 99.0 % (S3 alert at < 0.99)", but
  the four alerts enumerated in S3 are `HassalehAuthFailureRateHigh`,
  `HassalehHeartbeatMissed`, `HassalehSlowQueries`,
  `HassalehInternalErrors` — none of them fires on
  `hassaleh:agent_uptime_ratio:1h < 0.99`. Either add a fifth alert
  (e.g. `HassalehAgentUptimeBelowSLO`) to S3 or drop the "(S3 alert at
  < 0.99)" parenthetical from §3.2.1. Pre-existing in v1; not a
  v1.0.1 regression. Implementation-time fix is fine.
- **O2 — §5 merge-order vs. track-E dependency.** The enumerated merge
  order is A → B → C → E → D → F, but the prose for step 4 says
  "E … after D once dashboards exist" (because E's Grafana
  provisioning loads dashboard JSON produced by D). If E really must
  wait on D, the enumerated order should be A → B → C → D → E → F.
  Alternatively, E can ship without dashboards and D can add them in a
  follow-up merge. Pre-existing in v1; not a v1.0.1 regression.
- **O3 — Minor.** v1 change-log bullet "Non-blocking items addressed"
  under Inanna claims §8 adds a histogram-bucket-tuning row "as a v1.1
  follow-up"; §8 does include it (row "Histogram-bucket tuning", owner
  Dione). Cross-reference is correct — noting only because the same
  row is referenced again under the gemini-reviewer non-blocking
  items list. Harmless duplication.
- **O4 — Style.** §9.1 still names the review command
  `openclaw sessions spawn --agent worker-opus` inline but §4 has moved
  to formal per-artefact primary/secondary reviewer columns. §9.1 could
  be updated to reflect the two-reviewer pattern (worker-opus *and*
  worker-gemini spawned per artefact), but this is documentation
  polish, not a blocker.

**Sign-off line:** Reviewed by gemini-reviewer, 2026-04-19.
