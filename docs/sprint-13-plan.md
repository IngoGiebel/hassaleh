# Sprint 13 — Runtime Operator

**Author:** Dione
**Status:** **FROZEN as v1.1 — both Round-2 verdicts CLEAN** (Inanna Round-2b 2026-04-24, gemini Round-2 2026-04-23).
**Created:** 2026-04-21 (v0); **Revised:** 2026-04-23 (v1), 2026-04-24 (v1.1 → FROZEN)
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
| **R5** | **Observability native.** Every Intent execution emits one OTel span (children: `intent.validate`, `intent.capability_check`, `intent.precondition`, `intent.mutate` — see §2.6 authoritative list), one log line, one `hassaleh_intent_total{intent_type, result}` counter tick, and one `hassaleh_intent_duration_seconds` histogram sample — reusing the Sprint-12 surface. | Intent-level latency, error rate, and result-bucket distribution become first-class dashboard panels on day 1. The sweeper's "segment stuck" gets a metric-level twin (no stuck Intent == no stuck segment). |
| **R6** | **Six initial Intent types covering the Market Pipeline.** Delivered with handlers, preconditions, tests, and a migration of the current `scripts/market_state.py` call-sites: `AddAnalystAttempt`, `SetVerdict`, `RegisterWriterAttempt`, `UpdateWriterAttempt` (writer role), `UpdateWriterAttempt` (publisher role — split off in v1 per CR-6), `SetPublished`. After Sprint 13, `scripts/market_state.py` is a thin wrapper that builds Intents and calls `HassalehRuntime.execute()`. | The class of failures that broke the market pipeline on 2026-04-19, 2026-04-20, and 2026-04-21 is gone. We have a measured proof: the sweeper-dispatched recovery on any future date finds the state and disk in perfect agreement, because they are no longer separate stores. |

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
- **S2:** In-sprint test `test_add_analyst_attempt_writes_md_and_attempt_atomically`
  simulates a worker-crash between artefact-write and state-write and
  asserts the graph either (a) contains both or (b) contains neither —
  never the drifted-pair state that the sweeper used to find in the
  pre-runtime era. This is the test-level analogue of the production
  invariant; the production observation of the same property is moved
  to §7 Post-merge acceptance.
- **S3:** Every Intent execution appears in Tempo with a trace
  containing the four child spans listed in §2.6 (authoritative);
  appears in Prometheus as a `hassaleh_intent_total{intent_type, result}`
  counter tick; appears in structlog as one JSON line with
  `intent_type`, `trace_id`, `duration_ms`, and (on error) `error_code`.
- **S4:** A Capability-denied call (wrong scope on API key) produces
  a `CapabilityDenied` result without a Neo4j transaction being
  opened; measured by a test that asserts `hassaleh_intent_total{
  result="capability-denied"}` ticks without a corresponding
  `hassaleh_cypher_query_total` tick. *(Note on casing: result label
  values use hyphens throughout, matching the `error_code` enum
  values in §2.2. See §2.6.)*

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
    type: str                    # discriminator, e.g. "market.add-analyst-attempt"
    payload: Mapping[str, Any]   # per-type typed data, validated

@dataclass(frozen=True)
class Result:
    kind: Literal["ok", "precondition-failed", "capability-denied",
                  "validation-error", "internal-error"]
    data: Mapping[str, Any] | None
    error_code: str | None       # machine-readable, e.g. "terminal-verdict-exists"
    error_message: str | None
```

**Changes from v0 (per Round-1 CRs):**

- **Removed `capability: str` from Intent** (I-CR-3). The caller declared
  its own required capability, which opened a "declare-what-you-have"
  authorization-bypass path. In v1, the runtime looks the required
  capability up in the handler registry at dispatch time — see §2.3.
  This closes the bypass: a caller cannot silently escalate by
  declaring a weaker scope than the handler actually requires.
- **Removed `idempotency_key: str | None` from Intent v1** (G-CR-5). The
  semantic was declared in §2.2 but unspecified per-type (scope, TTL,
  storage, retry-return-logic). Rather than bake in a half-specified
  guarantee, v1 defers Intent-level idempotency to a named v2 sprint.
  The Market Pipeline's own retry logic (Sprint 13's sweeper + the
  precondition `terminal-verdict-exists` guard) already covers the
  practical cases.
- **Result `error_code` values are kebab-case** (hyphens), matching the
  `Result.kind` literal values and used verbatim as the `result` label
  value on Prometheus metrics. Translation between snake_case and
  kebab-case is never performed — one shape, one enum.

The discriminator + payload shape gives us:

- compile-time type checking in Kotlin/Python SDKs later (Sprint N+)
- a single `Result.kind` enum that drives the `result` label on the
  `hassaleh_intent_total` Prometheus counter
- one unambiguous observable per intent type

### 2.3 Handler registry

A single global registry maps `intent.type` → (handler, required
capability). Handlers are registered at module import time via a
decorator; the required capability is declared at registration, NOT
on the Intent itself (see §2.2 / I-CR-3):

```python
@register_handler(
    "market.add-analyst-attempt",
    requires_capability="market.analyst.write",
)
def handle_add_analyst_attempt(intent, tx, ctx): ...
```

The registry is the only way a handler gets called. Unknown
`intent.type` produces a `validation-error` with `error_code =
"unknown-intent-type"`. The runtime reads `requires_capability` from
the registry during capability check (§2.5), so the caller has no say
in what scope is required.

### 2.4 Transaction scope

- The runtime opens the transaction AFTER validation + capability
  check. This means:
  - malformed inputs cost nothing in Neo4j terms
  - capability-denied calls cost nothing in Neo4j terms
- **Precondition Cypher AND mutation Cypher run inside the SAME
  transaction** (per R2 and the MVCC argument in §2.7). A
  `precondition-failed` result rolls the transaction back without
  committing any write. This is the load-bearing invariant that
  makes the sweeper-hallucination class of bugs structurally
  impossible: a concurrent writer cannot slip between the
  precondition and the mutation.
- The transaction is committed IFF the handler returns a
  `kind == "ok"` result. Any other kind (including
  `precondition-failed`), or any exception, triggers rollback.
- Exception escaping the handler is caught, logged with the span
  marked failed, and returned as
  `Result(kind="internal-error", error_code="handler-exception", ...)`.
- **Runtime-bound values:** `$now` in handler Cypher is the runtime's
  monotonic UTC timestamp taken at transaction-open time and bound
  via `ctx.now` on the handler context. Handlers MUST NOT invoke
  `datetime.now()` directly — the runtime owns time for determinism
  and testability.

### 2.5 Capability enforcement (R4)

**Canonical execution order** (fixed in v1 per I-CR-2 + G-CR-2):

```
validate → capability_check → precondition → mutate
```

This order is authoritative in the plan; §2.1 diagram, §2.6 span list,
R5, and S3 all refer to this order without contradiction.

- Each registered handler declares `requires_capability` at
  registration time (§2.3). The runtime reads this value during
  capability check; the Intent itself never carries a capability
  field (I-CR-3).
- The caller's `ApiKey` has an array `scopes: [string]`. Grant check
  is **exact match** (Sprint 13 does not introduce wildcards or
  `any_of` semantics; see §3.4/§3.5 for the concrete pattern).
- Validation runs FIRST (so a malformed payload fails before we
  bother looking up scopes). Capability check runs SECOND, BEFORE
  precondition (so an unauthorized call costs zero Cypher).
- Result `capability-denied` carries `error_code =
  "scope-not-granted"` and `error_message` naming the missing scope.
- New Cypher constraint: `ApiKey.scopes` is stored as a list, indexed
  via `CREATE INDEX apikey_scope FOR (k:ApiKey) ON (k.scopes)`.

### 2.6 Observability integration (R5)

**This section is authoritative for spans, metric labels, and log
field names.** R5, S3, and S4 all reference §2.6 rather than
restate. Any apparent contradiction with those places is a bug to
fix here, not there.

Reuse Sprint-12 surface:

- **Tracing:** one root span per `execute()` (`hassaleh.intent.execute`)
  with attributes `intent.type`, `principal.id`. **Four child spans,
  in execution order**:
  1. `intent.validate`
  2. `intent.capability_check`
  3. `intent.precondition`
  4. `intent.mutate`
  (No `result` child span — the result is the return value of
  `execute()`, captured by the root span's status/attributes, not
  a separate child.)
- **Metrics:**
  - `hassaleh_intent_total{intent_type, result}` — counter.
    **Two labels only** (v1 per G-CR-3). `capability_granted` was
    removed because it duplicated information already in `result`
    (a successful execution implies capability was granted; a
    `result="capability-denied"` tick implies it wasn't).
  - `hassaleh_intent_duration_seconds{intent_type}` — histogram.
- **Logs:** one structlog line per intent with the following fields:
  `intent_type`, `trace_id`, `duration_ms`, `result`,
  `error_code?` (present only when `result != "ok"`),
  `principal_id`. **Field naming:** `error_code` is the canonical
  name everywhere — in `Result`, in §2.6 logs, in S3. The v0 string
  `error_type` does not exist in v1 (G-CR-7).
- **Label value casing:** all `result` label values use kebab-case
  hyphens (`"ok"`, `"precondition-failed"`, `"capability-denied"`,
  `"validation-error"`, `"internal-error"`) — no underscores, no
  runtime translation layer (G-CR-4).

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

**Capability (declared at registration, not on Intent):** `market.analyst.write`

**Payload:**
```
{
  "date": "YYYY-MM-DD",
  "segment": "us|eu|asia|macro|sentiment",
  "analyst_by": "worker-id"
}
```

**Precondition (Cypher, runs inside tx):** produces one of four
outcomes, each with a distinct `error_code` (v1 split per I-CR-5 +
I-CR-8).

```cypher
// Step 1: run exists?
MATCH (r:MarketRun {date: $date})
WITH r
OPTIONAL MATCH (r)-[:HAS_SEGMENT]->(s:Segment {name: $segment})
WITH r, s
OPTIONAL MATCH (s)-[:HAS_ATTEMPT]->(a:Attempt {n: s.currentCycle})
RETURN
  r IS NULL                              AS run_missing,
  r IS NOT NULL AND s IS NULL            AS segment_missing,
  a IS NOT NULL AND a.verdict IS NOT NULL AS terminal_exists,
  s.id                                   AS segment_id,
  s.currentCycle                         AS current_cycle,
  a                                      AS existing_pending_attempt
```

Mapping precondition result to `error_code`:

| Condition | `error_code` |
|-----------|--------------|
| `run_missing = true` | `"run-not-found"` |
| `segment_missing = true` | `"segment-not-found"` |
| `terminal_exists = true` | `"terminal-verdict-exists"` |
| otherwise | (none — proceed to mutation with `segment_id`, `current_cycle`) |

All three error codes are declared in §2.2's `Result.error_code` enum
for this Intent type. The previous v0 collapse into a single
`"terminal-verdict-exists"` is fixed.

**Mutation (Cypher, same tx):**
```cypher
// Fixed in v1 (I-CR-4): APOC inner-query parameter scope, schema
// uniqueness constraint now declared in §8, $now sourced from ctx.now
// per §2.4.  Fixed in v1.1 (I-CR-4.2 round-2): CREATE clause and
// SET clause both persist `segment_id` on the Attempt node, so the
// `(a.segment_id, a.n)` uniqueness constraint in §8 actually fires
// (Neo4j 5 treats null-tuples as "no constraint applies").
MATCH (s:Segment {id: $segment_id})
OPTIONAL MATCH (s)-[:HAS_ATTEMPT]->(a:Attempt {n: $current_cycle})
CALL apoc.do.when(
  a IS NOT NULL AND a.verdict IS NULL,
  'SET a.analystBy = $analyst_by,
       a.analystAt = $now,
       a.segment_id = coalesce(a.segment_id, $segment_id)
   RETURN a AS attempt',
  'CREATE (s)-[:HAS_ATTEMPT]->(attempt:Attempt {
         segment_id: $segment_id,
         n: $current_cycle,
         analystBy: $analyst_by, analystAt: $now,
         wordCount: null, reviewBy: null, reviewAt: null,
         verdict: null, reviewFeedback: null}) RETURN attempt',
  {a: a, s: s, segment_id: $segment_id, current_cycle: $current_cycle,
   analyst_by: $analyst_by, now: $now}
) YIELD value
SET s.status = "analyzing"
RETURN value.attempt AS attempt
```

Notes:

- The APOC inner queries now receive all referenced bindings explicitly
  in the params map. `$s`, `$a`, `$current_cycle` are no longer
  unbound references (v0 bug fixed per I-CR-4.1).
- `$now` is bound from `ctx.now` at transaction-open (§2.4); the
  handler never calls `datetime.now()` directly.
- **`segment_id` is written to every Attempt node** in both branches
  (CREATE unconditionally; SET via `coalesce` so a pre-existing Attempt
  without the field gets a lazy backfill on next touch, without
  clobbering any value already there). This is what makes the §8
  uniqueness constraint on `(a.segment_id, a.n)` actually bite —
  without `segment_id`, Neo4j 5's null-tuple rule silently disables
  the constraint and we'd still have the concurrent-writer race the
  constraint is meant to prevent (v1.1 fix per I-CR-4.2 round-2).
- The schema-level uniqueness constraint on `(Segment.id, Attempt.n)`
  is added to `schema.cypher` by Track C (§8). Without it plus the
  property write, two concurrent writers of cycle N could both create
  Attempt nodes; the constraint is what makes the precondition-to-
  mutation hand-off race-free even across connection pools.
- The APOC call disappears once we can use Cypher 5.0 conditional
  write; noted as a polish item for a follow-up patch, non-blocking.

**Result data:** `{"attempt_n": int, "analyst_by": string}`.

### 3.2 `market.set-verdict`

**Capability (declared at registration):** `market.reviewer.write`

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

**Precondition (Cypher, runs inside tx):** an attempt for
`(segment, cycle)` must exist and must have `verdict IS NULL`.

```cypher
MATCH (r:MarketRun {date: $date})-[:HAS_SEGMENT]->(s:Segment {name: $segment})
WITH r, s
OPTIONAL MATCH (s)-[:HAS_ATTEMPT]->(a:Attempt {n: $cycle})
RETURN
  r IS NULL                              AS run_missing,
  r IS NOT NULL AND s IS NULL            AS segment_missing,
  a IS NULL                              AS attempt_missing,
  a IS NOT NULL AND a.verdict IS NOT NULL AS verdict_already_set,
  s.id                                   AS segment_id
```

Mapping:

| Condition | `error_code` |
|-----------|--------------|
| `run_missing = true` | `"run-not-found"` |
| `segment_missing = true` | `"segment-not-found"` |
| `attempt_missing = true` | `"attempt-not-found"` |
| `verdict_already_set = true` | `"verdict-already-set"` |

**Mutation:** write verdict; adjust segment status:
- `ok` → `status = "ok"` (currentCycle stays)
- `reject` AND `cycle >= 3` → `status = "final-reject"` (cycle stays)
- `reject` AND `cycle < 3` → `status = "retry"`, `currentCycle = cycle + 1`

Re-evaluate run-level `phase1Complete` (all segments in a terminal state).

**Result data:** `{"segment_status": string, "phase1_complete": bool}`.

### 3.3 `market.register-writer-attempt`

**Capability (declared at registration):** `market.writer.write`

**Payload:** `{"date": "...", "started_by": "worker-id", "feedback": "string|null"}`

**Precondition (Cypher, runs inside tx):** either no writer attempt
exists yet, or the most-recent one (by `.n`) is non-pending.
Duplicate register-pending calls are an error, not a no-op.

```cypher
MATCH (r:MarketRun {date: $date})
WITH r
OPTIONAL MATCH (r)-[:HAS_WRITER_ATTEMPT]->(wa:WriterAttempt)
WITH r, wa ORDER BY wa.n DESC LIMIT 1
RETURN
  r IS NULL                                AS run_missing,
  r IS NOT NULL AND wa IS NOT NULL
    AND wa.publisherVerdict = "pending"    AS pending_writer_exists,
  coalesce(wa.n, 0)                        AS last_n
```

The `ORDER BY wa.n DESC LIMIT 1` subquery picks the highest-`n`
writer attempt — the unambiguous "latest" definition (resolves
I-CR-1.3's "ordering ambiguity" flag).

Mapping:

| Condition | `error_code` |
|-----------|--------------|
| `run_missing = true` | `"run-not-found"` |
| `pending_writer_exists = true` | `"pending-writer-attempt-exists"` |

**Mutation:** append a new writer attempt with `n = last_n + 1` and
`publisherVerdict = "pending"`, `startedBy = $started_by`,
`startedAt = $now`.

**Result data:** `{"attempt_n": int}`.

### 3.4 `market.update-writer-attempt` (writer role)

**Capability (declared at registration):** `market.writer.write`

Split from v0 (single `market.update-writer-attempt`) per I-CR-6 /
G-CR-3 option (a). Two Intent types with single-capability each is
strictly cleaner than one Intent with `any_of` semantics that the
exact-match rule (§2.5) doesn't support.

**Use case:** the writer submits its own writer-attempt result —
typically `verdict = "ok"` after the writer has composed the
Moltbook post, or `verdict = "reject"` if the writer itself found a
blocking issue (rare).

**Payload:** `{"date": "...", "verdict": "ok|reject", "feedback": "string|null"}`

**Precondition (Cypher, runs inside tx):** the latest writer attempt
(highest `.n`) has `publisherVerdict = "pending"`.

```cypher
MATCH (r:MarketRun {date: $date})
WITH r
OPTIONAL MATCH (r)-[:HAS_WRITER_ATTEMPT]->(wa:WriterAttempt)
WITH r, wa ORDER BY wa.n DESC LIMIT 1
RETURN
  r IS NULL                           AS run_missing,
  wa IS NULL                          AS no_writer_attempt,
  wa IS NOT NULL
    AND wa.publisherVerdict <> "pending" AS not_pending,
  wa.n                                AS attempt_n
```

Mapping:

| Condition | `error_code` |
|-----------|--------------|
| `run_missing = true` | `"run-not-found"` |
| `no_writer_attempt = true` | `"no-writer-attempt"` |
| `not_pending = true` | `"writer-attempt-not-pending"` |

**Mutation:** update the matched writer attempt with
`publisherVerdict = $verdict`, `feedback = $feedback`,
`finishedAt = $now`, `finishedBy = $caller_principal` (captured from
`ctx.principal_id`).

**Result data:** `{"attempt_n": int, "verdict": string}`.

### 3.5 `market.update-writer-attempt-by-publisher` (publisher role)

**Capability (declared at registration):** `market.publisher.write`

Second half of the v0 `§3.4` split. Semantically identical mutation
shape to §3.4, but called by the publisher principal with a different
scope. A publisher may update a pending writer-attempt to
`ok` / `reject` when the writer has not yet self-finalised.

**Payload:** same shape as §3.4.

**Precondition:** same Cypher as §3.4, same mapping. (Shared helper
`check_writer_attempt_pending()` in the implementation; Track B
deliverable.)

**Mutation:** same mutation Cypher as §3.4. `finishedBy = $caller_principal`
records which principal finalised the attempt, which the dashboards
use to split writer-self-finalise from publisher-finalise latency.

**Result data:** `{"attempt_n": int, "verdict": string}`.

### 3.6 `market.set-published`

**Capability (declared at registration):** `market.publisher.write`

**Payload:**
```
{
  "date": "...",
  "posts": [{"url": str, "segment": str, "molt": str, "author": str}],
  "verdict": "ok|manual-corrections",
  "manual_corrections": "string|null"
}
```

**Precondition (Cypher, runs inside tx):** the run exists and has
no prior `publisher.finalVerdict`.

```cypher
MATCH (r:MarketRun {date: $date})
OPTIONAL MATCH (r)-[:HAS_PUBLISHER_STATE]->(p:PublisherState)
RETURN
  r IS NULL                                   AS run_missing,
  p IS NOT NULL AND p.finalVerdict IS NOT NULL AS already_published,
  r.id                                        AS run_id
```

Mapping:

| Condition | `error_code` |
|-----------|--------------|
| `run_missing = true` | `"run-not-found"` |
| `already_published = true` | `"already-published"` |

**Mutation:** within the same tx:
1. For each entry in `$posts`, `CREATE (:Post {url, segment, molt, author, publishedAt: $now})-[:FOR_RUN]->(r)`.
2. `MERGE (r)-[:HAS_PUBLISHER_STATE]->(p:PublisherState)
   SET p.finalVerdict = $verdict,
       p.manualCorrections = $manual_corrections,
       p.publishedAt = $now,
       p.publishedBy = $caller_principal`

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
| B — Intent types (6 handlers) | The six handlers of §3.1–§3.6 with preconditions as Cypher, mutations as Cypher, type-level tests. §3.4 and §3.5 share a `check_writer_attempt_pending()` helper. | worker-codex | `src/hassaleh/runtime/intents/market.py`, `tests/test_runtime_market_intents.py`, docs |
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

Updated 2026-04-23 to reflect actual progress. v0 was authored
2026-04-21. Round-1 reviews landed 2026-04-21. v1 (this revision)
integrates Round-1 CRs. Current position: **Day 2 afternoon / Day 3
morning of the v0 timeline**, awaiting Round-2 sign-off.

| Day | Date | Phase | Parallelism | Status |
|-----|------|-------|-------------|--------|
| 0 | 2026-04-21 | v0 plan committed | Ingo + Dione | ✅ done |
| 1 | 2026-04-21 | Round-1 reviews (Inanna + gemini-reviewer) | reviewers parallel | ✅ done (14 CRs) |
| 2 | 2026-04-22 → 2026-04-23 | v1 integration of all 11 consolidated CRs | Dione | ✅ done (this commit) |
| 3 morning | 2026-04-24 | Round-2 review dispatch + verdicts | Inanna + gemini-reviewer | ⬜ next — see §6.5 |
| 3 afternoon | 2026-04-24 | v1 FROZEN as v1.0 (or minor v1.1 polish), Track A + D kickoff | Dione → 2 parallel dispatches | ⬜ pending Round-2 CLEAN |
| 4 | 2026-04-27 (Mon) | Tracks B + C (serialized on A's first 30%), D continues | 3 parallel | ⬜ |
| 5 morning | 2026-04-28 | Track E migration | gemini-author | ⬜ |
| 5 afternoon | 2026-04-28 | Track F integration tests, final review, merge | worker-opus (Dione) | ⬜ |
| 6 | 2026-04-29 | Post-merge acceptance (§7.2); first production run observed | Dione monitoring | ⬜ |

Total calendar budget: **~5 working days** after v1 FROZEN. Earliest
merge: 2026-04-28 evening (Tuesday). Earliest post-merge acceptance
visible: 2026-04-29 morning (Wednesday) — that's when the sweeper's
zero-intervention observation §7.2 bullet 2 lands.

### 6.5 Dispatch plan — concrete commands and hand-off signals

Each phase has (a) a concrete dispatch command, (b) a named hand-off
signal that transitions to the next phase, and (c) a fallback if the
hand-off doesn't land within its budget.

#### Phase P1 — Round-2 review dispatch (next action)

**Triggered by:** v1 commit of this file (`sprint-13-plan.md`) on
trunk of the `hassaleh` repo.

**Dispatch commands** (run from Dione's session after commit):

```bash
# Inanna (security + API correctness, second-round focused on CR closure)
openclaw agent --agent worker-opus --timeout 900 \
  --session-id "sprint-13-review-2-inanna" \
  --message "$(cat <<'PROMPT'
SPRINT-13 ROUND-2 REVIEW — INANNA

Context:
- DATE=2026-04-23
- PLAN_FILE=~/projects/hassaleh/docs/sprint-13-plan.md (v1, 958 lines)
- YOUR_R1_REVIEW=~/projects/hassaleh/docs/sprint-13-plan-review-1-inanna.md

Task: read v1 of the plan. For EACH of your 8 Round-1 blocking CRs
(I-CR-1 through I-CR-8 as labeled in your R1 review), verify in v1
whether it has been addressed. Produce
~/projects/hassaleh/docs/sprint-13-plan-review-2-inanna.md with:

- One row per original CR: ADDRESSED / PARTIALLY-ADDRESSED / NOT-ADDRESSED
- For PARTIAL or NOT: a concrete pointer to the remaining gap
- An overall verdict: CLEAN / CHANGE-REQ

Do NOT re-raise issues outside your R1 scope. If you see a new issue,
note it as an "Advisory for v1.1 or later", NOT as a blocking finding.

Anti-hallucination guard: quote file paths and Cypher/§ references
verbatim from the v1 file; any paraphrase must be flagged as such.
PROMPT
)"

# gemini-reviewer (clarity + internal consistency + verification block)
openclaw agent --agent worker-gemini --timeout 600 \
  --session-id "sprint-13-review-2-gemini" \
  --message "$(cat <<'PROMPT'
SPRINT-13 ROUND-2 REVIEW — GEMINI-REVIEWER

[same shape as above, but reference ~review-1-gemini.md and G-CR-1..G-CR-7]
PROMPT
)"
```

**Budget:** 15 min wall-clock per reviewer (both in parallel).

**Hand-off signal to P2:** both review-2 docs committed with a
`verdict: CLEAN` line. Explicit grep target:
`grep -l "^verdict: CLEAN" docs/sprint-13-plan-review-2-*.md | wc -l`
equals `2`.

**Fallback:** if one reviewer returns CHANGE-REQ with <3 items, Dione
produces a v1.1 polish patch same-day and re-dispatches that one
reviewer only. If CHANGE-REQ with ≥3 items, or if it's blocking,
escalate to Ingo — the scope assumption of "11 CRs was the whole
set" was wrong, and the plan needs wider revision.

#### Phase P2 — Track A + D kickoff

**Triggered by:** P1 CLEAN hand-off signal above.

**Actions:**

1. Commit this plan as FROZEN (add `STATUS: FROZEN as v1.0` to top).
2. Create `~/projects/hassaleh/sprint-13-state.json` (schema below).
3. Re-target `hassaleh-sprint-orchestrator` cron at
   `sprint-13-state.json` (edit the cron's "state-file" env var).
4. Dispatch Track A + Track D in parallel (different workers, no
   shared files):

```bash
# Track A — worker-codex, Core runtime (Intent/Result dataclasses, registry, execute())
openclaw agent --agent worker-codex --timeout 1800 \
  --session-id "sprint-13-track-a-implement" \
  --message "$(cat <<'PROMPT'
SPRINT-13 TRACK A — CORE RUNTIME IMPLEMENTATION

Context:
- DATE=2026-04-24
- PLAN=~/projects/hassaleh/docs/sprint-13-plan.md §2.2, §2.3, §2.4
- YOUR_OUTPUT=src/hassaleh/runtime/core.py, src/hassaleh/runtime/types.py
- TESTS=tests/test_runtime_core.py
- COMMIT_STYLE=see Sprint-12 commits on trunk

Task: implement the Intent/Result dataclasses (§2.2), the handler
registry decorator `register_handler` (§2.3), and the top-level
`HassalehRuntime.execute(intent, ctx) → Result` function (§2.4)
following the canonical order validate → capability_check →
precondition → mutate per §2.5.

No Cypher here — Track B fills in handlers. This track's deliverables
are the "skeleton + dispatch loop".

Tests required:
- unknown-intent-type → validation-error
- missing-capability → capability-denied (mock scope check)
- handler-exception → internal-error with rollback
- successful dispatch → ok result, transaction committed

Commit message: "sprint-13 Track A: runtime core (Intent/Result/registry)"
PROMPT
)"

# Track D — worker-gemini, Observability wiring
openclaw agent --agent worker-gemini --timeout 1800 \
  --session-id "sprint-13-track-d-implement" \
  --message "$(cat <<'PROMPT'
SPRINT-13 TRACK D — OBSERVABILITY WIRING

Context:
- DATE=2026-04-24
- PLAN=~/projects/hassaleh/docs/sprint-13-plan.md §2.6 (authoritative)
- YOUR_OUTPUT=src/hassaleh/runtime/observability.py
- TESTS=tests/test_runtime_observability.py

Task: implement the four child spans (intent.validate,
intent.capability_check, intent.precondition, intent.mutate), the
two metrics (hassaleh_intent_total{intent_type, result},
hassaleh_intent_duration_seconds{intent_type}), and the structured
log line per intent — all reusing the Sprint-12 OTel/Prometheus/
structlog surfaces.

Integration point with Track A: export three helpers that Track A
calls in sequence — start_root_span(intent), record_metric(intent,
result, duration_ms), emit_log(intent, result, error_code).

Commit message: "sprint-13 Track D: observability wiring for Intent execution"
PROMPT
)"
```

**Budget:** Track A ~4h for first clean test pass; Track D ~3h.
Dispatched in parallel.

**Hand-off signal to P3:** Track A's `src/hassaleh/runtime/types.py`
(the Intent/Result dataclasses) exists and passes `python -c "from
hassaleh.runtime.types import Intent, Result"`. This unblocks Track C.

#### Phase P3 — Track C kickoff (Capability module)

**Triggered by:** Track A's types module importable.

**Actions:**

```bash
openclaw agent --agent worker-codex --timeout 1200 \
  --session-id "sprint-13-track-c-implement" \
  --message "SPRINT-13 TRACK C — CAPABILITIES … see §3.2 of plan …"
```

**Hand-off to P4 (Track B + E):** Track C's `capabilities.py` exports
a `check_scope(principal, required_capability) -> bool` and the
schema addition on `ApiKey.scopes` is in `schema.cypher`. **Track B
(six handlers)** and **Track E (market_state.py refactor)** both
depend on this.

#### Phase P4 — Track B + Track E in parallel

Track B (worker-codex, six handlers §3.1–§3.6) and Track E (worker-
gemini, market_state.py migration) can author in parallel once Track
C's capability module is in place. They converge at Track F.

#### Phase P5 — Track F final integration + merge

Dione (worker-opus as main, NOT as a worker dispatch — this is
integration, not per-track authoring) writes the end-to-end test
(`tests/test_runtime_e2e.py`) and the crash-recovery test
(`tests/test_runtime_crash_recovery.py`). Green build → merge to
trunk → tag `sprint-13-v1.0`.

#### Per-track Round-1 review protocol

Every track deliverable is subject to one Round-1 review before its
merge:

| Track | Author | Reviewer | Review focus |
|-------|--------|----------|--------------|
| A | worker-codex | Inanna (worker-opus) | Runtime correctness, exception safety, dispatch-loop invariants |
| B | worker-codex | Inanna (worker-opus) | Precondition Cypher correctness, error-code coverage, mutation atomicity |
| C | worker-codex | Inanna (worker-opus) | Auth semantics (exact-match, scope list integrity, schema impact) |
| D | worker-gemini | gemini-reviewer (worker-gemini, different session) | Span shape, metric label match to §2.6, log field match |
| E | worker-gemini | Inanna (worker-opus) | Backward-compat flag correctness, rollback path, migration idempotency |
| F | Dione (main) | Inanna (worker-opus) | Test coverage vs S1–S4; no false-negative in crash-recovery |

**Round-2 on individual tracks is skipped unless a Round-1 is
CHANGE-REQ.** Design decision: individual tracks are small enough
(few hundred lines each) that the Round-1 reviewer usually sees
everything; the heavy "two-rounds with CR integration" protocol is
reserved for the sprint-level plan doc above.

**Review-cadence guardrail:** if ANY track accumulates ≥2 Round-1
CRs that are not addressable within an hour, that track is flagged
as "needs orchestrator attention" in `sprint-13-state.json`. Dione
escalates to Ingo rather than grinding toward Round-3.

#### sprint-13-state.json schema (Phase P2 will create)

```json
{
  "version": 1,
  "startedAt": "2026-04-24T<HH:MM>:00+02:00",
  "planVersion": "v1.0",
  "tracks": {
    "A": {"phase": "pending", "author": "worker-codex", "reviewer": "inanna", "round1Verdict": null, "mergedAt": null},
    "B": {"phase": "pending", "author": "worker-codex", "reviewer": "inanna", "round1Verdict": null, "mergedAt": null},
    "C": {"phase": "pending", "author": "worker-codex", "reviewer": "inanna", "round1Verdict": null, "mergedAt": null},
    "D": {"phase": "pending", "author": "worker-gemini", "reviewer": "gemini-reviewer", "round1Verdict": null, "mergedAt": null},
    "E": {"phase": "pending", "author": "worker-gemini", "reviewer": "inanna", "round1Verdict": null, "mergedAt": null},
    "F": {"phase": "pending", "author": "dione-main", "reviewer": "inanna", "round1Verdict": null, "mergedAt": null}
  }
}
```

The sprint-orchestrator cron (existing id
`5d854f3b-8dea-4e5c-a680-9ba5eee1c64b`, every 2h) watches
`tracks.*.phase` transitions and fires the next hand-off dispatch
automatically. Human-only operation: Ingo confirms each FROZEN
verdict before trunk tag.

---

## 7. Acceptance & "Done" criteria

Split in v1 (per I-CR-7) into **Merge-gate criteria** (satisfiable
before Day 5 afternoon merge) and **Post-merge acceptance** (observed
on or after Day 6 in production). The distinction matters because an
unmerged sprint is blocked by Merge-gate items but not by Post-merge
ones.

### 7.1 Merge-gate criteria (Day 5, before merge)

A green build AND:

- **Success criteria S1, S3, S4 from §1 all pass in CI.** (S2 is a
  test; see §1 Success Criteria for the test name.)
- **All six Intent types pass their per-intent tests** (§3.1–§3.6),
  including precondition-fail cases (one test per distinct
  `error_code` value declared in §3).
- **Capability-denied path is exercised** by at least one test per
  Intent (no-scope, wrong-scope, revoked-key).
- **Crash-recovery test green**: a process kill between precondition
  and mutation leaves the graph exactly as it was before (this is the
  test underlying S2).
- **Sprint-13 plan v1 or higher is FROZEN** with both Round-2
  verdicts CLEAN.

### 7.2 Post-merge acceptance (Day 6 onwards)

Observed, not tested in CI:

- **One production market pipeline daily run** uses the Runtime
  Operator end-to-end without rollback. Visible via the Sprint-12
  dashboards.
- **The sweeper fires zero `"analyst-md present but no attempt entry"`
  state-repair interventions** on that production run. The sweeper
  may still fire for unrelated failure classes (never-started
  segments); only the specific class addressed by Sprint 13 must be
  zero.

If §7.2 fails for a reason that would have been caught by a test
that didn't exist, the merge stands but the missing test is opened
as a CR against the next sprint's backlog.

---

## 8. Cross-cutting concerns & previously-forgotten items

- **Backwards compat during migration:** `scripts/market_state.py`
  will support BOTH old file-based write AND new Intent dispatch,
  gated by env `HASSALEH_RUNTIME_OPERATOR=1`, for one week after
  merge. Default off in the first 24 h post-merge to let us fall back
  fast if a regression hits. Flipped to default-on after one clean
  daily cycle.
- **Neo4j schema impact:**
  - R4 requires an index on `ApiKey.scopes`. Added to `schema.cypher`
    by Track C. No backfill required (fresh field).
  - **New v1 (per I-CR-4.2):** a uniqueness constraint on
    `(Segment.id, Attempt.n)`:
    ```cypher
    CREATE CONSTRAINT attempt_unique_per_segment IF NOT EXISTS
    FOR (a:Attempt) REQUIRE (a.segment_id, a.n) IS UNIQUE;
    ```
    Without it, two concurrent writers of the same `(segment, cycle)`
    could both create Attempt nodes — the precondition check would
    pass independently for each. The constraint makes the Cypher
    mutation atomically reject the duplicate with a schema error,
    which the runtime maps to
    `Result.kind = "precondition-failed",
    error_code = "concurrent-attempt-conflict"`.
    Added to `schema.cypher` by Track B along with the Attempt
    handlers. Backfill: the constraint's `IF NOT EXISTS` clause lets
    it be applied on a live graph without checking for pre-existing
    duplicates; any such duplicate would be a v0-era bug that the
    migration (Track E) should surface in a pre-deployment audit.
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
- **Every Intent type in §3 has explicit precondition Cypher**
  (not prose). v1 addresses this for all six Intent types per
  I-CR-1 / G-CR-1.
- **Every in-sprint success criterion (S1, S3, S4 in v1) is
  verifiable by a test whose name is plausibly derivable from
  this plan.** S2 is the named test
  `test_add_analyst_attempt_writes_md_and_attempt_atomically`
  (v1 restructure per G-CR-6); the production-observation form
  of S2 lives in §7.2, not §1.
- Tracks A–F in §5 each have clearly separable deliverables (no
  track's output is another track's internal module).
- The crash-recovery test is named and scoped (§7.1 bullet 4).
- §8 cross-cutting concerns are stated, not hidden.
- **The canonical execution order is declared once and referenced
  everywhere** — §2.1 diagram, §2.5 explicit order, §2.6 span list,
  R5 child-span parenthetical all agree on
  `validate → capability_check → precondition → mutate` (I-CR-2,
  G-CR-2).
- **Metric label and log field names are declared once (in §2.6)
  and referenced, not restated, in R5 / S3 / S4** (G-CR-3, G-CR-4,
  G-CR-7).
- **Intent capability is declared at the handler registration site,
  not on the Intent** (I-CR-3). §2.2, §2.3, §2.5, §3.1–§3.6 all
  align on this.

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

- **2026-04-21 v0**: Initial draft. Ingo selected Option B (Runtime
  Operator) over Option A (Observability patch) and Option C
  (Capability model) in the pre-sprint proposal. Sprint 13 scoped to:
  runtime infrastructure, five Intent types for the Market Pipeline,
  capability enforcement at the runtime boundary, Sprint-12
  observability wiring, full migration of the Market Pipeline to the
  runtime.

- **2026-04-23 v1 (this revision)**: Integrates 11 consolidated CRs
  from Round-1 reviews (Inanna: 8 blocking; gemini-reviewer: 6
  blocking + 1 change-req; 2 duplicates folded). Per-CR summary:

  | CR | Addressed in §… | Change |
  |----|-----------------|--------|
  | **I-CR-1 / G-CR-1** (precondition Cypher for §3.2–§3.5) | §3.2, §3.3, §3.4, §3.5, §3.6 | All five remaining Intents now carry explicit precondition Cypher with distinct error-code mappings. Matches §3.1's structure. |
  | **I-CR-2** (execution-order contradiction) | §2.1, §2.5, §2.6, R5 | Canonical order `validate → capability_check → precondition → mutate` declared in §2.5, referenced everywhere. R5 updated; §2.5 sentence "Scope check runs BEFORE validation" corrected. |
  | **I-CR-3** (Intent.capability bypass) | §2.2, §2.3, §2.5, §3.1–§3.6 | `capability` field removed from `Intent` dataclass. `@register_handler` now takes `requires_capability` kwarg. Runtime looks it up; caller has no say. |
  | **I-CR-4** (Cypher correctness in §3.1 mutation) | §3.1, §8 | APOC inner-query parameters passed explicitly. `apoc.when` → `apoc.do.when`. `$now` bound from `ctx.now` via §2.4. Schema uniqueness constraint on `(Segment.id, Attempt.n)` added in §8. |
  | **I-CR-5 / I-CR-8** (overloaded error code in §3.1 precondition) | §3.1 | Three distinct codes: `run-not-found`, `segment-not-found`, `terminal-verdict-exists`. Precondition Cypher returns boolean columns; mapping table added. Propagated pattern to §3.2–§3.6 as they got their own Cypher. |
  | **G-CR-2** (child-span identity contradiction) | §2.6, R5 | §2.6 is authoritative. Adopts `{validate, capability_check, precondition, mutate}`. `result` removed as child span (result is returned by `execute()`, not a separate span). R5 updated. |
  | **I-CR-6 / G-CR-3** (capability cardinality in §3.4) | §3.4 → §3.4 + §3.5 (new) | Split into `market.update-writer-attempt` (capability `market.writer.write`) and `market.update-writer-attempt-by-publisher` (capability `market.publisher.write`). Former §3.5 renumbered to §3.6. §2.5's exact-match rule kept unchanged. R6 updated to "six Intent types". §5 Track B updated. |
  | **G-CR-3 (metric)** (redundant `capability_granted` label) | §2.6 | Counter simplified to `{intent_type, result}`. `capability_granted` removed. |
  | **G-CR-4** (result-label casing drift) | §2.2, §2.6, S4 | Kebab-case hyphens throughout. S4 assertion now uses `"capability-denied"`. §2.6 explicitly states no runtime translation layer exists. |
  | **I-CR-7** (merge-gate / post-merge ambiguity) | §7 → §7.1 + §7.2 | Day-5 merge-gate criteria vs Day-6+ post-merge acceptance split into separate subsections. S2 production-observation form lives in §7.2. |
  | **G-CR-5** (idempotency_key declared but unspec) | §2.2 | `idempotency_key` field removed from v1 Intent. Deferred to a named v2+ sprint. Sprint-13 sweeper + `terminal-verdict-exists` precondition cover practical retry safety. |
  | **G-CR-6** (S2 is production observation, not test) | §1 (S2), §7.2 | S2 reframed as the in-sprint crash-recovery test `test_add_analyst_attempt_writes_md_and_attempt_atomically`. The production observation lives in §7.2, not as a Success Criterion. |
  | **G-CR-7** (S3 field name `error_type` vs `error_code`) | §1 (S3), §2.6 | Normalised to `error_code` everywhere. §2.6 declares it authoritative. |

  **Reviewer-agreement dividend:** no Inanna ↔ gemini contradictions
  needed adjudication; all 11 CRs had compatible recommendations.

### What will change in v1.1 (if needed post-Round-2)

Only minor polish in response to remaining Round-2 findings. If
substantive issues surface (which we don't expect — all blocking CRs
are addressed here with concrete mechanisms), v2 of the plan would
issue rather than v1.1.

- **2026-04-24 v1.1**: Single-file polish addressing Inanna Round-2's
  one remaining PARTIAL finding, I-CR-4.2.

  **The issue**: v1 added a uniqueness constraint on
  `(a.segment_id, a.n)` in §8 but the §3.1 mutation CREATE clause
  never wrote `segment_id` on the Attempt node. Neo4j 5's rule for
  node-property uniqueness constraints treats a null in the tuple
  as "no constraint applies", so the constraint silently did not
  fire — and the concurrent-writer race the constraint was supposed
  to close was still open.

  **The fix**: §3.1 mutation now writes `segment_id` on both paths of
  the `apoc.do.when` dispatch:
  - CREATE branch: `segment_id: $segment_id` added to the property map.
  - SET branch: `a.segment_id = coalesce(a.segment_id, $segment_id)`
    so a pre-existing Attempt missing the field gets a lazy backfill
    without clobbering any value already there.

  Both branches reference `$segment_id` in the params map (added).
  Per Inanna's Round-2 recommendation, option (a) — keep the node
  authoritative rather than re-expressing the constraint against the
  HAS_ATTEMPT relationship.

  No other §3 Intents are affected: they either don't CREATE Attempt
  nodes (§3.2 SetVerdict, §3.3 RegisterWriterAttempt target the
  existing attempt or writer-attempt respectively), or they target
  different node types (§3.6 SetPublished creates Post nodes).
  The constraint is scoped to Attempt nodes, and only §3.1 creates
  those.

  gemini-reviewer Round-2 was already CLEAN; v1.1 requires only an
  Inanna Round-2b re-verify on I-CR-4.2, not a full Round-2.

- **2026-04-24 FROZEN as v1.1**: Inanna Round-2b returned CLEAN
  (`sprint-13-plan-review-2b-inanna.md`) — 0 blocking remaining,
  1 non-blocking Advisory (A-1, carried over from Round-2, applies to
  Track B implementation review, not to this plan). Combined with
  gemini-reviewer Round-2 CLEAN, both reviewer sign-offs are now in
  hand. The plan is FROZEN as v1.1 and ready for §6.5 Phase P2
  (Track A + Track D kickoff) pending Ingo's operational go for worker
  dispatch.
