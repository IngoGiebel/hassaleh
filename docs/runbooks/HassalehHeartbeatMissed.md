# Runbook: HassalehHeartbeatMissed

## Symptom
Agent missed > 3 heartbeats in 5m.

## Likely Causes
- Agent host crashed or lost network.
- Agent process deadlocked.

## Panels to Check
- **Hassaleh Agent per ID** (`ha-agent-v1`): "Heartbeats Missed".

## Immediate Actions
1. Check if agent process is alive.
2. Verify network connectivity.
3. Restart agent if hung.
