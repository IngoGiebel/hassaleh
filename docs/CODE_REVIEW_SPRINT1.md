# Code Review — Sprint 1 MVP

**Reviewer:** Dione 🌙
**Date:** 2026-03-31
**Scope:** daemon.py, sdk.py, agent_dummy.py, schema.cypher, seed.cypher, setup_os.sh
**Note:** Gemini CLI was unavailable (MCP init hang). Review performed by Dione based on full code read.

---

## 1. Critical Issues (must fix before production)

### C1: Race condition in Intent claiming (daemon.py: `_process_pending_intents`)
The query `MATCH (i:Intent {lifecycle: 'pending'})` followed by a separate `SET i.lifecycle = 'running'` in `_execute_intent` is **not atomic**. If two Daemon instances (or a fast restart) process the same tick window, the same Intent could be claimed twice.

**Fix:** Use a single atomic transaction with `SET i.lifecycle = 'running'` in the initial MATCH query, returning only Intents that were successfully transitioned. Pattern:
```cypher
MATCH (agent:Agent)-[:PROPOSED]->(i:Intent {lifecycle: 'pending'})
SET i.lifecycle = 'running', i.started_at = datetime({timezone: 'UTC'})
RETURN i, agent
LIMIT $limit
```
Or use Neo4j transaction functions with explicit write transactions.

### C2: Cypher injection via dynamic label in sdk.py: `submit_intent`
```python
await session.run(f"""
    MATCH (i:Intent {{id: $intent_id}})
    MATCH (target:{target_label} {{id: $target_id}})
    CREATE (i)-[:TARGETS]->(target)
""", intent_id=intent_id, target_id=target_id)
```
`target_label` is interpolated as an f-string, not parameterized. A malicious agent could inject arbitrary Cypher via the label. Neo4j doesn't support parameterized labels, so this needs **allowlist validation**.

**Fix:** Validate `target_label` against a whitelist of known node labels:
```python
ALLOWED_LABELS = {"Agent", "Task", "Capability", "Workspace", "Project", "Sprint"}
if target_label not in ALLOWED_LABELS:
    raise ValueError(f"Invalid target label: {target_label}")
```

### C3: Signal handler creates task incorrectly (daemon.py: `main()`)
```python
loop.add_signal_handler(sig, lambda: asyncio.create_task(daemon.shutdown()))
```
`add_signal_handler` callbacks run in the event loop thread but should be simple — creating a task here can work but the `shutdown()` method is also called in the `finally` block, leading to **double shutdown**. If the first shutdown closes the Neo4j driver, the second will error.

**Fix:** Use the shutdown event instead:
```python
loop.add_signal_handler(sig, daemon._shutdown_event.set)
```
And in `_main_loop`, the existing `_shutdown_event.wait()` already handles this.

---

## 2. Important Improvements (should fix soon)

### I1: No explicit write transactions (daemon.py)
All Neo4j writes use `session.run()` directly instead of `session.execute_write()` / transaction functions. This means:
- No automatic retry on transient errors (leader switches, network blips)
- No proper transaction boundaries

**Fix:** Wrap writes in `session.execute_write(tx_func)`.

### I2: SDK query() BLOCKED_KEYWORDS is too aggressive
`"SET"` will block queries containing the word "OFFSET" (contains "SET") or property names like "dataset". The check is substring-based, not token-based.

**Fix:** Use word-boundary regex or tokenize the Cypher:
```python
import re
for kw in BLOCKED_KEYWORDS:
    if re.search(rf'\b{kw}\b', cypher_upper):
        raise PermissionError(...)
```

### I3: Intent args are shell-split unsafely (daemon.py: `_execute_capability`)
```python
args = str(intent["value"]).split()
```
If value contains spaces in paths or special characters, `split()` will break them. While `create_subprocess_exec` (not `shell=True`) mitigates injection, path arguments with spaces will fail.

**Fix:** Always expect JSON-structured args, fail gracefully on non-JSON:
```python
if isinstance(parsed["args"], list):
    args = parsed["args"]  # already a list
elif isinstance(parsed["args"], str):
    args = shlex.split(parsed["args"])  # proper shell-aware split
```

### I4: Health endpoint exposes internal state without auth
`GET /health` on port 9100 returns agent counts, pending intents, tick count. While bound to 127.0.0.1, any local process can query it.

**Fix (for later):** Add a simple bearer token or Unix socket authentication.

### I5: Missing `__init__.py` content
`src/hassaleh/__init__.py` exists but content wasn't checked — ensure it exports the SDK for clean imports.

---

## 3. Nice-to-Haves (minor improvements)

### N1: stdout/stderr cap at 100KB is arbitrary
Consider making this configurable via DaemonConfig.

### N2: No structured logging
Using `logging.basicConfig` with string formatting. For production, consider JSON-structured logging (e.g., `python-json-logger`) for easier parsing.

### N3: `_execute_update` uses dynamic property SET
```python
SET target[$prop] = $value
```
This is a Neo4j 5+ feature and works, but consider validating allowed properties.

### N4: agent_dummy.py has hardcoded EXPECTED_FILE
`schema.cypher` as expected output is fragile — depends on the workspace directory listing.

### N5: Missing type stubs for neo4j async driver
Consider adding `py.typed` or type ignore comments for cleaner mypy output.

---

## 4. Positive Observations ✅

### P1: Excellent security architecture
The triple-layer security model (Graph permissions → Daemon enforcement → OS-level sudo users) is well-designed. Per-capability OS users (`hassaleh-fs`, `hassaleh-exec`, etc.) provide genuine privilege isolation.

### P2: Clean async patterns
The tick loop with `asyncio.wait_for(shutdown_event.wait(), timeout=sleep_time)` is elegant — provides both periodic ticking and instant shutdown response.

### P3: Proper sd_notify implementation
Hand-rolled `sd_notify` via Unix socket avoids the `systemd` Python package dependency while supporting READY, WATCHDOG, and STOPPING states correctly.

### P4: SDK read-only enforcement
The BLOCKED_KEYWORDS approach (despite I2) provides defense-in-depth on top of Neo4j CE's lack of role-based access. The `submit_intent()` path forces all writes through the Daemon.

### P5: Intent lifecycle is well-designed
`pending → running → success/failed/rejected` with `awaiting_approval` for HITL is clean. Zombie recovery on boot is a mature pattern.

### P6: Comprehensive test coverage
Unit tests (mocked), integration tests (real Neo4j), chaos tests (zombie recovery, permission checks), and a graceful shutdown bash test. Good layering.

### P7: Config-from-graph pattern
Loading DaemonConfig and QueryConfig from Neo4j means runtime reconfiguration without restarts. Smart for a framework that's meant to be operated via its own graph.

---

## Summary

| Severity | Count | Status |
|----------|-------|--------|
| Critical | 3 | Must fix |
| Important | 5 | Should fix |
| Nice-to-have | 5 | Optional |
| Positive | 7 | 👏 |

**Overall:** Very solid MVP. The architecture is well-thought-out and the code quality is high for a Sprint 1 deliverable. The critical issues (race condition, Cypher injection, double shutdown) are straightforward to fix and don't require architectural changes.
