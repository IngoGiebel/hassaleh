from __future__ import annotations

CYPHER_PATTERNS = [
    "agent.lookup_by_lookup_hash",
    "agent.list_active",
    "intent.create",
    "intent.lookup_by_id",
    "intent.lookup_owned",
    "intent.list_pending",
    "heartbeat.lookup_by_lookup_hash",
    "heartbeat.write",
    "heartbeat.current_state",
    "daemon.intent.claim_pending",
    "daemon.intent.mark_running",
    "daemon.intent.reject",
    "daemon.intent.fail",
    "daemon.intent.park",
    "daemon.metrics.active_agents",
    "daemon.metrics.intent_counts",
    "daemon.sweep.active_to_stale",
    "daemon.sweep.stale_to_inactive",
    "__other__",
]
