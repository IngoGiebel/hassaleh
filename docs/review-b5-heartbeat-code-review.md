# B5 — Heartbeat Code Review

**Reviewer:** Dione (security + quality review, Inanna-style)
**Date:** 2026-04-15
**Spec Version:** 1.1 (Post-Review Revision)
**Files Reviewed:**
- `src/hassaleh/sdk.py` (heartbeat method — **absent**)
- `src/hassaleh/errors.py` (heartbeat error types — **partially absent**)
- `src/hassaleh/auth.py` (API key auth — present, relevant)
- `tests/test_heartbeat.py` (TDD test suite — present)
- `docs/spec-heartbeat.md` v1.1

---

## Summary

This is a **TDD-phase review**: the test suite (`test_heartbeat.py`) has been written against spec v1.1, but the implementation modules (`heartbeat_sdk.py`, heartbeat error classes) do not yet exist. The review evaluates:

1. Whether the tests faithfully encode the spec requirements
2. Whether the test design will guide a correct and secure implementation
3. Gaps between the spec, the existing codebase (`auth.py`, `errors.py`), and the test expectations

The test suite is well-structured and covers all 12 numbered spec scenarios plus meaningful edge cases. However, there are several findings that should be addressed before or during the B4 implementation phase.

---

## Findings

### F1 — Bcrypt vs. Deterministic Hash: Spec/Auth Mismatch
**Severity: HIGH**

The spec (§3.1) describes authentication as:
```cypher
MATCH (a:Agent) WHERE a.api_key_hash = $hash RETURN a.id AS agent_id
```

This implies a **deterministic hash** — given the same key, you get the same hash, enabling direct Cypher equality lookup.

However, `auth.py` uses **bcrypt** (`bcrypt.hashpw` with random salt). Bcrypt is non-deterministic: the same plaintext key produces a different hash each time. You cannot do `WHERE a.api_key_hash = $hash` in Cypher with bcrypt because there's no way to reproduce the stored hash from the plaintext key alone.

**Impact:** The implementation will be forced to either:
- (a) Fetch all agents and iterate with `bcrypt.checkpw()` — O(n), unacceptable at scale.
- (b) Add a deterministic lookup key (e.g., SHA-256 of the API key) stored alongside the bcrypt hash — the lookup key finds the agent, bcrypt verifies the key.
- (c) Replace bcrypt with a deterministic hash — loses the salting benefit.

**Recommendation:** Option (b) is the correct approach. Add `api_key_lookup` (SHA-256 hex digest) to Agent nodes for Cypher-level lookup, retain `api_key_hash` (bcrypt) for verification. Update the spec §3.1 authentication flow to reflect this two-step process. The test fixtures already store `api_key_hash` as bcrypt — they'll need a `api_key_lookup` field too.

---

### F2 — Missing Error Classes in `errors.py`
**Severity: HIGH**

The test suite imports three error types that **do not exist** in `errors.py`:
- `AgentDisabledError`
- `AgentNotFoundError`
- `HeartbeatTokenMismatchError`

`errors.py` currently only contains MVP Intent errors (`AuthenticationError`, `CapabilityNotFoundError`, etc.).

**Impact:** All heartbeat tests will fail with `ImportError` before reaching any test logic. This is expected in TDD, but the error classes should be added to `errors.py` as part of B4 implementation — they are a prerequisite.

**Recommendation:** Add the three error classes to `errors.py` before or at the start of B4 implementation. Keep them in the same module rather than creating a separate heartbeat errors file — the existing pattern groups all custom exceptions together.

---

### F3 — `HeartbeatSDK` Import: Separate Class Decision Not Documented
**Severity: MEDIUM**

Tests import `HeartbeatSDK` from `hassaleh.heartbeat_sdk`, a module that does not exist. The existing `HassalehSDK` in `sdk.py` has no `heartbeat()` method.

The spec (§3.1) describes `heartbeat()` as an SDK method but doesn't specify whether it belongs on the existing `HassalehSDK` or a new class.

**Impact:** The test suite has implicitly decided on a **separate `HeartbeatSDK` class** in a new module. This architectural decision should be explicit. The separate class approach is reasonable (heartbeat has its own auth model and writes directly to Neo4j, unlike the read-only `HassalehSDK`), but it means:
- `HeartbeatSDK` will need its own Neo4j connection management (duplicated from `HassalehSDK.connect()`/`close()`)
- Two SDK classes for agents to manage
- The `heartbeat()` method bypasses the `BLOCKED_KEYWORDS` write guard in `HassalehSDK.query()` by design (heartbeats perform writes)

**Recommendation:** Document this decision in the spec or architecture doc. The separation is justified — heartbeat is a privileged write operation that shouldn't go through the read-only SDK — but it should be an explicit design choice, not an accident of the TDD test structure.

---

### F4 — `test_no_api_key_raises_auth_error` Accepts `TypeError`
**Severity: MEDIUM**

```python
with pytest.raises((AuthenticationError, TypeError)):
    await sdk.heartbeat(None, heartbeat_token=None)
```

The spec (§3.1) says: "If no match → `AuthenticationError("Invalid API key")`." It does not permit `TypeError`. Accepting `TypeError` as valid means the implementation could leak internal details (e.g., `"NoneType object has no attribute 'encode'"`) instead of a clean auth error.

**Recommendation:** The test should only accept `AuthenticationError`. The implementation must validate `api_key is not None` and `isinstance(api_key, str)` before any hashing or DB lookup, raising `AuthenticationError` for all invalid inputs.

---

### F5 — No Validation of `source` Parameter
**Severity: MEDIUM**

The spec (§6.4) defines only two valid `heartbeat_source` values: `"agent"` and `"cron"`. The test suite tests both values but **never tests that invalid source values are rejected**. A caller could pass `source="admin"`, `source=""`, or `source="<script>alert(1)</script>"` — all would presumably be written to the Agent node without validation.

**Impact:** The `heartbeat_source` property becomes an unvalidated string field written to Neo4j. While not directly exploitable (Neo4j doesn't execute stored strings), it pollutes data quality and could confuse downstream Rule engine logic that switches on source values.

**Recommendation:** Add a test that verifies invalid source values raise `ValueError`. The implementation should validate `source in ("agent", "cron")` and default to `"agent"` if not provided.

---

### F6 — `AgentNotFoundError` Imported But Never Tested
**Severity: LOW**

`AgentNotFoundError` is imported (line 42) but no test raises it. The spec lists it as an error case: "Agent not found → `AgentNotFoundError`". However, since `agent_id` is derived from `api_key_hash`, a missing agent would be caught by the auth step as `AuthenticationError`.

**Impact:** The error type exists in the import list but is unreachable under normal conditions. If the implementation includes a code path that raises it (e.g., race condition where agent is deleted between auth and heartbeat write), there's no test coverage.

**Recommendation:** Either (a) add a test that deletes the agent after auth but before heartbeat write and asserts `AgentNotFoundError`, or (b) remove the import if the error is genuinely unreachable via the heartbeat path. Option (b) is cleaner — auth failure covers this case.

---

### F7 — No Connection Error Test Coverage
**Severity: LOW**

The spec lists `ConnectionError` as an error case for Neo4j connection failures. No test covers this scenario. While integration tests inherently can't easily simulate Neo4j failures, a unit test with a mocked driver could verify error propagation.

**Recommendation:** Add a unit test (non-integration) that mocks the Neo4j driver to raise a connection error and verifies the SDK surfaces it correctly. Low priority — this is infrastructure-level error handling.

---

### F8 — Rate-Limited Heartbeat Token Validation Ambiguity
**Severity: LOW**

`test_rapid_heartbeat_silently_accepted` passes the new `heartbeat_token` from `r1` to the rate-limited `r2` call. This is correct behavior — the token should be validated even for rate-limited requests (otherwise rate limiting could bypass token checks by sending rapid requests with stale tokens).

However, the spec is silent on whether token validation occurs for rate-limited heartbeats. The test **implicitly specifies** that token validation happens first, then rate limiting. If the implementation checks rate limiting first and skips token validation, the rate-limiting window becomes a replay window.

**Recommendation:** Add an explicit test: send a rate-limited heartbeat with a **wrong** token. Expected behavior:
- If token is checked first: `HeartbeatTokenMismatchError` (secure)
- If rate limit is checked first: silent success with stale data (insecure — creates a replay window)

The secure behavior (token check first) should be the documented expectation. Add this to the spec as a clarification under §3.1 rate limiting.

---

### F9 — Test Fixtures Use Real Neo4j — No CI Isolation
**Severity: LOW**

All fixtures connect to `bolt://localhost:7690` and perform `MERGE`/`DETACH DELETE` operations on Agent nodes. Test agent IDs (`hb-test-agent`, `hb-active-agent`, etc.) could collide with real data if tests run against a non-test database.

The fixtures use `MERGE` (good — idempotent) and clean up with `DETACH DELETE` (good — thorough). But there's no database-level isolation (no separate database, no transaction rollback).

**Recommendation:** For CI, consider using a dedicated Neo4j test database or namespace. For now, the `hb-` prefix convention provides sufficient isolation. Low risk given this is a development environment.

---

## Spec Compliance Matrix

| Spec Section | Test Coverage | Status |
|---|---|---|
| §7.1 First Heartbeat | `TestFirstHeartbeat` (6 tests) | **Covered** |
| §7.2 Subsequent Heartbeat | `TestSubsequentHeartbeat` (5 tests) | **Covered** |
| §7.3 Stale Detection | `TestStaleDetection` (3 tests) | **Covered** |
| §7.4 Recovery from Stale | `TestRecoveryFromStale` (3 tests) | **Covered** |
| §7.5 Inactive Detection | `TestInactiveDetection` (2 tests) | **Covered** |
| §7.6 Unknown Agent | `TestUnknownAgent` (2 tests) | **Covered** |
| §7.7 Concurrent Heartbeats | `TestConcurrentHeartbeats` (2 tests) | **Covered** |
| §7.8 Authentication Required | `TestAuthenticationRequired` (2 tests) | **Covered** (F4 noted) |
| §7.9 Token Chain | `TestHeartbeatTokenChain` (4 tests) | **Covered** |
| §7.10 Disabled Agent | `TestDisabledAgentRejection` (3 tests) | **Covered** |
| §7.11 Rate Limiting | `TestRateLimiting` (4 tests) | **Covered** (F8 noted) |
| §7.12 Source Tracking | `TestHeartbeatSource` (3 tests) | **Covered** (F5 noted) |
| Edge Cases | `TestEdgeCases` (3 tests) | **Good additional coverage** |

**Total: 42 tests across 13 test classes.** All spec scenarios have test coverage.

---

## Strengths

1. **Thorough TDD approach.** Tests are written before implementation, with clear imports from non-existent modules that will guide the implementation structure.
2. **Good fixture design.** Each lifecycle state (pending, active, stale, inactive, disabled) has its own fixture with appropriate seeding. Cleanup is thorough.
3. **Rate-limiting tests are clever.** Backdating `last_heartbeat` via direct Neo4j writes avoids real `time.sleep(60)` calls. This is the right approach for integration tests.
4. **Edge cases cover the full lifecycle.** `test_heartbeat_chain_survives_stale_recovery` exercises the complete active→stale→recovered→active chain, which is the most complex real-world scenario.
5. **Concurrent test verifies token uniqueness.** `test_no_data_corruption_across_agents` checks that all 4 agents get distinct tokens — catches cross-agent data corruption.

---

## Findings Summary

| # | Finding | Severity | Action |
|---|---------|----------|--------|
| F1 | Bcrypt vs. deterministic hash — spec/auth mismatch | HIGH | Resolve before B4: add SHA-256 lookup key |
| F2 | Missing error classes in `errors.py` | HIGH | Add to `errors.py` in B4 |
| F3 | `HeartbeatSDK` as separate class — undocumented decision | MEDIUM | Document in architecture |
| F4 | Test accepts `TypeError` alongside `AuthenticationError` | MEDIUM | Fix test: only accept `AuthenticationError` |
| F5 | No validation test for `source` parameter | MEDIUM | Add test + spec clarification |
| F6 | `AgentNotFoundError` imported but never tested | LOW | Remove import or add race-condition test |
| F7 | No `ConnectionError` test coverage | LOW | Add unit test with mocked driver |
| F8 | Rate-limited heartbeat token validation ambiguity | LOW | Add explicit test; clarify spec |
| F9 | Test fixtures share database — no CI isolation | LOW | `hb-` prefix is sufficient for now |

---

## Verdict

**APPROVED_WITH_NOTES**

The test suite is well-designed and provides strong coverage of spec v1.1. The TDD approach is disciplined — tests clearly define the expected behavior for all spec scenarios.

**F1 (bcrypt/hash mismatch) is the critical item** that must be resolved before B4 implementation begins, as it affects the fundamental authentication flow. F2 (missing error classes) is straightforward to address.

All other findings are improvements that strengthen the test suite and can be addressed during or after B4 implementation without blocking progress.

---

*Reviewed by Dione, 2026-04-15. Security focus areas informed by Inanna's original spec review (§9).*
