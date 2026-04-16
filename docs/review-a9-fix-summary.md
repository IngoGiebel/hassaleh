# A9 — Fix Summary: A7 Security + A8 Quality Review Findings

**Author:** Dione
**Date:** 2026-04-16
**Scope:** `intent_sdk.py`, `intent_daemon.py`, `errors.py`, `capabilities/exec_ls.py`
**Input:** `docs/review-a7-security-code-review.md`, `docs/review-a8-quality-code-review.md`

---

## Fixed Findings

### HIGH

| # | Finding | Fix |
|---|---------|-----|
| S1 | TOCTOU in `transition_intent()` — read and write in separate sessions | **Rewritten (A9).** The atomic CAS query now runs *first* — no pre-read. If the CAS succeeds, return immediately. If it fails (returns nothing), a diagnostic read determines the cause (intent not found vs. invalid transition). The diagnostic read is post-failure and cannot influence any mutation, eliminating the TOCTOU window entirely. |
| Q1 | `execute_ls()` dead code — daemon reimplements inline | `process_intent()` now calls `execute_ls(resolved_path, timeout)` from `capabilities/exec_ls.py` instead of running `subprocess.run` inline. Error handling catches `subprocess.TimeoutExpired` and `RuntimeError` (raised by `execute_ls` on non-zero exit). The daemon no longer imports `OUTPUT_TRUNCATION_LIMIT` — truncation is handled inside the capability module. |

### MEDIUM

| # | Finding | Fix |
|---|---------|-----|
| S3 | Auth query crosses all agents regardless of lifecycle | `_authenticate()` query now includes `AND a.lifecycle IN ['active', 'running']`. Disabled or circuit-broken agents are no longer authenticatable. |
| S4 | `process_intent` doesn't verify `claimed_by` before running | The `SET status = 'running'` query now matches `claimed_by: $daemon_id`. If another daemon claimed the intent, the query returns nothing and the daemon logs a warning and skips processing. |
| S6 | `capability_id` not validated as safe identifier | Added `_CAPABILITY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")`. `submit_intent()` validates `capability_id` against this pattern before any database interaction. |
| S7 | `params` dict has no size limit | Added `MAX_PARAMS_SIZE = 64 * 1024`. `submit_intent()` serializes params to JSON and rejects payloads exceeding 64 KB before any database interaction. The serialized JSON is reused for the `CREATE` query (no double-serialize). |
| Q2 | No async context manager on IntentSDK / IntentDaemon | Both classes now support `async with IntentSDK(...) as sdk:` and `async with IntentDaemon(...) as daemon:` via `__aenter__`/`__aexit__`. |
| Q3 | Duplicate ownership-check boilerplate | Extracted `_get_owned_intent(api_key, intent_id) -> dict` which handles auth, query, and ownership verification. `get_intent_status()` and `get_intent_result()` are now thin wrappers that select fields from the returned node dict. |
| Q4 | `driver: Any` weak typing | Both classes now declare `self.driver: AsyncDriver | None = None` with proper import from `neo4j`. |
| Q5 | Three separate sessions in `submit_intent()` | Merged capability existence check and agent permission check into a single query using `OPTIONAL MATCH`. Down from 4 sessions to 3 (auth + capability check + create). |
| Q6 | `_get_driver()` dead code | Removed entirely. `transition_intent()` handles its own temp driver inline. |
| Q7 | Hardcoded capability dispatch | Added a comment in `process_intent()` documenting the MVP limitation and the planned handler-registry dispatch pattern for multi-capability support. |

### Additional (bonus, while in the files)

| # | Finding | Fix |
|---|---------|-----|
| S11 (LOW) | `OPTIONAL MATCH` in ownership check | `_get_owned_intent()` uses `MATCH` (not `OPTIONAL MATCH`) for the `SUBMITTED_BY` edge. Orphaned intents without this edge now fall through to the "not found" path instead of producing a misleading "does not belong to caller" error. |
| Q10 (LOW) | `errors.py` docstring scope too narrow | Updated to `"""Hassaleh custom exceptions."""` |
| Q12 (LOW) | `params: dict` missing type parameter | Changed to `params: dict[str, Any]`. |
| Q13 (LOW) | `connect()` uses `assert` for verification | Both classes now use `if not record or record["ping"] != 1: raise ConnectionError(...)` instead of `assert`. |

---

## Deferred Findings (accepted for MVP)

| # | Finding | Severity | Reason |
|---|---------|----------|--------|
| S2 | Full agent table scan for bcrypt auth | HIGH | Requires key-prefix lookup scheme or HMAC pre-filter. Deferred to pre-production hardening. `lookup_hash()` already exists in `auth.py` for this purpose. |
| S5 | Error messages expose internal paths | MEDIUM | Acknowledged as F9 in spec. Accept for MVP; sanitize in hardening phase. |
| S8 | No rate limiting on SDK operations | MEDIUM | Acknowledged in spec §9. Accept for MVP; add per-agent token bucket in hardening phase. |

---

## Files Changed

| File | Changes |
|------|---------|
| `src/hassaleh/intent_daemon.py` | S1 atomic transition, Q1 use `execute_ls()`, S4 `claimed_by` guard, Q2 context manager, Q4 typing, Q6 remove dead code, Q7 dispatch comment, Q13 assert→raise |
| `src/hassaleh/intent_sdk.py` | S3 auth scope, S6 capability_id validation, S7 params size limit, Q2 context manager, Q3 ownership helper, Q4 typing, Q5 merged queries, Q12 params typing, Q13 assert→raise, S11 MATCH for ownership |
| `src/hassaleh/errors.py` | Q10 docstring fix |
| `src/hassaleh/capabilities/exec_ls.py` | No changes (already correct — daemon now calls it) |
| `src/hassaleh/auth.py` | No changes |
