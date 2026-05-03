# Sprint 12 Plan Review — Round 1 — Gemini (Clarity)

**Reviewer:** gemini-reviewer
**Date:** 2026-04-19
**Target:** docs/sprint-12-plan.md (v0, commit 4e301cf)
**Scope:** completeness, clarity, internal consistency, testability

## Verdict: change-req

The plan is well-organised and the §1/§2/§3/§8 skeleton is strong. The
verdict is `change-req` because four material gaps would each force a
downstream implementer to make an unreviewed judgement call — three of
them are load-bearing for the §1 success criteria (S1, S6) and for R4,
R5, R8. None of the required follow-ups touch the §9.4 non-reviewable
decisions (PLG+Tempo, separate stack, sampling-yes, meta-review).

## Findings

### Completeness

- **R8 has no data source.** §2.1 enumerates Loki/Prometheus/Tempo/
  Grafana/Promtail/neo4j-prometheus-exporter. Neo4j metrics are covered.
  But R8 also requires **daemon memory/CPU** and **container
  restart-count**, and nothing in the stack emits these — no
  `node_exporter`, no `cAdvisor`, no Docker stats scraper. An
  implementer cannot satisfy R8 from the spec as written.
- **R4 slow-query log is unspec'd.** R4 explicitly requires a
  "slow-query log (>100 ms)". §3.2 adds the `hassaleh_cypher_query_slow_total`
  counter, but §3.1 (log schema) has no event type, logger name, or
  emission rule for the slow-query log line. Track A (Logging) would
  have to invent the contract.
- **R5 uptime-SLO is undefined.** R5 names "uptime-SLO per agent" and
  the "so what?" column says "SLO-green 99.x%". No metric in §3.2
  computes uptime, no Prometheus recording rule is spec'd, and no
  numeric target is given. "99.x%" is a placeholder, not a requirement.
- **S6 drift-lint mechanism is missing.** S6 asserts "documented and
  linted (no drift between code and spec)". There is no named tool, no
  CI job, no generator script. Without a concrete mechanism S6 is not
  an automatable criterion (see Testability below).
- **SDK logging/tracing reach is unclear.** §3.1 lists `hassaleh-sdk` and
  `hassaleh-heartbeat-sdk` as valid `service` values, and §3.3 says SDK
  clients can propagate W3C `traceparent`. But SDK processes run inside
  external agents — Promtail scrapes docker logs from Hassaleh's
  containers, not from every agent host. How SDK logs reach Loki (or
  whether they deliberately don't) is not covered.
- **Rollback / revert procedure.** `HASSALEH_OBS=off` disables the
  client side, but there is no documented procedure for tearing down
  the observability stack cleanly (data retention, network-bridge
  removal, Grafana state).
- **Test-fixture strategy for Track F.** Track F runs integration tests
  "that verify logs/metrics/traces are emitted". It is not stated
  whether these run against the real compose stack, an in-process
  OTLP/Prometheus fixture, or both. This changes the tests' blast
  radius and CI cost significantly.

### Clarity

- **§3.1 `service` enum of three values** — but the daemon, SDK, and
  heartbeat-SDK ship and scale independently. If a fourth service
  appears mid-sprint (e.g. a future admin CLI), the enum breaks. The
  spec does not say whether `service` is a closed enum or an
  extensible string; Track A needs that answer before writing the
  validator.
- **§3.2 `hassaleh_active_agents` gauge** — no derivation. Is it
  sourced from live heartbeats, a Neo4j query, or an in-memory
  registry? Two implementers would pick differently.
- **§3.4 "2× p99 of the last 24h"** — computed how and where? A live
  PromQL query on every span? Pre-aggregated and cached? Implementation
  choice materially affects performance.
- **§3.4 `HASSALEH_TRACE_FORCE=1`** escape hatch is named but not
  specified: is it process-wide or per-request header? Does it override
  the drop-to-0% rule for `/health` and `/metrics`?
- **§2.4 `obs.setup()` "called from daemon.py at boot"** — nothing
  says whether SDK processes also call it, even though §3.1 treats
  them as log sources.
- **§4 single reviewer (Inanna) for both Architecture and Interface.**
  §9.1 contemplates a second (worker-gemini) reviewer, but §4 does
  not. Either the table is incomplete or the "independent technical
  reviewer" mentioned in the front-matter is not wired into §4.
- **Plan vs. artefacts.** §4 describes "Three artefacts" (Requirements,
  Architecture, Interface) and Day 1 is "Spec authoring". But §1/§2/§3
  already contain those contents. It is not stated whether §1-§3 *are*
  the artefacts, or whether Day 1 produces three separate docs derived
  from them. Timeline and ownership depend on which is true.
- **§6 merge order `A → B → C`** — tracks D, E, F are silent. D
  (dashboards) depends on B (metrics existing) and C (trace attributes
  existing). E (compose) is largely independent but unblocks smoke.
  F (tests) is last by nature. Making this explicit would prevent a
  mid-sprint conflict.
- **§3.6 runbook URL** is `https://…/runbooks/auth-failure` — a
  literal ellipsis. Either pin a host (even a placeholder like
  `runbooks.hassaleh.local`) or cross-reference §8's "Runbook stubs"
  owner.

### Internal consistency

- **Path inconsistency for the compose stack.** S4 writes
  `~/observability-stack/docker-compose.yml` (line 65). §2.2 and
  Track E both write `~/projects/observability-stack/` (lines 97, 390).
  One is wrong; `make observability-smoke` in §7 will fail against the
  other.
- **R1 has no single §5 track owner.** Agent-lifecycle visibility
  spans Track A (agent-state logs), Track B (`hassaleh_active_agents`,
  `hassaleh_active_intents`), and Track D (dashboards). §8 has a line
  for most cross-cutting items but none for R1 itself, so integration
  could slip through.
- **R4 slow-query log split.** The counter is Track B; the log line is
  (presumably) Track A. Neither track's deliverables row names the
  slow-query log line, so it could be omitted by both.
- **R7 log counterpart.** R7's "so what?" ("security audits — who
  invoked what?") implies a structured audit log, but only metrics
  appear in §3.2. Tool-invocation logging should be either explicitly
  added to §3.1 or declared out-of-scope.
- **§6 personas vs §4 reviewer column.** §6 says worker-opus owns
  "Dione (orchestrator, architecture, code reviews) + Inanna (security
  + API reviews)" — two personas, one worker, presumably different
  sessions. §4's reviewer column names "Inanna (tech)" for
  Architecture and "Inanna (security+API)" for Interface. If Dione
  authors the architecture and Inanna reviews it, the split is clean;
  the plan should say so once rather than leaving it implicit across
  two sections.
- **§3.4 sampling vs §8 "Log sampling at high volume".** §8 row covers
  *log* sampling, not *trace* sampling. Fine by itself — but the
  overlap means an implementer grepping §8 for "sampling" may think
  the whole topic is covered, miss §3.4, and ship a naive tracer.
- **§9.2 success criteria vs §9.4 non-reviewability.** §9.2 says
  "Architecture … specific enough that Track authors can start
  without a second conversation", but §9.4 removes the stack-choice
  and deploy topology from the reviewable surface. Reviewers can still
  critique *specificity* of the chosen stack; worth stating
  explicitly.

### Testability

| Criterion | Automatable? | Notes |
|-----------|--------------|-------|
| S1 — every intent has a `trace_id` in Tempo | **Partially** | Contradicts §3.4's 10% baseline sampling. "Every intent" vs "10% sampled" needs reconciliation (e.g. "every intent *in the smoke harness*, with `HASSALEH_TRACE_FORCE=1`"). |
| S2 — overview dashboard visualises R1–R8 live | **Partially** | Grafana API can enumerate panels and assert non-empty results; "live" and "visualises R1–R8" will need a per-requirement panel-UID checklist. |
| S3 — four alert rules test-fired once | **Yes** | Synthetic-load driver → poll Prometheus `/api/v1/alerts` for `state=firing`. |
| S4 — stack starts via `docker compose up` | **Yes** | Exit code + health-checks; trivial. |
| S5 — Hassaleh works with obs disabled | **Yes** | Track F explicitly calls out "obs=off regression test". |
| S6 — contracts documented and linted (no drift) | **No, as written** | No linter or generator is named. Needs a concrete mechanism (e.g. "CI job X regenerates §3.2 from `obs/metrics.py` and diffs"). |

## Required follow-ups (if change-req)

1. **Specify R8 data sources.** Add `node_exporter` (or `cAdvisor`) and
   Docker-stats scraping to §2.1 and §2.2 repo layout, or explicitly
   descope daemon-memory/CPU and container-restart-count from R8 for
   v1.
2. **Resolve compose-stack path.** Fix S4 (§1 line 65) to match §2.2
   and Track E — `~/projects/observability-stack/`.
3. **Spec the slow-query log.** Add to §3.1 the log line emitted when
   a Cypher query crosses 100 ms: logger name, level, required fields
   (pattern, duration_ms, agent_id, redacted statement), and assign
   an owner (Track A with Track B coordination).
4. **Define uptime-SLO for R5.** Either add a Prometheus recording
   rule (formula + window + labels) and a concrete numeric target in
   §1, or descope to "uptime-SLO-ready metrics" and move the SLO to
   v2.
5. **Specify the S6 drift-lint mechanism.** Name the tool or script
   (e.g. `scripts/check-metrics-catalog.py` compares §3.2 rows to
   `obs/metrics.py` introspection) and an owner (probably Track F or
   Dione).
6. **Reconcile S1 with §3.4 sampling.** Rephrase S1 to "every intent
   in the smoke-test harness (run with `HASSALEH_TRACE_FORCE=1`) has a
   discoverable `trace_id` in Tempo" — or commit to 100% sampling for
   the smoke lane and state that explicitly.
7. **State the artefact ↔ plan relationship.** Either say "§1, §2, §3
   are the artefacts; Day 1 'Spec authoring' means polishing and
   splitting them into separate files" or "§1-§3 are the plan
   summary; Day 1 produces `docs/sprint-12-requirements.md` etc." —
   pick one.
8. **Name the second reviewer.** §4 should include the optional
   worker-gemini "clarity/completeness" reviewer that §9.1 already
   contemplates (this very review), so the workflow matches the
   front-matter's "one independent technical reviewer".
9. **Clarify SDK log/trace delivery.** Either add an SDK → Loki path
   to §2.3, or restrict §3.1's `service` enum to the daemon for v1
   and parking SDK-side telemetry to v2.

## Non-blocking observations

- §3.6 alert example uses a literal `https://…/runbooks/auth-failure`.
  Even a placeholder host (`runbooks.hassaleh.local`) would be better
  than an ellipsis — it catches the "missing runbook" case in a
  link-checker.
- §6 gives calendar days but no person-hour budget per track. Useful
  for load-balancing worker-codex (3 tracks) vs worker-gemini (3
  tracks), though not a blocker.
- §8 has owners everywhere; consider adding a one-line concern for
  "Cardinality enforcement" (§3.2 self-specifies caps, but an owner
  for the runtime guard would mirror the other concerns).
- §2.4 mentions histogram buckets "tuned per metric if the spec
  changes after first-week data" — consider naming the owner for
  this tuning pass (Dione?) so it is not forgotten post-merge.
- `hassaleh_version_info` is spelled with a short description
  ("Constant 1; labels carry info"). Worth saying explicitly that the
  convention is the Prometheus `_info` pattern, so future metrics
  follow suit.
- Consider adding a brief "observability for the observability stack"
  note: how do we know Loki itself is healthy? Grafana self-monitoring
  panel? Out-of-scope for v1 is a fine answer, but say so.

**Sign-off line:** Reviewed by gemini-reviewer, 2026-04-19.
