# IL-04 / IL-05 / IL-06 — merge bundle (sprint-11, cycle 18)

Date: 2026-04-19
Branch: `trunk`
Repo: `~/projects/hassaleh`

## Summary

This cycle lands three independently-reviewed security fixes as a single
merge bundle on `trunk`. All three carry a CLEAN verdict from security
review. IL-04 was already committed in isolation earlier in the sprint;
IL-05 and IL-06 land here alongside the joint SR and test-validation
documentation.

## Commits

| Sprint item | SHA        | Landed                                  |
| ----------- | ---------- | --------------------------------------- |
| IL-04       | `c57dde3`  | pre-bundle (sanitize exec error surfaces) |
| IL-05 + IL-06 + joint docs | `2bc7d2d`  | this cycle (`sec: land IL-04/IL-05/IL-06 merge bundle (SR-CLEAN)`) |

## Files landed in `2bc7d2d`

Implementation:

- `src/hassaleh/capabilities/exec_ls.py` — IL-05 TOCTOU guard
  (`O_RDONLY|O_DIRECTORY|O_NOFOLLOW` + `readlink(/proc/self/fd/<N>)` ==
  `resolved_path`; `ls` runs against `/proc/self/fd/<N>/`).
- `src/hassaleh/heartbeat_sdk.py` — IL-06 explicit sequencing
  (`WITH a, a.last_heartbeat AS old_last` bound before `SET`).

Tests:

- `tests/test_il05_toctou.py` — final-component and intermediate-component
  swap regressions.
- `tests/test_il06_heartbeat_sequencing.py` — first-heartbeat NULL and
  subsequent-heartbeat sequencing invariants.

Documentation:

- `docs/IL-05-implementation.md`
- `docs/IL-05-security-review.md` — SR verdict CLEAN (cycle 17)
- `docs/IL-05-test-validation.md`
- `docs/IL-06-implementation.md`
- `docs/IL-04-IL-06-security-review.md` — joint SR, verdict CLEAN
- `docs/IL-04-IL-06-test-validation-cycle15.md` — joint test evidence

## Security review references

- **IL-04** — CLEAN. See `docs/IL-04-IL-06-security-review.md`.
- **IL-05** — CLEAN (cycle 17). See `docs/IL-05-security-review.md` +
  `docs/IL-04-IL-06-security-review.md`.
- **IL-06** — CLEAN. See `docs/IL-04-IL-06-security-review.md`.

## Scope discipline (items deliberately NOT in this bundle)

The working tree also contained unrelated changes at the time of this
commit. They were explicitly excluded to keep the bundle scoped to the
SR-CLEAN security fixes:

- `src/hassaleh/daemon.py` — IL-07 capability allowlist / trust-boundary
  work. The current diff contains **no IL-06 heartbeat plumbing**, only
  the IL-07 `CAPABILITY_ALLOWLIST` dict and the `_execute_capability()`
  validation. IL-07 will land in its own bundle once SR verdict clears.
- `tests/test_il07_trust_boundary.py`, `docs/IL-07-implementation.md` —
  IL-07 scope, not this cycle.
- `sprint-11-state.json`, `src/hassaleh/agent_dummy.py`,
  `tests/test_chaos.py`, `tests/test_integration.py`,
  `tests/test_messaging.py`, `tests/test_mvp_intent.py`,
  `tests/test_stress.py`, `tests/test_task_modes.py` — unrelated
  sprint/test work.
- `.codex`, `logs/`, `docs/IL-bundle-merge-cycle14*.md`,
  `docs/sprint-12-plan-review-1-*.md` — older/planning artifacts.

## Flag for Ingo

The original task brief listed `src/hassaleh/daemon.py` under IL-06.
On inspection the daemon.py diff is entirely IL-07 (capability
allowlist); there is no IL-06 heartbeat-related change in daemon.py.
I treated daemon.py as out-of-scope for this bundle and left it
unstaged. If IL-06 was supposed to include a daemon-side heartbeat
plumbing change, it has not yet been written.

## Push status

**NOT PUSHED.** `trunk` is now **19 commits ahead** of `origin/trunk`
(was 18 before this commit, +1 from `2bc7d2d`). Push scope is awaiting
Ingo's decision on which subset of the ahead-commits to publish
upstream.

## Next actions

- IL-07: review `src/hassaleh/daemon.py` + `tests/test_il07_trust_boundary.py`
  + `docs/IL-07-implementation.md`, route through SR, land in a
  separate cycle-19 bundle once CLEAN.
- Decide push scope with Ingo.
