# Sprint 12 Track F — Observability integration tests

Date: 2026-04-21
Branch: `trunk`
Plan reference: `docs/sprint-12-plan.md` §1 S5, §3.1, §3.2, §3.3, §3.4, §5 Track F, §7
Status: implemented and validated with `pytest tests/test_observability.py -v`

## Scope

Track F owns the cross-subsystem integration suite for Observability v1. The
suite verifies that the behavioural contracts from plan §3 hold end-to-end
when `HASSALEH_OBS` flips between `on` and `off`, without depending on the
external compose stack (Loki, Prometheus, Tempo, Grafana) delivered by
Track E. The compose-stack end-to-end smoke is a separate deliverable
(`scripts/observability-smoke.sh` — not covered here).

Out of scope for this file:
- Drift-lint script (`scripts/check-observability-drift.py`), plan §8 S6.
- Docker-dependent smoke harness (plan §7).

## Files

| File | Status | Purpose |
|------|--------|---------|
| `tests/test_observability.py` | new | Ten cross-subsystem integration tests. |
| `docs/sprint-12-track-f-implement.md` | new | This document. |

## Test strategy

Each test exercises one of the four behavioural pillars from the task brief:

1. **Logs** — assert the structured JSON schema from §3.1 when obs is on,
   and a plain-text stderr line with no structlog processor activity when
   obs is off.
2. **Metrics** — assert the frozen §3.2 catalog is present in the rendered
   `/metrics` body when obs is on, and that the daemon does not register
   a `/metrics` route and `get_metrics_registry()` returns `None` when off.
3. **Traces** — assert spans carry the expected names/attributes when obs
   is on, that `HASSALEH_TRACE_FORCE=1` defeats a zero sample rate
   (required by plan S1), and that `setup_tracing()` is a no-op when off.
4. **Regression (S5)** — a single end-to-end test that drives
   `obs.setup("hassaleh-daemon")` with `HASSALEH_OBS=off` and verifies
   zero subsystem state is created: no Prometheus registry, no OTEL
   `TracerProvider`, no structlog pipeline invocations.

### Isolation model

Test isolation is enforced by a single `autouse` fixture,
`_reset_obs_state`:

- Deletes every `HASSALEH_*` env var before each test. `monkeypatch` ensures
  the original environment is restored after the test regardless of outcome.
- Calls `shutdown_metrics()` and `obs_tracing.shutdown_tracing()` so the
  module-level `_STATE` / `_CONFIGURED_PROVIDER` singletons from the
  previous test do not leak. Without this, `setup_metrics()` would
  short-circuit on the cached state and silently swallow the new env-var
  intent of the current test.
- Clears the per-request context bag used for log/metric correlation.

This is the same pattern used by `tests/test_obs_metrics.py` and
`tests/test_obs_tracing.py`; Track F consolidates it into one suite that
also flips `HASSALEH_OBS` per test.

### Trace-id correlation

`test_logs_on_include_trace_id_when_span_active` exercises the §3.1 +
§3.3 joint contract: while a span is active, an application log line can
carry `trace_id` and `traceparent` fields that reference the same trace
context `get_current_traceparent()` exposes. It is a positive assertion
that the two subsystems are cooperating, not a claim that auto-injection
is in place.

### Daemon metrics endpoint

`test_metrics_endpoint_exposes_catalog_via_daemon` drives the real
`HassalehDaemon._start_health_endpoint()` path to verify that the
`/metrics` route exposes every name in `METRIC_NAMES`. The test mocks
`aiohttp.web.TCPSite.start` to avoid opening a socket (CI safety) and
mocks `_refresh_metrics_snapshot` to avoid needing a live Neo4j driver.
The assertion is exclusively over the rendered Prometheus body, not the
network transport.

## Coverage matrix

| Subsystem | `HASSALEH_OBS=on` assertion | `HASSALEH_OBS=off` assertion |
|-----------|----------------------------|------------------------------|
| Logging (schema) | `test_logs_on_emit_json_with_required_bindings` | `test_logs_off_uses_plain_text_and_bypasses_structlog` |
| Logging (trace correlation) | `test_logs_on_include_trace_id_when_span_active` | covered by regression test |
| Metrics (catalog) | `test_metrics_on_registers_full_catalog` | `test_metrics_off_is_noop_and_hides_endpoint` |
| Metrics (endpoint) | `test_metrics_endpoint_exposes_catalog_via_daemon` | covered by regression test |
| Tracing (span emission) | `test_traces_on_produce_spans_with_attributes` | `test_traces_off_is_noop` |
| Tracing (force sampling) | `test_trace_force_sampling_overrides_zero_ratio` | n/a (force only meaningful when on) |
| Cross-cutting regression (S5) | — | `test_obs_off_regression_no_subsystem_setup_side_effects` |

Total: 10 test cases, 7 positive (obs=on) and 3 negative (obs=off), plus
one consolidated S5 regression covering all three subsystems.

## Pytest output

Command:

```bash
cd ~/projects/hassaleh && python -m pytest tests/test_observability.py -v
```

Observed result:

```text
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.0.3, pluggy-1.6.0
rootdir: /home/uranus/projects/hassaleh
configfile: pyproject.toml
plugins: asyncio-1.3.0
asyncio: mode=Mode.AUTO

tests/test_observability.py::test_logs_on_emit_json_with_required_bindings PASSED [ 10%]
tests/test_observability.py::test_logs_on_include_trace_id_when_span_active PASSED [ 20%]
tests/test_observability.py::test_logs_off_uses_plain_text_and_bypasses_structlog PASSED [ 30%]
tests/test_observability.py::test_metrics_on_registers_full_catalog PASSED [ 40%]
tests/test_observability.py::test_metrics_off_is_noop_and_hides_endpoint PASSED [ 50%]
tests/test_observability.py::test_metrics_endpoint_exposes_catalog_via_daemon PASSED [ 60%]
tests/test_observability.py::test_traces_on_produce_spans_with_attributes PASSED [ 70%]
tests/test_observability.py::test_trace_force_sampling_overrides_zero_ratio PASSED [ 80%]
tests/test_observability.py::test_traces_off_is_noop PASSED              [ 90%]
tests/test_observability.py::test_obs_off_regression_no_subsystem_setup_side_effects PASSED [100%]

============================== 10 passed in 1.71s ==============================
```

Summary line: `10 passed in 1.71s`.

## Deviations from the plan

None. The suite is strictly additive against the contracts declared in
§3.1, §3.2, §3.3, §3.4 and the S5 regression requirement in §1.

## Follow-up items

- The end-to-end smoke suite that boots the compose stack and asserts
  logs/metrics/traces round-trip all the way to Grafana belongs to a
  future `scripts/observability-smoke.sh` invocation and is explicitly
  out of scope for Track F.
- `scripts/check-observability-drift.py` (plan §8 S6) is still
  outstanding; it will live alongside this file once landed.
- Once the SDK-side `submit_intent` starts writing `traceparent` into
  Intent nodes (Track C integration follow-up), `test_logs_on_include_trace_id_when_span_active`
  can be extended to assert the daemon resumes the trace via
  `start_span_from_traceparent`.
