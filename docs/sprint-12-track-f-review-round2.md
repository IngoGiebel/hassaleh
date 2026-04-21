**VERDICT: CHANGES-REQUESTED**

Round-2 scope was limited to re-checking the three Round-1 blockers against
`tests/test_observability.py` after commits `967e427` (tests) and `6d5e74e`
(docs), then running the test file locally.

## B1 — S5 import-time isolation subprocess test
**Status: PARTIALLY RESOLVED IN CODE, NOT RESOLVED IN EXECUTION**

The new test exists and is shaped correctly in source:
- `test_obs_off_import_time_isolation()` launches a fresh subprocess with
  `HASSALEH_OBS=off`
- the subprocess imports `hassaleh.obs`, `hassaleh.obs.metrics`, and
  `hassaleh.obs.tracing`
- it asserts:
  - no `hassaleh_*` collectors were added to `prometheus_client.REGISTRY`
  - no SDK `TracerProvider` was installed
  - `structlog.is_configured()` is false

Relevant excerpt:

```python
result = subprocess.run(
    [sys.executable, "-c", _IMPORT_TIME_ISOLATION_SCRIPT],
    env=env,
    capture_output=True,
    text=True,
    timeout=30,
    check=False,
)
```

```python
if hassaleh_collectors:
    sys.exit(2)
...
if isinstance(provider, SdkTracerProvider):
    sys.exit(4)
...
if structlog.is_configured():
    sys.exit(5)
```

However, local execution re-opened this blocker: the subprocess test **times
out after 30 seconds** in the project virtualenv instead of returning 0.
Observed failure from local run:

```text
FAILED tests/test_observability.py::test_obs_off_import_time_isolation - subprocess.TimeoutExpired
Command '['/home/uranus/projects/hassaleh/.venv/bin/python', '-c', '...']' timed out after 30 seconds
```

That means B1 is not actually closed yet in a passing test suite.

## B2 — S2a tool counter + explicit S2b deferral
**Status: RESOLVED**

Confirmed `test_tool_counter_increment()` now asserts exact +1 counter movement
per `record_tool_invocation()` call and checks the paired duration histogram
count moves in lockstep.

Relevant excerpt:

```python
before = registry.get_sample_value("hassaleh_tool_invocation_total", labels) or 0.0
record_tool_invocation(... duration_seconds=0.012)
after_one = registry.get_sample_value("hassaleh_tool_invocation_total", labels)
assert after_one - before == 1.0
```

```python
duration_count = registry.get_sample_value(
    "hassaleh_tool_invocation_duration_seconds_count",
    {"command": "invoke_command"},
)
assert duration_count == 2.0
```

Also confirmed the S2b deferral is explicit and not silent omission:
- `test_neo4j_exporter_up`
- `test_cadvisor_up`
- `test_per_container_metrics_non_empty`

all carry `@pytest.mark.skip` with a concrete Sprint-13 smoke-harness reason.

## B3 — canonical trace stage set + decorator-path force sampling
**Status: RESOLVED IN CODE**

Confirmed the canonical stage set is now covered beyond `intent.lifecycle` and
`intent.auth`.

Relevant excerpt:

```python
@pytest.mark.parametrize(
    ("helper", "expected_name"),
    [
        (obs_tracing.auth_stage, "intent.auth"),
        (obs_tracing.validate_stage, "intent.validate"),
        (obs_tracing.execute_stage, "intent.execute"),
        (obs_tracing.persist_stage, "intent.persist_result"),
        (obs_tracing.result_stage, "intent.result"),
    ],
)
def test_canonical_lifecycle_stage_emits_named_span(...):
```

Decorator-path force sampling is also now present, not only a manual span:

```python
@obs_tracing.trace_span("intent.execute")
def _decorated_stage():
    return "ok"
```

with `HASSALEH_TRACE_FORCE=1` and `sample_rate=0.0`, asserting the span is
still recorded.

## Local pytest run
Requested command:

```bash
cd ~/projects/hassaleh && python -m pytest tests/test_observability.py -v
```

Host `python` could not run because `pytest` is not installed there:

```text
/usr/bin/python: No module named pytest
```

Re-ran meaningfully in the project virtualenv:

```bash
. .venv/bin/activate && python -m pytest tests/test_observability.py -v
```

Result:

```text
1 failed, 17 passed, 3 skipped in 59.61s
```

The sole failure was `test_obs_off_import_time_isolation` timing out after 30s.

## New regressions introduced by the fix
1. **B1 test flakiness / deadlock risk** — the new subprocess-based isolation
   test is structurally correct, but in this environment it hangs long enough
   to fail the suite. That is now the only remaining blocker.
2. I did **not** observe evidence of registry leakage, monkeypatch leakage, or
   fixture pollution outside that timeout. The autouse reset fixture still
   looks correct and the remaining 17 non-skipped tests passed.

## Remaining blocker(s)
1. **Fix `test_obs_off_import_time_isolation` so it completes reliably under the
   project test environment.** Worker-gemini should debug why the fresh import
   subprocess hangs under `.venv/bin/python` and either:
   - remove the hang while preserving the same import-time assertions, or
   - replace the subprocess harness with an equally strict but reliable fresh-
     interpreter mechanism.

Once that test is green, B1–B3 should all be closed.
