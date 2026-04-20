# IL-07 test validation

Date: 2026-04-19
Repo: `~/projects/hassaleh`
Target: IL-07 trust-boundary enforcement in `src/hassaleh/daemon.py`
Reviewed docs: `docs/IL-07-implementation.md`

## Environment

- Working directory: `~/projects/hassaleh`
- Python environment: `.venv`
- Neo4j env set for validation:
  - `NEO4J_URI=bolt://localhost:7690`
  - `NEO4J_USER=neo4j`
  - `NEO4J_PASSWORD=hassaleh`

## IL-07 targeted test run — pass 1

Command:
```bash
python -m pytest tests/test_il07_trust_boundary.py -v
```

Output:
```text
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.0.3, pluggy-1.6.0 -- /home/uranus/projects/hassaleh/.venv/bin/python
cachedir: .pytest_cache
rootdir: /home/uranus/projects/hassaleh
configfile: pyproject.toml
plugins: asyncio-1.3.0
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 3 items

tests/test_il07_trust_boundary.py::test_graph_injected_shell_metacharacter_in_invoke_command_rejected PASSED [ 33%]
tests/test_il07_trust_boundary.py::test_graph_injected_non_allowlisted_uid_rejected PASSED [ 66%]
tests/test_il07_trust_boundary.py::test_allowlisted_values_reach_subprocess_path PASSED [100%]

============================== 3 passed in 1.03s ===============================
```

## IL-07 targeted test run — pass 2

Command:
```bash
python -m pytest tests/test_il07_trust_boundary.py -v
```

Output:
```text
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.0.3, pluggy-1.6.0 -- /home/uranus/projects/hassaleh/.venv/bin/python
cachedir: .pytest_cache
rootdir: /home/uranus/projects/hassaleh
configfile: pyproject.toml
plugins: asyncio-1.3.0
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 3 items

tests/test_il07_trust_boundary.py::test_graph_injected_shell_metacharacter_in_invoke_command_rejected PASSED [ 33%]
tests/test_il07_trust_boundary.py::test_graph_injected_non_allowlisted_uid_rejected PASSED [ 66%]
tests/test_il07_trust_boundary.py::test_allowlisted_values_reach_subprocess_path PASSED [100%]

============================== 3 passed in 0.74s ===============================
```

## Coverage sanity check

### Explicitly covered

1. **Mismatch branch: wrong `invoke_command`**
   - Covered by:
     - `test_graph_injected_shell_metacharacter_in_invoke_command_rejected`
   - Evidence from grep:
     - line 48 defines the test
     - line 52 sets `"command": "/usr/bin/ls; touch /tmp/pwned"`

2. **Mismatch branch: wrong `exec_as_user`**
   - Covered by:
     - `test_graph_injected_non_allowlisted_uid_rejected`
   - Evidence from grep:
     - line 69 defines the test
     - line 75 sets `"exec_user": "root"`

3. **Allowlist hit / happy path**
   - Covered by:
     - `test_allowlisted_values_reach_subprocess_path`
   - Evidence:
     - uses allowlisted `cap_id`, `command`, and `exec_user`
     - asserts `asyncio.create_subprocess_exec` is called with the expected `sudo -n -u ...` argv

### Not explicitly covered

4. **Unknown `capability_id` branch**
   - **Not present in the current test file**
   - Grep evidence:
     - all observed `cap_id` assignments in the file are `"exec-ls"`
     - no test name or assertion references `unknown` capability behavior

Assessment:
- The two requested mismatch branches are definitely exercised.
- The unknown-capability-id branch appears to be a **real coverage gap** relative to the task brief and the implementation-note wording.
- This is a **non-blocking gap** if the branch is trivial and already protected by shared validation logic, but it is still a missing direct regression for one of the requested scenarios.

## Sibling regression run

Command:
```bash
python -m pytest tests/test_il04_path_leak.py tests/test_il05_toctou.py tests/test_il06_heartbeat_sequencing.py -v
```

Output:
```text
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.0.3, pluggy-1.6.0 -- /home/uranus/projects/hassaleh/.venv/bin/python
cachedir: .pytest_cache
rootdir: /home/uranus/projects/hassaleh
configfile: pyproject.toml
plugins: asyncio-1.3.0
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 10 items

tests/test_il04_path_leak.py::test_out_of_scope_path PASSED              [ 10%]
tests/test_il04_path_leak.py::test_nonexistent_path PASSED               [ 20%]
tests/test_il04_path_leak.py::test_non_directory_path PASSED             [ 30%]
tests/test_il04_path_leak.py::test_execute_ls_runtime_failure PASSED     [ 40%]
tests/test_il04_path_leak.py::test_execute_ls_timeout PASSED             [ 50%]
tests/test_il05_toctou.py::test_final_component_symlink_swap_rejected PASSED [ 60%]
tests/test_il05_toctou.py::test_intermediate_component_symlink_swap_rejected PASSED [ 70%]
tests/test_il05_toctou.py::test_no_swap_happy_path_still_works PASSED    [ 80%]
tests/test_il06_heartbeat_sequencing.py::test_first_heartbeat_keeps_previous_heartbeat_null PASSED [ 90%]
tests/test_il06_heartbeat_sequencing.py::test_previous_heartbeat_equals_prior_last_heartbeat PASSED [100%]

======================== 10 passed in 76.68s (0:01:16) =========================
```

## Flakiness observations

- No flakiness observed across the two IL-07 runs.
- Runtime was stable (`1.03s` then `0.74s`).
- The sibling regression also passed cleanly.
- No intermittent failures, skips, or retries were observed.

## Verdict

**PASS-with-notes**

### Why
- The current IL-07 test suite passed twice.
- The sibling IL-04 / IL-05 / IL-06 suites remained green, so IL-07 did not appear to break adjacent security fixes.
- However, the test file does **not** appear to include a direct regression for the **unknown `capability_id` branch**, which was part of the requested coverage sanity check.

### Coverage gap classification
- **Non-blocking:** unknown-capability-id branch missing as an explicit test case.
- The implemented tests still validate the main trust-boundary enforcement paths (bad command, bad exec user, allowlisted happy path).
