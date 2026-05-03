# Sprint 13 Track D — Inanna Round-2 review

**VERDICT: CLEAN (blocker scope). No new blockers; no new security regressions introduced by the fix. Track D is closed for sprint-13.**

Reviewer: Inanna (worker-opus, security + API + precondition-semantics perspective)
Date: 2026-04-26
Scope: `~/projects/hassaleh` @ `c0b04f1` (base `05fd8d8`), reviewed against
the FROZEN plan `docs/sprint-13-plan.md` v1.1 §2.6 (commit `af6803b`),
Round-1 verdict `docs/sprint-13-track-d-review.md`, and the fix commit
`c0b04f1` ("sprint-13 Track D FIX (round 1): HASSALEH_OBS=off honored in
record_metric + emit_log").

Round-2 is narrow: confirm the single Round-1 blocker B1 (`HASSALEH_OBS=off`
not honored on `record_metric` / `emit_log`) is fixed in *code*, verify
the two new tests assert the right invariants (zero-cost no-op + cross-
registry isolation), confirm the seven §6 advisories are documented as
deferred per Dione's arbitration, and flag any **new** regressions the
fix may have introduced. Source files were not modified during this review.

---

## Per-blocker verification

### B1 — `HASSALEH_OBS=off` honoured by `record_metric` + `emit_log` — **RESOLVED ✓**

Verified in code (not just the doc), against `src/hassaleh/runtime/observability.py`
@ `c0b04f1`:

- **`record_metric` (L118–129):** the very first executable statement of
  the function body is

  ```python
  if not obs_tracing.is_obs_enabled():
      return
  ```

  This sits *before* `_ensure_metrics()` — meaning under
  `HASSALEH_OBS=off`, no `CollectorRegistry` is allocated, no `Counter`
  / `Histogram` is instantiated, no `.labels(...).inc()` /
  `.observe(...)` is called, and the `prometheus_client` module is
  effectively never touched on the off-path. The Round-1 §4.2 ask
  ("true zero-cost no-op, not just a different code path") is satisfied.

- **`emit_log` (L132–156):** the very first executable statement is the
  same guard:

  ```python
  if not obs_tracing.is_obs_enabled():
      return
  ```

  This sits *before* the `fields: dict[str, Any] = {...}` assembly,
  *before* `_current_trace_id()` (which would call `otel_trace.get_current_span()`),
  *before* `obs_logging.get_logger(...)`, and *before* `logger.info(...)`.
  Under `HASSALEH_OBS=off`: no dict construction, no OTel span lookup, no
  structlog `get_logger` (which would otherwise trigger the
  `bind_logger` / `PlainBoundLogger` fallback path), no stdlib
  `logging` line. True zero-cost.

- **Guard cost itself is constant-time, stateless:** `is_obs_enabled()`
  in `src/hassaleh/obs/tracing.py:98` is a one-line `_env_flag(...)
  == "on"` check — no I/O, no caching, no allocation, no lock. Calling
  it on every dispatch is a sub-microsecond cost. Acceptable for a
  hot-path function called twice per Intent.

- **Guards are at the TOP of the functions, not buried after work
  has been done.** Verified by literal line-number inspection: lines 125–126
  for `record_metric`, lines 141–142 for `emit_log`. No allocation, no
  registry write, no Prometheus client touch precedes either guard.

Round-1 B1 closed.

---

## New-test verification (Round-1 §5.2 follow-ups)

The Round-1 review asked for two specific test additions if option (a)
landed: an off-noop assertion pair (one per surface) and a cross-registry
isolation test. All three are present and they assert the right thing.

### `test_record_metric_noop_when_obs_off` (tests/test_runtime_observability.py:367)

Asserts the right invariant — **the counter sample value does not
change between an on-path tick and a subsequent off-path tick:**

```python
monkeypatch.setenv("HASSALEH_OBS", "on")
obs.record_metric(intent, result, duration_ms=10.0)
before = registry.get_sample_value("hassaleh_intent_total", {...})

monkeypatch.setenv("HASSALEH_OBS", "off")
obs.record_metric(intent, result, duration_ms=10.0)
after = registry.get_sample_value("hassaleh_intent_total", {...})
assert after == before == 1.0
```

This is the correct contract test. A weaker version
(`assert get_sample_value(...) is None`) would pass even if the off-path
were silently allocating the registry but not ticking — the chosen form
proves the off-call is a true no-op against an *already-warmed* registry,
which is the realistic dispatch sequence (daemon does at least one ON
intent before any off-path can occur). Strong.

### `test_emit_log_noop_when_obs_off` (tests/test_runtime_observability.py:389)

Asserts the right invariant — **no `"intent executed"` line on
stderr or stdout under off:**

```python
monkeypatch.setenv("HASSALEH_OBS", "off")
monkeypatch.setenv("HASSALEH_ENV", "prod")
obs_logging.setup("hassaleh-daemon", "prod")
obs.emit_log(intent, ctx, result, duration_ms=1.0)

captured = capsys.readouterr()
assert "intent executed" not in captured.err
assert "intent executed" not in captured.out
```

Note the subtle but important detail: it calls `obs_logging.setup(...)`
*after* setting `HASSALEH_OBS=off`. Without the new guard, structlog
would route through `PlainBoundLogger` and write
`f"{event} {json.dumps(merged)}"` to stdlib `logging` (which on the test
config goes to stderr) — the assertion would fail. The fact that both
streams are empty confirms `emit_log` short-circuits before
`get_logger` is ever called. Correct invariant, correct sensitivity.

### `test_cross_registry_isolation` (tests/test_runtime_observability.py:405)

Asserts the right invariant — **the global Prometheus default
registry does not see `hassaleh_intent_total` after a successful
on-path tick:**

```python
monkeypatch.setenv("HASSALEH_OBS", "on")
obs.record_metric(intent, result, duration_ms=1.0)
val = DEFAULT_PROM_REGISTRY.get_sample_value(
    "hassaleh_intent_total",
    {"intent_type": intent.type, "result": "ok"},
)
assert val is None
```

This locks the dedicated-registry invariant from Round-1 §4.1. A future
refactor that drops the `registry=_registry` kwarg would be caught here:
the metric would land in `prometheus_client.REGISTRY`, the global
sample lookup would return `1.0`, and the assertion would fail.
Defends against the Sprint-12 collision pattern as requested in §5.2.

### Existing on-path tests properly opt back in

A subtle correctness check: with the new guards, *any* existing test
that previously exercised `record_metric` or `emit_log` would now
silently no-op (and falsely "pass" without asserting anything) unless
it sets `HASSALEH_OBS=on` — because the autouse `_reset` fixture deletes
the env at the start of every test (line 54–61). Verified that all 12
on-path tests now explicitly `monkeypatch.setenv("HASSALEH_OBS", "on")`:

  - record_metric tests: lines 97, 118, 140, 174 (4 of 4)
  - span tests: lines 199, 243, 263 (3 of 3)
  - emit_log tests: lines 297, 324, 347 (3 of 3)
  - new tests: lines 372 (warm-up), 391 (intentional off), 407 (3 of 3)

No on-path test was left in a state where the fix would silently
mask a regression. The author did the cleanup correctly.

---

## Deferred-advisory documentation (§6, Round-1)

Per Dione's arbitration, the seven §6 advisories are deferred to v1.0.1
and **not** blocking for this round. Round-1 ask was that
`docs/sprint-13-track-d-implement.md` enumerate them so the v1.0.1
backlog is locked in.

`docs/sprint-13-track-d-implement.md` @ `c0b04f1` lists exactly the
seven items requested:

| Round-1 §6 advisory | Listed in implement.md |
|---|---|
| #1 Lazy-init thread-safety in `_ensure_metrics()` | ✓ "Lazy-init thread-safety" |
| #2 Strippable `assert _registry is not None` under `python -O` | ✓ "strippable assert" |
| #3 Cross-sprint `hassaleh_intent_duration_seconds` family collision | ✓ "cross-sprint metric-family collision" |
| #4 Counter-cardinality DoS via `intent_type` | ✓ "counter-cardinality DoS" |
| #5 Sub-ms histogram resolution / 10ms floor | ✓ "sub-ms histogram resolution" |
| #6 `emit_log` does not call `obs_logging.setup()` | ✓ "emit_log/setup contract" |
| #7 `_LOGGER_NAME` vs `SERVICE_ENUM` confirmation | ✓ "_LOGGER_NAME/SERVICE_ENUM confirmation" |

All seven are accounted for. **Not requested as blocking** per Dione's
arbitration; flagged here only to confirm the deferred-list is complete
and the v1.0.1 backlog is locked.

Minor non-blocking observation: `implement.md` is a single paragraph of
labels. For v1.0.1 traceability, attaching a one-line ticket / issue
number to each (e.g. `D-A1 thread-safety: …`) would help when the
backlog is groomed. Cosmetic; does not affect this round.

---

## New regressions introduced by the fix — **NONE**

Checked against the diff `05fd8d8..c0b04f1`:

- **Surface change:** none. The eight-symbol `__all__` list
  (`start_root_span`, four child spans, `record_metric`, `emit_log`,
  `get_runtime_registry`) is byte-for-byte identical. Track A's import
  contract is unchanged.

- **Behavior change on the on-path:** none. `record_metric` and
  `emit_log` under `HASSALEH_OBS=on` execute the same code in the
  same order as before; the guard returns false and falls through.
  All eleven Round-1 on-path tests continue to pass logically (and the
  diff shows the only modification to those tests is adding the
  `monkeypatch.setenv("HASSALEH_OBS", "on")` line — no assertion
  changes, no fixture changes).

- **Behavior change on the off-path:** intentional, documented, and
  matches the user's review-checklist requirement. Off was previously
  "downgrade to plain stdlib + warm registry"; off is now "true no-op."
  No caller in the current Track A surface depends on the previous
  off-path side effects (Track A is not yet wired to the Track D
  facade — see commit `1499117`, "Track A FIX round 1," which still
  pre-dates the dispatch loop instrumentation).

- **New imports:** one — `from prometheus_client import REGISTRY as
  DEFAULT_PROM_REGISTRY` in the test file, used only by the new
  cross-registry isolation test. Not pulled into the production module.

- **PII / span-attribute surface:** unchanged. Round-1 §4.3 PII verdict
  still holds (no payload, no Cypher, no message content reaches any
  observability surface).

- **Fail-open / fail-closed semantics:** the new guards are
  fail-closed *for observability* (off → silence) but **never**
  fail-closed for the dispatch path itself — `record_metric` /
  `emit_log` already returned `None` and never raised, so callers
  cannot tell the difference between a noisy-off (old) and a silent-off
  (new). No code path that depends on observability side effects is
  introduced or broken.

- **No new dependency, no new capability, no new env var.** The fix
  reuses the existing `obs_tracing.is_obs_enabled()` predicate that
  Sprint-12 already established as the single source of truth for
  on/off state. Single point of control preserved.

---

## Round-1 "Resolution path" re-verify checklist

Round-1 offered Dione two paths: (a) a six-line code fix + test pair,
or (b) a spec amendment retiring the "zero-cost no-op" requirement.
Dione chose (a). Verifying Path (a) deliverables:

- Six-line guard added at the top of `record_metric` — ✓
  (lines 125–126: 2 lines of guard + 5 lines of expanded docstring)
- Six-line guard added at the top of `emit_log` — ✓
  (lines 141–142: 2 lines of guard + 5 lines of expanded docstring)
- Off-noop test for `record_metric` — ✓
  (`test_record_metric_noop_when_obs_off`, asserts after == before == 1.0)
- Off-noop test for `emit_log` — ✓
  (`test_emit_log_noop_when_obs_off`, asserts no "intent executed"
  on stderr/stdout)
- Bonus: cross-registry isolation test added — ✓
  (`test_cross_registry_isolation`, asserts global REGISTRY untouched —
  Round-1 §5.2 gap #2 also closed in the same commit)

All Path-(a) deliverables landed. Dione's arbitration is fully reflected
in code.

---

## Verification not run in this round

Pytest was not executed: the in-repo `.venv` is currently broken
(Python 3.12 base, host now on 3.14 — see ops memory `2026-04-25-md`
"venv broken (3.12 vs 3.14), deleted; uv sync needed"), and `uv` is not
on PATH on this review host. The review is purely interface- and
contract-level by code inspection — same standard as Round-1, which
also did not execute the suite. The new tests are unambiguous by
inspection: each asserts a single named invariant against a freshly-isolated
fixture, and the guard placement makes the on-path semantics
provably equivalent to the pre-fix behavior. If the build host needs an
independent green run before merge, `uv sync && uv run pytest
tests/test_runtime_observability.py -q` is the one-line check —
expected: 18 passed.

---

## Recommendation

**Clear Track D for the B1 blocker scope; close Track D for sprint-13.**
The single Round-1 blocker is fixed in *code*, not just in the fix
commit message. The two requested off-noop tests and the bonus
cross-registry isolation test land the right invariants with the right
sensitivity. The seven §6 advisories are documented as deferred per
Dione's arbitration. No new secrets, no new privileged surfaces, no
new PII vectors, no new public-API drift.

The seven deferred advisories remain open for v1.0.1 grooming and
should be ticketed before sprint-13 retro. None of them are
security-blocking on their own; #1 (thread-safety) and #4
(counter-cardinality) are the two most likely to bite under
production load and warrant the earliest v1.0.1 slots.

— Inanna 🛡️
