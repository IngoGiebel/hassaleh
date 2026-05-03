# Sprint 12 Track D (Dashboards / Alerts / Runbooks) — Security & API Review

**Reviewer:** Inanna
**Commit Reviewed:** a61af86
**Scope:** 5 dashboards, 2 rule files, 4 runbook stubs, 1 implement doc.
**Review type:** Interface / contract compliance. No code was modified.

---

## Verdict

**CLEAN** — with material non-blocking advisories listed in §9.

Every check against plan §3.2, §3.2.1, §3.5, §3.6, §5, and §8 passes at
the contract level. No metric-name drift, no PII exposure, no illegal
labels, no missing UIDs, no missing runbooks. Dione arbitrates the
non-blocking items below.

## 1. Metric-name drift vs `src/hassaleh/obs/metrics.py`

All 15 metric names in `METRIC_NAMES` are accounted for; every panel and
rule references only names that exist in `metrics.py`. Histogram queries
correctly use the implicit `_bucket` suffix.

| Deliverable | Metrics referenced | Status |
|---|---|---|
| `hassaleh-overview.json` | `hassaleh_active_agents`, `hassaleh_intent_submitted_total`, `hassaleh_errors_total` | OK |
| `hassaleh-intent-deep-dive.json` | `hassaleh_intent_submitted_total`, `hassaleh_intent_duration_seconds_bucket`, `hassaleh_active_intents` | OK |
| `hassaleh-auth-security.json` | `hassaleh_auth_attempts_total`, `hassaleh_auth_bcrypt_duration_seconds_bucket` | OK |
| `hassaleh-graph-performance.json` | `hassaleh_cypher_query_duration_seconds_bucket`, `hassaleh_cypher_query_slow_total` | OK |
| `hassaleh-agent-per-id.json` | `hassaleh_heartbeat_received_total`, `hassaleh_heartbeat_missed_total`, `hassaleh_heartbeat_interval_seconds_bucket`, `hassaleh_tool_invocation_total`, `hassaleh_tool_invocation_duration_seconds_bucket` | OK |
| `hassaleh-recording.yml` | `hassaleh_heartbeat_missed_total`, `hassaleh_heartbeat_received_total`, `hassaleh_intent_duration_seconds_bucket` | OK |
| `hassaleh-alerts.yml` | `hassaleh_auth_attempts_total`, `hassaleh_heartbeat_missed_total`, `hassaleh_cypher_query_slow_total`, `hassaleh_errors_total`, `hassaleh_intent_submitted_total` | OK |

No drift. No ghost metrics.

## 2. Dashboard UID stability (§3.5)

| Expected UID | Present | Match |
|---|---|---|
| `ha-overview-v1` | yes | ✓ |
| `ha-intent-v1` | yes | ✓ |
| `ha-auth-v1` | yes | ✓ |
| `ha-graph-v1` | yes | ✓ |
| `ha-agent-v1` | yes | ✓ |

Template variables match §3.5 exactly (`env` on all five; `agent_id` on
intent + agent; `pattern` on graph).

## 3. Alert contract compliance (§3.6 + S3)

| Alert | Severity | `for:` | `runbook:` → `docs/runbooks/*.md` | Expr matches §1 S3 |
|---|---|---|---|---|
| `HassalehAuthFailureRateHigh` | warning | 1m | ✓ | verbatim |
| `HassalehHeartbeatMissed` | warning | 5m | ✓ | verbatim |
| `HassalehSlowQueries` | warning | 5m | ✓ | verbatim |
| `HassalehInternalErrors` | critical | 5m | ✓ | verbatim |

All severities are in `{critical, warning}`. All four runbook `file://`
URLs resolve to committed files.

## 4. Recording-rule correctness (§3.2.1)

- `hassaleh:agent_uptime_ratio:1h` matches the plan expression verbatim.
- `hassaleh:intent_duration_seconds:p99_1h` matches the plan expression
  verbatim. `le` and `stage` are correctly preserved in the `by` clause,
  which is required for `histogram_quantile` to work correctly.

## 5. Label-cardinality review

No alert or recording rule groups by an unbounded label that is not
already acknowledged by the plan:

- `by (agent_id)` on `HassalehHeartbeatMissed` and on the per-agent
  dashboard panels: §3.2.3 explicitly documents that `agent_id` has no
  cap today ("must be bounded before multi-tenant"). Acceptable for v1
  dev load; acknowledged upstream.
- `by (type, source)`, `by (result)`, `by (stage)`, `by (le, command)`:
  all drawn from closed enums per §3.2.2 / §3.2 / §3.2.3.
- `{pattern=~"$pattern"}`: bounded at ≤50 by §3.2.3 construction.

## 6. PII exposure review (§3.1 PII policy)

Panel titles, legends, rule annotations, and runbook bodies carry no
user emails, no raw api_keys, no intent text, no phone numbers, no
source IPs, no message content. `agent_id` is the only identity-like
field used in labels/variables, and §3.1 whitelists it. Clean.

## 7. Runbook structure (§8)

All four runbooks follow a consistent short-stub shape: Symptom →
Likely Causes → Panels to Check → Immediate Actions. Each names the
corresponding dashboard + UID. §8 only requires "one short markdown
runbook per alert rule under `docs/runbooks/`"; this is satisfied.

## 8. Acceptance against §5 Track D

Track D row: "Grafana JSON for the five dashboards, alert rules". All
five dashboards present; alert rules present. Deliverable scope met.

## 9. Non-blocking advisories (Dione arbitrates)

1. **Alert-storage location contradicts §2.2 / §3.6.** Plan §2.2 (which
   resolves Round-1 CR-7) declares *Grafana unified alerts* under
   `grafana/provisioning/alerting/` as the source of truth, with
   `prometheus/rules/` holding **only** recording rules. Track D
   delivers alerts in `observability/prometheus/rules/hassaleh-alerts.yml`
   instead. The implement doc acknowledges the deviation. Functional
   consequence: S3's "test-fired once" is not reachable via Telegram
   without either (a) mirroring the rules into
   `grafana/provisioning/alerting/` before Track E loads Grafana
   provisioning, or (b) adding Alertmanager → Telegram to the compose
   stack. Recommend (a) to match the frozen plan.
2. **`$env` template variable is cosmetic.** No metric except
   `hassaleh_version_info` carries an `env` label, so the dropdown does
   not filter any panel. Implement doc acknowledges. A join against
   `hassaleh_version_info` would make it functional (e.g.
   `hassaleh_intent_submitted_total * on() group_left(env)
   hassaleh_version_info{env=~"$env"}`). v1.1 polish.
3. **Panel UIDs not explicitly assigned.** §3.5 says "Panel UIDs inside
   each dashboard are also stable so we can link to specific panels
   from runbooks." JSON has no panel `id`/`uid` fields; runbooks
   reference panels by title only. S6 drift-lint (Track F) may or may
   not care, but link-stability would benefit from explicit panel UIDs.
4. **Aggregation inconsistency between §1 S3 and §3.6.** S3 writes
   `rate(hassaleh_auth_attempts_total{result!="ok"}[1m]) > 1`; §3.6's
   example uses `sum(rate(...)) > 1`. Track D follows S3 verbatim.
   Semantic difference: the S3 form fires when any *single* result
   bucket (`bad_key`, `unknown_agent`, `bcrypt_fail`) exceeds 1/s;
   the §3.6 form fires only when their *sum* does. Same applies to
   `HassalehSlowQueries` per-`pattern`. The plan itself is internally
   inconsistent; I flag for editorial reconciliation, not for Track D
   rework.
5. **Uptime recording rule can go negative.** `1 - missed /
   clamp_min(received, 1)` yields `1 - missed` for a dead agent
   (received=0, clamped to 1) — so missed=3 → ratio = -2. Plan
   specifies this formula verbatim; wrapping with `clamp(0, 1)` would
   be more SLO-friendly. v1.1 follow-up (§8 "Histogram-bucket tuning"
   row is the natural home).
6. **`ha-intent-v1` "Active Intents by State" uses `stat` for a
   labeled gauge.** `hassaleh_active_intents` has a `state` label;
   rendering it as a `stat` panel will show an indeterminate single
   series. Consider `timeseries` or `table` grouped by `state`. Minor
   UX issue, not a contract violation.
7. **Runbooks reference Loki searches but Track D does not ship the
   Loki queries.** Acceptable since Track D owns dashboards + alerts,
   not log-query templates; Track A/F may add saved LogQL queries
   later.

None of the above block merge; items 1 and 3 are the strongest
candidates for a Dione-arbitrated v1.0.1 patch on Track D.
