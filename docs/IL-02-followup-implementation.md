# IL-02 Follow-up Implementation

## 1. Summary of IL-02 Finding
The IL-02 security review identified a HIGH severity finding in the authentication mechanism: the `_authenticate` method was performing an O(N) bcrypt scan. It loaded every active Agent into memory and verified the provided API key against each `api_key_hash` in a loop, leading to severe performance degradation and potential denial-of-service risks as the agent population grew.

## 2. Schema Change (`schema.cypher`)
A uniqueness constraint has been added to the database schema to ensure that each agent's `api_key_lookup` is unique, preventing duplicate lookup hashes:
```cypher
CREATE CONSTRAINT agent_api_key_lookup IF NOT EXISTS
  FOR (a:Agent) REQUIRE a.api_key_lookup IS UNIQUE;
```

## 3. `IntentSDK._authenticate` Implementation (`src/hassaleh/intent_sdk.py`)
The `IntentSDK._authenticate` method has been rewritten to port the O(1) pattern used in `sdk.py` and `heartbeat_sdk.py`. It now:
- Computes a deterministic SHA-256 `lookup_hash` of the provided `api_key`.
- Performs a single indexed `MATCH (a:Agent) WHERE a.api_key_lookup = $lookup` to retrieve the agent's ID and `api_key_hash`.
- Performs exactly one `bcrypt.checkpw` verification against the stored hash.
- If the API key is missing/empty or if no matching `api_key_lookup` row is found, it short-circuits and skips bcrypt entirely. All failure branches raise a byte-identical `AuthenticationError("Invalid API key")`.

## 4. Test Changes and IL-01 Residual Cleanup
- **`tests/test_chaos.py` & `tests/test_messaging.py`**: Removed stale `agent_id` kwargs from `submit_intent` and `send_message` calls. This completes the IL-01 residual cleanup, as the server now derives the `agent_id` securely from the `api_key`.
- **`tests/test_mvp_intent.py`**: Added comprehensive regression tests to enforce the O(1) authentication cost guarantees.

## 5. Behavior Equivalence
No behavior change intended — equivalence with pre-patch auth for known/unknown key paths.

## 6. Verification
The regression tests added in `tests/test_mvp_intent.py` explicitly verify the O(1) performance guarantees:
- `test_missing_or_empty_key_skips_bcrypt`: Asserts zero bcrypt calls for empty keys.
- `test_unknown_key_skips_bcrypt`: Asserts zero bcrypt calls for unknown keys (no dummy bcrypt is performed, it short-circuits).
- `test_valid_lookup_wrong_hash_calls_checkpw_exactly_once`: Asserts exactly one bcrypt call when the lookup succeeds but the hash check fails.
- `test_valid_auth_cost_is_independent_of_agent_count`: Mocks a graph with 25 agents and asserts exactly one `bcrypt.checkpw` call on a valid authentication, proving the cost is independent of population size (N).
