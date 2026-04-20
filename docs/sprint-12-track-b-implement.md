# Sprint 12 Track B — Metrics implementation

Date: 2026-04-21
Branch: `trunk`
Plan reference: `docs/sprint-12-plan.md` §3.2, §3.2.1, §3.2.2, §3.2.3, §5 Track B, §8
Status: implemented with targeted Track B pytest green

## Scope

This change set covers the Track B metrics foundation only:
- metric catalog
- Prometheus registry + `/metrics` handler
- cardinality guards for `pattern` and `command`
- exception classification helper
- lightweight context helpers for instrumentation call-sites
- focused unit tests

It does **not** wire the full daemon/heartbeat instrumentation rollout yet.
That follow-up remains intentionally separate per the task constraints.

## Files changed

### `src/hassaleh/obs/metrics.py`
- new file
- 332 lines
- owns the Track B metric catalog and registry lifecycle

Implemented here:
- all 14 metrics from plan §3.2
- `CollectorRegistry`-backed metric registration
- `/metrics` HTTP response handler for aiohttp call-sites
- `MetricsState` wrapper exposing enabled/registry/handler state
- `setup_metrics()` opt-in registration path
- `classify_exception()` per plan §3.2.2 taxonomy
- `guard_pattern()` and `guard_command()` with WARN + `__other__` bucketing
- helper functions for incrementing counters / observing histograms / setting gauges

### `src/hassaleh/obs/context.py`
- new file
- 44 lines
- provides request/intent context helpers for instrumentation sites

Implemented here:
- contextvar-backed observation context
- `obs_context(...)` context manager
- `bind_obs_context(...)`
- `get_obs_context()`
- `current_agent_id()` / `current_intent_id()`

### `src/hassaleh/obs/__init__.py`
- modified
- 76 lines
- extends Track A’s `obs.setup()` entrypoint

Track B change:
- `setup()` now calls Track A logging setup first, then `setup_metrics(...)`
- metrics setup uses Track A’s resolved `env` and package `version`
- keeps Track C imports optional and untouched

### `tests/test_obs_metrics.py`
- new file
- 124 lines
- focused Track B unit coverage

Covered cases:
- metric registration and registry-backed `/metrics` exposure
- `obs=off` no-op behavior
- `classify_exception()` taxonomy branches
- cardinality guard behavior for unknown Cypher patterns / commands
- heartbeat + intent counter increments reflected in Prometheus output

### `pyproject.toml`
- modified
- 51 lines
- adds `prometheus_client>=0.20`

## Metric catalog delivered

Track B implemented all 14 plan metrics from §3.2:

1. `hassaleh_intent_submitted_total`
2. `hassaleh_intent_duration_seconds`
3. `hassaleh_auth_attempts_total`
4. `hassaleh_auth_bcrypt_duration_seconds`
5. `hassaleh_heartbeat_received_total`
6. `hassaleh_heartbeat_missed_total`
7. `hassaleh_heartbeat_interval_seconds`
8. `hassaleh_cypher_query_duration_seconds`
9. `hassaleh_cypher_query_slow_total`
10. `hassaleh_tool_invocation_total`
11. `hassaleh_tool_invocation_duration_seconds`
12. `hassaleh_errors_total`
13. `hassaleh_active_agents`
14. `hassaleh_active_intents`
15. `hassaleh_version_info`

Note on counting: the plan table lists “all 14 metrics” but enumerates 15 names if
`hassaleh_version_info` is counted separately. This implementation includes
`hassaleh_version_info` because it is explicitly present in §3.2 and useful for
Prometheus `_info`-style introspection.

## Opt-in / no-op behavior

Track B preserves the plan’s `HASSALEH_OBS=off` default.

Behavior implemented in `setup_metrics()`:
- when `HASSALEH_OBS=off` (or unset)
  - no registry is created
  - no metrics handler is exposed
  - metric recording helpers become no-ops
- when `HASSALEH_OBS=on`
  - a fresh `CollectorRegistry` is created
  - all Track B metrics are registered
  - `/metrics` handler becomes available
  - `hassaleh_version_info{version,env}` is seeded to `1`

This keeps Track B aligned with S5’s “opt-in only” contract.

## Exception taxonomy (`classify_exception()`)

Implemented mapping from §3.2.2:
- `AuthenticationError`, `AgentNotFoundError`, `AgentDisabledError`, `HeartbeatTokenMismatchError` → `auth`
- `CapabilityNotFoundError`, `CapabilityDeniedError`, `CapabilityParamError` → `capability`
- `AccessDeniedError`, `PermissionError` → `permission`
- `ValueError`, `TypeError` → `validation`
- `TimeoutError` → `timeout`
- Neo4j exceptions (`Neo4jError`, `ServiceUnavailable`, `TransientError`) → `graph`
- everything else → `internal`

`record_error(...)` also normalizes invalid `source` labels to a safe fallback.

## Cardinality enforcement

Per §3.2.3 and the §8 ownership row, Track B enforces runtime guards on:
- Cypher `pattern`
- tool `command`

Behavior:
- known value → recorded unchanged
- unknown value → bucketed into `__other__`
- a WARN log is emitted with the original label value for follow-up

Currently implemented closed sets:
- `CYPHER_PATTERNS`
  - includes required example labels plus `__other__`
- `TOOL_COMMANDS`
  - `invoke_command`
  - `exec_as_user`
  - `__other__`

This is enough to enforce bounded cardinality now while leaving room for the
pattern list to expand as instrumentation sites land.

## `/metrics` endpoint wiring

Track B delivers an aiohttp-compatible handler:
- `metrics_handler(request) -> web.Response`

Behavior:
- obs off / no registry → HTTP 404 (`observability disabled`)
- obs on → Prometheus exposition via `generate_latest(registry)` and
  `CONTENT_TYPE_LATEST`

This is the handler surface needed for follow-up daemon wiring without forcing
that rewiring into the current commit.

## Gauge / instrumentation readiness

Track B includes helper functions for future instrumentation call-sites:
- `record_intent_submitted(...)`
- `observe_intent_duration(...)`
- `record_auth_attempt(...)`
- `observe_auth_bcrypt(...)`
- `record_heartbeat_received(...)`
- `record_heartbeat_missed(...)`
- `observe_heartbeat_interval(...)`
- `observe_cypher_query(...)`
- `record_tool_invocation(...)`
- `record_error(...)`
- `set_active_agents(...)`
- `set_active_intents(...)`

The gauges are self-contained and usable by daemon polling / sweep paths later.
The heartbeat-miss ownership note from §8 is respected: the actual increment at
`heartbeat_sdk.py:168` is **not** wired in this commit, by instruction.

## Targeted pytest

Command run:

```bash
source .venv/bin/activate && .venv/bin/pytest tests/test_obs_metrics.py -q
```

Observed result:

```text
.....                                                                    [100%]
5 passed in 0.84s
```

Summary line:
- `5 passed in 0.84s`

## Deviations from §3.2

### 1) Plan says “14 metrics” but enumerates 15 names
I implemented `hassaleh_version_info` as well, because it is explicitly listed in
§3.2. If reviewers want the deliverable count normalized, the plan wording should
be clarified rather than dropping the metric.

### 2) Closed pattern list is intentionally minimal in this commit
The plan allows up to 50 Cypher patterns. This commit ships a bounded starter set
with `__other__` instead of trying to guess every future instrumentation label.
That keeps cardinality enforcement correct now without overcommitting to labels
that are not yet wired anywhere.

### 3) `/metrics` handler exists, but daemon route wiring is deferred
The endpoint logic is implemented and testable, but the daemon HTTP app was not
modified in this commit beyond providing the handler surface. That is deliberate
because the task explicitly said not to do the broader rewiring here.

## Follow-up items

1. Wire `metrics_handler` into the daemon health/HTTP app when Track B enters
   review/merge follow-up.
2. Instrument real daemon / SDK / heartbeat call-sites using the helper functions.
3. Add the heartbeat-miss increment at the plan-owned instrumentation site noted
   in §8 (`heartbeat_sdk.py:168` / sweep transition path) after review.
4. Expand `CYPHER_PATTERNS` as real query instrumentation lands.
5. Add integration coverage once Track F observability tests are in place.

## Final assessment

Track B now provides the reusable metrics substrate required by the plan:
- complete metric catalog
- bounded-label recording helpers
- exception classification
- opt-in registry lifecycle
- `/metrics` handler surface
- unit tests for the critical behaviors

That is enough to unblock review and subsequent call-site instrumentation without
crossing into Track C or broader daemon rewiring prematurely.
