# Sprint 13 Plan — Round-1 Review (gemini-reviewer, clarity & completeness)

- **Reviewer:** gemini-reviewer (worker-gemini, Opus 4.7 backing session, distinct from gemini-author)
- **Date:** 2026-04-21
- **Reviewed document:** `docs/sprint-13-plan.md`
- **Reviewed commit SHA:** `5547254516` (`5547254` short / `554725451642ef766db1744caa130ff1ff37f162` full)
- **Review scope:** §9.2 success criteria for *this document* (clarity, internal consistency, derivable tests, separable track deliverables, no hidden context). Inanna reviews security + API + precondition semantics in parallel.
- **Verdict:** **change-req**

This review follows the 2026-04-21 anti-hallucination guard: every quoted excerpt below was read back from the file at the SHA above and is marked with the exact line number. Any paraphrased passage is flagged as such.

---

## Verification

Five verbatim quotes I read from the file to make this review machine-checkable:

1. **Line 78** (R5 row, verbatim): `Every Intent execution emits one OTel span (children cover ` + `` `validate → precondition → mutate → result` `` + `), one log line, one ` + `` `hassaleh_intent_total{intent_type,result}` `` + ` counter tick`
2. **Line 188** (§2.2 Result.kind literal): `    kind: Literal["ok", "precondition-failed", "capability-denied",`
3. **Lines 247–248** (§2.6 tracing): `` `capability`. Four child spans: `intent.validate`, `` / `` `intent.capability_check`, `intent.precondition`, `intent.mutate`. ``
4. **Line 250** (§2.6 metric): `  - ` + `` `hassaleh_intent_total{intent_type, result, capability_granted}` `` + ` — counter`
5. **Lines 123–124** (S4 metric assertion): `opened; measured by a test that asserts ` + `` `hassaleh_intent_total{ `` / `  result="capability_denied"}` + `` ` ticks without a corresponding ``

All five lines exist verbatim in the reviewed SHA.

---

## Change-Request items

Numbered. Each item lists: section, severity, quoted-verbatim excerpt with line number, and a proposed fix.

### CR-1 — §3.2, §3.3, §3.4, §3.5 preconditions are prose, not Cypher

- **Section:** §3.2 `market.set-verdict`, §3.3 `market.register-writer-attempt`, §3.4 `market.update-writer-attempt`, §3.5 `market.set-published`
- **Severity:** **blocking** — directly violates §9.2 item: "Every Intent type in §3 has explicit precondition Cypher (not prose)."
- **Evidence (verbatim):**
  - §3.2, line 346: `**Precondition:** an attempt for `(segment, cycle)` exists and has`
  - §3.2, line 347: `` `verdict IS NULL`. Fails with `error_code = "verdict-already-set"` ``
  - §3.3, line 365: `**Precondition:** either (a) no pending writer attempt exists, or`
  - §3.4, line 381: `**Precondition:** the latest writer attempt has`
  - §3.5, line 403: `**Precondition:** the run's ` + `` `publisher.finalVerdict` `` + ` is null.`
- **Observation:** Only §3.1 (`market.add-analyst-attempt`, lines 294–302) contains a fenced ```` ```cypher ```` block. The other four sections describe the precondition in English. §9.2 explicitly requires the precondition to be Cypher so it is reviewable as code, not interpreted.
- **Why this matters (non-hallucinated rationale):** the *entire* structural-correctness argument in §1 R3 ("The 2026-04-21 worker-gemini hallucination ... becomes structurally impossible. Precondition is code, not interpretation.") rests on preconditions being code. Four of the five Intents currently fail that bar at the spec layer; handlers authored from this prose will have to invent Cypher, re-opening the exact gap §1 R3 closes.
- **Proposed fix:** For each of §3.2–§3.5, add a ```` ```cypher ```` block after the `**Precondition:**` line, with the same shape as §3.1 (MATCH the relevant graph shape, use WHERE / OPTIONAL MATCH for the negative check, return a bound variable the mutation block reuses). Keep the prose as an English gloss *above* the Cypher if desired, but the Cypher must be the normative form. Reference example for §3.2 (not normative — author to finalise):
  ```cypher
  MATCH (r:MarketRun {date: $date})-[:HAS_SEGMENT]->(s:Segment {name: $segment})
  MATCH (s)-[:HAS_ATTEMPT]->(a:Attempt {n: $cycle})
  WHERE a.verdict IS NULL
  RETURN a.n AS attempt_n
  ```

### CR-2 — R5 and §2.6 disagree on the four child-span identities

- **Section:** §1 R5 vs §2.6
- **Severity:** blocking — direct internal contradiction inside the observability spec, which two tracks (A core, D observability) and success criterion S3 all read as normative.
- **Evidence (verbatim):**
  - §1 R5, line 78: `one OTel span (children cover ` + `` `validate → precondition → mutate → result` ``
  - §2.6, lines 247–248: `` Four child spans: `intent.validate`, `intent.capability_check`, `intent.precondition`, `intent.mutate`. ``
- **Observation:** R5 lists four children `{validate, precondition, mutate, result}`. §2.6 lists four children `{validate, capability_check, precondition, mutate}`. The sets differ: R5 has `result` and is missing `capability_check`; §2.6 has the inverse. S3 (line 117) refers to "the four child spans" without naming them, so whichever set Tracks A+D implement, S3 can be written against it — but that makes R5 or §2.6 stale rather than resolving the disagreement.
- **Proposed fix:** pick the §2.6 set (it includes `capability_check`, which is required for R4 capability enforcement to be observable per-call) and rewrite R5 line 78's parenthetical to match: `(children: intent.validate, intent.capability_check, intent.precondition, intent.mutate)`. Remove the `→ result` child — the result is a root-span attribute, not a child span, and §2.4 already describes it that way.

### CR-3 — Metric label set disagreement: R5/S3 vs §2.6

- **Section:** §1 R5, §1 S3, §2.6
- **Severity:** blocking — defines Prometheus cardinality; observability wiring (Track D) cannot implement an ambiguous label schema.
- **Evidence (verbatim):**
  - §1 R5, line 78: `` one `hassaleh_intent_total{intent_type,result}` counter tick ``
  - §1 S3, line 118: `` `hassaleh_intent_total{intent_type, result}` counter tick ``
  - §2.6, line 250: `` - `hassaleh_intent_total{intent_type, result, capability_granted}` — counter ``
- **Observation:** R5 and S3 declare two labels (`intent_type`, `result`). §2.6 declares three (adds `capability_granted`). S4 (line 124) asserts a counter tick on `result="capability_denied"`, which is only consistent with the R5/S3 two-label shape — the §2.6 three-label shape would require a value for `capability_granted` in the assertion.
- **Proposed fix:** the `capability_granted` label is redundant with `result` (the value `capability-denied` already encodes the denial). Drop the third label from §2.6 line 250 so the counter is `hassaleh_intent_total{intent_type, result}`. Otherwise: extend R5, S3, and S4 to name the third label and give its value in the S4 assertion.

### CR-4 — `result` label value casing inconsistency (`capability-denied` vs `capability_denied`)

- **Section:** §2.2 Result definition vs §1 S4
- **Severity:** blocking — S4 is a verifiable success criterion whose Prometheus PromQL would fail against the label value declared in §2.2.
- **Evidence (verbatim):**
  - §2.2, line 188: `    kind: Literal["ok", "precondition-failed", "capability-denied",`
  - §1 S4, line 124: `  result="capability_denied"}` + `` ` ticks without a corresponding ``
- **Observation:** `Result.kind` is the source of the Prometheus `result` label value (it's the only enum R5 and §2.6 attach to the counter). §2.2 uses hyphens (`capability-denied`, `precondition-failed`, `validation-error`, `internal-error`). S4 uses an underscore (`capability_denied`). They cannot both be right: either the label carrier value is hyphenated (and S4 must be rewritten) or it's underscored (and §2.2 must be rewritten).
- **Proposed fix:** pick one. Prometheus label *values* accept both, so this is a style call. Recommend hyphens (matches §2.2 Literal, matches `error_code` values like `terminal-verdict-exists` on line 306). Rewrite S4 lines 123–124 to read `` `hassaleh_intent_total{result="capability-denied"}` ``. If the runtime instead translates hyphens → underscores for metric compatibility, §2.6 must state that translation explicitly.

### CR-5 — `idempotency_key` declared in §2.2 but not specified in any §3 payload

- **Section:** §2.2 Intent dataclass vs §3.1–§3.5
- **Severity:** blocking — the comment in §2.2 explicitly directs the reader to §3 for per-type behavior, but §3 is silent.
- **Evidence (verbatim):**
  - §2.2, line 183: `    idempotency_key: str | None  # optional; see §3 per-type`
- **Observation:** None of §3.1–§3.5 mentions `idempotency_key` in Payload, Precondition, or Mutation. A handler author reading §3 has no guidance on whether to (a) ignore the key, (b) use it as a dedup predicate in the precondition Cypher, or (c) persist it on the mutation node for replay defense. R2 atomicity and R3 deterministic-precondition together strongly suggest (b)+(c), but the spec doesn't say.
- **Proposed fix:** either (i) for each of §3.1–§3.5, add an `**Idempotency:**` subsection declaring whether the Intent is idempotent under a given key and, if so, the exact Cypher predicate that makes it so (e.g. `MATCH (:Attempt {idempotencyKey: $idempotency_key})`), or (ii) if Sprint 13 v1 is deferring idempotency to a later sprint, remove the field from §2.2 entirely and say so in §1 Non-Requirements. Do not leave a "see §3" comment pointing at silence.

### CR-6 — Success criterion S2 is not a derivable test

- **Section:** §1 Success Criteria S2 vs §9.2 "Every success criterion S1–S4 is verifiable by a test whose name is plausibly derivable from this plan."
- **Severity:** change-req (non-blocking if rephrased; blocking as currently written because §9.2 explicitly requires derivability).
- **Evidence (verbatim):**
  - §1 S2, lines 110–113: `**S2:** On the daily market pipeline run of the first working day / after merge, the sweeper fires zero state-repair interventions / due to "analyst-md present but no attempt entry" — that class of / drift is no longer possible.`
- **Observation:** S2 is a *production observation* on a post-merge daily run, not a test. S1, S3, S4 all imply a test name (`test_runtime_crash_leaves_graph_unchanged`, `test_intent_execute_emits_span_metric_log`, `test_capability_denied_does_not_open_transaction`). S2 has no analogous derivable test — "the sweeper fires zero interventions" is measured by inspecting sweeper logs on a real run. §7 bullet 5 ("One production market pipeline daily run ... observable via the Sprint-12 dashboards") repeats this as an acceptance criterion, so the observation *is* tracked — it's just not a test.
- **Proposed fix:** either (i) reclassify S2 as an *acceptance observation* distinct from the test-style S1/S3/S4 (introduce a subheading "Post-merge observations"), or (ii) add an in-sprint test with a derivable name such as `test_add_analyst_attempt_writes_md_and_attempt_atomically` that asserts at the unit level what S2 asserts at the production level (no divergence between file and Attempt node after a single Intent), and downgrade the production observation to §7.

### CR-7 — S3 log-field name mismatch: `error_type` (S3) vs `error_code?` (§2.6)

- **Section:** §1 S3 vs §2.6
- **Severity:** change-req (low blast radius, but confuses Track D wiring).
- **Evidence (verbatim):**
  - §1 S3, lines 119–120: `in structlog as one JSON line with ` + `` `intent_type`, `trace_id`, `` / `` `duration_ms`, and (on error) `error_type`. ``
  - §2.6, lines 252–253: `- **Logs:** one structlog line per intent with ` + `` `intent_type`, `trace_id`, `` / `` `duration_ms`, `result`, `error_code?`, `principal_id`. ``
- **Observation:** S3 names the on-error field `error_type`; §2.6 names it `error_code?`. §2.2 (line 191) uses `error_code` as the dataclass field and gives examples like `"terminal-verdict-exists"`. S3's `error_type` appears nowhere else. Track D test `test_runtime_log_includes_error_code_on_failure` cannot be derived unambiguously.
- **Proposed fix:** change S3 line 120 to `(on error) error_code` so it matches §2.2 and §2.6.

---

## Non-blocking observations

- **NB-1 — Implicit references to external context.** The plan cites several items without in-document or cross-file references:
  - `(see Rabt R-46)` on line 77 — no indication of where Rabt R-46 is defined. Reader of this plan alone cannot resolve.
  - `(see Sprint 12 CR-8)` on line 591 — Sprint 12 closed with `9cc36c0` on trunk; the CR-8 referent lives in Sprint 12's review docs, but this plan does not link or name them.
  - "Ingo selected Option B ... in the pre-sprint proposal" (change log, lines 630–631) — the pre-sprint proposal is not cited by path. A reader picking this up in six weeks will not know Options A/B/C.
  - Suggested fix: add footnote links (`[Rabt R-46](../rabt/...)`, `[Sprint 12 CR-8](./sprint-12-round-1-track-f-inanna.md#CR-8)`, and a path to the Option B proposal).

- **NB-2 — Track D observability vs "runtime-owned" language in §2.1.** §2.1 invariant 2 (line 167) says "Logging, metrics, and span start are runtime-owned." Track A delivers `src/hassaleh/runtime/core.py`, Track D delivers `src/hassaleh/runtime/observability.py` (line 449). If span start is owned by the runtime core (A) but the observability surface lives in D, then A must import D, which inverts the merge order (A merges first). Not a contradiction per se — A can expose a registration point that D populates — but the plan does not say which pattern is used. Flag for clarification, not blocking.

- **NB-3 — Track C scope-check integration into Track A without a named hook point.** §5 Track C (line 448) says "scope-check integration in runtime", but A's deliverables (line 446) are the runtime core, types, and registry. The merge order places C second, after A. Either (a) A ships a stub capability module that C replaces, or (b) C edits A's core.py, violating the "no shared surface" claim on line 467 ("Tracks A, D can be authored in parallel (different files, no shared surface)" — note C is not claimed parallel with A, so this is less acute). Worth naming the hook in §2.5 or §5.

- **NB-4 — APOC dependency in §3.1 mutation.** The `CALL apoc.when(...)` on line 313 introduces a runtime dependency (APOC plugin availability in the Neo4j instance) that is not mentioned in §8 cross-cutting concerns. Line 324 notes "(The APOC call disappears once we can use Cypher 5.0 conditional write; noted as a polish item, non-blocking.)" — fine, but the Neo4j version precondition (APOC installed OR Cypher 5.0) is a deployment assumption that should live in §8.

- **NB-5 — `phase1Complete` re-evaluation in §3.2 mutation (line 355) is itself a multi-step operation.** "Re-evaluate run-level `phase1Complete` (all segments in a terminal state)." This is a derived write that depends on the Attempt graph state — it's fine inside the same transaction, but the derived-write sub-query is not given as Cypher. Same class of issue as CR-1 at a smaller scale; folding a Cypher block for the re-evaluation into the §3.2 mutation would close it.

- **NB-6 — "Worker-crash simulation" test naming.** §5 Track F deliverable (line 451) names `tests/test_runtime_crash_recovery.py`; §1 S1 (lines 107–109) describes it as "a new test asserting that a simulated worker-crash (kill between precondition and mutation) leaves the graph unchanged." The test *file* is named, but the *test function* inside it is not implied strongly enough for a reviewer to derive. A sentence like "Contains `test_crash_between_precondition_and_mutation_leaves_graph_unchanged`" at §1 S1 or §7 bullet 4 would close the loop. Low priority.

---

## Summary

Six blocking CRs (CR-1 through CR-6) and one small change-request (CR-7), plus six non-blocking observations. The CRs cluster into two themes:

1. **The precondition-as-code bar is unmet for four of five Intents** (CR-1). This is the single most important item because R3's entire safety argument depends on it.
2. **The observability surface is internally inconsistent in three places** (CR-2, CR-3, CR-4) plus one log field (CR-7). Tracks A and D cannot both be implemented against the current wording without choosing which definition to follow.

CR-5 and CR-6 are smaller but concrete: a declared field with no per-type semantics (CR-5), and a success criterion that is an observation, not a test (CR-6).

Verdict: **change-req**. Recommend Dione produce v1 addressing CR-1 through CR-7 before Round-2 dispatch.
