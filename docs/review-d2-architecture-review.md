# D2: Architecture Documentation Review

**Reviewer:** Dione
**Date:** 2026-04-16
**Document:** `docs/architecture.md` (v1.0, by Gemini, Sprint 10)
**Method:** Line-by-line cross-reference against codebase in `src/hassaleh/`

---

## Executive Summary

The architecture document is well-structured, clearly written, and covers the core design principles accurately. However, it describes only **one of two** Intent pipeline implementations that exist in the codebase, omits several implemented modules entirely, and marks the heartbeat system as "planned" despite a working implementation already existing. The document reflects roughly 70% of the actual codebase.

**Severity legend:** CRITICAL = factual error or major omission that would mislead a developer. MODERATE = inaccuracy or gap that could cause confusion. MINOR = cosmetic, imprecise, or nice-to-have.

---

## 1. Accuracy Issues

### 1.1 CRITICAL — Two Parallel Intent Pipelines Exist; Only One Is Documented

The document describes the Intent pipeline implemented in `daemon.py` and `sdk.py`. However, the codebase contains a **second, independent Intent pipeline** in `intent_sdk.py` and `intent_daemon.py` that uses a different schema:

| Aspect | Documented (`daemon.py` / `sdk.py`) | Undocumented (`intent_sdk.py` / `intent_daemon.py`) |
|--------|--------------------------------------|------------------------------------------------------|
| State property | `lifecycle` | `status` |
| Agent→Intent edge | `PROPOSED` | `SUBMITTED_BY` |
| Intent→Capability edge | `TARGETS` | `REQUIRES` |
| Auth model | Agent ID passed explicitly | API key → agent_id derived server-side |
| Capability check | `HAS_CAPABILITY` edge + domain scoping | `HAS_CAPABILITY` edge (no domain scoping) |
| Execution | `sudo -n -u <user> <command>` via subprocess | Direct capability module invocation (`exec_ls.py`) |
| Zombie recovery | Fails `running`/`claimed` on boot | Reaper thread + per-instance orphan recovery |
| Additional properties | `source`, `source_rule`, `exit_code` | `claimed_by`, `duration_ms`, `result`, `error` |

The two pipelines create Intent nodes with **incompatible schemas**. A developer reading only the architecture doc would have no idea the second pipeline exists. This needs to be reconciled — either documented as the "MVP test pipeline" or merged.

**Files:** `intent_sdk.py:1-255`, `intent_daemon.py:1-354`

### 1.2 CRITICAL — Heartbeat System Described as "Planned" but Is Implemented

Section 5.2 labels the heartbeat system as _"Planned Heartbeat System (Sprint 10, Task B)"_. In reality, `heartbeat_sdk.py` is a **fully implemented** module (201 lines) with:

- API key authentication via `auth.py` (bcrypt + SHA-256 lookup hash)
- Chained heartbeat tokens (replay protection)
- Server-side rate limiting (60s minimum, enforced in Cypher)
- `disabled` terminal state rejection
- Lifecycle transitions: `pending`/`stale`/`inactive` → `active`

The doc's description of the design is accurate, but the framing as future work is wrong.

**File:** `heartbeat_sdk.py:1-201`

### 1.3 CRITICAL — SDK Performs Direct Writes, Contradicting "Intent-Mediated Writes" Principle

Section 1 states: _"Agents cannot write to the graph directly. They submit Intents; the Daemon processes them."_ Section 2.2 reinforces this: _"The SDK is the agent-facing API. It provides guarded, read-only graph access plus Intent submission."_

However, the SDK performs **direct Neo4j writes** in several methods that bypass the Intent pipeline entirely:

| SDK Method | Write Operation |
|------------|-----------------|
| `send_message()` | Creates `Message` nodes, `SENT`/`NEXT`/`HEAD_OF`/`TAIL_OF` edges |
| `contribute_to_discussion()` | Creates `CONTRIBUTED` edges, sets `Discussion.lifecycle` |
| `resolve_discussion()` | Creates `DECIDED_BY` edge, sets resolution properties |
| `advance_cursor()` | Deletes/creates `LAST_READ` edges |
| `review_task()` | Creates Intent (correct), but still a write |

The messaging and discussion operations are **not** intent-mediated. This is a significant deviation from the stated design principle. The document should either:
- Acknowledge that messaging/discussions bypass the Intent pipeline and explain why, or
- Flag this as a known limitation to be addressed

**File:** `sdk.py:423-669`

### 1.4 MODERATE — Python Version Requirement Mismatch

Section 9.1 states: **Python >= 3.13**
`pyproject.toml:17` states: **requires-python = ">=3.12"**

The shebangs in source files say `python3.13`, but the build config allows 3.12. The document should match `pyproject.toml`.

### 1.5 MODERATE — Missing `bcrypt` Dependency

Section 9.1 lists dependencies as: `neo4j`, `aiohttp`, `lark`.

`auth.py` imports `bcrypt`, which is not listed in the doc. However, `bcrypt` is also not in `pyproject.toml` — it's an **unlisted runtime dependency** that would cause `ImportError` on a fresh install. This is both a doc issue and a packaging bug.

**File:** `auth.py:13`

### 1.6 MODERATE — Approval Workflow State Transition Incorrect

Section 4.3 (Intent Lifecycle State Machine) shows:
```
awaiting_approval → (claimed on approval)
```

The actual implementation in `cli.py:1107-1111` transitions approved intents to `pending` (not `claimed`):
```python
SET i.lifecycle = 'pending',
    i.approved_at = datetime({timezone: 'UTC'})
```

This means the Intent re-enters the normal pending→claimed→running flow, which is arguably better design but doesn't match the diagram.

### 1.7 MINOR — Default Neo4j Password Inconsistency

The document and initialization examples use password `hassaleh`. The SDK default in `sdk.py:71` and `daemon.py:1327` is `hassaleh-dev-2026`. This would cause connection failures if someone follows the doc's init instructions then runs the SDK with defaults.

### 1.8 MINOR — Grammar Line Count

Section 4.2 states the grammar is "220 lines" in `gsl_ops.lark`. The actual file is 220 lines (including blank lines and comments). Accurate.

---

## 2. Completeness Gaps

### 2.1 CRITICAL — Six Source Files Entirely Undocumented

The project structure in Section 12 omits:

| File | Purpose | Lines |
|------|---------|-------|
| `auth.py` | API key generation, bcrypt hashing, SHA-256 lookup hash, verification | 39 |
| `errors.py` | Custom exception hierarchy (8 exception classes) | 37 |
| `heartbeat_sdk.py` | Agent heartbeat interface with chained tokens | 201 |
| `intent_sdk.py` | MVP Intent SDK with API-key auth | 255 |
| `intent_daemon.py` | MVP Intent Daemon with reaper | 354 |
| `capabilities/exec_ls.py` | exec-ls capability: path validation + execution | 99 |

Together, these represent **985 lines** — roughly 40% of the codebase by line count — that are invisible to anyone reading the architecture doc.

### 2.2 MODERATE — CLI Commands Table Incomplete

Section 2.3 lists 10 CLI commands. The actual CLI (`cli.py:1195-1302`) implements **22+ subcommands**:

**Missing from doc:**
- `hassaleh heartbeat <agent_id>` — Send agent heartbeat
- `hassaleh task review <id> --approve/--reject` — Review supervised task
- `hassaleh message list [--context]` — List messages
- `hassaleh discussion list` — List discussions
- `hassaleh discussion show <id>` — Show discussion detail
- `hassaleh skill list [--domain]` — List capabilities with filtering
- `hassaleh skill info <id>` — Capability detail view
- `hassaleh domain list` — Skill domain taxonomy tree
- `hassaleh report agents` — Agent activity report
- `hassaleh report rules` — Rule evaluation report
- `hassaleh report intents` — Intent statistics
- `hassaleh report daily` — Combined daily summary

The report subsystem (`cmd_report_*`) supports `--format {text,json,markdown}` output, which is a significant feature not mentioned anywhere.

### 2.3 MODERATE — `capabilities/` Module Pattern Undocumented

The codebase has a `capabilities/` package (`capabilities/__init__.py`, `capabilities/exec_ls.py`) that implements a **capability module pattern** — capabilities as importable Python modules with validation and execution functions. This pattern is important for understanding how to add new capabilities but is not described in the architecture doc.

`exec_ls.py` implements:
- Path character validation (regex allowlist)
- Traversal attack prevention (`..` detection)
- Canonical path resolution via `os.path.realpath`
- Prefix-based scope restriction (`EXEC_LS_ALLOWED_BASES = ["/app", "/data"]`)
- Output truncation (64KB limit)
- List-form subprocess invocation (no shell injection)

This is a security-critical module that deserves architectural documentation.

### 2.4 MODERATE — Error/Exception Hierarchy Undocumented

`errors.py` defines 8 custom exceptions used across the codebase:

```
AuthenticationError
CapabilityNotFoundError
CapabilityDeniedError
CapabilityParamError
AccessDeniedError
AgentNotFoundError
AgentDisabledError
HeartbeatTokenMismatchError
```

These exceptions form a structured error contract between SDK → Daemon and are important for understanding error handling behavior.

### 2.5 MINOR — No Mention of Report Generation

The CLI includes a full report subsystem (`cmd_report_agents`, `cmd_report_rules`, `cmd_report_intents`, `cmd_report_daily`) with multi-format output. This is a user-facing feature that warrants at least a brief mention.

### 2.6 MINOR — `cli_fmt.py` Only Mentioned in File Tree

`cli_fmt.py` appears in the project structure as "ANSI terminal formatting helpers" but its API and capabilities are not described. This is acceptable for a utility module.

---

## 3. Clarity Assessment

### Strengths

- **Section structure is logical**: The progression from overview → components → schema → pipelines → deployment is natural and easy to follow.
- **Diagrams are effective**: The ASCII system overview, Intent flow diagram, and rule evaluation cycle are clear and accurate (for the `daemon.py` pipeline).
- **Tables are well-used**: Property tables, index tables, and config tables are scannable and correct.
- **Design principles are clearly stated**: The five principles in Section 1 provide a strong mental model.
- **Glossary is helpful**: Appendix B defines key terms concisely.

### Areas for Improvement

- **Section 5 mixes current and future**: The "Current Implementation" vs "Planned" split is confusing when the "planned" system is already implemented.
- **No versioning or change log**: For a living document, it would help to have a change history.
- **No cross-references between sections**: E.g., Section 4.2 (Intent Processing) could reference Section 10.1 (Security Layers) where capability permission checks are described.
- **Seed data description is vague**: Section 8.1 says "8 seeded capabilities" and "~30 SkillDomain nodes" without listing them. A reference to `seed.cypher` with a brief summary would be more useful.

---

## 4. Recommendations

### Priority 1 (Before Merge)

1. **Document or reconcile the dual Intent pipelines.** Either:
   - Add a section explaining that `intent_sdk.py`/`intent_daemon.py` is the MVP test pipeline and `sdk.py`/`daemon.py` is the production pipeline, or
   - Document the planned consolidation path.

2. **Update heartbeat system status** from "Planned" to "Implemented" with reference to `heartbeat_sdk.py` and `auth.py`.

3. **Add missing files to Section 12** (Project Structure): `auth.py`, `errors.py`, `heartbeat_sdk.py`, `intent_sdk.py`, `intent_daemon.py`, `capabilities/`.

4. **Acknowledge SDK direct writes** in Section 2.2 or the design principles. Explain that messaging and discussions bypass the Intent pipeline by design (if that's intentional).

### Priority 2 (Before Sprint 11)

5. Fix Python version requirement to match `pyproject.toml` (`>=3.12`).
6. Add `bcrypt` to both `pyproject.toml` dependencies and doc Section 9.1.
7. Complete the CLI commands table in Section 2.3.
8. Fix the approval workflow diagram (Section 4.3): `awaiting_approval → pending`, not `→ claimed`.
9. Document the `capabilities/` module pattern and `exec_ls.py` security controls.
10. Harmonize default Neo4j password across doc, SDK, and daemon.

### Priority 3 (Nice to Have)

11. Add a brief section on the report subsystem.
12. Document the custom exception hierarchy.
13. Add cross-references between related sections.
14. Add a document change log.

---

## 5. Summary Scorecard

| Dimension | Score | Notes |
|-----------|-------|-------|
| **Accuracy** | 6/10 | Core pipeline description is accurate, but several factual errors (approval flow, Python version, "planned" heartbeat) and the undocumented second pipeline significantly undermine trust |
| **Completeness** | 5/10 | ~40% of codebase by line count is undocumented; many CLI features missing |
| **Clarity** | 8/10 | Well-structured, good diagrams, readable prose |
| **Usefulness** | 7/10 | A developer reading only this doc would understand the core architecture but would be surprised by significant portions of the codebase |

**Overall:** The document is a strong foundation that needs a revision pass to catch up with the actual implementation state. The dual-pipeline situation is the most important issue to resolve.
