# A5 — Observability: current state, gaps, thinnest OpenTelemetry addition

Scope: single-host home lab, all seven services + agents on one machine, no HA. Read-only review; nothing edited or run except log-file inspection and a PyPI version lookup.

**Blocker to flag first:** AGENTS.md lists `libs/` under "Files to Never Edit Directly". The design below needs one new module in `libs/service-commons`. That needs explicit owner approval.

## Current state

### Logging library
- `libs/service-commons/src/service_commons/logging.py:17-66` — `JSONFormatter`: one JSON object per line with `timestamp`, `level`, `logger`, `message`, `exception`, and every non-standard `LogRecord` attribute under `extra`. Structured: yes.
- `logging.py:24` — timestamp is `%Y-%m-%d %H:%M:%S`, **second precision, no milliseconds**. Older files (April–July) show minute precision.
- `logging.py:72-92` — `DailyRotatingFileHandler`, files `YYYY-MM-DD.log`, rollover at UTC midnight (`when="midnight"` hardcoded in lib; acceptable as the design doc fixes it).
- `logging.py:95-137` — `setup_logging(level, service_name, log_directory)`: stdout + file, on the logger named after the service, `propagate=False`. Only loggers under `<service>.*` reach the file. uvicorn's own loggers (`uvicorn.error`, `uvicorn.access`) are **not** in the file.
- No request id, trace id, or correlation id anywhere in services, libs, or agents (grep for `request_id|correlation|x-request|opentelemetry|otel|prometheus` finds nothing outside tests).
- Each service's `logging.py` is a thin wrapper (e.g. `services/central-bank/src/central_bank_service/logging.py:27-30`) calling `get_named_logger(settings.service.name, name)`.

### Config
- All seven `services/*/config.yaml` have `logging: {level, directory: "data/logs"}` (e.g. `services/task-board/config.yaml:13-15`). Compliant with the no-hardcoded-config rule.
- `services/db-gateway/config.yaml:16` still carries `format: "json"`, which the 2026-03-01 file-logging design removed. Drift, harmless only if its model accepts it.
- Service config models use `extra="forbid"` (e.g. `services/db-gateway/src/db_gateway_service/config.py:33`). A new `telemetry:` YAML section therefore needs a model field in every service.
- Retention: none. Design doc `docs/plans/2026-03-01-file-logging-design.md` ("No auto-deletion"). Today ~60 MB across `services/*/data/logs`.

### Error and audit logging
- `libs/service-commons/src/service_commons/exceptions.py:73-84` — every `ServiceError` is logged at **WARNING** with `error_code`, `error_message`, `status_code`, `details`, `path`. Expected 404s included.
- Effect in real logs: task-board 49,568 and db-gateway 51,084 "Service error" lines, dominated by `publication_not_found` 404 polling (`/task-publications/{op}`). Real failures drown.
- Central Bank emits an `audit` line per ledger operation (`ledger_db_client`, fields `operation`, `account_id`/`escrow_id`, `amount`, `tx_id`). This is the only business-event log stream.
- Log-call counts (`logger.info|warning|error|exception|debug`): central-bank 16, ui 10, task-board 6, court 5, db-gateway 5, reputation 4, identity 2, agents 85, libs 2. `services/court/src/court_service/services/ruling_orchestrator.py` has **zero** log calls.
- Secrets/bodies: no log call references tokens, payloads, bodies, keys. No request/response body logging exists. Only risk: `exceptions.py:81` logs `exc.details` verbatim.

### Process output under `just start-all`
- `justfile:254-330` backgrounds each service with `just "$recipe" &` (`justfile:296`); stdout/stderr go to the launching terminal, no redirect. Startup crashes and uvicorn access lines are visible only there and lost when it closes.
- `just logs` (`justfile:612-619`) runs `scripts/tail_logs.py`, which tails `services/<svc>/data/logs/<date>.log` color-coded. This is today's "one place", file-only, no agents.

### Health
- Every service has `GET /health` returning `status: "ok"` unconditionally plus counters (`services/identity/src/identity_service/routers/health.py:13-25`, `services/task-board/src/task_board_service/routers/health.py:13-29`). No dependency check, no background-loop liveness.
- Task Board runs three background loops created in `services/task-board/src/task_board_service/core/lifespan.py:145,178,198` (deadline sweep, posting recovery, financial-effect recovery). No `add_done_callback` anywhere. `deadline_evaluator.py:256-272` documents that unexpected errors propagate, which kills the sweep task silently while `/health` keeps saying ok.
- `services/ui` metrics router is business metrics, not ops.

### Agents
- `agents/src/task_feeder/__main__.py:43-49` and `agents/src/math_worker/__main__.py:39-43`: `logging.basicConfig(level=logging.INFO, ...)` plain text to **stderr only**. Level hardcoded (violates the no-hardcoded-config rule). No file. Started with `&` (`justfile:446,474`), so output is lost with the terminal.
- CLIs (`fund_feeder_cli`, `treasury_provision_cli`) same pattern with `print` for results.
- Agent decisions are logged as text with tags (`math_worker/loop.py:114` no suitable task, `:237-239` LLM selected/declined, `:253-264` bid amount, `:284-294` waiting for acceptance).

### HTTP clients (propagation points)
- `libs/service-clients/src/service_clients/base.py:17` `httpx.AsyncClient` (inter-service).
- `libs/service-auth/src/service_auth/agent.py:39` `httpx.AsyncClient` (agents + platform agents).
- Sync `httpx.Client` to DB Gateway: `services/task-board/src/task_board_service/services/task_db_client.py:127`, `services/court/src/court_service/services/dispute_db_client.py:27`, `services/reputation/src/reputation_service/services/feedback_db_client.py:28`. Called via `asyncio.to_thread`, which copies contextvars, so trace context survives.
- `services/court/src/court_service/services/ruling_orchestrator.py:48` `httpx.AsyncClient`; judge calls via litellm (httpx underneath).
- All are reachable by a single global `HTTPXClientInstrumentor().instrument()`; no client code changes needed.

### Dependencies
- Python `>=3.12`, `fastapi>=0.115`, `uvicorn>=0.34`, `httpx>=0.28` (lower-bound pins only). Services and agents depend on `libs/service-commons` as an editable path dep (`services/task-board/pyproject.toml:86`, `agents/pyproject.toml` via service-commons). No OTel packages present.
- Latest on PyPI today: `opentelemetry-sdk` 1.45.2, `opentelemetry-exporter-otlp-proto-http` 1.45.2, `opentelemetry-instrumentation-fastapi` / `-httpx` 0.66b2 (the 0.66b line pairs with SDK 1.45).

## Gaps (what the owner cannot see when something goes wrong)

1. **No cross-service correlation.** A Task Board "Service error" cannot be tied to the Bank or Gateway line that caused it. Which service failed a settlement is guesswork by timestamp, and timestamps are second-precision.
2. **No latency.** No request durations, no access log in files. How long a Court ruling took, or how long the judge LLM calls took, is invisible. Court's ruling path logs nothing.
3. **Signal drowned.** ~100k WARNING lines of expected 404s across task-board and db-gateway.
4. **Agents are dark.** Why a worker did not bid exists only on a terminal's stderr, as unstructured text, not in files, not correlated with the Task Board request it made.
5. **Background-loop health is a lie.** A dead deadline-sweep task (which is also the only autonomous path to Court rulings) leaves `/health` at ok. Recovery-loop failures are logged per cycle but nothing shows "last successful cycle".
6. **Startup/crash output lost.** Backgrounded `just` jobs write tracebacks to the launching terminal only.
7. **No per-task view.** No way to pull every event for one `task_id` across services; task id is present only in some `extra` fields.
8. **No single place.** Seven file trees plus terminals; no health overview over time.

## Proposed design

### Dependencies (add to `libs/service-commons/pyproject.toml` only; services and agents inherit)
```
opentelemetry-sdk>=1.45,<2
opentelemetry-exporter-otlp-proto-http>=1.45,<2
opentelemetry-instrumentation-fastapi==0.66b2
opentelemetry-instrumentation-httpx==0.66b2
```
Instrumentation betas are pinned exactly because each beta pairs with one SDK minor. **Not** needed: `opentelemetry-instrumentation-logging` (trace ids are injected by a 4-line change in `JSONFormatter`), any sqlite3/litellm/asyncio instrumentation, `prometheus-client`. The services' per-service `uv.lock` files regenerate via `uv sync`.

### Config (`telemetry:` section, required in all seven `services/*/config.yaml` and `agents/config.yaml`)
```yaml
telemetry:
  enabled: true
  otlp_endpoint: "http://localhost:4318"
  sample_ratio: 1.0
  excluded_urls: "health,events"
```
Service name comes from the existing `settings.service.name`; agents use their handle. Tests set `enabled: false` explicitly. No defaults in code: `TelemetryConfig` has no field defaults and `extra="forbid"`.

### Helper API — new `libs/service-commons/src/service_commons/telemetry.py` (~80 LOC)
```python
class TelemetryConfig(BaseModel):          # embedded in each service's Settings
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    otlp_endpoint: str
    sample_ratio: float
    excluded_urls: str

def setup_telemetry(config: TelemetryConfig, service_name: str, app: FastAPI | None) -> None
def shutdown_telemetry() -> None
def annotate(**attributes: str | int) -> None   # set attributes on the current span
```
`setup_telemetry` when enabled:
- `TracerProvider(resource={service.name}, sampler=ParentBased(TraceIdRatioBased(sample_ratio)))` with `BatchSpanProcessor(OTLPSpanExporter(f"{endpoint}/v1/traces"))`.
- `LoggerProvider` + `BatchLogRecordProcessor(OTLPLogExporter(f"{endpoint}/v1/logs"))`; attach one `LoggingHandler` to the `service_name` logger (the logger `setup_logging` already owns; it has `propagate=False`, so attach there, not to root). Note: the SDK logs API is still `opentelemetry.sdk._logs`.
- `HTTPXClientInstrumentor().instrument()` once (covers every client listed above, W3C `traceparent` propagation automatic).
- `FastAPIInstrumentor.instrument_app(app, excluded_urls=config.excluded_urls)` when `app` is given. Must be called in `create_app()`, not in lifespan, because the middleware stack is built before lifespan runs.
- Never enable header or body capture.

`JSONFormatter` change (`logging.py:26`): add `trace_id` / `span_id` from `trace.get_current_span().get_span_context()` when valid. File logs and OTLP logs then share the trace id; `just logs` keeps working.

Separate one-line fix bundled with this work: in `exceptions.py:75`, log `ServiceError` with `status_code < 500` at INFO instead of WARNING. Removes ~100k false warnings.

### Per-service integration (the only per-service code)
- `config.py`: add `telemetry: TelemetryConfig`.
- `app.py`: one call `setup_telemetry(settings.telemetry, settings.service.name, app)` (settings already loaded there, e.g. `services/task-board/src/task_board_service/app.py:21`).
- `core/lifespan.py`: `shutdown_telemetry()` on shutdown (flushes batches).
- Agents: `task_feeder/__main__.py` and `math_worker/__main__.py` call `setup_telemetry(..., app=None)` and replace hardcoded `logging.INFO` with a configured level. Agent loops open one span per cycle so the agent's outbound httpx calls become children of it.

### Business attributes for a per-task trace (`annotate(...)`, ~1 line each)
Each lifecycle step is its own trace; Tempo finds all of them with TraceQL `{ span.task.id = "t-..." }`. Bank, Gateway, Court and Reputation work appears as child spans automatically through httpx propagation.

| Event | Where | Attributes |
|---|---|---|
| task posted | `services/task-board/src/task_board_service/services/task_creation.py:168` | `task.id`, `task.reward`, `poster.id` |
| bid | `task_bidding.py:40` | `task.id`, `bid.id`, `bid.amount`, `worker.id` |
| accept | `task_bidding.py:222` | `task.id`, `bid.id` |
| submit | `task_review.py:31` | `task.id` |
| approve | `task_review.py:86` | `task.id`, `outcome=approved` |
| dispute | `task_ruling.py:37` | `task.id`, `dispute.id` |
| ruling recorded | `task_ruling.py:198` | `task.id`, `worker_pct` |
| ruling executed | `services/court/src/court_service/services/ruling_orchestrator.py:494` (`_evaluate_judges` :245 as a child span) | `dispute.id`, `task.id`, `worker_pct`, judge count |
| settlement | `services/central-bank/src/central_bank_service/services/ledger_db_client.py:285,332,388` | `escrow.id`, `tx.id`, `amount`, `operation` |
| deadline sweep | `deadline_evaluator.py:256` loop: one root span per task evaluated | `task.id`, `task.status`, `trigger=deadline_sweep` |
| recovery retry | `posting_recovery.py:115`, `financial_effect_recovery.py:93/313`: one span per cycle | `recovery.kind`, `attempts`, `error_code` |
| worker decision | `agents/src/math_worker/loop.py:114,237-239,253-264` | `task.id`, `agent.decision` (`no_task`/`declined`/`bid`), `bid.amount` |
| feeder accept/review | `agents/src/task_feeder/acceptance.py:234`, `review.py:121` | `task.id`, `agent.decision` |

### Collector (one choice): `grafana/otel-lgtm`
One container bundling an OpenTelemetry Collector, Loki (logs), Tempo (traces), Prometheus (metrics) and Grafana, receiving OTLP/HTTP on 4318 and serving Grafana on 3000. Logs, traces and health in one UI with trace-to-log links via `trace_id`.
- Health in the same place: a ~30-line `config/otel/collector.yaml` override adding the collector `httpcheck` receiver against the seven `http://host.docker.internal:800x/health` URLs. Produces `httpcheck.status` per service, no service code. Verify the image's override path and that its collector build includes `httpcheck` before relying on it.
- Q-6 descoped Docker for running the stack. This container is a third-party tool next to the stack, not a stack deployment; the owner should confirm that reading. If containers are refused, the fallback is the OpenObserve single binary (OTLP logs+traces, no httpcheck).
- justfile recipes:
```
observability-up:
    docker run -d --name otel-lgtm -p 3000:3000 -p 4318:4318 \
      -v "$PWD/config/otel/collector.yaml:/otel-lgtm/otelcol-config.yaml:ro" \
      -v otel-lgtm-data:/data grafana/otel-lgtm
observability-down:
    docker rm -f otel-lgtm
```
Exporters are batch and non-blocking. If the collector is down, spans and logs are dropped and file logs remain the record.

### What NOT to do
- No custom metrics framework, no Prometheus client, no `/metrics` endpoints.
- No per-service middleware, logging setup or tracer code beyond the one helper call.
- No changes to `libs/service-clients` or `libs/service-auth`; global httpx instrumentation covers them.
- No body or header capture, no logging of tokens or JWS.
- No sqlite3/DB instrumentation, no manual context propagation, no baggage.
- Do not remove file logging or `just logs`.

### Change list and size
| File(s) | Change | LOC |
|---|---|---|
| `libs/service-commons/pyproject.toml` | 4 deps | 4 |
| `libs/service-commons/src/service_commons/telemetry.py` | new helper | ~80 |
| `libs/service-commons/src/service_commons/logging.py` | trace_id/span_id in `JSONFormatter` | ~6 |
| `libs/service-commons/src/service_commons/exceptions.py` | 4xx at INFO | ~3 |
| `libs/service-commons/tests/` | helper disabled no-op; trace id in JSON line | ~60 |
| 7× `services/*/config.yaml` + `agents/config.yaml` + test fixture configs | `telemetry:` section (enabled false in fixtures) | ~5 each |
| 7× `config.py`, `app.py`, `core/lifespan.py` | field + 2 calls | ~4 each |
| `agents/src/{task_feeder,math_worker}/__main__.py` + config models | setup call, configured level, cycle span | ~20 |
| Business `annotate(...)` sites (table above) | 1–3 lines each | ~40 |
| `services/task-board/src/task_board_service/routers/health.py` + schema | report loop liveness via `task.done()`; status `degraded` if any loop dead | ~15 |
| `config/otel/collector.yaml`, root `justfile` | collector override + 2 recipes | ~45 |
Total about 350 LOC, roughly half tests and config.

## Work items (ordered; done-criterion each)
1. **Owner approval for `libs/service-commons` edit and the otel-lgtm container.** Done when the owner has said yes in writing.
2. **Failing tests for the helper** (`libs/service-commons/tests`): with telemetry enabled and an in-memory exporter, a log line inside a span carries `trace_id`, and an outbound httpx call carries `traceparent`. Done when tests exist and fail.
3. **Helper + JSONFormatter + deps.** Done when item 2 passes and `just ci-quiet` in `libs/service-commons` is green.
4. **4xx ServiceError at INFO.** Done when a unit test asserts a 404 `ServiceError` logs at INFO and a 500 at WARNING.
5. **Wire seven services** (config section, model field, `create_app` call, lifespan shutdown, fixture configs with `enabled: false`). Done when root `just ci-quiet` exits 0.
6. **Wire agents** (feeder, math worker, configured log level, per-cycle span). Done when an agent unit test shows a cycle span parenting its httpx span, and agents CI is green.
7. **Business attributes** at the sites in the table. Done when a cross-service test with an in-memory exporter finds Task Board and Central Bank spans sharing one trace id with `task.id` set.
8. **Task Board loop liveness in `/health`.** Done when a test cancels the sweep task and `/health` reports it dead with non-ok status.
9. **Collector config + justfile recipes.** Done when `just observability-up`, `just start-all` and one feeder/worker cycle show a task's traces, correlated logs and seven health series in Grafana on localhost:3000 (manual check, recorded in a dated `docs/plans/` note).
