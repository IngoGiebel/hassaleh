"""Shared library for sprint-14 failover dispatch + watcher.

Source-of-truth contracts:
- workers.json: enabled worker pool (extend by adding entries here)
- sprint-14-state.json: tracks[X].dispatchHistory[] is authoritative per-track
  attempt log. tracks[X].author / authorFoundation reflect the CURRENTLY
  running worker (not the original assignment, after a failover-switch).
- trinity-bus jsonl: source for stderr/stdout error pattern matching.

State-write coordination:
- save_state() acquires a flock on a sibling lockfile before writing,
  blocking until obtained. This protects against the orchestrator-cron
  (separate process) editing state.json concurrently.

Schema notes (history entries):
- Two writers historically populated dispatchHistory[]: orchestrator-cron
  (older entries) and failover-watcher (newer entries). The shapes differed.
- Canonical (this lib writes): see CANONICAL_HISTORY_FIELDS.
- Permissive read: normalize_history_entry() accepts both shapes and
  returns the canonical view. Old entries on disk stay as-is until they
  get rewritten through this lib.

Capacity-error pattern semantics:
- Match-set is intentionally narrow (FailoverError + explicit capacity/429
  phrases) to avoid mis-triggering failover on application-level errors —
  a worker that crashes due to a code bug should NOT be replaced because
  that hides the bug.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import random
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

REPO_ROOT = Path(__file__).resolve().parents[2]
STATE_FILE_DEFAULT = REPO_ROOT / "sprint-14-state.json"
WORKERS_FILE = Path(__file__).parent / "workers.json"

CANONICAL_HISTORY_FIELDS = (
    "attempt",
    "workerId",
    "foundation",
    "dispatchedAt",
    "dispatchBusJobId",
    "result",
    "failureReason",
    "discoveredAt",
    "writtenBy",
    "viaFailover",
)

PERSONA_TO_WORKER_DIR = {"inanna": "gemini", "nisaba": "codex", "dione": "opus"}

CAPACITY_ERROR_PATTERNS = [
    re.compile(r"FailoverError", re.IGNORECASE),
    re.compile(r"No\s+capacity\s+available", re.IGNORECASE),
    re.compile(r"capacity\s+exceeded", re.IGNORECASE),
    re.compile(r"\b429\b"),
    re.compile(r"rate[\s_-]?limit", re.IGNORECASE),
    re.compile(r"quota\s+exceeded", re.IGNORECASE),
]


@dataclass
class Worker:
    id: str
    persona: str
    foundation: str
    model: str
    enabled: bool
    notes: str = ""


@dataclass
class BusJobStatus:
    job_id: str
    status: str
    rc: int | None
    stderr_excerpt: str = ""
    is_capacity_error: bool = False
    is_terminal_failure: bool = False
    raw: str = field(default="", repr=False)


def load_workers(path: Path = WORKERS_FILE) -> list[Worker]:
    data = json.loads(path.read_text())
    return [Worker(**w) for w in data["workers"] if w.get("enabled", True)]


def load_state(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


@contextlib.contextmanager
def _state_lock(state_path: Path) -> Iterator[None]:
    """Cross-process exclusive lock keyed off a sibling lockfile.

    Uses flock; blocks (no timeout) until lock obtained. Other processes
    that don't take this lock can still race — orchestrator-cron and any
    ad-hoc edits should use the same lockfile if they want safety.
    """
    lock_path = state_path.with_name(state_path.name + ".lock")
    lock_path.touch(exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def save_state(state: dict[str, Any], path: Path) -> None:
    """Write state.json atomically under flock, preserving § / unicode."""
    with _state_lock(path):
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
            f.write("\n")
        tmp.replace(path)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def query_bus_status(job_id: str) -> BusJobStatus:
    """Run trinity-bus status and parse. Conservative: any uncertainty → not capacity."""
    try:
        proc = subprocess.run(
            ["trinity-bus", "status", job_id],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return BusJobStatus(job_id=job_id, status="status-timeout", rc=None, raw="")
    raw = (proc.stdout or "") + (proc.stderr or "")

    status = "unknown"
    if "status=running" in raw:
        status = "running"
    elif "status=completed" in raw or "status=ok" in raw:
        status = "completed"
    elif "status=failed" in raw or "status=error" in raw:
        status = "failed"
    elif "status=queued" in raw:
        status = "queued"

    rc_match = re.search(r"rc=(-?\d+|None)", raw)
    rc = None
    if rc_match and rc_match.group(1) != "None":
        rc = int(rc_match.group(1))

    return BusJobStatus(
        job_id=job_id,
        status=status,
        rc=rc,
        stderr_excerpt="",
        is_capacity_error=False,
        is_terminal_failure=(status == "failed" and rc not in (None, 0)),
        raw=raw,
    )


def parse_job_parts(job_id: str) -> tuple[str, str] | None:
    """Parse 'YYYYMMDD-HHMMSS-<sender>-to-<recipient>-<hash>' → (sender, recipient)."""
    m = re.match(r"\d{8}-\d{6}-([a-z]+)-to-([a-z]+)-[a-f0-9]+$", job_id)
    if not m:
        return None
    return m.group(1), m.group(2)


def fetch_bus_job_log(job_id: str, parts: tuple[str, str]) -> str:
    """Read a bus session jsonl/log to surface real worker stderr.

    Returns last 50KB or empty string. Empty is treated by callers as
    'no capacity error detected' — conservative on missing data.
    """
    sender, recipient = parts
    candidates: list[Path] = []
    if recipient in PERSONA_TO_WORKER_DIR:
        worker_dir = PERSONA_TO_WORKER_DIR[recipient]
        candidates.append(
            Path.home() / f".openclaw/agents/worker-{worker_dir}/sessions/trinity-bus-{sender}-to-{recipient}.jsonl"
        )
    candidates += [
        Path.home() / f".openclaw/agents/worker-{recipient}/sessions/trinity-bus-{sender}-to-{recipient}.jsonl",
        Path.home() / f".openclaw/agents/worker-{recipient}/sessions/trinity-bus-{sender}-to-{recipient}.log",
    ]
    for path in candidates:
        if path.exists():
            try:
                return path.read_text(errors="replace")[-50_000:]
            except OSError:
                continue
    return ""


def detect_capacity_error(text: str) -> tuple[bool, str]:
    """Return (is_capacity_error, matched_excerpt). Excerpt ≤ 200 chars."""
    for pat in CAPACITY_ERROR_PATTERNS:
        m = pat.search(text)
        if m:
            start = max(0, m.start() - 80)
            end = min(len(text), m.end() + 80)
            return True, text[start:end].replace("\n", " ").strip()[:200]
    return False, ""


def classify_bus_job(job_id: str) -> tuple[BusJobStatus, bool]:
    """Returns (status, is_capacity_failure).

    is_capacity_failure: True iff job is terminally failed AND error log
    matches a capacity-error pattern. False for all other failure modes
    (including unknown failures — those escalate to PM, not failover).
    """
    status = query_bus_status(job_id)
    if not status.is_terminal_failure:
        return status, False

    parts = parse_job_parts(job_id)
    log_text = ""
    if parts:
        log_text = fetch_bus_job_log(job_id, parts)
    combined = log_text + "\n" + status.raw
    is_cap, excerpt = detect_capacity_error(combined)
    status.is_capacity_error = is_cap
    status.stderr_excerpt = excerpt
    return status, is_cap


def _persona_to_worker_id(persona: str, all_workers: list[Worker]) -> str | None:
    for w in all_workers:
        if w.persona == persona:
            return w.id
    return None


def normalize_history_entry(entry: dict[str, Any], all_workers: list[Worker]) -> dict[str, Any]:
    """Permissive read: map either schema to canonical fields.

    Preserves unknown fields (forward-compat) but ensures the canonical
    field-set is populated where possible. Inference rules:
    - workerId: from explicit field, else from job-id recipient persona,
      else None.
    - foundation: from explicit field, else looked up via workerId.
    - dispatchedAt: from `dispatchedAt` else `attemptedAt`.
    - dispatchBusJobId: from `dispatchBusJobId` else `busJobId`.
    """
    out = dict(entry)  # preserve unknowns

    # dispatchedAt / dispatchBusJobId aliases
    if "dispatchedAt" not in out and "attemptedAt" in entry:
        out["dispatchedAt"] = entry["attemptedAt"]
    if "dispatchBusJobId" not in out and "busJobId" in entry:
        out["dispatchBusJobId"] = entry["busJobId"]

    # workerId inference
    if "workerId" not in out:
        job_id = out.get("dispatchBusJobId")
        if job_id:
            parts = parse_job_parts(job_id)
            if parts:
                _sender, recipient = parts
                wid = _persona_to_worker_id(recipient, all_workers)
                if wid:
                    out["workerId"] = wid

    # foundation inference via workerId
    if "foundation" not in out and out.get("workerId"):
        for w in all_workers:
            if w.id == out["workerId"]:
                out["foundation"] = w.foundation
                break

    out.setdefault("viaFailover", False)
    return out


def canonical_history_entry(
    *,
    attempt: int,
    worker: Worker,
    dispatched_at: str,
    dispatch_bus_job_id: str,
    result: str,
    failure_reason: str | None = None,
    discovered_at: str | None = None,
    written_by: str = "failover-watcher",
    via_failover: bool = False,
) -> dict[str, Any]:
    """Build a canonical history entry. None fields are omitted to keep diffs lean."""
    entry: dict[str, Any] = {
        "attempt": attempt,
        "workerId": worker.id,
        "foundation": worker.foundation,
        "dispatchedAt": dispatched_at,
        "dispatchBusJobId": dispatch_bus_job_id,
        "result": result,
        "writtenBy": written_by,
        "viaFailover": via_failover,
    }
    if failure_reason:
        entry["failureReason"] = failure_reason
    if discovered_at:
        entry["discoveredAt"] = discovered_at
    return entry


def candidate_workers(
    track: dict[str, Any], all_workers: list[Worker]
) -> list[Worker]:
    """Pool = enabled workers minus already-tried, random-shuffled.

    'already-tried' = union of:
      - track.author (current author)
      - normalized history entries' workerId

    Uses normalize_history_entry() so old-schema entries (orchestrator-cron)
    contribute to the exclusion set as well.
    """
    history = track.get("dispatchHistory", []) or []
    normalized = [normalize_history_entry(h, all_workers) for h in history]
    tried = {h["workerId"] for h in normalized if h.get("workerId")}
    if track.get("author"):
        tried.add(track["author"])
    pool = [w for w in all_workers if w.id not in tried]
    random.shuffle(pool)
    return pool


def resolve_persona(worker: Worker) -> str:
    return worker.persona


def detect_cross_llm_violation(
    new_worker: Worker, track: dict[str, Any], all_workers: list[Worker]
) -> bool:
    """True iff new_worker.foundation == reviewer foundation (case-insensitive).

    Reviewer foundation comes from track.reviewerFoundation if present,
    else looked up via reviewer persona.
    """
    rev_foundation = track.get("reviewerFoundation")
    if not rev_foundation:
        rev_persona = track.get("reviewer")
        for w in all_workers:
            if w.persona == rev_persona:
                rev_foundation = w.foundation
                break
    if not rev_foundation:
        return False
    return new_worker.foundation.lower() == rev_foundation.lower()


def history_attempt_count(track: dict[str, Any]) -> int:
    """Number of attempts already recorded — robust to either schema.

    Counts entries; the canonical 'attempt' field is informational only.
    """
    return len(track.get("dispatchHistory", []) or [])
