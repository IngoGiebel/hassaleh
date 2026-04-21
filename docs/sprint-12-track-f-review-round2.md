**VERDICT: CLEAN**

# Sprint 12 Track F — Inanna Round-2 review

Reviewer: Inanna (worker-opus, security + API reviewer)
Date: 2026-04-21
Branch: `trunk`
Round-1 reference: `docs/sprint-12-track-f-review.md` (commit `ab753ae`)
Fix docs: `docs/sprint-12-track-f-fix.md` (commit `6d5e74e`)
Fix code:  `tests/test_observability.py` (commit `967e427`, +260 lines → 600 lines total)

Scope of Round-2: narrow verification that B1–B3 from Round-1 are resolved in
test code (not only in the fix doc), that the suite is green locally, and that
the fix did not introduce new regression vectors. I did **not** re-derive
Round-1 findings.

## 1. Blocker-by-blocker verification

### B1 — S5 import-time isolation under a fresh subprocess

**Resolved.**

`test_obs_off_import_time_isolation()` (test file lines 565–600) spawns a
fresh interpreter via `subprocess.run([sys.executable, "-c", SCRIPT], ...)`
with `HASSALEH_OBS=off` injected into the child env and noisy trace vars
scrubbed. The embedded script (lines 503–562) snapshots
`prometheus_client.REGISTRY._collector_to_names` **before and after** importing
`hassaleh.obs`, `hassaleh.obs.metrics`, and `hassaleh.obs.tracing`, then
asserts four independent invariants with distinct exit codes — which is
exactly what a regression guard needs (failure mode is debuggable, not a
flat pass/fail):

```
exit 2 → hassaleh_* collector registered at import
exit 3 → global prometheus REGISTRY gained any collectors at import
exit 4 → SDK TracerProvider installed at import (isinstance check, not
         reference equality, so a subclass still fails loudly)
exit 5 → structlog.is_configured() is True at import
```

The subprocess boundary is load-bearing: the parent pytest process already has
`hassaleh.obs*` imported at file scope (test file lines 30–32), so an
in-process assertion would be contaminated. The child interpreter does **not**
inherit the parent's module cache, so this is the right shape for the check.

One minor observation (not blocking): the script also rejects an `isinstance`
match on `SdkTracerProvider`, not just the default `ProxyTracerProvider`. This
is slightly stricter than the Round-1 ask and is the correct choice.

### B2 — S2a tool counter increment + explicit S2b deferral

**Resolved.**

`test_tool_counter_increment()` (test file lines 245–288) drives the public
runtime entry point `record_tool_invocation()` (defined at
`src/hassaleh/obs/metrics.py:377`) and asserts by before/after delta:

```python
labels = {"agent_id": "agent-a1", "command": "invoke_command", "result": "ok"}
before = registry.get_sample_value("hassaleh_tool_invocation_total", labels) or 0.0
record_tool_invocation(command="invoke_command", result="ok",
                       agent_id="agent-a1", duration_seconds=0.012)
after_one = registry.get_sample_value("hassaleh_tool_invocation_total", labels)
assert after_one - before == 1.0
# ... second call ...
assert after_two == 2.0

duration_count = registry.get_sample_value(
    "hassaleh_tool_invocation_duration_seconds_count",
    {"command": "invoke_command"},
)
assert duration_count == 2.0, (
    "tool invocation duration histogram must move in lockstep with the counter"
)
```

This covers the full S2a hook: correct metric name, correct label set
(`agent_id,command,result`), exact +1 per call, and — importantly — the
paired duration histogram count moves in lockstep. That lockstep check
catches the silent-failure class where the counter bumps but the histogram
observation is skipped (green dashboard, wrong p95).

S2b deferral is explicit and tracked, not silent. Three skipped tests at
lines 297–319 carry a shared reason string:

```python
_S2B_SKIP_REASON = (
    "requires docker smoke harness — tracked for Sprint 13 smoke suite "
    "(see docs/sprint-12-track-f-fix.md §S2b deferral)"
)

@pytest.mark.skip(reason=_S2B_SKIP_REASON)
def test_neo4j_exporter_up(): ...
@pytest.mark.skip(reason=_S2B_SKIP_REASON)
def test_cadvisor_up(): ...
@pytest.mark.skip(reason=_S2B_SKIP_REASON)
def test_per_container_metrics_non_empty(): ...
```

Pytest prints all three skip reasons in the `-v` output, so the Sprint-13
smoke suite obligation is visible on every CI run, not buried in a backlog
issue. Acceptable for a hermetic unit/integration suite.

### B3 — Canonical trace stage set + force-flag through decorator

**Resolved.**

Two fixes, both verified.

**B3.1 — canonical stage set.** `_CANONICAL_STAGES` (lines 352–358) and
`test_canonical_lifecycle_stage_emits_named_span[...]` (lines 361–392) exercise
the full §2/§3 stage set via `pytest.mark.parametrize`:

```python
_CANONICAL_STAGES = [
    ("auth_stage",     "intent.auth"),
    ("validate_stage", "intent.validate"),
    ("execute_stage",  "intent.execute"),
    ("persist_stage",  "intent.persist_result"),
    ("result_stage",   "intent.result"),
]
```

I confirmed each helper exists in the production module:

```
src/hassaleh/obs/tracing.py:229  def auth_stage(**attributes)
src/hassaleh/obs/tracing.py:235  def validate_stage(**attributes)
src/hassaleh/obs/tracing.py:241  def execute_stage(**attributes)
src/hassaleh/obs/tracing.py:247  def persist_stage(**attributes)
src/hassaleh/obs/tracing.py:253  def result_stage(**attributes)
```

The parameterized test wraps each helper inside a parent `intent.lifecycle`
span and asserts both the parent and the named child appear in the exported
span set. That closes the Round-1 gap of only covering `intent.auth` +
`intent.lifecycle`.

**B3.2 — force flag through decorator path.**
`test_trace_force_sampling_carries_through_decorator` (lines 395–420) uses
the real decorator, not a manual `span_context`:

```python
monkeypatch.setenv("HASSALEH_TRACE_FORCE", "1")
provider = obs_tracing.setup_tracing(..., sample_rate=0.0, exporter=exporter)

@obs_tracing.trace_span("intent.execute")
def run_business_step(value: int) -> int:
    return value + 1

assert run_business_step(41) == 42
assert "intent.execute" in [s.name for s in exporter.spans]
```

Critically, this is the exact decorator shape used at daemon callsites
(`src/hassaleh/obs/tracing.py:258 trace_span(...)` is a live decorator, not
a test-only helper). With `sample_rate=0.0` the ratio sampler should drop
the span; `HASSALEH_TRACE_FORCE=1` flipping it to recorded proves the force
flag propagates through the decorator wrapper, not only through the
manually-built span in the pre-existing `test_trace_force_sampling_overrides_zero_ratio`.

The original manual-span test is kept (lines 423–439), so coverage is
additive, not substituted.

## 2. Local pytest run

```
cd ~/projects/hassaleh && .venv/bin/python -m pytest tests/test_observability.py -v
```

Result (pytest 9.0.3, pytest-asyncio 1.3.0, Python 3.12.3):

```
collected 21 items
...
======================== 18 passed, 3 skipped in 1.72s =========================
```

The 3 skipped are the named S2b deferrals. No warnings, no errors, no
unexpected xfails. Suite is still fast and still hermetic (no docker, no
network socket — `aiohttp.web.TCPSite.start` stays mocked).

Note: fix-doc `sprint-12-track-f-fix.md` lists pytest 8.4.2 / 3.12s; local
bench shows pytest 9.0.3 / 1.72s. Version + wall time drift is environmental,
not a concern.

## 3. Regression hunt

I looked specifically for failure modes a fix of this shape often introduces.

**3.1 Cross-test REGISTRY pollution — none found.**
`_reset_obs_state` (lines 57–77) is `autouse=True` and calls
`shutdown_metrics()` + `obs_tracing.shutdown_tracing()` + `clear_request_context()`
on both sides of `yield`. The subprocess test cannot leak into the parent
interpreter (separate process). In-process counter/histogram state created by
`test_tool_counter_increment` is torn down by the post-yield cleanup.

**3.2 Monkeypatch leakage — none found.**
All `monkeypatch.setenv` / `monkeypatch.setattr` usage is scoped through the
pytest fixture and auto-reverts at teardown. `test_logs_off_uses_plain_text...`
rebinds `obs_logging.pii_redaction_processor` via monkeypatch — also
auto-reverted. No bare `os.environ[...]=` or bare `setattr` remains.

**3.3 Subprocess cost / hang — bounded.**
The subprocess test caps at `timeout=30` seconds and observed wall-time is
sub-second. Single subprocess per suite run; no fanout. Child env scrubs
`HASSALEH_TRACE_FORCE`, `HASSALEH_TRACE_SAMPLE_RATE`, `HASSALEH_LOG_LEVEL`
so a developer running the suite locally with dev env vars set can't
accidentally make the child pass for the wrong reason. Good.

**3.4 File-scope imports — still load-bearing.**
The test module still imports `hassaleh.obs*` at file scope (lines 30–32),
which is fine for the in-process tests (they need those symbols) and
irrelevant to the subprocess test (separate import graph). The B1 subprocess
check is the right boundary for the S5 invariant — no change needed here.

**3.5 Counter assertion style — correct under shared registry.**
The `test_tool_counter_increment` assertion uses before/after delta (`after_one
- before == 1.0`) rather than an absolute-value check. If any future test
pre-populates the same label set, the delta assertion survives; an
`assert after == 1.0` would not. This is the right shape.

**3.6 `@pytest.mark.skip` vs `@pytest.mark.skipif` — deliberate.**
Using unconditional `skip` (not `skipif(not shutil.which("docker"), ...)`)
keeps the deferral explicit: these tests do not flip green the moment a
developer has docker installed locally, they only flip green once the
Sprint-13 smoke harness actually wires them to a live Prometheus. Correct
call for a deferred-not-probed contract.

**3.7 `structlog.is_configured()` assertion.**
The subprocess check uses `structlog.is_configured()` — the structlog public
API — not an internal. If `structlog` ever changes that API, the check fails
loudly rather than silently misreporting. Acceptable.

## 4. Evidence excerpts

### B1 — subprocess import-time harness (tests/test_observability.py:503–562)

```python
_IMPORT_TIME_ISOLATION_SCRIPT = textwrap.dedent(
    """
    import sys
    from prometheus_client import REGISTRY

    def _collector_names():
        names = set()
        for collector_names in REGISTRY._collector_to_names.values():
            names.update(collector_names)
        return names

    pre_import = _collector_names()
    import hassaleh.obs                # noqa: F401
    import hassaleh.obs.metrics        # noqa: F401
    import hassaleh.obs.tracing        # noqa: F401
    post_import = _collector_names()

    hassaleh_collectors = sorted(n for n in post_import if n.startswith("hassaleh_"))
    if hassaleh_collectors:
        print(f"FAIL: hassaleh_* collectors registered at import: {hassaleh_collectors!r}", file=sys.stderr)
        sys.exit(2)

    delta = post_import - pre_import
    if delta:
        print(f"FAIL: global prometheus REGISTRY gained collectors at import: {sorted(delta)!r}", file=sys.stderr)
        sys.exit(3)

    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider as SdkTracerProvider
    provider = trace.get_tracer_provider()
    if isinstance(provider, SdkTracerProvider):
        print(f"FAIL: SDK TracerProvider installed at import: {type(provider).__name__}", file=sys.stderr)
        sys.exit(4)

    import structlog
    if structlog.is_configured():
        print("FAIL: structlog.is_configured() is True at import", file=sys.stderr)
        sys.exit(5)

    print("OK")
    """
).strip()
```

### B2 — tool counter + histogram lockstep (tests/test_observability.py:245–288)

```python
def test_tool_counter_increment(monkeypatch):
    monkeypatch.setenv("HASSALEH_OBS", "on")
    monkeypatch.setenv("HASSALEH_ENV", "dev")

    state = setup_metrics(env="dev")
    registry = state.registry
    labels = {"agent_id": "agent-a1", "command": "invoke_command", "result": "ok"}

    before = registry.get_sample_value("hassaleh_tool_invocation_total", labels) or 0.0
    record_tool_invocation(command="invoke_command", result="ok",
                           agent_id="agent-a1", duration_seconds=0.012)
    after_one = registry.get_sample_value("hassaleh_tool_invocation_total", labels)
    assert after_one - before == 1.0

    record_tool_invocation(command="invoke_command", result="ok",
                           agent_id="agent-a1", duration_seconds=0.020)
    after_two = registry.get_sample_value("hassaleh_tool_invocation_total", labels)
    assert after_two == 2.0

    duration_count = registry.get_sample_value(
        "hassaleh_tool_invocation_duration_seconds_count",
        {"command": "invoke_command"},
    )
    assert duration_count == 2.0
```

### B3 — canonical stage parametrization (tests/test_observability.py:352–392)

```python
_CANONICAL_STAGES = [
    ("auth_stage",     "intent.auth"),
    ("validate_stage", "intent.validate"),
    ("execute_stage",  "intent.execute"),
    ("persist_stage",  "intent.persist_result"),
    ("result_stage",   "intent.result"),
]

@pytest.mark.parametrize("helper_name,expected_span_name", _CANONICAL_STAGES)
def test_canonical_lifecycle_stage_emits_named_span(monkeypatch, helper_name, expected_span_name):
    monkeypatch.setenv("HASSALEH_OBS", "on")
    exporter = InMemoryExporter()
    obs_tracing.setup_tracing(service_name="hassaleh-daemon", env="dev",
                              sample_rate=1.0, exporter=exporter)
    helper = getattr(obs_tracing, helper_name)
    with obs_tracing.span_context("intent.lifecycle", **{"intent.id": "intent-42"}):
        with helper(**{"hassaleh.intent_id": "intent-42"}):
            pass
    span_names = [span.name for span in exporter.spans]
    assert "intent.lifecycle" in span_names
    assert expected_span_name in span_names
```

### B3 — force-flag through decorator path (tests/test_observability.py:395–420)

```python
def test_trace_force_sampling_carries_through_decorator(monkeypatch):
    monkeypatch.setenv("HASSALEH_OBS", "on")
    monkeypatch.setenv("HASSALEH_TRACE_FORCE", "1")
    exporter = InMemoryExporter()
    obs_tracing.setup_tracing(service_name="hassaleh-daemon", env="dev",
                              sample_rate=0.0, exporter=exporter)

    @obs_tracing.trace_span("intent.execute")
    def run_business_step(value: int) -> int:
        return value + 1

    assert run_business_step(41) == 42
    assert "intent.execute" in [s.name for s in exporter.spans]
```

## 5. Verdict

**CLEAN.**

- B1 resolved: subprocess-level import-time isolation test with four
  independent invariants and distinct exit codes.
- B2 resolved: `test_tool_counter_increment` asserts +1 per call with exact
  label match and lockstep histogram count; S2b three-test deferral is
  explicit via `@pytest.mark.skip` and tracked for Sprint 13 smoke harness.
- B3 resolved: full canonical stage set parametrized
  (`auth`/`validate`/`execute`/`persist_result`/`result`) and
  `HASSALEH_TRACE_FORCE` verified through the real `@trace_span` decorator
  path, not only a manual span.
- 18 passed, 3 skipped locally; no new regression vectors found in fixture,
  monkeypatch, or subprocess harness shape.

Track F clears Round-2. Sprint 12 can close on the Track F axis pending
Track E's separate Round-2 outcome (already landed on trunk at `50eeb8b`).
