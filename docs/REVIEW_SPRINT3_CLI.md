# Sprint 3 CLI Review

No direct Cypher injection issue found in the current CLI. User-supplied values are passed as query parameters, and the only dynamic query assembly in `cmd_intent_list()` inserts fixed clause fragments rather than raw user text (`src/hassaleh/cli.py:395-416`).

## Findings

### High: Most subcommands can crash with raw exceptions instead of returning CLI errors
`main()` only wraps `status`, `init`, and `approve` in `try/except`; `agent`, `rule`, and `intent` dispatch paths call their handlers directly. A Neo4j connection failure or query error in `cmd_agent_list()`, `cmd_agent_info()`, `cmd_rule_list()`, `cmd_rule_compile()`, or `cmd_intent_list()` will therefore surface as an unhandled traceback instead of consistent CLI output (`src/hassaleh/cli.py:214-242`, `src/hassaleh/cli.py:246-432`, `src/hassaleh/cli.py:540-569`).

### Medium: Nested subcommands are not required by argparse
`hassaleh agent`, `hassaleh rule`, and `hassaleh intent` all parse successfully, then fail later with handwritten usage strings. This is weaker UX than letting argparse reject the command and print structured help. `add_subparsers(..., required=True)` should be used for the nested parsers (`src/hassaleh/cli.py:493-513`, `src/hassaleh/cli.py:547-569`).

### Medium: `init` can report success after partial failure
`cmd_init()` warns on per-statement errors but still prints `applied (...)` for the file and ends with `Init complete`, then returns `0`. That can hide a broken or partially applied schema/seed state from operators (`src/hassaleh/cli.py:188-210`).

### Low: `intent list` accepts invalid filter values silently
`--source` is documented as `agent/rule` but has no `choices`; `--lifecycle` is also unconstrained; `--limit` accepts any integer. Invalid values degrade to empty output or backend errors instead of immediate CLI validation (`src/hassaleh/cli.py:510-513`).

## Test Coverage Gaps

- `tests/test_cli.py` only covers formatter helpers and `build_parser().parse_args(...)`; it does not exercise `main()` dispatch, exit codes, or printed help/usage (`tests/test_cli.py:1-136`).
- No tests cover connection failures, query failures, missing graph data, or daemon health endpoint failures.
- No tests cover the bad-input paths above: missing nested subcommands, invalid `intent list` filters, or partial-failure behavior in `init`.
