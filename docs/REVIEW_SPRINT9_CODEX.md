# Sprint 9 Code Review

Summary:
- CRITICAL: 2
- HIGH: 5
- MEDIUM: 3
- LOW: 1

## Findings

### [CRITICAL] Hardcoded Neo4j credentials are baked into runtime defaults
**File:** daemon.py, line(s) 1350-1352; sdk.py, line(s) 69-71
**Category:** Security
**Description:** Both the daemon entrypoint and the SDK fall back to a real-looking default password (`hassaleh-dev-2026`) when `NEO4J_PASSWORD` is unset. In practice this turns a missing-secret misconfiguration into an authenticated connection attempt with a predictable credential. It also makes accidental production reuse of the development password much more likely.
**Fix:** Remove the password default entirely. Require the credential to be supplied via environment/config, fail fast when it is missing, and document a local-development bootstrap path separately.

### [CRITICAL] Capability execution accepts unvalidated arguments and ships dangerous seed commands
**File:** daemon.py, line(s) 449-460; seed.cypher, line(s) 245-278
**Category:** Security
**Description:** `_execute_capability()` forwards agent-controlled `Intent.value` directly into the subprocess argument vector and never validates it against `Capability.invoke_params_schema`. That would already be risky for benign commands, but the seed data makes it critical: `security-audit` points at `/usr/bin/env`, which can execute arbitrary programs (`env bash -c ...`), and `file-read` points at `/usr/bin/cat`, which can read any path reachable by `hassaleh-fs`. The current permission model only gates capability identity, not argument safety.
**Fix:** Introduce per-capability server-side executors instead of generic command + free-form args. Parse and validate arguments against a real schema, reject unknown keys, and maintain an allowlist of safe operations per capability. Remove `/usr/bin/env` from the seed set.

### [HIGH] `submit_intent()` reports success even when nothing was created or linked
**File:** sdk.py, line(s) 218-265
**Category:** Correctness
**Description:** The write transaction never verifies that the submitting agent exists or that the requested capability/target node matched. If `MATCH (agent:Agent ...)` returns no rows, the transaction commits a no-op and the method still returns an `intent_id` that does not exist. If the capability/target match fails, the intent may exist without its required `:TARGETS` edge. That produces hard-to-debug phantom or dangling intents.
**Fix:** After each write query, consume the result and validate counters, or rewrite the transaction to `MATCH` everything up front and `RETURN` a status row. Raise `ValueError` when the agent, capability, or target is missing.

### [HIGH] Message reads can leak across contexts once an agent has a cursor
**File:** sdk.py, line(s) 602-646
**Category:** Correctness
**Description:** `read_messages(..., context_id=...)` first loads the agent-wide `LAST_READ` cursor, then follows `[:NEXT*1..]` from that cursor without constraining the traversal to the requested context. If the agent last read a different conversation, the next read can return the wrong context's messages. The fallback branch has the opposite problem: it matches `HEAD_OF` on any node with the same `id`, not a typed context label.
**Fix:** Store cursors per `(agent, context)` or add a `context_id` property to the cursor edge. In the read query, require `(:Message)-[:IN_CONTEXT_OF]->(:Task|:Discussion {id: $context_id})` on every returned message.

### [HIGH] `send_message()` can commit orphan `Message` nodes and uses unlabeled context matches
**File:** sdk.py, line(s) 530-583
**Category:** Correctness
**Description:** The transaction creates the `Message` node before validating the context. If `context_label` is invalid, the function silently returns from the branch and commits a message with no context linkage. If the label is valid but `context_id` does not exist, the message is still created and the later context match becomes a no-op. The follow-up `MATCH (ctx {id: $context_id})` queries are also unlabeled, which is both slower and unsafe when IDs are not globally unique across labels.
**Fix:** Match and validate the context first, then create the message only after the full context chain is known. Use explicit labels for all context lookups and fail the transaction if the target context is missing.

### [HIGH] SDK query timeouts do not cover result streaming
**File:** sdk.py, line(s) 166-176
**Category:** Correctness
**Description:** `asyncio.wait_for()` wraps only `session.run()`. Neo4j can still spend arbitrarily long streaming records during the subsequent `async for record in result`, so the advertised timeout is not a real wall-clock limit. A large or poorly planned read can therefore block well past the configured deadline.
**Fix:** Wrap the entire query lifecycle in a timeout, or better, set server-side transaction timeouts through Neo4j session/query configuration and combine that with a client-side deadline around result consumption.

### [HIGH] Rule property application is non-atomic and vulnerable to lost updates
**File:** daemon.py, line(s) 1133-1219
**Category:** Correctness
**Description:** Rule evaluation fetches current property values, resolves conflicts in memory, then writes each resolved property in a separate autocommit statement. Any concurrent writer between the read phase and the final `SET` can be overwritten, and a mid-loop failure leaves only a subset of the resolved changes applied. This is a correctness problem, not just a performance concern.
**Fix:** Apply the full resolved set in a single write transaction. Re-read current values inside that transaction or switch to compare-and-set semantics so external changes cannot be silently lost.

### [MEDIUM] CLI initialization splits Cypher files on semicolons instead of parsing statements safely
**File:** cli.py, line(s) 344-346
**Category:** Correctness
**Description:** `cmd_init()` loads each `.cypher` file and splits on raw `;`. That breaks valid Cypher whenever semicolons appear inside string literals, embedded rule text, or future procedure calls. It also makes comments and multiline statements fragile.
**Fix:** Use Neo4j's script runner semantics if available, or implement a minimal lexer that understands comments and quoted strings before splitting statements.

### [MEDIUM] The GSL-Ops grammar is narrower than the runtime and rejects common string forms
**File:** gsl_ops.lark, line(s) 111, 205; runtime.py, line(s) 243-278; compiler.py, line(s) 233-250
**Category:** Correctness
**Description:** The runtime accepts fractional shorthand durations (`1.5h`), but the grammar only allows integer `DURATION_LITERAL`s (`/\d+[smhd]/`). Separately, `STRING` does not permit escaped quotes, which means rule authors cannot express messages like `He said \"stop\"`. The compiler also emits string literals directly into generated Python instead of using `repr()`, so extending the grammar later would be unsafe unless codegen changes with it.
**Fix:** Broaden the grammar to match runtime behavior, add escaped-string support, and change code generation for user-provided strings to use `repr()` instead of manual quote concatenation.

### [MEDIUM] Schema compatibility is checked only cosmetically
**File:** daemon.py, line(s) 1332-1342
**Category:** Production Readiness
**Description:** `_check_schema_version()` reads `compatible_daemon_versions` but never enforces it. The daemon will continue starting even when the graph is on an incompatible schema, which is exactly when startup should stop.
**Fix:** Compare the running daemon/compiler version against the compatibility list and raise a startup error on mismatch. Expose the active version in logs and health output.

### [LOW] OpenClaw client methods assume context-manager usage and fail with opaque `NoneType` errors
**File:** openclaw.py, line(s) 62-68, 189-199
**Category:** Quality
**Description:** `health()` and `_tool_call()` dereference `self._session` without checking whether `__aenter__()` has been called. If the class is used incorrectly, callers get an unhelpful attribute error instead of a clear lifecycle exception.
**Fix:** Add a private `_require_session()` helper that raises `RuntimeError("OpenClawBridge not started")` before any request is attempted.

## Positive Findings

- `RuleContext.match()` uses `execute_read()` and keeps rule-side graph access read-only at the driver level, which is the right default boundary for compiled rules.
- The daemon validates dynamic property names with `isidentifier()` before interpolating them into Cypher, which is a reasonable defense-in-depth measure for the current grammar.
- Domain scoping is consistently modeled in both the daemon and SDK, and the `domain_matches_any()` helper keeps the matching semantics simple and reusable.
- Health checks are bound to `127.0.0.1`, which avoids exposing operational state on a public interface by default.
- Most operator-facing Cypher in the CLI is parameterized instead of string-built, reducing avoidable injection risk in day-to-day commands.
