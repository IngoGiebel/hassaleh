# Sprint 12 Track D Implementation: Dashboards & Alerts

## Dashboards
- **hassaleh-overview.json** (`ha-overview-v1`)
- **hassaleh-intent-deep-dive.json** (`ha-intent-v1`)
- **hassaleh-auth-security.json** (`ha-auth-v1`)
- **hassaleh-graph-performance.json** (`ha-graph-v1`)
- **hassaleh-agent-per-id.json** (`ha-agent-v1`)

## Metric-to-Panel Mapping
| Dashboard | Panel | Metric(s) Used |
|-----------|-------|----------------|
| ha-overview-v1 | Active Agents | `hassaleh_active_agents` |
| ha-overview-v1 | Intent Submissions | `hassaleh_intent_submitted_total` |
| ha-overview-v1 | Errors | `hassaleh_errors_total` |
| ha-intent-v1 | Intent Submissions by Agent | `hassaleh_intent_submitted_total` |
| ha-intent-v1 | Intent Duration p99 | `hassaleh_intent_duration_seconds_bucket` |
| ha-intent-v1 | Active Intents by State | `hassaleh_active_intents` |
| ha-auth-v1 | Auth Attempts | `hassaleh_auth_attempts_total` |
| ha-auth-v1 | Bcrypt Duration p99 | `hassaleh_auth_bcrypt_duration_seconds_bucket` |
| ha-graph-v1 | Cypher Query Latency p99 | `hassaleh_cypher_query_duration_seconds_bucket` |
| ha-graph-v1 | Slow Query Count | `hassaleh_cypher_query_slow_total` |
| ha-agent-v1 | Heartbeats Received | `hassaleh_heartbeat_received_total` |
| ha-agent-v1 | Heartbeats Missed | `hassaleh_heartbeat_missed_total` |
| ha-agent-v1 | Heartbeat Interval p99 | `hassaleh_heartbeat_interval_seconds_bucket` |
| ha-agent-v1 | Tool Invocations | `hassaleh_tool_invocation_total` |
| ha-agent-v1 | Tool Invocation Duration p99 | `hassaleh_tool_invocation_duration_seconds_bucket` |

## Deviations & Gaps
- **Missing Resource Metrics**: cAdvisor and Neo4j JMX metrics (R8) are omitted from the dashboards because they are not present in `src/hassaleh/obs/metrics.py`. They are provided by external exporters.
- **Alert Rules Placement**: While §2.2 suggested placing alerting rules in Grafana unified alerts, Track D explicitly requested `observability/prometheus/rules/hassaleh-alerts.yml` as the deliverable location. This has been followed.
- **Label variables**: Not all metrics natively support the `env` label (e.g. `hassaleh_active_agents`). The template variable is defined to satisfy the dashboard variable contract, but queries do not enforce filtering by `env` on metrics that lack it.
