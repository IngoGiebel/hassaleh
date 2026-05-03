# Sprint 12 Track B — Security + API review (Inanna)

Date: 2026-04-21
Reviewer: Inanna (worker-opus, security + API reviewer persona)
Branch: `trunk`
Commit reviewed: `e6fd6b6` — *obs(track-b): land metric catalog + cardinality guards + /metrics endpoint*
Plan reference: `docs/sprint-12-plan.md` §3.2, §3.2.1, §3.2.2, §3.2.3, §5 Track B, §8
Self-attested summary: `docs/sprint-12-track-b-implement.md`

---

## 1. Scope

This review covers the Track B metrics implementation landed on `trunk` at `e6fd6b6`. Files read in full:

- `src/hassaleh/obs/metrics.py` (419 lines) — metric catalog, cardinality guards, exception taxonomy.
- `src/hassaleh/obs/context.py` (66 lines) — `contextvars`-backed request/intent context.
- `src/hassaleh/obs/cypher_patterns.py` (24 lines) — closed-list pattern registry.
- `src/hassaleh/obs/__init__.py` (189 lines) — Track A + Track B wiring, graceful fallback when `prometheus_client` is absent.
- `src/hassaleh/daemon.py` — `/metrics` endpoint mount (lines 1346–1362), metrics handler (lines 1401–1411), cached gauge refresh (lines 1419–1460), sweep-site heartbeat-miss instrumentation (lines 918–975), tool-invocation and error hooks (lines 445–552).
- `src/hassaleh/sdk.py` — auth / intent / cypher-pattern instrumentation (lines 150–261, 291–357).
- `src/hassaleh/heartbeat_sdk.py` — auth + heartbeat-receive + interval instrumentation (lines 115–232).
- `src/hassaleh/intent_sdk.py` — auth + intent-submit instrumentation (lines 88–283).
- `tests/test_obs_metrics.py` (256 lines) — five Track B tests.
- `docs/sprint-12-plan.md` §3.2, §3.2.1, §3.2.2, §3.2.3, §5 Track B, §8.

Out of scope for this review: Track A (logging), Track C (tracing), Track D/E/F artifacts, Prometheus recording rules (Prometheus-side YAML owned by Track D), and broader Sprint 11 IL- hardening.

---

## 2. Security findings (blocking)

**None.**

Detailed verification:

### 2.1 Secrets / PII in metric names or label values

- **No secrets in labels.** Grepped every `labels(...)` call site. Label values come from: `agent_id` (internal UUID), `result`/`type`/`source` (closed enums), `pattern`/`command` (closed enums, `__other__`-bucketed), `stage` (internal enum), `state` (Neo4j lifecycle), `version`/`env` (pyproject + `HASSALEH_ENV`). None sources from `api_key`, `api_key_hash`, `api_key_lookup`, `heartbeat_token`, Cypher text, stdout/stderr, or user-typed text.
- **Metric names are static literals** defined in `metrics.py:31–47` and never interpolated. No user input reaches metric names.
- **Bcrypt histogram is timing-observable but not a new oracle.** `observe_auth_bcrypt_duration` (`metrics.py:329–333`) is only called when the lookup-hash matched an Agent row (`sdk.py:184`, `heartbeat_sdk.py:149`, `intent_sdk.py:128`). An observer can already distinguish `result="unknown_agent"` from `result="bcrypt_fail"` via `hassaleh_auth_attempts_total`; the histogram adds no finer-grained leak than that counter already exposes.

### 2.2 Info leak via label cardinality

- `pattern` — closed list of 19 entries (≤ 50 cap from plan), enforced by `_normalize_pattern` (`metrics.py:262–268`). Unknown values → `__other__` + WARN. **Safe.**
- `command` — closed frozenset of 13 entries (`metrics.py:48–64`), enforced by `_normalize_command` (`metrics.py:271–277`). Unknown values → `__other__` + WARN. **Safe.**
- `agent_id` — **uncapped**, as explicitly allowed by plan §3.2.3 ("no cap today (dev load); must be bounded before multi-tenant"). Labelled only after `_authenticate()` succeeds (`sdk.py:292`, `heartbeat_sdk.py:159`, `intent_sdk.py:204`), so an unauthenticated attacker cannot inflate this dimension. The daemon's sweep site (`daemon.py:941,962`) only emits `agent_id`s already present in Neo4j. **Acceptable in v1; flagged in the plan as a v2 prerequisite.**
- `state` — supplied by `set_active_intents(counts: Mapping[str, int])` (`metrics.py:405–414`) from Neo4j's `i.lifecycle` values (`daemon.py:1448–1452`). Lifecycle is controlled by daemon-side code paths, not agent input; cardinality is bounded in practice but **not enforced by Track B**. Not a blocker — if a non-standard lifecycle ever leaks into the graph it becomes a data issue, not a metrics-injection issue.
- `result` / `type` / `source` / `stage` — free strings at the Python API surface (see §3 below). All current call sites use closed-enum values. Not a present-day leak vector; tracked under API findings.

### 2.3 `/metrics` endpoint exposure and auth

- **Endpoint is correctly gated** (`daemon.py:1352–1353`):
  ```python
  if obs.is_metrics_enabled():
      self._health_app.router.add_get("/metrics", self._metrics_handler)
  ```
  `is_metrics_enabled()` returns `True` only when `HASSALEH_OBS` is set to something other than `"off"` (`metrics.py:107–108`, default `"off"`).
- **`HASSALEH_OBS=off` regression coverage** is present in `tests/test_obs_metrics.py:99–128` — asserts `/metrics` is NOT in `router.routes()` when obs is off. Correct.
- **No authentication** is applied to `/metrics`. This matches standard Prometheus scrape convention and is consistent with `/health` behavior on the same port. Plan §2.3 / §3.2 does not mandate endpoint auth; Prometheus is expected to scrape from the same Docker network. **Not a Track B blocker**, but see §9 advisory item.
- **Bind address defaults to `0.0.0.0`** via `daemon.py:1349` (inherited from the pre-existing `/health` endpoint). This is config-controlled (`health_endpoint_bind` in `DaemonConfig`), not newly introduced by Track B, and production deployment is expected to front it with the Docker network + host-level firewalling in line with §2.3.

**Conclusion: no blocking security issue.**

---

## 3. API / interface findings

### 3.1 Metric catalog adherence to §3.2

All 15 metrics in plan §3.2 are registered with matching name, type, and label set:

| §3.2 metric | Type | Labels (plan) | Implementation | Verdict |
|---|---|---|---|---|
| `hassaleh_intent_submitted_total` | counter | `agent_id, result` | `metrics.py:115–120` | ✓ |
| `hassaleh_intent_duration_seconds` | histogram | `stage` | `metrics.py:121–127` | ✓ |
| `hassaleh_auth_attempts_total` | counter | `result` | `metrics.py:128–133` | ✓ |
| `hassaleh_auth_bcrypt_duration_seconds` | histogram | — | `metrics.py:134–139` | ✓ |
| `hassaleh_heartbeat_received_total` | counter | `agent_id` | `metrics.py:140–145` | ✓ |
| `hassaleh_heartbeat_missed_total` | counter | `agent_id` | `metrics.py:146–151` | ✓ |
| `hassaleh_heartbeat_interval_seconds` | histogram | `agent_id` | `metrics.py:152–158` | ✓ |
| `hassaleh_cypher_query_duration_seconds` | histogram | `pattern` | `metrics.py:159–165` | ✓ |
| `hassaleh_cypher_query_slow_total` | counter | `pattern` | `metrics.py:166–171` | ✓ |
| `hassaleh_tool_invocation_total` | counter | `agent_id, command, result` | `metrics.py:172–177` | ✓ |
| `hassaleh_tool_invocation_duration_seconds` | histogram | `command` | `metrics.py:178–184` | ✓ |
| `hassaleh_errors_total` | counter | `type, source` | `metrics.py:185–190` | ✓ |
| `hassaleh_active_agents` | gauge | — | `metrics.py:191–195` | ✓ |
| `hassaleh_active_intents` | gauge | `state` | `metrics.py:196–201` | ✓ |
| `hassaleh_version_info` | gauge | `version, env` | `metrics.py:202–207` | ✓ |

Default histogram buckets `(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)` (`metrics.py:27`) exactly match plan §3.2.3. `SLOW_QUERY_SECONDS = 0.100` (`metrics.py:30`) matches the "> 100 ms" slow-query threshold in `hassaleh_cypher_query_slow_total`'s description.

### 3.2 Exception taxonomy vs. §3.2.2

`classify_exception` (`metrics.py:284–312`) matches the plan table 1:1. The isinstance-chain ordering is safe because all hassaleh error classes inherit directly from `Exception` (verified in `src/hassaleh/errors.py:4–35`), so no subclass relationship silently reroutes a bucket.

| §3.2.2 row | Implementation line | Verdict |
|---|---|---|
| `AuthenticationError`, `AgentNotFoundError`, `AgentDisabledError`, `HeartbeatTokenMismatchError` → `auth` | 285–294 | ✓ |
| `CapabilityNotFoundError`, `CapabilityDeniedError`, `CapabilityParamError` → `capability` | 295–303 | ✓ |
| `AccessDeniedError`, stdlib `PermissionError` → `permission` | 304–305 | ✓ |
| `ValueError`, `TypeError` → `validation` | 306–307 | ✓ |
| stdlib `TimeoutError` → `timeout` | 308–309 | ✓ |
| `Neo4jError`, `ServiceUnavailable`, `TransientError` → `graph` | 310–311 | ✓ |
| Everything else → `internal` | 312 | ✓ |

One ordering subtlety worth recording (not a bug): `capability` is checked before `validation`, so if `CapabilityParamError` later subclasses `ValueError` for ergonomic reasons it still buckets as `capability`. Today all classes are direct `Exception` subclasses; the ordering is defensive.

### 3.3 Cypher pattern classification vs. §3.2.3

- `CYPHER_PATTERNS` in `cypher_patterns.py` is 19 entries including `__other__` — within the ≤ 50 cap.
- Unknown patterns are bucketed and warned (`metrics.py:262–268`) via `_warn_unknown_label` (`metrics.py:255–259`), which dedups warnings per distinct unknown value via `warned_patterns` — good operational hygiene (no log flooding).
- The plan's *sample* `heartbeat.sweep` pattern was replaced by the **more specific** `daemon.sweep.active_to_stale` and `daemon.sweep.stale_to_inactive`, which is consistent with §8's per-transition heartbeat-miss site. This is an improvement, not a deviation.
- Call-site rollout: observed `pattern=` kwargs at `sdk.py:258–261` (cypher query), `sdk.py:401` (`intent.lookup_owned`), `daemon.py:937–940`, `958–961`, `1437–1440`, `1453–1456`. Coverage is not universal — self-attested follow-up in `sprint-12-track-b-implement.md` explicitly calls this out. Acceptable for v1.

### 3.4 Free-string label values not in a closed-enum guard

`result`, `type`, `stage`, `source`, and `state` labels are accepted as arbitrary strings at the Python API surface. Plan §3.2.3 only mandates **runtime enforcement** for `pattern` and `command` — so this matches the plan's letter. All current call sites use the documented enums. Recording this as an **advisory**, not a finding, because the plan does not require defensive enforcement here.

### 3.5 `stage` enum coverage vs. §3.2

Plan §3.2 enumerates `stage ∈ {auth, validate, execute, persist, result}`. Observed call sites emit `auth`, `validate`, `persist`. `execute` and `result` stages are not wired at `intent_sdk.py` / `sdk.py`, which is consistent with Track B being submit-side only (daemon-side execution instrumentation is light). Not a blocker — the metric is still usable with the subset — but noted as an API completeness observation.

---

## 4. Cardinality enforcement verdict

**PASS.**

- `_normalize_pattern` (`metrics.py:262–268`): accepts `pattern` ∈ `_CYPHER_PATTERN_SET`; otherwise buckets into `UNKNOWN_BUCKET = "__other__"` (`metrics.py:28`) and emits a WARN log via `_warn_unknown_label` (`metrics.py:255–259`). Warnings are deduped in `state.warned_patterns` so a single bad pattern does not flood logs.
- `_normalize_command` (`metrics.py:271–277`): symmetrical treatment for tool-invocation commands against `_TOOL_COMMANDS` (`metrics.py:48–64`).
- Both helpers are unconditionally invoked on every `observe_cypher_query` (`metrics.py:367–374`) and `record_tool_invocation` (`metrics.py:377–395`) call path — no bypass.
- Dedicated test: `test_cardinality_guard_unknown_pattern_buckets_to_other_and_warns` (`tests/test_obs_metrics.py:148–164`) asserts both bucketing and WARN emission for an unknown pattern.

Minor gap: there is no analogous test for `_normalize_command`. Listed under §7 as a non-blocking coverage observation.

---

## 5. `/metrics` endpoint verdict

**PASS.**

- **Mounted only when obs is on:** `daemon.py:1352–1353` gates the route registration on `obs.is_metrics_enabled()`. Regression-tested in `test_metric_registration_and_obs_off_noop` (`tests/test_obs_metrics.py:99–128`).
- **Renders only when obs is on:** `_metrics_handler` (`daemon.py:1401–1411`) calls `obs.render_metrics()`, which returns `None` if state was never initialized (`metrics.py:248–252`), and raises `HTTPNotFound` — so even if someone binds the route externally, no payload is emitted with obs off.
- **Content-Type** uses `METRICS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"` (`obs/__init__.py:28`), which matches `prometheus_client.CONTENT_TYPE_LATEST`. Scrapeable by Prometheus as-is.
- **Gauge-refresh on mount:** `_start_health_endpoint` calls `_refresh_metrics_snapshot(force=True)` immediately after the route is added (`daemon.py:1361`), so the first scrape has non-placeholder gauges. Subsequent scrapes use a 30s cache (`_metrics_cache_ttl_sec`, `daemon.py:187`) exactly matching plan §3.2's "periodic (30s) Neo4j query ... cached in-process".
- **Graceful refresh failure:** `_refresh_metrics_snapshot` wraps the Neo4j calls in a try/except (`daemon.py:1428–1460`) that logs a WARNING but does not raise — so a transient Neo4j outage does not crash the scrape path.

---

## 6. Recording rules + heartbeat instrumentation site (§8)

### 6.1 Recording rules (§3.2.1)

Plan §3.2.1 places the recording rules (`hassaleh:agent_uptime_ratio:1h`, `hassaleh:intent_duration_seconds:p99_1h`) in `prometheus/rules/hassaleh-recording.yml`. That is **Prometheus-side configuration owned by Track D (Dashboards)** per plan §5 / `Timeline`. Track B is not expected to land that YAML. **Not in scope; not a Track B blocker.**

### 6.2 Heartbeat-miss instrumentation site (§8)

Plan §8 requires: *"Increment `hassaleh_heartbeat_missed_total` inside the daemon sweep loop that transitions `agent.lifecycle` between `active`/`stale`/`inactive`. Each `active → stale` or `stale → inactive` transition is exactly one missed beat."*

Verified in `daemon.py:918–975` (`HassalehDaemon._sweep`):

- `active → stale` branch (`daemon.py:923–942`): returns the `a.id` of every transitioned agent, then iterates `for agent_id in active_to_stale: obs.record_heartbeat_missed(agent_id)` (`daemon.py:941–942`).
- `stale → inactive` branch (`daemon.py:944–963`): identical pattern (`daemon.py:962–963`).

Each Cypher write is also surrounded by `time.perf_counter()` bracketing that feeds `obs.observe_cypher_query(..., pattern="daemon.sweep.active_to_stale" | "daemon.sweep.stale_to_inactive")` — this matches the closed-pattern list and gives per-sweep cypher-duration histograms.

**Verdict: implementation lands at exactly the site §8 names, with 1:1 transition-to-increment semantics.**

---

## 7. Test coverage observations

Present (`tests/test_obs_metrics.py`, 5 tests):
1. `test_metric_registration_and_obs_off_noop` — covers §5 "not exposed when obs=off" requirement and `version_info` sample value.
2. `test_classify_exception_branches` — exhaustive §3.2.2 taxonomy coverage.
3. `test_cardinality_guard_unknown_pattern_buckets_to_other_and_warns` — covers §3.2.3 pattern enforcement + WARN log.
4. `test_metrics_endpoint_exposes_all_defined_metrics` — asserts every `METRIC_NAMES` entry appears in the rendered `/metrics` body.
5. `test_heartbeat_and_intent_counters_increment_correctly` — covers SDK instrumentation.

**Non-blocking coverage gaps** (mirrors the self-attested follow-ups):
- No test for `_normalize_command` bucketing of unknown commands. Symmetry with the pattern test would be a cheap addition.
- No test exercises the daemon `_sweep` path that increments `hassaleh_heartbeat_missed_total`. The plan calls out this site explicitly in §8; a targeted test with a fake Neo4j driver would close the loop.
- No test exercises `_refresh_metrics_snapshot` (the `active_agents` / `active_intents` gauge refresh path) end-to-end.
- No test covers `observe_intent_duration("execute", ...)` or `("result", ...)` stage values — consistent with §3.5's observation that those stages aren't yet wired.

None of these gaps invalidate the security posture of Track B; each is a hardening opportunity and is already acknowledged in `docs/sprint-12-track-b-implement.md` §"Follow-up items".

---

## 8. VERDICT

**CLEAN.**

The Track B implementation at `e6fd6b6` matches plan §3.2, §3.2.2, §3.2.3, and §8 on every load-bearing point:
- Full §3.2 metric catalog (all 15 metrics, correct types + label sets).
- Exception taxonomy (`classify_exception`) 1:1 with §3.2.2.
- Closed-list `pattern` + `command` cardinality guards with `__other__` bucketing and WARN log dedup (§3.2.3).
- `/metrics` endpoint only mounted when `HASSALEH_OBS != "off"`, with a regression test.
- Heartbeat-miss emitted at exactly the daemon-sweep sites §8 names, with the correct "one transition = one missed beat" semantics.
- No secrets, PII, or user-controlled strings reach metric names or label values.
- `prometheus_client`-absent fallback keeps the package importable.

No blocking issues. The non-blocking observations (coverage gaps, free-string label-enum non-enforcement for `result`/`type`/`stage`/`source`/`state`, `0.0.0.0` bind default inherited from `/health`) are either called out in the plan or recorded as follow-ups in the self-attested summary.

**Track B is cleared for merge closure.**

---

## 9. Non-blocking advisory items (for the sprint retro, not for this review's verdict)

1. Symmetric `_normalize_command` test to mirror the pattern test.
2. Daemon-sweep integration test that asserts `hassaleh_heartbeat_missed_total` increments on transition.
3. Consider a runtime `stage` enum guard (cheap defense-in-depth; keeps the §3.2 contract honest even under future refactors).
4. If `/metrics` will ever be reachable outside the Docker network in production, add a scrape-token auth — out of scope for Track B, flag for Track E (compose) sign-off.
5. Post-v1: revisit `agent_id` cardinality bounding before multi-tenant (already in plan §3.2.3).
