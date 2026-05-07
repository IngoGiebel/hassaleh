#!/usr/bin/env bash
# A3: CI-rerun (max once per issue — caller must check attempts counter)
# Hassaleh CI runs on GitHub Actions; rerun via gh CLI.
# Usage: pm-ci-rerun.sh <branch>
set -euo pipefail

BRANCH="${1:?branch name required}"

if ! command -v gh >/dev/null 2>&1; then
  echo "[A3] gh CLI not available — skipping rerun" >&2
  exit 1
fi

LATEST_RUN_ID=$(gh run list --repo IngoGiebel/hassaleh-nexus --branch "$BRANCH" \
                  --limit 1 --json databaseId --jq '.[0].databaseId' 2>/dev/null \
              || gh run list --branch "$BRANCH" --limit 1 --json databaseId --jq '.[0].databaseId')

if [[ -z "${LATEST_RUN_ID:-}" || "$LATEST_RUN_ID" == "null" ]]; then
  echo "[A3] no CI run found for branch=$BRANCH" >&2
  exit 1
fi

gh run rerun "$LATEST_RUN_ID" --failed
echo "[A3] reran run-id=$LATEST_RUN_ID on branch=$BRANCH"
