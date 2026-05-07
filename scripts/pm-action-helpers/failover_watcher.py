#!/usr/bin/env python3
"""Failover-watcher cron entry-point.

Iterates all tracks with phase==implement and dispatchBusJobId set,
calls failover_dispatch.run() for each. Reports a single summary line
per track to stdout (cron captures it).

Cron config: every 15 min on worker-opus.
  Cmd: python3 ~/projects/hassaleh/scripts/pm-action-helpers/failover_watcher.py

Idempotency: handled by failover_dispatch.run() — it no-ops if the bus
job is still running or already terminally-completed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import _failover_lib as lib
import failover_dispatch


def run(state_path: Path, max_attempts: int, dry_run: bool) -> int:
    if not state_path.exists():
        print(f"WATCHER ERR state file not found: {state_path}", file=sys.stderr)
        return 4

    state = lib.load_state(state_path)
    tracks = state.get("tracks", {})
    if not tracks:
        print("WATCHER skip: no tracks in state")
        return 0

    # Eligibility:
    #   (a) phase=implement AND dispatchBusJobId set → check live job status
    #   (b) phase=implement AND no job AND history>0 → previously failed/reset,
    #       dispatcher's fallback path will pick a fresh worker from the pool
    eligible = []
    for tid, t in tracks.items():
        if t.get("phase") != "implement":
            continue
        has_job = bool(t.get("dispatchBusJobId"))
        has_history = lib.history_attempt_count(t) > 0
        if has_job or (not has_job and has_history):
            eligible.append(tid)

    if not eligible:
        print("WATCHER skip: no tracks in implement phase needing watch")
        return 0

    print(f"WATCHER scanning {len(eligible)} eligible tracks: {','.join(eligible)} (max_attempts={max_attempts}, dry_run={dry_run})")

    summary = {"no_action": 0, "failover": 0, "exhausted": 0, "non_cap": 0, "errors": 0}
    for tid in eligible:
        ns = argparse.Namespace(track=tid, state=str(state_path), max_attempts=max_attempts, dry_run=dry_run)
        try:
            rc = failover_dispatch.run(ns)
        except Exception as exc:
            print(f"WATCHER ERR track={tid}: {exc}", file=sys.stderr)
            summary["errors"] += 1
            continue
        if rc == failover_dispatch.ESC_NO_ACTION:
            summary["no_action"] += 1
        elif rc == failover_dispatch.ESC_FAILOVER_DISPATCHED:
            summary["failover"] += 1
        elif rc == failover_dispatch.ESC_ATTEMPTS_EXHAUSTED:
            summary["exhausted"] += 1
        elif rc == failover_dispatch.ESC_NON_CAPACITY_FAILURE:
            summary["non_cap"] += 1
        else:
            summary["errors"] += 1

    print(
        f"WATCHER summary: ok={summary['no_action']} failover={summary['failover']} "
        f"exhausted={summary['exhausted']} non_cap={summary['non_cap']} errors={summary['errors']}"
    )
    return 1 if summary["errors"] else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Sprint-14 failover watcher (cron)")
    parser.add_argument("--state", default=str(lib.STATE_FILE_DEFAULT))
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    return run(Path(args.state).expanduser(), args.max_attempts, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
