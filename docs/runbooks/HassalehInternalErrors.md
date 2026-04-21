# Runbook: HassalehInternalErrors

## Symptom
Internal errors > 1% of submitted intents for 5m.

## Likely Causes
- Unhandled exceptions in new deploy.
- Downstream failures.

## Panels to Check
- **Hassaleh Overview** (`ha-overview-v1`): "Errors" panel.

## Immediate Actions
1. Search Loki for `level=ERROR` or `CRITICAL`.
2. Evaluate immediate rollback of recent deploy.
3. Triage exception.
