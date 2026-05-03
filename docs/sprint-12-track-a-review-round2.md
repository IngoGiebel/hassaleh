# Sprint 12 Track A (Logging) — Round-2 Security & API Review

**Reviewer:** Inanna
**Round:** 2 (re-verification only)
**Commit Reviewed:** 63d4c47 (on top of e6fd6b6 for Blocker 1)
**Scope:** Re-verify resolution of the three Round-1 blockers from
`docs/sprint-12-track-a-review.md`. No new lines of inquiry.

## 1. Verdict

**CLEAN**

All three Round-1 blockers are resolved. Track A is unblocked for merge from a
security/API-review standpoint.

## 2. Blocker 1 — Missing `src/hassaleh/obs/__init__.py`

**Status:** RESOLVED (via Track B commit e6fd6b6).

Evidence:
- `src/hassaleh/obs/__init__.py` is present (189 lines, 5360 bytes).
- It re-exports `setup`, `bind_logger`, `get_logger`, `rebind_daemon_logger`,
  and `ObsLoggingConfig` from `hassaleh.obs.logging`, and defines a top-level
  `setup()` that composes logging + metrics setup.
- The daemon can now resolve `hassaleh.obs.setup` without `ImportError` /
  `AttributeError`, which was the exact failure mode flagged in Round-1.

## 3. Blocker 2 — `cypher_params` PII bypass

**Status:** RESOLVED.

Evidence (`src/hassaleh/obs/logging.py`):
- Lines 148–156: when `key == "cypher_params"`, `_redact_mapping` now iterates
  each nested `param` and checks `_SECRET_KEY_RE.search(param.lower())` first.
  Secret-like keys (`lookup`, `api_key`, `api_key_hash`, `api_key_lookup`, etc.)
  are forced to `"[REDACTED_SECRET]"`; only non-secret params fall through to
  `_shape_only(...)`.
- This closes the Round-1 path where `_shape_only` was applied uniformly and
  leaked length information for `lookup`-like keys.

Test evidence (`tests/test_obs_logging.py` lines 57, 70–75):
- Input now includes `cypher_params={"lookup": ..., "api_key_lookup": ...,
  "agent_id": ..., "count": 3}`.
- Assertion: `cypher_params == {"lookup": "[REDACTED_SECRET]",
  "api_key_lookup": "[REDACTED_SECRET]", "agent_id": "str(9)", "count": "int"}`.

## 4. Blocker 3 — `HASSALEH_OBS=off` not a zero-cost no-op

**Status:** RESOLVED.

Evidence (`src/hassaleh/obs/logging.py`):
- Lines 213–231: when `enabled` is false, `configure_logging` installs a plain
  `logging.Formatter` directly on a `StreamHandler`, calls
  `structlog.reset_defaults()`, and returns early. The `shared_processors`
  list — including `pii_redaction_processor` — is never constructed, and
  `structlog.configure(...)` is never invoked.
- Lines 55–88: new `PlainBoundLogger` is a stdlib-only wrapper providing
  `bind` / `info` / `warning` / `error` / `critical` without touching structlog.
- Lines 291–295: `bind_logger` returns a `PlainBoundLogger` when
  `HASSALEH_OBS=off`, so call sites get a zero-structlog fast path end-to-end.

Test evidence (`tests/test_obs_logging.py` lines 85–100):
- `test_obs_off_uses_text_fallback` monkeypatches
  `obs_logging.pii_redaction_processor` with a counting wrapper and asserts
  `calls["count"] == 0` after a full `setup` + `get_logger().info(...)` cycle.
- Output is also verified to be non-JSON and to carry the literal
  `source_ip` value, confirming the stdlib path runs and nothing is silently
  swallowed.

## 5. Non-blocking observations

- `get_logger()` / `bind_logger()` now return `Any` instead of
  `structlog.stdlib.BoundLogger`. That is consistent with the dual-mode
  behavior but slightly weakens type signals for callers — worth tightening to
  a shared `Protocol` in a later pass, not a blocker.
- `PlainBoundLogger._log` serializes merged bindings via `json.dumps`. If
  any future caller passes non-JSON-serializable bindings, the `default=str`
  fallback will stringify them silently; acceptable for off-mode.
