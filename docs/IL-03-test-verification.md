# IL-03 test verification

Date: 2026-04-18
Branch: `trunk`
Repo: `~/projects/hassaleh`
Neo4j: `bolt://localhost:7690`
Spec reviewed: `docs/IL-03-implementation.md`

## Summary

I reviewed `docs/IL-03-implementation.md`, added missing adversarial unit coverage in `tests/test_sdk.py`, and re-ran the IL-03 subset.

Result: **all IL-03 subset tests passed**.

## Tests added

Added to `tests/test_sdk.py`:

- `tests/test_sdk.py:480` — `test_wait_for_intent_sanitizes_not_found_vs_not_owned`
- `tests/test_sdk.py:511` — `test_poll_intent_empty_api_key_raises_without_graph_touch`
- `tests/test_sdk.py:530` — `test_wait_for_intent_empty_api_key_raises_without_graph_touch`
- `tests/test_sdk.py:554` — `test_poll_intent_inactive_agent_cannot_read_prior_intent`
- `tests/test_sdk.py:581` — `test_wait_for_intent_inactive_agent_cannot_read_prior_intent`

Existing relevant IL-03 coverage already present:

- `tests/test_sdk.py:380` — `test_poll_intent_rejects_other_agents_intent`
- `tests/test_sdk.py:429` — `test_poll_intent_sanitizes_not_found_vs_not_owned`
- `tests/test_sdk.py:457` — `test_wait_for_intent_enforces_ownership`

## Findings

- The adversarial cases requested for IL-03 are now covered at the unit-test layer.
- The implementation behaved consistently with the spec:
  - unauthorized access is sanitized as `intent not found or not authorized`
  - empty / malformed credentials fail before any graph read
  - inactive / revoked agents are denied before any graph read
- I did **not** modify `src/hassaleh/sdk.py` or `src/hassaleh/intent_sdk.py`.
- `uv` was not installed in this shell, so I used the project virtualenv’s `pytest` directly with the same test selection.

## Full pytest output for IL-03 subset

```text
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.0.3, pluggy-1.6.0 -- /home/uranus/projects/hassaleh/.venv/bin/python3
cachedir: .pytest_cache
rootdir: /home/uranus/projects/hassaleh
configfile: pyproject.toml
plugins: asyncio-1.3.0
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 25 items / 13 deselected / 12 selected

tests/test_sdk.py::test_poll_intent_returns_state PASSED                 [  8%]
tests/test_sdk.py::test_poll_intent_requires_api_key PASSED              [ 16%]
tests/test_sdk.py::test_poll_intent_rejects_other_agents_intent PASSED   [ 25%]
tests/test_sdk.py::test_poll_intent_allows_owner PASSED                  [ 33%]
tests/test_sdk.py::test_poll_intent_sanitizes_not_found_vs_not_owned PASSED [ 41%]
tests/test_sdk.py::test_wait_for_intent_enforces_ownership PASSED        [ 50%]
tests/test_sdk.py::test_wait_for_intent_sanitizes_not_found_vs_not_owned PASSED [ 58%]
tests/test_sdk.py::test_poll_intent_empty_api_key_raises_without_graph_touch PASSED [ 66%]
tests/test_sdk.py::test_wait_for_intent_empty_api_key_raises_without_graph_touch PASSED [ 75%]
tests/test_sdk.py::test_poll_intent_inactive_agent_cannot_read_prior_intent PASSED [ 83%]
tests/test_sdk.py::test_wait_for_intent_inactive_agent_cannot_read_prior_intent PASSED [ 91%]
tests/test_sdk.py::test_wait_for_intent_requires_api_key_positional PASSED [100%]

====================== 12 passed, 13 deselected in 0.48s =======================
```

## Verdict

**READY FOR SECURITY-REVIEW**
