# IL-07 implementation — validate graph-sourced capability execution fields

Date: 2026-04-19
Branch: `trunk`
Repo: `~/projects/hassaleh`
Severity: **MEDIUM**
Spec: `docs/security-audit-intent-lifecycle.md` § IL-07

## Problem

`src/hassaleh/daemon.py::_execute_capability()` reads `invoke_command` and
`exec_as_user` from Neo4j and uses them to construct the runtime command:

```python
cmd = ["sudo", "-n", "-u", exec_user, command] + args
```

Even though this is a list-form exec (so shell metacharacters are not evaluated
by a shell), it still crosses a trust boundary: the graph is not the source of
truth for which binary and which OS user a capability may run as. Any actor who
can write `Capability.invoke_command` or `Capability.exec_as_user` could try to
redirect execution to a different program or a more privileged account.

IL-07 closes that by requiring every graph-sourced `(capability_id,
invoke_command, exec_as_user)` tuple to match a strict, server-side allowlist.

## Approach

The daemon now treats Neo4j capability metadata as **untrusted input** and
compares it against a local immutable allowlist before constructing the `sudo`
command.

### Server-side source of truth

`src/hassaleh/daemon.py` defines:

```python
CAPABILITY_ALLOWLIST: dict[str, tuple[str, str]]
```

Each entry maps:

```text
capability_id -> (allowed_invoke_command, allowed_exec_as_user)
```

Examples:
- `exec-ls -> ("/usr/bin/ls", "hassaleh-fs")`
- `graph-query-inspector -> ("hassaleh.graph_query_inspector", "hassaleh-daemon")`

A graph entry is accepted only if:
1. `capability_id` exists in `CAPABILITY_ALLOWLIST`, and
2. the graph values for `invoke_command` and `exec_as_user` exactly match the
   allowlisted pair.

### Sanitized failure mode

Allowlist failures use the same sanitized correlation-ID pattern introduced in
IL-04. The agent-visible error is always:

```text
Parameter validation failed [cid: <32-hex>]
```

The daemon log retains the full detail (capability id, graph value, allowlist
value, intent id) under the same `cid`.

This keeps trust-boundary failures indistinguishable from other parameter
validation failures to the agent while preserving server-side debuggability.

## Files touched

| File | Change |
|------|--------|
| `src/hassaleh/daemon.py` | Removed the duplicate private allowlist and made `_execute_capability()` validate against the documented `CAPABILITY_ALLOWLIST` constant. On mismatch, it logs full detail with a correlation ID and fails the intent with the sanitized IL-04 message pattern. |
| `tests/test_il07_trust_boundary.py` | New adversarial regression tests for graph-injected `invoke_command`, graph-injected `exec_as_user`, and the allowlisted happy path. |
| `docs/IL-07-implementation.md` | This implementation note. |

## Security reasoning

### Why list-form exec is not enough

List-form subprocess invocation prevents shell expansion, but it does **not**
solve the trust problem. If Neo4j says:

- `invoke_command = "/usr/bin/env"` instead of `"/usr/bin/ls"`, or
- `exec_as_user = "root"` instead of `"hassaleh-fs"`,

then the daemon would still obediently run the wrong binary or user unless it
checks the graph data against a local policy.

### Why exact-match allowlisting

Exact-match validation is intentionally strict:
- shell-metacharacter payloads in `invoke_command` fail because the string does
  not equal the allowlisted binary path;
- unapproved OS users fail because the string does not equal the allowlisted
  service account;
- newly added capabilities require a code change to expand the allowlist.

This turns the graph from an execution authority into a request for execution
that must pass a local policy gate.

## Cypher changes

None. IL-07 is enforced entirely in the daemon trust boundary after graph read
and before subprocess construction.

## Tests

`tests/test_il07_trust_boundary.py` covers:

1. **Graph-injected shell metacharacter in `invoke_command` → rejected**
   - Example payload: `"/usr/bin/ls; touch /tmp/pwned"`
   - Asserts the intent is failed with sanitized `Parameter validation failed`
     and the raw payload is not exposed to the agent.

2. **Graph-injected non-allowlisted uid in `exec_as_user` → rejected**
   - Example payload: `"root"`
   - Asserts the intent is failed with sanitized `Parameter validation failed`
     and the raw uid is not exposed to the agent.

3. **Happy path → accepted**
   - An allowlisted `(command, user)` pair reaches the subprocess path.
   - Asserts the daemon constructs:
     `sudo -n -u <allowlisted-user> <allowlisted-command> ...`

## Result

IL-07 now enforces a clear server-side trust boundary:
Neo4j may describe a capability, but only the daemon’s local allowlist is
trusted to authorize which binary and OS user may actually run.
