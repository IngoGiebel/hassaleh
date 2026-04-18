# B6 — Final Review: Heartbeat System

**Reviewer:** Dione
**Date:** 2026-04-16
**Spec:** docs/spec-heartbeat.md v1.1
**Revision notes:** docs/b6-revision-notes.md
**Prior review:** docs/review-b5-heartbeat-code-review.md

---

## B5 Findings — Resolution Status

### F1 — Bcrypt vs. Deterministic Hash (HIGH) — RESOLVED

`auth.py` now exports `lookup_hash()` which computes a SHA-256 hex digest for Cypher-level `WHERE a.api_key_lookup = $lookup`. The `HeartbeatSDK.heartbeat()` method performs two-step auth: SHA-256 lookup, then `verify_api_key()` (bcrypt) to confirm the key. All test fixtures seed both `api_key_lookup` and `api_key_hash` on Agent nodes. This is the correct approach — O(1) lookup without sacrificing bcrypt's salting benefit.

### F2 — Missing Error Classes (HIGH) — RESOLVED

`errors.py` now contains `AgentNotFoundError`, `AgentDisabledError`, and `HeartbeatTokenMismatchError` under a clearly labeled heartbeat section. Follows the existing single-module pattern for all custom exceptions.

### F3 — HeartbeatSDK as Separate Class (MEDIUM) — RESOLVED

`heartbeat_sdk.py` is a standalone module with its own `HeartbeatSDK` class. The separation is justified and now documented in the B6 revision notes: heartbeat performs privileged writes, bypasses `BLOCKED_KEYWORDS`, and has its own auth model (API key + chained token). Connection lifecycle is self-contained with `connect()`/`close()` and async context manager support.

### F4 — Test Accepts TypeError (MEDIUM) — RESOLVED

`test_no_api_key_raises_auth_error` now asserts only `AuthenticationError`. The implementation validates `isinstance(api_key, str) and api_key` at method entry (line 103), raising `AuthenticationError` before any hashing or DB interaction for `None`, empty, and non-string inputs.

### F5 — No Validation of Source Parameter (MEDIUM) — RESOLVED

Implementation validates `source in VALID_SOURCES` (frozen set: `{"agent", "cron"}`) at line 106, raising `ValueError` with a clear message. Two new tests added: `test_invalid_source_rejected` (source="admin") and `test_empty_source_rejected` (source=""). Test count increased from 42 to 47.

### F6 — AgentNotFoundError Imported But Never Tested (LOW) — RESOLVED

Import removed from tests. The class remains in `errors.py` for potential future use (race condition between auth and heartbeat write), but the test file no longer carries dead imports.

### F7 — No ConnectionError Test (LOW) — DEFERRED (Acceptable)

Not addressed. This is a unit-test concern requiring a mocked Neo4j driver — orthogonal to the integration test suite. Correctly tracked for future hardening. No objection to deferral.

### F8 — Rate-Limited Heartbeat Token Validation (LOW) — RESOLVED

Token validation occurs at the Python level (lines 139-148) before the Cypher write query. This ensures token mismatch is caught regardless of rate-limiting state. New test `test_wrong_token_rejected_even_when_rate_limited` confirms that a wrong token during the rate-limit window raises `HeartbeatTokenMismatchError`, not silent success. This closes the replay window concern.

### F9 — Test Fixtures Share Database (LOW) — ACCEPTABLE

`hb-` prefix convention and `DETACH DELETE` cleanup are sufficient for development. No change needed.

---

## Implementation Quality Assessment

### Architecture

- `HeartbeatSDK` is clean: 200 lines, single responsibility, clear separation from the read-only `HassalehSDK`.
- Auth model (SHA-256 lookup + bcrypt verify) is sound and consistent with the Intent pipeline's approach.
- Token chain validation happens at the Python layer before DB writes — correct ordering for security.
- Rate limiting is handled in the Cypher WHERE clause — no write occurs if the 60s interval hasn't elapsed, preventing write amplification.

### Security Controls Verified

| Control | Status | Evidence |
|---------|--------|----------|
| API key auth (F1) | Enforced | SHA-256 lookup + bcrypt verify before any write |
| Replay protection (F2) | Enforced | Chained token validated at Python level |
| Disabled agent guard (F6) | Enforced | `lifecycle == "disabled"` checked before token validation |
| Rate limiting (F4) | Enforced | Cypher WHERE clause prevents writes within 60s |
| Source validation (F5) | Enforced | `VALID_SOURCES` frozenset check |
| Input validation | Enforced | `isinstance(api_key, str) and api_key` guard |
| Token before rate limit (F8) | Enforced | Python-level token check precedes Cypher execution |

### Code Observations (Non-Blocking)

1. **Three separate sessions in the worst case.** A rate-limited heartbeat opens three sessions: auth lookup, write attempt (no match), read-back. This is functionally correct but could be consolidated into fewer sessions in a future optimization pass. Not a correctness issue.

2. **`AgentNotFoundError` is defined but unreachable.** With the two-step auth model, a missing agent is caught as `AuthenticationError` (no SHA-256 match). The error class exists as a safety net for a theoretical race condition. This is fine — it's a single class definition, not dead code paths.

---

## Test Results

```
47 passed in 52.97s
```

All 47 tests pass against live Neo4j (bolt://localhost:7690). Test classes cover all 12 spec scenarios plus edge cases. No failures, no warnings.

### Test Coverage Summary

| Test Class | Tests | Spec Section |
|---|---|---|
| TestFirstHeartbeat | 6 | 7.1 |
| TestSubsequentHeartbeat | 5 | 7.2 |
| TestStaleDetection | 3 | 7.3 |
| TestRecoveryFromStale | 3 | 7.4 |
| TestInactiveDetection | 2 | 7.5 |
| TestRecoveryFromInactive | 2 | 7.5+ |
| TestUnknownAgent | 2 | 7.6 |
| TestConcurrentHeartbeats | 2 | 7.7 |
| TestAuthenticationRequired | 2 | 7.8 |
| TestHeartbeatTokenChain | 4 | 7.9 |
| TestDisabledAgentRejection | 3 | 7.10 |
| TestRateLimiting | 5 | 7.11 |
| TestHeartbeatSource | 5 | 7.12 |
| TestEdgeCases | 3 | Full lifecycle |
| **Total** | **47** | |

---

## Verdict

**APPROVED**

All 8 actionable B5 findings have been addressed (7 resolved, 1 acceptably deferred). The implementation faithfully follows spec v1.1, the security controls from Inanna's original review are correctly enforced, and the test suite provides comprehensive coverage across all spec scenarios. The heartbeat system is ready for integration.

---

*Reviewed by Dione, 2026-04-16.*
