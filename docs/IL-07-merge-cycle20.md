# IL-07 merge — Sprint 11 cycle 20

Date: 2026-04-20
Branch: `trunk`
Repo: `~/projects/hassaleh`
Cycle: Sprint 11 / cycle 20 — final security-audit landing

## Commit

- **SHA:** `d77523ccad7ad92e6452cb2d6f4cb054877f1119`
- **Subject:** `sec: land IL-07 (capability-allowlist trust boundary)`
- **Diff stat:** 5 files changed, 707 insertions(+), 1 deletion(-)
- **Parent:** `fe4f232` (`docs(sprint-11): add IL-04/IL-05/IL-06 merge-bundle cycle-18 record`)

## Files merged

| Path | Status | Notes |
|------|--------|-------|
| `src/hassaleh/daemon.py` | M (+55/-1) | `CAPABILITY_ALLOWLIST` constant, `_execute_capability` validation gate, `uuid` import, sanitized `_MSG_PARAM_VALIDATION_FAILED` constant. |
| `tests/test_il07_trust_boundary.py` | A | 3 adversarial regressions: shell-metacharacter `invoke_command`, non-allowlisted `exec_as_user`, allowlisted happy path. |
| `docs/IL-07-implementation.md` | A | Implementation note: trust-boundary rationale, exact-match allowlisting, sanitized failure mode. |
| `docs/IL-07-test-validation.md` | A | Two-pass test validation, sibling regression evidence, coverage sanity check. |
| `docs/IL-07-security-review.md` | A | Inanna security review, line-referenced threat-model analysis, **CLEAN** verdict. |

## Test gate at merge

Command:
```
.venv/bin/python -m pytest tests/test_il07_trust_boundary.py -v
```

Result: **3 passed in 0.99s**
- `test_graph_injected_shell_metacharacter_in_invoke_command_rejected` PASS
- `test_graph_injected_non_allowlisted_uid_rejected` PASS
- `test_allowlisted_values_reach_subprocess_path` PASS

Sibling regression (IL-04 / IL-05 / IL-06): **10/10 PASS** as recorded in
`docs/IL-07-test-validation.md` (cycle 19, two consecutive runs). Not re-run
at merge gate; the IL-07 surface does not touch IL-04/05/06 code paths.

## Sprint 11 final tally — 7 / 7

| ID    | Topic                                              | Status   | Landed at          |
|-------|----------------------------------------------------|----------|--------------------|
| IL-01 | Intent-claim concurrency / atomic transition       | LANDED   | (pre-cycle-14 bundle) |
| IL-02 | Heartbeat staleness detection                      | LANDED   | (pre-cycle-14 bundle) |
| IL-03 | Sudoers-scope hardening                            | LANDED   | (pre-cycle-14 bundle) |
| IL-04 | Path-leak / sanitized error surface                | LANDED   | cycle-18 bundle     |
| IL-05 | TOCTOU symlink-swap rejection                      | LANDED   | cycle-18 bundle     |
| IL-06 | Heartbeat sequencing                               | LANDED   | cycle-18 bundle     |
| IL-07 | Capability-allowlist trust boundary                | **LANDED** | **cycle-20 (this commit, `d77523c`)** |

**Sprint 11 security audit: 7 / 7 complete.**

## Outstanding follow-ups

### Non-blocking — file before cycle 22

**Ticket:** "IL-07: add regression for unknown `capability_id` branch."
- **Owner:** tests
- **Source:** `docs/IL-07-security-review.md` §3 (coverage gap), §4 (follow-up)
- **Scope:** one new test in `tests/test_il07_trust_boundary.py`:
  - inject no allowlist entry for the supplied `cap_id`,
  - assert sanitized `Parameter validation failed [cid: …]` reason,
  - assert subprocess launch is never awaited,
  - assert raw `cap_id` does not appear in the agent-visible reason.
- **Rationale:** the unknown-id branch at `daemon.py:462-466` is
  structurally load-bearing (it guards the `CAPABILITY_ALLOWLIST[cap_id]`
  dict access at line 468). A future refactor that fuses the two checks
  could silently regress the sanitized failure path to an unhandled
  `KeyError`. Runtime risk today is zero; this is maintenance hardening.
- **Classification:** documentation-confirmed, non-blocking, accept-with-followup.

## Push status

**Not pushed.** Trunk is now 19+ commits ahead of `origin/trunk` pending
Ingo's scope decision on the publish window. This commit (`d77523c`) joins
that queue; no force-push, no rebase, no rewrite of prior history.

## Out-of-scope artifacts left uncommitted

The following modifications and untracked files are present in the working
tree but were intentionally **not** included in this merge:

- `sprint-11-state.json` (operational sprint tracker — separate hygiene)
- `src/hassaleh/agent_dummy.py` (unrelated worker tweak)
- `tests/test_chaos.py`, `tests/test_integration.py`, `tests/test_messaging.py`,
  `tests/test_mvp_intent.py`, `tests/test_stress.py`, `tests/test_task_modes.py`
  (older modifications not in IL-07 scope)
- `docs/IL-bundle-merge-cycle14*.md` (sprint-11 mid-cycle records, separate
  bundle history)
- `docs/sprint-12-plan-review-1-{gemini,inanna}.md` (sprint-12 planning;
  separate landing track)
- `.codex`, `logs/` (tooling state, never tracked)

These remain in the working tree and will be triaged in Sprint 12.
