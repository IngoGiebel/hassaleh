# Sprint 13 Plan — Inanna Round-2b Review (narrow re-verify of I-CR-4.2)

verdict: CLEAN

**Reviewer:** Inanna (worker-opus, security + API + precondition-semantics perspective)
**Date:** 2026-04-24
**Reviewed commit:** `13dce84` — v1.1 polish addressing the one remaining Round-2 PARTIAL finding.
**Reviewed file:** `docs/sprint-13-plan.md` (1244 lines, v1.1)
**Prior rounds:** `docs/sprint-13-plan-review-1-inanna.md`, `docs/sprint-13-plan-review-2-inanna.md`.

This is a **narrow** re-verify. Round-2 verdict-ed CHANGE-REQ with 7/8 CRs
ADDRESSED and only **I-CR-4.2** PARTIAL. v1.1 is a single-file polish
targeting exactly that finding. I re-verify only I-CR-4.2 here; the
other 7 CRs remain ADDRESSED per Round-2 and are not re-read.

All quoted file excerpts below are verbatim from v1.1 at the stated
line ranges; anything re-worded is flagged `(paraphrased)`.

---

## I-CR-4.2 — Uniqueness constraint now actually fires — **ADDRESSED**

### Round-2 remaining gap (restated for context)

In v1 the §8 constraint required `(a.segment_id, a.n) IS UNIQUE` on
`Attempt`, but the §3.1 mutation CREATE clause did not persist
`segment_id` as a property of the Attempt node. Neo4j 5's node-property
uniqueness-constraint semantics treat a null component as "no constraint
applies", so the constraint was inert — two concurrent writers of the
same `(segment, cycle)` could each pass the precondition independently
and both CREATE an Attempt node, and the constraint would not reject
the duplicate.

Round-2 recommended option (a): keep the Attempt node authoritative and
have the mutation write `segment_id` on both branches of the
`apoc.do.when` dispatch. v1.1 takes option (a).

### Verbatim evidence from v1.1

**CREATE clause** — `docs/sprint-13-plan.md:418-423`:

```cypher
  'CREATE (s)-[:HAS_ATTEMPT]->(attempt:Attempt {
         segment_id: $segment_id,
         n: $current_cycle,
         analystBy: $analyst_by, analystAt: $now,
         wordCount: null, reviewBy: null, reviewAt: null,
         verdict: null, reviewFeedback: null}) RETURN attempt',
```

`segment_id: $segment_id` is now the first property in the CREATE
property map. Previously absent.

**SET clause** — `docs/sprint-13-plan.md:414-417` (within the
`apoc.do.when` "condition true" branch):

```cypher
  'SET a.analystBy = $analyst_by,
       a.analystAt = $now,
       a.segment_id = coalesce(a.segment_id, $segment_id)
   RETURN a AS attempt',
```

The SET branch now carries a lazy-backfill assignment for `segment_id`
using `coalesce`, so a pre-existing Attempt node that predates v1.1 and
lacks `segment_id` gets the field written on its next touch, without
overwriting any value already present. Previously absent.

**APOC params map** — `docs/sprint-13-plan.md:424-425`:

```cypher
  {a: a, s: s, segment_id: $segment_id, current_cycle: $current_cycle,
   analyst_by: $analyst_by, now: $now}
```

`segment_id: $segment_id` is now in the params map, so both inner query
strings can resolve `$segment_id`. This is the mechanical prerequisite
that makes the SET and CREATE changes above actually bind.

**Constraint §8** — `docs/sprint-13-plan.md:1065-1066`, inside the
§8 cross-cutting-concerns block on Neo4j schema impact:

```cypher
CREATE CONSTRAINT attempt_unique_per_segment IF NOT EXISTS
FOR (a:Attempt) REQUIRE (a.segment_id, a.n) IS UNIQUE;
```

Unchanged from v1. The v1.1 change is purely on the mutation side: the
property that the constraint predicates on is now persistently written.

**Change-log entry** — `docs/sprint-13-plan.md:1212-1243` explicitly
records the v1.1 scope as this single fix, per Round-2's recommendation:

> **2026-04-24 v1.1**: Single-file polish addressing Inanna Round-2's
> one remaining PARTIAL finding, I-CR-4.2.

> **The fix**: §3.1 mutation now writes `segment_id` on both paths of
> the `apoc.do.when` dispatch: […] Per Inanna's Round-2 recommendation,
> option (a) — keep the node authoritative rather than re-expressing the
> constraint against the HAS_ATTEMPT relationship.

### Is `(segment_id, n)` non-null on new Attempt writes?

**Yes**, on every code path that produces or updates an Attempt in §3.1:

- **CREATE branch** (line 419): `segment_id: $segment_id` is
  unconditionally written. Since `$segment_id` is sourced from the
  precondition's `s.id AS segment_id` (line 386) where `s` is the
  matched Segment node, and Segment's `id` is a node property that
  cannot be null on a matched node, `$segment_id` is non-null at
  mutation time. `n` is `$current_cycle`, sourced from
  `s.currentCycle AS current_cycle` (line 387); if `current_cycle` were
  null the precondition's `OPTIONAL MATCH (s)-[:HAS_ATTEMPT]->(a:Attempt
  {n: s.currentCycle})` at line 381 would be a no-op match rather than
  a null-tuple Attempt. So `(segment_id, n)` is `(non-null, non-null)`
  at CREATE time and the constraint **fires** for concurrent duplicate
  CREATEs — this is the load-bearing invariant behind Round-2's
  finding.
- **SET branch** (line 416): `a.segment_id = coalesce(a.segment_id,
  $segment_id)`. If `a.segment_id` was already set by a prior CREATE,
  it stays. If it was missing (pre-v1.1 Attempt node), it becomes
  `$segment_id`. After this SET, the Attempt has `segment_id` non-null,
  so any subsequent CREATE attempt on the same `(segment_id, n)` tuple
  is caught by the constraint.

In both branches, the post-mutation state has `segment_id IS NOT NULL`
and `n IS NOT NULL` on the Attempt, satisfying the §8 constraint's
predicate. The Neo4j 5 null-tuple-skip rule no longer silently disables
the constraint.

### Status

**ADDRESSED.** The concrete mechanism Round-2 asked for (option (a):
persist `segment_id` on Attempt so the §8 constraint's predicate is
never null-on-new-writes) is implemented exactly as recommended. The
concurrent-writer race the constraint was meant to close is now
structurally closed: a second writer's CREATE against an existing
`(segment_id, n)` tuple will fail at commit with Neo4j's
constraint-violation error, which §8 at `:1072-1074` maps verbatim to
`Result.kind = "precondition-failed", error_code =
"concurrent-attempt-conflict"`.

---

## Other CRs

All 7 other I-CR items remain **ADDRESSED** per Round-2. They were not
re-read in Round-2b; v1.1 does not touch §2.1, §2.2, §2.3, §2.5, §2.6,
§3.2–§3.6, §7, or §9.

---

## Advisory for v1.2+ (non-blocking; noted as a side-effect observation)

- **A-1 (from Round-2) still holds for v1.2+.** §3.2's
  `market.set-verdict` mutation remains prose (`:499-504`, paraphrased:
  "write verdict; adjust segment status; re-evaluate phase1Complete"),
  not Cypher. When Track B implements it, it must target the Attempt by
  `(segment_id, n)` (or its MATCH equivalent) and must not overwrite
  `segment_id`. With v1.1's change, Attempts created via §3.1 now carry
  `segment_id`, so a §3.2 mutation that writes verdict-only (never
  touches `segment_id`) keeps the constraint predicate non-null. I do
  **not** raise this as a v1.1 blocker because §3.2 is already
  prose-only in v1 by design (Track B converts it to Cypher), and the
  reminder is already captured in A-1 of my Round-2 review. Parking as
  v1.2+ / Track B review.

- **No new v1.1-induced issues.** I checked whether the added property
  write created a stale-copy risk elsewhere (e.g. duplicated `segment_id`
  on both Segment and Attempt drifting out of sync if a segment were
  ever re-keyed). Segment `id` is not mutated anywhere in §3.1–§3.6, and
  the Attempt's `segment_id` is bound from `$segment_id` which is
  sourced from the Segment node at transaction-open. There is no code
  path in v1.1 that would produce a stale `a.segment_id`. Clean.

---

## Summary

- **Blocking remaining:** 0.
- **Re-verified:** I-CR-4.2 — ADDRESSED.
- **Not re-read (per Round-2b scope):** I-CR-1, I-CR-2, I-CR-3, I-CR-4.1,
  I-CR-4.3, I-CR-5, I-CR-6, I-CR-7, I-CR-8 — all remain ADDRESSED per
  Round-2.
- **Advisory:** A-1 from Round-2 carries into v1.2+ / Track B review.
  No new issues.
- **Verdict:** CLEAN.

v1.1 is the minimum-diff fix Round-2 asked for, quoting the recommended
option verbatim in the change log and applying it exactly to §3.1
CREATE + SET + params map. The plan is ready to freeze as v1.1 and
proceed to Phase P2 (Track A + D kickoff) per §6.5.
