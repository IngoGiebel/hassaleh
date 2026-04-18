# Sprint 12 — Observability v1

**Author:** Dione  
**Status:** DRAFT — pending review by Inanna (security + API) and one
independent technical reviewer.  
**Created:** 2026-04-18  
**Sprint timeline (tentative):** starts when Sprint 11 is fully green; ~5 working days.  
**Review process:** This plan itself is subject to the agentic review workflow
described in §9 below.

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
| R5 | **Heartbeat Health** — counter of missed heartbeats, histogram of inter-beat intervals per `agent_id`, uptime-SLO per agent. | Drives the Sprint-10 heartbeat invariant from "hope it works" to "SLO-green 99.x%". |
| R6 | **Error Classification** — single `hassaleh_errors_total` counter with labels `{type, source}`, where `type ∈ {PermissionError, ValidationError, GraphError, Internal, Timeout}`. | Lets us watch error mix over time and correlate spikes with deploys. |
| R7 | **Tool-Execution Stats** — `invoke_command`/`exec_as_user` counts per `{agent_id, command, result}`, with command duration histograms. | Feeds security audits (who invoked what?) and capacity planning. |
| R8 | **Resource Utilization** — Neo4j memory/CPU/connection-count, daemon memory/CPU, container restart-count. | Turns "it feels slow" into a datum, and underpins alert thresholds. |

### Non-Requirements (v1 explicitly out)

- Log-search UI beyond Grafana Explore (v2).
- Retention longer than 7 days in hot storage (cold storage is v2).
- User-facing dashboards for external agents (operator-only for now).
- External paging destinations (PagerDuty/Opsgenie). v1 stays on Telegram.
- Profiling/flamegraphs (v3 if ever).
- Multi-tenant dashboards (single-tenant today; a label is enough).

### Success Criteria

- **S1:** Every intent has a `trace_id` discoverable in Grafana Tempo end-to-end.
- **S2:** The `hassaleh-overview` dashboard visualizes R1–R8 live.
- **S3:** Four default alert rules are configured and test-fired once:
  - `auth_failed_rate > 1/s` for ≥ 1 min
  - `heartbeat_missed_count > N/agent` for ≥ 5 min
  - `cypher_query_slow_total increasing` > Y/min
  - `hassaleh_errors_total{type="Internal"} rate > 1%`
- **S4:** The observability stack starts cleanly via
  `docker compose -f ~/observability-stack/docker-compose.yml up`.
- **S5:** Hassaleh works with observability **disabled** (it is opt-in via
  env var), so Ingo can still run locally without the stack.
- **S6:** All relevant interface contracts (log schema, metric names,
  dashboard UIDs) are documented and linted (no drift between code and spec).

---

## 2. Architecture

### 2.1 Stack choice: PLG + Tempo

- **Loki** for logs (indexed by labels, LogQL).
- **Prometheus** for metrics (PromQL).
- **Tempo** for traces (Grafana's trace store; OTLP-native).
- **Grafana** as the single UI for logs/metrics/traces and alerting.
- **Promtail** as the log shipper (tails Hassaleh container stdout/err).
- **neo4j-prometheus-exporter** exposes Neo4j JMX as Prometheus metrics.

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
├── grafana/
│   ├── provisioning/
│   │   ├── datasources/*.yml           # preconfigured Loki, Prometheus, Tempo
│   │   ├── dashboards/*.yml            # dashboard loader (points to /var/lib/grafana/dashboards)
│   │   └── alerting/*.yml              # alert channels incl. Telegram contact point
│   └── dashboards/
│       ├── hassaleh-overview.json
│       ├── hassaleh-intent-deep-dive.json
│       ├── hassaleh-auth-security.json
│       ├── hassaleh-graph-performance.json
│       └── hassaleh-agent-per-id.json
└── networking/
    └── README-bridge-network.md        # how Hassaleh reaches the stack
```

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

- `obs/logging.py` — structlog setup, JSON renderer, service-wide bindings.
- `obs/metrics.py` — Prometheus client and the full metric catalog.
- `obs/tracing.py` — OTEL tracer provider, OTLP/HTTP exporter, span decorators.
- `obs/context.py` — `ObservabilityContext` that threads `agent_id`,
  `intent_id`, `trace_id`, `span_id` through request handlers.
- `obs/__init__.py` — single entry point `obs.setup(service_name, env)` called
  from `daemon.py` at boot.

**Opt-in via env vars** (R5 backward-compat):

- `HASSALEH_OBS=on|off` (default off)
- `HASSALEH_OTLP_ENDPOINT=http://tempo:4318/v1/traces`
- `HASSALEH_METRICS_PORT=9100`
- `HASSALEH_LOG_LEVEL=INFO|DEBUG|WARN`
- `HASSALEH_TRACE_SAMPLE_RATE=0.10` (see §4 sampling)

When `HASSALEH_OBS=off`, `obs.setup()` is a no-op — every decorator becomes a
pass-through so performance and behavior are unchanged.

### 2.5 Resource profile (estimated)

| Service | RAM | Disk/day |
|---------|-----|----------|
| Loki | ~300 MB | ~500 MB |
| Prometheus | ~200 MB | ~300 MB |
| Tempo | ~200 MB | ~1 GB (traces are heavy) |
| Grafana | ~100 MB | ~50 MB |
| Promtail | ~50 MB | — |
| neo4j-exporter | ~40 MB | — |
| **Total** | **~900 MB** | **~2 GB** |

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
- `service` — one of `hassaleh-daemon`, `hassaleh-sdk`, `hassaleh-heartbeat-sdk`
- `version` — semver of the hassaleh package
- `env` — `dev|staging|prod`

**Conventional optional fields** (set when applicable):
- `agent_id`, `intent_id`, `trace_id`, `span_id`, `duration_ms`, `result`

**PII & secrets policy** (mandatory — see §8):
- `api_key` — NEVER logged
- `api_key_hash`, `api_key_lookup` — NEVER logged (even partially)
- `cypher_params` containing user-supplied strings — redacted to length only
- `message_content`, `intent_payload.content` — hash-only or truncated to
  first 40 chars with trailing `…[N chars]`
- `agent_id`, `intent_id` — OK in logs
- `source_ip` — logged at INFO, redacted to /24 at DEBUG

Extras live under `extra.*` so the top level stays predictable.

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
| `hassaleh_active_agents` | gauge | — | Live agent count |
| `hassaleh_active_intents` | gauge | `state` | Live intents by state |
| `hassaleh_version_info` | gauge | `version, env` | Constant 1; labels carry info |

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

**W3C traceparent propagation**: SDK clients that opt into observability
set the `traceparent` header on outgoing daemon calls. The daemon
continues the trace rather than starting a new one when a valid header
is present. Clients without the header get a fresh trace (no error).

### 3.4 Sampling strategy (Q3)

- **Baseline sample rate**: 10% of traces (`HASSALEH_TRACE_SAMPLE_RATE=0.10`).
- **Boost to 100%**:
  - Any intent that produces an error of type ∈ `{Internal, GraphError,
    Timeout}`.
  - Any intent whose root-span duration exceeds 2 × p99 of the last 24h.
  - `HASSALEH_TRACE_FORCE=1` env-var setting — Ingo's manual escape hatch
    for debugging.
- **Drop to 0%**: health-check endpoints (e.g. `/health`, `/metrics`) by
  default.
- **Parent-based**: if a client sends `traceparent` with `sampled=1`,
  always record downstream spans (tail-based sampling is v2).

All sampled-out traces are still **counted** in metrics, so the histogram
view is not biased. Only the trace detail is absent.

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

Alerts are defined in Prometheus rule files (`grafana/provisioning/alerting/*`)
with the format:

```yaml
- alert: HassalehAuthFailureRateHigh
  expr: sum(rate(hassaleh_auth_attempts_total{result!="ok"}[1m])) > 1
  for: 1m
  labels:
    severity: warning
  annotations:
    summary: "Auth failure rate > 1/s for 1 min"
    runbook: "https://…/runbooks/auth-failure"
    suggested_action: "Check hassaleh-auth-security dashboard; correlate with source IPs."
```

Channel: Grafana → Telegram contact point (reuses the OpenClaw bot).

---

## 4. Review Phases

Three artefacts × two review rounds.

| Artefact | Reviewer(s) | Worker | Verdicts |
|----------|-------------|--------|----------|
| Requirements doc | Ingo (product) | — (human) | approve / change-req |
| Architecture doc | Inanna (tech) | worker-opus | clean / change-req |
| Interface spec | Inanna (security+API) | worker-opus | clean / change-req |

- **Round 1**: each reviewer produces a verdict doc under
  `docs/sprint-12-artefact-<name>-review-1.md`.
- **Revision**: authors address all CR items in-place on the artefact;
  diff goes into the PR/commit.
- **Round 2**: reviewers re-verify only the CR items. If still not clean,
  Ingo arbitrates.

**Cap:** three rounds total per artefact. If still not clean, Ingo calls it.

This plan document itself follows the same process — see §9.

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
and B both touch `daemon.py`). The standing rule is: merge in the order
A → B → C, with Dione resolving conflicts by rebasing.

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
- `make observability-smoke` (or equivalent script) runs green: starts the
  stack, runs a synthetic intent, verifies logs/metrics/traces appear.
- S1–S6 from §1 demonstrably green.
- A one-paragraph "what changed" note gets appended to
  `docs/ADR-000N-observability.md` (an architecture decision record).
- Final Telegram message to Ingo with dashboard URLs and known gaps.

---

## 8. Cross-cutting concerns & previously-forgotten items

Each item below has an explicit owner so it cannot fall through the cracks.

| Concern | Owner | Resolution plan |
|---------|-------|-----------------|
| **PII policy in logs** | Track A (Codex) | Redaction in `obs/logging.py`; explicit allow-list of fields; test asserts `api_key` never appears |
| **Retention policy** | Track E (Codex) | Loki `retention_period: 7d`, Prometheus `--storage.tsdb.retention.time=7d`, Tempo `retention: 168h` |
| **Log sampling at high volume** | Track A | DEBUG loggers sample 1-in-10 above 1000 lines/s; INFO always kept |
| **trace_id propagation over SDK boundaries** | Track C | W3C `traceparent` header in SDK outbound calls; daemon continues the trace |
| **Backward-compat with legacy agents** | Track A/B/C | `HASSALEH_OBS=off` is a no-op; missing fields on the wire are tolerated |
| **Dev vs. prod mode** | Track A | `HASSALEH_ENV` env-var drives log level + sampling defaults |
| **Graceful-shutdown of OTEL exporter** | Track C | SIGTERM handler flushes pending spans with a 5 s budget |
| **Dashboard versioning** | Track D | JSON-as-code; Grafana provisioning in read-only mode blocks UI edits from clobbering committed files |
| **Alert channel wiring** | Track E | Grafana contact point `telegram-ingo` reusing OpenClaw's bot; provisioned via config file |
| **Secrets handling** | Track E | Grafana admin password and any future API keys live in `.env` (gitignored) with `.env.example` committed |
| **Smoke-test suite** | Track F | `tests/test_observability.py` asserts at least one log, one metric, one trace per endpoint |
| **Runbook stubs** | Track D | One short markdown runbook per alert rule, linked from the alert annotation |
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
