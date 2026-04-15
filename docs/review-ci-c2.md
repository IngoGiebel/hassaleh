# CI Review — Sprint 10 Phase C2

**Reviewer:** Dione 🌙
**Date:** 2026-04-14
**File:** `.github/workflows/ci.yml`
**Verdict:** CHANGES_REQUESTED

---

## Checklist Results

### 1. Triggers (push + PR) ✅
Workflow triggers on `push` to `main` and `pull_request` to `main`. Correct.

### 2. Neo4j Service Container ⚠️ Acceptable with notes
- Uses standard Bolt port **7687** (not 7690 dev port). This is fine for CI — the dev port 7690 only matters locally to avoid conflicts.
- `NEO4J_AUTH: none` is appropriate for CI.
- Health check is well-configured with 30 retries at 10s intervals.
- The inline Python wait-for-Neo4j script is thorough (handles auth=None correctly).

### 3. Steps: ruff, mypy, pytest ✅
All three present in correct order: Lint → Type check → Tests. Good separation of unit vs integration tests.

### 4. Dependency Installation ⚠️ Minor concern
- Uses `uv` with caching — good.
- Installs `ruff` and `mypy` alongside `.[dev]` — works, but these could be added to `[project.optional-dependencies].dev` in `pyproject.toml` for single-source-of-truth. Not blocking.
- Uses `--ignore-requires-python` — see issue #1 below.

### 5. Neo4j Environment Variables ✅
- `NEO4J_TEST_URI`, `NEO4J_TEST_USER`, `NEO4J_TEST_PASSWORD` set at job level.
- Empty user/password matches `NEO4J_AUTH: none` on the container side.
- `NEO4J_URI`/`NEO4J_USER`/`NEO4J_PASSWORD` also set (used by non-test code paths).

### 6. Python Version ❌ MISMATCH
`pyproject.toml` declares `requires-python = ">=3.13"` but CI uses **Python 3.12**. The `--ignore-requires-python` flag masks this. See issue #1.

### 7. Schema Setup ❌ MISSING
`test_integration.py` header states: _"Schema + seed must be applied before running."_ No CI step loads `schema.cypher` into Neo4j before running integration tests. See issue #2.

### 8. Security ✅
- `permissions: contents: read` — minimal, correct.
- No secrets in the file; Neo4j auth disabled for CI (empty strings, not real credentials).
- No unnecessary permissions.

---

## Issues

### Issue #1 — Python version mismatch (HIGH)

**Problem:** `pyproject.toml` requires `>=3.13`, CI runs 3.12 with `--ignore-requires-python`.

**Fix:** Change `python-version: "3.12"` → `"3.13"` in both the `setup-python` step and the `uv venv` command. Remove the `--ignore-requires-python` flag.

```yaml
# setup-python step
python-version: "3.13"

# Install dependencies step
uv venv --python 3.13
uv pip install --python .venv/bin/python -e ".[dev]" ruff mypy
```

### Issue #2 — No schema loading before integration tests (MEDIUM)

**Problem:** Integration tests expect constraints and schema to exist in the database. The CI starts a bare Neo4j container with no schema applied.

**Fix:** Add a schema loading step after "Wait for Neo4j" and before tests:

```yaml
- name: Load schema
  run: |
    cat schema.cypher | .venv/bin/python -c "
    import asyncio, os, sys
    from neo4j import AsyncGraphDatabase
    URI = os.environ['NEO4J_TEST_URI']
    async def main():
        driver = AsyncGraphDatabase.driver(URI, auth=None)
        async with driver.session() as s:
            for stmt in sys.stdin.read().split(';'):
                stmt = stmt.strip()
                if stmt and not stmt.startswith('//'):
                    await s.run(stmt)
        await driver.close()
    asyncio.run(main())
    "
```

### Issue #3 — Hardcoded integration test file list (LOW)

**Problem:** Line 103 hardcodes `tests/test_integration.py tests/test_messaging.py tests/test_chaos.py`. If new integration test files are added, they won't run in CI unless this line is updated.

**Suggestion:** Consider using just the marker: `pytest tests -m integration` instead of listing individual files.

---

## Summary

| # | Issue | Severity | Action |
|---|-------|----------|--------|
| 1 | Python 3.12 vs requires-python ≥3.13 | HIGH | Change to 3.13, drop `--ignore-requires-python` |
| 2 | No schema.cypher loaded before integration tests | MEDIUM | Add schema load step |
| 3 | Hardcoded integration test file list | LOW | Optional: use `-m integration` alone |
