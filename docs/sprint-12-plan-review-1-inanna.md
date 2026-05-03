# Sprint 12 Plan Review — Round 1 — Inanna

**Reviewer:** Inanna 🛡️
**Date:** 2026-04-19
**Target:** docs/sprint-12-plan.md (v0, commit 4e301cf)
**Scope:** §9.2 review criteria (Requirements, Architecture, Interface,
Cross-cutting). PLG+Tempo stack choice, separate-deployment decision,
existence of a sampling strategy, and the meta-review workflow itself
are explicitly off-limits per §9.4.

## Verdict: change-req

The plan's structure and ambition are sound, and the Cross-cutting section
(§8) is unusually disciplined. But three artefacts (auth telemetry, error
classification, and trace propagation) hard-reference codebase symbols and
mechanisms that do not match the current Hassaleh source, and the sampling
strategy contradicts itself. A track author starting from this document
today would either have to invent answers or come back to ask. Six concrete
items below must be resolved before the plan can clear the "implementable
without another conversation" bar in §9.2.

## Findings

### §1 Requirements

- **PASS — "so what?" coverage.** Each of R1–R8 carries a one-line
  motivation that explains the operational capability gained. The table at
  §1 lines 35–44 reads cleanly.
- **PASS (marginal) — testable markers for R1–R6.** S1 maps to R2 (trace
  end-to-end), S3 covers R3/R5/R6/R4 via the four named alert rules, and
  S2 ("dashboard visualizes R1–R8") catches the rest by reference. Done
  criteria in §7 add `make observability-smoke` as a behavioural gate.
- **CR-1 — R7 and R8 have no distinct testable marker beyond "shown on the
  dashboard."** The S2 catch-all is a verification fig leaf for tool-stats
  (R7) and resource-utilization (R8). Add per-requirement assertions —
  e.g., S2a "tool invocation counter increments by exactly 1 per
  `invoke_command` call observed in `tests/test_observability.py`" and S2b
  "neo4j-exporter `up==1` is asserted by smoke test" — or accept that R7/R8
  are visualization-only deliverables and say so.
- **PASS — Non-Requirements section.** The "explicitly out of v1" list (lines
  46–53) is concrete and prevents scope creep. Good.

### §2 Architecture

- **PASS — stack and ports specified.** §2.1–§2.3 name every component, list
  the bridge network, and pin the integration ports (`:9100`, `:4318`,
  `:2004`).
- **PASS — repo layout for the observability stack.** The tree at §2.2 is
  copy-pasteable for Track E.
- **CR-2 — SDK→daemon trace propagation does not match the current
  architecture.** §2.4 + §3.3 specify W3C `traceparent` over outbound HTTP
  calls from the SDK, but the SDK writes Intent nodes directly to Neo4j
  (`sdk.py:225-321`); there is no HTTP RPC channel between SDK and daemon.
  R2's "every intent gets a `trace_id` end-to-end" therefore cannot be
  achieved by header propagation alone. Either:
  (a) add a `traceparent` property on the Intent node (schema change), with
      the daemon resuming the trace from that property, or
  (b) explicitly scope tracing to in-process spans only and acknowledge
      that daemon-side execution gets a fresh trace linked by `intent_id`.
  Pick one and document; without this, Track C cannot meet R2.
- **CR-3 — Logging boot-order collision.** `daemon.py:33` calls
  `logging.basicConfig(...)` at module-import time, before `obs.setup()`
  could run. `basicConfig` is a no-op once the root handler exists, so
  Track A will silently get a non-JSON formatter unless the call is removed
  or the obs setup tears down and rebuilds handlers. Call this out as
  Track A's first task and decide which side gives way.
- **PASS — opt-in semantics.** §2.4's `HASSALEH_OBS=on|off` plus a no-op
  `obs.setup()` satisfies S5 backward-compat cleanly.
- **PASS — resource profile.** ~900 MB / ~2 GB/day fits the 16 GB host;
  numbers are conservative rather than optimistic.

### §3 Interface spec

- **PASS — log schema (§3.1).** Mandatory + optional fields are explicit;
  `extra.*` namespace prevents top-level drift.
- **PASS (with one nit) — PII policy.** `api_key`, `api_key_hash`,
  `api_key_lookup` are correctly forbidden. Nit: `api_key_lookup` enters
  Cypher as the `$lookup` parameter at `sdk.py:167` and
  `heartbeat_sdk.py:119`. The "redact `cypher_params` containing
  user-supplied strings" rule won't catch it because `lookup` is server-
  computed. Add: any Cypher parameter named `lookup` or `api_key*` is
  hashed/redacted regardless of source. Otherwise a future "log all
  cypher_params for debugging" patch will leak the SHA-256 of every API
  key in plaintext.
- **CR-4 — error taxonomy in R6 and §3.4 doesn't match `errors.py`.**
  R6 (line 42) and the sampling boost in §3.4 ("error of type ∈ `{Internal,
  GraphError, Timeout}`") reference type buckets that do not exist in the
  codebase. `src/hassaleh/errors.py` defines `AuthenticationError`,
  `CapabilityNotFoundError`, `CapabilityDeniedError`, `CapabilityParamError`,
  `AccessDeniedError`, `AgentNotFoundError`, `AgentDisabledError`,
  `HeartbeatTokenMismatchError`. Plus stdlib `PermissionError` and
  `TimeoutError` are raised in `sdk.py:204,233,365,403`. Add an explicit
  mapping table — exception class → bucket label — so that
  `obs/metrics.py` doesn't have to invent one and so the `sampling=100%`
  predicate in §3.4 maps to actual raised exceptions.
- **CR-5 — Cypher pattern classification is hand-waved.** §3.2 line 269–270
  says patterns are "classified by template, not by rendered query," but
  doesn't say *how*. The current `sdk.py::query()` accepts arbitrary cypher
  strings (`sdk.py:181-233`); there is no pattern-id annotation on the call
  site. Track B implementer needs to know whether: (a) callers must pass
  an explicit `pattern: str` argument and `query()` grows a kwarg, (b)
  obs/metrics.py normalizes the cypher (strip whitespace, dollar-replace
  literals) and hashes it, or (c) a curated dispatch table maps known
  query strings to short labels. Pick one. Without this, the cardinality
  cap (≤50) cannot be enforced.
- **CR-6 — sampling §3.4 is internally contradictory.** The boost-to-100%
  rule "any intent whose root-span duration exceeds 2×p99 of the last 24h"
  requires a sampling decision *after* the span finishes — i.e., tail-based
  sampling — but the same section's last bullet says "tail-based sampling
  is v2." Pick one: drop the duration-boost criterion from v1, or move it
  to v2 with the rest of tail-sampling. Head-based samplers cannot make
  this decision at span-start.
- **PASS — alert example.** §3.6's YAML stub shows the contract: `expr`,
  `for`, `labels.severity`, `annotations.runbook`, and `suggested_action`.
  Track D can produce conformant alerts.
- **CR-7 — alert-rule storage location is ambiguous.** §2.2 puts alert
  rules in `prometheus/rules/hassaleh-alerts.yml` (Prometheus-side
  recording/alerting rules); §3.6 puts them in
  `grafana/provisioning/alerting/*` (Grafana-side unified alerts). These
  are two different stores. Pick one as the source of truth, or document
  the split (e.g., recording rules live in Prometheus, contact-point/route
  config lives in Grafana).
- **PASS — dashboard UID contract.** Stable dashboard + panel UIDs is the
  right call for runbook linkability.
- **NIT — bcrypt histogram bucket boundaries.** `0.01` and `0.05` are
  effectively dead buckets for bcrypt (cost-12 ≈ 100 ms). Consider
  replacing them with finer resolution near 0.1 and 0.25 once first-week
  data lands; documented as a v1.1 tweak.
- **NIT — service enum in §3.1.** Lists `hassaleh-daemon`, `hassaleh-sdk`,
  `hassaleh-heartbeat-sdk`. There is also `IntentSDK` in
  `src/hassaleh/intent_sdk.py` (still live per `tests/test_mvp_intent.py`).
  Add `hassaleh-intent-sdk` or document that it folds into `hassaleh-sdk`.
- **NIT — heartbeat-miss instrumentation point (R5).** The current
  codebase infers misses via the daemon sweep transitioning
  `agent.lifecycle` between `active|stale|inactive`
  (`heartbeat_sdk.py:168`). The spec asks for an explicit
  `hassaleh_heartbeat_missed_total` counter — name the instrumentation
  point (presumably the daemon sweep loop) so Track B knows where to
  increment it.

### §8 Cross-cutting

- **PASS — every item has a named owner.** All 13 rows in the §8 table
  carry an explicit owner. The discipline is visible.
- **NIT — three rows share owners.** "Backward-compat with legacy
  agents" → "Track A/B/C" is shared ownership; in practice this means no
  one. Either pick a primary owner who arbitrates the cross-track API or
  re-cast it as a constraint each track must satisfy independently
  (matching the wording in §2.4 about `HASSALEH_OBS=off` no-op).
- **NIT — §7 references `make observability-smoke`.** No `Makefile`
  exists in the repo today. Specify the script location (e.g.,
  `scripts/observability-smoke.sh` or `pytest -m smoke
  tests/test_observability.py`) so Track F isn't free-form.

## Required follow-ups (change-req)

1. **CR-1 (§1):** Add per-requirement testable markers for R7
   (tool-execution stats) and R8 (resource utilization) — currently both
   collapse into S2 "shown on dashboard" with no functional gate.
2. **CR-2 (§2.4 + §3.3):** Specify SDK→daemon trace propagation. The SDK
   writes Intent nodes via Neo4j, not HTTP. Either (a) carry `traceparent`
   as a property on the Intent node and have the daemon resume the trace,
   or (b) explicitly scope tracing to in-process spans linked by
   `intent_id`. Document the schema implication if (a).
3. **CR-3 (§2.4):** Resolve the `daemon.py:33` `logging.basicConfig`
   collision. Either remove the basicConfig and let `obs.setup()` own
   handler installation, or make `obs.setup()` explicitly tear down +
   rebuild root handlers. Decide which and reflect it in the Track A
   deliverables list (§5).
4. **CR-4 (§3.2 + §3.4):** Reconcile the error-type taxonomy with
   `src/hassaleh/errors.py`. Provide an exception-class → bucket-label
   table (e.g., `AuthenticationError → auth`, `Capability*Error →
   capability`, `AccessDeniedError → permission`, `Heartbeat*Error →
   heartbeat`, stdlib `TimeoutError → timeout`, anything else → `internal`).
   Apply the same taxonomy to §3.4's "boost to 100%" predicate so the
   types are guaranteed to be raised somewhere.
5. **CR-5 (§3.2):** Specify how Cypher patterns are assigned a `pattern`
   label. Three options (caller-supplied kwarg, normalized-cypher hash,
   curated dispatch table) — pick one so Track B has an unambiguous
   instrumentation contract and the ≤50 cardinality cap is enforceable.
6. **CR-6 (§3.4):** Fix the head-vs-tail sampling contradiction. Either
   remove the "duration > 2×p99" boost from v1, or relocate it to v2
   alongside the tail-sampling deferral.
7. **CR-7 (§2.2 + §3.6):** Resolve the alert-rule storage location
   conflict between `prometheus/rules/` and `grafana/provisioning/alerting/`.
   Pick one source of truth or document the split between recording rules
   (Prometheus) and notification routes (Grafana).

## Non-blocking observations

- §3.1 PII policy: extend the `api_key*` redaction rule to cover any
  Cypher parameter named `lookup` (server-computed SHA-256 of an API key)
  so a future "log all cypher_params" patch can't leak hashes.
- §3.1 service enum: add `hassaleh-intent-sdk` or note its folding into
  `hassaleh-sdk`.
- §3.2 bcrypt histogram: lower buckets (`0.01`, `0.05`) are wasted on
  bcrypt cost-12; revisit boundaries after first-week data per the spec's
  own note (line 276).
- §5 Track F (Tests) and §7 `make observability-smoke`: pick a concrete
  invocation location.
- §8 "Backward-compat with legacy agents" lists three owners (A/B/C).
  Designate a primary or restate as a per-track invariant.
- R5 heartbeat-miss counter: name the instrumentation point (likely the
  daemon sweep loop) so Track B knows where to increment.

**Sign-off line:** Reviewed by Inanna, 2026-04-19.
