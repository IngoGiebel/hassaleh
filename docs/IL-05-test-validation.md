# IL-05 test validation

Date: 2026-04-19
Repo: `~/projects/hassaleh`
Target: IL-05 TOCTOU fix in `src/hassaleh/capabilities/exec_ls.py`
Spec reference: `docs/security-audit-intent-lifecycle.md` § IL-05
Implementation note reviewed: `docs/IL-05-implementation.md`

## Test runs

### Targeted IL-05 suite — run 1
Command:
```bash
pytest tests/test_il05_toctou.py -v
```

Result:
- `tests/test_il05_toctou.py::test_final_component_symlink_swap_rejected` — PASS
- `tests/test_il05_toctou.py::test_intermediate_component_symlink_swap_rejected` — PASS
- `tests/test_il05_toctou.py::test_no_swap_happy_path_still_works` — PASS

Summary: **3 passed**

### Targeted IL-05 suite — run 2
Command:
```bash
pytest tests/test_il05_toctou.py -v
```

Result:
- `tests/test_il05_toctou.py::test_final_component_symlink_swap_rejected` — PASS
- `tests/test_il05_toctou.py::test_intermediate_component_symlink_swap_rejected` — PASS
- `tests/test_il05_toctou.py::test_no_swap_happy_path_still_works` — PASS

Summary: **3 passed**

### Broader regression
Command:
```bash
NEO4J_URI=bolt://localhost:7690 \
NEO4J_USER=neo4j \
NEO4J_PASSWORD=hassaleh \
NEO4J_TEST_URI=bolt://localhost:7691 \
NEO4J_TEST_USER=neo4j \
NEO4J_TEST_PASSWORD=hassaleh-dev-2026 \
pytest tests/test_sdk.py tests/test_chaos.py -x -q
```

Result:
- `tests/test_sdk.py` — PASS
- `tests/test_chaos.py` — PASS

Summary: **29 passed**

## PASS / FAIL per test

### IL-05 targeted tests
- `test_final_component_symlink_swap_rejected` — **PASS**
- `test_intermediate_component_symlink_swap_rejected` — **PASS**
- `test_no_swap_happy_path_still_works` — **PASS**

### Broader regression
- `tests/test_sdk.py` — **PASS**
- `tests/test_chaos.py` — **PASS**

## Coverage analysis vs spec

Required validation points:

1. **Final-component symlink swap after validate**
   - Covered by `test_final_component_symlink_swap_rejected`
   - This matches the spec’s final-component attack variant.
   - The test verifies rejection after validation succeeds and the last path segment is replaced by a symlink.

2. **Intermediate-component symlink swap**
   - Covered by `test_intermediate_component_symlink_swap_rejected`
   - This matches the spec’s intermediate-component attack variant.
   - The test correctly exercises the case plain `O_NOFOLLOW` would miss and relies on the canonical-path recheck to detect the swap.

3. **Happy path in `/app` and `/data`**
   - **Partially covered only.**
   - `test_no_swap_happy_path_still_works` verifies the normal success path, but it scopes `EXEC_LS_ALLOWED_BASES` to a temporary synthetic base rather than explicitly covering both `/app` and `/data` separately.
   - From a unit/regression perspective this is acceptable for the path-handling logic, because the core behavior depends on allowed-base matching rather than those literal mount names.
   - From a strict wording-of-request perspective, the suite does **not** contain separate explicit happy-path tests for both `/app` and `/data`.

Assessment:
- The suite is strong on the two security-critical TOCTOU attack variants.
- Coverage is sufficient to validate the actual mitigation behavior.
- If you want literal completeness against the request text, add one explicit happy-path test for `/app` and one for `/data` names/mounts. That is an enhancement, not a blocker for the security claim made by IL-05.

## Flakiness observations

- The IL-05 suite passed twice consecutively.
- Run times were stable and low (`0.20s` then `0.08s`).
- No intermittent failures, retries, or timing-sensitive assertions were observed.
- The tests are filesystem-manipulation tests, but in their current form they are deterministic because they simulate the race in-process rather than depending on thread scheduling or external timing.

Conclusion on flakiness: **no flakiness observed in this validation pass**.

## Overall assessment

- The IL-05 implementation appears to be correctly validated for the two attack classes described in the spec.
- Broader regression checks (`test_sdk.py` + `test_chaos.py`) remained green under the requested environment.
- The only notable gap is that the happy-path test does not separately name-check both `/app` and `/data`; it validates equivalent allowed-base behavior through a patched test base.

## Verdict

**READY FOR SECURITY-REVIEW**
