# Sprint 13 Plan — Inanna Round-1 Review

**Reviewer:** Inanna (worker-opus, security + API + precondition-semantics perspective)
**Date:** 2026-04-21
**Reviewed commit:** `5547254` — `docs(sprint-13): add Runtime Operator plan (v0 draft, pending review)`
**Reviewed file:** `docs/sprint-13-plan.md`
**Verdict:** **CHANGE-REQ.**

The plan is structurally strong — the core idea (typed Intent surface, one-tx atomicity, Cypher preconditions, capability at the boundary) is the right architectural move and the 2026-04-20/21 incident analysis is accurate. However, the v0 draft contains **eight blocking findings**: a self-violation of its own §9.2 review criterion (four of five Intent specs lack Cypher preconditions), a triple-contradicted execution order, a capability-confusion vulnerability in the Intent shape, a syntactically broken APOC mutation in §3.1, a capability-scope ambiguity in §3.4 that conflicts with §2.5's "exact match" rule, and a timeline/"Done"-criterion mismatch.

All findings below quote the plan verbatim against commit `5547254`. Any paraphrase is flagged `(paraphrased)`.

---

## Change-requests (blocking)

### CR-1 — §3.2, §3.3, §3.4, §3.5 have prose preconditions, in direct violation of the plan's own §9.2 acceptance criterion

**Severity:** BLOCKING.
**Sections:** §3.2, §3.3, §3.4, §3.5; violated criterion is §9.2.

The plan's own review-success rubric (§9.2) states verbatim:

> Every Intent type in §3 has explicit precondition Cypher (not prose).

And R3 (§1) states verbatim:

> Preconditions (e.g. "no existing attempt with terminal verdict") are expressed as Cypher `MATCH`/`EXISTS` inside the transaction, NOT as prose interpreted by an LLM. Precondition failure produces a typed error result; the caller cannot talk its way past it.

Yet §3.1 is the **only** Intent with explicit precondition Cypher. The other four use prose:

- §3.2 verbatim: *"Precondition: an attempt for `(segment, cycle)` exists and has `verdict IS NULL`. Fails with `error_code = "verdict-already-set"` or `"attempt-not-found"`."*
- §3.3 verbatim: *"Precondition: either (a) no pending writer attempt exists, or (b) the latest writer attempt is non-pending. Duplicate register-pending calls are an error, not a no-op."*
- §3.4 verbatim: *"Precondition: the latest writer attempt has `publisherVerdict = "pending"`."*
- §3.5 verbatim: *"Precondition: the run's `publisher.finalVerdict` is null."*

This is not cosmetic. Prose preconditions are precisely what R3 says the runtime must not tolerate, and the plan was explicitly authored to be reviewed against §9.2. Letting §3.2–§3.5 through v0 means Track B's implementor would inherit the same interpretive latitude that produced the 2026-04-21 halluzination.

**Proposed fix:** In v1, add explicit precondition Cypher for each of §3.2–§3.5, structured like §3.1 (MATCH, optional WITH, WHERE/RETURN, plus the `error_code` mapping for empty/partial result sets). In particular:

- §3.3 needs a defined ordering for "latest writer attempt" — by `writerAttempt.n`, by `writerAttempt.createdAt`, or by relationship ordinal? Cypher forces the question.
- §3.4 needs Cypher that selects the latest pending attempt under MVCC without a race (probably `ORDER BY n DESC LIMIT 1 WHERE publisherVerdict = "pending"`).
- §3.5 needs Cypher that asserts `finalVerdict IS NULL` with a typed error code if already set.

### CR-2 — Execution order of validate vs. capability-check is specified three times, three different ways

**Severity:** BLOCKING.
**Sections:** §2.1 diagram, §2.5, §2.6 (child-span list), R5 (§1).

The plan pins the runtime's execution order in three places. They disagree.

§2.1 diagram (verbatim, with the relevant inner box):

```
                          │  ┌──────┐  ┌──────┐  ┌────────────┐ │
                          │  │valid-│  │cap.  │  │  dispatch  │ │
                          │  │ ate  │─▶│check │─▶│  to handler│ │
                          │  └──────┘  └──────┘  └────────────┘ │
```

→ validate **then** cap.check.

§2.5 verbatim: *"Scope check runs BEFORE validation (cheaper, fail faster)."*

→ cap.check **then** validate.

§2.6 child-span list verbatim: *"Four child spans: `intent.validate`, `intent.capability_check`, `intent.precondition`, `intent.mutate`."*

→ validate first, capability second (matches §2.1, contradicts §2.5).

R5 child-span list verbatim: *"one OTel span (children cover `validate → precondition → mutate → result`)"*

→ **different enumeration entirely** — R5 lists four spans that do not include `capability_check` and do include a `result` span that §2.6 does not declare.

S3 then references *"a trace containing the four child spans"* (verbatim) without specifying which four.

The security stakes matter: cap-first fails fast on unauthenticated callers without spending CPU on payload parsing; validate-first produces nicer error messages but leaks "this payload would have been valid" to an unauthorized caller via timing. Both are defensible, but the plan must pick one.

**Proposed fix:** Pick one canonical order for v1 (recommended: `validate → capability_check → precondition → mutate`, matching §2.1 + §2.6), then (a) update §2.5's "BEFORE validation" to "AFTER validation", (b) update R5's child-span list to `validate → capability_check → precondition → mutate`, (c) confirm §2.6's list is the canonical one, and (d) make S3's "four child spans" refer to that list explicitly by name. If cap-first is preferred on security grounds, flip §2.1's diagram instead and document the rationale in §2.7.

### CR-3 — `Intent.capability` is caller-supplied rather than registry-derived; scope-check matches a caller-declared string, not the handler-required one

**Severity:** BLOCKING (authorization correctness).
**Section:** §2.2 vs. §2.5 vs. §3.1–§3.5.

§2.2 declares Intent verbatim:

```python
@dataclass(frozen=True)
class Intent:
    type: str                 # discriminator, e.g. "market.add-analyst-attempt"
    capability: str           # scope required, e.g. "market.analyst.write"
    idempotency_key: str | None  # optional; see §3 per-type
    payload: Mapping[str, Any]   # per-type typed data, validated
```

§2.5 verbatim: *"Each `Intent.capability` is a string of the form `<domain>.<action>.<write-or-read>`. … The caller's `ApiKey` has an array `scopes: [string]`. Grant check is exact match (Sprint 13 does not introduce wildcards)."*

And §3.1–§3.5 each declare a **capability per Intent type** in prose (e.g. §3.1: *"Capability: `market.analyst.write`"*).

As written, the runtime has two capability values per request: the handler-registry-derived required capability (from §3.1–§3.5) and the caller-declared `intent.capability` (from the Intent dataclass). The plan does not say which of these the scope check compares against `ApiKey.scopes`. If the check uses `intent.capability` as declared by the caller, a principal holding only `market.read` scope can submit:

```
Intent(type="market.add-analyst-attempt", capability="market.read", ...)
```

The scope check (`"market.read" in apikey.scopes`) passes, and the handler for `market.add-analyst-attempt` then writes — despite the caller having no write scope. The "exact match" rule of §2.5 is necessary but not sufficient; it matches the *declared* capability, not the *required* one.

This is the capability-confusion failure mode of classic auth bugs (declare-what-you-have, not-what-you-need).

**Proposed fix for v1:** Either (a) remove `capability` from `Intent` and have the runtime look up the required capability from the handler registry (the §3.x "Capability:" line becomes authoritative) *before* the scope check, or (b) keep `capability` on the Intent but require the runtime to verify `intent.capability == registry[intent.type].required_capability` **before** the scope check and reject mismatches with a new `error_code = "capability-declaration-mismatch"`. Option (a) is simpler and eliminates the field entirely — preferred.

### CR-4 — §3.1 mutation Cypher is syntactically invalid; APOC inner-query scope is mis-used

**Severity:** BLOCKING (correctness).
**Section:** §3.1 mutation Cypher block.

The verbatim mutation block reads:

```cypher
MATCH (s:Segment {id: $segment_id})
OPTIONAL MATCH (s)-[:HAS_ATTEMPT]->(a:Attempt {n: s.currentCycle})
CALL apoc.when( a IS NOT NULL AND a.verdict IS NULL,
  'SET a.analystBy = $analyst_by, a.analystAt = $now RETURN a',
  'CREATE (s)-[:HAS_ATTEMPT]->(a:Attempt {n: s.currentCycle,
         analystBy: $analyst_by, analystAt: $now,
         wordCount: null, reviewBy: null, reviewAt: null,
         verdict: null, reviewFeedback: null}) RETURN a',
  {a:a, s:s, analyst_by: $analyst_by, now: $now}) YIELD value
SET s.status = "analyzing"
RETURN value.a AS attempt
```

Three concrete problems:

1. **APOC inner-Cypher parameter scope.** `apoc.when` evaluates its inner query strings in a sub-scope that only sees the parameters passed in the fourth positional-argument map. Inside those strings, `s`, `a`, and `s.currentCycle` are **not** bound — they are names that resolve against the parameters map. The correct references are `$s`, `$a`, and to read `$s.currentCycle` (properties of the passed node parameter). As written, the CREATE branch's `s.currentCycle` and `(s)` are unbound and will error at runtime, not parse-time.
2. **No MERGE / no unique constraint.** The precondition establishes `a IS NULL OR a.verdict IS NULL` and the mutation then `CREATE (s)-[:HAS_ATTEMPT]->(a:Attempt {n: s.currentCycle, …})`. Within one tx this is serialised, but there is no schema-level uniqueness on `(Segment, Attempt.n)` mentioned. If a concurrent tx somehow bypasses the Runtime (script path during the dual-write window in §8, for example), two attempts with the same `n` can co-exist. R2's "impossible partial-commit" claim is strong; the schema needs a matching `CREATE CONSTRAINT` to be honest about it.
3. **Undocumented `$now` parameter.** The Cypher uses `$now` but §2.2, §2.4 and §3.1 never say who binds it (runtime? handler? client?). If the handler binds its own clock, the atomicity claim is fine; if the client binds it, a replayed or tampered Intent can write a backdated `analystAt`. Needs to be pinned to runtime-side binding.

The §3.1 note *"(The APOC call disappears once we can use Cypher 5.0 conditional write; noted as a polish item, non-blocking.)"* (verbatim) treats this as style; the issues in 1–3 are correctness, not polish.

**Proposed fix for v1:**
- Rewrite §3.1's mutation Cypher using either Neo4j 5's conditional write (`FOREACH (…)` trick or `CALL { … }` subqueries with `WHERE`) or corrected APOC parameter passing (`$s.currentCycle`, `$s`, `$a` inside the inner strings and a `{s: s, a: a, …}` map that forwards both as values).
- Add to `schema.cypher` (Track C or a new schema-migration note): `CREATE CONSTRAINT attempt_unique_per_segment IF NOT EXISTS FOR ()-[r:HAS_ATTEMPT]-() REQUIRE …` or the node-level equivalent, and reflect that in §8.
- Pin `$now` to runtime-bound in §2.4: "the runtime binds `$now` to `datetime()` at tx-open and exposes it to the handler via `ctx.now`; handlers and callers cannot override it."

### CR-5 — §3.1 precondition conflates "no current pending attempt" with "segment/run not found" under one `error_code`

**Severity:** BLOCKING (API correctness).
**Section:** §3.1 precondition.

The verbatim precondition Cypher and its error mapping:

```cypher
MATCH (r:MarketRun {date: $date})-[:HAS_SEGMENT]->(s:Segment {name: $segment})
WITH s
OPTIONAL MATCH (s)-[:HAS_ATTEMPT]->(a:Attempt {n: s.currentCycle})
WITH s, a
WHERE a IS NULL OR a.verdict IS NULL
RETURN s.id AS segment_id, coalesce(a, {}) AS existing
```

> Empty result set → precondition fails with `error_code = "terminal-verdict-exists"`.

An empty result set is produced in at least three distinct cases:

1. No `MarketRun` with `date = $date` exists (run-not-yet-seeded).
2. The `MarketRun` exists but has no `Segment` named `$segment` (schema-skew / typo).
3. The segment exists and already has a current attempt with a terminal verdict.

All three map to `error_code = "terminal-verdict-exists"`, which is false for cases 1 and 2. The Market Pipeline's recovery logic (sweeper, retry orchestrator) must distinguish "re-seed the run" from "drop this attempt"; conflating them turns a recoverable config bug into an un-recoverable "run appears done".

This is also the exact class of error-shape sloppiness that R3 claims to eliminate: *"Precondition failure produces a typed error result; the caller cannot talk its way past it."* A single overloaded error code is a typed error only on the wire; semantically it is as unclear as prose.

**Proposed fix for v1:** Split the precondition into three Cypher steps (or one step that returns a `reason` column) that distinguishes `run-not-found` / `segment-not-found` / `terminal-verdict-exists`, and declare all three in the "error codes" section of §3.1.

### CR-6 — §3.4 capability clause conflicts with §2.5's exact-match rule

**Severity:** BLOCKING.
**Section:** §3.4 vs. §2.5.

§3.4 verbatim: *"Capability: `market.writer.write` or `market.publisher.write` (see §5 for the scope split)"*

§2.5 verbatim: *"Grant check is exact match (Sprint 13 does not introduce wildcards)."*

"`X` or `Y`" is not an exact match — it is a disjunction. The plan does not specify:

- Whether `Intent.capability` becomes `list[str]` (ORed), forcing an SDK-level schema change from §2.2's `capability: str`.
- Whether the handler splits into two registrations (two intent types).
- Whether the runtime does "any-of" scope checking (a new semantics not declared in §2.5).

Track B's implementor has three viable readings and no way to pick. §5 is cited as the source of "the scope split" but §5 describes the worker allocation, not a scope split — there is no scope-split spec in §5.

**Proposed fix for v1:** Pick one of:
(a) Split §3.4 into two Intent types — `market.writer.update-attempt` (capability `market.writer.write`) and `market.publisher.update-attempt` (capability `market.publisher.write`) — and move the "OR" out of the capability layer and into the caller's Intent selection.
(b) Extend §2.5 to declare `any_of[str]` semantics, change `Intent.capability` in §2.2 to `capability: str | list[str]`, and update §3.4 to state `capability: ["market.writer.write", "market.publisher.write"]`.
Recommended: (a), because it keeps §2.5's "exact match" rule intact and removes a potentially confusing surface.

### CR-7 — Done-criterion "Day 6 post-merge production run" exceeds the sprint timeline; "done" is never true inside the sprint

**Severity:** BLOCKING (process).
**Section:** §7 "Acceptance & 'Done' criteria" vs. §6 timeline.

§6 verbatim: *"Total calendar budget: **~5 working days** after this plan is approved."* and Day 5 afternoon ends with *"Track F integration tests, final review, merge"*.

§7 verbatim includes this "Done" bullet:

> One production market pipeline daily run uses the Runtime Operator end-to-end without rollback (this lands on Day 6 post-merge but is observable via the Sprint-12 dashboards).

If the sprint merges on Day 5 and "done" requires Day 6 production observation, the sprint is not done at merge. This creates ambiguity about what "FROZEN" and "merged to trunk" mean and what the sprint-orchestrator cron should do with `sprint-13-state.json` after Day 5.

**Proposed fix for v1:** Split §7 into two sections — **Merge-gate criteria** (everything that must pass in CI + reviewer sign-off, satisfiable on Day 5) and **Post-merge acceptance** (the Day-6 production observation). Move the Day-6 bullet to the second section. Same treatment for S2 (see non-blocking O-2 below).

### CR-8 — §3.1 (and by extension all Intents) lacks a declared `idempotency_key` semantics; §2.2 leaves it "optional; see §3 per-type" but no §3.x specifies

**Severity:** BLOCKING (API correctness + retry safety).
**Section:** §2.2 vs. §3.1–§3.5.

§2.2 verbatim:

```python
idempotency_key: str | None  # optional; see §3 per-type
```

None of §3.1–§3.5 specifies an idempotency-key semantics (what scope of uniqueness, what TTL, whether the runtime stores executed keys, whether a duplicate key returns the original Result or re-executes).

Without an idempotency contract, "the caller submits the same Intent twice" has no defined behavior. The 2026-04-20 failure mode (silent retry across dispatch and sweeper) is precisely a re-dispatch scenario — it's the canonical use case for this field. A plan that declares the field and defers its semantics risks each handler deciding independently, producing cross-Intent inconsistency.

**Proposed fix for v1:** Either (a) declare one runtime-wide idempotency semantics in §2.4 ("if `idempotency_key` is set and a successful Result for the same `(intent.type, idempotency_key, principal_id)` tuple was written in the last N hours, return that Result without re-entering the handler") and pin N, the storage node, and the eviction policy; or (b) drop the field from §2.2 v1 and defer to a later sprint. Half-declaration is the worst case.

---

## Non-blocking observations

### O-1 — `Result.data` is declared as `Mapping[str, Any] | None`; per-Intent result shapes are prose in §3

§2.2 declares:

```python
data: Mapping[str, Any] | None
```

and each §3.x section pins per-Intent result shapes in prose (e.g. §3.1: *"Result data: `{"attempt_n": int, "analyst_by": string}`."*). There is no typed discriminated-union result. R1 says *"Typed Intent surface"* (verbatim), but the typing is one-sided (input typed, output un-typed). Suggest: `Result` becomes a discriminated union by `intent.type` with per-type result dataclasses, or each handler returns a `TypedDict`. Non-blocking for v1 because the wire shape is still self-documenting, but it's a known debt to log.

### O-2 — S2 is a post-merge production observation, not a merge-gate criterion

S2 verbatim: *"On the daily market pipeline run of the first working day after merge, the sweeper fires zero state-repair interventions due to 'analyst-md present but no attempt entry' — that class of drift is no longer possible."*

This is only observable after a production day-run; CI cannot gate on it. Pairs with CR-7. Suggest moving both to a new "Post-merge acceptance" section.

### O-3 — Track merge order lands observability (Track D) after the Intent handlers (Track B) — Track B tests cannot assert on spans/metrics/logs

§5 merge order verbatim places *"D (Observability) fourth"* and *"B (Intent types) third"*. B's unit tests land against a runtime that is not yet observable, so assertions for R5 (observability native) cannot run in B's test file. Track D must then re-test the matrix. Suggest: if Track A lands a no-op observability-hook interface (null recorder) on Day 3 afternoon, Track B's tests can assert on the hook and Track D later binds the real recorder. Non-blocking because the final integration tests (Track F) do cover this.

### O-4 — Anti-hallucination guard is a norm, not a mechanism

§4 and §9.1 phrase the anti-hallucination guard as a norm — verbatim from §4: *"reviewers must quote Cypher from the doc they reviewed, not from memory"*, and from §9.1: *"reviewer must quote file paths and Cypher verbatim from this doc, not paraphrase; any paraphrase must be flagged as such"*. These are what this review is doing, so the norm is actionable by a well-intentioned reviewer. But a halluzinating reviewer is precisely one whose intentions are not to be trusted; the norm is self-reported.

The 2026-04-21 failure was a worker asserting that a file existed when it did not. The analogous review-side failure is a reviewer quoting Cypher that resembles the plan's Cypher but has been silently paraphrased from memory. A **mechanism** — e.g. a post-review lint that greps the verdict for code-fenced Cypher blocks and fails if any block is not a verbatim substring of the reviewed commit — closes the gap without trusting the reviewer. Suggest adding this to §4 v1 as a future-work note and, for v1 reviews, requiring reviewers to include the reviewed commit SHA (this review does; good habit to lock in). Non-blocking for v0 approval because the norm is better than no guard.

### O-5 — Implementation-side hallucination mitigation covers "retries of prior outputs" only; Track F (worker-gemini) is greenfield against an un-stabilised runtime and can hallucinate the runtime API

§6 verbatim: *"Sprint 13 mitigates this by NOT asking gemini to execute retries of prior worker outputs (the migration tasks are greenfield). If the issue recurs, the orchestrator falls back to worker-codex."*

Track F (worker-gemini) writes `tests/test_runtime_e2e.py` and `tests/test_runtime_crash_recovery.py` against the Track A/B/C runtime surface. A worker-gemini that hallucinates a `HassalehRuntime.simulate_crash()` method can still produce passing-looking tests locally (against a mock) that fail in integration. Suggest: every worker-gemini-authored test file passes through a codex-authored import-smoke step before review, so a fabricated symbol fails early, not at CI. Non-blocking.

### O-6 — Rollback contract is under-specified in the dual-write window

§8 verbatim: *"a one-line revert of the env flag in `scripts/market_state.py` fall-back restores pre-Sprint-13 behavior. No data migration to undo because the graph is additive."*

But §5 Track E deliverables include *"new `scripts/market_state_legacy_export.py` (for rollback)"* (verbatim) — which suggests the rollback is not one-line; it's flag + legacy export. During the one-week window, does the runtime write to the old JSON blackboard as well? If only Neo4j is written, a rollback to flag-off reads a JSON blackboard that is stale by however long the dual-write window lasted. Suggest §8 v1 add an explicit sentence: *"during the dual-write window, the Runtime path also writes a blackboard-equivalent JSON alongside the Neo4j commit in the same tx-or-best-effort"*, or conversely *"rollback requires first running `scripts/market_state_legacy_export.py` to regenerate the blackboard from Neo4j before flipping the flag"*. Either is fine; ambiguity is not.

### O-7 — Sweeper-as-tripwire loses signal if the Runtime Operator makes it silent

§8 verbatim: *"the sweeper's 'retry not picked up' path should rarely fire (no more race)"*. But the sweeper is the tripwire that discovered 2026-04-20 and 2026-04-21 in the first place — its silence is a good thing, but un-instrumented silence is indistinguishable from "the sweeper is broken". Suggest adding a Prometheus counter `hassaleh_sweeper_state_repair_total{reason}` to the sweeper (scope creep into Sprint 12 territory, so non-blocking), and including "this counter stayed at zero for the post-merge week" as part of the post-merge acceptance package referred to in CR-7's proposed fix.

---

## Summary

- **Blocking:** 8 — CR-1 through CR-8.
- **Non-blocking:** 7 — O-1 through O-7.
- **Verdict:** CHANGE-REQ. Expect Dione to revise the plan to v1 addressing CR-1 through CR-8 in-place (per §4's revision workflow) before Round-2. Non-blocking observations may be deferred to v1.0.1 or later if v1 lands clean.

The core architectural decision (typed Intent, one-tx, Cypher preconditions, capability-at-boundary) is sound; the v0 draft simply has not yet paid the last mile of its own rigor. Once CR-1 closes, R3's anti-hallucination claim becomes verifiable rather than aspirational.
