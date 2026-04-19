# IL-06 implementation — explicit heartbeat previous_heartbeat sequencing

Date: 2026-04-19
Branch: `trunk`
Repo: `~/projects/hassaleh`

## Problem

The heartbeat write in `src/hassaleh/heartbeat_sdk.py` previously relied on Neo4j `SET` semantics that are easy to misread:

```cypher
SET a.last_heartbeat = datetime(),
    a.previous_heartbeat = a.last_heartbeat,
    ...
```

A reviewer had to re-derive whether `a.previous_heartbeat` receives the pre-write or post-write value of `a.last_heartbeat`.

## Decision

**First-heartbeat behavior:** `previous_heartbeat` remains `null` on the first successful heartbeat.

Rationale:
- There is no prior heartbeat to preserve.
- Setting `previous_heartbeat` to the same timestamp as the new `last_heartbeat` would falsely imply an earlier heartbeat existed.
- `null` cleanly distinguishes first contact from subsequent heartbeats.

## Cypher change

### Before

```cypher
MATCH (a:Agent {id: $agent_id})
WHERE a.lifecycle <> 'disabled'
  AND (a.last_heartbeat IS NULL
       OR a.last_heartbeat < datetime() - duration('PT60S'))
SET a.last_heartbeat = datetime(),
    a.previous_heartbeat = a.last_heartbeat,
    a.heartbeat_count = coalesce(a.heartbeat_count, 0) + 1,
    ...
```

### After

```cypher
MATCH (a:Agent {id: $agent_id})
WHERE a.lifecycle <> 'disabled'
  AND (a.last_heartbeat IS NULL
       OR a.last_heartbeat < datetime() - duration('PT60S'))
WITH a, a.last_heartbeat AS old_last
SET a.previous_heartbeat = old_last,
    a.last_heartbeat = datetime(),
    a.heartbeat_count = coalesce(a.heartbeat_count, 0) + 1,
    ...
```

## Why `WITH` fixes the ambiguity

`WITH a, a.last_heartbeat AS old_last` binds the pre-update value of `a.last_heartbeat` into an explicit variable before any mutation occurs.

That makes the sequencing obvious:
1. Read the old heartbeat timestamp into `old_last`
2. Copy `old_last` into `previous_heartbeat`
3. Write a fresh current timestamp into `last_heartbeat`

No reviewer has to rely on implicit interpretation of multi-assignment `SET` ordering.

## Tests added

New regression file:
- `tests/test_il06_heartbeat_sequencing.py`

Coverage:
1. **First heartbeat case**
   - Verifies `previous_heartbeat is None`
2. **Subsequent heartbeat sequencing**
   - Sends a first heartbeat
   - Stores the returned `last_heartbeat`
   - Advances time eligibility for the next heartbeat in Neo4j
   - Sends a second heartbeat with the chained token
   - Verifies `previous_heartbeat == first last_heartbeat`

## Notes on the regression test

The production heartbeat path is rate-limited to one write per 60 seconds. The regression test uses a real Neo4j connection at `bolt://localhost:7690` and waits just over 60 seconds between two genuine heartbeat writes so the assertion is made against the actual prior `last_heartbeat`, not a test-mutated timestamp.
