#!/usr/bin/env bash
# A1: Re-trigger stalled bus-dispatch
# Usage: pm-retrigger.sh <recipient> <topic> <message-file-or-text>
set -euo pipefail

RECIPIENT="${1:?recipient required (dione|inanna|nisaba)}"
TOPIC="${2:?topic required}"
MSG_INPUT="${3:?message file path or inline text required}"

if [[ -f "$MSG_INPUT" ]]; then
  MSG=$(cat "$MSG_INPUT")
else
  MSG="$MSG_INPUT"
fi

trinity-bus send \
  --from inanna \
  --to "$RECIPIENT" \
  --topic "$TOPIC" \
  --async \
  --timeout 600 \
  -m "[PM-RETRIGGER] $MSG"

echo "[A1] retriggered $RECIPIENT topic=$TOPIC at $(date -Iseconds)"
