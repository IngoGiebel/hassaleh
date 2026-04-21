# Runbook: HassalehSlowQueries

## Symptom
Slow query rate > 0.1/s for 5m.

## Likely Causes
- Missing Neo4j indices.
- Neo4j resource contention.

## Panels to Check
- **Hassaleh Graph Performance** (`ha-graph-v1`): "Slow Query Count".

## Immediate Actions
1. Identify offending pattern in dashboard.
2. Review exact query in Loki logs.
3. Run `EXPLAIN` and add indices if needed.
