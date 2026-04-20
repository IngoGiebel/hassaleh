# Sprint 12 Track A — Inanna retrospective review

**Reviewer:** Inanna 🛡️  
**Date:** 2026-04-21  
**Target commit:** `ecab59a` on `trunk`  
**Plan baseline:** `docs/sprint-12-plan.md` (FROZEN v1.0.2)  
**Review type:** retrospective security/API review (logging surface only)

---

## 1. Scope (what I reviewed)

I reviewed the Track A implementation note and the logging-facing code paths
that establish the Observability v1 logging surface and daemon boot ownership:

- `docs/sprint-12-track-a-implement.md`
- `src/hassaleh/obs/logging.py`
- `src/hassaleh/obs/__init__.py`
- `src/hassaleh/daemon.py`
- `tests/test_obs_logging.py`
- `pyproject.toml`
- `docs/sprint-12-plan.md` with focus on:
  - §2.4 `obs.setup()` ownership boundary
  - §3.1 structured audit log schema + required fields
  - §3.1.1 Cypher slow-query log PII shape rules
  - §3.1.2 tool-invocation audit log rules
  - §S5 `HASSALEH_OBS=off` must be a zero-cost no-op

I did **not** review Track B metrics semantics beyond what is necessary to judge
whether Track A violates the Track A contract by invoking metrics unconditionally.
I also did not review tracing behavior beyond the import boundary in
`obs/__init__.py`.

---

## 2. Security findings — blocking CR-style issues if any

### CR-1 — `HASSALEH_OBS=off` is **not** a zero-cost no-op

**Severity:** blocking  
**Plan references:** §2.4, S5  
**Files:**
- `src/hassaleh/obs/__init__.py:15-18`
- `src/hassaleh/obs/__init__.py:20-23`
- `src/hassaleh/obs/metrics.py:186-194`

The plan is explicit that when `HASSALEH_OBS=off`, observability must be a
**zero-cost no-op**: no new processes, no network calls, and no new formatters
(S5). Track A does **not** meet that contract as implemented.

Why:

1. `obs/__init__.py` imports metrics unconditionally:
   - `from hassaleh.obs.metrics import get_metrics_state, setup_metrics`
2. `obs.setup()` unconditionally calls `setup_metrics(...)`:
   - `config = setup_logging(service_name, env)`
   - `setup_metrics(env=config.env, version=config.version)`
3. `setup_metrics()` does short-circuit when obs is off, but that still means
   Track A's top-level entrypoint performs additional module import work and an
   extra setup path even in the supposed no-op mode.

This is not merely an internal purity nit. The frozen plan’s contract is:
- Track A owns `obs.setup()`
- `HASSALEH_OBS=off` must preserve the pre-observability behavior shape
- zero-cost no-op is part of the sprint success criteria

Current behavior instead is:
- Track A still installs a new structlog-backed root handler / formatter path
  in off mode (`logging.py:169-186`)
- Track A also invokes metrics setup machinery in off mode (`__init__.py:20-23`)

That may be operationally acceptable, but it is **not the spec that was
approved**.

**Required fix:**
- `obs.setup()` must not initialize metrics (or any later-track surface) when
  `HASSALEH_OBS=off`.
- If the intended contract changed from “zero-cost” to “minimal stderr-only
  compatibility mode,” the plan and S5 must be updated explicitly. Right now,
  code and plan disagree.

### CR-2 — Track A claims deferred audit-log readiness, but no contract guard exists for §3.1.1 / §3.1.2 high-risk fields beyond generic redaction

**Severity:** blocking  
**Plan references:** §3.1.1, §3.1.2  
**Files:**
- `src/hassaleh/obs/logging.py:96-125`
- `tests/test_obs_logging.py:37-72`

Track A correctly redacts several sensitive field families, but the approved
plan for audit-style logs is stricter than “generic redaction helper exists.”
Specifically:

- §3.1.1 requires `param_shape` for slow-query logs — types/lengths only,
  never raw values
- §3.1.2 requires tool-invocation audit logs to exclude command arguments

The implementation note says the explicit call-sites are deferred, which is
fine. But the logger substrate itself does **not** currently provide a dedicated
schema-level safeguard for those two planned structures; it only provides:
- special handling for `cypher_params`
- generic recursive redaction for mappings

Why this matters:
- once follow-on call-sites land, a developer can emit `param_shape` incorrectly
  or include `argv` / command args under some non-redacted key name, and Track A
  provides no schema-enforcing processor to stop it
- the existing tests do not cover these audit-contract-specific failure modes

I am calling this blocking because Track A is already on trunk and is being
positioned as the stable logging substrate that later tracks depend on. The
contract should be encoded now, not merely documented for future callers.

**Required fix:**
- either add processor-level handling that explicitly constrains the future
  §3.1.1 / §3.1.2 structures (`param_shape`, no raw argv / args fields),
- or narrow the implementation note and plan-facing claims so Track A does not
  overstate readiness for those audit surfaces.

---

## 3. API / interface findings

### A-1 — `obs.setup()` ownership boundary is implemented correctly for daemon boot

**Pass**

I verified the central ownership claim from §2.4:
- `daemon.py` no longer owns `logging.basicConfig(...)` at module import
- `main()` now calls `obs.setup("hassaleh-daemon", obs_env)`
- the module logger is rebound through `obs_logging.rebind_daemon_logger()`

References:
- `src/hassaleh/daemon.py:1377`
- `src/hassaleh/daemon.py:1380`

That resolves the old basicConfig collision and establishes a single
observability entrypoint.

### A-2 — service enum enforcement is correct and useful

**Pass**

`logging.py` enforces a closed service enum and `setup()` raises on unknown
service names:
- `SERVICE_ENUM` at `src/hassaleh/obs/logging.py:15-20`
- `setup()` validation at `src/hassaleh/obs/logging.py:208-211`

That matches §3.1’s closed-enum requirement.

### A-3 — output schema is only partially asserted by tests

**Non-blocking**

The JSON schema test checks the expected top-level logging fields for one
representative event, but Track A does not yet encode a reusable schema test or
validator for the required-field contract from §3.1. This is not a blocker by
itself, but it leaves future drift likely.

---

## 4. PII handling verdict (redaction correctness, schema adherence)

### PII verdict: **partially correct, not yet sufficient for full Track A contract**

What is good:

- direct string redaction for email / bearer / phone is present
  - `src/hassaleh/obs/logging.py:21-24`, `:88-93`
- secret-key family redaction covers:
  - `api_key`
  - `api_key_hash`
  - `api_key_lookup`
  - `lookup`
  - via `_SECRET_KEY_RE` and `_redact_mapping()`
  - `src/hassaleh/obs/logging.py:23`, `:99-107`
- `cypher_params` are shape-only, not value-logged
  - `src/hassaleh/obs/logging.py:99-102`
- `message_content` is truncated and `intent_payload.content` becomes hash-only
  - `src/hassaleh/obs/logging.py:107-112`
- the unit test verifies representative cases
  - `tests/test_obs_logging.py:37-72`

What is still weak / incomplete:

- there is no dedicated enforcement for future `param_shape` audit events from
  §3.1.1 — correctness currently depends on caller discipline
- there is no explicit protection against logging raw tool invocation args under
  arbitrary field names, despite §3.1.2’s “no command arguments in the audit
  log” rule
- `source_ip` handling matches the implement note, but the frozen plan says
  “INFO full IP allowed, DEBUG redact to /24” — that policy is unusual, and the
  tests only exercise the INFO/off case, not the DEBUG redaction branch

So the redaction core is competent, but I cannot sign it off as fully adherent
against the **full** Track A audit/logging contract as written.

---

## 5. `HASSALEH_OBS=off` no-op verification (confirm it is truly zero-cost)

### Verdict: **No — not a true zero-cost no-op**

Evidence:

- `obs.setup()` always configures logging, even when `HASSALEH_OBS=off`
  - `src/hassaleh/obs/logging.py:152-206`
- in off mode, it still:
  - clears root handlers
  - installs a new `StreamHandler`
  - installs a `structlog.stdlib.ProcessorFormatter`
  - configures structlog globally
  - binds contextvars
- `obs.setup()` also calls `setup_metrics()` unconditionally
  - `src/hassaleh/obs/__init__.py:20-23`

The existing test only proves:
- off mode uses text fallback
- output is not JSON
- a log line still appears

Reference:
- `tests/test_obs_logging.py:75-92`

It does **not** prove the S5 contract:
- zero new processes
- zero network calls
- zero new formatters

And the code itself contradicts “zero new formatters,” because a new
`ProcessorFormatter` is explicitly installed in all modes.

This is the clearest blocker in the review.

---

## 6. Test coverage observations (non-blocking)

1. **No test for unknown service-name rejection**  
   `setup()` has a useful guard, but no direct test asserts it.

2. **No DEBUG-mode `source_ip` redaction test**  
   The `/24` masking branch is untested.

3. **No regression test for “off mode does not initialize later tracks”**  
   Given the S5 contract, there should be a test asserting that off mode does
   not invoke metrics/tracing setup.

4. **No schema-lock test for required §3.1 fields across render modes**  
   Only one representative JSON event is checked.

5. **No test for future audit-log contract hardening**  
   Specifically:
   - `param_shape` contains shapes only
   - tool audit log never emits raw argv / args

6. **No negative tests for nested arbitrary secret-like keys beyond the sample**  
   The regex looks sensible, but coverage is narrow.

---

## 7. VERDICT

Track A is **not CLEAN** as a retrospective review.

The daemon integration / root-handler ownership change is good, and the core
redaction helpers are competent. But two issues remain incompatible with the
frozen Sprint 12 Track A contract:

1. `HASSALEH_OBS=off` is not a true zero-cost no-op (blocking against §2.4 + S5)
2. the logging substrate overstates readiness for §3.1.1 / §3.1.2 audit-log
   contracts without schema-level safeguards for those structures

Because Track A is already on trunk, these are **mandatory follow-up commits**
before Track B should treat this logging surface as stable.

**VERDICT: CHANGES-REQUESTED**
