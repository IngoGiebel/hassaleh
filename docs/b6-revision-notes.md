# B6 Revision Notes — Heartbeat Implementation (Addressing B5 Code Review)

**Date:** 2026-04-16
**Reviewer findings addressed:** docs/review-b5-heartbeat-code-review.md
**Spec:** docs/spec-heartbeat.md v1.1

---

## Files Modified

| File | Change |
|------|--------|
| `src/hassaleh/errors.py` | Added `AgentNotFoundError`, `AgentDisabledError`, `HeartbeatTokenMismatchError` |
| `src/hassaleh/auth.py` | Added `lookup_hash()` — SHA-256 deterministic hash for Cypher-level agent lookup |
| `src/hassaleh/heartbeat_sdk.py` | **New file** — `HeartbeatSDK` class with full heartbeat implementation |
| `tests/test_heartbeat.py` | Updated fixtures, fixed tests, added new tests per review findings |

---

## Findings Addressed

### F1 — Bcrypt vs. Deterministic Hash (HIGH) ✅

**Problem:** Bcrypt is non-deterministic (random salt). Can't do `WHERE a.api_key_hash = $hash` in Cypher.

**Solution:** Two-step authentication:
1. `lookup_hash(api_key)` → SHA-256 hex digest stored as `api_key_lookup` on Agent nodes. Used for O(1) Cypher lookup: `WHERE a.api_key_lookup = $lookup`.
2. `verify_api_key(api_key, agent.api_key_hash)` → bcrypt verification after lookup.

**Files:** `auth.py` (added `lookup_hash()`), `heartbeat_sdk.py` (uses two-step auth), all test fixtures (seed `api_key_lookup` alongside `api_key_hash`).

### F2 — Missing Error Classes (HIGH) ✅

**Problem:** Tests imported `AgentDisabledError`, `AgentNotFoundError`, `HeartbeatTokenMismatchError` — none existed in `errors.py`.

**Solution:** Added all three to `errors.py` under a heartbeat section, following the existing pattern of grouping all custom exceptions in one module.

### F3 — HeartbeatSDK as Separate Class (MEDIUM) ✅

**Decision documented:** `HeartbeatSDK` is a separate class in `heartbeat_sdk.py`, not a method on `HassalehSDK`. Rationale:
- Heartbeat performs privileged writes; `HassalehSDK` is read-only + Intent submission.
- Heartbeat bypasses `BLOCKED_KEYWORDS` write guard by design.
- Separate connection lifecycle keeps concerns clean.
- Has its own auth model (API key + chained token).

### F4 — Test Accepts TypeError (MEDIUM) ✅

**Problem:** `test_no_api_key_raises_auth_error` accepted `(AuthenticationError, TypeError)`.

**Solution:** Changed to only accept `AuthenticationError`. The implementation validates `isinstance(api_key, str)` and `api_key` is truthy before any hashing, raising `AuthenticationError` for `None`, empty string, and non-string inputs.

### F5 — No Validation of Source Parameter (MEDIUM) ✅

**Problem:** No test or validation for invalid `heartbeat_source` values.

**Solution:**
- Implementation validates `source in ("agent", "cron")`, raises `ValueError` for anything else.
- Added two new tests: `test_invalid_source_rejected` and `test_empty_source_rejected`.

### F6 — AgentNotFoundError Imported But Never Tested (LOW) ✅

**Problem:** `AgentNotFoundError` was imported in tests but never used.

**Solution:** Removed the import. The auth step (SHA-256 lookup + bcrypt verify) covers the "agent not found" case — if the key doesn't match any agent, `AuthenticationError` is raised. `AgentNotFoundError` is defined in `errors.py` for potential future use (e.g., race condition where agent is deleted between auth and heartbeat write).

### F8 — Rate-Limited Heartbeat Token Validation (LOW) ✅

**Problem:** Spec silent on whether token validation occurs for rate-limited heartbeats. If rate limiting is checked first, the rate-limiting window becomes a replay window.

**Solution:**
- Implementation checks token **before** the Cypher write (which includes the rate-limit WHERE clause). Token mismatch is caught at the Python level before any DB interaction.
- Added `test_wrong_token_rejected_even_when_rate_limited` — sends a wrong token during the rate-limit window, asserts `HeartbeatTokenMismatchError`.

### F7 — No ConnectionError Test (LOW) — Deferred

Not addressed in this pass. Would require mocking the Neo4j driver, which is a unit test concern rather than integration test. Tracked for future hardening.

### F9 — Test Fixtures Share Database (LOW) — Acceptable

The `hb-` prefix convention and `DETACH DELETE` cleanup in fixtures provide sufficient isolation for the development environment. No changes needed.

---

## Additional Fix: Neo4j Null Property Handling

**Problem:** The `get_agent_node` helper used `dict(record["a"])` which omits null-valued properties (Neo4j removes null properties from nodes). Tests asserting `node["heartbeat_token"] is None` got `KeyError`.

**Solution:** Changed `get_agent_node` to use an explicit `RETURN` with named fields, so Cypher returns `null` as Python `None` rather than omitting the key.

---

## Test Results

```
47 passed in 38.70s
```

All 47 tests pass (42 original + 3 new tests for F5 and F8 + 2 existing tests for inactive recovery).
