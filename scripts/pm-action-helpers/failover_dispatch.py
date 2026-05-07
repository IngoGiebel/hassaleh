#!/usr/bin/env python3
"""Failover-dispatcher for sprint-14 author tracks.

Invariants enforced:
- One in-flight dispatch per track at a time (re-dispatch only after the
  prior bus job is terminally failed)
- Workers are never re-tried on the same track within the same sprint
- max_attempts is hard — at exhaustion, escalate to PM, do NOT silently retry
- state.json writes are atomic + ensure_ascii=False (preserves §)
- Cross-LLM violation is MARKED only (per Ingo 2026-05-07); plan-reviewer
  decides Redo at G-gates

Caller contract:
  python failover_dispatch.py --track C [--state /path] [--max-attempts 3] [--dry-run]

Exit codes:
  0  no action needed (job healthy / already running / completed)
  1  failover dispatched (state.json updated, new bus job queued)
  2  attempts exhausted, escalation bus message sent to inanna PM
  3  non-capacity failure detected — escalation sent without failover
  4  configuration / prerequisite error
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import _failover_lib as lib

ESC_NO_ACTION = 0
ESC_FAILOVER_DISPATCHED = 1
ESC_ATTEMPTS_EXHAUSTED = 2
ESC_NON_CAPACITY_FAILURE = 3
ESC_CONFIG_ERROR = 4


def render_dispatch_prompt(track_id: str, track: dict, new_worker: lib.Worker, all_workers: list[lib.Worker]) -> str:
    """Build a dispatch prompt for the new worker.

    Priority:
      1. track.lastDispatchPrompt (verbatim — set by initial dispatcher)
      2. Synthesized fallback referencing sprint-14-plan.md §5 Track <X>

    The synthesized fallback is intentionally terse — if it ever fires,
    the operator should investigate why the original prompt wasn't stored.
    """
    persona_label = {"inanna": "Inanna ⭐", "nisaba": "Nisaba 🌾", "dione": "Dione 🌙"}.get(
        new_worker.persona, new_worker.persona.title()
    )
    rev_persona = track.get("reviewer", "?")
    rev_persona_label = {"inanna": "Inanna ⭐", "nisaba": "Nisaba 🌾", "dione": "Dione 🌙"}.get(
        rev_persona, rev_persona.title()
    )

    base = track.get("lastDispatchPrompt")
    if base:
        # Replace addressed-persona only (first line typically "You are X (worker-Y...)")
        lines = base.splitlines()
        if lines:
            lines[0] = (
                f"SPRINT-14 TRACK {track_id} — AUTHOR (FAILOVER → {new_worker.id} × "
                f"{rev_persona_label} review)"
            )
            # Patch the standard "You are <persona> (worker-<id>..." line if present
            for i, ln in enumerate(lines[:5]):
                if ln.startswith("You are ") and "worker-" in ln:
                    lines[i] = (
                        f"You are {persona_label} ({new_worker.id}, "
                        f"{new_worker.foundation} foundation), authoring Track {track_id} "
                        f"of Sprint-14 in Hassaleh (FAILOVER from prior worker)."
                    )
                    break
        return "\n".join(lines) + "\n\n## Failover note\n\nA prior dispatch failed with a capacity error. State.json carries the full attempt history under tracks." + track_id + ".dispatchHistory[]. Cross-LLM invariant may now be violated — see crossLLMViolated flag. Plan reviewer will decide post-hoc whether the track requires Redo at G-gates."

    # Synthesized fallback
    return (
        f"SPRINT-14 TRACK {track_id} — AUTHOR (FAILOVER → {new_worker.id})\n\n"
        f"You are {persona_label} ({new_worker.id}, {new_worker.foundation} foundation), "
        f"authoring Track {track_id} of Sprint-14 in Hassaleh.\n\n"
        f"## Authoritative spec\n\n"
        f"Read ~/projects/hassaleh/docs/sprint-14-plan.md §5 Track {track_id} (and §6.1).\n\n"
        f"## Process\n\n"
        f"- Branch sprint-14/track-{track_id.lower()} from trunk\n"
        f"- One bundled commit, push branch when ready, NO merge to trunk\n"
        f"- DO NOT modify sprint-14-state.json\n"
        f"- Reviewer is {rev_persona_label}; Dione handles merge\n\n"
        f"## Failover note\n\n"
        f"A prior dispatch failed. See sprint-14-state.json tracks.{track_id}."
        f"dispatchHistory[] for context.\n"
    )


def send_via_bus(persona: str, prompt: str, dry_run: bool = False) -> str:
    """Send async via trinity-bus, return job id.

    Raises RuntimeError on bus failure (caller decides whether to escalate).
    """
    cmd = [
        "trinity-bus", "send",
        "--from", "dione",
        "--to", persona,
        "--async",
        "--timeout", "14400",
        "-m", prompt,
    ]
    if dry_run:
        return f"DRY-RUN-{persona}-job-id"

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(
            f"trinity-bus send failed rc={proc.returncode}: {proc.stderr or proc.stdout}"
        )
    out = (proc.stdout or "").strip()
    # Expected line: "queued <job-id> from->to pid=N session=..."
    for line in out.splitlines():
        if line.startswith("queued "):
            return line.split()[1]
    raise RuntimeError(f"could not parse job id from bus output: {out!r}")


def escalate_to_pm(
    track_id: str,
    attempts: int,
    reason: str,
    history: list[dict],
    all_workers: list[lib.Worker],
    dry_run: bool = False,
) -> str:
    """Send [DISPATCH-FAILED] to Inanna PM. Returns job id."""
    msg = (
        f"[DISPATCH-FAILED] track={track_id} attempts={attempts} reason={reason}\n\n"
        f"Failover dispatcher exhausted the worker pool for Track {track_id}.\n"
        f"History (worker → result):\n"
    )
    for h in history:
        n = lib.normalize_history_entry(h, all_workers)
        msg += (
            f"  - {n.get('workerId','?')}: {n.get('result','?')} "
            f"at {n.get('dispatchedAt','?')}"
        )
        if n.get("failureReason"):
            msg += f" ({n['failureReason'][:80]})"
        msg += "\n"
    msg += (
        f"\nNext PM action: A6 log + escalate to Dione via bus [PM-ESCALATE].\n"
        f"State file: ~/projects/hassaleh/sprint-14-state.json (track key: {track_id}).\n"
    )
    if dry_run:
        return "DRY-RUN-pm-escalate-id"
    return send_via_bus("inanna", msg, dry_run=dry_run)


def run(args: argparse.Namespace) -> int:
    state_path = Path(args.state).expanduser()
    if not state_path.exists():
        print(f"ERR state file not found: {state_path}", file=sys.stderr)
        return ESC_CONFIG_ERROR

    state = lib.load_state(state_path)
    track = state.get("tracks", {}).get(args.track)
    if not track:
        print(f"ERR track {args.track} not found in state", file=sys.stderr)
        return ESC_CONFIG_ERROR

    all_workers = lib.load_workers()
    job_id = track.get("dispatchBusJobId")

    if not job_id:
        # Track has no in-flight job. Two cases:
        # (a) phase=="implement" + no history → never dispatched; nothing to do
        # (b) phase=="implement" + history present → previous attempt failed,
        #     state was reset by an external actor (e.g., Ingo unblocking
        #     a previously-blocked track). Try a fresh dispatch using the
        #     candidate pool.
        if track.get("phase") != "implement":
            print(f"[failover] track={args.track} phase={track.get('phase')}; nothing to do")
            return ESC_NO_ACTION
        if not lib.history_attempt_count(track):
            print(f"[failover] track={args.track} never dispatched; orchestrator owns initial dispatch")
            return ESC_NO_ACTION
        # Fall through to capacity-failure path with no fresh job to classify
        return _attempt_failover(args, state, state_path, track, all_workers, prior_status=None)

    status, is_cap = lib.classify_bus_job(job_id)
    print(f"[failover] track={args.track} job={job_id} status={status.status} rc={status.rc} is_capacity={is_cap}")

    if not status.is_terminal_failure:
        return ESC_NO_ACTION

    if not is_cap:
        print(f"[failover] non-capacity terminal failure on {job_id}; escalating without failover")
        if not args.dry_run:
            esc_id = escalate_to_pm(
                args.track,
                lib.history_attempt_count(track) + 1,
                "non-capacity-failure",
                track.get("dispatchHistory", []),
                all_workers,
            )
            track["lastEscalation"] = {
                "at": lib.now_iso(),
                "reason": "non-capacity-failure",
                "busJobId": esc_id,
                "originalJob": job_id,
            }
            lib.save_state(state, state_path)
        return ESC_NON_CAPACITY_FAILURE

    # Capacity failure: record the failed attempt before selecting next worker
    history = track.setdefault("dispatchHistory", [])
    current_author_id = track.get("author")
    current_worker = next((w for w in all_workers if w.id == current_author_id), None)
    already_logged = any(
        lib.normalize_history_entry(h, all_workers).get("dispatchBusJobId") == job_id
        for h in history
    )
    if current_worker and not already_logged:
        history.append(lib.canonical_history_entry(
            attempt=lib.history_attempt_count(track) + 1,
            worker=current_worker,
            dispatched_at=track.get("dispatchedAt") or lib.now_iso(),
            dispatch_bus_job_id=job_id,
            result="failed",
            failure_reason=status.stderr_excerpt or "capacity-error",
            discovered_at=lib.now_iso(),
            written_by="failover-watcher",
            via_failover=False,
        ))

    return _attempt_failover(args, state, state_path, track, all_workers, prior_status=status)


def _attempt_failover(
    args: argparse.Namespace,
    state: dict,
    state_path: Path,
    track: dict,
    all_workers: list[lib.Worker],
    prior_status: lib.BusJobStatus | None,
) -> int:
    """Pick a new worker, dispatch, update state. Or escalate if no pool/attempts left."""
    history = track.get("dispatchHistory", [])
    attempts_used = lib.history_attempt_count(track)

    if attempts_used >= args.max_attempts:
        print(f"[failover] attempts exhausted ({attempts_used} >= {args.max_attempts}); escalating to PM")
        if not args.dry_run:
            esc_id = escalate_to_pm(
                args.track, attempts_used, "capacity-attempts-exhausted",
                history, all_workers,
            )
            track["lastEscalation"] = {
                "at": lib.now_iso(),
                "reason": "capacity-attempts-exhausted",
                "busJobId": esc_id,
            }
            lib.save_state(state, state_path)
        return ESC_ATTEMPTS_EXHAUSTED

    pool = lib.candidate_workers(track, all_workers)
    if not pool:
        print("[failover] worker pool exhausted (all enabled workers tried); escalating to PM")
        if not args.dry_run:
            esc_id = escalate_to_pm(
                args.track, attempts_used, "worker-pool-exhausted",
                history, all_workers,
            )
            track["lastEscalation"] = {
                "at": lib.now_iso(),
                "reason": "worker-pool-exhausted",
                "busJobId": esc_id,
            }
            lib.save_state(state, state_path)
        return ESC_ATTEMPTS_EXHAUSTED

    next_worker = pool[0]
    cross_llm_violated = lib.detect_cross_llm_violation(next_worker, track, all_workers)
    print(f"[failover] selecting {next_worker.id} (foundation={next_worker.foundation}); cross_llm_violated={cross_llm_violated}")

    prompt = render_dispatch_prompt(args.track, track, next_worker, all_workers)
    if args.dry_run:
        new_job_id = f"DRY-RUN-{next_worker.persona}-job-id"
        print(f"[failover DRY-RUN] would dispatch to {next_worker.persona}, prompt {len(prompt)} chars")
    else:
        try:
            new_job_id = send_via_bus(next_worker.persona, prompt)
        except RuntimeError as exc:
            print(f"[failover] bus send failed: {exc}", file=sys.stderr)
            return ESC_CONFIG_ERROR

    now = lib.now_iso()
    track["author"] = next_worker.id
    track["authorFoundation"] = next_worker.foundation
    track["dispatchedAt"] = now
    track["dispatchBusJobId"] = new_job_id
    track["phase"] = "implement"  # in case caller arrived from a blocked state
    if cross_llm_violated:
        track["crossLLMViolated"] = True
        track.setdefault("crossLLMViolations", []).append({
            "at": now,
            "newAuthor": next_worker.id,
            "newFoundation": next_worker.foundation,
            "reviewerFoundation": track.get("reviewerFoundation"),
            "reason": "failover-capacity",
        })

    track.setdefault("dispatchHistory", []).append(lib.canonical_history_entry(
        attempt=attempts_used + 1,
        worker=next_worker,
        dispatched_at=now,
        dispatch_bus_job_id=new_job_id,
        result="dispatched",
        written_by="failover-watcher",
        via_failover=True,
    ))

    if not args.dry_run:
        lib.save_state(state, state_path)
    print(f"[failover] track={args.track} re-dispatched to {next_worker.id}, new job={new_job_id}")
    return ESC_FAILOVER_DISPATCHED


def main() -> int:
    parser = argparse.ArgumentParser(description="Sprint-14 failover dispatcher")
    parser.add_argument("--track", required=True, help="Track ID (B/C/E/F/G)")
    parser.add_argument("--state", default=str(lib.STATE_FILE_DEFAULT), help="Path to sprint-14-state.json")
    parser.add_argument("--max-attempts", type=int, default=3, help="Max dispatch attempts before PM escalation")
    parser.add_argument("--dry-run", action="store_true", help="Plan only; no bus sends, no state writes")
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
