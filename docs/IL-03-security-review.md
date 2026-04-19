# IL-03 Security Review

**Reviewer:** Inanna 🛡️  
**Date:** 2026-04-18  
**Branch:** `trunk` (uncommitted)  
**Scope:** `src/hassaleh/sdk.py` (`_authenticate`, `poll_intent`, `wait_for_intent`), cross-checked against `tests/test_sdk.py`, `tests/test_mvp_intent.py`, `docs/IL-03-implementation.md`, `docs/IL-03-test-verification.md`.

---

## Verdict: **CHANGES REQUESTED**

The core IL-03 control is implemented correctly: `poll_intent()` now requires
`api_key`, authenticates first, binds the authenticated `agent_id` into an
ownership-scoped Cypher query, and returns a single sanitized `PermissionError`
for not-found vs not-owned.

However, I am **not** signing this off as CLEAN yet because `wait_for_intent()`
still exposes a non-sanitized timeout exception containing the raw `intent_id`
and timeout value:

- `src/hassaleh/sdk.py:403-405`

That is not one of the four negative branches called out in the ticket, but it
**is** part of the same externally observable error surface on the IL-03 code
path and violates the review requirement to avoid leaking identifiers / timing
through exceptions.

---

## 1. Attestation — No path reads an Intent row without `_authenticate()` clearing the caller

**Attestation: PASS**

### Evidence

Inspected:

- `src/hassaleh/sdk.py:325-366` — `poll_intent()`
- `src/hassaleh/sdk.py:368-405` — `wait_for_intent()`
- `src/hassaleh/sdk.py:147-175` — `_authenticate()`

`poll_intent()` calls `_authenticate(api_key)` before any graph read:

- `src/hassaleh/sdk.py:346`

The only Intent read in `poll_intent()` is the ownership-scoped query after
that authentication step:

- `src/hassaleh/sdk.py:353-362`

`wait_for_intent()` performs no direct Intent read of its own. Every loop
iteration delegates back to `poll_intent(intent_id, api_key)`:

- `src/hassaleh/sdk.py:397-399`

That means there is **no IL-03 path** in `sdk.py` where an Intent row is read
without `_authenticate()` clearing the caller first.

### Test corroboration

- `tests/test_sdk.py` includes `test_poll_intent_requires_api_key`
- `docs/IL-03-test-verification.md` records that invalid / empty credentials
  fail before any graph read

---

## 2. Attestation — After auth, ownership is verified before any intent data is returned

**Attestation: PASS**

### Evidence

After authentication, `poll_intent()` binds the server-derived `agent_id` into a
single ownership-scoped Cypher query:

```cypher
MATCH (a:Agent {id: $agent_id})-[:PROPOSED]->(i:Intent {id: $id})
RETURN i.lifecycle AS lifecycle,
       i.stdout AS stdout,
       i.stderr AS stderr,
       i.error_reason AS error_reason,
       i.exit_code AS exit_code,
       i.submitted_at AS submitted_at,
       i.completed_at AS completed_at
```

Source:

- `src/hassaleh/sdk.py:353-362`

This is the correct authorization shape for IL-03:

- auth establishes caller identity (`agent_id`)
- ownership check is encoded in the graph pattern itself
- no post-fetch filtering occurs
- no intent fields are returned unless the authenticated agent owns the
  `PROPOSED` edge to that exact Intent

If zero rows return, the code raises the same sanitized error before returning
any data:

- `src/hassaleh/sdk.py:364-365`

### Test corroboration

- `tests/test_sdk.py::test_poll_intent_allows_owner`
- `tests/test_sdk.py::test_poll_intent_rejects_other_agents_intent`
- `tests/test_sdk.py::test_poll_intent_sanitizes_not_found_vs_not_owned`

---

## 3. Attestation — `wait_for_intent()` loop re-checks ownership per iteration or proves immutability

**Attestation: PASS**

### Evidence

`wait_for_intent()` does **not** cache an earlier authorization result. Instead,
it calls `await self.poll_intent(intent_id, api_key)` on every loop iteration:

- `src/hassaleh/sdk.py:397-399`

That means both authentication and ownership are re-applied on each poll:

- `_authenticate(api_key)` happens inside `poll_intent()` at `sdk.py:346`
- ownership query runs at `sdk.py:353-362`

This is the stronger of the two acceptable models from the review requirement:
**re-check per iteration**, not merely “bound once and assumed immutable.”

Security consequence:

- a revoked / inactive / invalidated key loses access on the next poll cycle
- a changed ownership edge would also be enforced on the next poll cycle

### Test corroboration

- `tests/test_sdk.py::test_wait_for_intent_enforces_ownership`
- `docs/IL-03-test-verification.md` notes adversarial tests for inactive agents
  and empty credentials on both `poll_intent()` and `wait_for_intent()`

---

## 4. Attestation — Error strings for the 4 negative branches are byte-equal where required

**Attestation: PASS for auth pair and authz pair; FAIL for broader exception-surface sanitization due to timeout leak**

The ticket requirement mixes **two pairs** of branches that must collapse:

1. auth failures:
   - missing/empty `api_key`
   - invalid `api_key`
2. authorization / existence failures:
   - intent not found
   - intent exists but not owned by caller

Those pairs are correctly collapsed in `sdk.py`.

### Grep / literal comparison

Inspected literal strings in `src/hassaleh/sdk.py`:

- `src/hassaleh/sdk.py:159` — `AuthenticationError("Invalid API key")`
- `src/hassaleh/sdk.py:172` — `AuthenticationError("Invalid API key")`
- `src/hassaleh/sdk.py:175` — `AuthenticationError("Invalid API key")`
- `src/hassaleh/sdk.py:365` — `PermissionError("intent not found or not authorized")`

### Pair analysis

#### A. missing/empty `api_key` vs invalid `api_key`

**PASS**

- missing/empty key branch: `sdk.py:158-159`
- no matching record / missing hash branch: `sdk.py:171-172`
- bcrypt mismatch branch: `sdk.py:174-175`

All three produce the same literal string:

```text
Invalid API key
```

#### B. intent not found vs intent exists but not owned

**PASS**

Both conditions collapse to zero rows from the ownership-scoped query and raise:

```text
intent not found or not authorized
```

Source:

- `src/hassaleh/sdk.py:348-365`

### Important caveat

Although the four required branches are correctly collapsed, the **overall error
surface is still not fully sanitized** because `wait_for_intent()` later raises:

```text
Intent {intent_id} did not complete within {timeout_sec}s
```

Source:

- `src/hassaleh/sdk.py:403-405`

That leaks both the raw identifier and timing detail through an externally
observable exception on the same reviewed path.

---

## 5. Attestation — No filesystem paths, Neo4j internals, or timing differences leak through exceptions

**Attestation: FAIL (narrowly)**

### What is clean

I found **no filesystem path leakage** and **no Neo4j-internal leakage** in the
reviewed IL-03 methods:

- no Cypher text is surfaced
- no driver exception wrapping is exposed intentionally
- no node labels / graph internals / DB metadata are interpolated into the auth
  or authorization errors

The explicit auth / authorization exceptions are sanitized:

- `Invalid API key`
- `intent not found or not authorized`

### What still leaks

`wait_for_intent()` emits a timeout exception containing:

- the raw `intent_id`
- the raw `timeout_sec`

Source:

- `src/hassaleh/sdk.py:403-405`

This is an identifier leak and a timing-detail leak through the exception text.

To be precise:

- it does **not** let a caller bypass ownership
- it does **not** reveal whether an Intent exists vs is unauthorized
- but it **does** violate the review requirement’s broader exception-surface
  constraint

### Timing differences

The required four negative branches are string-sanitized, but runtime timing is
still naturally distinguishable at a coarse level:

- auth failures return immediately
- unauthorized/not-found on `poll_intent()` require one query
- `wait_for_intent()` timeout returns after the configured delay

The timeout behavior is functional, but the exception text should not amplify
that timing distinction with identifier-bearing details.

---

## Concrete code changes required

Because the verdict is **CHANGES REQUESTED**, these are the specific changes
needed before sign-off:

1. **Sanitize the timeout exception in `wait_for_intent()`**  
   File: `src/hassaleh/sdk.py:403-405`

   Replace:

   ```python
   raise TimeoutError(
       f"Intent {intent_id} did not complete within {timeout_sec}s"
   )
   ```

   With a non-identifying message, e.g.:

   ```python
   raise TimeoutError("intent did not complete before timeout")
   ```

   or an equivalent sanitized constant string.

2. **Add / update a regression test for timeout sanitization**  
   File: `tests/test_sdk.py` (near existing IL-03 `wait_for_intent` coverage)

   Add a test that asserts the raised `TimeoutError` message:

   - does **not** include the `intent_id`
   - does **not** include raw timeout formatting if you want a fully constant
     error string

No additional code changes are required for the main IL-03 auth / ownership
control itself.

---

## Reviewed evidence

### Documents read

- `docs/IL-03-implementation.md`
- `docs/IL-03-test-verification.md`
- `docs/IL-01-IL-02-security-review.md`

### Code inspected

- `src/hassaleh/sdk.py:147-175` — `_authenticate()`
- `src/hassaleh/sdk.py:325-366` — `poll_intent()`
- `src/hassaleh/sdk.py:368-405` — `wait_for_intent()`

### Tests cross-checked

From `tests/test_sdk.py` / verification note:

- `test_poll_intent_requires_api_key`
- `test_poll_intent_rejects_other_agents_intent`
- `test_poll_intent_allows_owner`
- `test_poll_intent_sanitizes_not_found_vs_not_owned`
- `test_wait_for_intent_enforces_ownership`
- `test_wait_for_intent_sanitizes_not_found_vs_not_owned`
- `test_poll_intent_empty_api_key_raises_without_graph_touch`
- `test_wait_for_intent_empty_api_key_raises_without_graph_touch`
- `test_poll_intent_inactive_agent_cannot_read_prior_intent`
- `test_wait_for_intent_inactive_agent_cannot_read_prior_intent`
- `test_wait_for_intent_requires_api_key_positional`

`tests/test_mvp_intent.py` was reviewed for context; IL-03’s concrete security
coverage lives in `tests/test_sdk.py` and the verification note.

---

**Sign-off line:** Reviewed by Inanna, 2026-04-18.
