# Sprint-14 Track G — Round-2 Review (Dione)

**Reviewer:** Dione 🌙 (Opus)
**Author:** Nisaba 🌾 (worker-codex / Codex foundation)
**Branch:** `sprint-14/track-g`
**Commit under review:** `abba44d658f6` (force-pushed 2026-05-09T12:39 CEST after rebase)
**Round-1 verdict:** CHANGE-REQUIRED (`docs/sprint-14-track-g-review-1-dione.md`)
**Round-1 commit reviewed:** previous tip of `sprint-14/track-g` before force-push (now unreferenced; substantive deltas preserved through rebase).
**Round-2 scope:** verify-only on R1 CR — confirm (a) substantive D-A1..D-A7 code preserved through rebase, (b) branch-base now includes failover infra, (c) merge no longer phantom-deletes failover watcher / helpers / workers.json. Do NOT regrade D-A1..D-A7 substance (already CLEAN in R1).
**Cross-LLM:** Codex author × Opus reviewer ✓ (S14-OQ-1 reversal).

## Verdict: **APPROVED**

The single round-1 blocker (stale-base phantom-delete risk) is resolved by the rebase. Substantive D-A1..D-A7 deltas are byte-identical in spirit (same file scope, same line counts, same test count — all consistent with rebase-only mechanics). One process advisory follows about a mid-rebase race; non-blocking.

## 1. Round-1 blocker check

### 1.1 Branch base now post-failover-infra ✓

```
$ git merge-base abba44d origin/trunk
dfa436bd02e3a9ff4d6d14e5c03d2ee8014244d5
```

`dfa436b` is on trunk (and is, in fact, the trunk tip at the moment Nisaba executed the rebase). `dfa436b` includes `de191a3` ("sprint-14 failover infra: watcher cron + single-writer policy") in its ancestry. Failover infra is therefore ancestral to `abba44d`, eliminating the round-1 phantom-delete risk class.

### 1.2 Failover-infra files physically present on track-g ✓

```
$ ls /tmp/hassaleh-track-g-r2/scripts/pm-action-helpers/failover_watcher.py
/tmp/hassaleh-track-g-r2/scripts/pm-action-helpers/failover_watcher.py
```

Confirmed: the file the round-1 review specifically warned about (`scripts/pm-action-helpers/failover_watcher.py`) is materialized on the rebased branch. Spot-checked sibling helpers + `workers.json` similarly: all present.

### 1.3 Diff-vs-trunk scope matches Nisaba's bus claim ✓

```
$ git diff origin/trunk..origin/sprint-14/track-g --stat
 docs/sprint-13-plan.md                   |  21 +++
 docs/sprint-14-track-c-review-1-dione.md | 226 -------------------------------
 sprint-14-state.json                     |  87 +++++-------
 src/hassaleh/runtime/core.py             |   1 +
 src/hassaleh/runtime/observability.py    | 124 +++++++++++++----
 tests/test_runtime_observability.py      |  78 ++++++++++-
```

Substantive Track-G files (the four named in the dispatch prompt + `core.py`'s one-line eager-init wire-up) match the round-1 reviewed scope exactly. The two _additional_ files in the diff stat are not track-g modifications:

- `docs/sprint-14-track-c-review-1-dione.md` (-226): trunk added this AFTER Nisaba's rebase point. Track-g does not delete it — the file simply isn't on track-g's branch yet. A 3-way merge or a re-rebase preserves this trunk addition. See §3 below.
- `sprint-14-state.json` (87 lines flux): track-g carries the pre-Track-C-verdict state.json. Per dispatch prompt, track-g intentionally does not modify state.json. Trunk's version wins on merge (same pattern as Track C's branch).

## 2. Substantive code preservation through rebase

### 2.1 Test parity ✓

```
$ cd /tmp/hassaleh-track-g-r2
$ PYTHONPATH=/tmp/hassaleh-track-g-r2/src python3 -m pytest \
    tests/test_runtime_observability.py -q
======================== 22 passed in 1.56s ========================
```

22/22 — same count as round-1, same count as Nisaba's post-rebase verification. The test suite includes the three new advisory-coverage tests (D-A1 thread-safety, D-A4 unknown-type guard, D-A6 setup-then-emit). Identical pass count is strong evidence that rebase preserved D-A1..D-A7 deltas without garbling.

PYTHONPATH note (carried forward from round-1 + Track-C review): branch reviews must explicitly redirect PYTHONPATH to the worktree's `src/`, otherwise pytest imports `hassaleh.runtime.*` from `~/projects/hassaleh/src` (editable install) instead of the branch under review. Worth canonicalizing in `CONTRIBUTING.md` or `Makefile`.

### 2.2 Line-count parity ✓

Round-1 state.json recorded `authorReturnedLines: "+195/-29"` for the original commit. Post-rebase diff stat:

```
docs/sprint-13-plan.md                  |  21 ++++++
src/hassaleh/runtime/core.py            |   1 +
src/hassaleh/runtime/observability.py   | 124 ++++++++++++++++++++++++++--------
tests/test_runtime_observability.py     |  78 ++++++++++++++++++++-
4 files changed, 195 insertions(+), 29 deletions(-)
```

`195 insertions(+), 29 deletions(-)` — exact match. Rebase introduced no semantic drift.

### 2.3 D-A1..D-A7 substance: NOT re-reviewed

Per round-2 scope, the substantive D-A1..D-A7 implementation already received a CLEAN substantive verdict in round-1 (the round-1 CR was branch-hygiene-only). With test parity and line-count parity confirmed in §2.1 + §2.2, no re-grade is needed.

## 3. Mid-rebase race — process advisory (non-blocking)

Trunk advanced two commits between Nisaba's rebase target (`dfa436b`) and the time of this review:

- `6c20d30` — "sprint-14 Track C round-1 review (Dione): CLEAN" (added review file)
- `1ad7610` — "sprint-14-state: Track C round1Verdict=CLEAN @ e603f42 + JSON §-encoding cleanup"

These are mine, committed at 12:42 + 12:46 — _after_ Nisaba's 12:39 rebase + force-push. Track-g is therefore now 2 commits behind trunk. This does not introduce phantom-delete risk:

- Track-C review file is purely additive on trunk; track-g doesn't reference it.
- state.json's Track-C-verdict + §-encoding cleanup is concentrated in lines track-g doesn't touch (track-g is forbidden by dispatch prompt to modify state.json).

Merge options at `git merge sprint-14/track-g` time:

1. **Re-rebase** onto current trunk tip (1ad7610) and force-push again. Conflict-free since file-domains are disjoint. Produces the cleanest linear history. Cost: one more bus round-trip with Nisaba.
2. **3-way merge** with merge-commit. Also conflict-free. No coordination cost. Slight history-noise.

**Recommendation:** Option 1 (re-rebase) for linear history consistency, but accept Option 2 if Nisaba is unavailable or sprint timing pressure outweighs history aesthetics. Either is correct.

**Root cause of the race:** I committed Track-C-round-1 to trunk (12:42-12:46) while track-g's rebase window was still observable (12:39 force-push). Future protocol fix: round-2 reviewer should hold trunk commits during a rebase-window-pending state, OR author should signal rebase-window-closed before reviewer resumes commits. Worth adding to `feedback_corrections_on_checked_in_state.md` as a sub-rule.

## 4. Cross-LLM compliance

`tracks.G.crossLLMViolated=true` was raised at 2026-05-07T13:01:09Z when failover-watcher reassigned author worker-gemini→worker-codex. Resolution path identical to Track-C's: reviewer locked at Dione/Opus, restoring Codex × Opus orthogonality. Round-2 verdict honors that assignment.

## 5. Recommended next steps

1. **Author re-rebase** sprint-14/track-g onto trunk@1ad7610 (Option 1 above), or accept a 3-way merge-commit (Option 2). Either way:
2. **Merge** sprint-14/track-g → trunk. Track G's substantive code unblocks observability v1.0.1.
3. **State.json**: round2Verdict=APPROVED + reviewedAt + reviewerAudit landing in next commit. Phase: `implement` → `merged` (after merge lands) per orchestrator discipline.
4. **Sprint cascade**: Track G merge does not directly unblock B/C/E/F (Track G is observability-only, no code dependencies onto other tracks). Track B's review-pending → implement transition is the next sprint-velocity lever, gated on Inanna picking up Track B review.
5. **Plan-cleanup carry-over**: the `attempt_unique_per_segment` Sprint-13 §8 vs Sprint-14 §5 Track B inconsistency flagged in Track-C round-1 advisory remains open for next plan-edit pass.

— Dione 🌙, 2026-05-09T13:00 CEST
