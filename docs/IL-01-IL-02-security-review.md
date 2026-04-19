# IL-01 / IL-02 Security Review

**Reviewer:** Inanna 🛡️
**Date:** 2026-04-18
**Branch:** `trunk` (uncommitted)
**Scope:** `src/hassaleh/sdk.py` (per implementation evidence), cross-checked
against `src/hassaleh/auth.py`, `src/hassaleh/heartbeat_sdk.py`,
`src/hassaleh/intent_sdk.py`, `schema.cypher`, `tests/test_sdk.py`,
`docs/IL-01-test-verification.md`.

---

## Verdict: **CHANGES REQUESTED**

The `sdk.py` implementation of IL-01 is clean and well-tested. The `sdk.py`
implementation of IL-02 meets the *security* goal (single bcrypt per auth
attempt, sanitized errors) but the stated "indexed Cypher MATCH" is not backed
by the schema, and a parallel live SDK class — `IntentSDK` — still carries the
exact O(N) bcrypt scan IL-02 was written to eliminate. IL-02 is therefore **not
closed at codebase level**.

Two required follow-ups block "IL-02 done":

1. **Add explicit `api_key_lookup` index to `schema.cypher`.** Preferably a
   uniqueness constraint (which implicitly indexes and enforces 1:1
   key→agent).
2. **Migrate `IntentSDK._authenticate()` to the `lookup_hash` + single-bcrypt
   pattern.** This is a port of the same fix already applied in `sdk.py` and
   `heartbeat_sdk.py`.

A nonblocking scope question for the sprint lead: if IL-02 was deliberately
scoped to `sdk.py` only (with `IntentSDK` handled in a follow-on ticket),
document that decision explicitly. The current evidence note
(`docs/IL-01-test-verification.md`) does not call this out, and without it
the residual vulnerability will read as an oversight during a subsequent audit.

---

## IL-01 findings — Remove caller-supplied `agent_id`, derive from API key

### Coverage

Every state-mutating method on `HassalehSDK` that previously attributed
identity now derives `agent_id` via `_authenticate(api_key)` before it touches
the graph. Verified against
`src/hassaleh/sdk.py:251,334,406,463,523,604,657,713,740,773`:

| Method | Authenticates? | Binds server-derived `agent_id`? |
|---|---|---|
| `submit_intent` (line 225) | ✅ `sdk.py:251` | ✅ `sdk.py:284` |
| `poll_intent` (line 313) | ✅ `sdk.py:334` | ✅ `sdk.py:350` |
| `wait_for_intent` (line 356) | via `poll_intent` | n/a |
| `my_tasks` (line 397) | ✅ `sdk.py:406` | ✅ `sdk.py:415` |
| `review_task` (line 452) | ✅ `sdk.py:463` | ✅ `sdk.py:488` |
| `send_message` (line 501) | ✅ `sdk.py:523` | ✅ `sdk.py:537` |
| `read_messages` (line 584) | ✅ `sdk.py:604` | ✅ `sdk.py:612` etc. |
| `advance_cursor` (line 651) | ✅ `sdk.py:657` | ✅ `sdk.py:667` |
| `contribute_to_discussion` (line 702) | ✅ `sdk.py:713` | ✅ `sdk.py:725` |
| `resolve_discussion` (line 729) | ✅ `sdk.py:740` | ✅ `sdk.py:747,762` |
| `agent_info` (line 766) | ✅ `sdk.py:773` | ✅ `sdk.py:782` |

No `sdk.py` method signature takes `agent_id`. Grepping for `agent_id=` and
`agent_id:` in `sdk.py` returns only internal bindings from `_authenticate`'s
return value and Cypher parameter passthrough — no caller-supplied values.

### Bypasses checked

- **No path reads `api_key` but skips `_authenticate`.** Walked every method
  that accepts `api_key` — all call `_authenticate` as the first statement
  before any graph interaction.
- **No path writes agent-scoped state without authentication.**
  `create_discussion` (line 672) does not authenticate but also does not
  attribute identity (it only creates a `Discussion` node + optional
  `IN_CONTEXT_OF` edge). Not an IL-01 violation; flag only as an unrelated
  missing-auth observation outside this review's scope.
  `project_status` / `task_group_status` are read-only helpers that take
  node IDs, not `api_key` — intentional.
- **No identity forgery via legacy kwargs.** `tests/test_sdk.py` asserts
  `TypeError` on `agent_id=` for `submit_intent`, `my_tasks`, `review_task`,
  `send_message`, `agent_info`, and `wait_for_intent`
  (`tests/test_sdk.py:195-208,268-304,479-490`). The Python signature itself
  is the enforcement point — a forged `agent_id` can never reach the graph.
- **Authentication failure short-circuits before writes.**
  `tests/test_sdk.py:246-264` asserts `session.execute_write.assert_not_called()`
  when `_authenticate` raises — verified.

### Edge cases

- `submit_intent(action="update_property", target_label=X)` validates
  `target_label` against `ALLOWED_TARGET_LABELS` at `sdk.py:256` **before**
  the f-string interpolation at `sdk.py:300` — Cypher injection via label
  closed. Same pattern correctly applied in `send_message` at
  `sdk.py:541-546` and `create_discussion` at `sdk.py:692`.
- `wait_for_intent` re-authenticates on every poll loop via `poll_intent`
  (`sdk.py:386`). A revoked/disabled API key cannot continue streaming state
  indefinitely.
- Residual: `tests/test_chaos.py:123-126` still calls
  `sdk.submit_intent(agent_id='no-cap-agent', …)`. Per
  `docs/IL-01-test-verification.md` this test file failed earlier during
  fixture setup (port 7691 unreachable) and masked the IL-01 migration
  break. This does **not** weaken IL-01 — the signature guard will raise
  `TypeError` once the port is fixed — but the stale call site must be
  updated before merge so the chaos suite actually runs. Same applies to
  `tests/test_messaging.py:57,67-69,79-80` per the verification doc.

**IL-01 verdict: ✅ CLEAN in `sdk.py`.** Chaos/messaging callers need stale
kwarg removal but those are test fixes, not security regressions.

---

## IL-02 findings — Replace O(N) bcrypt scan with indexed lookup

### Index correctness

The implementation evidence states `sdk.py::_authenticate()` "uses
`api_key_lookup` index". The Cypher does use the `api_key_lookup` property
(`sdk.py:153`) and computes the deterministic SHA-256 via `lookup_hash`
(`sdk.py:149`, backed by `auth.py:26-33`). **However, no index exists in
`schema.cypher`.** Grep for `api_key_lookup` in `schema.cypher` returns zero
hits; the only indexed properties on `Agent` are `id` (unique constraint,
line 15) and `lifecycle` (index, line 60).

Effect: `MATCH (a:Agent) WHERE a.api_key_lookup = $lookup` degrades to a
label scan over all `Agent` nodes with a per-record string-equality check.
For the current agent population (single-digit to low-double-digit) this is
microseconds and effectively free — the security goal of capping auth cost
at one bcrypt is still met. But the claim "indexed lookup" is not backed by
the schema, and at scale (hundreds+ of agents) this will regress.

**Required:** Add to `schema.cypher`:

```cypher
CREATE CONSTRAINT agent_api_key_lookup IF NOT EXISTS
  FOR (a:Agent) REQUIRE a.api_key_lookup IS UNIQUE;
```

A uniqueness constraint is preferable to a plain index here: it implicitly
creates an index **and** formally forbids two agents from sharing a
lookup hash (a SHA-256 collision is cryptographically infeasible, but
absence of the constraint means a future bug that backfills the wrong hash
would go undetected at write time and produce ambiguous `await .single()`
behavior at read time).

Note: `heartbeat_sdk.py:116` uses the exact same unindexed MATCH pattern
already merged. The constraint fixes both call sites simultaneously.

### Timing analysis

Path analysis of `_authenticate` (`sdk.py:135-165`):

| Condition | Cypher cost | bcrypt calls | Error |
|---|---|---|---|
| `api_key` empty/non-str | — | 0 | `AuthenticationError("Invalid API key")` |
| No Agent with matching `api_key_lookup` | label scan | 0 | `AuthenticationError("Invalid API key")` |
| Agent found, `api_key_hash` empty/None | label scan | 0 | `AuthenticationError("Invalid API key")` |
| Agent found, bcrypt rejects | label scan | 1 | `AuthenticationError("Invalid API key")` |
| Agent found, bcrypt accepts | label scan | 1 | returns `agent_id` |

- **Constant-time-ish bcrypt layer:** ✅ — exactly one `bcrypt.checkpw` call
  on the happy path, zero on the miss path. No O(N) dependency on agent
  count.
- **Residual timing side-channel:** present but acceptable. An attacker can
  measure whether their submitted key has a *matching lookup hash* on the
  server (fast path ≈ µs; match path ≈ 100ms bcrypt). That leak is
  equivalent to what the authentication response already reveals —
  knowing a specific raw key is registered — so it doesn't expand the
  attack surface. To close it fully would require calling bcrypt on the
  miss path (against a dummy hash) — not standard practice, not recommended
  unless threat model demands it.
- **Preimage / enumeration:** SHA-256 of a 256-bit-entropy `secrets.token_urlsafe(32)`
  key is not brute-forceable from a graph dump. No concern.

### Error parity

All three failure branches raise **byte-identical** `AuthenticationError("Invalid API key")`
(`sdk.py:147,160,163`). An attacker cannot distinguish:

- missing/empty key
- no agent with that lookup hash
- agent exists with empty `api_key_hash` (misconfigured seed)
- agent exists, bcrypt rejects

This matches the `heartbeat_sdk.py` pattern and the IL-03 "sanitized
PermissionError" pattern used in `poll_intent` at `sdk.py:353`. ✅

### IL-02 in other SDKs — **UNFIXED**

`src/hassaleh/intent_sdk.py::IntentSDK._authenticate()` (lines 85-104) still
loads **every** Agent with `api_key_hash IS NOT NULL` and
`lifecycle IN ['active', 'running']` into memory and loops
`bcrypt.checkpw` over them until one matches. This is exactly the pattern
`docs/security-audit-intent-lifecycle.md:97-101` flagged as IL-02 and that
`docs/review-e2-audit-review.md:82` cites as "a per-request O(N) bcrypt
scan… simply wasn't migrated."

`IntentSDK` is a live class — imported by `tests/test_mvp_intent.py:46` and
exercised across dozens of tests (submit/status/result paths per spec §4).
It is not dead code.

**Required:** Migrate `IntentSDK._authenticate()` to the same two-step
`lookup_hash` + single `bcrypt.checkpw` pattern used in `sdk.py:149-165`
and `heartbeat_sdk.py:112-128`. The primitive (`lookup_hash`) already
exists; this is a port, not new design work. Same uniqueness constraint
from the schema change above covers it.

Without this migration, an attacker can still DoS authentication by
submitting a bogus key through the IntentSDK path: server walks every
active-agent bcrypt hash (~100ms × N) on every attempt, parallelize to
exhaust auth workers. IL-02 is explicitly a DoS finding; leaving
`IntentSDK` behind does not close it.

**IL-02 verdict: ⚠️ `sdk.py` meets the security goal; codebase-level
finding remains open.**

---

## Required follow-ups (blocking merge of "IL-02 done")

1. **`schema.cypher`** — add `CREATE CONSTRAINT agent_api_key_lookup IF NOT EXISTS
   FOR (a:Agent) REQUIRE a.api_key_lookup IS UNIQUE;` so the "indexed lookup"
   claim is backed, `heartbeat_sdk` + `sdk` MATCHes use an index at scale, and
   a uniqueness invariant guards against backfill bugs.
2. **`src/hassaleh/intent_sdk.py`** — migrate `IntentSDK._authenticate()` to
   `lookup_hash` + single `bcrypt.checkpw`. Add a regression test equivalent
   to the existing heartbeat/sdk ones (wrong key → single bcrypt; missing key
   → zero bcrypt; graph with N agents → auth cost independent of N).
3. **`tests/test_chaos.py:123-126` and `tests/test_messaging.py:57,67-69,79-80`** —
   remove the stale `agent_id=` kwargs from `submit_intent` / `send_message`
   callers. These are masked today by the port-7691 fixture failure; once that
   is fixed, they will surface as `TypeError`s and the chaos/messaging suites
   will regress. (Noted in `docs/IL-01-test-verification.md`.)

## Non-blocking observations

- `sdk.py::create_discussion` (line 672) creates a `Discussion` node without
  authentication. Not an IL-01 violation (no agent identity is asserted) but
  worth a separate ticket — a Discussion without an owner edge is a
  permission-model gap that `resolve_discussion` partially papers over by
  requiring `CONTRIBUTED` before resolve.
- `sdk.py::send_message` silently drops the context linkage when
  `context_label not in ALLOWED_TARGET_LABELS` (line 542 `return`). A
  caller who mistypes a label gets a `Message` node with no
  `IN_CONTEXT_OF` edge and no error. Prefer raising `ValueError`, as
  `submit_intent` does at line 257. Out of scope for IL-01/IL-02.

---

**Sign-off line:** Reviewed by Inanna, 2026-04-18.
