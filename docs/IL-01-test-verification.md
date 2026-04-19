# IL-01 test verification

Date: 2026-04-18
Branch: `trunk`
Repo: `~/projects/hassaleh`

## Command run

```bash
source .venv/bin/activate
export NEO4J_URI=bolt://localhost:7690
export NEO4J_USER=neo4j
export NEO4J_PASSWORD=hassaleh
pytest -q
```

Neo4j dev at `bolt://localhost:7690` was reachable before the run.

## Overall result

- Total tests: **393**
- Passed: **372**
- Failed: **5**
- Errors: **16**
- Runtime: **103.89s**

## IL-01 verdict

- **New IL-01 tests in `tests/test_sdk.py`: PASS**
- The IL-01-specific coverage added in `tests/test_sdk.py` passed.
- The only failure in `tests/test_sdk.py` was unrelated to IL-01:
  - `tests/test_sdk.py::test_connect_omits_auth_when_credentials_are_blank`

## Requested existing test files

- `tests/test_task_modes.py`: **PASS** (`21 passed`)
- `tests/test_chaos.py`: **FAIL** (`3 errors`)
- `tests/test_integration.py`: **FAIL** (`7 errors`)

### Why `test_chaos.py` and `test_integration.py` failed

These files are still hard-wired to the separate test instance on port `7691`, not the requested dev instance on `7690`:

- `tests/test_chaos.py:24-26`
- `tests/test_integration.py:21-23`

The same hard-coded `7691` pattern also caused additional suite errors in:

- `tests/test_messaging.py:17-19`
- `tests/test_stress.py:30-32`

Representative error:

```text
neo4j.exceptions.ServiceUnavailable: Couldn't connect to localhost:7691
(resolved to ('127.0.0.1:7691',)):
Failed to establish connection to ResolvedIPv4Address(('127.0.0.1', 7691))
(reason [Errno 111] Connect call failed ('127.0.0.1', 7691))
```

## Failures clearly related to IL-01 signature migration

One stale caller still uses the removed `agent_id` argument to `submit_intent()`:

- `tests/test_chaos.py:123-126`

```python
intent_id = await sdk.submit_intent(
    agent_id='no-cap-agent',
    action='execute_capability',
    capability_id='test-cap-2',
)
```

This did **not** surface as the reported pytest failure because the file failed earlier during fixture setup when trying to connect to `localhost:7691`, but it is a clear IL-01 migration break waiting underneath the connection issue.

There are also stale pre-IL-01 `send_message()` call sites in `tests/test_messaging.py`:

- `tests/test_messaging.py:57`
- `tests/test_messaging.py:67-69`
- `tests/test_messaging.py:79-80`

Those calls were likewise masked by the same `7691` fixture failure.

## Other non-IL-01 failures from the full suite

1. `tests/test_mvp_intent.py::TestDaemonProcessing::test_claim_fails_for_missing_capability`
   - Fails with Neo4j constraint error:
   - `Node(...) already exists with label Capability and property id = 'exec-ls'`

2. `tests/test_mvp_intent.py::TestDaemonProcessing::test_execution_timeout`
   - Expected timeout result, but got:
   - `Parameter validation failed: Path does not exist: /app`

3. `tests/test_mvp_intent.py::TestConcurrency::test_atomic_claim_prevents_double_processing`
   - Both daemons claimed the same intent:
   - `assert results.count(True) == 1`, actual `2`

4. `tests/test_mvp_intent.py::TestOutputTruncation::test_stdout_truncated_at_64kb`
   - `result["result"]` was `None`

5. `tests/test_sdk.py::test_connect_omits_auth_when_credentials_are_blank`
   - Expected `auth=None`
   - Actual driver call used `auth=('neo4j', 'hassaleh')`

## Pytest failure/error output

```text
FAILED tests/test_mvp_intent.py::TestDaemonProcessing::test_claim_fails_for_missing_capability
FAILED tests/test_mvp_intent.py::TestDaemonProcessing::test_execution_timeout
FAILED tests/test_mvp_intent.py::TestConcurrency::test_atomic_claim_prevents_double_processing
FAILED tests/test_mvp_intent.py::TestOutputTruncation::test_stdout_truncated_at_64kb
FAILED tests/test_sdk.py::test_connect_omits_auth_when_credentials_are_blank
ERROR tests/test_chaos.py::test_zombie_recovery - neo4j.exceptions.ServiceUnavailable: Couldn't connect to localhost:7691 (resolved to ('127.0.0.1:7691',)): Failed to establish connection to ResolvedIPv4Address(('127.0.0.1', 7691)) (reason [Errno 111] Connect call failed ('127.0.0.1', 7691))
ERROR tests/test_chaos.py::test_read_only_enforcement - neo4j.exceptions.ServiceUnavailable: Couldn't connect to localhost:7691 (resolved to ('127.0.0.1:7691',)): Failed to establish connection to ResolvedIPv4Address(('127.0.0.1', 7691)) (reason [Errno 111] Connect call failed ('127.0.0.1', 7691))
ERROR tests/test_chaos.py::test_capability_permission_denied - neo4j.exceptions.ServiceUnavailable: Couldn't connect to localhost:7691 (resolved to ('127.0.0.1:7691',)): Failed to establish connection to ResolvedIPv4Address(('127.0.0.1', 7691)) (reason [Errno 111] Connect call failed ('127.0.0.1', 7691))
ERROR tests/test_integration.py::test_connect_and_query - neo4j.exceptions.ServiceUnavailable: Couldn't connect to localhost:7691 (resolved to ('127.0.0.1:7691',)): Failed to establish connection to ResolvedIPv4Address(('127.0.0.1', 7691)) (reason [Errno 111] Connect call failed ('127.0.0.1', 7691))
ERROR tests/test_integration.py::test_load_query_config - neo4j.exceptions.ServiceUnavailable: Couldn't connect to localhost:7691 (resolved to ('127.0.0.1:7691',)): Failed to establish connection to ResolvedIPv4Address(('127.0.0.1', 7691)) (reason [Errno 111] Connect call failed ('127.0.0.1', 7691))
ERROR tests/test_integration.py::test_my_tasks - neo4j.exceptions.ServiceUnavailable: Couldn't connect to localhost:7691 (resolved to ('127.0.0.1:7691',)): Failed to establish connection to ResolvedIPv4Address(('127.0.0.1', 7691)) (reason [Errno 111] Connect call failed ('127.0.0.1', 7691))
ERROR tests/test_integration.py::test_submit_intent - neo4j.exceptions.ServiceUnavailable: Couldn't connect to localhost:7691 (resolved to ('127.0.0.1:7691',)): Failed to establish connection to ResolvedIPv4Address(('127.0.0.1', 7691)) (reason [Errno 111] Connect call failed ('127.0.0.1', 7691))
ERROR tests/test_integration.py::test_submit_intent_creates_targets_edge - neo4j.exceptions.ServiceUnavailable: Couldn't connect to localhost:7691 (resolved to ('127.0.0.1:7691',)): Failed to establish connection to ResolvedIPv4Address(('127.0.0.1', 7691)) (reason [Errno 111] Connect call failed ('127.0.0.1', 7691))
ERROR tests/test_integration.py::test_agent_info - neo4j.exceptions.ServiceUnavailable: Couldn't connect to localhost:7691 (resolved to ('127.0.0.1:7691',)): Failed to establish connection to ResolvedIPv4Address(('127.0.0.1', 7691)) (reason [Errno 111] Connect call failed ('127.0.0.1', 7691))
ERROR tests/test_integration.py::test_submit_intent_rejects_invalid_api_key - neo4j.exceptions.ServiceUnavailable: Couldn't connect to localhost:7691 (resolved to ('127.0.0.1:7691',)): Failed to establish connection to ResolvedIPv4Address(('127.0.0.1', 7691)) (reason [Errno 111] Connect call failed ('127.0.0.1', 7691))
ERROR tests/test_messaging.py::test_send_and_read_message - neo4j.exceptions.ServiceUnavailable: Couldn't connect to localhost:7691 (resolved to ('127.0.0.1:7691',)): Failed to establish connection to ResolvedIPv4Address(('127.0.0.1', 7691)) (reason [Errno 111] Connect call failed ('127.0.0.1', 7691))
ERROR tests/test_messaging.py::test_message_ordering - neo4j.exceptions.ServiceUnavailable: Couldn't connect to localhost:7691 (resolved to ('127.0.0.1:7691',)): Failed to establish connection to ResolvedIPv4Address(('127.0.0.1', 7691)) (reason [Errno 111] Connect call failed ('127.0.0.1', 7691))
ERROR tests/test_messaging.py::test_cursor_advancement - neo4j.exceptions.ServiceUnavailable: Couldn't connect to localhost:7691 (resolved to ('127.0.0.1:7691',)): Failed to establish connection to ResolvedIPv4Address(('127.0.0.1', 7691)) (reason [Errno 111] Connect call failed ('127.0.0.1', 7691))
ERROR tests/test_messaging.py::test_discussion_lifecycle - neo4j.exceptions.ServiceUnavailable: Couldn't connect to localhost:7691 (resolved to ('127.0.0.1:7691',)): Failed to establish connection to ResolvedIPv4Address(('127.0.0.1', 7691)) (reason [Errno 111] Connect call failed ('127.0.0.1', 7691))
ERROR tests/test_stress.py::test_rapid_intent_submission - neo4j.exceptions.ServiceUnavailable: Couldn't connect to localhost:7691 (resolved to ('127.0.0.1:7691',)): Failed to establish connection to ResolvedIPv4Address(('127.0.0.1', 7691)) (reason [Errno 111] Connect call failed ('127.0.0.1', 7691))
ERROR tests/test_stress.py::test_stress_10_intents_processed - neo4j.exceptions.ServiceUnavailable: Couldn't connect to localhost:7691 (resolved to ('127.0.0.1:7691',)): Failed to establish connection to ResolvedIPv4Address(('127.0.0.1', 7691)) (reason [Errno 111] Connect call failed ('127.0.0.1', 7691))

FAILED tests/test_mvp_intent.py::TestDaemonProcessing::test_claim_fails_for_missing_capability
neo4j.exceptions.ConstraintError: {neo4j_code: Neo.ClientError.Schema.ConstraintValidationFailed} {message: Node(45) already exists with label `Capability` and property `id` = 'exec-ls'}

FAILED tests/test_mvp_intent.py::TestDaemonProcessing::test_execution_timeout
AssertionError: assert 'timed out' in 'parameter validation failed: path does not exist: /app'

FAILED tests/test_mvp_intent.py::TestConcurrency::test_atomic_claim_prevents_double_processing
AssertionError: assert 2 == 1
 +  where 2 = <built-in method count of list object>(True)
 +  where <built-in method count of list object> = [True, True].count

FAILED tests/test_mvp_intent.py::TestOutputTruncation::test_stdout_truncated_at_64kb
TypeError: object of type 'NoneType' has no len()

FAILED tests/test_sdk.py::test_connect_omits_auth_when_credentials_are_blank
AssertionError: expected call not found.
Expected: driver('bolt://localhost:7687', auth=None)
  Actual: driver('bolt://localhost:7687', auth=('neo4j', 'hassaleh'))

5 failed, 372 passed, 16 errors in 103.89s (0:01:43)
```
