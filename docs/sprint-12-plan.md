# Sprint 12 — Observability v1

**Author:** Dione  
**Status:** DRAFT v1 — Round-1 reviews incorporated; pending Round-2.  
**Created:** 2026-04-18  
**Revised:** 2026-04-19 (addresses all Round-1 CRs from Inanna + gemini-reviewer)  
**Sprint timeline (tentative):** starts when Sprint 11 is fully green; ~5 working days.  
**Review process:** This plan itself is subject to the agentic review workflow
described in §9 below. Round-1 reviews:
`docs/sprint-12-plan-review-1-inanna.md`,
`docs/sprint-12-plan-review-1-gemini.md`.

**Artefact ↔ plan relationship (clarified):** §1, §2, §3 of this plan **are**
the three spec artefacts (Requirements, Architecture, Interface). Day 1 of
the sprint consists of polishing them and, if the reviewers request, splitting
them into separate files `docs/sprint-12-requirements.md`,
`docs/sprint-12-architecture.md`, `docs/sprint-12-interfaces.md`.
v1 of this plan treats all three as co-located for ease of cross-referencing
during review; the split (if any) is a pure-file-move task that does not
change content.

---

## 0. Executive Summary

Sprint 12 introduces **Observability v1** for Hassaleh: structured logging,
Prometheus metrics, and OpenTelemetry distributed tracing, fronted by a
Grafana UI. Everything runs in a **separate** docker-compose stack outside
Hassaleh's own compose file.

Every subsequent sprint will produce better, measurable decisions because we
have data — agent-lifecycle visibility, intent-pipeline latencies, auth DoS
detection, and query-performance insight that we currently lack.

The sprint is organised as **three spec artefacts** (Requirements,
Architecture, Interface) → **two review rounds** → **six parallel
implementation tracks** → **integration + smoke** → **final review + merge**.

---

## 1. Requirements (R1–R8)

The "so what?" for each requirement states what we can do once it's in
place that we cannot do today.

| ID | Requirement | So what? |
|----|-------------|----------|
| R1 | **Agent-Lifecycle Visibility** — query live and historical state per `agent_id`: active/deprovisioned, last-seen, heartbeats received, intents currently in-flight. | Directly answers "is agent X healthy?" without diving into the daemon logs. |
| R2 | **Intent-Pipeline Tracing** — every intent gets a `trace_id`; spans cover the stages `auth → validate → execute → persist → result`. | One click in Grafana Tempo shows where an intent spent its time, which makes latency regressions a 30-second diagnosis. |
| R3 | **Authentication Telemetrie** — counts of auth attempts by result bucket (`ok \| bad_key \| unknown_agent \| bcrypt_fail`), avg + p99 bcrypt duration, rate of failures per source. | Detects credential stuffing and O(N)-auth-DoS attempts before they wreck the daemon. |
| R4 | **Graph-Query Performance** — per-Cypher-pattern histogram of query duration, a slow-query log (>100 ms) and Neo4j connection-pool stats. | Separates "slow daemon" from "slow Neo4j" without attaching a profiler. |
| R5 | **Heartbeat Health** — counter of missed heartbeats, histogram of inter-beat intervals per `agent_id`, uptime-SLO per agent (defined in §3.2.1 as a recording rule). | Drives the Sprint-10 heartbeat invariant from "hope it works" to a measurable 99.0 % target. |
| R6 | **Error Classification** — single `hassaleh_errors_total` counter with labels `{type, source}`. The `type` enum is grounded in the actual exception hierarchy; see §3.2.2 for the exception → bucket mapping. | Lets us watch error mix over time and correlate spikes with deploys. |
| R7 | **Tool-Execution Stats** — `invoke_command`/`exec_as_user` counts per `{agent_id, command, result}`, with command duration histograms **and** a structured audit log line per invocation (see §3.1.1). | Feeds security audits (who invoked what?) and capacity planning. |
| R8 | **Resource Utilization** — Neo4j memory/CPU/connection-count (via `neo4j-prometheus-exporter`), per-container memory/CPU/restart-count for all Hassaleh containers (via **cAdvisor**). | Turns "it feels slow" into a datum, and underpins alert thresholds. |

### Non-Requirements (v1 explicitly out)

- Log-search UI beyond Grafana Explore (v2).
- Retention longer than 7 days in hot storage (cold storage is v2).
- User-facing dashboards for external agents (operator-only for now).
- External paging destinations (PagerDuty/Opsgenie). v1 stays on Telegram.
- Profiling/flamegraphs (v3 if ever).
- Multi-tenant dashboards (single-tenant today; a label is enough).
- **Tail-based trace sampling**, including "sample 100 % of traces whose
  duration > 2×p99". v2.
- **Loki → S3 cold storage.** v2.
- **SLOs with explicit error budgets.** v1 ships the uptime recording rule
  (R5) and a 99.0 % target; formal error-budget policy is v2.
- **Log-based anomaly detection** (e.g. LogQL anomaly detectors). v2+.
- **Observability for the observability stack itself** — Loki health,
  Prometheus self-monitoring, Tempo ingest-rate panels. Parked to v2;
  for v1 we rely on Grafana's built-in self-dashboard.

### Success Criteria

- **S1:** Every intent produced by the observability-smoke harness (run with
  `HASSALEH_TRACE_FORCE=1` to defeat the 10 % baseline sampling) has a
  `trace_id` discoverable end-to-end in Grafana Tempo — from SDK
  submit-spans through daemon `auth/validate/execute/persist` spans. In
  production, the 10 % baseline applies; sampled-out intents are still
  counted in metrics.
- **S2:** The `hassaleh-overview` dashboard visualizes R1–R8 live. Each R*
  maps to named panel UIDs (see §3.5) so the test harness can assert
  non-empty data per panel.
- **S2a:** *(R7)* The integration test `tests/test_observability.py::test_tool_counter_increment`
  asserts `hassaleh_tool_invocation_total{command="…"}` increments by
  exactly 1 per `invoke_command` call.
- **S2b:** *(R8)* The same test asserts:
  (a) Prometheus target `neo4j-exporter` has `up==1`,
  (b) Prometheus target `cadvisor` has `up==1`,
  (c) per-container metrics for `hassaleh-daemon` are non-empty.
- **S3:** Four default alert rules are configured and test-fired once:
  - `HassalehAuthFailureRateHigh` — `rate(hassaleh_auth_attempts_total{result!="ok"}[1m]) > 1` for 1 min.
  - `HassalehHeartbeatMissed` — `sum by (agent_id) (increase(hassaleh_heartbeat_missed_total[5m])) > 3` for 5 min.
  - `HassalehSlowQueries` — `rate(hassaleh_cypher_query_slow_total[5m]) > 0.1` for 5 min.
  - `HassalehInternalErrors` — `sum(rate(hassaleh_errors_total{type="internal"}[5m])) / sum(rate(hassaleh_intent_submitted_total[5m])) > 0.01` for 5 min.
- **S4:** The observability stack starts cleanly via
  `docker compose -f ~/projects/observability-stack/docker-compose.yml up`
  *(path matches §2.2 and Track E)*.
- **S5:** Hassaleh works with observability **disabled** (it is opt-in via
  `HASSALEH_OBS=off`, which is the default). A regression test
  `tests/test_observability.py::test_obs_off_is_noop` asserts zero new
  processes, zero network calls, and zero new log-formatters when
  `HASSALEH_OBS=off`.
- **S6:** All relevant interface contracts (log schema, metric names,
  trace span names, dashboard UIDs) are documented in §3 of this plan
  **and** verified by the script `scripts/check-observability-drift.py`
  (Track F) which introspects `src/hassaleh/obs/metrics.py`,
  `src/hassaleh/obs/logging.py`, `src/hassaleh/obs/tracing.py`, and
  `observability/dashboards/*.json` and diffs them against the tables in
  §3.1–§3.5. The script runs in CI and exits non-zero on drift.

---

## 2. Architecture

### 2.1 Stack choice: PLG + Tempo

- **Loki** for logs (indexed by labels, LogQL).
- **Prometheus** for metrics (PromQL).
- **Tempo** for traces (Grafana's trace store; OTLP-native).
- **Grafana** as the single UI for logs/metrics/traces and alerting.
- **Promtail** as the log shipper (tails Hassaleh container stdout/err).
- **neo4j-prometheus-exporter** exposes Neo4j JMX as Prometheus metrics.
- **cAdvisor** exposes per-container memory/CPU/restart-count metrics for
  every Hassaleh container (satisfies R8's daemon-side resource requirement).
  Chosen over `node_exporter` because R8 needs *per-container* granularity,
  not host-level.

### 2.2 Deployment — separate compose stack (Q2)

The observability stack lives **outside** Hassaleh's own compose so that:
- Stopping Hassaleh for upgrades doesn't take down observability (and vice
  versa).
- The stack can eventually be reused by other projects (system-maintenance
  reports, market-pipeline audits).
- Deploy concerns stay separable; operators can restart Grafana without
  touching the graph database.

Repo layout:

```
~/projects/observability-stack/
├── docker-compose.yml                  # Loki + Promtail + Prometheus + Tempo + Grafana
├── README.md
├── .env.example                        # no secrets committed
├── loki/
│   └── loki-config.yml
├── promtail/
│   └── promtail-config.yml             # scrapes docker logs from labeled containers
├── prometheus/
│   ├── prometheus.yml                  # scrape targets incl. hassaleh :9100, neo4j exporter :2004
│   └── rules/
│       ├── hassaleh-alerts.yml
│       └── neo4j-alerts.yml
├── tempo/
│   └── tempo-config.yml                # OTLP receiver at :4318 + storage
├── cadvisor/
│   └── README.md                       # notes on docker.sock mount + per-container scrape
├── grafana/
│   ├── provisioning/
│   │   ├── datasources/*.yml           # preconfigured Loki, Prometheus, Tempo
│   │   ├── dashboards/*.yml            # dashboard loader (points to /var/lib/grafana/dashboards)
│   │   └── alerting/*.yml              # unified alerts — SOURCE OF TRUTH for alert rules + contact points
│   └── dashboards/
│       ├── hassaleh-overview.json
│       ├── hassaleh-intent-deep-dive.json
│       ├── hassaleh-auth-security.json
│       ├── hassaleh-graph-performance.json
│       └── hassaleh-agent-per-id.json
└── networking/
    └── README-bridge-network.md        # how Hassaleh reaches the stack
```

**Alert-rule storage decision (resolves Round-1 CR-7):** Grafana unified
alerts under `grafana/provisioning/alerting/` are the *source of truth* for
alert rules AND contact points. `prometheus/rules/` holds only **recording
rules** (aggregated series, e.g. the R5 uptime-SLO) and alerting rules that
must run in Prometheus for HA purposes — for v1, that means none. This
matches the modern Grafana pattern and connects natively to our Telegram
contact point.

### 2.3 Network topology

A shared docker bridge network `observability-bridge` lets Hassaleh's daemon
reach Loki/Tempo as-needed. Only outbound connections from Hassaleh —
nothing in the observability stack calls back into Hassaleh's runtime
APIs. This avoids turning an observability incident into a Hassaleh
incident.

```
hassaleh-daemon (container)
   ├── stdout JSON logs        ─── (docker logs) ───▶  promtail → loki
   ├── :9100/metrics (prom)    ─── (scrape)      ───▶  prometheus
   └── :4318 OTLP/HTTP traces  ─── (export)      ───▶  tempo

                                                       │
                                                       ▼
                                                     grafana :3000  (Ingo's browser)
```

### 2.4 Hassaleh-side integration

**New internal package:** `src/hassaleh/obs/` with submodules:

- `obs/logging.py` — structlog setup, JSON renderer, service-wide bindings,
  PII redaction middleware.
- `obs/metrics.py` — Prometheus client and the full metric catalog.
- `obs/tracing.py` — OTEL tracer provider, OTLP/HTTP exporter, span decorators.
- `obs/context.py` — `ObservabilityContext` that threads `agent_id`,
  `intent_id`, `trace_id`, `span_id` through request handlers.
- `obs/__init__.py` — single entry point `obs.setup(service_name, env)` called
  from `daemon.py` at boot.

**Logging-handler collision (resolves Round-1 CR-3):** The existing
`daemon.py:33` calls `logging.basicConfig(...)` at module import, which is
a no-op once the root handler exists. v1 resolution:
1. Remove the `logging.basicConfig(...)` call from `daemon.py`.
2. `obs.setup()` becomes the single owner of root-handler configuration.
3. When `HASSALEH_OBS=off`, `obs.setup()` installs a minimal text-formatter
   root handler (functionally equivalent to what `basicConfig` did before)
   so that stderr output is preserved.

This is Track A's *first* task and must land before any other track can
rely on structured log output.

**Opt-in via env vars** (R5 backward-compat):

- `HASSALEH_OBS=on|off` (default off)
- `HASSALEH_ENV=dev|staging|prod` (defaults to `dev`)
- `HASSALEH_OTLP_ENDPOINT=http://tempo:4318/v1/traces`
- `HASSALEH_LOKI_ENDPOINT=http://loki:3100/loki/api/v1/push` (optional; if
  unset, logs only go to stdout and Promtail picks them up)
- `HASSALEH_METRICS_PORT=9100`
- `HASSALEH_LOG_LEVEL=INFO|DEBUG|WARN`
- `HASSALEH_TRACE_SAMPLE_RATE=0.10` (see §3.4 sampling)
- `HASSALEH_TRACE_FORCE=1` (force-sample all traces, including health
  endpoints — smoke-test only)

When `HASSALEH_OBS=off`, `obs.setup()` installs only the minimal stderr
text handler described above — every decorator becomes a pass-through so
performance and behavior are unchanged.

**SDK ↔ daemon trace propagation (resolves Round-1 CR-2):** The SDK
writes Intent nodes directly to Neo4j; there is no HTTP RPC from SDK to
daemon to carry a `traceparent` header. v1 resolution:
1. `schema.cypher` gets a new optional `traceparent` string property on
   `Intent` nodes (W3C format: `00-<trace-id>-<span-id>-<flags>`). The
   constraint is **nullable** so legacy callers are unaffected.
2. When `HASSALEH_OBS=on` in the SDK process, `submit_intent()` captures
   its current active span's context and writes the `traceparent` value
   as part of the Intent node creation.
3. When the daemon picks up an Intent for processing, it looks at the
   `traceparent` property; if present, it creates its processing span as
   a *child of* the SDK's span via `TraceContextTextMapPropagator.extract`.
   Absent → fresh trace, linked to the SDK-side only via `intent_id` in
   metrics/logs.
4. This yields a single distributed trace spanning SDK `submit_intent` →
   Neo4j write → daemon pick-up → `auth/validate/execute/persist/result`,
   viewable end-to-end in Grafana Tempo.

Schema migration is covered in Track C's deliverables; §7 Done criteria
include a `schema.cypher`-update smoke.

**SDK log/trace delivery (resolves Round-1 CR "clarify SDK log/trace
delivery"):** SDK processes run inside external agents' hosts; Promtail
only scrapes Hassaleh's own container logs. v1 rules:
- SDK `obs/logging.py` always writes JSON to stderr.
- If `HASSALEH_LOKI_ENDPOINT` is set in the SDK's env, it pushes logs
  directly via HTTP. Unset → stderr only; the external agent host is
  responsible for shipping further.
- SDK traces are exported via OTLP only if `HASSALEH_OTLP_ENDPOINT` is
  reachable from the SDK host (typical when SDK runs inside the
  observability-bridge network, not typical for external agents).
- The `service` enum in §3.1 includes `hassaleh-sdk` and
  `hassaleh-intent-sdk` and `hassaleh-heartbeat-sdk`; whether a log line
  actually reaches Loki is deployment-dependent.

### 2.5 Resource profile (estimated)

| Service | RAM | Disk/day |
|---------|-----|----------|
| Loki | ~300 MB | ~500 MB |
| Prometheus | ~200 MB | ~300 MB |
| Tempo | ~200 MB | ~1 GB (traces are heavy) |
| Grafana | ~100 MB | ~50 MB |
| Promtail | ~50 MB | — |
| neo4j-exporter | ~40 MB | — |
| cAdvisor | ~80 MB | — |
| **Total** | **~970 MB** | **~2 GB** |

On the 16GB MINISFORUM this is affordable alongside Hassaleh (~1 GB),
OpenClaw (~1.5 GB), and the usual desktop/browser overhead.

### 2.6 Trade-offs noted for the reviewer

- **PLG over EFK/ELK**: lighter memory footprint, native Grafana integration,
  LogQL is powerful enough. ELK would give more features we don't need
  in v1 at ~3× the RAM.
- **Separate stack vs. integrated**: chosen per Ingo's Q2; documented above.
  Cost: one extra bridge network. Benefit: independent failure domains.
- **No Jaeger**: Tempo integrates natively with Grafana and uses object-
  storage semantics that match future cold-storage plans. Jaeger is an
  alternative if we later need its UI, but not for v1.
- **No Pushgateway**: metrics are pulled. If we later add short-lived
  batch jobs that need push-style metric emission, we'll evaluate.

---

## 3. Interface Specification

### 3.1 Structured log schema

All Hassaleh JSON logs adhere to:

```json
{
  "ts": "2026-04-19T13:00:00.123Z",
  "level": "INFO",
  "logger": "hassaleh.daemon.intent",
  "msg": "intent submitted",
  "service": "hassaleh-daemon",
  "version": "0.10.1",
  "env": "dev",
  "agent_id": "agent-a1",
  "intent_id": "intent-42",
  "trace_id": "4a9c2e...",
  "span_id": "1f3d...",
  "duration_ms": 12.4,
  "result": "accepted",
  "extra": {"action": "submit", "target": "agent-b2"}
}
```

**Mandatory top-level fields** (always present):
- `ts` — ISO-8601 UTC, millisecond precision
- `level` — `DEBUG|INFO|WARN|ERROR|CRITICAL`
- `logger` — dotted name, e.g. `hassaleh.daemon.auth`
- `msg` — human-readable short message (≤ 80 chars)
- `service` — closed enum for v1:
  `hassaleh-daemon | hassaleh-sdk | hassaleh-intent-sdk | hassaleh-heartbeat-sdk`.
  The validator rejects unknown values; to add a new service, the enum and
  validator must be updated together in one commit.
- `version` — semver of the hassaleh package
- `env` — `dev|staging|prod`

**Conventional optional fields** (set when applicable):
- `agent_id`, `intent_id`, `trace_id`, `span_id`, `duration_ms`, `result`

**PII & secrets policy** (mandatory — see §8):
- `api_key` — NEVER logged
- `api_key_hash`, `api_key_lookup` — NEVER logged (even partially)
- **Any Cypher parameter named `lookup` or matching `api_key*`** — redacted
  regardless of how it was computed (even server-side SHA-256 hashes), to
  guard against a future "log all cypher_params for debugging" patch
  leaking key hashes. *(Resolves Round-1 non-blocking finding.)*
- `cypher_params` containing other user-supplied strings — redacted to
  length only
- `message_content`, `intent_payload.content` — hash-only or truncated to
  first 40 chars with trailing `…[N chars]`
- `agent_id`, `intent_id` — OK in logs
- `source_ip` — logged at INFO, redacted to /24 at DEBUG

Extras live under `extra.*` so the top level stays predictable.

### 3.1.1 Slow-query log (resolves Round-1 "slow-query log unspec'd")

Every Cypher query whose duration exceeds 100 ms emits a structured log
line *in addition to* incrementing `hassaleh_cypher_query_slow_total`:

```json
{
  "ts": "…",
  "level": "WARN",
  "logger": "hassaleh.daemon.graph.slow_query",
  "msg": "slow cypher query",
  "service": "hassaleh-daemon",
  "agent_id": "agent-a1",
  "intent_id": "intent-42",
  "trace_id": "4a9c2e…",
  "duration_ms": 237.5,
  "pattern": "intent.lookup_by_lookup_hash",
  "param_shape": {"lookup": "str(64)", "agent_id": "str(10)"}
}
```

- `pattern` — the pattern label assigned by §3.2.3 classification.
- `param_shape` — types/lengths only, **never values** (PII policy).
- Raw Cypher statement text is **not** logged.
- Owner: Track A (logging) with Track B (graph instrumentation) for the
  call-site hook.

### 3.1.2 Tool-invocation audit log (resolves Round-1 "R7 log counterpart")

Alongside the `hassaleh_tool_invocation_total` counter, every tool
invocation emits a structured audit log line:

```json
{
  "ts": "…",
  "level": "INFO",
  "logger": "hassaleh.daemon.audit.tool",
  "msg": "tool invoked",
  "service": "hassaleh-daemon",
  "agent_id": "agent-a1",
  "command": "invoke_command",
  "target": "agent-b2",
  "result": "ok",
  "duration_ms": 42.1
}
```

- `command` drawn from the same closed enum as the metric label (§3.2).
- `target` — where applicable.
- No command **arguments** in the audit log (they often contain secrets);
  the metric captures aggregate patterns, the log captures the "who/what/
  when/result" digest.
- Owner: Track A with Track B coordination.

### 3.2 Metrics catalog

| Metric | Type | Labels | Notes |
|--------|------|--------|-------|
| `hassaleh_intent_submitted_total` | counter | `agent_id, result` | Intent submissions |
| `hassaleh_intent_duration_seconds` | histogram | `stage` ∈ `{auth, validate, execute, persist, result}` | End-to-end and per-stage |
| `hassaleh_auth_attempts_total` | counter | `result` ∈ `{ok, bad_key, unknown_agent, bcrypt_fail}` | Auth telemetry |
| `hassaleh_auth_bcrypt_duration_seconds` | histogram | — | Bcrypt perf (target <100 ms p99) |
| `hassaleh_heartbeat_received_total` | counter | `agent_id` | Heartbeat rx |
| `hassaleh_heartbeat_missed_total` | counter | `agent_id` | Detected gaps |
| `hassaleh_heartbeat_interval_seconds` | histogram | `agent_id` | Inter-beat duration |
| `hassaleh_cypher_query_duration_seconds` | histogram | `pattern` | Query performance |
| `hassaleh_cypher_query_slow_total` | counter | `pattern` | Slow-query counter (>100 ms) |
| `hassaleh_tool_invocation_total` | counter | `agent_id, command, result` | Tool stats |
| `hassaleh_tool_invocation_duration_seconds` | histogram | `command` | Tool perf |
| `hassaleh_errors_total` | counter | `type, source` | Classified errors |
| `hassaleh_active_agents` | gauge | — | Live agent count, sourced from a periodic (30s) Neo4j query `MATCH (a:Agent) WHERE a.lifecycle IN ['active','running'] RETURN count(a)`. Cached in-process between scrapes. |
| `hassaleh_active_intents` | gauge | `state` | Live intent count per state, sourced from the same periodic sweep. |
| `hassaleh_version_info` | gauge | `version, env` | Constant 1; follows the Prometheus `_info` convention (labels carry info). |

**cAdvisor metrics** (exposed by the cAdvisor container, no Hassaleh code
needed): `container_memory_usage_bytes`, `container_cpu_usage_seconds_total`,
`container_last_seen` — filter by `container_label_com_docker_compose_service`
in dashboards to isolate Hassaleh containers.

### 3.2.1 Recording rules (Prometheus side)

R5's uptime-SLO is computed as a Prometheus recording rule, not as a
standalone metric. Defined in `prometheus/rules/hassaleh-recording.yml`:

```yaml
groups:
- name: hassaleh.recording
  interval: 1m
  rules:
  # Per-agent rolling-hour uptime ratio. Target: 99.0 %. The SLO-violation
  # alert (`hassaleh:agent_uptime_ratio:1h < 0.99`) is NOT in S3's four
  # baseline alerts; it can be added after v1 once a week of real data
  # establishes sensible hysteresis.
  - record: hassaleh:agent_uptime_ratio:1h
    expr: |
      1 - (
        sum by (agent_id) (increase(hassaleh_heartbeat_missed_total[1h]))
        /
        clamp_min(
          sum by (agent_id) (increase(hassaleh_heartbeat_received_total[1h])),
          1
        )
      )
  # Rolling-hour p99 of intent duration; feeds the slow-intent alert panel.
  - record: hassaleh:intent_duration_seconds:p99_1h
    expr: histogram_quantile(0.99, sum(rate(hassaleh_intent_duration_seconds_bucket[1h])) by (le, stage))
```

### 3.2.2 Error-type taxonomy (resolves Round-1 CR-4)

`hassaleh_errors_total` has labels `{type, source}`. The `type` enum is
grounded in the *actual* exception classes raised in the codebase today
(from `src/hassaleh/errors.py` and stdlib). Track B's `obs/metrics.py`
owns the exception → bucket mapping function:

| Exception class | `type` label |
|-----------------|--------------|
| `AuthenticationError` | `auth` |
| `AgentNotFoundError`, `AgentDisabledError` | `auth` |
| `HeartbeatTokenMismatchError` | `auth` |
| `CapabilityNotFoundError`, `CapabilityDeniedError`, `CapabilityParamError` | `capability` |
| `AccessDeniedError`, stdlib `PermissionError` | `permission` |
| `ValueError`, `TypeError` (validation at API surface) | `validation` |
| stdlib `TimeoutError` | `timeout` |
| Neo4j driver errors, `ServiceUnavailable`, `TransientError` | `graph` |
| Everything else (unhandled) | `internal` |

The `source` label names the raising subsystem: `auth`, `intent`,
`heartbeat`, `tool`, `graph`, `sdk`. A single `internal` bucket catching
"everything else" is deliberate — it's the signal that something uncaught
escaped our taxonomy and needs triage.

The §3.4 sampling boost "boost-to-100 % on error" uses the same table.
Because v1 is strictly head-based (see §3.4), the sampling decision is
made at span start, not at exception time: callers that *know* an
operation is likely to raise `type ∈ {internal, graph, timeout}` (for
example, a retry after a known-flaky path) call
`obs.tracing.hint_error_prone()` to force 100 % sampling for that span.

### 3.2.3 Cypher-pattern label classification (resolves Round-1 CR-5)

`hassaleh_cypher_query_duration_seconds` and `hassaleh_cypher_query_slow_total`
carry a `pattern` label. The enum is caller-supplied, drawn from a closed
list in `src/hassaleh/obs/cypher_patterns.py`:

```python
CYPHER_PATTERNS = [
    "agent.lookup_by_lookup_hash",
    "agent.list_active",
    "intent.create",
    "intent.lookup_by_id",
    "intent.list_pending",
    "heartbeat.sweep",
    # … total ≤ 50
    "__other__",  # catchall for exploratory/ad-hoc queries
]
```

- Each `sdk.query()` / `daemon.query()` call site receives a
  `pattern: str` keyword arg. Track B adds this kwarg as part of the
  instrumentation rollout.
- At runtime, `obs/metrics.py` validates `pattern in CYPHER_PATTERNS`;
  unknown values bucket into `__other__` and emit a WARN log so the
  closed list can grow with intent.
- This gives an unambiguous instrumentation contract and bounds
  cardinality at 50 by construction, not by hope.

**Label-cardinality caps** (enforced by obs/metrics.py):
- `agent_id`: no cap today (dev load); **must be bounded** before we go multi-tenant.
- `pattern` (Cypher): ≤ 50 distinct patterns; patterns are classified by
  template, not by rendered query.
- `command` for tool invocations: drawn from a closed enum; unknown commands
  bucketed into `__other__` to prevent explosions.
- `result` / `type`: closed enums.

Histogram buckets: `{0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10}` seconds for
duration metrics; tuned per metric if the spec changes after first-week data.

### 3.3 Trace span conventions

Every intent creates a **root span** `intent.lifecycle` with attributes
`intent.id`, `intent.sender`, `intent.target`, `intent.action`,
`hassaleh.version`.

Child spans, in order:

| Span | What it covers |
|------|----------------|
| `intent.auth` | API-key → agent lookup + bcrypt verify |
| `intent.validate` | schema + permission checks |
| `intent.execute` | the business operation |
| `intent.persist_result` | writing the result back to the graph |

Span tag namespaces:

- `hassaleh.*` — product-specific (e.g. `hassaleh.agent_id`,
  `hassaleh.intent_id`).
- `db.*` — follows the OpenTelemetry semantic conventions for databases
  (`db.system=neo4j`, `db.statement` redacted, `db.operation`).
- `authn.*` — authentication outcome tags (`authn.result`).

**Trace propagation** (resolves Round-1 CR-2): Because the SDK writes
Intent nodes directly to Neo4j (no HTTP channel), the W3C `traceparent`
value is carried as a **property on the Intent node**, not as an HTTP
header. When the daemon picks up an intent whose node has a non-empty
`traceparent`, it extracts the parent context with
`TraceContextTextMapPropagator.extract({"traceparent": node.traceparent})`
and creates its processing spans as children. Intents without a
`traceparent` property get a fresh daemon-side trace, linked to the SDK
only via `intent_id` in metrics/logs. See §2.4 for the schema change.

### 3.4 Sampling strategy (Q3)

v1 is strictly **head-based sampling** — the sample/drop decision is made
at span start, not after the span finishes. Tail-based sampling (including
duration-based boosts) is parked for v2.

- **Baseline sample rate**: 10 % of traces (`HASSALEH_TRACE_SAMPLE_RATE=0.10`).
- **Boost to 100 %** (head-based):
  - Any request whose caller classifies the operation as `type ∈
    {internal, graph, timeout}` *before* work begins (rare — typically a
    retry after a known-flaky path). Implementation: `obs.tracing.hint_error_prone()`
    sets a sampling attribute that the sampler reads.
  - `HASSALEH_TRACE_FORCE=1` env-var — forces all spans to be sampled,
    including `/health` and `/metrics`. Scope: process-wide. Used only by
    the observability-smoke harness (see S1).
- **Drop to 0 %**: health-check endpoints (`/health`, `/metrics`) by
  default, overridable by `HASSALEH_TRACE_FORCE=1`.
- **Parent-based**: if a client sends `traceparent` with `sampled=1`,
  always record downstream spans (respecting the parent's decision is
  mandatory per W3C).
- **Error-post-sampling**: even when a span was **not** sampled, if it
  *ends* with an exception, its basic metadata (`trace_id`, `intent_id`,
  `exception.type`, `duration_ms`) is still written to a log line at WARN
  level, so operators can at least link metrics to intents via
  `trace_id` even without the full trace. This is the v1 compromise for
  "all errors observable" without tail-sampling.

All sampled-out traces are still **counted** in metrics, so the
histogram view is not biased. Only the full trace detail is absent.

**2 × p99 duration boost was dropped in v1** and added to §1's
Non-Requirements; it requires tail-based sampling which lands in v2.

### 3.5 Dashboard UIDs and contract

Dashboards are committed as JSON with stable UIDs:

| Dashboard | UID | Template variables |
|-----------|-----|--------------------|
| `hassaleh-overview` | `ha-overview-v1` | `env` |
| `hassaleh-intent-deep-dive` | `ha-intent-v1` | `env`, `agent_id` |
| `hassaleh-auth-security` | `ha-auth-v1` | `env` |
| `hassaleh-graph-performance` | `ha-graph-v1` | `env`, `pattern` |
| `hassaleh-agent-per-id` | `ha-agent-v1` | `env`, `agent_id` |

UIDs freeze in v1. Panel UIDs inside each dashboard are also stable so we
can link to specific panels from runbooks.

### 3.6 Alert contract

Alerts are defined as **Grafana unified alerts** under
`grafana/provisioning/alerting/*` (the source of truth per §2.2).
Example rule format:

```yaml
- alert: HassalehAuthFailureRateHigh
  expr: sum(rate(hassaleh_auth_attempts_total{result!="ok"}[1m])) > 1
  for: 1m
  labels:
    severity: warning
  annotations:
    summary: "Auth failure rate > 1/s for 1 min"
    runbook: "file:///home/uranus/projects/hassaleh/docs/runbooks/auth-failure.md"
    suggested_action: "Check hassaleh-auth-security dashboard; correlate with source IPs."
```

Runbooks live at `docs/runbooks/<alert-name>.md`. Grafana renders the
file:// URL as a clickable link; Track D owns the runbook-stub templates
so every rule ships with a matching runbook.

Channel: Grafana → Telegram contact point (reuses the OpenClaw bot).

---

## 4. Review Phases

Three artefacts × up to three review rounds × two reviewer perspectives.

| Artefact | Primary reviewer | Secondary reviewer (clarity) | Workers | Verdicts |
|----------|------------------|------------------------------|---------|----------|
| Requirements doc (§1) | Ingo (product) | gemini-reviewer | human + worker-gemini | approve / change-req |
| Architecture doc (§2) | Inanna (tech) | gemini-reviewer | worker-opus + worker-gemini | clean / change-req |
| Interface spec (§3) | Inanna (security+API) | gemini-reviewer | worker-opus + worker-gemini | clean / change-req |

Every artefact gets both reviewer perspectives: Inanna (tech/security, runs
on worker-opus in a session distinct from Dione's) and gemini-reviewer
(clarity/completeness/internal-consistency, runs on worker-gemini).

- **Round 1**: each reviewer produces a verdict doc:
  `docs/sprint-12-<artefact>-review-1-<reviewer>.md`.
- **Revision**: authors address all CR items in-place on the artefact;
  diff + a short "how each CR was addressed" block goes into the commit.
- **Round 2**: reviewers re-verify only the CR items; may downgrade
  remaining issues to non-blocking.
- **Round 3** (cap): Ingo arbitrates.

This plan document itself follows the same process (§9). v1 of this plan
resolves all Round-1 CRs from Inanna and gemini-reviewer per the change
log in §10.

---

## 5. Implementation Tracks (post-approval, parallel)

| Track | Scope | Worker | Deliverables |
|-------|-------|--------|--------------|
| A — Logging | structlog setup, JSON formatter, PII redaction, service-wide bindings | worker-codex | `src/hassaleh/obs/logging.py`, daemon integration, unit tests |
| B — Metrics | prometheus_client, metric catalog implementation, `/metrics` endpoint, cardinality guards | worker-codex | `src/hassaleh/obs/metrics.py`, `src/hassaleh/obs/context.py`, tests |
| C — Traces | OTEL SDK setup, OTLP exporter, sampling policy, span decorators | worker-gemini | `src/hassaleh/obs/tracing.py`, decorator library, tests |
| D — Dashboards | Grafana JSON for the five dashboards, alert rules | worker-gemini | `observability/*/dashboards/*.json`, `rules/*.yml` |
| E — Docker Compose | Loki/Prometheus/Tempo/Grafana/Promtail/neo4j-exporter services + provisioning | worker-codex | `~/projects/observability-stack/` |
| F — Tests | Integration tests that verify logs/metrics/traces are emitted for every opt-in flag | worker-gemini | `tests/test_observability.py` incl. "obs=off" regression test |

Dione (worker-opus) coordinates integration conflicts (e.g. when Tracks A
and B both touch `daemon.py`). Explicit merge order:

1. **A (Logging)** — first, because every other track relies on the
   structlog handler being installed (`obs.setup()` ownership, per §2.4).
2. **B (Metrics)** — second, because Track D (Dashboards) queries the
   metric names and Track F (Tests) asserts on them.
3. **C (Traces)** — third, carries the schema.cypher `traceparent`
   addition; coordinates with ongoing Sprint 11 IL-01/IL-02 edits to
   schema.cypher (merge conflict risk; Dione arbitrates).
4. **D (Dashboards)** — fourth, lands after B+C so panels can reference
   real metrics and traces.
5. **E (Docker Compose)** — fifth, lands after D because Grafana
   provisioning loads the dashboard JSON produced by D.
6. **F (Tests)** — last by nature; the smoke suite asserts behaviour
   across all tracks.

Tracks A, B, C can run in parallel on authoring; merge serialization
follows the order above. D, E, and F block on their inputs.

---

## 6. Resource allocation

### Workers

| Worker | Backing model | Sprint-12 role |
|--------|---------------|----------------|
| worker-opus | claude-opus-4-7 | Dione (orchestrator, architecture, code reviews) + Inanna (security + API reviews) |
| worker-codex | gpt-5.4 | Implementation author for Tracks A, B, E; architecture support |
| worker-gemini | gemini-3.1-pro-preview | Requirements/Interface spec author, Tracks C, D, F |

### Personas

| Persona | Worker | Responsibilities |
|---------|--------|------------------|
| **Dione** | worker-opus | Sprint orchestration, architecture authoring, code reviews, integration-conflict resolution |
| **Inanna** | worker-opus (different session) | Independent security review + API/interface review + final sign-off |
| **gemini-author** | worker-gemini | Requirements draft, interface spec draft, Tracks C/D/F |
| **codex-author** | worker-codex | Tracks A/B/E implementation |

### Sprint-12 cron + state file

Mirrors the Sprint-11 shape:

- `~/projects/hassaleh/sprint-12-state.json` — blackboard with per-track
  phase (`spec → review → implement → test → merge`) and verdicts.
- `hassaleh-sprint-12-orchestrator` cron (every 2h, starts after Sprint 11
  is closed) dispatches per track.

### Timeline (working days)

| Day | Phase | Parallelism |
|-----|-------|-------------|
| 0 | **Plan approval** (this document) | Ingo + Inanna |
| 1 | Spec authoring | 3 authors in parallel |
| 2 morning | Review round 1 | reviewers in parallel |
| 2 afternoon | CR integration | authors |
| 3 morning | Review round 2 / sign-off | reviewers |
| 3 afternoon | Implementation tracks start | 6 tracks in parallel |
| 4 | Implementation continues | |
| 5 morning | Integration + smoke | worker-opus as Dione |
| 5 afternoon | Final review + merge | Inanna |

Total calendar budget: **~5 working days** after Sprint 11 is closed.

---

## 7. Acceptance & "Done" criteria

**Done for the plan (this doc):**
- One round of CR addressed and §9 sign-off by Ingo + Inanna.

**Done for the sprint:**
- All six implementation tracks merged on trunk.
- `scripts/observability-smoke.sh` runs green end-to-end: starts the
  stack, runs a synthetic intent, asserts logs/metrics/traces appear.
  (No Makefile in the repo; a shell script is canonical. The same
  invocation is wired into CI as `pytest -m smoke tests/test_observability.py`.)
- `scripts/check-observability-drift.py` runs green in CI.
- S1–S6 (including S2a, S2b) from §1 demonstrably green.
- `schema.cypher` includes the optional `traceparent` property on Intent
  nodes, with a migration note.
- A one-paragraph "what changed" note gets appended to
  `docs/ADR-000N-observability.md` (an architecture decision record).
- Final Telegram message to Ingo with dashboard URLs and known gaps.

---

## 8. Cross-cutting concerns & previously-forgotten items

Each item below has an explicit owner so it cannot fall through the cracks.

| Concern | Owner | Resolution plan |
|---------|-------|-----------------|
| **PII policy in logs** | Track A (Codex) | Redaction in `obs/logging.py`; explicit allow-list of fields; test asserts `api_key`, `api_key_hash`, `api_key_lookup`, and any cypher param named `lookup` never appear |
| **Retention policy** | Track E (Codex) | Loki `retention_period: 7d`, Prometheus `--storage.tsdb.retention.time=7d`, Tempo `retention: 168h` |
| **Log sampling at high volume** | Track A | DEBUG loggers sample 1-in-10 above 1000 lines/s; INFO always kept |
| **trace_id propagation over SDK boundaries** | Track C | `traceparent` as a property on `Intent` nodes (schema change); daemon resumes trace via `TraceContextTextMapPropagator` (see §2.4) |
| **Backward-compat with legacy agents** | Track A (primary; B+C satisfy invariants) | `HASSALEH_OBS=off` is a no-op; missing wire-fields are tolerated. Track A enforces the pattern; B and C must demonstrate no-op behaviour in their own tests. |
| **Dev vs. prod mode** | Track A | `HASSALEH_ENV` env-var drives log level + sampling defaults |
| **Graceful-shutdown of OTEL exporter** | Track C | SIGTERM handler flushes pending spans with a 5 s budget |
| **Dashboard versioning** | Track D | JSON-as-code; Grafana provisioning in read-only mode blocks UI edits from clobbering committed files |
| **Alert channel wiring** | Track E | Grafana contact point `telegram-ingo` reusing OpenClaw's bot; provisioned via config file |
| **Secrets handling** | Track E | Grafana admin password and any future API keys live in `.env` (gitignored) with `.env.example` committed |
| **Smoke-test suite** | Track F | `tests/test_observability.py` asserts ≥1 log, ≥1 metric, ≥1 trace per named endpoint. Invocation: `scripts/observability-smoke.sh` (shell harness) and `pytest -m smoke tests/test_observability.py` (CI). |
| **Drift-lint (S6)** | Track F | `scripts/check-observability-drift.py` introspects `obs/metrics.py`, `obs/logging.py`, `obs/tracing.py`, and dashboard JSONs; compares to §3.1–§3.5 tables; exits non-zero on drift. |
| **Runbook stubs** | Track D | One short markdown runbook per alert rule under `docs/runbooks/`; linked via `file://` URL from the alert annotation |
| **Cardinality enforcement** | Track B | Runtime guard in `obs/metrics.py` rejects unknown `pattern`/`command` labels and buckets them into `__other__` with a WARN log |
| **Heartbeat-miss instrumentation site** | Track B | Increment `hassaleh_heartbeat_missed_total` inside the daemon sweep loop that transitions `agent.lifecycle` between `active`/`stale`/`inactive` (currently at `heartbeat_sdk.py:168`). Each `active → stale` or `stale → inactive` transition is exactly one missed beat from the instrumentation's point of view. |
| **Histogram-bucket tuning** | Dione | After one week of production data, revisit bucket boundaries per-metric (the bcrypt histogram in particular — the `0.01` and `0.05` buckets are dead for cost-12 bcrypt). v1.1 patch. |
| **R1 agent-lifecycle integration** | Dione | R1 spans Track A (lifecycle logs), Track B (`hassaleh_active_agents`/`active_intents` gauges), and Track D (dashboards). Dione owns the integration check in §7 Done criteria. |
| **ADR commitment** | Dione | One `docs/ADR-000N-observability.md` summarising the chosen stack and rejected alternatives |

### Things that are **explicitly out of v1** and parked for v2+

- Service-level objectives (SLOs) with error budgets.
- Loki → S3 cold-storage.
- Multi-tenant dashboard scoping.
- Log-based anomaly detection.
- PagerDuty/Opsgenie integration.
- Distributed trace analytics (trace-ID-to-root-cause recommendations).

---

## 9. Meta — This plan under the agentic review workflow

Ingo requested (Q4) that the plan itself passes through the agentic review
workflow. Concretely:

### 9.1 Review invocation

Immediately after this file is committed on `trunk`, one
`openclaw sessions spawn --agent worker-opus` is triggered with the
prompt "read `docs/sprint-12-plan.md` and produce a CR / clean verdict
covering Requirements, Architecture, Interface, and Cross-cutting
concerns; save the review to `docs/sprint-12-plan-review-1.md`".

A second review, with an orthogonal perspective, can be spawned on
worker-gemini for a clarity/completeness check (author: "gemini-reviewer").

### 9.2 Review success criteria for *this document*

- Requirements (§1): each R* has a clear "so what?" and a testable success
  marker in §1's success criteria or §7's Done criteria.
- Architecture (§2): the stack choice, deploy topology, and integration
  surface in Hassaleh are specific enough that Track authors can start
  without a second conversation.
- Interfaces (§3): enough detail that a Track-E (compose) author could
  reproduce the log/metric/trace contract from this doc alone.
- Cross-cutting (§8): nothing in the "previously-forgotten items" list
  without an owner.

### 9.3 Post-review

If CR-clean after round 1 (or round 2 with CRs integrated), the plan is
frozen as the source of truth and the Sprint-12 orchestrator cron is
armed. If still CR after round 3, Ingo decides whether to split the sprint
or re-scope.

### 9.4 Explicit non-reviewability items

Items decided by Ingo and therefore not open for review:

- Tool stack = PLG + Tempo (Q1).
- Deploy = separate stack (Q2).
- Sampling = sampling-strategy yes (Q3) — reviewers may critique the
  specific strategy in §3.4 but not the decision to sample.
- Workflow = agentic review applies to the plan (Q4).

---

## Change log

| Date | Author | Change |
|------|--------|--------|
| 2026-04-18 | Dione | Initial draft (v0). Pending Inanna + Ingo review. |
| 2026-04-19 | Dione | v1 — integrated all Round-1 CRs from Inanna and gemini-reviewer. Summary below. |
| 2026-04-19 | Dione | v1.0.1 — Round-2 editorial cleanup: (a) §3.2.2 case consistency (`Internal` → `internal`), (b) §3.2.2 prose now correctly states the sampling decision is at span-start via `hint_error_prone()`, not retroactively, (c) §8 adds an explicit heartbeat-miss instrumentation-site row pointing at the daemon sweep loop (`heartbeat_sdk.py:168`). All per Inanna's Round-2 non-blocking observations. |
| 2026-04-19 | Dione | v1.0.2 — **FROZEN** after both Round-2 verdicts are CLEAN. Fixed gemini-reviewer's two Round-2 non-blocking observations: (a) §3.2.1 parenthetical about "S3 alert at < 0.99" updated — the SLO alert is explicitly NOT in S3's four baseline alerts; it can be added after v1 once a week of real data establishes hysteresis; (b) §5 merge order fixed: A→B→C→D→E→F (was A→B→C→E→D→F but the prose correctly said E depends on D). Observations O3 (harmless duplication) and O4 (§9.1 two-reviewer pattern prose polish) are not regressions and can land during implementation. **Plan is now the frozen source of truth for the Sprint-12 orchestrator cron.** |

### v1 change summary — how each Round-1 CR was addressed

**From `docs/sprint-12-plan-review-1-inanna.md` (Inanna — tech + security):**

1. **CR-1 — R7/R8 testable markers:** Added S2a (tool-counter assertion
   in integration test) and S2b (neo4j-exporter + cAdvisor `up==1`
   assertions). §1 Success Criteria.
2. **CR-2 — SDK→daemon trace propagation:** Rewrote propagation as a
   `traceparent` property on `Intent` nodes (schema change, nullable) —
   daemon uses `TraceContextTextMapPropagator` to resume the trace.
   §2.4, §3.3. Schema migration owned by Track C.
3. **CR-3 — `logging.basicConfig` collision:** Track A's first task is
   to remove the `daemon.py:33` basicConfig call; `obs.setup()` is now
   the sole owner of root-handler configuration, with an explicit
   off-mode handler to preserve legacy stderr behaviour. §2.4.
4. **CR-4 — Error-type taxonomy:** Added §3.2.2 "Error-type taxonomy"
   with an explicit exception-class → bucket-label table grounded in
   `src/hassaleh/errors.py` + stdlib. Sampling §3.4 now uses the same
   table.
5. **CR-5 — Cypher pattern classification:** Added §3.2.3 specifying a
   caller-supplied `pattern: str` kwarg drawn from a closed ≤50-entry
   enum in `src/hassaleh/obs/cypher_patterns.py`. Unknown values bucket
   to `__other__` with a WARN.
6. **CR-6 — Sampling contradiction:** Dropped the "2×p99 duration boost"
   from v1 (head-based only); added it to Non-Requirements. §3.4
   rewritten for strictly head-based; added error-post-sampling log-only
   fallback for non-sampled failures.
7. **CR-7 — Alert-rule storage:** §2.2 now declares Grafana unified
   alerts as the source of truth; Prometheus `rules/` holds only
   recording rules (per §3.2.1). §3.6 updated to match.

Non-blocking items addressed:
- §3.1 PII policy now explicitly covers any Cypher param named `lookup`
  or matching `api_key*`.
- §3.1 service enum now includes `hassaleh-intent-sdk` and documents
  that it is a closed enum.
- §8 "backward-compat" row now designates Track A as primary with B+C
  as invariant-satisfiers.
- §8 adds histogram-bucket-tuning row (bcrypt specifically) as a v1.1
  follow-up.
- §8 adds R1 integration row owned by Dione.

**From `docs/sprint-12-plan-review-1-gemini.md` (gemini-reviewer — clarity):**

1. **R8 data sources:** Added cAdvisor to §2.1 and §2.2 stack layout.
   §2.5 resource profile updated (+80 MB).
2. **Compose-stack path inconsistency:** Fixed S4 to use
   `~/projects/observability-stack/` (matching §2.2 and Track E).
3. **Slow-query log spec:** Added §3.1.1 with logger name, level,
   required fields, param_shape redaction rule, and Track A+B
   co-ownership.
4. **R5 uptime-SLO:** Added §3.2.1 Prometheus recording rule with a
   concrete 99.0 % target.
5. **S6 drift-lint mechanism:** Named `scripts/check-observability-drift.py`
   in Track F's deliverables and in §8 Cross-cutting.
6. **S1 ↔ sampling contradiction:** S1 now explicitly scopes to the
   observability-smoke harness running with `HASSALEH_TRACE_FORCE=1`.
7. **Artefact ↔ plan relationship:** Clarified in the front-matter —
   §1/§2/§3 *are* the artefacts; Day-1 may split them into separate
   files as a pure-move.
8. **Second reviewer in §4:** Added `gemini-reviewer` as secondary
   reviewer for all three artefacts.
9. **SDK log/trace delivery:** Added §2.4 subsection describing how SDK
   logs reach Loki (direct push via `HASSALEH_LOKI_ENDPOINT`, or
   stderr-only if unset), and how SDK traces depend on OTLP endpoint
   reachability.

Non-blocking items addressed:
- §3.6 runbook URL now points at a concrete `file://` path under
  `docs/runbooks/`.
- §5 merge order for Tracks D, E, F is now explicit.
- §7 `make observability-smoke` was replaced with the concrete
  `scripts/observability-smoke.sh` + `pytest -m smoke` invocations.
- §8 added a "Cardinality enforcement" row owned by Track B.
- §8 added a "Histogram-bucket tuning" row owned by Dione (v1.1 follow-up).

### Remaining non-blocking items (not fixed in v1)

- **R4 slow-query log call-site hook** is described but not yet named at
  the code level — Track A during implementation will add the hook point
  once they know whether it belongs in a `sdk.query()` wrapper or a
  Neo4j driver event handler.
- **Bcrypt histogram buckets** keep their v0 default (`0.01, 0.05, 0.1,
  0.25, 0.5, 1, 2.5, 5, 10` seconds); the tuning pass to drop the two
  lowest buckets is scheduled as a v1.1 patch (§8 owner: Dione).
- **"Observability for the observability stack"** is explicitly parked
  to v2 (§1 Non-Requirements).
