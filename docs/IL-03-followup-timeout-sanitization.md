# IL-03 Follow-up: `wait_for_intent` Timeout Message Sanitization

**Date:** 2026-04-19
**Trigger:** CHANGES REQUESTED on IL-03 security review — residual leak
flagged by Inanna in `docs/IL-03-security-review.md`.

## What Changed

The `TimeoutError` raised when `wait_for_intent` reaches its deadline used
to embed caller-supplied data in the exception message:

```python
# before (leaked intent_id + timeout_sec)
raise TimeoutError(
    f"Intent {intent_id} did not complete within {timeout_sec}s"
)
```

This exposed:

1. The caller's `intent_id` (same enumeration surface the IL-03 sanitized
   `PermissionError` was designed to close).
2. The caller's `timeout_sec` value (a weaker fingerprint, but still
   observable metadata about client behavior).

The message is now a fixed static string with no interpolated values, matching
the tone of the sanitized `PermissionError` in `poll_intent`:

```python
# after
raise TimeoutError("intent did not complete before deadline")
```

**File / line:** `src/hassaleh/sdk.py:403` (was `:403-405`).

## Regression Test

A new async test guards the message shape:

- **Name:** `test_wait_for_intent_timeout_message_is_sanitized`
- **Location:** `tests/test_sdk.py`
- **Path driven:** intent exists and is owned by the caller, but its
  lifecycle stays `"pending"`, so the `while deadline` loop exhausts and
  raises `TimeoutError`.
- **Assertions:**
  - `str(exc) == "intent did not complete before deadline"` (byte-equal,
    not regex `match=`, so extra leaked data would fail the test).
  - `intent_id not in str(exc)` — explicit negative check.
  - `str(timeout_sec) not in str(exc)` — explicit negative check.

### Mock fixture note

The stock `AsyncResultMock` iterator is single-use (`_index` never resets),
so `session.run = AsyncMock(return_value=...)` would exhaust after the first
poll and fall through into the ownership-`PermissionError` path. The test
uses `side_effect=factory` to return a fresh result per poll cycle.

## Pytest Output Summary

Command: `pytest tests/test_sdk.py -k wait_for_intent -v`

```
tests/test_sdk.py::test_wait_for_intent_enforces_ownership PASSED                           [ 16%]
tests/test_sdk.py::test_wait_for_intent_sanitizes_not_found_vs_not_owned PASSED             [ 33%]
tests/test_sdk.py::test_wait_for_intent_empty_api_key_raises_without_graph_touch PASSED     [ 50%]
tests/test_sdk.py::test_wait_for_intent_inactive_agent_cannot_read_prior_intent PASSED      [ 66%]
tests/test_sdk.py::test_wait_for_intent_requires_api_key_positional PASSED                  [ 83%]
tests/test_sdk.py::test_wait_for_intent_timeout_message_is_sanitized PASSED                 [100%]

6 passed, 20 deselected in 0.53s
```

All 6 `wait_for_intent` tests pass, including the new timeout-sanitization
guard. Full suite was **not** re-run per task scope.

## Scope Boundary

No other files were touched. The docstring at `sdk.py:393` still documents
`TimeoutError` generically (`"Intent did not complete within timeout_sec"`)
— that describes the **behavior**, not the exception string, and does not
need updating.
