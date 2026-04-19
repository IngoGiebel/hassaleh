# IL-03 Security Review v2

**Verdict:** CLEAN

**Reviewer:** Inanna 🛡️  
**Date:** 2026-04-19  
**Target:** `src/hassaleh/sdk.py` IL-03 follow-up only  
**Scope:** Verify closure of the single residual flagged in `docs/IL-03-security-review.md` (timeout-message sanitization in `wait_for_intent()`), plus a narrow sanity sweep of nearby raise sites in the intent path.

## Summary

I re-checked the exact residual flagged in cycle 9: `wait_for_intent()` used to
raise a `TimeoutError` that interpolated caller-controlled `intent_id` and
`timeout_sec` into the exception message.

That residual is now closed.

The current implementation raises a static string literal:

```python
raise TimeoutError("intent did not complete before deadline")
```

No `f`-string, `.format()`, `%` formatting, or other caller-controlled data is
used in the exception path. The new regression test asserts byte-equality to the
sanitized string and explicitly checks that neither the raw `intent_id` nor the
string form of `timeout_sec` appears in the exception text. The requested narrow
pytest run passes.

**Ready to merge.**

---

## Check 1 — `sdk.py` timeout path is now a static string literal

**Result:** PASS

### Evidence

Inspected `src/hassaleh/sdk.py` in the requested window around the timeout path.
The relevant block now reads:

- `src/hassaleh/sdk.py:397-403` — loop / terminal return / timeout raise
- `src/hassaleh/sdk.py:403` — `raise TimeoutError("intent did not complete before deadline")`

Observed behavior:

- static string literal only
- no `f"..."`
- no `.format(...)`
- no `%` interpolation
- no inclusion of `intent_id`
- no inclusion of `timeout_sec`

This addresses the only concrete defect raised in the prior review.

---

## Check 2 — regression test covers byte-equality + negative assertions

**Result:** PASS

### Evidence

Located the new test in `tests/test_sdk.py`:

- `tests/test_sdk.py:627-671` — `test_wait_for_intent_timeout_message_is_sanitized`

Key assertions present:

- byte-equal sanitized message
  - `tests/test_sdk.py:669`
- explicit negative check for `intent_id`
  - `tests/test_sdk.py:670`
- explicit negative check for `timeout_sec`
  - `tests/test_sdk.py:671`

The test drives the intended path correctly:

- owned intent remains non-terminal (`"pending"`)
- `wait_for_intent()` exhausts the deadline
- `TimeoutError` is captured and inspected

This is the right regression guard for the issue I previously flagged.

---

## Check 3 — narrow pytest run

**Result:** PASS

### Command run

```bash
cd ~/projects/hassaleh && pytest tests/test_sdk.py -k wait_for_intent -v 2>&1 | tail -40
```

### Tail output

```text
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.0.3, pluggy-1.6.0 -- /home/uranus/projects/hassaleh/.venv/bin/python3
cachedir: .pytest_cache
rootdir: /home/uranus/projects/hassaleh
configfile: pyproject.toml
plugins: asyncio-1.3.0
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 26 items / 20 deselected / 6 selected

tests/test_sdk.py::test_wait_for_intent_enforces_ownership PASSED        [ 16%]
tests/test_sdk.py::test_wait_for_intent_sanitizes_not_found_vs_not_owned PASSED [ 33%]
tests/test_sdk.py::test_wait_for_intent_empty_api_key_raises_without_graph_touch PASSED [ 50%]
tests/test_sdk.py::test_wait_for_intent_inactive_agent_cannot_read_prior_intent PASSED [ 66%]
tests/test_sdk.py::test_wait_for_intent_requires_api_key_positional PASSED [ 83%]
tests/test_sdk.py::test_wait_for_intent_timeout_message_is_sanitized PASSED [100%]

======================= 6 passed, 20 deselected in 0.64s =======================
```

---

## Check 4 — quick sanity sweep of nearby raise sites in the intent path

**Result:** PASS (for IL-03 scope)

I performed the requested narrow grep/sanity sweep for `TimeoutError`,
`PermissionError`, and `ValueError` raise sites in the intent path region of
`src/hassaleh/sdk.py`.

Relevant raise sites found:

- `src/hassaleh/sdk.py:269` — `ValueError(...)` on invalid `target_label` in `submit_intent()`
- `src/hassaleh/sdk.py:365` — `PermissionError("intent not found or not authorized")` in `poll_intent()`
- `src/hassaleh/sdk.py:403` — `TimeoutError("intent did not complete before deadline")` in `wait_for_intent()`

Assessment for IL-03 v2 scope:

- `sdk.py:365` remains properly sanitized and unchanged.
- `sdk.py:403` is now properly sanitized.
- `sdk.py:269` is outside IL-03’s timeout/ownership leak and was not part of my
  prior finding; no new IL-03 blocker is introduced by its continued presence.

I also noted `query()` has a separate timeout raise at `sdk.py:233`
(`"Query exceeded {timeout_ms}ms timeout"`), but that is part of generic SDK
query behavior, not the IL-03 intent path under re-review, and was not flagged
in the original IL-03 review. Per scope lock, I am not expanding this into a
new audit item here.

---

## Check 5 — prior review item closure

**Result:** PASS

Cross-checked against `docs/IL-03-security-review.md`.

The prior review requested exactly two follow-ups:

1. sanitize the timeout exception in `wait_for_intent()`
2. add/update a regression test for timeout sanitization

Both are now present and verified:

- code fix: `src/hassaleh/sdk.py:403`
- regression test: `tests/test_sdk.py:627-671`

There were no additional IL-03 code-change requests in the prior review.
Everything I flagged there is now addressed.

---

## Explicit merge statement

**IL-03 is ready to merge.**

Within the locked IL-03 v2 scope, I found no remaining gaps after the timeout
sanitization follow-up. The previously flagged residual is closed, the new test
is adequate, and the targeted pytest run is green.

**Sign-off line:** Reviewed by Inanna, 2026-04-19.
