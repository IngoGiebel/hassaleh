#!/usr/bin/env bash
# A5: Schedule one-shot follow-up cron in <delay> minutes (60-180 recommended)
# Bounded: PM skill enforces max 5 active follow-up crons.
# Usage: pm-schedule-followup.sh <issue-id> <delay-minutes>
set -euo pipefail

ISSUE_ID="${1:?issue-id required}"
DELAY_MIN="${2:?delay in minutes required}"

if [[ "$DELAY_MIN" -lt 30 || "$DELAY_MIN" -gt 360 ]]; then
  echo "[A5] delay $DELAY_MIN out of bounds [30, 360]" >&2
  exit 1
fi

PROMPT_TEMPLATE="$HOME/projects/hassaleh/skills/sprint-14-pm/followup-prompt.md"
[[ -f "$PROMPT_TEMPLATE" ]] || { echo "[A5] prompt template missing" >&2; exit 1; }

PROMPT_BODY=$(sed "s/<<ISSUE>>/${ISSUE_ID}/g" "$PROMPT_TEMPLATE")

CRON_OUT=$(openclaw cron add \
  --description "Sprint-14 PM follow-up: verify ${ISSUE_ID}" \
  --at "+${DELAY_MIN}m" \
  --agent worker-gemini \
  --best-effort-deliver \
  --delete-after-run \
  --json \
  --prompt "$PROMPT_BODY" 2>&1) || {
    echo "[A5] cron add failed: $CRON_OUT" >&2
    exit 1
  }

CRON_ID=$(echo "$CRON_OUT" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d.get('id','?'))" 2>/dev/null || echo "?")

echo "[A5] follow-up scheduled: cron-id=$CRON_ID issue=$ISSUE_ID delay=${DELAY_MIN}m"
