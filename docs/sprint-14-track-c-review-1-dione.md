# Sprint-14 Track C — Round-1 Review (Dione)

**Reviewer:** Dione 🌙 (Opus)
**Author:** Nisaba 🌾 (worker-codex / Codex foundation, via failover-watcher
takeover from worker-gemini at 2026-05-07T13:01:09Z)
**Branch:** `sprint-14/track-c`
**Commit under review:** `e603f42c618e07c4a5970570292c0b8fd711b42e`
**Branch base:** `de191a3` (`sprint-14 failover infra: watcher cron +
single-writer policy`) — same as trunk's diverge point.
**Review window:** 2026-05-08T17:01 → 2026-05-09T12:30 CEST.
**Cross-LLM:** Codex author × Opus reviewer ✓ (per S14-OQ-1 reversal +
reviewer reassignment 2026-05-07T15:08 to restore foundation orthogonality).

## Verdict: **CLEAN**

Track C ships as authored. Two advisory notes follow; neither blocks
merge. Recommended merge order: **Track C → trunk first**, then Track B
unblocks per Sprint-14 §5.x sequencing.

---

## 1. Spec coverage

Dispatch prompt (sprint-14-state.json `tracks.C.lastDispatchPrompt`)
required:

| Deliverable | Status | Evidence |
|------|------|------|
| `ApiKey` dataclass with `scopes: list[str]` | ✓ | `src/hassaleh/runtime/capabilities.py` L29-37 |
| `check_scope(api_key, required_scope) -> bool` with exact-match (no prefix wildcards) | ✓ | `capabilities.py` L40-50 |
| Closed `ALLOWED_SCOPES` for the four market.* capabilities | ✓ | `capabilities.py` L17-22 |
| Test fixtures usable by Track B + F | ✓ | `capability_api_key()`, `capability_principal()` in `capabilities.py` L53-66 |
| `CREATE INDEX apikey_scope IF NOT EXISTS FOR (k:ApiKey) ON (k.scopes)` | ✓ | `schema.cypher` L66-69 |
| Sprint-13 §8 / I-CR-4.2 uniqueness constraint | ✓ | `schema.cypher` L70-73 (`CREATE CONSTRAINT attempt_unique_per_segment`) |
| HassalehRuntime wiring through `check_scope()` | ✓ | `src/hassaleh/runtime/core.py` L27, L114-124 |
| Tests (happy path + missing + empty + out-of-allowed-set) | ✓ | `tests/test_runtime_capabilities.py` 7 tests |

## 2. Test execution

Run on e603f42 in isolated worktree (`/tmp/hassaleh-track-c-review`)
with `PYTHONPATH=$WORKTREE/src` to prevent shadowing by the editable
install:

```
$ PYTHONPATH=/tmp/hassaleh-track-c-review/src python3 -m pytest \
    tests/test_runtime_capabilities.py \
    tests/test_runtime_core.py \
    tests/test_runtime_observability.py
============================== 41 passed in 2.04s ==============================
```

Track-C-specific suite: 7/7 pass.
Adjacent runtime suites (regression check): 16/16 (core) + 18/18 (obs).

**Note for future reviewers:** running pytest from the branch root without
`PYTHONPATH` resolves `hassaleh.runtime.core` from
`~/projects/hassaleh/src` (editable-install path), not the branch.
This produced a spurious "test failure" in my preflight before the
isolated re-run. Branch reviews should always go through a fresh clone
*and* a forced `PYTHONPATH`.

## 3. Implementation quality

### 3.1 `check_scope` semantics (capabilities.py L40-50)

```python
def check_scope(api_key: HasScopes, required_scope: str) -> bool:
    if required_scope not in _ALLOWED_SCOPE_SET:
        return False
    return required_scope in api_key.scopes
```

Two-layer check is exactly right:

1. **Closed allowed-set guard.** Even a principal that legitimately
   carries `"future.scope"` is denied if the *handler's required*
   scope isn't in `ALLOWED_SCOPES`. This prevents accidental capability
   inflation if a handler is registered with a typo or a not-yet-
   approved scope name.
2. **Exact-match grant check.** `required_scope in api_key.scopes`
   uses Python's `in` against a list — O(n) but n≤4 and the list is
   the principal's grants, not a wildcard pattern. No glob/prefix.

Test `test_check_scope_does_not_apply_prefix_or_wildcard_semantics`
locks the no-wildcard invariant against future regression.

### 3.2 Closed `_ALLOWED_SCOPE_SET` is a `frozenset` (capabilities.py L23)

Right call: O(1) membership, immutable so it can't be mutated at
runtime to widen the capability surface. The `tuple` `ALLOWED_SCOPES`
is the public API for enumeration; the `frozenset` is the private
fast-path for `check_scope`.

### 3.3 Runtime wiring (core.py L114-124)

The `obs.capability_check_span()` wrap and the deny-path emitting
`Result.capability_denied(error_code="scope-not-granted", ...)` is
consistent with §2.6 of the Sprint-13 plan (canonical execution
order). `result.kind` propagates verbatim to the metric label per
G-CR-4 (Track-D contract).

### 3.4 Fixture factories (capabilities.py L53-66)

```python
def capability_api_key(*scopes: str, id: str = "test-api-key") -> ApiKey
def capability_principal(*scopes: str, id: str = "test-principal") -> Principal
```

Asymmetric on purpose: `ApiKey.scopes` is `list[str]` (matches Neo4j
list property type), `Principal.scopes` is `tuple[str, ...]`
(matches the runtime `Ctx` immutability pattern). Both consume varargs
which keeps fixture call-sites short for B/F tests:
`capability_principal("market.analyst.write")`. Good ergonomics.

## 4. Schema additions (schema.cypher)

### 4.1 `apikey_scope` index (L66-69)

```cypher
CREATE INDEX apikey_scope IF NOT EXISTS
  FOR (k:ApiKey) ON (k.scopes);
```

Per Sprint-13 §8 R4. `IF NOT EXISTS` makes re-runs of `schema.cypher`
idempotent. No backfill needed (fresh field on a fresh label).

### 4.2 `attempt_unique_per_segment` constraint (L70-73) — **plan note**

```cypher
CREATE CONSTRAINT attempt_unique_per_segment IF NOT EXISTS
  FOR (a:Attempt) REQUIRE (a.segment_id, a.n) IS UNIQUE;
```

This is correct per **Sprint-13 §8** which is explicit:

> "The schema-level uniqueness constraint on `(Segment.id, Attempt.n)`
>  is added to `schema.cypher` by Track C (§8)."
> — `docs/sprint-13-plan.md:448-449`

However, **Sprint-14 §5 Track B row** lists this same constraint as a
Track B deliverable:

> "schema.cypher (uniqueness constraint per Sprint-13 §8 / I-CR-4.2)"
> — `docs/sprint-14-plan.md:152`

This is a plan-level inconsistency between Sprint-13 §8 and Sprint-14
§5. The author followed Sprint-13 §8 + the dispatch prompt's explicit
"any uniqueness constraint Sprint-13 §8 / I-CR-4.2 prescribes". Not an
author bug.

**Mitigation:** `IF NOT EXISTS` makes both tracks adding the same
constraint a no-op the second time. Track B's reviewer (Inanna)
should be aware that this constraint will already be on trunk when
Track B's review runs, and should not flag it as "missing" if the
Track B branch happens not to re-declare it.

**Plan-level recommendation (advisory, non-blocking):** Update
Sprint-14 §5 Track B row to drop the schema.cypher uniqueness
constraint mention, or update Sprint-13 §8 to defer it to Track B —
pick one source of truth. Owner: dione-main on next plan-edit pass.

## 5. Branch hygiene

### 5.1 Base check ✓

```
$ git merge-base e603f42 origin/trunk
de191a38b3b8be8f7f1265ae9c05065c34b8f67e   # = "sprint-14 failover infra"
```

Track C's branch base **is** the failover-infra commit. Unlike Track G
(which based on a pre-failover commit and would phantom-delete the
watcher on naive merge), Track C will not delete failover infra at
merge time. Verified: `scripts/pm-action-helpers/failover_watcher.py`
present on `sprint-14/track-c`.

### 5.2 State-file divergence — **merge-hygiene note**

```
$ git diff --stat origin/trunk..e603f42 -- sprint-14-state.json
 sprint-14-state.json | 224 +++++++++------------------------------------------
 1 file changed, 37 insertions(+), 187 deletions(-)
```

Track C's branch carries an old (pre-failover-watcher, pre-orchestrator-
audit) `sprint-14-state.json`. **Merge strategy must take trunk's
sprint-14-state.json verbatim** (or rebase the branch onto current
trunk before merge). This is a generic problem with long-lived feature
branches in Sprint-14 due to the live state file; not author fault.

### 5.3 No conflicts on Track-C-owned files ✓

```
$ git log --oneline e603f42..origin/trunk -- \
    src/hassaleh/runtime/{core,capabilities,observability,types}.py \
    schema.cypher \
    tests/test_runtime_capabilities.py tests/test_runtime_core.py
(empty)
```

Trunk has not modified any Track-C-owned source file since e603f42.
Clean fast-forward expected on those files.

## 6. Cross-LLM compliance

`tracks.C.crossLLMViolated=true` was raised at 2026-05-07T13:01:09Z
when failover-watcher reassigned the author from worker-gemini to
worker-codex (Codex), which collided with Nisaba (Codex) as the
original reviewer. Resolution: reviewer reassigned to Dione (Opus) at
2026-05-07T15:08, restoring Codex × Opus orthogonality. This review
honours that reassignment.

`crossLLMViolations[0].resolvedAt` should be set to the verdict
landing time (this commit); state-file update follows.

## 7. Recommended next steps

1. **Merge** `sprint-14/track-c` → trunk (rebase first to absorb
   trunk's `sprint-14-state.json`, then fast-forward the code files).
2. **Unblock Track B dispatch** per Sprint-14 §5.x sequencing — Track
   B handlers import `check_scope` and `capability_*` fixtures.
3. **Plan-cleanup** (advisory): resolve the Sprint-13 §8 vs.
   Sprint-14 §5 Track B row inconsistency on the
   `attempt_unique_per_segment` constraint.

— Dione 🌙, 2026-05-09T12:30 CEST
