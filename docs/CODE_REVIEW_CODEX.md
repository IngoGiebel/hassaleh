# Code Review

## 1. Fix Verification

- **C1: Atomic Intent claiming**: Mostly fixed. [`src/hassaleh/daemon.py:208`](./../src/hassaleh/daemon.py#L208) now claims `pending` intents by setting `lifecycle = 'claimed'` in the same Cypher statement that selects them, which closes the earlier read-then-write race. The follow-on transitions to `rejected`, `awaiting_approval`, or `running` are also consistent with the new intermediate state.
- **C2: Cypher injection prevention via allowlist**: Fixed for `target_label`. [`src/hassaleh/sdk.py:244`](./../src/hassaleh/sdk.py#L244) validates `target_label` against `ALLOWED_TARGET_LABELS` before interpolating it into Cypher, which removes the direct label-injection path.
- **C3: Double shutdown prevention**: Fixed. [`src/hassaleh/daemon.py:594`](./../src/hassaleh/daemon.py#L594) registers signal handlers that only set `_shutdown_event`, and the actual shutdown is centralized in the main loop / `finally` path. Because [`shutdown()`](./../src/hassaleh/daemon.py#L112) sets `self.running = False`, the `finally` guard prevents a second shutdown call.
- **I2: `BLOCKED_KEYWORDS` word-boundary regex**: Fixed for the original false-positive class. [`src/hassaleh/sdk.py:145`](./../src/hassaleh/sdk.py#L145) uses `\b...\b`, so substrings like `OFFSET` no longer trip `SET`.

## 2. Remaining Issues

- **High: `submit_intent()` is still non-transactional and can leave orphaned `Intent` nodes.** [`src/hassaleh/sdk.py:209`](./../src/hassaleh/sdk.py#L209) creates the `Intent` in one auto-commit query, then creates the `TARGETS` edge in a later query. If validation or the second query fails, the graph is left with a `pending` intent that has no valid target. This is especially visible for invalid `target_label`, where the method raises after the `Intent` is already created. These writes should be wrapped in a single write transaction, with validation before the first write.
- **High: `update_property` still permits arbitrary property mutation on privileged labels.** [`src/hassaleh/daemon.py:351`](./../src/hassaleh/daemon.py#L351) writes `SET target[$prop] = $value` with no property allowlist. Combined with the broad target-label allowlist in [`src/hassaleh/sdk.py:45`](./../src/hassaleh/sdk.py#L45), an agent that can submit `update_property` intents can mutate sensitive fields such as `lifecycle`, config values, or system metadata on `Agent`, `Project`, `DaemonConfig`, `QueryConfig`, etc.
- **Medium: subprocess cancellation/timeout is incomplete.** [`src/hassaleh/daemon.py:307`](./../src/hassaleh/daemon.py#L307) uses `wait_for(proc.communicate())`, but on timeout it only marks the intent failed; it does not terminate or reap the child process. The same applies when the worker task is cancelled during shutdown. This can leave OS processes running after the intent is marked failed or after daemon shutdown.
- **Low: the read-only Cypher guard is still lexical, not syntactic.** [`src/hassaleh/sdk.py:145`](./../src/hassaleh/sdk.py#L145) is better than the old substring check, but it can still misclassify keywords inside string literals/comments and is not a full parser-level read-only guarantee.

## 3. Positive Observations

- The new `claimed` lifecycle in [`src/hassaleh/daemon.py:208`](./../src/hassaleh/daemon.py#L208) and zombie recovery for both `running` and `claimed` in [`src/hassaleh/daemon.py:420`](./../src/hassaleh/daemon.py#L420) fit together well.
- Shutdown control flow is cleaner now: signal handlers are lightweight, shutdown is centralized, and active worker cancellation is explicit.
- The SDK’s `target_label` allowlist is a pragmatic fix for the immediate Cypher injection risk and is easy to audit.
