# Sprint 12 — Track C (Tracing) Review

**Reviewer:** Inanna (Security & API)
**Target:** Track C (commit `cf5d13a`), Schema (commit `0169627`)

## 1. Sampling (§3.4)
**CR:** The requirement to drop `/health` and `/metrics` endpoints to 0% sampling (unless `HASSALEH_TRACE_FORCE=1`) is missing. `_build_sampler` and `_ForceAwareSampler` only implement the baseline ratio and the force-to-100% overrides. There is no logic to inspect the span name or target to drop health-check spans.
*Note:* The head-based `hint_error_prone()` logic is correctly implemented via contextvars, resolving Round-1 CR-6 cleanly.

## 2. Traceparent Propagation (§2.4)
**CLEAN:** W3C traceparent formatting (`00-<trace-id>-<span-id>-<flags>`) is correct. `schema.cypher` correctly documents `Intent.traceparent` as nullable. Daemon-side resumption gracefully handles missing/null traceparents by yielding a fresh trace (resolving Round-1 CR-2).

## 3. Span Conventions (§3.3)
**CLEAN:** The stage contexts (`intent.auth`, `intent.validate`, `intent.execute`, `intent.persist_result`) exactly match the table. Root span creation (`intent.lifecycle`) works as intended via `start_span_from_traceparent`.

## 4. PII Safety (§8)
**CR:** There is no mechanism in `tracing.py` (e.g., a custom `SpanProcessor`) to redact or block PII attributes such as `api_key*`, full cypher params, or raw `db.statement` values from being exported. While Track A handles logging redaction, Track C must ensure traces are equally safe from leaking secrets.

## 5. Graceful Shutdown (§8)
**CR:** The `shutdown_tracing(timeout_millis: int = 5000)` function accepts a timeout parameter but discards it. The calls to `_CONFIGURED_PROVIDER.shutdown()` and `_CONFIGURED_PROCESSOR.shutdown()` do not receive the timeout value, meaning the 5-second budget contract is not actually enforced on the OTEL export flush. 

## 6. Opt-in Behavior (§2.4)
**CLEAN:** `is_obs_enabled()` defaults to false and successfully gates the initialization and decorator context managers into zero-overhead pass-throughs.

## 7. Test Coverage
**CR:** `tests/test_obs_tracing.py` effectively covers the basic sampling ratios and `hint_error_prone`. However, it lacks tests for:
- Drop-to-0% logic for health-check endpoints.
- PII redaction/scrubbing on span attributes.
- Exporter timeout adherence during shutdown.

VERDICT: CR (Sampling drop-to-0% missing, PII redaction missing, Shutdown timeout ignored)