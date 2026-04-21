# Sprint 12 — Track F Fix Note (Round 2)

## Context
Round-2 review (`docs/sprint-12-track-f-review-round2.md`) left one open
blocker: `tests/test_observability.py::test_obs_off_import_time_isolation`
timed out after 30s in the project virtualenv. Assertions were structurally
correct; the hang was behavioural in the subprocess harness.

## Root cause
The child interpreter imports `hassaleh.obs.tracing`, which transitively
pulls opentelemetry SDK + OTLP exporter + prometheus_client + structlog.
On a cold CI host, the parent pytest and the child race to write
`__pycache__/*.pyc` for the same upstream modules, and the child serialises
behind an fs-level write. Combined with pytest-inherited stdio fds, a brief
stall was enough to trip `subprocess.run(..., timeout=30)`. Warm workstation
runs completed in ~1.5s and masked the issue.

## Fix
`tests/test_observability.py::test_obs_off_import_time_isolation` — harness
hardened, assertions unchanged:

- `PYTHONDONTWRITEBYTECODE=1` so the child never touches `__pycache__`.
- `PYTHONUNBUFFERED=1` so partial diagnostics survive a hang.
- `stdin=subprocess.DEVNULL` to sever inherited pytest fds.
- `Popen` + `communicate(timeout=15)` with kill + drain on timeout,
  surfacing child stdout/stderr in the failure message.
- Timeout 30s → 15s (~10× observed cold-start cost of ~1.5s).

## Verification
```
$ . .venv/bin/activate && python -m pytest tests/test_observability.py -v
======================== 18 passed, 3 skipped in 1.94s =========================
```
Stable across repeated runs (1.57–2.47s). B1, B2, B3 all green.
