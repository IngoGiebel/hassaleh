# Sprint 13 — Runtime Operator

**Author:** Dione
**Status:** DRAFT v0 — pending Round-1 reviews.
**Created:** 2026-04-21
**Sprint timeline (tentative):** starts when Sprint 12 is fully merged
(already true as of 2026-04-21T20:44+02:00) and this plan is approved
(Round-2 CLEAN from Inanna + gemini-reviewer); ~5 working days.
**Review process:** this plan is subject to the agentic review workflow
described in §9 below. Round-1 reviews land in
`docs/sprint-13-plan-review-1-inanna.md` and
`docs/sprint-13-plan-review-1-gemini.md`.

**Artefact ↔ plan relationship:** §1, §2, §3 of this plan **are** the
three spec artefacts (Requirements, Architecture, Interface). Day 1 of
the sprint polishes them; if reviewers request a split, they move into
separate files `docs/sprint-13-requirements.md`,
`docs/sprint-13-architecture.md`, `docs/sprint-13-interfaces.md`.

---

## 0. Executive Summary

Sprint 13 introduces the **Hassaleh Runtime Operator** — a new layer
that turns agent-driven state mutations from "LLM writes Cypher"
into "agent submits a typed Intent, the runtime validates it and
executes it atomically inside a single Neo4j transaction."

**Why this sprint exists, concretely:**

- 2026-04-20 and 2026-04-21 both produced market-pipeline failures
  whose root cause was *not* bad analysis — it was state inconsistency:
  an analyst wrote `analyst-<seg>.md` to disk, the process died before
  calling `add-analyst-attempt`, and the sweeper then found the file
  but never reconciled the state. On 2026-04-21 a worker additionally
  **halluzinated** that a non-existent file already existed.
- Both failure modes are structural properties of "LLM agents write
  state directly": the write is non-atomic across files + DB; the LLM
  can interpret the task creatively ("already done"), avoiding the
  work silently.
- The Runtime Operator eliminates both classes in one move: the agent
  never writes state. It submits an Intent. The runtime checks
  preconditions (deterministic Cypher) and runs the state mutation
  inside an ACID transaction. A dead agent mid-call leaves the
  transaction uncommitted; a halluzinating agent can still lie in its
  prose, but its lie cannot reach the graph.

**Scope:** runtime infrastructure + the five Intent types the Market
Pipeline actually needs + capability enforcement + Observability
wiring + a migration of `scripts/market_state.py` callers to
`HassalehRuntime.execute(Intent)`.

**Out of scope for this sprint:** Rabt intents, Nexus intents,
generic public API exposure of the runtime, cross-graph transactions.

Every subsequent sprint will be able to add Intent types *without*
re-solving the race and hallucination problems — they become
properties of the surface, not per-use-case concerns.

The sprint is organised as **three spec artefacts** (Requirements,
Architecture, Interface) → **two review rounds** → **six parallel
implementation tracks** → **integration + smoke** → **final review
+ merge**.

---

## 1. Requirements (R1–R6)

The "so what?" for each requirement states what we can do once it's
in place that we cannot do today.

| ID | Requirement | So what? |
|----|-------------|----------|
| **R1** | **Typed Intent surface.** Agents submit an `Intent` object (typed via a discriminated union) to `HassalehRuntime.execute(intent, ctx) -> Result`. No other state-write path exists; `scripts/market_state.py` becomes a client of this runtime, not a sibling. | Every state mutation has a declared schema, a declared precondition, and a declared post-condition. The plain "write a file, then update a DB" pattern is no longer representable. |
| **R2** | **Atomic execution.** Every Intent executes inside **one** Neo4j transaction that spans precondition-checks + mutation + result-write. Partial commits are impossible; a mid-execution worker death leaves the graph exactly as it was before. | The 2026-04-20/21 silent-write-then-die race becomes structurally impossible. The fix is at the surface level, so no per-Intent defensive code is needed. |
| **R3** | **Deterministic precondition checking.** Preconditions (e.g. "no existing attempt with terminal verdict") are expressed as Cypher `MATCH`/`EXISTS` inside the transaction, NOT as prose interpreted by an LLM. Precondition failure produces a typed error result; the caller cannot talk its way past it. | The 2026-04-21 worker-gemini hallucination ("report is already complete") becomes structurally impossible. Precondition is code, not interpretation. |
| **R4** | **Capability enforcement at the runtime boundary.** Every Intent carries a `capability` declaration (e.g. `market.analyst.write`); the runtime checks that the calling principal's API-key grants it. Scope-mismatches are rejected with a typed `CapabilityDenied` result. | Lays the foundation for multi-principal mode (see Rabt R-46) without blocking Sprint 13 on multi-tenant UX. A Sprint-11 authenticated principal now has scoped rights, not global rights. |
| **R5** | **Observability native.** Every Intent execution emits one OTel span (children cover `validate → precondition → mutate → result`), one log line, one `hassaleh_intent_total{intent_type,result}` counter tick, and one `hassaleh_intent_duration_seconds` histogram sample — reusing the Sprint-12 surface. | Intent-level latency, error rate, and result-bucket distribution become first-class dashboard panels on day 1. The sweeper's "segment stuck" gets a metric-level twin (no stuck Intent == no stuck segment). |
| **R6** | **Five initial Intent types covering the Market Pipeline.** Delivered with handlers, preconditions, tests, and a migration of the current `scripts/market_state.py` call-sites: `AddAnalystAttempt`, `SetVerdict`, `RegisterWriterAttempt`, `UpdateWriterAttempt`, `SetPublished`. After Sprint 13, `scripts/market_state.py` is a thin wrapper that builds Intents and calls `HassalehRuntime.execute()`. | The class of failures that broke the market pipeline on 2026-04-19, 2026-04-20, and 2026-04-21 is gone. We have a measured proof: the sweeper-dispatched recovery on any future date finds the state and disk in perfect agreement, because they are no longer separate stores. |

### Non-Requirements (v1 explicitly out)

- **Rabt Intent types** (email, telegram, contact-merge). Deferred to
  their own sprint once Rabt's core comes online.
- **Nexus Intent types** (market-report ingest into the financial
  graph). Deferred to Nexus's own sprints.
- **Generic public API exposure** — Sprint 13 exposes the runtime
  only to Python-side callers in the same process. Cross-process
  (REST/gRPC) Intent submission is a later sprint.
- **Cross-graph transactions** — one Intent, one Neo4j transaction,
  one graph. We do not support "write to Nexus and Rabt atomically"
  in v1. v2+ possibly.
- **Intent replay / journaling** — the Intent is not persisted as an
  event log. It is executed, and the mutation is the record.
  Event-sourcing is a legitimate v2+ evolution, not v1.
- **Saga / compensating actions** — no multi-Intent workflows with
  rollback. The caller composes Intents; if one fails the caller
  decides what to do.
- **Schema-change Intents** — Sprint 13 Intent handlers write nodes
  and edges. They do not run `CREATE INDEX` or migration statements.
  Schema migrations remain in `schema.cypher`.

### Success Criteria

- **S1:** The Market Pipeline's `market_state.py` is refactored to
  use the Runtime Operator for all five state-mutation operations.
  The new code path passes 100 % of existing market-pipeline tests
  *and* a new test asserting that a simulated worker-crash (kill
  between precondition and mutation) leaves the graph unchanged.
- **S2:** On the daily market pipeline run of the first working day
  after merge, the sweeper fires zero state-repair interventions
  due to "analyst-md present but no attempt entry" — that class of
  drift is no longer possible. (Sweeper may still fire for
  "never-started" segments — that is a different failure class,
  unrelated to this sprint.)
- **S3:** Every Intent execution appears in Tempo with a trace
  containing the four child spans; appears in Prometheus as a
  `hassaleh_intent_total{intent_type, result}` counter tick; appears
  in structlog as one JSON line with `intent_type`, `trace_id`,
  `duration_ms`, and (on error) `error_type`.
- **S4:** A Capability-denied call (wrong scope on API key) produces
  a `CapabilityDenied` result without a Neo4j transaction being
  opened; measured by a test that asserts `hassaleh_intent_total{
  result="capability_denied"}` ticks without a corresponding
  `hassaleh_cypher_query_total` tick.

---

## 2. Architecture

### 2.1 Runtime core

```
┌──────────────────┐      ┌──────────────────────────────────────┐
│  Caller (agent,  │      │        HassalehRuntime               │
│  script, adapter)│─────▶│                                      │
│                  │      │  ┌──────┐  ┌──────┐  ┌────────────┐ │
│  submit(Intent)  │      │  │valid-│  │cap.  │  │  dispatch  │ │
│                  │      │  │ ate  │─▶│check │─▶│  to handler│ │
└──────────────────┘      │  └──────┘  └──────┘  └─────┬──────┘ │
                          │                            │         │
                          │                            ▼         │
                          │                   ┌──────────────┐   │
                          │                   │ handler runs │   │
                          │                   │ IN ONE Neo4j │   │
                          │                   │ transaction  │   │
                          │                   └──────┬───────┘   │
                          │                          │           │
                          │                          ▼           │
                          │                   ┌──────────────┐   │
                          │                   │  Result or   │   │
                          │                   │  typed Error │   │
                          │                   └──────────────┘   │
                          └──────────────────────────────────────┘
                                         │
                                         ▼
                                   ┌───────────┐
                                   │  Neo4j    │
                                   └───────────┘
```

Key invariants:

- **No handler writes the graph outside the passed transaction handle.**
  Static enforcement: the handler signature is
  `def handle(intent: T, tx: Transaction, ctx: Ctx) -> Result`
  and the runtime never gives a `Session`/`Driver` to handler code.
- **No side effects before validation.** Logging, metrics, and span
  start are runtime-owned; handlers only contribute their own inner
  spans and structured log keys.
- **Preconditions are Cypher, not Python.** Any "does X exist?" check
  runs as `MATCH … WITH count(*) AS n RETURN n` inside the same `tx`
  as the mutation — so that a concurrent writer between precondition
  and mutation cannot slip past.

### 2.2 Intent shape

```python
@dataclass(frozen=True)
class Intent:
    type: str                 # discriminator, e.g. "market.add-analyst-attempt"
    capability: str           # scope required, e.g. "market.analyst.write"
    idempotency_key: str | None  # optional; see §3 per-type
    payload: Mapping[str, Any]   # per-type typed data, validated

@dataclass(frozen=True)
class Result:
    kind: Literal["ok", "precondition-failed", "capability-denied",
                  "validation-error", "internal-error"]
    data: Mapping[str, Any] | None
    error_code: str | None   # machine-readable, e.g. "terminal-verdict-exists"
    error_message: str | None
```

The discriminator + payload shape gives us:

- compile-time type checking in Kotlin/Python SDKs later (Sprint N+)
- a single `Result.kind` enum that drives the `result` label on the
  `hassaleh_intent_total` Prometheus counter
- one unambiguous observable per intent type

### 2.3 Handler registry

A single global registry maps `intent.type` → handler. Handlers are
registered at module import time via a decorator:

```python
@register_handler("market.add-analyst-attempt")
def handle_add_analyst_attempt(intent, tx, ctx): ...
```

The registry is the only way a handler gets called. Unknown
`intent.type` produces a `validation-error` with `error_code =
"unknown-intent-type"`.

### 2.4 Transaction scope

- The runtime opens the transaction AFTER validation + capability
  check. This means:
  - malformed inputs cost nothing in Neo4j terms
  - capability-denied calls cost nothing in Neo4j terms
- The transaction is committed IFF the handler returns a
  `kind == "ok"` result. Any other kind, or any exception, triggers
  rollback.
- Exception escaping the handler is caught, logged with the span
  marked failed, and returned as
  `Result(kind="internal-error", error_code="handler-exception", ...)`.

### 2.5 Capability enforcement (R4)

- Each `Intent.capability` is a string of the form
  `<domain>.<action>.<write-or-read>`.
- The caller's `ApiKey` has an array `scopes: [string]`. Grant check
  is exact match (Sprint 13 does not introduce wildcards).
- Scope check runs BEFORE validation (cheaper, fail faster).
- Result `capability-denied` carries `error_code =
  "scope-not-granted"` and `error_message` naming the missing scope.
- New Cypher constraint: `ApiKey.scopes` is stored as a list, indexed
  via `CREATE INDEX apikey_scope FOR (k:ApiKey) ON (k.scopes)`.

### 2.6 Observability integration (R5)

Reuse Sprint-12 surface:

- **Tracing:** one root span per `execute()` with attributes
  `intent.type`, `intent.idempotency_key`, `principal.id`,
  `capability`. Four child spans: `intent.validate`,
  `intent.capability_check`, `intent.precondition`, `intent.mutate`.
- **Metrics:**
  - `hassaleh_intent_total{intent_type, result, capability_granted}` — counter
  - `hassaleh_intent_duration_seconds{intent_type}` — histogram
- **Logs:** one structlog line per intent with `intent_type`, `trace_id`,
  `duration_ms`, `result`, `error_code?`, `principal_id`.

No new dashboards in Sprint 13 — a follow-up dashboard PR
`ha-intent-v2` lands in a v1.0.1-style patch after merge.

### 2.7 Trade-offs noted for the reviewer

- **Why a single dispatch function, not per-intent endpoints?** One
  function = one span shape = one metric shape. Per-intent endpoints
  would need a new span + metric definition every time a new Intent
  lands, and the observability surface would drift.
- **Why Cypher preconditions instead of Python-level checks?** A
  Python check outside the transaction is subject to time-of-check-
  to-time-of-use (TOCTOU) drift. Inside the Cypher transaction,
  MVCC guarantees the precondition result is consistent with the
  mutation.
- **Why no event journal?** Adding event-sourcing in Sprint 13 would
  double the surface to review. The graph mutation is the record;
  replay is a legitimate v2+ enhancement.

---

## 3. Interface Specification

Five Intent types for Sprint 13. Each specification includes:
type string, capability, payload shape, precondition, mutation,
result data shape, and the Cypher sketch for the reviewer.

### 3.1 `market.add-analyst-attempt`

**Capability:** `market.analyst.write`

**Payload:**
```
{
  "date": "YYYY-MM-DD",
  "segment": "us|eu|asia|macro|sentiment",
  "analyst_by": "worker-id"
}
```

**Precondition (Cypher, runs inside tx):**
```cypher
MATCH (r:MarketRun {date: $date})-[:HAS_SEGMENT]->(s:Segment {name: $segment})
WITH s
OPTIONAL MATCH (s)-[:HAS_ATTEMPT]->(a:Attempt {n: s.currentCycle})
WITH s, a
WHERE a IS NULL OR a.verdict IS NULL
RETURN s.id AS segment_id, coalesce(a, {}) AS existing
```

Empty result set → precondition fails with
`error_code = "terminal-verdict-exists"`.

**Mutation (Cypher, same tx):**
```cypher
// if existing pending attempt → update in place
// else → append new Attempt node with n = s.currentCycle
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

(The APOC call disappears once we can use Cypher 5.0 conditional
write; noted as a polish item, non-blocking.)

**Result data:** `{"attempt_n": int, "analyst_by": string}`.

### 3.2 `market.set-verdict`

**Capability:** `market.reviewer.write`

**Payload:**
```
{
  "date": "YYYY-MM-DD",
  "segment": "...",
  "cycle": int,
  "verdict": "ok|reject",
  "review_by": "worker-id",
  "feedback": "string|null",
  "word_count": int|null
}
```

**Precondition:** an attempt for `(segment, cycle)` exists and has
`verdict IS NULL`. Fails with `error_code = "verdict-already-set"`
or `"attempt-not-found"`.

**Mutation:** write verdict; adjust segment status:
- `ok` → `status = "ok"` (currentCycle stays)
- `reject` AND `cycle >= 3` → `status = "final-reject"` (cycle stays)
- `reject` AND `cycle < 3` → `status = "retry"`, `currentCycle = cycle + 1`

Re-evaluate run-level `phase1Complete` (all segments in a terminal state).

**Result data:** `{"segment_status": string, "phase1_complete": bool}`.

### 3.3 `market.register-writer-attempt`

**Capability:** `market.writer.write`

**Payload:** `{"date": "...", "started_by": "worker-id", "feedback": "string|null"}`

**Precondition:** either (a) no pending writer attempt exists, or
(b) the latest writer attempt is non-pending. Duplicate
register-pending calls are an error, not a no-op.

**Mutation:** append a new writer attempt with
`publisherVerdict = "pending"`.

**Result data:** `{"attempt_n": int}`.

### 3.4 `market.update-writer-attempt`

**Capability:** `market.writer.write` or `market.publisher.write`
(see §5 for the scope split)

**Payload:** `{"date": "...", "verdict": "ok|reject", "feedback": "string|null"}`

**Precondition:** the latest writer attempt has
`publisherVerdict = "pending"`.

**Mutation:** update the latest pending writer attempt to the given
verdict, set `finishedAt = $now`.

**Result data:** `{"attempt_n": int, "verdict": string}`.

### 3.5 `market.set-published`

**Capability:** `market.publisher.write`

**Payload:**
```
{
  "date": "...",
  "posts": [{"url": str, "segment": str, "molt": str, "author": str}],
  "verdict": "ok|manual-corrections",
  "manual_corrections": "string|null"
}
```

**Precondition:** the run's `publisher.finalVerdict` is null.

**Mutation:** write all post nodes, set finalVerdict, set publishedAt.

**Result data:** `{"post_count": int, "published_at": iso}`.

---

## 4. Review Phases

Three artefacts × up to three review rounds × two reviewer perspectives.

| Artefact | Primary reviewer | Secondary reviewer (clarity) | Workers | Verdicts |
|----------|------------------|------------------------------|---------|----------|
| Requirements doc (§1) | Ingo (product) | gemini-reviewer | human + worker-gemini | approve / change-req |
| Architecture doc (§2) | Inanna (tech + security) | gemini-reviewer | worker-opus + worker-gemini | clean / change-req |
| Interface spec (§3) | Inanna (API + precondition correctness) | gemini-reviewer | worker-opus + worker-gemini | clean / change-req |

**Anti-hallucination guard (new for Sprint 13):** the review-Dispatch
prompt to gemini-reviewer includes an explicit "verify file paths and
line numbers you cite by reading them back" clause. We will expect
reviewers to quote Cypher from the doc they reviewed, not from
memory. The 2026-04-21 halluzinations in the market-pipeline context
are the reason.

- **Round 1:** each reviewer produces a verdict doc:
  `docs/sprint-13-plan-review-1-<reviewer>.md`.
- **Revision:** authors address all CR items in-place on the artefact;
  diff + a short "how each CR was addressed" block goes into the
  commit message.
- **Round 2:** reviewers re-verify only the CR items; may downgrade
  remaining issues to non-blocking.
- **Round 3** (cap): Ingo arbitrates.

v0 of this plan has NO incorporated CRs yet; v1 will land after
Round-1.

---

## 5. Implementation Tracks (post-approval, parallel)

| Track | Scope | Worker | Deliverables |
|-------|-------|--------|--------------|
| A — Core runtime | `HassalehRuntime.execute()`, Intent + Result dataclasses, handler registry decorator, transaction scope, exception mapping | worker-codex | `src/hassaleh/runtime/core.py`, `src/hassaleh/runtime/types.py`, unit tests, docs/sprint-13-track-a-implement.md |
| B — Intent types (5 handlers) | The five handlers of §3.1–§3.5 with preconditions as Cypher, mutations as Cypher, type-level tests | worker-codex | `src/hassaleh/runtime/intents/market.py`, `tests/test_runtime_market_intents.py`, docs |
| C — Capability enforcement | Scope storage on ApiKey, scope-check integration in runtime, CapabilityDenied result | worker-codex | `src/hassaleh/runtime/capabilities.py`, `schema.cypher` addition, tests |
| D — Observability wiring | OTel spans, prom metrics, structlog line emission per intent execution | worker-gemini | `src/hassaleh/runtime/observability.py`, integration with Sprint-12 surface, tests |
| E — Market Pipeline migration | Refactor `scripts/market_state.py` to emit Intents instead of writing the JSON blackboard directly. State becomes a Neo4j view, the blackboard becomes legacy. | worker-gemini | Edits to `scripts/market_state.py`, new `scripts/market_state_legacy_export.py` (for rollback), migration tests, docs |
| F — Integration tests | End-to-end: an Intent from a Python caller, through validation + capability + precondition + mutation, producing span + metric + log in the Sprint-12 observability stack. Plus: worker-crash simulation (kill between precondition and mutation) asserts no state change. | worker-gemini | `tests/test_runtime_e2e.py`, `tests/test_runtime_crash_recovery.py` |

Merge order:

1. **A (Core runtime)** — first, because every other track depends on
   `Intent`, `Result`, `register_handler`.
2. **C (Capabilities)** — second, because B's handlers carry capability
   declarations that must parse against a live capability module.
3. **B (Intent types)** — third, builds on A+C.
4. **D (Observability)** — fourth, wraps A+B+C and emits the spans/
   metrics/logs.
5. **E (Market migration)** — fifth, uses all of A+B+C+D and proves
   the real value.
6. **F (Integration tests)** — last, asserts across all tracks
   including the crash-recovery scenario.

Tracks A, D can be authored in parallel (different files, no shared
surface). Track C can be authored as soon as A's Intent/Result
dataclasses are frozen. B and E serialize on C.

Dione (worker-opus) coordinates integration conflicts and runs the
final merge.

---

## 6. Resource allocation

### Workers

| Worker | Backing model | Sprint-13 role |
|--------|---------------|----------------|
| worker-opus | claude-opus-4-7 | Dione (orchestration, integration, reviews) + Inanna (security + API reviews, separate session) |
| worker-codex | gpt-5.4 | Implementation author for Tracks A, B, C |
| worker-gemini | gemini-3.1-pro-preview | Implementation author for Tracks D, E, F. **Note**: 2026-04-21 showed a hallucination pattern in worker-gemini on dispatch-retry tasks; Sprint 13 mitigates this by NOT asking gemini to execute retries of prior worker outputs (the migration tasks are greenfield). If the issue recurs, the orchestrator falls back to worker-codex. |

### Personas

| Persona | Worker | Responsibilities |
|---------|--------|------------------|
| **Dione** | worker-opus | Sprint orchestration, architecture authoring, integration-conflict resolution, final merge |
| **Inanna** | worker-opus (distinct session) | Independent security + API review per track + final sign-off |
| **gemini-author** | worker-gemini | Tracks D/E/F implementation |
| **codex-author** | worker-codex | Tracks A/B/C implementation |
| **gemini-reviewer** | worker-gemini (distinct session from gemini-author) | Clarity/completeness/internal-consistency review. See §4 anti-hallucination guard. |

### Sprint-13 cron + state file

- `~/projects/hassaleh/sprint-13-state.json` — blackboard with per-track
  phase and verdicts.
- `hassaleh-sprint-orchestrator` cron (existing, id
  `5d854f3b-8dea-4e5c-a680-9ba5eee1c64b`) gets repurposed to target
  Sprint 13 once the plan is approved. It already runs every 2 h.

### Timeline (working days)

| Day | Phase | Parallelism |
|-----|-------|-------------|
| 0 | **Plan approval** (this document) | Ingo + Inanna |
| 1 | Spec authoring (if CRs require split) | 3 authors in parallel |
| 2 morning | Review round 1 | reviewers in parallel |
| 2 afternoon | CR integration | authors |
| 3 morning | Review round 2 / sign-off | reviewers |
| 3 afternoon | Implementation tracks A + D start | 2 in parallel |
| 4 | Tracks B + C (serialized on A), D continues | 3 in parallel |
| 5 morning | Track E migration | gemini-author |
| 5 afternoon | Track F integration tests, final review, merge | worker-opus |

Total calendar budget: **~5 working days** after this plan is approved.

---

## 7. Acceptance & "Done" criteria

A green build AND:

- **Success criteria S1–S4 from §1 all pass.**
- **All five Intent types pass their per-intent tests** including
  precondition-fail cases.
- **Capability-denied path is exercised** by at least one test per
  Intent (no-scope, wrong-scope, revoked-key).
- **Crash-recovery test green**: a process kill between precondition
  and mutation leaves the graph exactly as it was before.
- **One production market pipeline daily run** uses the Runtime
  Operator end-to-end without rollback (this lands on Day 6 post-merge
  but is observable via the Sprint-12 dashboards).
- **Sprint-13 plan v1 or higher is FROZEN** with both Round-2
  verdicts CLEAN.

---

## 8. Cross-cutting concerns & previously-forgotten items

- **Backwards compat during migration:** `scripts/market_state.py`
  will support BOTH old file-based write AND new Intent dispatch,
  gated by env `HASSALEH_RUNTIME_OPERATOR=1`, for one week after
  merge. Default off in the first 24 h post-merge to let us fall back
  fast if a regression hits. Flipped to default-on after one clean
  daily cycle.
- **Neo4j schema impact:** R4 requires an index on `ApiKey.scopes`.
  Added to `schema.cypher` by Track C. No backfill required (fresh
  field).
- **Sprint-12 advisories consumed in-passing:** the "v1.0.1 patch
  candidates" from Track D of Sprint 12 (alert-rules-in-prom vs
  Grafana unified, panel UIDs) are addressed in a one-commit patch
  on Day 0 before Sprint 13 formally starts, to close Sprint 12
  without making Sprint 13 carry them.
- **Sweeper interaction:** the market-retry-sweeper (`scripts/
  market_retry_sweeper.py`) continues to exist. With the Runtime
  Operator, its "retry not picked up" path should rarely fire (no
  more race). The sweeper's "never-started" path remains useful
  (orthogonal concern: dispatch-time silent failures).
- **Rollback:** a one-line revert of the env flag in
  `scripts/market_state.py` fall-back restores pre-Sprint-13 behavior.
  No data migration to undo because the graph is additive.

### Things that are **explicitly out of v1** and parked for v2+

- Rabt Intent types (contact merge, outbox, draft edit).
- Nexus Intent types (observation ingest).
- Generic public REST/gRPC API for Intent submission.
- Cross-graph atomic transactions.
- Intent journaling / event-sourcing.
- Saga / compensating workflows.
- Capability-scope wildcards (`market.*.write`).
- Per-Intent rate limits (the Sprint-12 auth rate-limit already
  caps per-key, which is sufficient for v1).

---

## 9. Meta — This plan under the agentic review workflow

### 9.1 Review invocation

Round-1 dispatches are fired by Dione after this v0 is committed:

- Inanna review (worker-opus, distinct session): focuses on
  security + API correctness + precondition semantics.
- gemini-reviewer (worker-gemini): focuses on clarity, internal
  consistency, and explicit identification of any place where the
  plan assumes a capability the reader may not have (see Sprint 12
  CR-8). **Anti-hallucination guard:** reviewer must quote file
  paths and Cypher verbatim from this doc, not paraphrase; any
  paraphrase must be flagged as such.

### 9.2 Review success criteria for *this document*

- Every R1–R6 has a stated "so what?" (already done in §1).
- Every Intent type in §3 has explicit precondition Cypher (not
  prose).
- Every success criterion S1–S4 is verifiable by a test whose name
  is plausibly derivable from this plan.
- Tracks A–F in §5 each have clearly separable deliverables (no
  track's output is another track's internal module).
- The crash-recovery test is named and scoped (§7 bullet 4).
- §8 cross-cutting concerns are stated, not hidden.

### 9.3 Post-review

Once Round-2 yields CLEAN from both reviewers:

- This plan is FROZEN as v1.0 (or v1.0.1 if minor polish).
- `sprint-13-state.json` is created.
- The orchestrator cron is repurposed.
- Track A + D dispatches fire.

### 9.4 Explicit non-reviewability items

- **The decision to do Sprint 13 at all.** Ingo approved Option B in
  conversation on 2026-04-21 evening. Reviewers do not arbitrate
  scope selection — they arbitrate execution quality.
- **The choice of five initial Intent types.** These are grounded in
  the actual Market Pipeline surface; they are not a negotiation
  space. Reviewers may flag that a sixth is missing but should not
  re-litigate inclusion of the first five.

---

## Change log

- **2026-04-21 v0 (this file)**: Initial draft. Ingo selected
  Option B (Runtime Operator) over Option A (Observability patch) and
  Option C (Capability model) in the pre-sprint proposal. Sprint 13
  is scoped to cover: runtime infrastructure, five Intent types
  specifically for the Market Pipeline state-ops (derived from the
  operations currently in `scripts/market_state.py`), capability
  enforcement at the runtime boundary, Sprint-12 observability
  wiring for every Intent, and a full migration of the Market
  Pipeline state to go through the runtime. Out-of-scope items
  listed in §1 Non-Requirements and §8 Explicit v2+ parking.

### What will change in v1 (post Round-1)

v1 will address all Round-1 CRs from Inanna + gemini-reviewer with
a per-CR change summary block analogous to the Sprint-12 v1 commit
message format.
