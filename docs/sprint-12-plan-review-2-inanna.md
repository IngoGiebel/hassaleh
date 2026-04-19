# Sprint 12 Plan Review — Round 2 — Inanna

**Reviewer:** Inanna 🛡️
**Date:** 2026-04-19
**Target:** docs/sprint-12-plan.md (v1, commit 377f2ed)
**Scope:** Re-verify only the Round-1 CRs and non-blocking observations from
`docs/sprint-12-plan-review-1-inanna.md`. Per Round-2 protocol, no *new*
CRs are raised unless v1 introduced a regression.

## Verdict: clean

All seven Round-1 CRs are resolved. All six non-blocking observations are
either resolved or carried forward with an explicit owner. No regressions
were introduced by the v1 edits. The plan is ready to freeze as the source
of truth for the Sprint-12 orchestrator cron.

One minor editorial ambiguity in §3.2.2's closing sentence is flagged
below as a non-blocking observation (does not affect implementability).

## CR-by-CR verification

### CR-1 — R7/R8 testable markers beyond "shown on dashboard"
**Status:** resolved
**Resolution location:** §1 Success Criteria — new S2a and S2b.
**Rationale:** S2a ties R7 to a concrete pytest assertion
(`test_tool_counter_increment` increments `hassaleh_tool_invocation_total`
by exactly 1 per `invoke_command` call). S2b ties R8 to three distinct
assertions: `neo4j-exporter up==1`, `cadvisor up==1`, and non-empty
per-container metrics for `hassaleh-daemon`. Both S2a/S2b are wired into
`tests/test_observability.py`, which S6's drift-lint already protects from
silent removal. Visualization-only verification is no longer the backstop.

### CR-2 — SDK→daemon trace propagation architectural mismatch
**Status:** resolved
**Resolution location:** §2.4 ("SDK ↔ daemon trace propagation"), §3.3
("Trace propagation"), §7 Done criteria, Track C deliverables in §5.
**Rationale:** v1 correctly acknowledges that the SDK writes Intent nodes
directly to Neo4j with no HTTP RPC, and adopts option (a) from my Round-1
CR: `traceparent` becomes an **optional nullable** string property on
Intent nodes in `schema.cypher` (W3C format, backward-compatible for
legacy callers). The SDK captures the active span's context on
`submit_intent()`; the daemon extracts it via
`TraceContextTextMapPropagator.extract` when picking up the intent, and
emits its processing spans as children. Fresh-trace fallback is
documented when `traceparent` is absent. The schema migration is
explicitly owned by Track C, and §7 Done criteria includes a
`schema.cypher`-update smoke. Clean resolution.

### CR-3 — logging.basicConfig collision in daemon.py:33
**Status:** resolved
**Resolution location:** §2.4 ("Logging-handler collision"), §5 merge
order, §10 change-log item 3.
**Rationale:** v1 makes three correct decisions: (1) the
`logging.basicConfig(...)` call at `daemon.py:33` is removed; (2)
`obs.setup()` becomes the sole owner of root-handler configuration; (3)
when `HASSALEH_OBS=off`, `obs.setup()` still installs a minimal
text-formatter root handler so legacy stderr output is preserved — no
silent formatter-loss regression. This is explicitly designated Track A's
*first* task in §2.4, and §5 pins Track A as merge-order position 1
("every other track relies on the structlog handler being installed").
The S5 no-op regression test (`test_obs_off_is_noop`) covers the off-mode
path.

### CR-4 — Error-type taxonomy not matching src/hassaleh/errors.py
**Status:** resolved
**Resolution location:** §3.2.2 ("Error-type taxonomy").
**Rationale:** v1 provides the explicit exception-class → `type`-label
table I requested, grounded in the actual `src/hassaleh/errors.py`
classes plus stdlib raises:

- `AuthenticationError`, `AgentNotFoundError`, `AgentDisabledError`,
  `HeartbeatTokenMismatchError` → `auth`
- `CapabilityNotFoundError`, `CapabilityDeniedError`,
  `CapabilityParamError` → `capability`
- `AccessDeniedError`, stdlib `PermissionError` → `permission`
- `ValueError`, `TypeError` → `validation`
- stdlib `TimeoutError` → `timeout`
- Neo4j driver errors → `graph`
- Everything else → `internal`

The mapping function lives in `obs/metrics.py` under Track B. The
`source` label enum is also closed. §3.4 sampling boost uses the same
table, so the predicate maps to actually-raised exceptions. The
deliberate single `internal` catchall for uncaught escapees is a
sensible triage signal.

Small note: the `type` label values in §3.2.2 are lowercase (`auth`,
`capability`, `internal`, etc.) while the closing paragraph refers to a
capitalised `Internal` bucket. The metric contract is clear from the
table; the prose is merely slightly looser. Filed as a non-blocking nit
below.

### CR-5 — Cypher pattern classification hand-waved
**Status:** resolved
**Resolution location:** §3.2.3 ("Cypher-pattern label classification").
**Rationale:** v1 picks option (a) from my Round-1 CR unambiguously:
callers pass an explicit `pattern: str` kwarg drawn from a closed list
in `src/hassaleh/obs/cypher_patterns.py`. The enum is bounded at ≤50 by
construction with an `__other__` catchall that logs WARN so growth is
intentional, not silent. `obs/metrics.py` validates `pattern in
CYPHER_PATTERNS` at runtime. Track B owns the kwarg rollout at call
sites as part of the instrumentation. The cardinality cap is now
enforceable, not aspirational. Clean.

### CR-6 — Sampling §3.4 self-contradictory (head-based vs tail-based)
**Status:** resolved
**Resolution location:** §3.4 ("Sampling strategy") + §1
Non-Requirements.
**Rationale:** v1 declares §3.4 "strictly **head-based sampling**" in
the opening sentence and explicitly moves "2× p99 duration boost" into
§1 Non-Requirements ("Tail-based trace sampling, including 'sample
100 % of traces whose duration > 2×p99'. v2"). The boost-to-100 %
predicate now keys on caller-supplied type classification via
`obs.tracing.hint_error_prone()`, which is computable at span-start —
no time-travel required. §3.4's closing paragraph explicitly states
"2 × p99 duration boost was dropped in v1". The error-post-sampling
log-only fallback is a sensible v1 compromise that preserves
error-correlation via `trace_id` without needing tail sampling.

### CR-7 — Alert-rule storage location conflict
**Status:** resolved
**Resolution location:** §2.2 ("Alert-rule storage decision"), §3.6
("Alert contract"), §3.2.1 ("Recording rules (Prometheus side)").
**Rationale:** v1 declares Grafana unified alerts under
`grafana/provisioning/alerting/` as the *source of truth* for alert
rules and contact points. Prometheus `rules/` is now scoped to
**recording rules only** (e.g., the R5 uptime-SLO) plus HA-mandated
alerting rules (which for v1 means none). §3.6 is internally consistent
with §2.2. §3.2.1's concrete recording-rule YAML for
`hassaleh:agent_uptime_ratio:1h` and `hassaleh:intent_duration_seconds:p99_1h`
reinforces the split and gives Track D an unambiguous target.

## Non-blocking observations — status

| Round-1 item | Status in v1 | Where |
|--------------|--------------|-------|
| PII: redact Cypher param `lookup` / `api_key*` regardless of source | **resolved** | §3.1 PII policy, bullet 3 |
| Service enum: add `hassaleh-intent-sdk` | **resolved** | §3.1 service enum (closed, validator-enforced), §2.4 SDK log/trace delivery |
| Bcrypt histogram buckets (kill dead `0.01` / `0.05`) | **deferred-with-owner** | §8 "Histogram-bucket tuning" row owned by Dione as v1.1 patch; §10 change-log explicitly acknowledges v0 defaults retained |
| Heartbeat-miss instrumentation point named | **partially resolved** | Metric is in §3.2 table; exact instrumentation point (daemon sweep loop on `agent.lifecycle` transitions at `heartbeat_sdk.py:168`) is not explicitly named. Track B has enough from the metric contract to find it, but naming it would save one round-trip. Filed below. |
| `make observability-smoke` → concrete script | **resolved** | §7 Done criteria names `scripts/observability-smoke.sh` + `pytest -m smoke tests/test_observability.py`; §8 row matches |
| Backward-compat shared ownership | **resolved** | §8 "Backward-compat with legacy agents" now reads "Track A (primary; B+C satisfy invariants)" with an explicit clause that Track A enforces the pattern and B/C must demonstrate no-op behaviour in their own tests |

## Regression check — issues introduced by v1

None blocking. One minor editorial ambiguity was introduced by the new
material:

- **§3.2.2 wording nit (non-blocking).** The closing paragraph says "any
  exception that maps to `type ∈ {internal, graph, timeout}` forces a
  sampled trace." In a strictly head-based sampler the decision is made
  *at span start* based on a caller hint (`hint_error_prone()`), not at
  exception time. §3.4 makes this clear; §3.2.2's wording could be read
  as retroactive sampling. Suggested minor edit (non-blocking):
  "…is pre-classified as likely to raise type ∈ {internal, graph,
  timeout}…". Does not affect Track B's or Track C's implementation
  contract, because §3.4 is unambiguous and is the normative section.

- **§3.2.2 case-ness nit (non-blocking).** The `type` enum in the table
  is lowercase (`auth`, `internal`, …) while the prose mentions a
  capitalised `Internal` bucket. The table is the contract; prose
  should match. v1.0.1 editorial fix.

## Non-blocking observations carried into implementation

1. **Heartbeat-miss instrumentation site (carried from Round-1).** Track B
   can find it from the codebase, but naming it in §8 (e.g., "the daemon
   sweep loop that transitions `agent.lifecycle` between
   `active|stale|inactive`") would save a conversation. Purely
   documentation-side, does not block merge.
2. **§3.2.2 prose ↔ §3.4 head-based wording** (described above).
3. **R4 slow-query log call-site hook** — v1 explicitly carries this
   under "Remaining non-blocking items" in §10 with Track A picking the
   hook location at implementation time. Acceptable.

## Summary of the Round-1 → Round-2 delta

| Round-1 CR | Round-2 status | Blocking? |
|------------|----------------|-----------|
| CR-1 R7/R8 markers | resolved (S2a, S2b) | no |
| CR-2 trace propagation | resolved (Intent.traceparent property) | no |
| CR-3 basicConfig collision | resolved (Track A first task) | no |
| CR-4 error taxonomy | resolved (§3.2.2 table) | no |
| CR-5 cypher pattern classification | resolved (§3.2.3 closed enum + kwarg) | no |
| CR-6 sampling contradiction | resolved (head-based only; 2×p99 → v2) | no |
| CR-7 alert-rule storage | resolved (Grafana unified = SoT) | no |

Seven for seven, with no regressions beyond two editorial nits. The plan
is implementable as-is.

**Sign-off line:** Reviewed by Inanna, 2026-04-19. Verdict: **clean**.
Plan ready to freeze.
