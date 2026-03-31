# Architecture Review — Hassaleh Sprints 2-4

**Reviewer:** Gemini Deep Think (via Web UI, gemini-2.5-pro)
**Date:** 2026-03-31
**Verdict:** "Production-grade in philosophy" / "Excellent trajectory"

---

## Key Findings

### Security
- **`__import__` in safe_builtins is risky** — can load arbitrary modules. Fix: pre-inject RuleContext into namespace, remove `__import__`.
- **`execute_read()`** is the strongest guardrail ✅
- **`isidentifier()` for property names** is sensitive but acceptable

### Performance
- **`_fetch_current_values` has N+1 query problem** — separate query per property/node pair. Fix: batch with UNWIND.

### Compiler Edge Cases
- **MATCH_LINE regex** fails on Cypher with inline string colons (e.g. `{url: "http://..."}`)
- **`_extract_cypher_vars` regex** may extract false positives from property values
- **Same-priority SET conflict** is non-deterministic — should log warning

### Minor
- `_compile_expr_list` join may produce `not True` without parens (works but fragile)
- `ctx.match()` may fail on relationship results (not fully dict-serializable)
- `uuid` import inside method body (inconsistent but functional)

### CONCEPT.md
- 95% consistent. TimeBucket archiving mentioned but not implemented (acceptable for Sprint 4).

### Positive
- "One of the most structured agent frameworks available"
- Trusted Daemon + sudo -n -u = "brilliant"
- Atomic claim pattern = horizontally scalable
- Bridge async/sync context = well-navigated
- CLI = "excellent for Ops workflows"
- Avoids "messy YAML trap"

### Suggestions
- `hassaleh logs` command for Sprint 5
- Consider "Veto" status for high-priority agents in resolver
- Batch _fetch_current_values with UNWIND
