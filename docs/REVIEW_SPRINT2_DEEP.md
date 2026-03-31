# Sprint 2 Deep Architectural Review — GSL-Ops Rule Engine

**Reviewer:** Claude (automated deep review)
**Date:** 2026-03-31
**Scope:** Grammar, Parser, Compiler, Runtime, Resolver, Daemon integration, Seed rules
**Commit:** `894e213` (Sprint 2 Phase 3)

---

## 1. Architecture Assessment

### 1.1 Grammar Design (`engine/gsl_ops.lark`)

The GSL-Ops grammar is well-designed for deterministic agent orchestration. Key strengths:

- **Python-style indentation** (PythonIndenter + Earley) is an excellent choice — it matches the operational audience and avoids brace/end-block noise.
- **MATCH → IF → Action** nesting models the inspect-decide-act pattern cleanly.
- **EVERY blocks** provide schedule semantics without cron complexity.
- **Operator precedence** is correct: `or < and < not < comparison < arith < term < factor < atom`.
- **DURATION_LITERAL** shorthand (`5m`, `1h`) alongside ISO 8601 is pragmatic.

**Limitations for orchestration:**

| Gap | Impact | Severity |
|-----|--------|----------|
| No `FOR`/`FOREACH` over lists | Cannot iterate agent capabilities, tags, or relationships | Medium |
| No `EXISTS` subquery | Cannot express "if agent has ANY capability matching X" | Medium |
| No string interpolation | Log messages are static strings, cannot embed variable values | Low-Medium |
| No `IN` operator for lists | `"not in"` and `"in"` are defined as COMP_OPs but there's no list literal to compare against | Low |
| No `MATCH` `WHERE` clause | All filtering must be inline `{prop: val}` Cypher or post-match IF | Low |
| Single comparison only | `?comparison: arith (COMP_OP arith)?` — no chaining like `0 < x < 10` | Low |

### 1.2 Pipeline Design

The compile-once-exec-many pipeline is sound:

```
GSL-Ops text → Lark parse → AST → Python source → compile() → exec() → evaluate(ctx)
```

Caching compiled Python in Neo4j (`compiled_python` + `compiler_version`) avoids redundant work. The `COMPILER_VERSION` invalidation key is correct.

### 1.3 Separation of Concerns

Clean layering:
- **Parser** — pure parsing, no business logic
- **Compiler** — AST → Python text, no execution
- **Runtime** — execution context, intent accumulation
- **Resolver** — stateless conflict resolution
- **Daemon** — orchestration, Neo4j I/O, lifecycle

---

## 2. Critical Issues

### 2.1 CRITICAL: `exec()` of arbitrary/cached Python — Code Injection via Neo4j

**File:** `daemon.py:547-553`

```python
code = compile(python_source, f"<rule:{rule_id}>", "exec")
ns: dict[str, Any] = {}
exec(code, ns)
```

The daemon `exec()`s Python source that may come from:
1. **Freshly compiled** rule text (somewhat trusted — goes through the compiler)
2. **Cached `compiled_python`** from Neo4j (line 525: `python_source = cached_python`)

**Risk:** If an attacker gains write access to Neo4j (or a rule author is malicious), they can store arbitrary Python in `r.compiled_python` and it will be `exec()`'d by the daemon with full daemon privileges (`hassaleh-svc` user). The `compiler_version` check (line 525) is only a string comparison — a tampered node can set `compiler_version` to the current version.

**Recommended mitigations:**
1. **Never trust cached Python.** Always recompile from `rule_text` and compare the hash against `compiled_python`. If they diverge, log a security alert and refuse to load.
2. **Restrict the `exec()` namespace.** Currently `ns = {}` gives access to all builtins. Use `{"__builtins__": {}}` and explicitly inject only allowed names (`RuleContext`, safe builtins).
3. **Add a cryptographic signature** to compiled rules (HMAC with a daemon-held secret).
4. **Restrict `rule_text` authorship** — only allow rules from trusted authors, validated via graph relationships.

### 2.2 CRITICAL: Cypher Injection via `ctx.match()`

**File:** `runtime.py:118`

```python
query = f"MATCH {cypher_pattern} RETURN *"
result = self.session.run(query, **params)
```

The `cypher_pattern` comes from the compiled rule's MATCH line, which is a raw string extracted from the grammar (`compiler.py:74`):

```python
cypher = match_line[6:].rstrip().rstrip(":")
```

This string is embedded directly into a Cypher query via f-string. While the pattern originates from compiled rule text (not user input at runtime), if a rule author writes:

```
MATCH (a:Agent) DETACH DELETE a RETURN a:
```

...the grammar's `MATCH_LINE` regex (`/MATCH\s+.+:/`) would capture it, and `ctx.match()` would execute a destructive query. The `RETURN *` append does not prevent preceding destructive clauses.

**Recommended mitigations:**
1. **Validate Cypher patterns** at compile time — reject patterns containing write keywords (`CREATE`, `DELETE`, `DETACH`, `SET`, `REMOVE`, `MERGE`).
2. **Use a read-only Neo4j transaction** in `ctx.match()`: `session.begin_transaction(access_mode="READ")` or use `session.read_transaction()`.
3. Add a regex whitelist in the compiler for the MATCH pattern (only `()`, `[]`, `{}`, `-`, `>`, `<`, `:`, alphanumerics, whitespace, quotes).

### 2.3 CRITICAL: `_evaluate_rules` Uses Shared Sync Session Across All Rules

**File:** `daemon.py:569`

```python
with self.sync_driver.session() as session:
    for rule_id, rule_data in self._compiled_rules.items():
```

All rules share the same Neo4j session. If rule A's `ctx.match()` fails (e.g., transaction timeout, network blip), the session may enter a broken state, and all subsequent rules in the loop silently fail or behave unpredictably. There is a `try/except` per rule (line 592), but the session itself may be tainted.

**Recommendation:** Create a fresh session per rule, or at minimum per `ctx.match()` call. The performance cost is negligible compared to the correctness risk.

---

## 3. Important Improvements

### 3.1 Resolver: SUB Negation Assumes Numeric Values

**File:** `resolver.py:67`

```python
for intent in subs:
    current = _safe_add(current, -intent.value)
```

The unary `-` on `intent.value` will raise `TypeError` for non-numeric values (strings, None, datetimes). The `_safe_add` fallback on line 87 would then replace the current value with `-intent.value`, which is nonsensical.

**Fix:** Guard SUB operations: if `intent.value` is not numeric, log a warning and skip.

### 3.2 Resolver: ADD/SUB Order Is Non-Deterministic

**File:** `resolver.py:64-67`

```python
for intent in adds:
    current = _safe_add(current, intent.value)
for intent in subs:
    current = _safe_add(current, -intent.value)
```

The iteration order of `adds` and `subs` depends on the order intents were appended to the list, which depends on rule evaluation order in `_evaluate_rules`. While ADD/SUB are mathematically commutative for numbers, `_safe_add`'s fallback path (line 87: `return b`) is **not** commutative — it replaces the accumulator with the last failing value. For robustness, sort intents by `(rule_id, priority)` before applying, so the fallback path is at least deterministic.

### 3.3 Compiler: `_compile_negation` Recurses on Wrong Child

**File:** `compiler.py:297-298`

```python
if d == "negation":
    inner = self._compile_expr_tree(node.children[0])
```

In the grammar, `not_expr: NOT_OP not_expr -> negation`. So `node.children` is `[NOT_OP_token, not_expr_tree]`. `children[0]` is the `NOT_OP` token, not the expression. This should be `node.children[1]` or should filter for `Tree` children.

**Actual behavior:** `_compile_expr_tree` receives a `Token`, falls through all the `if d ==` checks (since Tokens don't have `.data`), and would raise `AttributeError`. However, this may be masked by Lark's tree simplification (`?not_expr` with `?` prefix may inline children).

**Recommended fix:** Filter to Tree children: `inner = [c for c in node.children if isinstance(c, Tree)][0]`, or handle the token index explicitly.

### 3.4 Compiler: `_compile_neg` Has the Same Issue

**File:** `compiler.py:300-301`

```python
if d == "neg":
    inner = self._compile_expr_tree(node.children[0])
```

Same problem: `factor: MINUS atom -> neg` means `children[0]` is the MINUS token. Should be `children[1]` or filtered.

### 3.5 Daemon: `_create_rule_intent` Missing Agent Link

**File:** `daemon.py:599-636`

The created Intent node is linked to a Capability (line 625-629) but **not** to the originating Agent via `[:PROPOSED]`. The `_process_pending_intents` query (line 229-236) requires `(agent:Agent)-[:PROPOSED]->(i:Intent)`, so rule-created Intents will **never be picked up** by the intent processing pipeline.

**Fix:** The `_create_rule_intent` method needs to also create a `[:PROPOSED]` relationship from the target agent (or the rule's matched agent) to the Intent node.

### 3.6 Daemon: Rule Evaluation Only Happens During Sweep

**File:** `daemon.py:478-480`

```python
if self._compiled_rules:
    await asyncio.get_event_loop().run_in_executor(
        None, self._evaluate_rules
    )
```

Rules are only evaluated during the periodic sweep (default: every 15 minutes). For health-check rules with `EVERY "PT5M"`, this means a 5-minute rule might wait up to 15 minutes to be evaluated. The EVERY block's `should_run_schedule` will fire when eventually evaluated, but the latency is surprising.

**Recommendation:** Either evaluate rules every tick (with the EVERY guard preventing over-firing), or make rule evaluation frequency independently configurable.

### 3.7 Compiler: Unknown Functions Pass Through Unsafely

**File:** `compiler.py:359-364`

```python
# Unknown function — pass through as ctx method
if args_node:
    args = [...]
    return f"ctx.{func_name}({', '.join(args)})"
return f"ctx.{func_name}()"
```

Any function name not in `FUNC_MAP` becomes a `ctx.{name}()` call. This means a rule author can call any method on `RuleContext`, including internal methods. A rule with `LET x = session()` would compile to `ctx.session()` and return the raw Neo4j session object.

**Fix:** Maintain an explicit allowlist. Reject unknown function names at compile time.

### 3.8 Runtime: `node_id()` Fallback to `id(node)` Is Fragile

**File:** `runtime.py:144`

```python
return str(id(node))
```

If a Neo4j node doesn't have `element_id`, `_element_id`, or `id`, the Python object identity is used. This changes between rule evaluations (dict results from `ctx.match()` are ephemeral), so intents from different evaluations targeting the "same" node would get different IDs, defeating conflict resolution.

**Recommendation:** Raise an error instead of falling back to `id()`. Silent incorrect behavior is worse than a loud failure.

---

## 4. Security Analysis

### 4.1 Threat Model Summary

| Threat | Vector | Current Mitigation | Residual Risk |
|--------|--------|-------------------|---------------|
| Arbitrary code execution | Tampered `compiled_python` in Neo4j | Version check (easily spoofed) | **HIGH** |
| Cypher injection | Malicious MATCH pattern in rule_text | None | **HIGH** |
| Privilege escalation via function passthrough | `ctx.{unknown_func}()` | None | **MEDIUM** |
| Neo4j session leak via `ctx.session` access | Rule can reference `ctx` attributes | Python scope (weak) | **MEDIUM** |
| Daemon credential in source | `daemon.py:789` hardcoded password | Env var override available | **LOW** |
| DoS via expensive MATCH | Rule with unindexed Cypher pattern | None | **LOW** |

### 4.2 Hardcoded Default Password

**File:** `daemon.py:789`

```python
password = os.environ.get("NEO4J_PASSWORD", "hassaleh-dev-2026")
```

The default password is visible in source. While overridden by env vars in production, it should not be in the codebase. Use a sentinel value and fail fast if unset.

### 4.3 `exec()` Namespace Is Unrestricted

**File:** `daemon.py:549`

```python
ns: dict[str, Any] = {}
exec(code, ns)
```

An empty dict inherits Python's default builtins, meaning compiled rules can `import os`, `open()`, `__import__()`, etc. The same applies to `test_compiler.py:56`.

---

## 5. Test Coverage Gaps

### 5.1 No Runtime Unit Tests (`test_runtime.py` missing)

The `RuleContext` class is tested only indirectly through compiler E2E tests. Missing dedicated tests:

| Method | Gap |
|--------|-----|
| `duration()` | No tests for edge cases: `"PT0S"`, `"P1DT12H30M"`, invalid specs, negative values |
| `should_run_schedule()` | Only tested via compiler E2E; no direct test for `last_run=None` (first-run) edge case |
| `prop()` | No test for the `hasattr(__getitem__)` branch, `getattr` fallback, or `None` returns |
| `node_id()` | No tests for `element_id`, `_element_id`, `id`, or fallback paths |
| `clamp()` | Not tested at all |
| `match()` | No test for actual Cypher construction or empty results |
| `log()` / `alert()` | Only tested through E2E; no test for Python logger integration |

### 5.2 Compiler Edge Cases Not Tested

| Scenario | File | Gap |
|----------|------|-----|
| Negation (`not`, `!`) | `compiler.py:296-298` | Potentially buggy (see §3.3), no dedicated test |
| Unary minus (`-x`) | `compiler.py:300-301` | Potentially buggy (see §3.4), no dedicated test |
| `ALERT` without target | `compiler.py:214-218` | `_get_token` returns `""` which is truthy — may generate `ctx.alert("msg", )` |
| Multiple MATCH blocks | — | No test for rules with >1 MATCH at top level |
| Deeply nested IF chains | — | No test beyond 2 levels of nesting |
| `SUBMIT_INTENT` WITH with expressions | — | No test for computed values in WITH clause |
| Boolean/null in expressions | — | `true`, `false`, `null` tested in parser but not through compiler E2E |
| `DURATION_LITERAL` (shorthand `5m`) | — | Not tested through compiler; only ISO 8601 string tested |
| Comment preservation/stripping | — | No test that comments are properly ignored in compiled output |
| Cypher var extraction with complex patterns | `compiler.py:409-413` | No test for multi-hop paths like `(a)-[:X]->(b)-[:Y]->(c)` |

### 5.3 Resolver Edge Cases Not Tested

| Scenario | Gap |
|----------|-----|
| SUB with non-numeric value | Will fail silently (see §3.1) |
| MUL with zero | Tested implicitly but no explicit zero-product test |
| MUL with non-numeric value | `_safe_mul` fallback untested |
| SET + SUB + MUL combined | Only SET+ADD and SET+ADD+MUL tested |
| Very large intent counts | No stress test for resolver performance |
| Datetime values in SET | No test for non-numeric SET values beyond strings |

### 5.4 No Daemon Tests (`test_daemon.py` missing)

The daemon has zero test coverage. Key untested paths:

- `_load_rules()` — cache hit vs. miss, compilation failure handling
- `_evaluate_rules()` — multi-rule evaluation, exception isolation
- `_create_rule_intent()` — Intent node creation, Capability linking
- `_apply_resolved_intents()` — element_id vs. id routing
- `_recover_zombies()` — state transitions
- Shutdown sequence — worker cancellation, Intent failure marking
- Health endpoint — JSON response format

### 5.5 Seed Rules Not Parse-Tested

The two seed rules in `seed_rules.cypher` use escaped single quotes (`\'`) within the Cypher string literal. There is no test that verifies these exact strings parse and compile correctly after extraction from Neo4j. The parser tests use similar but not identical rule text.

---

## 6. Positive Observations

1. **Clean pipeline architecture.** The parse → compile → cache → exec pipeline is well-structured with clear boundaries between stages. Each module has a single responsibility and a small public API.

2. **Deterministic by design.** The deliberate exclusion of distributions, truth values, and randomness from the GWW3 GSL parent language is a strong architectural choice for operational rules.

3. **Conflict resolution is principled.** The SET → ADD/SUB → MUL ordering with priority-based SET winning is a well-known pattern from game engine attribute systems. The commutative grouping of ADD/SUB and MUL is correct.

4. **Injectable time.** `RuleContext.now` accepts an injectable datetime, making rules testable without time mocking. The compiler E2E tests use this effectively.

5. **Compilation caching with version invalidation.** Storing `compiled_python` and `compiler_version` in Neo4j avoids re-parsing on every daemon boot. The version key ensures stale compiled code is regenerated when the compiler changes.

6. **Earley parser choice.** Earley handles the ambiguous grammar (MATCH_LINE captures colons that also appear in Cypher patterns) correctly, avoiding the need for a separate lexer mode.

7. **Graceful daemon lifecycle.** Signal handling, zombie recovery, worker reaping, and the shutdown sequence are all well-implemented. The `_shutdown_event` pattern with `asyncio.wait_for` is a clean way to make the tick loop interruptible.

8. **Good test coverage for the core path.** 87 tests across parser, compiler, and resolver cover the main language constructs thoroughly. The E2E tests in `test_compiler.py` (lines 285-415) are particularly valuable — they validate the full parse → compile → exec → inspect-outputs cycle.

9. **Seed rules are realistic.** The agent health check (with circuit breaker) and task timeout sweep are genuine operational patterns, not toy examples.

10. **Grammar is extensible.** The `?statement` alternatives and `action_stmt` umbrella make it straightforward to add new statement types (e.g., `EMIT_EVENT`, `SCHEDULE`) without grammar surgery.

---

## Summary of Action Items

| # | Severity | Item | Section |
|---|----------|------|---------|
| 1 | **CRITICAL** | Validate/recompile cached Python; restrict `exec()` builtins | §2.1 |
| 2 | **CRITICAL** | Prevent Cypher injection in `ctx.match()` — use read-only transactions + keyword filter | §2.2 |
| 3 | **CRITICAL** | Use per-rule Neo4j sessions in `_evaluate_rules` | §2.3 |
| 4 | **HIGH** | Fix missing `[:PROPOSED]` link in `_create_rule_intent` | §3.5 |
| 5 | **HIGH** | Reject unknown function names at compile time | §3.7 |
| 6 | **MEDIUM** | Fix negation/neg child indexing in compiler | §3.3, §3.4 |
| 7 | **MEDIUM** | Sort ADD/SUB intents for deterministic fallback | §3.2 |
| 8 | **MEDIUM** | Raise on `node_id()` fallback instead of using `id()` | §3.8 |
| 9 | **MEDIUM** | Decouple rule evaluation from sweep interval | §3.6 |
| 10 | **LOW** | Remove hardcoded default password | §4.2 |
| 11 | **TEST** | Add `test_runtime.py` with dedicated RuleContext unit tests | §5.1 |
| 12 | **TEST** | Add `test_daemon.py` with mocked Neo4j | §5.4 |
| 13 | **TEST** | Test compiler edge cases: negation, alert-no-target, complex Cypher vars | §5.2 |
| 14 | **TEST** | Test resolver edge cases: non-numeric SUB, datetime SET | §5.3 |
