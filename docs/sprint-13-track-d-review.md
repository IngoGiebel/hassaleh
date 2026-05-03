# Sprint 13 Track D (Observability wiring: spans, metrics, logs) — Security & API Review

**Reviewer:** Inanna
**Commit Reviewed:** 05fd8d8
**Scope:** `src/hassaleh/runtime/observability.py` (175 lines), `tests/test_runtime_observability.py` (375 lines).
**Plan reference:** docs/sprint-13-plan.md §2.6 (authoritative).
**Review type:** Interface / contract / security. No code was modified.

---

## 1. Scope

Track D delivers the eight-symbol surface that Track A's `HassalehRuntime.execute` will call around each Intent dispatch:

| Symbol | Purpose | §2.6 anchor |
|---|---|---|
| `start_root_span(intent, ctx)` | Root span `hassaleh.intent.execute` with `intent.type`, `principal.id` attributes | Tracing |
| `validate_span` / `capability_check_span` / `precondition_span` / `mutate_span` | Four child spans in canonical order | Tracing |
| `record_metric(intent, result, duration_ms)` | Counter + histogram tick | Metrics |
| `emit_log(intent, ctx, result, duration_ms)` | One structlog `"intent executed"` line | Logs |
| `get_runtime_registry()` | Hands Track E the dedicated `CollectorRegistry` | Implementation choice |

Sprint-12's OTel (`hassaleh.obs.tracing`) and structlog (`hassaleh.obs.logging`) surfaces are reused directly; Track D is a thin §2.6-shaped facade over them plus a private metrics registry.

## 2. Security findings (Blocking)

**None.** No PII, no secrets, no command/log/span-attribute injection vectors, no unbounded label cardinality.

The full security walk-through is in §4 (PII) and §6 (defense-in-depth advisories).

## 3. API / interface compliance with §2.6

### 3.1 Spans (verbatim from §2.6)

| Required | Present | Match |
|---|---|---|
| Root span name `hassaleh.intent.execute` | `_ROOT_SPAN_NAME = "hassaleh.intent.execute"` | ✓ |
| Root attribute `intent.type` | `start_root_span` passes `**{"intent.type": intent.type}` | ✓ |
| Root attribute `principal.id` | `start_root_span` passes `**{"principal.id": ctx.principal.id}` | ✓ |
| Child `intent.validate` (step 1) | `_CHILD_SPAN_NAMES[0]` | ✓ |
| Child `intent.capability_check` (step 2) | `_CHILD_SPAN_NAMES[1]` | ✓ |
| Child `intent.precondition` (step 3) | `_CHILD_SPAN_NAMES[2]` | ✓ |
| Child `intent.mutate` (step 4) | `_CHILD_SPAN_NAMES[3]` | ✓ |
| **No** `intent.result` child (G-CR-2) | Not exposed; not constructable through public surface | ✓ |

Canonical execution order (§2.5: `validate → capability_check → precondition → mutate`) is enforced by the test `test_four_child_spans_under_root_in_canonical_order`, which asserts both child names AND parent-id linkage to the root span. Strong contract test.

### 3.2 Metrics

| Required | Present | Match |
|---|---|---|
| Counter `hassaleh_intent_total{intent_type, result}` | `Counter("hassaleh_intent_total", labelnames=("intent_type", "result"), …)` | ✓ |
| **Two labels only** (G-CR-3, no `capability_granted`) | Verified by `test_counter_has_only_intent_type_and_result_labels` | ✓ |
| Histogram `hassaleh_intent_duration_seconds{intent_type}` | `Histogram(…, labelnames=("intent_type",), buckets=_DURATION_BUCKETS, …)` | ✓ |
| Result label values kebab-case verbatim (G-CR-4) | `counter.labels(result=result.kind).inc()` — no translation | ✓ |
| All five `ResultKind` values pass through unchanged | Parametrized in `test_record_metric_per_label_tick_for_every_result_kind` | ✓ |
| Duration emitted in **seconds**, accepted in **ms** | `histogram.labels(...).observe(duration_ms / 1000.0)` — verified by `test_record_metric_ticks_histogram_with_intent_type_label_only` (100ms in → 0.100s out) | ✓ |

### 3.3 Logs

| Required (§2.6) | Present | Match |
|---|---|---|
| Event `"intent executed"` | `logger.info("intent executed", **fields)` | ✓ |
| `intent_type` | always | ✓ |
| `trace_id` (when span recording) | added when `_current_trace_id()` returns a 32-hex id | ✓ |
| `duration_ms` | always | ✓ |
| `result` | always | ✓ |
| `error_code` (only when `result != "ok"`) | gated `if result.kind != "ok" and result.error_code is not None` | ✓ |
| `principal_id` | always | ✓ |
| Field is `error_code`, not `error_type` (G-CR-7) | grep `error_type`: 0 hits | ✓ |
| `trace_id` absent when no recording span | `_current_trace_id()` returns `None` on `not ctx.is_valid` — verified by `test_emit_log_omits_trace_id_when_no_span_active` | ✓ |
| `error_code` absent on success | verified by `test_emit_log_has_required_fields_on_success` (`assert "error_code" not in payload`) | ✓ |

Trace-id format `f"{ctx.trace_id:032x}"` is W3C-compliant lowercase 32-hex; consistent with `obs_tracing.get_current_traceparent`.

## 4. Observability correctness

### 4.1 Dedicated `CollectorRegistry` (special check #1)

The module allocates its own `CollectorRegistry(auto_describe=True)` and never touches `prometheus_client.REGISTRY`. Verified by inspection: every `Counter` / `Histogram` constructor is passed `registry=_registry`. This is the **correct** response to the same-name conflict with Sprint-12's `hassaleh_intent_duration_seconds{stage}` — `prometheus_client` raises on duplicate metric names with differing label sets in one registry.

Lesson from Sprint-12 Track D (registry collision in dashboard rules): same family, different label set, single registry = scrape failure. Track D avoids this. Hand-off cost is on Track E, which must either scrape both registries or merge.

### 4.2 `HASSALEH_OBS=off` no-op semantics (special check #2) — **mixed**

| Helper | `HASSALEH_OBS=off` behavior | True zero-cost? |
|---|---|---|
| `start_root_span` | `obs_tracing.span_context` checks `is_obs_enabled()` and yields `None`; no provider, no exporter, no processor chain | ✓ |
| `validate_span` / `capability_check_span` / `precondition_span` / `mutate_span` | Same — degrades through `obs_tracing.span_context` | ✓ |
| `record_metric` | **Always** allocates the registry (first call) and ticks counter + histogram. No env-var check. | ✗ |
| `emit_log` | **Always** calls `obs_logging.get_logger` → `bind_logger`, which returns `PlainBoundLogger` when off and writes to stdlib `logging` (formatted `f"{event} {json.dumps(merged)}"`). One log line still emitted per intent. | ✗ |

The user's review checklist required "true zero-cost no-op (no processor chain, no hidden overhead)." Spans satisfy this. **Metrics and logs do not.** With `HASSALEH_OBS=off`:

- a `CollectorRegistry` is still allocated on the first `record_metric` call;
- every dispatch ticks two metric series (in a registry no exporter scrapes) and writes one stdlib log line;
- `pii_redaction_processor` does **not** run on the off-path (it's only wired in `configure_logging` when `enabled=True`), so any future caller-controlled field would be emitted raw to stderr.

Two acceptable resolutions, in decreasing order of strictness:

1. **Add `is_obs_enabled()` guards** at the top of `record_metric` and `emit_log` (six lines total). Aligns Track D with the user's hard requirement.
2. **Document the convention.** Sprint-12's `logging.py` already has the same shape (off → plain stdlib logging, not silence). If "off = downgrade, not silence" is the project-wide convention, §2.6 should say so explicitly and the user's special check should be retired. This is an arbitration call for Dione.

I rate this **change-req** because the user's review checklist for this commit explicitly demanded zero-cost off, and Track D doesn't deliver it on two of three surfaces. The fix is six lines if option (1) is chosen.

### 4.3 PII redaction (special check #3)

§2.6 surfaces, by source, what flows out of Track D:

| Surface | Field(s) | PII assessment |
|---|---|---|
| Root span attribute | `intent.type` | Closed enum (six §3 Intent types). Not PII. ✓ |
| Root span attribute | `principal.id` | The authenticated `ApiKey.id` (a stable opaque identifier, not the bearer secret nor the lookup key). §2.5 + the `Principal` dataclass doc both treat this as a non-secret. ✓ |
| Counter labels | `intent_type`, `result` | Both closed enums. ✓ |
| Histogram label | `intent_type` | Closed enum. ✓ |
| Log fields | `intent_type`, `trace_id`, `duration_ms`, `result`, `error_code`, `principal_id` | All non-secret. ✓ |

**No `api_key`, `api_key_hash`, `api_key_lookup`, or `cypher_params` field is ever written by Track D.** `Intent.payload` is read for **none** of these helpers — only `intent.type`, `result.kind`, `result.error_code`, `ctx.principal.id`, and the runtime-computed `duration_ms`/`trace_id` cross the boundary. No payload, no Cypher, no message content.

The `_SECRET_KEY_RE` redactor in `obs_logging` (`api_key*`, `*_lookup*` → `[REDACTED_SECRET]`) runs over the structlog pipeline when `HASSALEH_OBS=on`. Track D produces no field that would trigger it; the redactor's role here is defense-in-depth against future misuse.

**Span-attribute caveat:** OTel span attributes do **not** flow through `pii_redaction_processor`; that processor only sees the structlog event dict. Track D's `start_root_span` signature is closed (no `**kwargs`), so callers cannot inject ad-hoc attributes through the public surface. Track A — which will own the `requires_capability` lookup — must be reviewed in turn for any ad-hoc attribute injection on the existing spans.

PII verdict: **clean.**

### 4.4 Span / metric naming conformity to §2.6

All names are verbatim, including the lower-cased dotted span identifiers (`hassaleh.intent.execute`, `intent.validate`, …) and the snake_cased Prometheus names (`hassaleh_intent_total`, `hassaleh_intent_duration_seconds`). No drift from the plan.

Histogram bucket choice (`0.01 … 10.0`, nine buckets) is **not** prescribed by §2.6. The 10ms floor will collapse sub-millisecond intents into the smallest bucket, which the upcoming `ha-intent-v2` dashboard PR (mentioned in §2.6, post-v1.0.1) should revisit. Non-blocking.

## 5. Test coverage

Eleven tests, all of which target §2.6 behaviors:

| §2.6 clause | Covered by |
|---|---|
| Counter labels = `{intent_type, result}` (G-CR-3) | `test_counter_has_only_intent_type_and_result_labels` |
| Counter ticks per `(intent_type, result)` | `test_record_metric_ticks_counter_with_intent_type_and_kebab_case_result` |
| Counter accepts every `ResultKind` verbatim (G-CR-4) | `test_record_metric_per_label_tick_for_every_result_kind` (parametrized over all five) |
| Histogram label = `{intent_type}` only | `test_record_metric_ticks_histogram_with_intent_type_label_only` |
| Histogram unit = seconds, input = ms | same test (`100ms → 0.100s`) |
| Four child spans under root, canonical order, parented | `test_four_child_spans_under_root_in_canonical_order` |
| Root attributes `intent.type`, `principal.id` | `test_root_span_carries_intent_type_and_principal_id_attributes` |
| No `intent.result` child (G-CR-2) | `test_no_result_child_span` |
| Log success: required fields, no `error_code` | `test_emit_log_has_required_fields_on_success` |
| Log error: `error_code` present | `test_emit_log_includes_error_code_on_non_ok` |
| Log: `trace_id` absent when no recording span | `test_emit_log_omits_trace_id_when_no_span_active` |
| Public surface importable by Track A | `test_public_surface_is_exported` |

Per-test isolation is solid: the autouse `_reset` fixture clears the five relevant env vars, resets the OTel `_TRACER_PROVIDER_SET_ONCE` guard (avoids the noisy "Overriding of current TracerProvider" stderr leak that bit Sprint-12), and wipes the module-level metric singletons via `obs._reset_for_tests()`.

**Gaps (non-blocking):**

1. **No `HASSALEH_OBS=off` behavior tests.** No test asserts that `start_root_span` is a no-op when off, that `record_metric` does (or does not) tick, or that `emit_log` emits plain-stdlib output instead of structured JSON. Tightly coupled to §4.2 above — if the change-req lands, the tests follow naturally.
2. **No cross-registry isolation test.** A bug where the `registry=_registry` kwarg was forgotten would silently register Track D's metrics in `prometheus_client.REGISTRY` and re-introduce the Sprint-12 collision. A two-line test (`assert prometheus_client.REGISTRY.get_sample_value("hassaleh_intent_total", …) is None` after a `record_metric` call) would lock the dedicated-registry invariant.
3. **No bucket-boundary test.** No assertion against `hassaleh_intent_duration_seconds_bucket{le="…"}` series, so a future change to `_DURATION_BUCKETS` won't be caught.
4. **No concurrency test for `_ensure_metrics()`.** Module-level lazy init is unsafe under simultaneous cold-start callers (see §6.2). Hard to test cleanly; eager init at daemon startup would be a better fix than a test.

## 6. Defense-in-depth advisories (Dione arbitrates)

1. **`_ensure_metrics()` lazy init is not thread-safe.** Two concurrent first-callers can each create a `CollectorRegistry`; the second overwrites the first. The Prometheus client's internal lock prevents a crash, but threads holding the first registry observe a stale view. **Recommendation:** wrap the body in a `threading.Lock` *or* (preferred) eager-init at daemon startup so Track A never lazy-initializes from a request thread.
2. **`assert _registry is not None` in `get_runtime_registry()` is strippable under `python -O`.** If `_ensure_metrics()` is ever refactored such that it doesn't always set `_registry`, the assert disappears under optimizations and `get_runtime_registry()` quietly returns `None`. Trivial fix: rebind locally — `_, _ = _ensure_metrics(); return _registry` — or have `_ensure_metrics()` return the registry too.
3. **Same-name family collision across Sprint-12 and Sprint-13** — both define `hassaleh_intent_duration_seconds`, with `{stage}` and `{intent_type}` label sets respectively. Track D's dedicated-registry choice contains the implementation-side problem, but Track E's `/metrics` exposure must scrape both registries (or expose them on separate paths) or Prometheus will reject one family. This is a **plan-level** inconsistency; flagging for editorial reconciliation, not for Track D rework.
4. **Counter-cardinality DoS by `intent_type`** is mitigated only because §3 closes the enum to six types. Track D itself does not validate `intent.type` is in that enum — it trusts Track A's pydantic validator. Defense in depth: a small whitelist guard inside `record_metric` would harden the boundary against a future Track A refactor regression. Non-blocking; out of §2.6 scope.
5. **Sub-ms histogram resolution.** `_DURATION_BUCKETS` floor at 10ms; sub-ms RAM-only intents (e.g., a `validation-error` that fails before any Cypher) collapse into the smallest bucket and ruin p50 resolution. Out of §2.6 scope; v1.0.1 follow-up under "Histogram-bucket tuning."
6. **`emit_log` does not call `obs_logging.setup()`.** Track D assumes Track A or the daemon entrypoint runs `obs_logging.setup("hassaleh-daemon", env)` before the first dispatch. Reasonable separation of concerns, but worth surfacing in the Track A wire-up review: failing to call setup means the structlog processor chain (including `pii_redaction_processor`) is bypassed.
7. **`_LOGGER_NAME = "hassaleh.runtime"` is **not** in `SERVICE_ENUM`.** This is correct — `_LOGGER_NAME` is a logger name, not a service name; only `obs_logging.setup()` validates against `SERVICE_ENUM`. Track D's `get_logger(_LOGGER_NAME)` correctly bypasses that check. Verifying for the record so a future refactor doesn't conflate the two.

## 7. Acceptance against Sprint-13 §5 (Track D row)

Track D row: "Observability wiring per §2.6: spans, metrics, structured logs." Delivered:

- 1 root span + 4 child spans (5 total — §2.6 calls for 4 children, root is implicit).
- 2 metrics (counter + histogram).
- 1 structured log line shape per intent.

Deliverable scope met. Public surface is the eight-symbol `__all__` list — exactly what Track A requires.

## 8. VERDICT

**change-req** — on a single concrete item:

> `record_metric` and `emit_log` do not honor `HASSALEH_OBS=off`. The user's review checklist for Sprint-13 Track D made "true zero-cost no-op (no processor chain, no hidden overhead)" a hard requirement. Spans satisfy it; metrics and logs do not.

Resolution path (Dione's call):

- **(a) Six-line code fix.** Add `if not obs_tracing.is_obs_enabled(): return` at the top of `record_metric` and `emit_log`, plus a corresponding `HASSALEH_OBS=off` test pair. Brings Track D into compliance with the checklist.
- **(b) Spec amendment.** Document in §2.6 that `HASSALEH_OBS=off` means "downgrade to plain stdlib logging + local-registry counters" (matching Sprint-12's `obs_logging.configure_logging` convention). Retire the "zero-cost no-op" line from the user checklist as a misread of project convention.

Everything else — span/metric/log naming, kebab-case label values (G-CR-4), two-label counter (G-CR-3), no `intent.result` child (G-CR-2), `error_code` field name (G-CR-7), dedicated registry, PII surface — is **clean**.

The seven §6 advisories are non-blocking and well-suited to a Dione-arbitrated v1.0.1 patch alongside whichever resolution path is chosen for the change-req.
