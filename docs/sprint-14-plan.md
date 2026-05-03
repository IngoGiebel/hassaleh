# Sprint 14 — Runtime Operator Closure

**Author:** Dione
**Status:** v1 — Round-1 CRs integrated (Inanna CLEAN, Nisaba CHANGE-REQ on §5.G + Track-G diff-surface); pending Nisaba Round-2 verify.
**Created:** 2026-05-03
**Revised:** 2026-05-04 (v1)
**Sprint timeline (tentative):** starts on Round-2 CLEAN from Nisaba; ~3–4 working days.
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
| **G** — Observability v1.0.1 patch | Closes the seven Track-D Round-1 advisories deferred per Dione's 2026-04-26 arbitration. **Scope is fixed and small** — see §5.G below for the per-advisory breakdown | worker-opus | Inanna (Round-1 only; if CLEAN no Round-2) | `src/hassaleh/runtime/observability.py`, `tests/test_runtime_observability.py` (~3 new tests), change-log entry in `docs/sprint-13-plan.md`. (D-A3 routes to Track E; not a Track-G code change.) |

### 5.G Track G — v1.0.1 patch breakdown

The seven advisories from `docs/sprint-13-track-d-review.md` §6 (Inanna's
Sprint-13 Track-D Round-1 review) that Dione's 2026-04-26 arbitration
deferred to v1.0.1.

This is a **normalized remediation table**, not a verbatim lift. The
"Sprint-13 source (verbatim)" column quotes Inanna's §6 summary
faithfully; the "Severity (Dione)" column is Dione's v1.0.1 priority
labelling (not present in source); the "Sprint-14 proposed resolution"
column is Dione's chosen v1.0.1 fix, which may differ from any specific
suggestion in the source. Reviewers verifying source-fidelity should
spot-check column 2 against `docs/sprint-13-track-d-review.md` §6 #1–#7.

| ID | Sprint-13 source (verbatim, `docs/sprint-13-track-d-review.md` §6) | Severity (Dione) | Sprint-14 proposed resolution |
|----|------------------------------------------------------------------|------------------|-------------------------------|
| **D-A1** | §6 #1: "`_ensure_metrics()` lazy init is not thread-safe. Two concurrent first-callers can each create a `CollectorRegistry`; the second overwrites the first. … **Recommendation:** wrap the body in a `threading.Lock` *or* (preferred) eager-init at daemon startup so Track A never lazy-initializes from a request thread." | P1 (production-load risk) | Eager init at daemon startup, guarded by `is_obs_enabled()`, called from the runtime entrypoint **before** the first dispatch (preferred path from source). `_ensure_metrics()` retains a `threading.Lock` as defense-in-depth for any test or out-of-band caller. |
| **D-A2** | §6 #2: "`assert _registry is not None` in `get_runtime_registry()` is strippable under `python -O`. … Trivial fix: rebind locally — `_, _ = _ensure_metrics(); return _registry` — or have `_ensure_metrics()` return the registry too." | P2 (defense-in-depth) | Have `_ensure_metrics()` return `(counter, histogram, registry)`; `get_runtime_registry()` calls it and returns the third element. No `assert`. (Equivalent to source's "have `_ensure_metrics()` return the registry too" alternative.) |
| **D-A3** | §6 #3: "Same-name family collision across Sprint-12 and Sprint-13 — both define `hassaleh_intent_duration_seconds`, with `{stage}` and `{intent_type}` label sets respectively. Track D's dedicated-registry choice contains the implementation-side problem, but **Track E's `/metrics` exposure must scrape both registries (or expose them on separate paths) or Prometheus will reject one family.** This is a **plan-level** inconsistency; flagging for editorial reconciliation, not for Track D rework." | P1 (cross-sprint reuse risk) | Source frames this as a **Track-E /metrics exposure** decision, not a Track-D code change. Sprint-14 routes the resolution into Track E: the migration spec adds an explicit `/metrics` exposure rule (separate paths or merged scrape) and a dashboard-rule reconciliation note. **Track G itself takes no code action on D-A3.** |
| **D-A4** | §6 #4: "Counter-cardinality DoS by `intent_type` is mitigated only because §3 closes the enum to six types. Track D itself does not validate `intent.type` is in that enum — it trusts Track A's pydantic validator. Defense in depth: a small whitelist guard inside `record_metric` would harden the boundary against a future Track A refactor regression. Non-blocking; out of §2.6 scope." | P1 (production-load risk) | Allow-list of the six §3 Intent types inside `record_metric`; un-registered types emit `intent_type="unknown"` and increment a separate guard counter `hassaleh_intent_unknown_type_total`. (Source called this "non-blocking, out of §2.6 scope"; Sprint-14 elects to land it as defense-in-depth.) |
| **D-A5** | §6 #5: "Sub-ms histogram resolution. `_DURATION_BUCKETS` floor at 10ms; sub-ms RAM-only intents (e.g., a `validation-error` that fails before any Cypher) collapse into the smallest bucket and ruin p50 resolution. Out of §2.6 scope; v1.0.1 follow-up under 'Histogram-bucket tuning.'" | P2 (latency observability) | Add four sub-ms boundaries to `_DURATION_BUCKETS`: `0.0005, 0.001, 0.002, 0.005` (preserves the existing 0.01 anchor and all higher buckets). |
| **D-A6** | §6 #6: "`emit_log` does not call `obs_logging.setup()`. Track D assumes Track A or the daemon entrypoint runs `obs_logging.setup('hassaleh-daemon', env)` before the first dispatch. Reasonable separation of concerns, but **worth surfacing in the Track A wire-up review:** failing to call setup means the structlog processor chain (including `pii_redaction_processor`) is bypassed." | P2 (caller-contract surface) | Source asks for **Track-A wire-up review surfacing**. Sprint-14 lands two complementary actions: (a) Track G adds a module-docstring precondition note + integration test asserting setup-then-emit is the canonical sequence; (b) Track-A non-blocking review (Sprint-14 §7.3) explicitly cites D-A6 in its setup-call path verification. |
| **D-A7** | §6 #7: "`_LOGGER_NAME = 'hassaleh.runtime'` is **not** in `SERVICE_ENUM`. **This is correct** — `_LOGGER_NAME` is a logger name, not a service name; only `obs_logging.setup()` validates against `SERVICE_ENUM`. Track D's `get_logger(_LOGGER_NAME)` correctly bypasses that check. **Verifying for the record so a future refactor doesn't conflate the two.**" | P2 (anti-regression doc) | Source is an OBSERVATION confirming intentional separation, **not** a fix request. Sprint-14 lands a one-line cross-reference comment at the `_LOGGER_NAME` definition in `observability.py` documenting that the name is intentionally outside `SERVICE_ENUM` and pointing at `obs_logging.setup()` as the only `SERVICE_ENUM`-validated surface. **No code-name change.** |

**Track G is bundled as ONE commit** with subject
`sprint-14 Track G: observability v1.0.1 patch (D-A1..D-A7)`. The
commit's body lists each advisory and the line-numbered fix.
`docs/sprint-13-plan.md` change-log gets a v1.0.1 entry pointing at
the Track-G commit SHA.

**Diff surface (consolidated):** Track G touches **one source file**
(`src/hassaleh/runtime/observability.py`) and **one test file**
(`tests/test_runtime_observability.py`, ~3 new tests covering D-A1
thread-safety, D-A4 unknown-type guard, D-A6 setup-then-emit). Plus
the change-log entry in `docs/sprint-13-plan.md`. Track G does **not**
touch `src/hassaleh/obs/tracing.py` (none of D-A1..D-A7 require it);
the Track-E migration spec (Track E, separate commit) is the routing
target for D-A3.

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
small enough (one source file `observability.py` + ~3 new tests in
`test_runtime_observability.py` + a change-log entry; D-A3 is routed
to Track E, not a Track-G code change) that worker-codex could
plausibly author them in a single dispatch under tighter Inanna
scrutiny. Should Sprint 14 use Track G as a bounded re-trial of codex
+ gemini, or hold the only-opus stance? Default in v0: hold the
stance. Reviewers may recommend revision.

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
- **2026-05-04 v1**: Round-1 CR integration.
  - Inanna R1 verdict: CLEAN (no changes required from Inanna).
  - Nisaba R1 verdict: CHANGE-REQ. Three findings addressed:
    - **BLOCKING (D-A7 source inversion)**: §5.G restructured as a
      4-column normalized remediation table (ID | Sprint-13 source
      verbatim | Severity (Dione's labels) | Sprint-14 proposed
      resolution). D-A7 corrected: source is an OBSERVATION
      confirming intentional separation between `_LOGGER_NAME =
      "hassaleh.runtime"` and `SERVICE_ENUM`; v1.0.1 fix is a
      cross-reference comment in `observability.py`, **not** a
      logger-name change. The earlier v0 wording would have inverted
      the source design intent.
    - **ADVISORY (editorial fix-sketch drift)**: Each row's "Sprint-14
      proposed resolution" column now explicitly distinguishes
      Dione's chosen v1.0.1 fix from the source's wording. D-A3 is
      explicitly re-routed from Track G to Track E (per source's
      "plan-level inconsistency, not Track D rework"); D-A6 lands a
      Track G code+doc change AND a Track-A wire-up review citation
      (per source's request to surface in Track-A review).
    - **ADVISORY (Track-G diff-surface inconsistency)**: §5 main
      table, §5.x point 3, §5.G "Diff surface" paragraph, and §6.1
      "small enough" wording all consolidated to one source file
      (`observability.py`) + ~3 new tests + change-log entry.
      `obs/tracing.py` removed from Track-G files.
  - `sprint-14-state.json`: `tracks.G.files` updated to drop
    `obs/tracing.py`.
  - Inherited §1, §2, §3 untouched (anti-scope guard preserved).

— Dione 🌙
