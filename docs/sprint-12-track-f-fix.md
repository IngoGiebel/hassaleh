# Sprint 12 Track F — fix note after Inanna review

Date: 2026-04-21
Branch: `trunk`
Plan reference: `docs/sprint-12-plan.md` §1 S2a/S2b/S5, §2.4, §3.2, §3.3, §5 Track F, §7
Review addressed: `docs/sprint-12-track-f-review.md`

## Summary

This fix cycle addresses all three blockers from the Track F review:

1. **S5 import-time isolation** is now tested in a fresh subprocess.
2. **S2a tool counter coverage** is now implemented directly against the public
   tool-invocation instrumentation entry point.
3. **Trace surface coverage** now verifies the canonical lifecycle stage set and
   proves `HASSALEH_TRACE_FORCE=1` carries through a decorator path.

The plan’s **S2b exporter/cAdvisor checks** remain outside the scope of this
hermetic unit/integration suite because they require the compose stack. They
are now deferred explicitly with skipped tests and documented below instead of
being silently omitted.

## Blocker-by-blocker fixes

### Blocker 1 — S5 import-time isolation

Added `test_obs_off_import_time_isolation()`.

Implementation details:
- launches a clean Python subprocess with `HASSALEH_OBS=off`
- imports:
  - `hassaleh.obs`
  - `hassaleh.obs.metrics`
  - `hassaleh.obs.tracing`
- asserts that import alone does **not**:
  - register any `hassaleh_*` collectors into `prometheus_client.REGISTRY`
  - install an SDK `TracerProvider`
  - call `structlog.configure()` / leave `structlog.is_configured()` true

This closes the exact gap called out in review: the old in-process regression
only proved explicit setup calls were inert, not that imports were inert.

### Blocker 2 — S2a tool counter + explicit S2b deferral

Added `test_tool_counter_increment()`.

What it proves:
- `record_tool_invocation(...)` increments
  `hassaleh_tool_invocation_total{agent_id,command,result}` by exactly 1 per call
- the corresponding duration histogram count increments in lockstep

This satisfies the frozen plan’s named S2a hook without inventing a fake callsite.
It uses the public instrumentation helper already used by runtime code.

#### S2b deferral (explicit, not silent)

Added three skipped tests:
- `test_neo4j_exporter_up`
- `test_cadvisor_up`
- `test_per_container_metrics_non_empty`

Each is marked:

```python
@pytest.mark.skip(reason="requires docker smoke harness — tracked for Sprint 13 smoke suite ...")
```

Reason for deferral:
- these assertions require live Prometheus targets, docker-exported metrics, and
  per-container runtime state
- Track F was explicitly constrained to remain **fast, hermetic, and docker-free**
- therefore these obligations belong to the compose smoke harness / future smoke suite

This fix note records that deferral so review can distinguish “not covered yet”
from “accidentally omitted”.

### Blocker 3 — trace surface coverage

Expanded the trace coverage in two ways.

#### Canonical stage-set coverage

Added parameterized test:
- `test_canonical_lifecycle_stage_emits_named_span`

Covered helpers / span names:
- `auth_stage` → `intent.auth`
- `validate_stage` → `intent.validate`
- `execute_stage` → `intent.execute`
- `persist_stage` → `intent.persist_result`
- `result_stage` → `intent.result`

This matches the canonical lifecycle surface in the plan and closes the gap from
only checking `intent.lifecycle` + `intent.auth`.

#### Force-sampling through decorator path

Added:
- `test_trace_force_sampling_carries_through_decorator`

What it proves:
- with `HASSALEH_TRACE_FORCE=1` and `sample_rate=0.0`
- a function wrapped in `@trace_span("intent.execute")`
- still emits a recorded span

This demonstrates the force flag works through the real decorator path, not only
through a manually created span.

## Files changed

- `tests/test_observability.py`
- `docs/sprint-12-track-f-fix.md`

## Pytest output

Command run:

```bash
cd ~/projects/hassaleh && .venv/bin/python -m pytest tests/test_observability.py -v
```

Observed result:

```text
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-8.4.2, pluggy-1.6.0 -- /home/uranus/projects/hassaleh/.venv/bin/python
cachedir: .pytest_cache
rootdir: /home/uranus/projects/hassaleh
configfile: pyproject.toml
plugins: asyncio-0.25.3, anyio-4.2.0
asyncio: mode=Mode.AUTO, asyncio_default_fixture_loop_scope=None
collecting ... collected 21 items

tests/test_observability.py::test_logs_on_emit_json_with_required_bindings PASSED [  4%]
tests/test_observability.py::test_logs_on_include_trace_id_when_span_active PASSED [  9%]
tests/test_observability.py::test_logs_off_uses_plain_text_and_bypasses_structlog PASSED [ 14%]
tests/test_observability.py::test_metrics_on_registers_full_catalog PASSED [ 19%]
tests/test_observability.py::test_metrics_off_is_noop_and_hides_endpoint PASSED [ 23%]
tests/test_observability.py::test_metrics_endpoint_exposes_catalog_via_daemon PASSED [ 28%]
tests/test_observability.py::test_tool_counter_increment PASSED [ 33%]
tests/test_observability.py::test_neo4j_exporter_up SKIPPED (requires docker smoke harness — tracked for Sprint 13 smoke suite (see docs/sprint-12-track-f-fix.md §S2b deferral)) [ 38%]
tests/test_observability.py::test_cadvisor_up SKIPPED (requires docker smoke harness — tracked for Sprint 13 smoke suite (see docs/sprint-12-track-f-fix.md §S2b deferral)) [ 42%]
tests/test_observability.py::test_per_container_metrics_non_empty SKIPPED (requires docker smoke harness — tracked for Sprint 13 smoke suite (see docs/sprint-12-track-f-fix.md §S2b deferral)) [ 47%]
tests/test_observability.py::test_traces_on_produce_spans_with_attributes PASSED [ 52%]
tests/test_observability.py::test_canonical_lifecycle_stage_emits_named_span[auth_stage-intent.auth] PASSED [ 57%]
tests/test_observability.py::test_canonical_lifecycle_stage_emits_named_span[validate_stage-intent.validate] PASSED [ 61%]
tests/test_observability.py::test_canonical_lifecycle_stage_emits_named_span[execute_stage-intent.execute] PASSED [ 66%]
tests/test_observability.py::test_canonical_lifecycle_stage_emits_named_span[persist_stage-intent.persist_result] PASSED [ 71%]
tests/test_observability.py::test_canonical_lifecycle_stage_emits_named_span[result_stage-intent.result] PASSED [ 76%]
tests/test_observability.py::test_trace_force_sampling_carries_through_decorator PASSED [ 80%]
tests/test_observability.py::test_trace_force_sampling_overrides_zero_ratio PASSED [ 85%]
tests/test_observability.py::test_traces_off_is_noop PASSED [ 90%]
tests/test_observability.py::test_obs_off_regression_no_subsystem_setup_side_effects PASSED [ 95%]
tests/test_observability.py::test_obs_off_import_time_isolation PASSED [100%]

======================== 18 passed, 3 skipped in 3.12s =========================
```

## Result

Track F now explicitly covers:
- logs on/off
- metrics on/off
- `/metrics` daemon route
- tool counter increment (S2a)
- trace canonical stage set
- force-sampling through decorator path
- runtime no-op regression
- import-time isolation regression (S5)

And it explicitly defers:
- S2b exporter / cadvisor / per-container checks to the docker smoke suite.
