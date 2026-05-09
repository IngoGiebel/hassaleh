# Sprint-14 Track G Round-1 Review — Dione

**Reviewer:** Dione (worker-opus)
**Branch:** `sprint-14/track-g`
**Author commit:** `e49338c` ("sprint-14 Track G: observability v1.0.1 patch (D-A1..D-A7)")
**Branch base:** `b872f03` (Phase-1 dispatch fired)
**Plan reference:** docs/sprint-14-plan.md §5.G + Sprint-13 D-A1..D-A7 advisory bundle.
**Review type:** Code + branch-hygiene. No code modified.

## Findings

| Type | Location | Finding |
|---|---|---|
| **BLOCKING** | branch state | Stale-branch phantom-delete. Branch base `b872f03` predates `de191a3` ("sprint-14 failover infra: watcher cron + single-writer policy") which landed on trunk on 2026-05-07 and ships the failover-watcher cron infrastructure currently in production (18+ green cycles, 0 cap-errors, last green fire 2026-05-09T05:02). `git diff trunk..sprint-14/track-g` shows DELETION of `scripts/pm-action-helpers/_failover_lib.py` (371), `scripts/pm-action-helpers/failover_dispatch.py` (339), `scripts/pm-action-helpers/failover_watcher.py` (92), `scripts/pm-action-helpers/workers.json` (29), `skills/sprint-14-pm/SKILL.md` (27), and 274 lines from `sprint-14-state.json` — none of which are in §5.G's stated scope. A merge of Track G in its current shape would silently regress the failover infrastructure. **Required action:** rebase `sprint-14/track-g` onto current `trunk`, resolve any real conflicts within scope (`observability.py`, its test, and `docs/sprint-13-plan.md`), re-run the test suite, force-push the rebased branch, re-trigger review. |
| CLEAN | §2.6 D-A1 | `_ensure_metrics()` adopts the canonical double-checked locking pattern (`_metrics_lock`, recheck-inside-lock). `initialize_runtime_metrics()` exposes an eager-init handle and the new test `test_ensure_metrics_is_thread_safe_under_concurrent_cold_start` (16-worker pool, 64 calls, asserts single registry id) exercises the cold-start race directly. `HassalehRuntime.__init__` integration covered by `test_runtime_constructor_eager_initializes_metrics_when_obs_enabled`. |
| CLEAN | §2.6 D-A2 | `_ensure_metrics()` returns `(counter, histogram, registry)` as a tuple; `get_runtime_registry()` consumes the registry slot directly and the strippable `assert _registry is not None` is gone. Module export list updated. |
| CLEAN | §2.6 D-A3 routing | Change-log entry explicitly routes D-A3 to Track E ("no Track-G code change") — scope discipline correct. |
| CLEAN | §2.6 D-A4 | `_ALLOWED_INTENT_TYPES` allowlist (six explicit market intents) maps unregistered types to label `intent_type="unknown"` and ticks the new `hassaleh_intent_unknown_type_total` guard counter. Test `test_record_metric_maps_unregistered_intent_type_to_unknown` proves both the clamp and the guard tick, AND asserts that the original raw intent type does NOT appear as a label — correct cardinality-bound assertion. |
| CLEAN | §2.6 D-A5 | `_DURATION_BUCKETS` extends with `0.0005, 0.001, 0.002, 0.005` while preserving the `0.01` anchor and all higher buckets. Bucket choice reflects realistic intent latency (most dispatches under 10ms). Test extension verifies `le=0.005` bucket = 0 and `le=0.1` bucket = 1 for a 100ms observation, confirming bucket-monotonicity in the new range. |
| CLEAN | §2.6 D-A6 | Module docstring adds the caller precondition: entrypoint must run `obs_logging.setup("hassaleh-daemon", env)` before first dispatch; `emit_log` is explicitly NOT self-configuring. New test `test_setup_then_emit_is_canonical_sequence` locks the canonical setup-then-emit ordering and asserts JSON-payload `service`/`env` fields. |
| CLEAN | §2.6 D-A7 | `_LOGGER_NAME = "hassaleh.runtime"` carries an anti-regression comment documenting that logger names intentionally bypass `SERVICE_ENUM`; `obs_logging.setup()` remains the only `SERVICE_ENUM`-validated surface. This addresses the v0 finding from Round-1 plan review. |
| CLEAN | docs/sprint-13-plan.md | Change-log entry is well-formed and self-aware about the SHA-self-reference paradox ("self-embedding the final Git SHA in the file is intentionally avoided because it would change the commit hash"). All 7 advisories accounted for in the entry. |
| OBSERVATION | tests | Local run on `sprint-14/track-g`: `pytest tests/test_runtime_observability.py` → 22/22 passed in 2.58s. Test count grew from 17 to 22 (+5: thread-safety, eager-init, bucket-coverage, cardinality-guard, setup-emit-sequence) — direct 1:1 mapping to the substantive new behaviors. |
| OBSERVATION | scope discipline | Within the in-scope diff (`observability.py` +124 / -49, test +78 / -7, plan +21), the change is tight: no opportunistic refactors, no unrelated cleanup, no scope creep. The phantom-delete is the only branch-level concern; the substantive code is cleanly within §5.G. |

## Overall Verdict
**CHANGE-REQUIRED**

The substantive observability work is high-quality and would land CLEAN on a current-trunk-base branch. The single blocker is purely branch-hygiene: a rebase onto current trunk to drop the phantom-deletes of the failover infrastructure landed in `de191a3`. After rebase + test re-run, this is a straightforward CLEAN merge.
