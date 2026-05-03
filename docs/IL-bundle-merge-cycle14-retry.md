# IL-01 / IL-02 / IL-03 merge bundle — cycle 14 retry

Date: 2026-04-19

## TL;DR

- **Test gate: GREEN** (`29 passed in 4.29s`, exit 0) on the second retry after killing a stuck bolt-probe loop.
- **Bundle is already committed locally** as `0078b39` (the single atomic commit that matches the requested scope).
- **Push to origin: NOT performed this cycle — scope decision needed.**
  The branch `trunk` is **18 commits ahead** of `origin/trunk`. Pushing `trunk` as-is would publish much more than the IL-01/02/03 bundle, including an unreviewed IL-04 commit.

## Test gate outcome

Command (corrected env vars — see "Env-var mismatch" below):

```bash
NEO4J_URI=bolt://localhost:7690 NEO4J_USER=neo4j NEO4J_PASSWORD=hassaleh \
NEO4J_TEST_URI=bolt://localhost:7691 NEO4J_TEST_USER=neo4j NEO4J_TEST_PASSWORD=hassaleh-dev-2026 \
pytest tests/test_sdk.py tests/test_chaos.py -x -q
```

Result: `29 passed in 4.29s` — exit 0.

Full stdout/stderr is appended to `logs/sprint11-cycle14-il-bundle-merge-retry.log`.

## Root cause of the earlier transient

The cycle-14 failure was **not** a stale rate-limit on `bolt://localhost:7690`. Two separate issues were layered:

1. **Env-var mismatch.** `tests/test_chaos.py` reads `NEO4J_TEST_URI` / `NEO4J_TEST_USER` / `NEO4J_TEST_PASSWORD`, defaulting to `bolt://localhost:7691` and `hassaleh-dev-2026`. The task's `NEO4J_URI=bolt://localhost:7690 NEO4J_PASSWORD=hassaleh` never reached the chaos suite; the chaos fixture used the default 7691/`hassaleh-dev-2026`, which was fine in principle.
2. **Stuck bolt-probe loop (PID 424945).** A leftover `until docker exec ... cypher-shell -u neo4j -p hassaleh-test "RETURN 1"` loop had been running every 3 s since 15:31 CET with a typoed password (`hassaleh-test` — correct is `hassaleh-dev-2026`). This kept `AuthenticationRateLimit` permanently tripped on port 7691, so *any* correct-credential connection (including the chaos fixture) failed.

   Killed the loop with `kill -TERM 424945`; after ~15 s cooldown, port 7691 accepted `neo4j / hassaleh-dev-2026` cleanly and the gate ran green on retry.

## Commit status on `trunk`

The IL-01/02/03 bundle was already committed during cycle 14 (by the earlier opus dispatch, before the failing gate) as a single commit:

- **SHA: `0078b39`** — `sec: land IL-01/IL-02/IL-03 (auth derivation, indexed bcrypt, result-auth)`

Files in `0078b39` (exact match with the bundle scope):

```
schema.cypher                                 |   9 +
src/hassaleh/intent_sdk.py                    |  43 ++-
src/hassaleh/sdk.py                           | 187 ++-
tests/test_sdk.py                             | 463 ++-
docs/IL-01-IL-02-security-review.md           | 248 +
docs/IL-01-test-verification.md               | 151 +
docs/IL-02-followup-implementation.md         |  32 +
docs/IL-02-security-review-v2.md              | 244 +
docs/IL-03-followup-timeout-sanitization.md   |  81 +
docs/IL-03-implementation.md                  | 129 +
docs/IL-03-security-review-v2.md              | 180 +
docs/IL-03-security-review.md                 | 342 +
docs/IL-03-test-verification.md               |  71 +
```

Because the commit already exists locally and matches the requested scope, **no new commit was created** in this retry cycle. Creating a second commit with the exact requested message would either have been empty or have duplicated the same changes.

## Why the push was deferred

`git status -sb` reports `trunk...origin/trunk [ahead 18]`. The 18 commits include:

- `0078b39` — the IL-01/02/03 bundle (this task's scope, CLEAN v2).
- `c57dde3` — `fix: sanitize intent execution error surfaces`, which is actually an **IL-04** implementation (touches `src/hassaleh/capabilities/exec_ls.py`, `src/hassaleh/intent_daemon.py`, adds `tests/test_il04_path_leak.py` and `docs/IL-04-implementation.md`). IL-04 is **not in this task's scope** and has **no CLEAN v2 verdict**. A parallel cycle-14 gemini worker was explicitly told "Do NOT commit — leave changes in working tree" but committed anyway.
- 16 older commits from Sprint 10, Sprint 11 kickoff, and Sprint 12 planning, all unpublished on `origin/trunk`.

Pushing `trunk` directly would publish all 18 commits, which exceeds the task's scope and mixes reviewed (IL-01/02/03) with unreviewed (IL-04) security changes.

## Recommended next steps (pending user decision)

Pick one:

**A. Scope-tight push — recommended for security discipline.**
```bash
git push origin 0078b39:trunk
```
Fast-forwards `origin/trunk` to `0078b39` (includes IL-01/02/03 and the 16 prior commits). Leaves `c57dde3` (IL-04) local until it receives a CLEAN verdict.

**B. Full sync.** `git push origin trunk` — lands IL-04 alongside the bundle. Faster, but publishes unreviewed code.

**C. Rewrite first.** If `c57dde3` should not have been committed at all, `git reset --soft 0078b39` would un-commit IL-04 (leaving the files staged/working-tree), then push. Only safe because no one else pulls `trunk` — verify first.

## Files

- Test log: `logs/sprint11-cycle14-il-bundle-merge-retry.log` (both failing runs + the passing retry).
- This report: `docs/IL-bundle-merge-cycle14-retry.md`.
