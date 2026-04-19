# IL-02 Security Review — v2

**Reviewer:** Inanna 🛡️
**Date:** 2026-04-19
**Target:** `docs/IL-02-followup-implementation.md` (324 lines, cycle-7 landing)
**Prior verdict (v1):** CHANGES REQUESTED — code was in place but the
implementation doc was missing, which blocked review against a concrete
diff.
**Scope:** verify that the doc's claims match the actual code; decide
both open items the doc explicitly flags to the v2 reviewer.

## Verdict: CLEAN

All code-level claims in `IL-02-followup-implementation.md` match the
source. The O(N)-bcrypt CPU-DoS amplification vector (the sole HIGH-
severity finding from IL-02) is closed. The two open items are
addressed below; neither blocks merge.

**Merge-bundle recommendation: proceed with IL-01/02/03.** IL-01 and
IL-03 are already CLEAN; IL-02 is now CLEAN with the residual timing
channel filed as a **separate, non-blocking MEDIUM-severity follow-up
finding** (see §3 below).

## 1. Evidence — code vs. doc

### 1.1 `schema.cypher:24-25` — uniqueness constraint

Verified against `schema.cypher`:

```cypher
// schema.cypher:18-25
// IL-02: indexed auth lookup + hash-collision guard.
// Implicitly indexes api_key_lookup so sdk.py / heartbeat_sdk.py / intent_sdk.py
// MATCH (a:Agent) WHERE a.api_key_lookup = $lookup uses a range index instead of
// a label scan, and enforces a 1:1 key→agent invariant (a backfill bug that
// duplicated a lookup hash would now fail at write time instead of returning an
// ambiguous record at read time).
CREATE CONSTRAINT agent_api_key_lookup IF NOT EXISTS
  FOR (a:Agent) REQUIRE a.api_key_lookup IS UNIQUE;
```

Matches `IL-02-followup-implementation.md:39-48` byte-for-byte.
`IF NOT EXISTS` makes re-applying the schema safe; the constraint fires
the duplicate-detection signal by design on a polluted graph.

### 1.2 `src/hassaleh/intent_sdk.py:85-121` — indexed `_authenticate`

Verified against source (line numbers match the doc):

- `intent_sdk.py:102` — non-string / empty-string short-circuit; no DB
  session, no bcrypt work. Matches doc §3 step 1.
- `intent_sdk.py:105` — `key_lookup = lookup_hash(api_key)`
  (SHA-256 hex via `hassaleh.auth.lookup_hash`). Matches step 2.
- `intent_sdk.py:107-113` — `MATCH (a:Agent) WHERE a.api_key_lookup =
  $lookup RETURN a.id AS agent_id, a.api_key_hash AS api_key_hash`
  inside a single `async with self.driver.session()` block, `result.single()`
  → at most one row (uniqueness constraint enforces this at write time).
  Matches step 3.
- `intent_sdk.py:115-116` — `if record is None or not record["api_key_hash"]:
  raise AuthenticationError("Invalid API key")`. Matches step 4. No bcrypt
  on this path.
- `intent_sdk.py:118-119` — single `verify_api_key(api_key,
  record["api_key_hash"])` call; failure raises the same
  `AuthenticationError("Invalid API key")`. Matches step 5.
- `intent_sdk.py:121` — returns `record["agent_id"]`.

Error-message uniformity: all four failure branches raise
byte-identical `AuthenticationError("Invalid API key")`. Matches doc §3.1
and the reference to `sdk.py:147,160,163` in the source docstring
(`intent_sdk.py:94`).

### 1.3 Parity across `sdk.py`, `heartbeat_sdk.py`, `intent_sdk.py`

All three auth paths now share the same `MATCH` shape:

| File | Line | Query |
|------|------|-------|
| `src/hassaleh/sdk.py` | 165 | `MATCH (a:Agent) WHERE a.api_key_lookup = $lookup ...` |
| `src/hassaleh/heartbeat_sdk.py` | 116 | `MATCH (a:Agent) WHERE a.api_key_lookup = $lookup ...` |
| `src/hassaleh/intent_sdk.py` | 109 | `MATCH (a:Agent) WHERE a.api_key_lookup = $lookup ...` |

Bcrypt cost: 0 or 1 per call site (verified by reading each
`_authenticate`). The only inter-site divergence is the subclass raised
on `AuthenticationError`, which is identical wording across all three.

### 1.4 Regression tests — verified line-by-line

**`tests/test_mvp_intent.py` — `TestIntentSDKAuthenticationCost`** (mock-driven, l. 291-370):

- `test_auth_wrong_key_calls_bcrypt_once` (l. 311-324) — asserts
  `mock_checkpw.call_count == 1` for wrong-hash path. ✓
- `test_auth_missing_or_empty_key_skips_bcrypt` (l. 326-337,
  parametrised over `""` and `None`) — asserts both
  `mock_checkpw.call_count == 0` *and* `sdk.driver.session.assert_not_called()`.
  This is stronger than the doc claims: it proves no DB session is even
  opened on the invalid-input path. ✓
- `test_auth_cost_independent_of_agent_count` (l. 339-370,
  `@pytest.mark.integration`) — seeds 25 `extra-agent-*` rows with
  both `a.api_key_hash` and `a.api_key_lookup`, then asserts
  `mock_checkpw.call_count == 1` for the valid key. Tears down the
  decoys on finally. ✓

**`tests/test_mvp_intent.py` — `TestAuthenticationCost`** (integration-marked, l. 611-694):

- `test_missing_or_empty_key_skips_bcrypt` (l. 622-630) — covers `""` and
  `None`, asserts `mock_check.call_count == 0`. ✓
- `test_unknown_key_skips_bcrypt` (l. 632-639) — the specific test that
  documents the timing-channel gap (`mock_check.call_count == 0` for a
  never-registered key). ✓ — *this is the evidence for §3 below.*
- `test_valid_lookup_wrong_hash_calls_checkpw_exactly_once` (l. 641-650) —
  `mock_check.call_count == 1` for wrong-hash path. ✓
- `test_valid_auth_cost_is_independent_of_agent_count` (l. 652-694) —
  seeds 24 `il02-decoy-*` agents alongside `test-dione`; asserts
  `mock_check.call_count == 1` with a forensic failure message
  (`"auth cost is not O(1) in agent population"`). Tear-down deletes
  the decoys via `MATCH ... WHERE a.id STARTS WITH 'il02-decoy-' DETACH
  DELETE a`. ✓

### 1.5 Fixture updates

- `tests/test_chaos.py:52, 127` — `a.api_key_lookup = $lookup` written
  alongside `a.api_key_hash = $hash` in both the `test_zombie_recovery`
  fixture and the `test_capability_permission_denied` fixture. ✓
- `tests/test_messaging.py:47, 58` — same pattern applied to both
  `Agent` MERGE blocks (the `a.` and `b.` peer fixtures for messaging). ✓
- `tests/test_heartbeat.py` — seven fixture sites (l. 126, 166, 205,
  239, 272, 305, 342) already carried the pattern from the pre-existing
  `heartbeat_sdk.py` port. ✓
- `tests/test_integration.py:68` and `tests/test_mvp_intent.py:148,
  192, 198, 352, 669` — same pattern. ✓

## 2. Open item (a) — property naming: `api_key_lookup` (not `api_key_lookup_hash`)

**Verified consistent.** A full-tree grep for `api_key_lookup` and
`api_key_lookup_hash` across `src/hassaleh/*.py`, `schema.cypher`, and
`tests/test_*.py` returns:

- 20+ occurrences of `api_key_lookup` across the expected surfaces
  (constraint DDL, three SDK `_authenticate` queries, every fixture).
- **Zero occurrences of `api_key_lookup_hash` anywhere in the codebase.**

The task-prompt's reference to `api_key_lookup_hash` is a task-level
mis-naming only; the code consistently uses `api_key_lookup` (which is
actually the correct name — the property *is* the SHA-256 hex digest,
so the trailing `_hash` would be doubled-up). The doc calls this out
at §2 line 52-54, and `logs/sprint11-cycle12-il02-doc.log:5` documents
the naming-decision rationale.

**Status: resolved, no action needed.**

## 3. Open item (b) — residual timing channel on unknown-key path

**Decision: acceptable-for-IL-02. File a separate MEDIUM-severity
follow-up finding; do NOT block the IL-01/02/03 merge bundle on it.**

### 3.1 What the test asserts

`test_unknown_key_skips_bcrypt` (`test_mvp_intent.py:632-639`) asserts
`mock_check.call_count == 0` for a fresh unregistered key. This is
intentional: `intent_sdk.py:115-116` returns on `record is None` before
`verify_api_key` is reached.

### 3.2 The residual oracle — and why it's not IL-02-grade

An attacker who can time responses can now distinguish two buckets:

| Path | Bcrypt calls | Wall-clock cost |
|------|--------------|-----------------|
| Unknown key (no matching lookup row) | 0 | ~1 ms |
| Known agent, wrong hash | 1 | ~100 ms (bcrypt cost-12) |
| Known agent, correct hash | 1 | ~100 ms |

This is a "zero-vs-one-bcrypt" oracle. Concretely, it leaks:
*"this SHA-256 hex digest is registered as a lookup row"*.

It does **not** meaningfully help an attacker recover any API key:

- `api_key_lookup` is `SHA-256(api_key)`; SHA-256 is one-way, so an
  observed timing signal cannot be inverted into the raw `api_key`.
- A brute-force enumeration of the lookup space is 2^256 probes; a
  dictionary attack requires the attacker to already have plausible
  `api_key` candidates, at which point they can just try bcrypt verify
  directly.
- Account enumeration (learning "agent X exists") is the only
  information gain — but agent IDs are *already* low-sensitivity in
  this system (they appear in logs, runbooks, and dashboards).

### 3.3 Why a dummy bcrypt is not obviously a net-positive fix

The naïve fix — "run a dummy `bcrypt.checkpw` against a fixed hash on
the unknown-key path so both paths cost ~100 ms" — re-introduces a DoS
surface:

- Today, an unauth attacker can hit the endpoint at high RPS for
  ~1 ms of CPU per request.
- After a dummy bcrypt, every unauth request costs ~100 ms of CPU.
- Sustained RPS of ~10 is enough to saturate one daemon core with
  pure bcrypt work *without* any authentication at all. That is a
  **regression on the IL-02 finding itself** — we would be closing a
  small timing oracle by re-opening a CPU-amplification vector, just
  with a smaller amplification factor (1x rather than N×).

Before-merging a dummy-bcrypt patch, the correct sequence is:

1. Land IL-02 as-is (O(N)→O(1) bcrypt; CPU-DoS closed).
2. Land a rate-limiter on unauthenticated auth attempts (not in
   scope for Sprint 11, but appropriate for Sprint 12's observability
   work where we now *have* the counter to drive one).
3. Only then add a dummy bcrypt for timing-uniformity, behind a kill-
   switch in case the CPU-DoS returns.

### 3.4 Recorded as a separate finding

I'm filing this as **IL-04 — Auth-path timing oracle on unknown-key
lookup**, severity **MEDIUM**, not blocking. Proposed remediation:
rate-limit first, dummy-bcrypt second. Owner: whoever picks up the
security-review rotation in Sprint 12.

## 4. Merge-bundle recommendation

**Proceed with the IL-01 / IL-02 / IL-03 merge bundle.**

- IL-01 (agent-identity-is-server-derived) — already CLEAN, and cycle
  7's residual cleanup of `submit_intent(agent_id=...)` call sites is
  covered by the §5 grep check in the implementation doc
  (`test_chaos.py:139-141`, `test_mvp_intent.py:237,249,274,376,...`).
- IL-02 (O(N) bcrypt DoS) — CLEAN per this review. O(N)→O(1) verified
  at both the SQL and the test-invariant level.
- IL-03 — already CLEAN.

Post-merge follow-ups (non-blocking):

1. **IL-04** (new, MEDIUM): rate-limit + dummy-bcrypt sequence for the
   residual timing oracle; open as a separate ticket.
2. **Backfill runbook** (doc §8): if any prod instance still holds
   duplicate `api_key_lookup` values, document the
   find-duplicates / pick-canonical / delete-rest / re-apply-constraint
   sequence. Low-priority but cheap to write.
3. **Rotate-all-keys policy** (doc §8): formalise "uniqueness
   constraint fired at write time" as a key-rotation trigger. Belongs
   in the Sprint-12 auth-security runbook set.

**Sign-off line:** Reviewed by Inanna, 2026-04-19. Verdict: **CLEAN**.
IL-01/02/03 merge bundle authorised to proceed.
