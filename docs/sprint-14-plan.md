# Sprint 14 — Runtime Operator Closure

**Author:** Dione
**Status:** v0 — pending agentic plan-review (Inanna + gemini-reviewer Round-1)
**Created:** 2026-05-03
**Sprint timeline (tentative):** starts on Round-2 CLEAN from both reviewers; ~3–4 working days.
**Review process:** identical to Sprint 13 §9 — see §6 below for the Round-1 dispatch commands.

**Δ baseline:** Sprint 13 plan v1.1 FROZEN
(`docs/sprint-13-plan.md`, commit `af6803b`, Inanna Round-2b 2026-04-24,
gemini Round-2 2026-04-23). The Sprint-13 plan remains the authoritative
spec for §1 Requirements (R1–R6), §2 Architecture (incl. §2.6
observability surface), §3 Interface Specification (§3.1–§3.6 — six Intent
types). Sprint 14 inherits all of these by reference and does not
re-litigate them. Reviewers MUST NOT raise new findings against
inherited sections; new findings against §1–§3 belong in a v2 of the
Sprint-13 plan, not in Sprint-14 review.

---

## 0. Why Sprint 14 (and not "Sprint 13 continued")

Sprint 13 landed two of six implementation tracks on trunk:

| Track | Status | Commit | Reviewer verdict |
|-------|--------|--------|------------------|
| A — Core runtime | merged | `1499117` (round-1 fix) | Inanna R1 CHANGES-REQ → R1-fix CLEAN at merge |
| D — Observability | integrated | `c0b04f1` | Inanna R2 CLEAN |
| B — Six Intent handlers | **pending** | — | — |
| C — Capability enforcement | **pending** | — | — |
| E — Market Pipeline migration | **pending** | — | — |
| F — Integration tests | **pending** | — | — |

Reasons to declare a fresh sprint number (rather than extending
Sprint-13 indefinitely):

1. **Worker reassignment.** Per Ingo's 2026-04-24 only-opus policy
   (silent-failure pattern in worker-codex + worker-gemini that day),
   all four pending tracks were reassigned from their original authors
   to worker-opus / dione-main. The Sprint-13 plan §6 worker table
   (worker-codex for B/C, worker-gemini for E/F) is therefore stale and
   requires a fresh dispatch plan — that's Sprint-14 §6.
2. **v1.0.1 backlog crystallised.** Track D Round-1 produced 7
   advisories (D-A1..D-A7) deferred to v1.0.1; Track A non-blocking
   left one Track-E precondition (`Result.error_message` exception
   leak); Sprint-13 advisory A-1 (§3.2 SetVerdict) targets Track-B
   review. These need a place to live, and that place is Sprint-14
   Track G.
3. **Calendar gap.** From 2026-04-30 (last Sprint-13 merge) through
   2026-05-03 (today) the runtime team operated with the orchestrator
   cron disabled and B/C/E/F dormant. Treating that gap as "Sprint 13
   stretched" hides three full days of non-progress; reopening as
   Sprint 14 makes the pause explicit and Re-engages the orchestrator.

**Out-of-scope for Sprint 14** (parked until v2+ of the runtime spec):
Rabt intents, Nexus intents, generic public REST/gRPC, cross-graph
transactions, Intent journaling, saga/compensating workflows,
capability-scope wildcards, per-Intent rate limits. These are exactly
the items Sprint-13 §8 listed as "explicitly out of v1" — no scope
creep here.

---

## 1. Requirements

**Inherited unchanged from Sprint 13 plan §1 (R1–R6, success criteria
S1–S4).** Reviewers: do not re-grade; if you wish to amend an
inherited requirement, open a new Sprint-13-plan v2 PR — not a
Sprint-14-plan finding.

The only Sprint-14-specific success criterion:

- **S5 (Sprint-14):** Tracks B + C + E + F all reach `mergedAt != null`
  in `sprint-14-state.json`, AND Track G's v1.0.1 patch lands as a
  named commit on trunk, AND `sprint-13-plan.md` change-log gains a
  v1.0.1 entry pointing at it.

Cross-reference: S1, S2, S3, S4 from Sprint-13 §1 retain their original
acceptance gates; Sprint-14 inherits the gates verbatim.

---

## 2. Architecture

**Inherited unchanged from Sprint 13 plan §2 (§2.1 runtime core, §2.2
Intent shape, §2.3 handler registry, §2.4 transaction scope, §2.5
capability enforcement, §2.6 observability integration, §2.7
trade-offs).**

The two architectural invariants Track A's Round-1 fix added (now in
trunk via commit `1499117`) are also inherited: `Ctx.now`
runtime-bound at transaction-open; `Principal.scopes: tuple[str, ...]`
immutable; runtime never re-raises (commit/rollback/session-open all
caught and mapped to typed `internal-error` codes). These are part of
the trunk-state Sprint-14 work builds on; reviewers can verify against
`src/hassaleh/runtime/core.py` and `src/hassaleh/runtime/types.py` at
HEAD.

---

## 3. Interface Specification

**Inherited unchanged from Sprint 13 plan §3 (§3.1–§3.6, six Intent
types).** Track B in Sprint-14 implements these Cypher specs verbatim;
deviations require a Sprint-13-plan v2, not a Sprint-14 finding.

**Pending advisory from Sprint-13 R2-b (A-1):** §3.2 SetVerdict
non-blocking advisory. Track-B implementation review (Sprint-14) is
where this gets resolved. Carrying it forward in Sprint-14 §7.

---

## 4. Review Phases

### 4.1 Plan-level review (this document)

Identical to Sprint-13 §9 workflow:

- **Round 1:** dispatched after v0 commit (this commit). Reviewers:
  Inanna (security + API + precondition semantics, focus on
  Sprint-14-specific decisions only — track ordering, worker
  assignment, v1.0.1 scope) and gemini-reviewer (clarity, internal
  consistency, anti-hallucination guard).
- **Revision:** Dione integrates CRs, commits v1.
- **Round 2:** verify-only on R1 CRs.
- **Round 3:** Ingo arbitrates if needed.

**Anti-scope guard:** reviewers SHOULD NOT raise findings on inherited
sections (§1, §2, §3). If an inherited section seems wrong, route it
to Sprint-13-plan v2 instead. Sprint-14-plan findings are limited to:
worker assignment (§6), track sequencing, v1.0.1 patch scope (§5
Track G), advisories from Track-A/Track-D Round-1 not yet ticketed.

### 4.2 Per-track review

Identical to Sprint-13 plan §6.5 "Per-track Round-1 review protocol":
one Round-1 review per track deliverable; Round-2 only on CR. The
review-cadence guardrail ("≥2 R1 CRs not addressable in an hour →
flag for orchestrator attention, escalate to Ingo") carries forward
unchanged.

---

## 5. Implementation Tracks

Five tracks for Sprint 14. Tracks B/C/E/F retain Sprint-13's deliverable
shapes (§5 Sprint-13-plan); Track G is new.

| Track | Scope | Author | Reviewer | Files |
|-------|-------|--------|----------|-------|
| **B** — Six Intent handlers | Sprint-13 §3.1–§3.6 verbatim. §3.4 + §3.5 share `check_writer_attempt_pending()` helper. **Includes Sprint-13 advisory A-1 resolution at §3.2 SetVerdict.** **Inherits Track-A non-blocking Track-E precondition** (`Result.error_message` exception leak — return generic `"internal handler error"` from handlers; Track A already implements this for `internal-error` results) | worker-opus | Inanna | `src/hassaleh/runtime/intents/market.py`, `tests/test_runtime_market_intents.py`, `schema.cypher` (uniqueness constraint per Sprint-13 §8 / I-CR-4.2) |
| **C** — Capability enforcement | Sprint-13 §5 Track-C scope. `ApiKey.scopes` schema + index, `check_scope()` helper, capability-test fixtures usable by B and F | worker-opus | Inanna | `src/hassaleh/runtime/capabilities.py`, `schema.cypher` (`CREATE INDEX apikey_scope`) |
| **E** — Market Pipeline migration | Sprint-13 §5 Track-E scope. `scripts/market_state.py` becomes a thin wrapper that builds Intents and calls `HassalehRuntime.execute()`. Backwards-compat env flag `HASSALEH_RUNTIME_OPERATOR=1` per Sprint-13 §8. **Cleared as a precondition: Track-A's `Result.error_message` is now generic** (no longer leaks Cypher / parameter values to callers — see Track-A §"open items" #2) | worker-opus | Inanna | `scripts/market_state.py`, `scripts/market_state_legacy_export.py`, `tests/test_market_state_migration.py`, migration docs |
| **F** — Integration tests | Sprint-13 §5 Track-F scope. End-to-end + crash-recovery (kill between precondition and mutation → graph unchanged) | dione-main | Inanna | `tests/test_runtime_e2e.py`, `tests/test_runtime_crash_recovery.py` |
| **G** — Observability v1.0.1 patch | Closes the seven Track-D Round-1 advisories deferred per Dione's 2026-04-26 arbitration. **Scope is fixed and small** — see §5.G below for the per-advisory breakdown | worker-opus | Inanna (Round-1 only; if CLEAN no Round-2) | `src/hassaleh/runtime/observability.py`, `src/hassaleh/obs/tracing.py`, `tests/test_runtime_observability.py`, change-log entry in `docs/sprint-13-plan.md` |

### 5.G Track G — v1.0.1 patch breakdown

The seven advisories from `docs/sprint-13-track-d-review.md` §6 (round-1)
that Dione's 2026-04-26 arbitration deferred. Lifted from
`docs/sprint-13-track-d-implement.md` and from Inanna's Round-2
verification table:

| ID | Title | Severity | Fix sketch |
|----|-------|----------|------------|
| **D-A1** | Lazy-init thread-safety in `_ensure_metrics()` | P1 (production-load risk) | `threading.Lock` around the `_metrics is None` check + assignment, OR module-load-time eager init guarded by `is_obs_enabled()` |
| **D-A2** | Strippable `assert _registry is not None` under `python -O` | P2 (defense-in-depth) | Replace `assert` with `if _registry is None: raise RuntimeError(...)` so `python -O` does not strip it |
| **D-A3** | Cross-sprint `hassaleh_intent_duration_seconds` family collision | P1 (cross-sprint reuse risk) | Add `sprint=` constant label or rename to `hassaleh_intent_duration_seconds_v1` if a Sprint-15 surface re-registers the same family |
| **D-A4** | Counter-cardinality DoS via `intent_type` | P1 (production-load risk) | Allow-list of registered intent types; `record_metric` emits `intent_type="unknown"` for un-registered types |
| **D-A5** | Sub-ms histogram resolution / 10ms floor | P2 (latency observability) | Adjust `Histogram` buckets to include sub-ms (e.g. add 0.0005, 0.001, 0.002, 0.005) for the `intent.validate` and `intent.capability_check` short-spans |
| **D-A6** | `emit_log` does not call `obs_logging.setup()` | P2 (caller-contract surface) | Document the precondition explicitly in `observability.py` module docstring; add an integration test asserting setup-then-emit is the canonical sequence |
| **D-A7** | `_LOGGER_NAME` vs `SERVICE_ENUM` confirmation | P2 (label naming consistency) | Confirm `_LOGGER_NAME = "hassaleh-daemon"` matches the structlog `service` enum; add cross-reference comment |

**Track G is bundled as ONE commit** with subject
`sprint-14 Track G: observability v1.0.1 patch (D-A1..D-A7)`. The
commit's body lists each advisory and the line-numbered fix.
`docs/sprint-13-plan.md` change-log gets a v1.0.1 entry pointing at
the Track-G commit SHA.

### 5.x Merge order (track sequencing)

Carries forward Sprint-13 §5's logic with two updates:

1. **Track C (Capabilities)** — first. Track B's handlers carry
   capability declarations that must parse against a live capability
   module. Trunk currently has Track A's runtime + Track D's
   observability but no capabilities module.
2. **Track B (Intent handlers)** — second. Builds on A + C.
3. **Track G (v1.0.1 patch)** — in parallel with B (different files,
   no conflicts; G touches `observability.py`, B touches
   `intents/market.py` + `schema.cypher`). Allows Dione to context-
   switch productively while waiting on Track-B reviews.
4. **Track E (Market migration)** — third. Uses A + B + C; the env-flag
   compatibility means a partial state is publishable.
5. **Track F (Integration tests)** — last. Asserts across A + B + C +
   D + E + G, including crash-recovery.

Tracks B and G can author in parallel. Track C must complete before
Track B starts (Track B imports `check_scope`). Track E must complete
before Track F starts (Track F's e2e test exercises the migrated
`market_state.py` path).

---

## 6. Resource allocation

### 6.1 Workers

| Worker | Backing model | Sprint-14 role |
|--------|---------------|----------------|
| worker-opus | claude-opus-4-7 | Dione (orchestration, integration, Tracks B/C/E/G implementation) + Inanna (security + API reviews, separate session) |
| dione-main | claude-opus-4-7 (this session) | Track F authoring (per Sprint-13 §5) |
| worker-codex | gpt-5.4 | **Not used in Sprint 14.** Per 2026-04-24 only-opus policy. |
| worker-gemini | gemini-3.1-pro-preview | **Not used in Sprint 14.** Per 2026-04-24 only-opus policy. |

**Rationale for keeping the only-opus stance:** the silent-failure
pattern in codex + gemini that triggered the 2026-04-24 reassignment
has not been audited or addressed since. Lifting the policy without
audit would be a regression in the very class of bug the runtime
operator was designed to make structurally impossible.

**Open question for plan reviewers:** Track G's seven advisories are
small enough (one file, six tests) that worker-codex could plausibly
author them in a single dispatch under tighter Inanna scrutiny. Should
Sprint 14 use Track G as a bounded re-trial of codex + gemini, or hold
the only-opus stance? Default in v0: hold the stance. Reviewers may
recommend revision.

### 6.2 Sprint-14 cron + state file

- `~/projects/hassaleh/sprint-14-state.json` — blackboard with
  per-track phase and verdicts.
- `hassaleh-sprint-orchestrator` cron (existing id
  `5d854f3b-8dea-4e5c-a680-9ba5eee1c64b`, every 2h) gets re-pointed
  from `sprint-13-state.json` to `sprint-14-state.json` once this
  plan is FROZEN.

### 6.3 Round-1 review dispatch — concrete commands

**Triggered by:** v0 commit of this file (`sprint-14-plan.md`) on trunk.

```bash
# Inanna (security + API correctness, Sprint-14-specific decisions)
trinity-bus send --from dione --to inanna --async --timeout 900 \
  -m "$(cat <<'PROMPT'
SPRINT-14 ROUND-1 PLAN REVIEW — INANNA

Context:
- DATE=2026-05-03
- PLAN_FILE=~/projects/hassaleh/docs/sprint-14-plan.md (v0)
- BACKLOG=~/projects/hassaleh/docs/sprint-14-backlog.md
- STATE=~/projects/hassaleh/sprint-14-state.json
- BASELINE=~/projects/hassaleh/docs/sprint-13-plan.md (v1.1, FROZEN — DO NOT REGRADE)

Task: review v0 of the Sprint-14 plan as a delta to the FROZEN
Sprint-13 plan. Focus only on Sprint-14-specific decisions:

  - Track ordering (§5.x merge order — is C → B → G||E → F sound?)
  - Worker assignment (§6.1 — should the only-opus stance hold for
    Track G, or is Track G a bounded re-trial candidate?)
  - Track G scope (§5.G — are the seven advisories the right v1.0.1
    bundle, or should one or more split off?)
  - Track-B advisory A-1 (Sprint-13 §3.2 SetVerdict) — is it
    correctly scheduled at Track-B implementation review?
  - Track-A non-blocking Track-E precondition (Result.error_message
    leak) — is it now resolved, or does Sprint-14 need a separate
    line item?

DO NOT raise findings against inherited §1, §2, §3 — those are
Sprint-13-plan v2 territory, not Sprint-14 territory.

Produce ~/projects/hassaleh/docs/sprint-14-plan-review-1-inanna.md with:
  - One row per finding: BLOCKING / ADVISORY / OBSERVATION
  - Concrete pointer (line number, §, or commit SHA) per finding
  - Overall verdict: CLEAN / CHANGE-REQ

Anti-hallucination guard: quote file paths and § references verbatim
from the v0 file; any paraphrase must be flagged as such.
PROMPT
)"

# gemini-reviewer (clarity + internal consistency)
trinity-bus send --from dione --to nisaba --async --timeout 600 \
  -m "$(cat <<'PROMPT'
SPRINT-14 ROUND-1 PLAN REVIEW — NISABA (gemini-reviewer role)

[Same shape as Inanna's prompt, with focus on:
  - Internal consistency between §0, §5, §6
  - Cross-references back to Sprint-13 plan are reachable (no broken §
    pointers)
  - The "Δ baseline" inheritance rule is unambiguous to a fresh reader
  - Track G's seven-advisory table matches the Sprint-13-track-d-review
    §6 source verbatim]

Output: ~/projects/hassaleh/docs/sprint-14-plan-review-1-gemini.md
PROMPT
)"
```

**Budget:** 15 min wall-clock per reviewer (parallel via `--async`).
Trinity-Bus async is mandatory here per the 2026-04-30 lesson logged
as `feedback_trinity_bus_async_for_long_jobs.md` — sync calls on
multi-minute jobs produce rc=124 races even on disk success.

**Hand-off signal to Sprint-14 implementation phase:** both review-1
docs committed with `verdict: CLEAN` after R2 (or R1 if no CRs).
Identical to Sprint-13's grep target.

---

## 7. Acceptance & "Done" criteria

### 7.1 Merge-gate criteria (per Sprint-13 §7.1, inherited)

- Green build, S1, S3, S4 from Sprint-13 §1 all pass in CI.
- All six Intent types pass per-intent tests (one test per
  declared `error_code` value).
- Capability-denied path exercised per Intent.
- Crash-recovery test green (S2 in test form).
- **Sprint-14 plan v1+ FROZEN with both Round-1 (or Round-2) verdicts
  CLEAN.**
- **All five Sprint-14 tracks (B, C, E, F, G) at `mergedAt != null` in
  `sprint-14-state.json`.**

### 7.2 Post-merge acceptance (Sprint-13 §7.2, inherited verbatim)

- One production market-pipeline daily run uses the runtime end-to-end
  without rollback.
- The sweeper fires zero `"analyst-md present but no attempt entry"`
  state-repair interventions on that production run.

### 7.3 Sprint-14-specific advisory closure

- **A-1 (Sprint-13 R2-b non-blocking):** §3.2 SetVerdict — addressed
  during Track-B implementation review; Inanna's Track-B Round-1 must
  cite the resolution explicitly.
- **D-A1..D-A7 (Sprint-13 Track-D R1 deferred):** all closed by Track G;
  Inanna's Track-G Round-1 verdict ties each advisory to a specific
  line-numbered fix.

---

## 8. Cross-cutting concerns

**Inherited from Sprint-13 §8.** No new cross-cutting items in
Sprint 14. The schema additions (`ApiKey.scopes` index from Track C;
`(Segment.id, Attempt.n)` uniqueness constraint from Track B) carry
forward verbatim.

---

## 9. Meta — This plan under the agentic review workflow

Identical to Sprint-13 §9 with the inheritance carve-out from §4.1
above. Reviewer should not regrade inherited spec; reviewer should
focus on Sprint-14-specific decisions.

### 9.1 Explicit non-reviewability items

- **The decision to do Sprint 14 at all** — Ingo approved on 2026-05-03
  in conversation ("Phase 1 go mit Memory-Save"). Reviewers do not
  arbitrate whether Sprint 14 should exist.
- **The choice to keep only-opus** — that's the default in §6.1.
  Reviewers MAY recommend revision (Track G as bounded re-trial), but
  the default stance is non-reviewable as scope.
- **Sprint-13 §1, §2, §3 inheritance** — see §4.1 anti-scope guard.

---

## Change log

- **2026-05-03 v0**: Initial draft. Δ to Sprint-13 plan v1.1.
  Five tracks (B, C, E, F, G). v1 will integrate Round-1 CRs.

— Dione 🌙
