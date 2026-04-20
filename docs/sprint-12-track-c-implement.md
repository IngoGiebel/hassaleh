# Sprint 12 — Track C implementation (Traces)

Status: implemented on `trunk`
Commit: `cf5d13a` (merge-order 3; daemon integration deferred to post-Track-A merge)
Plan: `docs/sprint-12-plan.md` (frozen v1.0.2, commit `c2aea90`)
Author track: **C — Traces**
Landed at: 2026-04-20 (Sprint 12, cycle 4 retry)

## Summary

Track C lands the standalone tracing substrate for Observability v1 without yet
wiring it into the daemon execution path. The implementation adds a dedicated
`src/hassaleh/obs/tracing.py` module that owns OpenTelemetry tracer-provider
setup, OTLP/HTTP export, sampling policy, span decorators, and W3C
`traceparent` propagation helpers. This matches the Sprint 12 merge plan: Track
C may author in parallel with Tracks A/B, but daemon integration waits until
Track A has merged its `obs.setup()` ownership and logging baseline.

## Files touched

- `src/hassaleh/obs/tracing.py`
  - `setup_tracing()` configures an OTEL `TracerProvider` with:
    - OTLP/HTTP exporter on `HASSALEH_OTLP_ENDPOINT`
    - baseline head-based sampling of `ParentBased(TraceIdRatioBased(rate))`
    - process-wide `HASSALEH_TRACE_FORCE=1` override → `AlwaysOn`
    - local `hint_error_prone()` context manager for temporary force-sampling
  - `trace_span(name)` decorator wraps sync/async callables in spans
  - stage helpers provide the canonical span names:
    - `intent.auth`
    - `intent.validate`
    - `intent.execute`
    - `intent.persist_result`
    - `intent.result`
  - `get_current_traceparent()` serializes the active span to W3C format
  - `extract_traceparent()` and `start_span_from_traceparent()` support daemon
    resume-from-Intent semantics once Track A integrates them
- `src/hassaleh/obs/__init__.py`
  - exports the tracing surface immediately
  - keeps Track C importable even if Track A logging symbols are absent or not
    yet merged cleanly
- `schema.cypher`
  - records the optional nullable `Intent.traceparent` contract-of-record
  - no Neo4j DDL needed because this is a schemaless nullable property
- `tests/test_obs_tracing.py`
  - sampling behaviour
  - decorator wrapping
  - traceparent round-trip / child-span parenting

## Security / correctness notes

- Sampling is strictly **head-based** in v1, per the frozen plan.
- `HASSALEH_TRACE_FORCE=1` is process-wide and intended for smoke tests.
- `traceparent` is nullable and optional, preserving backward compatibility for
  legacy SDK callers.
- This change intentionally does **not** integrate tracing into daemon intent
  processing yet; that merge-order dependency remains with Track A.

## Schema note

No new Neo4j constraint or index is required. The schema change is documentary:
`Intent.traceparent` is an optional string property carrying the W3C trace
context header value when SDK-side tracing is enabled.

## Verification

Targeted test slice:

```bash
pytest tests/test_obs_tracing.py -x -v
```

Expected coverage:
- ratio sampling vs force override
- `@trace_span` wraps a callable in a recorded span
- `traceparent` generated in one span tree resumes correctly in another
