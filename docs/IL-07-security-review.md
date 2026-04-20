# IL-07 security review — graph-write trust boundary on capability execution

Date: 2026-04-20
Reviewer: Inanna (security)
Branch: `trunk`
Scope: `src/hassaleh/daemon.py::_execute_capability` + `CAPABILITY_ALLOWLIST`
Inputs reviewed:
- `docs/IL-07-implementation.md`
- `docs/IL-07-test-validation.md`
- `src/hassaleh/daemon.py` (lines 58–90, 443–477, 744–752)
- `tests/test_il07_trust_boundary.py`

## 1. Threat model

### Asset
The daemon runs capabilities as privileged service accounts via `sudo -n -u
<exec_user> <invoke_command> <args...>`. The pair `(invoke_command,
exec_as_user)` therefore controls **which binary executes** and **under which
OS uid**. Both fields are sourced from the graph (`Capability` node properties).

### Attacker
Any actor with write access to a `Capability` node in Neo4j. This includes:
- a compromised graph-write credential (Cypher injection upstream, leaked
  service password, lateral movement from another component that has
  Neo4j write),
- an authoring tool with overly broad write scope (rule-author, admin
  console, manual Cypher),
- a malicious migration or seed script merged through a weak code-review
  path.

The attacker is assumed **not** to have:
- daemon process-host filesystem write,
- ability to modify shipped Python sources,
- direct invocation of `_execute_capability` (it is reached only via a
  scheduled Intent).

### Goals the attacker would pursue

1. **Binary substitution.** Repoint `invoke_command` from the policy-blessed
   binary (e.g. `/usr/bin/ls`) to an arbitrary executable
   (`/usr/bin/env`, `/bin/sh`, a dropped script, a setuid helper).
2. **Argv smuggling via shell.** Inject shell metacharacters into
   `invoke_command` (e.g. `"/usr/bin/ls; touch /tmp/pwned"`) hoping for shell
   evaluation downstream.
3. **Privilege escalation via uid swap.** Replace `exec_as_user` with a more
   privileged account (`root`, `hassaleh-daemon` from a `hassaleh-fs` slot,
   `hassaleh-audit` from a non-audit slot) to gain that account's sudoers
   reach.
4. **Capability identity confusion.** Submit a `Capability` whose `id` is not
   in the local allowlist at all, hoping the daemon trusts the graph as the
   source of truth for capability identity.

### Out of scope for IL-07

- `args` content (covered by IL-04 path-scope enforcement for the `exec-ls`
  family; per-capability arg policy is a separate roadmap item).
- Sudoers configuration on the host (treated as an environmental control;
  IL-07 does not weaken it).
- TOCTOU on the graph row between read and subprocess launch — there is no
  second graph read of `(command, exec_user)` between validation and the
  `create_subprocess_exec` call, so there is no in-process re-read window
  to race.

## 2. Line-referenced analysis

### 2.1 Allowlist as in-process trust root

`src/hassaleh/daemon.py:74-83` defines `CAPABILITY_ALLOWLIST` as a module-scope
`dict[str, tuple[str, str]]` mapping `capability_id` to
`(allowed_invoke_command, allowed_exec_as_user)`.

Findings:
- Defined at module scope, read-only at runtime (no daemon code path mutates
  it; tests only mutate via `monkeypatch.setitem`, which is reverted after
  each test).
- Mutating the allowlist requires a code change + review (documented at
  lines 69–71). Cypher writes cannot extend it. PASS.
- Each value is a fixed `(allowed_invoke_command, allowed_exec_as_user)`
  tuple; there is no per-row variability that the graph could influence. PASS.

### 2.2 Capability-id gate (unknown-id branch)

`src/hassaleh/daemon.py:462-466`:

```
if cap_id not in CAPABILITY_ALLOWLIST:
    cid = uuid.uuid4().hex
    log.error(f"Intent {intent_id} validation failed [cid: {cid}]: capability {cap_id} not in allowlist")
    await self._fail_intent(intent_id, f"Parameter validation failed [cid: {cid}]")
    return
```

Findings:
- Hard early return: an unknown `cap_id` cannot reach the subprocess launch. PASS.
- Server-side log retains the offending `cap_id` and the correlation id;
  agent-visible reason is sanitized to `Parameter validation failed [cid: …]`. PASS.
- This branch is also **structurally load-bearing**: it guards the
  `CAPABILITY_ALLOWLIST[cap_id]` dict access on line 468. If a future refactor
  collapsed it on the assumption "the mismatch branch will catch it anyway,"
  an unknown id would raise `KeyError` and produce an unsanitized failure
  path. (See §4 — recommended follow-up regression.)

### 2.3 Tuple-equality gate (binary + uid mismatch branch)

`src/hassaleh/daemon.py:468-477`:

```
allowed_cmd, allowed_user = CAPABILITY_ALLOWLIST[cap_id]
if command != allowed_cmd or exec_user != allowed_user:
    cid = uuid.uuid4().hex
    log.error(
        f"Intent {intent_id} validation failed [cid: {cid}]: capability {cap_id} values "
        f"(cmd={command!r}, user={exec_user!r}) do not match allowlist "
        f"(expected cmd={allowed_cmd!r}, user={allowed_user!r})"
    )
    await self._fail_intent(intent_id, f"Parameter validation failed [cid: {cid}]")
    return
```

Findings:
- Exact-string equality (`!=`), no normalization, no `startswith`, no glob,
  no realpath rewrite. This forecloses every common bypass (case folding,
  trailing slash, symlink alias, equivalent absolute path, leading `./`,
  null-byte truncation, unicode confusables). PASS.
- The `or` short-circuit is fine here because both halves are checked before
  any subprocess launch; there is no asymmetric trust between `command` and
  `exec_user`. PASS.
- Goal #1 (binary substitution): rejected — any deviation from `allowed_cmd`
  fails. PASS.
- Goal #2 (shell metacharacter smuggling): rejected at this gate **and**
  again defended in depth by the list-form subprocess launch on line 495,
  which bypasses any shell. The covered test
  `test_graph_injected_shell_metacharacter_in_invoke_command_rejected`
  exercises this. PASS.
- Goal #3 (uid escalation): rejected — `exec_as_user="root"` (or any other
  non-blessed account) fails. The covered test
  `test_graph_injected_non_allowlisted_uid_rejected` exercises this. PASS.

### 2.4 Error-surface analysis (no allowlist disclosure)

`src/hassaleh/daemon.py:744-752` (`_fail_intent`):

```
async def _fail_intent(self, intent_id: str, reason: str) -> None:
    async with self.driver.session() as session:
        await session.run("""
            MATCH (i:Intent {id: $id})
            SET i.lifecycle = 'failed',
                i.error_reason = $reason,
                i.completed_at = datetime({timezone: 'UTC'})
        """, id=intent_id, reason=reason)
    log.error(f"Intent {intent_id} failed: {reason}")
```

Findings:
- The `reason` written to `Intent.error_reason` is **only** the sanitized
  `Parameter validation failed [cid: <hex>]` string. The expected binary
  path, expected uid, supplied binary, supplied uid, and `cap_id` are
  written **only** to the daemon log, not into the graph. PASS.
- There is no separate write of `actual_command` / `expected_command` /
  `attempted_user` onto the Intent or any sibling node. An agent with
  Intent-read but not host-log access cannot enumerate the allowlist by
  probing. PASS.
- The correlation id is fresh per failure (`uuid.uuid4().hex`) so two
  failures from the same allowlist mismatch are not linkable agent-side. PASS.
- The agent-visible error is **byte-identical** to the IL-04 path-validation
  failure surface, so an attacker cannot distinguish "wrong path" from
  "wrong binary" from "wrong uid" from "unknown capability." This denies
  the oracle. PASS.

### 2.5 Absence of bypass paths

I searched for alternative entry points to subprocess launch that might
sidestep the allowlist:

- `_execute_capability` is the only caller of `asyncio.create_subprocess_exec`
  in `daemon.py` (single grep match, line 495).
- The graph-read query at lines 447–450 fetches only `cap.id`,
  `cap.invoke_command`, `cap.exec_as_user` — there is no second source for
  these values that could be substituted later in the function.
- `args` are appended **after** the validated `command`, and
  `sudo -n -u <user> <command> <args…>` runs `<command>` as `<user>`; argv
  cannot retroactively change which binary or uid is launched.

No alternative path observed. PASS.

## 3. Test coverage assessment

| Threat goal                              | Test | Status |
|------------------------------------------|------|--------|
| #1 binary substitution                   | covered transitively by the metacharacter test (any non-equal `command` is rejected by the same code path) | PASS |
| #2 shell metacharacter in `invoke_command` | `test_graph_injected_shell_metacharacter_in_invoke_command_rejected` | PASS |
| #3 uid escalation to `root`              | `test_graph_injected_non_allowlisted_uid_rejected` | PASS |
| #4 unknown `capability_id`               | **not directly tested** | GAP |
| Happy path reaches sudo argv unchanged   | `test_allowlisted_values_reach_subprocess_path` | PASS |
| Sibling regressions (IL-04/05/06)        | 10/10 PASS per `docs/IL-07-test-validation.md` | PASS |

### Judgment on the unknown-capability_id gap

The unknown-id branch (line 462) is:
- a real production branch with its own sanitized error and log line,
- **structurally load-bearing** — without it, line 468 (`CAPABILITY_ALLOWLIST[cap_id]`)
  would raise `KeyError` and produce an unsanitized failure path,
- not currently exercised by any regression test.

The runtime risk today is **zero** (the branch is present and correct).
The maintenance risk is **non-trivial**: a future refactor that fuses the
two checks, swaps `dict` for `defaultdict`, or replaces the allowlist with
a different lookup type could silently change the unknown-id behavior, and
no test would fail.

**Verdict on the gap:** *accept with follow-up ticket*. Not a merge blocker.
The follow-up should add `test_unknown_capability_id_rejected` that:
1. injects no entry for the supplied `cap_id`,
2. asserts the sanitized `Parameter validation failed [cid: …]` reason,
3. asserts the subprocess launch is never awaited,
4. asserts the raw `cap_id` does not appear in the agent-visible reason.

This mirrors the existing two mismatch tests and is mechanical to add.

## 4. Verdict

### **CLEAN — cleared for merge on cycle 20.**

Justification:
- The trust boundary inversion is correct: graph is untrusted, the
  in-process `CAPABILITY_ALLOWLIST` is the sole authority for
  `(capability_id → invoke_command, exec_as_user)`.
- All three documented attacker goals against IL-07 scope (binary swap,
  shell-metacharacter smuggling, uid escalation) are rejected before the
  subprocess launch is reached.
- The error surface to the agent is sanitized, correlation-id-based, and
  identical to other IL-04-class failures, denying the agent any
  enumeration oracle.
- The unknown-`capability_id` branch is present and correct in production
  code; the missing direct regression is a maintenance hardening item, not
  a security defect.

### Follow-up (non-blocking, file before cycle 22)

- **Ticket:** "IL-07: add regression for unknown `capability_id` branch."
  - Owner: tests
  - Scope: 1 new test in `tests/test_il07_trust_boundary.py`,
    sanity-check sibling regressions still green.
  - Rationale: protect the load-bearing early return at
    `daemon.py:462-466` against silent removal in future refactors.

No other blockers, no conditional gates. IL-07 is cleared for merge on
cycle 20.
