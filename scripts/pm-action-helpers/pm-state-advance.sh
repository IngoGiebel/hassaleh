#!/usr/bin/env bash
# A2: State-file consistency repair (advance phase when verdict already set)
# Option-A guard: forbidden on tracks B and E (Inanna's own reviewer tracks).
# Usage: pm-state-advance.sh <track> <new-phase>
set -euo pipefail

TRACK="${1:?track id required (B|C|E|F|G)}"
NEW_PHASE="${2:?new phase required}"

# Option-A B/E conflict-of-interest guard
if [[ "$TRACK" == "B" || "$TRACK" == "E" ]]; then
  echo "[A2] FORBIDDEN: track=$TRACK is Inanna's reviewer-track. Escalate to Dione." >&2
  exit 2
fi

STATE_FILE="${HASSALEH_STATE:-$HOME/projects/hassaleh/sprint-14-state.json}"

python3 - "$STATE_FILE" "$TRACK" "$NEW_PHASE" <<'PY'
import json, sys
from datetime import datetime

state_path, track, new_phase = sys.argv[1], sys.argv[2], sys.argv[3]
with open(state_path) as f:
    state = json.load(f)

if track not in state.get("tracks", {}):
    sys.exit(f"track {track} not in state.tracks")

state["tracks"][track]["phase"] = new_phase
state["tracks"][track][f"phaseAdvancedAt_{new_phase}"] = datetime.now().astimezone().isoformat()
state["tracks"][track]["lastPmAdvance"] = {
    "by": "inanna-pm-A2",
    "to": new_phase,
    "at": datetime.now().astimezone().isoformat(),
}

with open(state_path, "w") as f:
    json.dump(state, f, indent=2)
print(f"[A2] track={track} phase→{new_phase}")
PY
