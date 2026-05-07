#!/usr/bin/env bash
# A6: Append entry to state.projectLead.autonomousActions[] (idempotent on issue+action+timestamp)
# Usage: pm-log-action.sh <issue-id> <action-id> <result-json-or-string>
set -euo pipefail

ISSUE_ID="${1:?issue-id required}"
ACTION_ID="${2:?action-id required (A1|A2|A3|A4|A5|A6)}"
RESULT="${3:?result required}"

STATE_FILE="${HASSALEH_STATE:-$HOME/projects/hassaleh/sprint-14-state.json}"

python3 - "$STATE_FILE" "$ISSUE_ID" "$ACTION_ID" "$RESULT" <<'PY'
import json, sys
from datetime import datetime

state_path, issue_id, action_id, result = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
with open(state_path) as f:
    state = json.load(f)

pl = state.setdefault("projectLead", {})
actions = pl.setdefault("autonomousActions", [])

now = datetime.now().astimezone().isoformat()
prior_attempts = sum(1 for a in actions if a.get("issueId") == issue_id)
entry = {
    "timestamp": now,
    "issueId": issue_id,
    "actionTaken": action_id,
    "result": result,
    "attempt": prior_attempts + 1,
    "resolved": result.startswith("RESOLVED") or result == "resolved",
}
actions.append(entry)

with open(state_path, "w") as f:
    json.dump(state, f, indent=2)
print(f"[A6] logged action: issue={issue_id} action={action_id} attempt={entry['attempt']}")
PY
