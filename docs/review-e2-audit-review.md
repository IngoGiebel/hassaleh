# E2 — Audit Review: Intent Lifecycle Security (Sprint 10 Sign-Off)

**Reviewer:** Dione 🌙
**Date:** 2026-04-17
**Project:** Hassaleh
**Phase:** E2 (Audit Review)
**Inputs:**
- `docs/security-audit-intent-lifecycle.md` — Inanna via worker-opus (primary, 11 findings)
- `docs/security-audit-intent-lifecycle-codex.md` — Inanna via worker-codex (peer, 5 findings)

**Purpose:** Cross-validate the two parallel E1 audits, verify key claims against the code, and issue a final sign-off for Sprint 10 security work.

---

## 1. Scope and Methodology Recap

Both E1 audits examined the same codepaths:
`intent_daemon.py`, `intent_sdk.py`, `capabilities/`, `auth.py`, `daemon.py`, `errors.py`, `heartbeat_sdk.py`, `sdk.py`.

Both consumed the same prior context (A7 security review, A9 fixes in `b0b4c08`, B6 heartbeat approval).

**Methodological differences:**
- **Primary (worker-opus):** deeper, wider surface — 11 findings across CRITICAL/HIGH/MEDIUM/LOW/INFO. Surfaces second-order risks (capability trust boundary, dispatch fallthrough, health endpoint exposure, heartbeat sequencing correctness).
- **Peer (worker-codex):** tighter, more focused — 5 findings limited to the most concrete and exploitable issues. Acts as a confirmation signal for the headline flaws.

Running two audits in parallel was the right call: the peer confirms the critical findings without anchoring on the primary's framing, and the primary widens the net where the peer intentionally narrowed it.

---

## 2. Consolidated Findings Table

| ID (primary) | ID (peer)    | Severity   | Title                                                                 | Agreement     | Verified |
|--------------|--------------|------------|------------------------------------------------------------------------|---------------|----------|
| IL-01        | HLI-SEC-001  | CRITICAL   | `sdk.py` accepts caller-supplied `agent_id`, no auth                  | ✅ Full       | ✅       |
| IL-02        | HLI-SEC-002  | HIGH       | `IntentSDK._authenticate()` O(N) bcrypt scan                          | ✅ Full       | ✅       |
| IL-03        | HLI-SEC-003  | HIGH / MED | `sdk.py.poll_intent` / `wait_for_intent` — unauthenticated result read | ⚠️ Sev diff  | ✅       |
| IL-04        | HLI-SEC-004  | MEDIUM     | `exec-ls` + daemon error paths leak host filesystem details           | ✅ Full       | ✅       |
| IL-05        | —            | MEDIUM     | TOCTOU between `validate_exec_ls_path()` and `execute_ls()`           | Primary only  | ✅       |
| IL-06        | —            | MEDIUM     | Heartbeat `previous_heartbeat` sequencing ambiguity                   | Primary only  | ✅       |
| IL-07        | —            | MEDIUM     | Daemon trusts graph-sourced `invoke_command` / `exec_as_user`         | Primary only  | ✅       |
| IL-08        | —            | LOW        | Intent SDK readers don't validate `intent_id` as UUID                 | Primary only  | ✅       |
| IL-09        | —            | LOW        | Generic `ValueError` for domain errors                                | Primary only  | ✅       |
| IL-10        | —            | LOW        | Health endpoint binds `0.0.0.0` by default                            | Primary only  | ✅       |
| IL-11        | HLI-SEC-005  | LOW        | Dev-default Neo4j credentials in runtime entrypoints                  | ✅ Full       | ✅       |
| IL-INFO      | —            | INFO       | `process_intent()` hardcoded to `exec-ls`, silent on unknown caps     | Primary only  | ✅       |

### Severity divergence: IL-03 vs HLI-SEC-003

Primary rates the legacy polling exposure **HIGH**; peer rates it **MEDIUM**. **Dione's call: HIGH.**

The peer's MEDIUM framing treats intent-ID unguessability as partial mitigation. But the polling endpoint returns **execution artifacts** — stdout, stderr, `exit_code`, `error_reason` — which per IL-04 already leak host paths and OS context. Combined with IL-01 (forged intents carrying chosen IDs), the effective exposure is "read any agent's execution output once you can submit a forged intent and know its own ID." That's HIGH.

---

## 3. Verification Notes

Every finding was spot-checked in the current code tree. Details below.

### IL-01 / HLI-SEC-001 — CRITICAL, verified

`src/hassaleh/sdk.py:188-269`. `HassalehSDK.submit_intent(agent_id, action, ...)` takes `agent_id` as a plain parameter, never consults an API key, and writes `(agent:Agent {id: $agent_id})-[:PROPOSED]->(i:Intent ...)` via `MATCH`+`CREATE`. There is **no ownership proof, no API-key check, and no capability gate at submission.** The daemon's `_check_capability()` uses the `PROPOSED` edge as authority, so a forged edge directly unlocks `sudo -u <exec_user>` capability execution.

This is the real thing. Ship-blocker on its own.

### IL-02 / HLI-SEC-002 — HIGH, verified

`src/hassaleh/intent_sdk.py:85-104`. `_authenticate()` executes:

```python
result = await session.run(
    "MATCH (a:Agent) "
    "WHERE a.api_key_hash IS NOT NULL "
    "  AND a.lifecycle IN ['active', 'running'] "
    "RETURN a.id AS agent_id, a.api_key_hash AS hash"
)
records = [record async for record in result]
for record in records:
    if verify_api_key(api_key, record["hash"]):
        return record["agent_id"]
```

This is a per-request O(N) bcrypt scan. The correct primitive already exists at `src/hassaleh/auth.py:26-33` (`lookup_hash()`) and is used correctly in `src/hassaleh/heartbeat_sdk.py` (deterministic SHA-256 candidate lookup + single `bcrypt.checkpw`). The intent path simply wasn't migrated.

Practical impact: with N active agents, invalid keys cost N × bcrypt-cost (~100 ms each at default rounds). A handful of concurrent bad-key requests saturates CPU.

### IL-03 / HLI-SEC-003 — HIGH (Dione's call), verified

`src/hassaleh/sdk.py:273-323`. `poll_intent()` and `wait_for_intent()` call `self.query(...)`, which only gates **write keywords** — no authentication, no ownership verification. Any caller with bolt-level reach can read lifecycle + stdout/stderr/error_reason for any known intent ID.

### IL-04 / HLI-SEC-004 — MEDIUM, verified

`src/hassaleh/capabilities/exec_ls.py:67-73` raises `CapabilityParamError` strings containing the **post-`realpath()` canonical path**. `src/hassaleh/intent_daemon.py:260-262, 278-280` funnel those strings verbatim into `Intent.error`, and `IntentSDK.get_intent_result()` returns `error` to the owning agent. `execute_ls()` also passes raw `ls` stderr through `RuntimeError(result.stderr.strip())`.

Distinguishable error classes ("not in allowed scope" vs "does not exist" vs "not a directory") enable filesystem probing; symlink-target disclosure via realpath is the most concerning piece.

### IL-05 — MEDIUM, verified (primary only)

`validate_exec_ls_path()` resolves the path then returns it; `process_intent()` at `intent_daemon.py:256-268` calls `execute_ls(resolved_path, ...)`, which runs `ls -la resolved_path` without `O_NOFOLLOW` / `O_DIRECTORY`. Between the two calls the filesystem can change. Exploitability depends on who has write access under `/app` and `/data`; if agents or low-priv users can, this becomes an information-disclosure escape.

The peer audit missed this. It's a real finding.

### IL-06 — MEDIUM, verified (primary only)

`heartbeat_sdk.py:164-165`:

```cypher
SET a.last_heartbeat = datetime(),
    a.previous_heartbeat = a.last_heartbeat,
```

Neo4j's documented `SET` semantics evaluate RHS against a consistent snapshot within a clause, so this happens to be correct — but the code reads as "assign new, then copy new to previous." A reader should not have to rederive that. This is correctness-by-accident, not by design.

Not a security blocker; bookkeeping/observability concern.

### IL-07 — MEDIUM, verified (primary only)

`daemon.py` `_execute_capability()` reads `cap.invoke_command` and `cap.exec_as_user` straight from the graph and shells them via `sudo -n -u <exec_user> <command>`. List-form kills shell injection, but **the full security boundary is graph integrity**. Any writer that reaches `Capability` nodes picks the binary and the target user. Combined with IL-01, this is a direct lateral-movement path.

The peer missed this one too. It matters.

### IL-08 / IL-09 / IL-10 / IL-11 / IL-INFO — LOW / INFO, verified

Spot-checked; all accurately described. No objections.

### A9 fixes — no regressions observed

Both audits agree and code confirms: A7-S1 (transition TOCTOU), A7-S4 (`claimed_by` hijack), A7-S6 (`capability_id` regex), A7-S7 (params size cap), A8/Q1 (execution source of truth) are correctly fixed in `b0b4c08` and still hold.

---

## 4. Remediation Priority

**Tier 1 — SHIP BLOCKERS (Sprint 11, must close before any external reach):**
1. **IL-01** — eliminate or authenticate `HassalehSDK.submit_intent()`. Preferred: remove intent submission from `sdk.py` entirely; route all agent writes through `IntentSDK`. Minimum: require API-key auth and derive `agent_id` server-side, same model as `IntentSDK.submit_intent()`.
2. **IL-03** — eliminate or authenticate `HassalehSDK.poll_intent()` / `wait_for_intent()`. Same pattern as IL-01.

**Tier 2 — BEFORE BROADER EXPOSURE (Sprint 11 target, blocking for prod rollout):**
3. **IL-02** — migrate `IntentSDK._authenticate()` to the `lookup_hash` + single-bcrypt pattern. Primitive already exists; this is a port, not a design task. Include backfill of `api_key_lookup` for existing agents.
4. **IL-07** — local allowlist mapping `capability_id → (binary, allowed_user)`. Reject any `Capability` that doesn't match at load time.

**Tier 3 — HARDENING (trackable, next sprint):**
5. **IL-04** — normalize agent-visible errors; move detail to server-side logs with correlation IDs.
6. **IL-05** — close exec-ls TOCTOU with `O_NOFOLLOW` / dirfd, or restrict writable paths under the allowed bases.

**Tier 4 — CLEANUP (trackable, no deadline):**
7. **IL-06** — rewrite heartbeat Cypher for explicit old→previous sequencing + add the N≥2 regression test.
8. **IL-08 / IL-09** — UUID validation at SDK boundary; domain exception hierarchy for Intent errors.
9. **IL-10 / IL-11** — loopback-default health bind; fail-closed on missing Neo4j credentials gated by `HASSALEH_DEV_MODE=1`.
10. **IL-INFO** — handler registry + fail-fast `rejected` transition on unknown capability.

---

## 5. Final Verdict

## **BLOCKED_UNTIL_FIXED**

Minimum bar to move to APPROVED_WITH_NOTES: **IL-01 and IL-03 closed.**

### Why not PASS_WITH_NOTES

The primary audit proposed PASS_WITH_NOTES *conditional on `sdk.py` being daemon-internal and technically prevented from being called by agent processes.* On inspection, no such technical enforcement exists in the repo — `sdk.py` is a regular importable module with the same Neo4j auth as the hardened path, reachable from any process with bolt credentials. The condition is currently unmet.

IL-01 is not a latent risk — it is an active bypass of the entire authenticated intent lifecycle. While it stands, the E1 hardening of `intent_sdk.py` is advisory: anyone with module-level access can submit arbitrary intents as any agent, and IL-03 lets them read the results back. That combination is a ship-blocker.

### Why not FAIL (categorical)

The dedicated MVP pipeline (`intent_sdk.py` + `intent_daemon.py`) is genuinely in good shape. Parameterized Cypher is consistent, `claim_intent()` is atomic, transition CAS is correct, ownership checks are enforced on both read paths, and `exec_ls.execute_ls()` is list-form / no-shell. The remediation for IL-01 and IL-03 is well-scoped (remove the two legacy methods or wrap them in the existing auth pattern) — it is not architectural rework.

### Heartbeat / B6 status

IL-06 does not reopen B6. The token-chain, rate-limit, and replay-protection logic remain sound; IL-06 is a correctness-by-accident smell in the `previous_heartbeat` assignment, not an auth flaw. **B6 approval stands.**

---

## 6. Sprint 11 Guidance

### Must block ship (Sprint 11 exit criteria)
- **IL-01** closed (legacy `HassalehSDK.submit_intent()` removed or authenticated).
- **IL-03** closed (legacy `HassalehSDK.poll_intent()` / `wait_for_intent()` removed or authenticated).
- Re-audit (call it E3) to confirm the legacy surface is gone or hardened, and that no new import of `HassalehSDK.submit_intent()` exists in agent code paths.

### Strongly recommended in Sprint 11 (blocking for prod, not for internal sprint close)
- **IL-02** migrated to `lookup_hash` pattern — low-effort, high-value, primitive already exists.
- **IL-07** capability allowlist — before any additional capability beyond `exec-ls` is registered.

### Tracked in backlog (can defer past Sprint 11)
- IL-04, IL-05, IL-06, IL-08, IL-09, IL-10, IL-11, IL-INFO — file as individual issues with the severity noted, linked to this E2 document.

### Suggested sequencing
1. IL-01 + IL-03 in one PR (they touch the same file; natural unit of work).
2. IL-02 as a follow-up PR with `api_key_lookup` schema migration.
3. IL-07 before any multi-capability work begins.
4. IL-04 bundled with IL-05 (both touch the exec-ls path and error surface).

### Notes for the implementer
- When removing `HassalehSDK.submit_intent()`, also audit callers: `review_task()` on line 378 performs a nearly identical unauthenticated write and needs the same treatment. (Worth flagging to Ingo — neither E1 audit called this out explicitly, but it's the same class of flaw in the same file.)
- The `IntentSDK` auth migration should be covered by a property-based test that asserts constant-time-ish behavior across |active agents| ∈ {1, 10, 100}. Without that test this will regress.

---

**Sign-off:** Dione 🌙 — E2 review complete, verdict BLOCKED_UNTIL_FIXED pending IL-01 + IL-03 remediation. Escalating to Ingo for Sprint 11 scope decision.
