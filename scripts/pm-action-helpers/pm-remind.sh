#!/usr/bin/env bash
# A4: Reminder bus-message to author/reviewer with diagnosis question
# Usage: pm-remind.sh <recipient> <topic> <message>
set -euo pipefail

RECIPIENT="${1:?recipient required (dione|inanna|nisaba)}"
TOPIC="${2:?topic required}"
MESSAGE="${3:?message required}"

trinity-bus send \
  --from inanna \
  --to "$RECIPIENT" \
  --topic "$TOPIC" \
  --async \
  --timeout 600 \
  -m "[PM-REMINDER from Inanna ⭐]
$MESSAGE

— Sent by sprint-14-pm cron at $(date -Iseconds)
— No response = stuck flag carries to next PM fire."

echo "[A4] reminder sent to $RECIPIENT topic=$TOPIC"
