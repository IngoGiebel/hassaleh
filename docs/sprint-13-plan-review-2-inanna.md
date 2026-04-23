# Sprint 13 Plan — Inanna Round-2 Review

verdict: CHANGE-REQ

**Reviewer:** Inanna (worker-opus, security + API + precondition-semantics perspective)
**Date:** 2026-04-23
**Reviewed commit:** `518213a` — `docs(sprint-13): v1 — all 11 Round-1 CRs integrated + dispatch plan`
**Reviewed file:** `docs/sprint-13-plan.md` (1194 lines)
**Round-1 review:** `docs/sprint-13-plan-review-1-inanna.md`

This review verifies only the eight I-CR items raised in Round-1. Seven
are ADDRESSED; one (I-CR-4) is PARTIALLY-ADDRESSED due to a concrete
schema/mutation inconsistency that would render the uniqueness
constraint inert as declared. All file paths and § references below are
quoted verbatim from v1; anything re-worded is flagged `(paraphrased)`.

---

## Status table

| CR | Topic | Status |
|----|-------|--------|
| **I-CR-1** | §3.2–§3.5 prose preconditions violated §9.2 | **ADDRESSED** |
| **I-CR-2** | Execution order triple-contradicted | **ADDRESSED** |
| **I-CR-3** | `Intent.capability` caller-supplied (capability-confusion) | **ADDRESSED** |
| **I-CR-4** | §3.1 mutation Cypher / APOC scope / `$now` / missing constraint | **PARTIALLY-ADDRESSED** |
| **I-CR-5** | §3.1 precondition conflated three cases under one `error_code` | **ADDRESSED** |
| **I-CR-6** | §3.4 capability `"X or Y"` vs. §2.5 exact-match | **ADDRESSED** |
| **I-CR-7** | "Done" criterion exceeds sprint timeline | **ADDRESSED** |
| **I-CR-8** | `idempotency_key` declared without semantics | **ADDRESSED** |

---

## Per-CR verification

### I-CR-1 — Cypher preconditions for §3.2–§3.5 — **ADDRESSED**

All four previously-prose preconditions are now explicit Cypher with
distinct `error_code` mappings matching §3.1's structure:

- §3.2 `market.set-verdict` — precondition Cypher at `docs/sprint-13-plan.md:462-472`; mapping table at `:476-481` (`run-not-found`, `segment-not-found`, `attempt-not-found`, `verdict-already-set`).
- §3.3 `market.register-writer-attempt` — precondition Cypher at `:502-512`; mapping at `:520-523` (`run-not-found`, `pending-writer-attempt-exists`). The ambiguity flagged in I-CR-1.3 (ordering of "latest writer attempt") is resolved by the explicit `ORDER BY wa.n DESC LIMIT 1` at `:506` with the rationale stated verbatim at `:514-516`: *"The `ORDER BY wa.n DESC LIMIT 1` subquery picks the highest-`n` writer attempt — the unambiguous 'latest' definition (resolves I-CR-1.3's 'ordering ambiguity' flag)."*
- §3.4 `market.update-writer-attempt` (writer role) — precondition Cypher at `:550-561`; mapping at `:565-569` (`run-not-found`, `no-writer-attempt`, `writer-attempt-not-pending`).
- §3.5 `market.update-writer-attempt-by-publisher` (publisher role) — shares §3.4's Cypher by declaration at `:589-591`: *"Precondition: same Cypher as §3.4, same mapping. (Shared helper `check_writer_attempt_pending()` in the implementation; Track B deliverable.)"*
- (v0 §3.5 `set-published` was renumbered to §3.6 after the I-CR-6 split; its own explicit precondition Cypher is at `:616-623` with mapping at `:627-630`.)

§9.2 self-criterion at `:1110-1112` updated verbatim: *"Every Intent type in §3 has explicit precondition Cypher (not prose). v1 addresses this for all six Intent types per I-CR-1 / G-CR-1."* The plan no longer self-violates.

### I-CR-2 — Execution order canonicalised — **ADDRESSED**

A single canonical order `validate → capability_check → precondition → mutate` is now declared once and referenced everywhere; the three prior disagreements are reconciled:

- §2.5 `:271-278` declares it as **canonical** verbatim: *"Canonical execution order (fixed in v1 per I-CR-2 + G-CR-2): validate → capability_check → precondition → mutate. This order is authoritative in the plan; §2.1 diagram, §2.6 span list, R5, and S3 all refer to this order without contradiction."*
- The v0 §2.5 sentence *"Scope check runs BEFORE validation (cheaper, fail faster)"* is removed and replaced by `:287-289` verbatim: *"Validation runs FIRST (so a malformed payload fails before we bother looking up scopes). Capability check runs SECOND, BEFORE precondition (so an unauthorized call costs zero Cypher)."*
- §2.1 diagram `:136-163` shows `validate → cap.check → dispatch to handler`; precondition and mutation live inside the handler's one-tx box, which is consistent with the canonical order (not a contradiction).
- §2.6 child-span list `:306-310` enumerates the four spans in the canonical order: `intent.validate`, `intent.capability_check`, `intent.precondition`, `intent.mutate`, with the explicit note at `:311-313`: *"No `result` child span — the result is the return value of `execute()`, captured by the root span's status/attributes, not a separate child."*
- R5 at `:78` is updated verbatim: *"one OTel span (children: `intent.validate`, `intent.capability_check`, `intent.precondition`, `intent.mutate` — see §2.6 authoritative list)"*.
- S3 at `:117-118` no longer restates the span list: *"the four child spans listed in §2.6 (authoritative)"*.

The v0 R5 discrepancy (four spans that did not include `capability_check` and did include a `result` span) is gone. §9.2 criterion at `:1124-1127` now explicitly requires this convergence.

### I-CR-3 — Capability declared at registration, not on Intent — **ADDRESSED**

The capability-confusion vulnerability is closed at the schema level:

- §2.2 `:182-194` — the `Intent` dataclass now contains only `type: str` and `payload: Mapping[str, Any]`. The `capability: str` field is gone.
- §2.2 change block `:196-203` states the rationale verbatim: *"Removed `capability: str` from Intent (I-CR-3). The caller declared its own required capability, which opened a 'declare-what-you-have' authorization-bypass path. In v1, the runtime looks the required capability up in the handler registry at dispatch time — see §2.3. This closes the bypass: a caller cannot silently escalate by declaring a weaker scope than the handler actually requires."*
- §2.3 `:224-236` declares the registry decorator with `requires_capability="market.analyst.write"` kwarg; `:241-242` verbatim: *"The runtime reads `requires_capability` from the registry during capability check (§2.5), so the caller has no say in what scope is required."*
- §2.5 `:281-283` reinforces: *"The runtime reads this value during capability check; the Intent itself never carries a capability field (I-CR-3)."*
- Each of §3.1–§3.6 declares the capability with the phrase *"Capability (declared at registration, not on Intent)"* — see `:360`, `:444`, `:494`, `:533`, `:580`, `:601`.
- §9.2 criterion at `:1131-1133` pins this as a review invariant.

The caller can no longer submit an Intent that declares `market.read` and have a `market.add-analyst-attempt` handler accept it. CLOSED.

### I-CR-4 — §3.1 mutation correctness — **PARTIALLY-ADDRESSED**

Three sub-items. Two closed, one open.

**I-CR-4.1 (APOC parameter scope) — addressed.** The mutation block `:404-423` switches from `apoc.when` to `apoc.do.when`, and the params map at `:418-419` now includes every referenced binding: `{a: a, s: s, current_cycle: $current_cycle, analyst_by: $analyst_by, now: $now}`. Under `apoc.do.when`, parameters passed via the map are bound as Cypher variables in the inner strings, so `(s)`, `a IS NOT NULL`, and `a.verdict IS NULL` now resolve. Scalar values use explicit parameter syntax (`$current_cycle`, `$analyst_by`, `$now`) so the previous v0 `s.currentCycle` unbound-property access is gone. Note at `:427-429` verbatim: *"The APOC inner queries now receive all referenced bindings explicitly in the params map. `$s`, `$a`, `$current_cycle` are no longer unbound references (v0 bug fixed per I-CR-4.1)."*

**I-CR-4.3 (`$now` binding) — addressed.** §2.4 `:263-267` pins it verbatim: *"Runtime-bound values: `$now` in handler Cypher is the runtime's monotonic UTC timestamp taken at transaction-open time and bound via `ctx.now` on the handler context. Handlers MUST NOT invoke `datetime.now()` directly — the runtime owns time for determinism and testability."* §3.1 `:430-431` back-references §2.4 verbatim: *"`$now` is bound from `ctx.now` at transaction-open (§2.4); the handler never calls `datetime.now()` directly."* The tampered-client-clock attack from I-CR-4.3 is closed.

**I-CR-4.2 (uniqueness constraint) — NOT fully addressed.** A constraint was added at `docs/sprint-13-plan.md:1048-1051` verbatim:

```cypher
CREATE CONSTRAINT attempt_unique_per_segment IF NOT EXISTS
FOR (a:Attempt) REQUIRE (a.segment_id, a.n) IS UNIQUE;
```

However the §3.1 mutation at `:414-417` creates the Attempt node with this property list (verbatim):

```cypher
CREATE (s)-[:HAS_ATTEMPT]->(attempt:Attempt {n: $current_cycle,
       analystBy: $analyst_by, analystAt: $now,
       wordCount: null, reviewBy: null, reviewAt: null,
       verdict: null, reviewFeedback: null})
```

`segment_id` is never written on Attempt. `s.id` is returned as `segment_id` by the precondition query (`:386` and `:398`, paraphrased as "used only as Cypher parameter") and consumed by the mutation's MATCH (`:409`, `MATCH (s:Segment {id: $segment_id})`), but it is not persisted as a property of the Attempt node. Neo4j node-key / node-uniqueness constraints on `(a.segment_id, a.n)` evaluate the tuple per node; when `segment_id` is missing the tuple contains a null, and in Neo4j 5 unique node property constraints treat nulls as "no constraint applies" — so two concurrent writers of the same `(segment, cycle)` could still both create Attempt nodes with the same `n` and no `segment_id`, and the constraint would not fire.

Either (a) the mutation's CREATE clause must add `segment_id: $segment_id` to the property map (§3.1 `:414-417`), matching the constraint, or (b) the constraint at §8 `:1048-1051` must be re-expressed against the `HAS_ATTEMPT` relationship rather than the node (the alternative form the original I-CR-4.2 proposed). Option (a) is simpler and keeps the node the authority.

Remaining-gap pointer: **`docs/sprint-13-plan.md:1050` (constraint predicate `(a.segment_id, a.n)`) vs `docs/sprint-13-plan.md:414-417` (mutation CREATE property list omits `segment_id`).** This is the only R1-scoped blocker still open.

### I-CR-5 — §3.1 precondition error-code conflation — **ADDRESSED**

§3.1 precondition at `:375-389` now returns four boolean columns (`run_missing`, `segment_missing`, `terminal_exists`, plus data columns). The mapping table at `:394-398` splits into three distinct `error_code` values verbatim:

| Condition | `error_code` |
|-----------|--------------|
| `run_missing = true` | `"run-not-found"` |
| `segment_missing = true` | `"segment-not-found"` |
| `terminal_exists = true` | `"terminal-verdict-exists"` |

Confirmation at `:400-402` verbatim: *"All three error codes are declared in §2.2's `Result.error_code` enum for this Intent type. The previous v0 collapse into a single `'terminal-verdict-exists'` is fixed."*

The same pattern (split conditions → distinct error codes) is propagated to §3.2 (`:476-481`), §3.3 (`:520-523`), §3.4 (`:565-569`), §3.6 (`:627-630`) — see I-CR-1 above. The sweeper / retry orchestrator can now distinguish "re-seed the run" from "drop this attempt" from "segment-name typo" as a typed discriminant rather than overloaded prose.

### I-CR-6 — §3.4 capability disjunction removed — **ADDRESSED**

Option (a) from the Round-1 proposal (split into two Intent types) was taken:

- §3.4 `market.update-writer-attempt` (writer role) at `:531-576` — capability `market.writer.write`.
- §3.5 `market.update-writer-attempt-by-publisher` (publisher role) at `:578-597` — capability `market.publisher.write`.
- §3.4 `:535-538` verbatim: *"Split from v0 (single `market.update-writer-attempt`) per I-CR-6 / G-CR-3 option (a). Two Intent types with single-capability each is strictly cleaner than one Intent with `any_of` semantics that the exact-match rule (§2.5) doesn't support."*
- §2.5 exact-match rule at `:285-287` unchanged: *"Grant check is exact match (Sprint 13 does not introduce wildcards or `any_of` semantics; see §3.4/§3.5 for the concrete pattern)."*
- R6 at `:79` updated to "Six initial Intent types covering the Market Pipeline" with the list now naming the split pair verbatim.
- §5 Track B at `:680` updated to "The six handlers of §3.1–§3.6" including the `check_writer_attempt_pending()` shared-helper deliverable.

No `list[str]` capability semantics, no wildcards. The exact-match rule is intact. CLOSED.

### I-CR-7 — Merge-gate vs Post-merge separation — **ADDRESSED**

§7 is split into §7.1 and §7.2 at `:991-1031`. Preamble at `:993-997` verbatim: *"Split in v1 (per I-CR-7) into **Merge-gate criteria** (satisfiable before Day 5 afternoon merge) and **Post-merge acceptance** (observed on or after Day 6 in production)."*

- §7.1 Merge-gate criteria `:999-1014` — five bullets, all Day-5-satisfiable: green build + success criteria S1/S3/S4 in CI, per-intent tests, capability-denied path exercised, crash-recovery test green, plan FROZEN with CLEAN Round-2 verdicts.
- §7.2 Post-merge acceptance `:1016-1027` — two bullets, both observation-based: one production daily run end-to-end without rollback, sweeper's `"analyst-md present but no attempt entry"` class fires zero times on that run. The scope-creep caveat at `:1025-1027` verbatim: *"The sweeper may still fire for unrelated failure classes (never-started segments); only the specific class addressed by Sprint 13 must be zero."*

The FROZEN-at-merge ambiguity from R1 is gone: merge-gate items are exhaustively listed in §7.1 and are all Day-5 verifiable.

### I-CR-8 — `idempotency_key` semantics — **ADDRESSED**

Option (b) from the Round-1 proposal (drop the field from v1, defer to later sprint) was taken:

- §2.2 dataclass at `:182-194` no longer contains `idempotency_key: str | None`.
- §2.2 change block at `:204-210` verbatim: *"Removed `idempotency_key: str | None` from Intent v1 (G-CR-5). The semantic was declared in §2.2 but unspecified per-type (scope, TTL, storage, retry-return-logic). Rather than bake in a half-specified guarantee, v1 defers Intent-level idempotency to a named v2 sprint. The Market Pipeline's own retry logic (Sprint 13's sweeper + the precondition `terminal-verdict-exists` guard) already covers the practical cases."*
- §3.1–§3.6 contain no per-type `idempotency_key` semantics (the v0 "optional; see §3 per-type" that never materialised is gone with the field).
- Change-log row at `:1182` confirms verbatim: *"G-CR-5 (idempotency_key declared but unspec) | §2.2 | `idempotency_key` field removed from v1 Intent. Deferred to a named v2+ sprint. Sprint-13 sweeper + `terminal-verdict-exists` precondition cover practical retry safety."*

(The fix is labelled G-CR-5 in the change log because gemini-reviewer raised the same issue; the substance closes I-CR-8 in full.) The "half-declared field with undefined behaviour" failure mode is resolved.

---

## Advisory for v1.1+ (non-blocking, outside R1 scope)

- **A-1 — Attempt `segment_id` property semantics when writer role differs from analyst role.** Once the I-CR-4.2 remaining-gap is closed by writing `segment_id` on Attempt (my recommended option (a)), §3.2's `market.set-verdict` mutation (`:483-490`, paraphrased because §3.2 only states mutation in prose, not Cypher) will also need to preserve the property. Track B should ensure the `check_writer_attempt_pending()` helper and the shared mutation path both read from and respect `segment_id`. Non-blocking for this review — it is a direct consequence of fixing the constraint, not a separate finding.
- **A-2 — `hassaleh_sweeper_state_repair_total{reason}` counter.** Still recommended as O-7 was in R1; not raised as blocking. The sweeper-as-tripwire argument is independent of R1 closure.
- **A-3 — Anti-hallucination review mechanism (grep-based lint for code-fenced Cypher verbatim match).** R1 O-4 remains unrealised; v1 §4 at `:654-659` still phrases the guard as a norm, not a mechanism. Parking as v1.1 follow-up.

---

## Summary

- **Blocking remaining:** 1 — I-CR-4.2 (uniqueness-constraint vs mutation-property mismatch).
- **Addressed:** 7 of 8 I-CR items.
- **Advisory (non-blocking):** 3.
- **Verdict:** CHANGE-REQ, narrowly. Expect a v1.1 one-line patch to §3.1's mutation CREATE clause (or an equivalent §8 constraint reshape) before Day-5 merge. Once that lands, the plan should be re-verifiable by a single targeted Round-3 read of §3.1 + §8 alone — the other 7 CRs do not need re-review.

The v1 revision was otherwise precise and honest: it quoted the original Round-1 CR labels in-place, made the execution-order and capability-at-boundary changes structural rather than textual, and chose the safer "drop the field" option for idempotency rather than half-specifying it. The remaining I-CR-4.2 gap is a detail, not a design flaw.
