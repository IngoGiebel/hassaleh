# Sprint 12 Track A — Logging implementation

Date: 2026-04-20
Branch: `trunk`
Plan reference: `docs/sprint-12-plan.md` §2.4, §3.1, §3.1.1, §3.1.2, §5
Status: implemented and committed candidate validated with targeted pytest

## Scope

This note covers the Track A logging deliverables only.
It does not include Track B metrics, Track C tracing behavior changes, or any
market-pipeline work.

## Files changed

### 1) `src/hassaleh/obs/logging.py`
- File status: new
- File length: 239 lines
- Cached diff contribution: new file
- Purpose: central structured logging implementation for Observability v1

Key contents:
- `ObsLoggingConfig` dataclass for the setup return value
- `setup(service_name, env)` / `configure_logging(...)`
- stdlib + structlog integration
- JSON renderer for production mode
- text renderer fallback for dev / obs-off mode
- PII redaction helpers and processor chain
- service-wide binding (`service`, `hostname`, `version`, `env`, `obs_mode`)
- `bind_logger(...)`, `get_logger(...)`, `rebind_daemon_logger()`

### 2) `src/hassaleh/obs/__init__.py`
- File status: modified
- File length: 67 lines
- Cached diff stat: 93-line diff in staged state
- Purpose: package entrypoint and ownership surface for `obs.setup()`

Track A makes `obs.setup()` the root-handler owner and exports the logging
surface first. Tracing exports remain optional behind a guarded import so
Track A can merge first without blocking on Track C.

### 3) `src/hassaleh/daemon.py`
- File status: modified
- File length: 1403 lines
- Cached diff stat: ~15-line staged diff
- Purpose: remove daemon-side root logging ownership and wire boot-time setup

Track A wiring now:
- imports `hassaleh.obs` and `hassaleh.obs.logging as obs_logging`
- removes direct `logging.basicConfig(...)` ownership
- calls `obs.setup("hassaleh-daemon", obs_env)` in `main()`
- rebinds the module logger via `obs_logging.rebind_daemon_logger()`

### 4) `tests/test_obs_logging.py`
- File status: new
- File length: 92 lines
- Purpose: focused unit coverage for Track A requirements

Covered cases:
- JSON output schema
- representative PII redaction
- `HASSALEH_OBS=off` text fallback regression mode

### 5) `pyproject.toml`
- File status: modified
- File length: 50 lines
- Purpose: add `structlog` runtime dependency

## Pytest result

Command run:

```bash
source .venv/bin/activate && pytest tests/test_obs_logging.py -v --no-header -q
```

Observed result:

```text
============================= test session starts ==============================
collected 3 items

tests/test_obs_logging.py ...                                            [100%]

============================== 3 passed in 0.82s ===============================
```

Summary line:
- `3 passed in 0.82s`

## `obs.setup()` contract

Track A establishes `obs.setup(service_name, env)` as the single owner of root
logging configuration, per plan §2.4.

### Signature
- `service_name: str`
- `env: str | None`

### Accepted `service_name` values
From `SERVICE_ENUM` in `src/hassaleh/obs/logging.py`:
- `hassaleh-daemon`
- `hassaleh-sdk`
- `hassaleh-intent-sdk`
- `hassaleh-heartbeat-sdk`

Unknown service names raise `ValueError`, which keeps the service field aligned
with the closed enum required by plan §3.1.

### Env behavior
`env` resolves as:
1. explicit function argument
2. `HASSALEH_ENV`
3. fallback `dev`

### `HASSALEH_OBS` behavior
`HASSALEH_OBS` resolves as:
- `off` (default) → observability-disabled mode
- any non-`off` value → observability-enabled mode

Current Track A behavior:
- `HASSALEH_OBS=off`
  - root handler is still installed by `obs.setup()`
  - output uses the text renderer fallback
  - stderr logging remains available
- `HASSALEH_OBS=on` with `env=prod`
  - output uses JSON rendering
- `HASSALEH_OBS=on` with `env=dev`
  - output currently uses text fallback

That matches the requested Track A split of JSON renderer for production and
text fallback for development / off mode.

## Formatter and processor behavior

### JSON formatter
In `src/hassaleh/obs/logging.py`, `configure_logging(...)` selects:
- `structlog.processors.JSONRenderer(sort_keys=True)` when obs is enabled and
  `env == "prod"`
- `TextRenderer()` otherwise

The JSON test verifies the emitted schema includes the required top-level
fields from plan §3.1 that this Track A implementation covers:
- `ts`
- `level`
- `logger`
- `msg`
- `service`
- `env`
- `version`
- `hostname`

It also verifies representative contextual fields:
- `agent_id`
- `intent_id`
- `result`
- `extra`

### Text fallback
`TextRenderer` renders:
- ISO timestamp
- logger name
- log level
- message
- JSON-encoded extras appended to the line

This is the minimal stderr-preserving fallback described in plan §2.4 for
non-JSON mode.

## PII redaction coverage

Track A implements a redaction processor (`pii_redaction_processor`) applied in
both JSON and text modes.

### Direct string redaction
Patterns currently covered:
- email addresses → `[REDACTED_EMAIL]`
- bearer tokens → `Bearer [REDACTED_TOKEN]`
- phone numbers → `[REDACTED_PHONE]`

Regexes:
- `_EMAIL_RE`
- `_BEARER_RE`
- `_PHONE_RE`

### Secret-key / lookup redaction
Field-name-based redaction is implemented with `_SECRET_KEY_RE`.
This covers keys matching the plan’s sensitive categories, including:
- `api_key`
- `api_key_hash`
- `api_key_lookup`
- `lookup`
- related underscore-delimited variants

These are replaced with:
- `[REDACTED_SECRET]`

This is aligned with plan §3.1’s requirement that `api_key`,
`api_key_hash`, `api_key_lookup`, and lookup-like Cypher parameters never be
logged as values.

### `cypher_params`
For `cypher_params`, Track A now applies a two-step rule:
- keys matching `_SECRET_KEY_RE` (for example `lookup`, `api_key`, `api_key_hash`, `api_key_lookup`) are forced to `[REDACTED_SECRET]`
- all other values are reduced to type/shape only via `_shape_only(...)`

Examples from the test:
- `lookup` → `[REDACTED_SECRET]`
- `api_key_lookup` → `[REDACTED_SECRET]`
- ordinary string → `str(N)`
- int → `int`

This aligns with the plan’s requirement that lookup-like and `api_key*` Cypher
parameters must be redacted regardless of how they were computed.

### `message_content`
`message_content` is truncated to:
- first 40 characters plus `…[N chars]`

This follows the allowed plan behavior of truncation for message-like payloads.

### `intent_payload.content`
`intent_payload.content` is transformed to hash-only form using SHA-256 prefix
material:
- `sha256:<12-hex-prefix>`

This follows the plan’s requirement that content payloads be hash-only or
safely truncated rather than logged verbatim.

### `source_ip`
The current implementation preserves full `source_ip` in non-debug cases and
redacts to `/24` only in debug mode.
That is consistent with the plan language:
- INFO → full IP allowed
- DEBUG → redact to `/24`

## Daemon integration call-sites

Track A changes daemon boot ownership exactly at the entrypoint.

### Removed ownership
The previous inline `logging.basicConfig(...)` at the top of `daemon.py` is no
longer the configuration owner.

### New ownership flow
At `main()`:
1. read `HASSALEH_ENV` into `obs_env`
2. call `obs.setup("hassaleh-daemon", obs_env)`
3. rebind module logger via `obs_logging.rebind_daemon_logger()`
4. continue daemon boot normally

This satisfies plan §2.4’s CR-3 resolution that `obs.setup()` becomes the
single root-handler owner before other tracks depend on structured output.

## §3.1.1 slow-query-log hook status

Status: **deferred to Track B coordination**

Track A delivers the logging substrate needed for slow-query logs:
- JSON/text formatting
- context binding
- redaction
- stable root-handler ownership

However, the actual slow-query call-site hook and query-duration
classification/pattern emission are not wired in this staged Track A diff.
That part depends on graph instrumentation and belongs to the Track A + Track B
integration point described in the plan.

So for §3.1.1:
- logging substrate: implemented
- daemon/graph slow-query emission hook: deferred

## §3.1.2 audit-log hook status

Status: **deferred to follow-on integration**

Track A provides the logger surface and redaction policy needed for the tool
invocation audit log, but the explicit `hassaleh.daemon.audit.tool` call-sites
are not added in this staged Track A set.

So for §3.1.2:
- audit-log-ready logging substrate: implemented
- explicit tool-invocation audit event emission: deferred

## Deviations / notes versus plan §2.4 and §3.1

### Implemented as planned
- `obs.setup(service_name, env)` is now the root-handler owner
- daemon no longer owns `basicConfig(...)`
- JSON renderer exists for production mode
- text fallback exists for dev/off mode
- service/version/env/hostname bindings are applied globally
- PII redaction exists for the core sensitive categories required by Track A

### Intentional staging compromise
Track A keeps tracing imports optional in `obs/__init__.py` so the package stays
importable even if Track C is incomplete or still evolving. This is consistent
with the merge-order constraint that Track A must land first.

### Deferred pieces
The following plan items are not fully wired by this Track A commit and are
explicitly deferred rather than silently claimed:
- slow-query log emission hook (§3.1.1 call-site)
- tool-invocation audit-log emission hook (§3.1.2 call-site)
- broader observability stack work outside logging

## Final assessment

Track A is in a mergeable state for its core deliverables:
- central `obs.setup()` ownership
- structlog-backed formatting
- JSON production renderer
- text fallback for off/dev
- PII redaction middleware
- daemon entrypoint integration
- unit coverage for schema/redaction/off mode

The remaining §3.1.1 and §3.1.2 call-site hooks should be treated as follow-on
integration work with Track B / later observability wiring, not as blockers for
landing the logging foundation.

## Follow-up for Inanna Round-1 review

A follow-up change addressed the two remaining blocking findings from
`docs/sprint-12-track-a-review.md`.

### 1) `cypher_params` secret redaction bypass fixed
Previously, `cypher_params` values were always reduced through `_shape_only()`,
which meant secret-like keys such as `lookup` could leak shape information
instead of being hard-redacted.

The fix now checks each `cypher_params` key against `_SECRET_KEY_RE` first:
- secret-like keys → `[REDACTED_SECRET]`
- non-secret keys → `_shape_only(...)`

Test updates:
- `tests/test_obs_logging.py` now expects `cypher_params["lookup"] == "[REDACTED_SECRET]"`
- added `api_key_lookup` coverage inside `cypher_params`

### 2) `HASSALEH_OBS=off` is now a true zero-cost no-op for processors
Previously, obs-off mode still installed the structlog pipeline and paid the
cost of the shared processor chain, including `pii_redaction_processor`.

The fix now bypasses structlog entirely when `HASSALEH_OBS=off`:
- plain stdlib `logging.Formatter` is installed directly on the root handler
- no shared structlog processor chain is constructed or executed
- `get_logger()` / `bind_logger()` return a lightweight plain logger wrapper in
  off mode so existing call sites still work without structlog processing

Test updates:
- added an assertion that `pii_redaction_processor` is **not invoked** when
  obs is off

### Resulting test delta
Required follow-up test command:

```bash
.venv/bin/pytest tests/test_obs_logging.py tests/test_obs_metrics.py tests/test_obs_tracing.py -q
```

This follow-up is intended to leave Track A compliant with both the plan and
Inanna’s review without changing Track C-owned files.
