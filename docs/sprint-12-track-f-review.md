# Sprint 12 Track F — Inanna review

**VERDICT: CHANGES-REQUESTED**

## Criterion-by-criterion findings

### 1. Coverage matrix is real
**Mostly pass, but incomplete against plan §7 / S2a / S2b.**

`tests/test_observability.py` does exercise on/off behavior for logs, metrics,
and traces, and the matrix in the implement doc is not fabricated. Positive
coverage exists for:
- logs on/off
- metrics on/off
- traces on/off
- force-sampling
- a consolidated off-mode regression

However, the Track F suite does **not** cover all of the plan’s own named done
criteria for Track F and §7:
- Missing `test_tool_counter_increment` required by S2a
- Missing S2b checks for `neo4j-exporter up==1`, `cadvisor up==1`, and
  non-empty per-container metrics
- No smoke-harness level assertion for end-to-end stack presence (acknowledged
  as out-of-scope in the implement doc, but still part of the frozen §7 Done
  criteria)

This means the matrix is real, but **not sufficient to claim Track F satisfies
all plan-level acceptance hooks**.

### 2. OBS=off zero-cost regression is load-bearing
**Fail (blocking).**

The test `test_obs_off_regression_no_subsystem_setup_side_effects` verifies
post-call state after `obs_pkg.setup()` and `obs_tracing.setup_tracing()`, but
it does **not** verify import-time isolation.

Why this matters:
- The requirement explicitly calls for checking import-order isolation and
  guarding against import-time side effects.
- The test imports these modules at file import time:
  - `from hassaleh.obs import logging as obs_logging`
  - `from hassaleh.obs import metrics as obs_metrics`
  - `from hassaleh.obs import tracing as obs_tracing`
  - `from hassaleh.obs.metrics import ...`
- Therefore the suite cannot detect whether module import itself mutated:
  - Prometheus global/default registry state
  - global tracer-provider state
  - structlog global configuration

It only proves that *after teardown + explicit setup calls*, the resulting
runtime state is inert. That is weaker than S5 as phrased.

### 3. Each subsystem test asserts the right surface
**Partial pass, with two blockers.**

#### Logs
Good:
- `test_logs_on_emit_json_with_required_bindings` checks `service`, `env`,
  `version`, `msg`, and a representative contextual field.
- `test_logs_on_include_trace_id_when_span_active` checks `trace_id` and
  `traceparent` correlation while a span is active.

Gap:
- It does **not** assert the full plan-facing structured-log surface named in
  the prompt (`service/env/version/trace_id` bindings together in one test,
  plus logger/level/timestamp shape). Not fatal on its own.

#### Metrics
Good:
- `test_metrics_on_registers_full_catalog` asserts every name in
  `METRIC_NAMES` is rendered.
- `test_metrics_endpoint_exposes_catalog_via_daemon` hits the real daemon route
  registration path without opening a socket.

Gap:
- No check for actual Prometheus scrape semantics beyond body inclusion.

#### Traces
Blocking gaps:
- `test_traces_on_produce_spans_with_attributes` asserts `intent.lifecycle`
  and `intent.auth`, but the required §2/§3 stage set is broader and Track F
  does not verify the full canonical surface (`validate`, `execute`,
  `persist_result`, result-stage shape as implemented).
- `test_trace_force_sampling_overrides_zero_ratio` does correctly show
  `HASSALEH_TRACE_FORCE=1` flips sampling, but only for a single manually
  created span. It does not prove the force flag carries through the intended
  lifecycle/stage decorators.

### 4. Tests are fast and hermetic
**Pass.**

The suite is fast (~1.7s per implement doc), does not require docker-compose,
does not open a real socket (aiohttp `TCPSite.start` mocked), uses no sleeps,
and uses in-memory exporters / rendered bodies rather than network transport.

### 5. PII
**Pass.**

The test artifacts do not contain real credentials, raw Cypher payloads, or
user-content blobs. The identifiers used are synthetic (`agent-a1`,
`intent-42`, etc.). No obvious PII leak in the tests themselves.

## Blockers / Advisories

### Blockers
1. **S5 import-time isolation is not actually tested.**  
   Add a test that imports `hassaleh.obs`, `hassaleh.obs.metrics`, and
   `hassaleh.obs.tracing` inside a controlled subprocess or fresh-import
   boundary with `HASSALEH_OBS=off`, then asserts no Prometheus registry,
   no tracer-provider installation, and no structlog global configuration side
   effects arise merely from import order.

2. **Plan-required S2a / S2b coverage is missing.**  
   Add the named assertions required by the frozen plan:
   - tool counter increment test for `hassaleh_tool_invocation_total`
   - exporter/cAdvisor/up checks and non-empty per-container metrics
   If those belong to smoke rather than unit/integration, the plan or Track F
   implement doc must say so explicitly; right now the frozen plan names them as
   test obligations.

3. **Trace surface coverage is incomplete against the required lifecycle stages.**  
   Extend the trace assertions so Track F verifies the canonical stage set from
   the plan, not only `intent.lifecycle` + `intent.auth`.

### Advisories
- A single shared fixture resets state well; keep it.
- The daemon `/metrics` route test is well-shaped and hermetic.
- Log/trace correlation test is useful, but should eventually assert the
  daemon-resume path once traceparent propagation is fully integrated.

Track F is close, but not yet sufficient to close Sprint 12 on its own. Once
these blockers are fixed, Sprint 12 can close once Track E’s outstanding fix
cycle lands.
