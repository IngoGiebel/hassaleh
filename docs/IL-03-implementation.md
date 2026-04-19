# IL-03 implementation — Authenticate `poll_intent` / `wait_for_intent` with ownership check

Date: 2026-04-18
Branch: `trunk`
Repo: `~/projects/hassaleh`
Severity: **HIGH** (Sprint 11 release blocker)

## Problem

Before this change, `poll_intent` and `wait_for_intent` accepted only an
`intent_id` and exposed the Intent's `lifecycle`, `stdout`, `stderr`,
`error_reason`, and `exit_code` to any caller that could guess or observe an ID.
That crossed the agent trust boundary: an agent could read another agent's
command output (including sensitive stderr) with zero authentication.

This is the companion fix to IL-01 (which closed the same hole on
`submit_intent`).

## Files touched

| File | Change |
|------|--------|
| `src/hassaleh/sdk.py` | `poll_intent` and `wait_for_intent` now require `api_key`, authenticate via `_authenticate`, and scope the graph read to the owner |
| `src/hassaleh/agent_dummy.py` | Internal caller already forwards the agent's `api_key` to `wait_for_intent` |
| `tests/test_sdk.py` | IL-03 unit tests (auth required, ownership rejected, owner allowed, sanitized not-found-vs-not-owned, `wait_for_intent` inherits the check, positional signature guard) |
| `tests/test_chaos.py`, `tests/test_stress.py`, `tests/test_integration.py` | Callers updated to pass `api_key` |
| `docs/IL-03-implementation.md` | This note |

## Public signature changes

```python
# Before
async def poll_intent(self, intent_id: str) -> dict[str, Any]: ...
async def wait_for_intent(
    self,
    intent_id: str,
    timeout_sec: float = 60.0,
    poll_interval: float = 1.0,
) -> dict[str, Any]: ...

# After — api_key is required and positioned right after intent_id
async def poll_intent(self, intent_id: str, api_key: str) -> dict[str, Any]: ...
async def wait_for_intent(
    self,
    intent_id: str,
    api_key: str,
    timeout_sec: float = 60.0,
    poll_interval: float = 1.0,
) -> dict[str, Any]: ...
```

## Authentication + ownership resolution

1. `_authenticate(api_key)` resolves the caller's `agent_id` via the
   deterministic `lookup_hash` + bcrypt verify path (IL-02). Failure raises
   `AuthenticationError` before any Intent read is attempted.
2. The Intent query is **ownership-scoped** in a single Cypher round-trip:

   ```cypher
   MATCH (a:Agent {id: $agent_id})-[:PROPOSED]->(i:Intent {id: $id})
   RETURN i.lifecycle, i.stdout, i.stderr, i.error_reason, i.exit_code,
          i.submitted_at, i.completed_at
   ```

   The `PROPOSED` edge is the same edge `submit_intent` writes at creation time
   (`sdk.py:282`), so ownership is derived from the existing graph topology —
   no new edge type or property is required.
3. Zero rows from that query means **either** the Intent doesn't exist **or**
   it exists but is owned by a different agent. Both branches raise the same
   `PermissionError("intent not found or not authorized")`. Callers cannot
   probe for Intent IDs through the error surface.
4. `wait_for_intent` calls `self.poll_intent(intent_id, api_key)` on every
   internal poll, so the ownership check applies to every iteration (not just
   the first one).

## Sanitization contract

The error surface is byte-identical between the "not found" and "not owned"
branches:

```
PermissionError: intent not found or not authorized
```

`test_poll_intent_sanitizes_not_found_vs_not_owned` asserts the two messages
are equal strings. This follows the IL-04 pattern of collapsing
existence-vs-authorization paths in anything an attacker can observe.

## Test additions (in `tests/test_sdk.py`)

- `test_poll_intent_requires_api_key` — invalid/missing `api_key` raises
  `AuthenticationError` **before** any graph query runs
  (`session.run.assert_not_called()`).
- `test_poll_intent_rejects_other_agents_intent` — agent B polling agent A's
  Intent raises `PermissionError`; message does not leak the Intent ID.
- `test_poll_intent_allows_owner` — owning agent succeeds; asserts the
  ownership predicate
  `(a:Agent {id: $agent_id})-[:PROPOSED]->(i:Intent {id: $id})` is in the
  executed Cypher.
- `test_poll_intent_sanitizes_not_found_vs_not_owned` — negative equivalence
  test: `str(not_found.value) == str(not_owned.value)`.
- `test_wait_for_intent_enforces_ownership` — poll loop surfaces the same
  sanitized `PermissionError`; asserts `_authenticate` is called with the
  forwarded `api_key`.
- `test_wait_for_intent_requires_api_key_positional` — signature guard: calling
  `wait_for_intent("some-intent")` raises `TypeError`, preventing legacy
  callers from silently running without auth.
- `test_poll_intent_returns_state` was also updated to pass `api_key` and
  asserts the authenticated `agent_id` is bound into the Cypher params.

## Backwards-compat impact

- **Breaking** at the Python API layer: every caller of `poll_intent` and
  `wait_for_intent` must now supply `api_key`. This is intentional — the
  `TypeError` on missing `api_key` is the enforcement mechanism that blocks
  legacy code paths from silently reading other agents' Intents.
- Internal callers updated in the same change:
  `src/hassaleh/agent_dummy.py`, `tests/test_chaos.py`, `tests/test_stress.py`,
  `tests/test_integration.py`.
- No graph schema changes: the `PROPOSED` edge already existed via
  `submit_intent`. No migration is required.
- Daemon-side Intent processing is unaffected (the Daemon reads intents via
  its own queries, not via the SDK).

## Out of scope

- IL-02 (O(N) bcrypt scan) — already fixed via `lookup_hash`, leveraged here.
- IL-04..IL-07 — tracked separately; this change does not touch them.
- `submit_intent` — unchanged; IL-01 already migrated it.
