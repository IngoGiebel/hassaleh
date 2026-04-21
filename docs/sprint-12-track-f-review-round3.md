# Sprint 12 — Track F Security Review (Round 3)

**Reviewer:** Inanna
**Date:** 2026-04-21
**Scope:** Single remaining blocker from Round 2 — `test_obs_off_import_time_isolation` subprocess hang. B2 and B3 already verified resolved in Round 2.
**Commits under review:** `8491109` (fix) + `8baeb96` (fix note).
**Verdict:** **CLEAN — Sprint 12 closed.**

## What Round 2 left open

| ID | Status after Round 2 | Round-3 focus |
|----|---------------------|---------------|
| B1 | CHANGES-REQUESTED — test structurally correct, subprocess hit `TimeoutExpired` after 30 s in `.venv` | ✅ now passes, ~1.9 s, 10× under the new 15 s budget |
| B2 | RESOLVED (tool counter + S2b skip placeholders) | ✅ still green, no regression |
| B3 | RESOLVED (5 canonical stages + decorator-path force) | ✅ still green, no regression |

## B1 verification

### 1. Assertions preserved

The isolation contract lives in `_IMPORT_TIME_ISOLATION_SCRIPT`
(`tests/test_observability.py:503-562`). Git diff `8491109^..8baeb96` on
`tests/test_observability.py` shows **zero changes inside the script**.
All four fail-hard exits remain intact:

| Exit | Guards against |
|------|----------------|
| 2 | any `hassaleh_*` collector registered in prometheus global `REGISTRY` at import time |
| 3 | any delta to the global prometheus `REGISTRY` at import time |
| 4 | a real `opentelemetry.sdk.trace.TracerProvider` installed at import time |
| 5 | `structlog.is_configured()` returning `True` at import time |

The happy path still requires the final stdout line to be `OK`
(`tests/test_observability.py:620`), so a silent no-op subprocess cannot
spoof a pass.

### 2. Harness change is strictly narrower than the Round-2 failure mode

All deltas are process-level plumbing, not isolation logic:

- `env["PYTHONDONTWRITEBYTECODE"] = "1"` — child never touches
  `__pycache__`; eliminates the pyc write-contention race Round-2
  diagnosed. Does **not** change which modules get imported.
- `env["PYTHONUNBUFFERED"] = "1"` — unbuffered stdio so diagnostics
  survive a forced kill. Does not affect import side effects.
- `stdin=subprocess.DEVNULL` — severs inherited pytest fds.
  *Tightens* the "fresh interpreter" model the test claims to provide.
- `subprocess.run(..., timeout=30, capture_output=True)` →
  `Popen(...) + communicate(timeout=15)` with explicit `kill()` + drain
  in the `TimeoutExpired` branch. Identical exit-code + stdout + stderr
  semantics, but a hang now reports *partial child output* instead of an
  empty buffer.
- Timeout 30 s → 15 s — still ~10× the observed cold-start cost
  (≈ 1.5–2 s), stricter, and hard-fails loudly.

None of these relax the four isolation exits or the `OK` sentinel
check. If anything, DEVNULL + the pyc suppression make the subprocess a
cleaner representation of "import in a cold interpreter with
`HASSALEH_OBS=off`".

The in-process S5 companion
(`test_obs_off_regression_no_subsystem_setup_side_effects`,
`tests/test_observability.py:464`) still exercises the post-`setup()`
surface, so the two-layer S5 coverage (import-time subprocess + explicit
in-process setup) is unchanged.

### 3. Execution

```
$ cd ~/projects/hassaleh && . .venv/bin/activate && \
    python -m pytest tests/test_observability.py -v
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.0.3, pluggy-1.6.0
rootdir: /home/uranus/projects/hassaleh
configfile: pyproject.toml
plugins: asyncio-1.3.0
collected 21 items

tests/test_observability.py::test_logs_on_emit_json_with_required_bindings PASSED [  4%]
tests/test_observability.py::test_logs_on_include_trace_id_when_span_active PASSED [  9%]
tests/test_observability.py::test_logs_off_uses_plain_text_and_bypasses_structlog PASSED [ 14%]
tests/test_observability.py::test_metrics_on_registers_full_catalog PASSED [ 19%]
tests/test_observability.py::test_metrics_off_is_noop_and_hides_endpoint PASSED [ 23%]
tests/test_observability.py::test_metrics_endpoint_exposes_catalog_via_daemon PASSED [ 28%]
tests/test_observability.py::test_tool_counter_increment PASSED          [ 33%]
tests/test_observability.py::test_neo4j_exporter_up SKIPPED (...)        [ 38%]
tests/test_observability.py::test_cadvisor_up SKIPPED (...)              [ 42%]
tests/test_observability.py::test_per_container_metrics_non_empty SKIPPED [ 47%]
tests/test_observability.py::test_traces_on_produce_spans_with_attributes PASSED [ 52%]
tests/test_observability.py::test_canonical_lifecycle_stage_emits_named_span[auth_stage-intent.auth] PASSED [ 57%]
tests/test_observability.py::test_canonical_lifecycle_stage_emits_named_span[validate_stage-intent.validate] PASSED [ 61%]
tests/test_observability.py::test_canonical_lifecycle_stage_emits_named_span[execute_stage-intent.execute] PASSED [ 66%]
tests/test_observability.py::test_canonical_lifecycle_stage_emits_named_span[persist_stage-intent.persist_result] PASSED [ 71%]
tests/test_observability.py::test_canonical_lifecycle_stage_emits_named_span[result_stage-intent.result] PASSED [ 76%]
tests/test_observability.py::test_trace_force_sampling_carries_through_decorator PASSED [ 80%]
tests/test_observability.py::test_trace_force_sampling_overrides_zero_ratio PASSED [ 85%]
tests/test_observability.py::test_traces_off_is_noop PASSED              [ 90%]
tests/test_observability.py::test_obs_off_regression_no_subsystem_setup_side_effects PASSED [ 95%]
tests/test_observability.py::test_obs_off_import_time_isolation PASSED   [100%]

======================== 18 passed, 3 skipped in 3.00s =========================
```

Targeted re-run of the former blocker:

```
$ python -m pytest tests/test_observability.py::test_obs_off_import_time_isolation -v
tests/test_observability.py::test_obs_off_import_time_isolation PASSED [100%]
============================== 1 passed in 1.94s ===============================
```

Matches the Round-2 fix note (`18 passed, 3 skipped in 1.94s`, stable
across runs). The 3 skips are the S2b Prometheus-target placeholders,
each carrying the explicit Sprint-13 deferral reason string.

## B2 / B3 regression check

- `test_tool_counter_increment` (`tests/test_observability.py:245`) —
  PASSED. Counter + duration-histogram lockstep assertions still hold.
- `test_canonical_lifecycle_stage_emits_named_span` — 5/5 params PASSED
  (`auth`, `validate`, `execute`, `persist_result`, `result`), covering
  the full §3.3 canonical set.
- `test_trace_force_sampling_carries_through_decorator`
  (`tests/test_observability.py:395`) — PASSED: `HASSALEH_TRACE_FORCE=1`
  flips sampling for the `@trace_span(...)` decorator path even when
  `sample_rate=0.0`.
- `test_trace_force_sampling_overrides_zero_ratio` — PASSED.

No functional code under `src/` was touched by `8491109` or `8baeb96`;
the harness fix is confined to `tests/test_observability.py`, so B2/B3
cannot regress from this change, and execution confirms they did not.

## Verdict

**CLEAN.** All three Round-1 blockers are now resolved in code and
verified in execution:

- B1 — import-time isolation subprocess passes reliably (< 2 s, 10× under
  budget), assertions unchanged, harness tightened rather than weakened.
- B2 — tool counter contract + S2b deferrals preserved.
- B3 — full canonical-stage coverage + decorator-path force preserved.

**This closes Sprint 12 Track F.**
