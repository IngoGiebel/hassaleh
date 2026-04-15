# Security Review: MVP Intent Pipeline Tests (Phase A5)

**Reviewer:** Inanna
**Date:** 2026-04-15
**Test file:** `tests/test_mvp_intent.py`
**Spec:** `docs/spec-mvp-test.md` v1.1 (Post-Review Revision)
**Scope:** Coverage completeness, missing edge cases, test quality, spec compliance

---

## Verdict: APPROVED_WITH_NOTES

The test suite is **structurally sound** and demonstrates clear understanding of the security requirements from the spec. All CRITICAL and HIGH findings from the A3 security review (F1-F5) have corresponding test coverage. The TDD approach is well-executed: imports target modules that don't exist yet, and failure modes will be clear.

However, there are **7 issues** that should be addressed before the implementation phase begins, ranging from a false-pass risk in an authentication test to missing spec scenarios. None are blocking, but several could allow bugs to slip through if left as-is.

---

## 1. Coverage Checklist

### Spec §3A — Path Validation (exec-ls)

| Requirement | Test(s) | Status |
|---|---|---|
| Step 1: Regex `^[a-zA-Z0-9/_.\-]+$` | `test_rejects_null_bytes`, `_non_printable`, `_control_sequences`, `_spaces`, `_semicolons`, `_pipes`, `_backticks`, `_accepts_valid_characters` | PASS |
| Step 2: Raw `..` segment rejection | `test_rejects_dotdot_segment`, `_leading_dotdot`, `_trailing_dotdot`, `_dotdot_embedded_in_name_is_ok` | PASS |
| Step 3: `os.path.realpath()` canonicalization | `test_symlink_escape_rejected` | PASS |
| Step 4: Prefix check against allowlist | `test_rejects_path_outside_allowlist`, `_accepts_path_under_allowed_base`, `_allowed_base_itself_is_permitted` | PASS |
| Step 5: `os.path.isdir()` existence check | `test_rejects_nonexistent_path`, `_rejects_file_not_directory` | PASS |
| Output: 64 KB stdout truncation | `test_stdout_truncated_at_64kb` | PASS |
| Output: 10,000 entry limit | *(none)* | **MISSING** |
| Execution: no `shell=True` | `test_no_shell_true` | PASS (with quality caveat, see §3) |
| Execution: list-form invocation | `test_uses_list_form_invocation` | PASS (with quality caveat, see §3) |

### Spec §3B — Authentication

| Requirement | Test(s) | Status |
|---|---|---|
| `secrets.token_urlsafe(32)` generation | `test_generate_api_key_is_url_safe` | PASS |
| bcrypt hash + verify round-trip | `test_hash_and_verify_round_trip`, `test_hash_is_bcrypt_format` | PASS |
| Wrong key rejected | `test_verify_rejects_wrong_key` | PASS |
| Invalid API key → `AuthenticationError` | `test_invalid_api_key_raises` | PASS |
| Failed auth creates no Intent | `test_invalid_key_creates_no_intent` | **WEAK** (see §3.1) |
| `agent_id` derived server-side | `test_agent_id_derived_server_side` | PASS |
| Empty/None API key | *(none)* | **MISSING** |

### Spec §4 — SDK Interface

| Method / Requirement | Test(s) | Status |
|---|---|---|
| `submit_intent` returns UUID | `test_happy_path_returns_uuid` | PASS |
| Intent node properties (§4.1) | `test_creates_pending_intent_in_neo4j` | PASS |
| `SUBMITTED_BY` edge | `test_creates_submitted_by_edge` | PASS |
| `REQUIRES` edge | `test_creates_requires_edge` | PASS |
| Timestamps set | `test_timestamps_are_set` | PASS |
| `CapabilityNotFoundError` | `test_unknown_capability_raises` | PASS |
| `CapabilityDeniedError` | `test_agent_lacks_capability_raises` | PASS |
| `CapabilityParamError` at submit | `test_traversal_intent_fails_with_param_error` | PASS |
| `ConnectionError` | *(none)* | **MISSING** (minor — infra error) |
| `get_intent_status` returns dict | `test_returns_pending_status` | PASS |
| `get_intent_status` nonexistent | `test_nonexistent_intent_raises` | PASS |
| `get_intent_result` — still processing | `test_still_processing_returns_none_result` | PASS |
| `get_intent_result` — success response | *(none)* | **MISSING** |
| `get_intent_result` — failure response | *(none)* | **MISSING** |
| `get_intent_result` — `duration_ms` set | *(none)* | **MISSING** |

### Spec §5 — Daemon Processing

| Requirement | Test(s) | Status |
|---|---|---|
| §5.1 Valid transitions | `test_valid_transition_accepted` (parametrized ×5) | PASS |
| §5.1 Invalid transitions | `test_invalid_transition_rejected` (parametrized ×5) | PASS |
| §5.1 Terminal immutability | `test_terminal_states_are_immutable` | PASS |
| §5.2 Atomic claim + cap check | `test_atomic_claim_with_capability_check` | PASS |
| §5.2 Rejection on missing cap | `test_claim_fails_for_missing_capability` | PASS (with data issue, see §3.4) |
| §5.2 `claimed_by` recorded | `test_claim_records_daemon_instance_id` | PASS |
| §5.3 Timeout → failed | `test_execution_timeout` | PASS |
| §5.4 Max 3 concurrent | `test_max_concurrent_intents` | **WEAK** (see §3.5) |
| §5.5 Reaper: stale claimed | `test_reaper_fails_stale_claimed_intents` | PASS |
| §5.5 Reaper: stale running | `test_reaper_fails_stale_running_intents` | PASS |
| §5.5 Reaper: error message | `test_reaper_error_message_is_informative` | PASS |
| §5.5 Startup recovery | `test_startup_recovery_fails_own_orphans` | PASS |
| §5.5 Fresh intents safe | `test_reaper_does_not_touch_fresh_intents` | PASS |

### Spec §6 & §8 — Test Scenarios

| Scenario | Test(s) | Status |
|---|---|---|
| §6.1 Happy path (end-to-end) | Partial: submit + pending verified | **INCOMPLETE** — no e2e through to success |
| §6.2 Missing capability | `test_agent_lacks_capability_raises` | PASS |
| §6.3 Unknown agent (→ AuthN) | `test_invalid_api_key_raises` | PASS (correctly maps to AuthenticationError under new model) |
| §6.4 Unknown capability | `test_unknown_capability_raises` | PASS |
| §6.5 Execution failure | *(none)* | **MISSING** |
| §6.6 Concurrent intents | `test_concurrent_submissions_all_processed` | PASS |
| §6.7 Status polling | `test_updated_at_changes_with_transitions` | PASS (partial) |
| §8.1 Path traversal | `test_traversal_intent_fails_with_param_error`, `test_no_listing_returned_on_traversal` | PASS |
| §8.2 Auth failure | `test_invalid_api_key_raises`, `test_invalid_key_creates_no_intent` | PASS |
| §8.3 Cross-agent read | `test_status_blocked_for_other_agent`, `test_result_blocked_for_other_agent` | PASS |
| §8.4 Orphan recovery | `test_reaper_fails_stale_claimed_intents`, `test_startup_recovery_fails_own_orphans` | PASS |
| §8.5 Atomic claim concurrency | `test_atomic_claim_prevents_double_processing` | PASS |

---

## 2. Missing Tests

### 2.1 Execution Failure (§6.5) — SHOULD ADD

The spec explicitly lists this as a test scenario: *"Submit Intent with `exec-ls` on non-existent path → Assert Intent becomes `failed` → Assert error message is meaningful."* There is no test that submits an intent for a non-existent directory and verifies the full pipeline produces a `failed` intent with a meaningful error. The path validation unit tests catch nonexistent paths at the validator level, but no integration test confirms this is wired into the daemon processing path (§5.2 step 3: "If validation fails → `status = "failed"`, `error = "Parameter validation failed: <reason>"`").

### 2.2 Success/Failure Result Responses (§4.3) — SHOULD ADD

`get_intent_result` only tests the "still processing" response shape. The spec defines three distinct response shapes:
- **Success:** `result` is a string, `error` is None, `duration_ms` is an integer
- **Failure:** `result` is None, `error` is a string, `duration_ms` is an integer
- **Processing:** `result` and `error` are None, `duration_ms` is None

Only the third is tested. The success and failure shapes are important because `duration_ms` must be populated in terminal states — this is untested.

### 2.3 10,000 Entry Truncation (§3A) — SHOULD ADD

The `MAX_DIR_ENTRIES` constant is defined at line 68 but never referenced in any test. The spec says: *"If the directory contains more than 10,000 entries, truncate and note in output."* No test verifies this behavior.

### 2.4 Empty/Missing Path Parameter — SHOULD ADD

No test covers `params={}` (missing `path` key) or `params={"path": ""}` (empty string). These are the simplest malformed inputs and should be rejected with `CapabilityParamError`.

### 2.5 Empty API Key — MINOR

No test covers `api_key=""` or `api_key=None`. Bcrypt will produce a hash for empty string, so an implementation could accidentally authenticate if there's an Agent with an empty-string key hash. Low risk since `generate_api_key()` produces long keys, but worth a defensive test.

---

## 3. Test Quality Concerns

### 3.1 False-Pass Risk: `test_invalid_key_creates_no_intent` (line 471) — FIX RECOMMENDED

**Problem:** The query checks for intents linked to `test-dione` via `SUBMITTED_BY`:

```python
MATCH (i:Intent)-[:SUBMITTED_BY]->(:Agent {id: 'test-dione'})
WHERE i.status = 'pending'
RETURN count(i) AS cnt
```

But a bogus API key should not resolve to `test-dione` at all. A buggy implementation could create an orphaned Intent node with NO `SUBMITTED_BY` edge — this query would still return `cnt == 0`, giving a false pass.

**Fix:** Query for ANY Intent nodes created in the test window, not just those linked to test-dione:

```python
# Check for orphaned intents (no SUBMITTED_BY edge)
MATCH (i:Intent)
WHERE NOT (i)-[:SUBMITTED_BY]->()
  AND i.created_at > datetime() - duration({seconds: 5})
RETURN count(i) AS cnt
```

### 3.2 Source-Inspection Tests Are Brittle (lines 1269-1292)

**Problem:** `TestExecutionSafety` uses `inspect.getsource()` and checks for literal strings like `'["ls"'`. This is fragile:

- `cmd = ["ls", "-la", path]; subprocess.run(cmd)` — correct code, test fails
- `subprocess.run(["ls", f"-la"], shell=False)` — test passes but doesn't verify the claim
- Minified or compiled code would break `getsource()`

**Recommendation:** Replace with behavioral tests. Mock `subprocess.run`, call `execute_ls()`, and assert the mock was called with a list (not a string) and `shell` was not `True`:

```python
with patch("subprocess.run") as mock_run:
    mock_run.return_value = MagicMock(stdout="", stderr="", returncode=0)
    execute_ls("/app")
    args, kwargs = mock_run.call_args
    assert isinstance(args[0], list), "Must use list-form invocation"
    assert kwargs.get("shell") is not True, "Must not use shell=True"
```

### 3.3 Max-Concurrent Test Is a Constructor Check (line 939-949)

**Problem:** `test_max_concurrent_intents` only verifies `daemon.max_concurrent == 3`. This is an attribute assignment test, not a behavioral test. It does not verify that the daemon actually enforces the limit at runtime.

**Recommendation:** Submit 5 intents, start the daemon, and verify that at most 3 are in `running` state simultaneously (the others remain `claimed` or `pending` until a slot opens). This can be done by mocking the execution handler to block on an `asyncio.Event`, counting how many are running at peak.

### 3.4 Duplicate Capability Node in `test_claim_fails_for_missing_capability` (line 884)

**Problem:** The test seeds an intent with:
```cypher
CREATE (i)-[:REQUIRES]->(:Capability {id: 'exec-ls'})
```

This creates a **new** Capability node. The `two_agent_sdk` fixture already created `(:Capability {id: 'exec-ls'})`. Now there are two nodes with the same `id`. While the test still passes (agent-beta has no `HAS_CAPABILITY` edge to either), the duplicate node is a data integrity issue. It could confuse other tests or complicate cleanup.

**Fix:** Use `MATCH` instead of `CREATE` for the Capability:
```cypher
MATCH (cap:Capability {id: 'exec-ls'})
CREATE (i)-[:REQUIRES]->(cap)
```

### 3.5 State Transition Tests: Missing Daemon Lifecycle (lines 738-743)

The `test_valid_transition_accepted` and `test_invalid_transition_rejected` parametrized tests create an `IntentDaemon` but never call `connect()` or `close()`. If `transition_intent()` requires a database connection, these tests fail for the wrong reason (connection error vs. transition error). This is acceptable in TDD (the implementation will define the interface), but worth noting: the daemon may need lifecycle management or the method should accept a session.

---

## 4. Security Concerns

### 4.1 Authentication Timing Oracle — NOTED (not blocking)

The spec's auth flow (§3B step 2) queries the DB by hash: `MATCH (a:Agent) WHERE a.api_key_hash = $hash RETURN a.id`. The implementation must hash the input key with bcrypt FIRST, then query. But bcrypt is intentionally slow (~100ms). If the implementation short-circuits on clearly invalid inputs (empty string, wrong format) before calling bcrypt, the response time difference reveals whether the input "looked like" a valid key format. No test measures timing behavior.

**Impact:** Low for MVP (pre-shared keys in isolated environment). Flag for hardening phase.

### 4.2 Truncation Marker as Data (§3A)

The truncation marker `[output truncated at 64KB]` is appended to stdout. If an agent parses the result and treats the last line as a filename, it would interpret the marker as data. The spec should note that the marker is metadata, not a filename entry. No test verifies the marker is on its own line preceded by `\n` as the spec says: `\n[output truncated at 64KB]`.

**The test at line 1266** checks `result["result"].endswith("[output truncated at 64KB]")` — correct. But doesn't verify the `\n` prefix.

### 4.3 Fixture Cleanup Ordering — NOTED

If a test creates an intent and then the cleanup fixture runs but the daemon has claimed the intent concurrently, the `DETACH DELETE` in cleanup could race with daemon processing. This is unlikely in TDD (no real daemon runs during tests), but worth noting for future integration test runs with a live daemon.

---

## 5. Summary

**Strengths:**
- All 10 findings from the A3 security review (F1-F10) that are in-scope have test coverage
- TDD import structure is clean — will produce clear `ImportError` breadcrumbs
- Good use of parametrized tests for state transitions
- Cross-agent access tests (§8.3) are well-designed with proper two-agent fixture
- Reaper tests cover both directions: fails stale intents, leaves fresh ones alone
- Symlink escape test is a nice touch — many test suites miss this

**Weaknesses:**
- `test_invalid_key_creates_no_intent` has a false-pass risk (§3.1 above)
- Source-inspection tests for shell safety are brittle (§3.2)
- Three spec test scenarios have no coverage (§6.5 execution failure, §4.3 response shapes, §3A 10k entries)
- Max-concurrent test is purely structural, not behavioral

**Bottom line:** The test suite covers the security-critical paths well. The issues identified are quality improvements, not security gaps. The false-pass risk in §3.1 is the most actionable item — it should be fixed before implementation begins, because it could silently pass even with a buggy auth implementation.

---

**Verdict: APPROVED_WITH_NOTES**

Proceed to implementation. Address the notes (especially §3.1 false-pass fix and §2.1/§2.2 missing scenarios) either before or concurrently with the implementation phase. None of the issues require a full revision cycle.

— Inanna
