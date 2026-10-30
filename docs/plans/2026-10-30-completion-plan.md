# Completion plan — 2026-10-30 review

Review of the tree at `802fe37` (branch `refactor`) against the handover documents `MASTERPLAN-TO-COMPLETE-THE-PROJECT.md` and `FEATURE-COMPLETENESS.md` (both 2026-10-23). Eight independent code audits produced the evidence; their full reports with file:line citations are in [`2026-10-30-review-findings/`](2026-10-30-review-findings/). This document is the consolidated verdict and the ordered plan to finish the project as a **home-lab job board**: one host runs all services, any LAN machine registers agents and posts or takes tasks, the stack recovers from ordinary restarts.

## Verdict

The foundations hold. Root CI was green on 2026-10-25, identity and signing are sound, platform authority cannot be gained by an ordinary agent, escrow money is conserved, and the autonomous math economy runs end to end. The handover inventory is accurate: of the twenty gaps G01–G20, **none is closed**; nine are partial, eleven are missing. On top of the inventory the review found a small number of robustness and security bugs that are more urgent than any missing feature, and three things the handover does not mention at all: the stack cannot be bound to the LAN, no supervisor restarts a crashed service, and there is no observability.

The project is finishable without architecture change. The order below puts "runs robustly on the LAN" before "all features", because that is the owner's stated use.

## Handover checklist result

| Gap | Handover claim | Verified status | Report |
|---|---|---|---|
| G01 accepted-bid pricing | missing | **MISSING.** Acceptance stores the posted reward as `accepted_reward`; the bid amount is stored but never paid. Trap: `accepted_reward` is also the escrow amount receipts are checked against, so the fix needs a separate price field and a Bank settle operation. | A1 |
| G02 V6 schema active | missing | **MISSING.** Fresh DB boots V5 only; V6 artifact/publication SQL loaded only by test fixtures. | A1 |
| G03 poster inputs | missing | **MISSING.** No route, no SDK method. | A1, A6 |
| G04 upload crash recovery | partial | **PARTIAL.** In-process cleanup solid; no sweep of `.staging/` or orphan blobs after a crash; no upload idempotency key, so a retry after a lost response duplicates the asset. | A1 |
| G05 immutable output manifest | missing | **MISSING at runtime.** Gateway V2 manifest exists; Task Board pins protocol 1. | A1 |
| G06 download policy | partial | **PARTIAL.** Nonterminal public + terminal 410 work and are tested; no Court grant, no archive sweep. | A1 |
| G07 complete evidence bundle | missing | **MISSING.** Byte-budget truncation, UTF-8 replacement, hashes/filenames dropped, no untrusted-content boundary. | A2, A7 |
| G08 ruling persisted before delivery | missing | **MISSING + live bug** (see Bugs 2–4). | A2 |
| G09 independent quality findings, 3 judges | missing | **MISSING.** Both quality scores derived from payout pct; shipped profile is one mock judge. | A2 |
| G10 feedback party/outcome authorization | missing | **MISSING + security bug** (Bug 5). | A2, A7 |
| G11 durable timed reveal | partial | **PARTIAL.** Read-time computation; UI and API disagree after timeout. | A2 |
| G12 service-owned quality score | missing | **MISSING.** Feeder and UI each compute their own; disputed tasks counted twice. | A2, A3 |
| G13 human poster controls | partial | **PARTIAL.** Post/bids/accept/approve/dispute/download exist; no inputs, cancel, feedback, balance, funds guidance. | A3, A4 |
| G14 human worker UI | missing | **MISSING.** | A3, A4 |
| G15 external-agent onboarding | partial | **PARTIAL.** SDK methods complete; no funding path for a non-roster agent, no standalone example, monorepo-only install. | A3, A6 |
| G16 worker/feeder restart recovery | partial | **PARTIAL.** Intents and feeder postings durable; worker job state in memory, accepted job never resumed. | A3 |
| G17 SSE/metrics/clients | partial | **PARTIAL.** Duplicate events on reconnect; clients turn malformed 2xx into empty success; sync gateway client on Task Board event loop. | A3 |
| G18 non-math workload | missing | **MISSING.** | A3 |
| G19 ops readiness/backup | partial | **PARTIAL.** Tiered start works; failed tier leaves earlier tiers running; backup only for isolated runtime; stale-minting doc already fixed. | A3, A8 |
| G20 docs match shipped | partial | **PARTIAL.** Specs match routes; README undercounts proxy routes; task-board POST /tasks example stale. | A3, A6 |

## Confirmed bugs (ranked)

Each was verified by the auditing agent reading the cited code; bug 1 was additionally reproduced with a probe. Bugs 1, 2 and 5 were then **re-verified independently by the review lead** at the cited lines; the confirming evidence is noted inline.

1. **A db-gateway outage permanently stops Task Board's deadline sweep.** `deadline_evaluator.py:275-284` calls `list_tasks` outside the per-task try; a transport error ends the asyncio task and nothing restarts it. After one gateway restart, review timeouts, auto-approve and dispute-to-Court triggers stop until Task Board restarts. (A3) *Lead re-verified:* `self._store.list_tasks(...)` at line 278 sits before the `try` at line 286; the store is a sync `httpx.Client` (`task_db_client.py:127`) whose `ConnectError` is not caught there; `core/lifespan.py` attaches no `add_done_callback` to `deadline_sweep_task` (line 145). The A3 report also named posting recovery; that part is **withdrawn**: `posting_recovery.py:120` catches `PlatformHttpError`, which is `httpx.HTTPError` (`task_db_client.py:56`) and therefore includes `ConnectError`, and `financial_effect_recovery.py:317-326` catches `Exception`. Only the deadline sweep is vulnerable.
2. **A partly delivered Court ruling never finishes.** `ruling_orchestrator.py:444-475` settles on Task Board, then records feedback, then persists the ruling. If step 2 or 3 fails the dispute is reverted; Task Board stops triggering because the task is already `ruled`; a manual retry re-judges and the Gateway rejects the duplicate `operation_id`. Money is paid, ruling never stored. The existing retry test passes only because its fake Task Board accepts duplicates. (A2) *Lead re-verified:* order at lines 448-473 is `_evaluate_judges` → `_record_task_ruling` → `_record_feedback` → `persist_ruling`; the `except ServiceError` at 474 calls `revert_to_rebuttal_pending`, which (`dispute_db_client.py:296-298`) sets `rebuttal_pending` and deletes any ruling; the ruling `operation_id` is deterministic per dispute (`operation_id_for("court-ruling", (dispute_id,))`, line 362), so a retry with a new lifecycle version collides at the Gateway; `task_ruling.py:231-246` only accepts the ruling when the task is still `disputed`.
3. **A crash during judging leaves the dispute stuck in `judging` forever.** No lease, no reclaim. (A2)
4. **Two concurrent ruling triggers can both run judges**; the loser's revert undoes the winner. (A2)
5. **Anyone can file reputation feedback about anyone for any task id.** `reputation/routers/feedback.py:181-214` checks signer == sender only; never consults Task Board. Fake ratings go public after the 24 h reveal. (A7, A2) *Lead re-verified:* the only authorization in the route is `signer_agent_id != from_agent_id_in_payload` (lines 192-198); `services/feedback.py` validates fields, rating and duplicates only (lines 34-191); a grep of `services/reputation/src` for `task_board` or `TaskBoard` returns nothing, so the service has no way to check task parties or outcome.
6. **A Task Board restart kills the task-feeder process.** Loops run in a `TaskGroup`; one `ConnectError` cancels the group. (A3)
7. **A worker abandons accepted jobs** after `max_poll_attempts` or any restart; next cycle scans only `open` tasks, so the poster's escrow waits for the execution deadline. Also: solve failure leaves the job unexecuted; upload happens before the intent is journaled (duplicate on retry); ruling payout reads a field that does not exist and so records every ruling as a full win. (A3)
8. **Deliverables are downloadable by anyone while a task is accepted/submitted/disputed.** Unauthenticated list + download; a rival takes the work for free. (A7)
9. **Court prompt injection.** Spec, claim, rebuttal and deliverables are pasted raw; with `panel_size: 1` one manipulated vote decides. Only matters once a live LLM judge is configured. (A7, A2)
10. **UI "Post Task" re-submits the previous posting** on second click; successful post shows no link to the task. (A4)
11. **Court error messages leak LLM provider details** (api_base, partial key, raw output) into 502 bodies. (A7)

Smaller items (no global storage quota, world-readable `data/keys` dir, content-type passthrough on downloads, `/docs` enabled on the UI, SSE duplicate replay, balances public via UI, late rebuttals accepted, upload duplicates on retry) are in the reports and folded into the phases below.

## Security and data-leak review (A7)

Trust tiers today: Identity registration/lookup, Task Board reads (including nonterminal asset list/download), Reputation reads and Court dispute reads are **unauthenticated**; all task mutations, bids, accounts, feedback and escrow lock are **agent-signed** and verified through Identity with the signer compared to the payload principal; credit, escrow release/split, rulings and Court filing are **platform-signed** and verified locally; every DB Gateway route except `/health` needs a per-request JWS from one of five configured service keys bound to the body and query digest and a per-caller capability set. The UI proxy signs as `operator` for any loopback caller passing Host/Origin checks. JWS is EdDSA-only, lifetime 300 s, skew 30 s, no nonce; replay is harmless because every mutation carries a caller `operation_id`.

Ranked findings. Severity assumes the LAN binding of Phase 1; today everything is loopback-only.

| # | Sev | Finding | Where | Fix (plan item) |
|---|---|---|---|---|
| H1 | High | Reputation accepts feedback for any task from any agent about any agent; no Task Board lookup; fake ratings become public after the 24 h reveal and reputation drives bid selection. | `reputation/routers/feedback.py:181-214`, `services/feedback.py:75-200` | 0.4 |
| M1 | Med | Deliverables and poster inputs are listable and downloadable by anyone while a task is accepted/submitted/disputed. | `task-board/routers/assets.py:92-124`, `asset_manager.py:563-605` | 0.6 |
| M2 | Med | Court judge prompt injection: spec, claim, rebuttal and deliverables interpolated raw between plain section markers; payout clamped 0–100 so the blast radius is the disputed escrow. Live only once a real LLM judge is configured. | `court/judges/prompts.py:55-73`, `llm_judge.py:85-96` | 3.1 |
| M3 | Med | Plain HTTP on the LAN: bid tokens (containing `amount`) and 300 s bearer tokens are sniffable, defeating sealed bidding and letting a sniffer read balances. | all services | Phase 1 decision (TLS front proxy) |
| M4 | Med | The UI loopback guard validates `server.host`, which nothing uses (the justfile hardcodes the bind host), and the operator proxy has no secret beyond the loopback-client check and a fixed CSRF marker served by `/api/proxy/browser-config`. Rebinding the UI, or any same-host forwarder without `X-Forwarded-For`, lets LAN hosts spend as `operator`. | `ui/config.py:226`, `core/middleware.py:173-180`, `justfile:251` | 1.1 and Phase 1 decision (LAN UI) |
| M5 | Med | Agent balances and earnings are public through the UI while the Bank restricts them to the owner. | `ui/services/agents.py:302-306, 561-590` | owner decision 4 |
| M6 | Med | No global storage quota; open registration plus 10 files × 10 MiB per task lets one funded agent fill the disk via self-accepted tasks. | `asset_manager.py:238`, `identity/routers/agents.py:24` | 2.6 |
| L1 | Low | Open registration without proof of possession; display names not unique (`platform`, `operator` can be registered). | `identity/routers/agents.py:24-43` | 1.8 |
| L2 | Low | Court claims, rebuttals and rulings are publicly listable. Acceptable if transparency is intended. | `court/routers/disputes.py:268-300` | none unless decided |
| L3 | Low | `services/court/.env` is 0644; `data/keys` dir is 0755 (key files are 0600). | — | 1.8 |
| L4 | Low | Court 502 bodies carry LiteLLM exception text (provider errors, `api_base`, possibly partial key, raw output). | `llm_judge.py:62,118-120`, `ruling_orchestrator.py:260-263` | 0.8 |
| L5 | Low | Uploaded Content-Type is stored and served back; `attachment` + `nosniff` currently prevent stored XSS, and uploads served from the UI origin satisfy `script-src 'self'`. | `streaming_upload.py:175-184`, `ui/routers/proxy.py:40-52` | 0.6 |
| L6 | Low | Gateway `/health` echoes the absolute DB path on failure; UI bid-list error echoes internal URLs; UI `/docs` and `/openapi.json` enabled and list the proxy surface. | `db-gateway/routers/health.py:41`, `ui/services/proxy.py:532`, `ui/app.py:38` | 0.8 |

Checked and holding: no path for an ordinary agent to gain platform authority; agent A cannot sign as B; poster/worker checks are enforced in both Task Board and the Gateway; self-bid rejected; sealed bids hidden by API and UI while open; sealed feedback hidden until `visible`; no secrets or tokens in git or logs; unhandled errors return a generic 500; no path traversal in asset storage; no CORS middleware; no confirmed XSS (about 30 innerHTML sinks spot-checked, all through `ATE.escapeHTML`).

## Observability design (A5)

**Current state.** Structured JSON logs go to stdout and a daily file per service (`service_commons/logging.py:17-137`), configured per service. Timestamps have second precision; there is no request, trace or correlation id anywhere. No `/health` reflects background-loop liveness (db-gateway and reputation do return 503 when their database is unavailable, `db-gateway/routers/health.py:37-44`; the others return ok whenever the process is up); Task Board's three background loops have no liveness signal, so a dead deadline sweep (the only autonomous path to Court) is invisible. Every `ServiceError` logs at WARNING (`exceptions.py:75`), producing about 100k expected-404 warnings. `ruling_orchestrator.py` has no log calls. Agents log plain text to stderr at a hardcoded INFO level (`task_feeder/__main__.py:43`, `math_worker/__main__.py:39`). Services launched by `start-all` send crash output only to the launching terminal.

**Design: one thin OpenTelemetry helper, nothing per service.**

- Dependencies, added only to `libs/service-commons/pyproject.toml`, pinned as a matched pair because each instrumentation beta tracks exactly one SDK minor: `opentelemetry-sdk~=1.45.0`, `opentelemetry-exporter-otlp-proto-http~=1.45.0`, `opentelemetry-instrumentation-fastapi==0.66b2`, `opentelemetry-instrumentation-httpx==0.66b2`; bump all four together. No logging, sqlite, LiteLLM or asyncio instrumentation and no Prometheus client.
- Config, a required `telemetry:` section in all seven `services/*/config.yaml` and `agents/config.yaml`, no defaults, `extra="forbid"`:
  ```yaml
  telemetry:
    enabled: true
    otlp_endpoint: "http://localhost:4318"
    sample_ratio: 1.0
    excluded_urls: "health,events"
  ```
  Test fixtures set `enabled: false`.
- Helper `libs/service-commons/src/service_commons/telemetry.py` (~80 lines): `TelemetryConfig`, `setup_telemetry(config, service_name, app | None)`, `shutdown_telemetry()`, `annotate(**attributes)`. When enabled it installs a `TracerProvider` with `ParentBased(TraceIdRatioBased(sample_ratio))` and a batch OTLP/HTTP span exporter, a `LoggerProvider` with a batch OTLP log exporter attached as one `LoggingHandler` to the service logger that `setup_logging` already owns, `HTTPXClientInstrumentor().instrument()` once (covers service-clients, service-auth, the sync Gateway clients and LiteLLM, with W3C `traceparent` propagation), and `FastAPIInstrumentor.instrument_app(app, excluded_urls=...)` when an app is given. The FastAPI call must happen in `create_app()`, not in lifespan, because the middleware stack is built first. Never capture headers or bodies.
- `JSONFormatter` gains `trace_id`/`span_id` from the current span (about 6 lines), so file logs and OTLP logs share ids and `just logs` keeps working. `ServiceError` with status below 500 logs at INFO.
- Per-service code is limited to the config field, one `setup_telemetry` call in `create_app`, and one `shutdown_telemetry` in lifespan shutdown. Agents call the helper with `app=None`, use a configured log level, and open one span per loop cycle so their outbound calls nest under it.
- Business attributes via `annotate(...)` at the lifecycle sites (post `task_creation.py:168`, bid/accept `task_bidding.py:40,222`, submit/approve `task_review.py:31,86`, dispute/ruling `task_ruling.py:37,198`, ruling execution `ruling_orchestrator.py:494`, settlement `ledger_db_client.py:285,332,388`, deadline sweep, recovery cycles, worker and feeder decisions), always including `task.id`, so one TraceQL query `{ span.task.id = "…" }` returns every step of a task across services.
- Task Board `/health` loop liveness is delivered by Phase 0 item 0.1; Phase 6 only consumes it through the httpcheck receiver (item 6.4 depends on 0.1).
- Viewer: `grafana/otel-lgtm`, one container bundling the collector, Loki, Tempo, Prometheus and Grafana, OTLP on 4318 and Grafana on 3000, with a ~30-line collector override adding an `httpcheck` receiver on the seven `/health` URLs so health, logs and traces share one UI. Two justfile recipes, `observability-up` and `observability-down`. Exporters are batch and non-blocking; if the collector is down, file logs remain the record. Fallback if containers are refused: the OpenObserve single binary.
- Total about 350 lines, roughly half tests and config. Change list and attribute table are in the A5 report.

## Not in the handover, needed for the goal

- **LAN binding is impossible by config.** `server.host` in every config.yaml is dead; the justfile hardcodes `--host 127.0.0.1` and `scripts/stop_agent.py:509` matches that literal. The UI rejects non-loopback hosts in code. (A8)
- **No supervisor.** `start-all` orphans children onto the launching terminal; a crash stays down. (A8)
- **No funding path for an external agent.** Only `fund-feeder` and `provision` exist, hardwired to roster handles. A LAN machine can take work but cannot post. (A6, A8)
- **No observability.** JSON logs exist but have no request/trace correlation; `/health` always says ok even when the deadline sweep is dead; ~100k expected-404 WARNINGs; Court ruling code logs nothing; agents log plain text at a hardcoded level. (A5)
- **Containers / kind:** the Q-6 descope still stands. Server-side services must be co-located (UI reads `economy.db` directly, shared `fcntl` lock, keys and `agents/config.yaml` read from disk). The only viable container shape is one all-in-one image as one Pod with one PVC, and that first needs the UI frontend moved out of `services/ui/data` and a single data root. Defer until after the LAN goal works. (A8)

## Plan

Work with focused tests and the component `just ci-quiet` while implementing. An item closes only under the delivery-governance rule: its own failing-test-first proof plus root `just ci-quiet` exit 0 from the repo root (about 50 minutes; several finished items may share one root run). Sizes: S < half a day, M one to two days, L several days.

Tracker: the canonical tracker is `openspec/specs/completion-backlog/spec.md`. Items in Phases 2–4 and 7 fall under existing open scenario pairs and close through them: 2.x → T-126/T-127 (artifacts) and the accepted-price part of T-120/T-121; 3.1–3.3 → T-128–T-131; 3.4–3.5 and 0.4 → T-134–T-137; 4.x → T-138/T-139 and T-146/T-147; 0.5 → T-142–T-145; 5.x → T-140/T-141; 1.6–1.7 → T-150/T-151; 7.x → T-152–T-159. Items with no existing scenario (0.1, 0.2, 0.3, 0.6, 0.7, 0.8, 1.1–1.5, 1.8, 6.1–6.4, and the commit-hook fix) must be registered as new `T-203+` scenario pairs with WHEN/THEN wording before implementation starts; this document is the design rationale, not a second tracker.

### Phase 0 — Stop the bleeding (robustness and security bugs)

| # | Item | Done when | Size |
|---|---|---|---|
| 0.1 | Supervise Task Board background loops: catch transport errors around the sweep's `list_tasks`, add a done-callback on all three loop tasks that logs and restarts; report loop liveness in Task Board `/health` (this is the single owner of that health change; Phase 6 only consumes it). | Test with gateway down for N intervals shows the deadline sweep resume; `/health` is non-ok while any loop task is done. | S |
| 0.2 | Feeder loops survive dependency restarts (wrap iterations, back off on `httpx.HTTPError`/`ServiceError`). | Task Board raising `ConnectError` once leaves all three loops running. | S |
| 0.3 | Court: persist votes, `worker_pct`, summary and request snapshot in one compare-and-set claim **before** any Task Board/Reputation call; delivery replays the stored payload with the stored `expected_lifecycle_version`; add a judging lease with expiry and a reclaim sweep; make `begin_ruling` a status-checked CAS. | Reputation failing once after settlement reaches `ruled` with one judge invocation and one settlement against real Gateway `operation_id` semantics; expired `judging` lease is reclaimed; two concurrent triggers produce one ruling. | M |
| 0.4 | Reputation feedback authorization: look up the task; accept only approved/auto-approved/ruled tasks, only between that task's poster and worker, direction and category checked; store `source` = `peer` or `court`; exact replay 200, changed content 409. (Closes G10.) | Non-party, wrong direction, unknown task and ineligible outcome are rejected through `POST /feedback`. | M |
| 0.5 | Worker resumes assigned jobs: on each cycle list `accepted`/`submitted`/`disputed` tasks with `worker_id == me` before scanning open tasks; persist solution and upload state; journal the upload inside the intent; poll acceptance until the task leaves `open`; fix the ruling payout field. (Closes most of G16.) | Worker killed after acceptance, restarted, submits and is paid; restart after dispute files exactly one rebuttal. | M |
| 0.6 | Asset read authorization: list/download require a signed token; allow poster, assigned worker, platform/court, operator; Court's `DeliverableFetcher` sends a platform token; always serve `application/octet-stream`. | Unauthenticated or third-party download of a submitted task returns 401/403; poster and worker get 200. | S |
| 0.7 | Fix the UI repeat-post bug and link to the new task after posting. | Second Post click with new terms creates a new task; completed posting shows an anchor to the task page. | S |
| 0.8 | Court 502 bodies become "Judge N failed"; exception logged server-side only. Gateway `/health` and UI bid-list errors stop echoing internal paths/URLs. UI `/docs` and `/openapi.json` are disabled. | Unit tests assert the fixed messages; `GET /docs` on the UI returns 404. | S |

### Phase 1 — Run on the LAN, stay up

| # | Item | Done when | Size |
|---|---|---|---|
| 1.1 | Make `server.host` authoritative: `_launch-*`, per-service `run` recipes and `stop_agent.py` read the host from the effective config. No new flag, no default. | `start-all` then `stop-all` succeed with `server.host: 0.0.0.0` on identity; `lsof` shows `*:8001`. | S |
| 1.2 | LAN config: `0.0.0.0` for identity, central-bank, task-board and reputation (the four exposed ports); db-gateway, court and UI stay `127.0.0.1` (Task Board reaches Court locally and Court's only agent-facing routes are public reads). Document NTP (30 s skew) and the macOS firewall prompt. | Second LAN machine: `curl http://<ip>:8003/health` ok, `:8005` and `:8007` refused. | S |
| 1.3 | `just fund-agent <agent_id> <amount> <operation_id>` generalising `fund_feeder_cli`; document the grant procedure in README. | An externally registered, non-roster agent is funded with one command and can post. | S |
| 1.4 | Remote-agent kit doc: config copy with `platform.*_url = http://<server-ip>:800x`, trimmed roster, own `keys_dir`, NTP. | A math worker on a second machine registers, bids and is paid end to end. | S |
| 1.5 | Supervision: `just supervise-install` / `supervise-uninstall` generating launchd plists (macOS) or systemd user units (Linux) from one template, using the exact launch argv so `stop_agent.py` still recognises them; include feeder and mathbot units. Document that stop/start go through the supervisor in that mode. | `kill -9` of the task-board pid is followed by a healthy restart within 10 s. | M |
| 1.6 | `start-all` partial-failure cleanup and per-service log redirection. | Injected Court start failure leaves no listening ports. | S |
| 1.7 | `just backup <dest>` / `just restore <src>`: stop, tar `economy.db` (+wal/shm), `data/keys`, task-board assets, `data/agent-runtime`, court `.env`, start. | Restore into a fresh checkout then `start-all` shows the same balances and resumes a pending task. | S |
| 1.8 | Hygiene and registration: untrack `services/ui/data/logs/*` (14 files tracked today), fix stale `UI__`/`AGENT__` env-override comments, create `data/keys` with 0700, fail `start-all` on a world-readable court `.env`, reserve the `platform` and `operator` display names at Identity registration (L1). | `git ls-files services/ui/data/logs` prints nothing; a registration named `platform` is rejected by a test. | S |

Decision for the owner (not blocking 1.1–1.8): **LAN UI.** Exposing the dashboard needs the loopback validators relaxed, the operator proxy kept loopback-only or given a bearer token, and a Q-3 amendment. Until decided, the UI stays on the server host and is used there or over SSH port-forward. **TLS**: tokens travel in clear on the LAN for 300 s; sealed bids are readable on the wire. Accept for a trusted home LAN, or front the four exposed ports with one TLS reverse proxy later.

### Phase 2 — Money and artifacts (master plan §1)

| # | Item | Done when | Size |
|---|---|---|---|
| 2.1 | G01: add `accepted_price` separate from the escrow amount; add a Bank "settle" operation that credits the worker an exact amount and refunds the remainder to the poster atomically with a receipt; approve/timeout pay the price, ruling pays `floor(price*pct/100)`; recovery and receipt matching use the right field; UI and feeder show receipt-based earnings; reconcile `test_economic_cycle.py`. | Budget 100 / bid 60 pays 60 and refunds 40 on approve and on timeout; 50 % ruling on price 61 pays 30 and refunds 70; exact retry adds no ledger rows. | M |
| 2.2 | G02: load V5 + artifact + publication SQL as the single current schema on fresh DBs; drop the V5-only admission. | Fresh Gateway starts current with all `board_artifact_*` tables. | S |
| 2.3 | G04/G05: wire `ArtifactWriter` into Gateway routes and Task Board streaming upload (reservation → attempt → ready); switch submit to protocol 2 with a frozen output manifest through Task Board, SDK `submit_deliverable` and agents; add an upload idempotency key; add a startup/periodic sweep for orphaned `.staging/` and `live/` files. | Two-file submission's manifest hashes match downloaded bytes through the SDK; upload set cannot change afterwards; killed-mid-upload restart leaves no orphan and a retry completes once. | L |
| 2.4 | G03: poster input flow (posting intent → input upload → frozen input manifest → publish) via `PrepublicationWriter`, Task Board routes and SDK `submit_task_posting(..., inputs=)`; inputs visible to bidders and frozen with the accepted terms. | Bidder downloads exact input bytes after publish; manifest immutable after acceptance. | L |
| 2.5 | G06: Court evidence grant independent of public access; archive sweep after three days, no purge; poster keeps signed read access after terminal state. | Court reads after terminal, public gets 410, poster gets 200, archived bytes survive a restart. | M |
| 2.6 | Global or per-agent storage quota in upload eligibility. | A funded agent cannot exceed the configured total. | S |

### Phase 3 — Court and reputation (master plan §2)

| # | Item | Done when | Size |
|---|---|---|---|
| 3.1 | G07: complete evidence bundle (frozen spec, input and output manifests with hashes, uploader and role, claim, rebuttal); reject or explicitly defer oversized/undecodable evidence; wrap each untrusted section as JSON data with random-nonce delimiters and a system instruction that section content is data. | Tests for oversized asset, invalid UTF-8 and an injected section header each show explicit behaviour and no silent change. | M |
| 3.2 | G09: versioned judge output with `spec_quality`, `delivery_quality`, `worker_pct`, explanations; malformed votes fail the panel instead of defaulting to 50; ship a 3-judge OpenAI-compatible profile. | Malformed vote fails; ruling stores both quality findings independently of payout. | M |
| 3.3 | Rebuttal replay and deadline: identical rebuttal 200, changed 409, late rejected (Court and Task Board). | Three cases pass through the public API. | S |
| 3.4 | G11: background sweep sets `visible = 1` on timeout and emits one `feedback.revealed` event. | Reputation API and UI agree after timeout; one event. | S |
| 3.5 | G12: `GET /agents/{id}/quality` with separate dimensions, equal per-task weighting, Court override, eligibility filter, explicit `unrated`, counts, restricted provenance; feeder and UI call it and delete their local formulas. | Disputed task counts once; feeder acceptance reads one service score. | M |

### Phase 4 — Human participation through the UI (master plan §3, G13/G14)

| # | Item | Done when | Size |
|---|---|---|---|
| 4.1 | Jobs list in every nav; status / posted-by-me / worked-by-me filters wired to `/api/tasks`; "Needs my action" panel (bids to accept, submissions to review). | Operator finds tasks awaiting them without scrolling the ticker. | S |
| 4.2 | Single task overview: spec, all deadlines with countdowns, budget and accepted price, bids, worker, files, dispute and ruling together; phase strip is navigation only; SSE refresh preserves phase and form input; task-scoped history via `/api/events?task_id=`; correct copy for cancelled/expired. | One page shows the whole job; a half-typed dispute reason survives an event. | M |
| 4.3 | Visible action errors and plain-language recovery copy; balance and escrow explanation on the create form; duration fields labelled with units and explanations. | Accept/approve/dispute failures render on the page; reward above balance blocks submit with a clear message. | S |
| 4.4 | Poster controls: input attachments on create, cancel, feedback. | UI e2e posts with an attachment, cancels one task, leaves feedback on another. | M |
| 4.5 | Human-worker flow: proxy bid, input download, output upload, submit, rebuttal for the operator; controls shown to the right party in the right status. | Operator completes a feeder task end to end through the UI, including a rebuttal branch. | L |
| 4.6 | Link every agent name; agent activity tab rendering `/api/agents/{id}/feed` (bids, payouts, disputes, rulings) with live updates. | Every poster/worker/bidder/party name is an anchor; agent page shows history. | S |
| 4.7 | SSE: honour `Last-Event-ID` or reconnect with the cursor and dedupe by `event_id`; buffer events while the Observatory feed is paused. | Forced reconnect yields no duplicate rows; "N new" on resume. | S |

### Phase 5 — External agents (G15)

| # | Item | Done when | Size |
|---|---|---|---|
| 5.1 | SDK standalone: drop the `service-commons`/FastAPI runtime dependency from `service-auth`; documented `BaseAgent.from_urls(name, key_path, base_urls, token_ttl_seconds, token_policy)`; typed `EconomyApiError(status, error, message, details)` from `_request`; move platform-only Court methods and `lock_escrow` off `BaseAgent`. | Fresh project outside the repo installs it without FastAPI; a 409 surfaces its code; `BaseAgent` has no method an ordinary signature cannot satisfy. | M |
| 5.2 | `examples/external_agent.py` (~100 lines) using only `service-auth` and explicit URLs, run as an e2e against `start-all`: register → funded by operator → work a task → post a task with inputs → another worker completes it. (B11) | Example passes in CI. | S |
| 5.3 | `docs/specifications/service-api/agent-protocol.md`: every agent route with signed payload, `aud`/`resource`, idempotency rules, `lock_operation_id` UUIDv5 recipe with a test vector; replace the stale `POST /tasks` example. | A test recomputes the documented vector. | S |
| 5.4 | Document `/api/events/stream` (or a Task Board equivalent) as the discovery feed with an SDK async iterator. | A worker learns acceptance from an event without polling. | S |

### Phase 6 — Observability (new; OpenTelemetry)

Requires two owner approvals first: editing `libs/service-commons` (AGENTS.md says libs are not edited directly) and running one Docker container (`grafana/otel-lgtm`) despite the Q-6 descope. The container is the viewer only; the services stay native.

| # | Item | Done when | Size |
|---|---|---|---|
| 6.1 | One helper `service_commons/telemetry.py` (`setup_telemetry`, `shutdown_telemetry`, `annotate`) from a required `telemetry:` config section (`enabled`, `otlp_endpoint`, `sample_ratio`, `excluded_urls`); deps: `opentelemetry-sdk`, `-exporter-otlp-proto-http`, `-instrumentation-fastapi`, `-instrumentation-httpx`; global httpx instrumentation covers all clients including LiteLLM; `JSONFormatter` gains `trace_id`/`span_id`; OTLP log handler. | Log line inside a span carries `trace_id`; outbound httpx call carries `traceparent`. | M |
| 6.2 | Wire seven services (config field, one `create_app` call, one shutdown call, fixtures with `enabled: false`) and agents (configured log level, per-cycle span). | Root `just ci-quiet` exits 0; agent cycle span parents its httpx span. | S |
| 6.3 | Business attributes (`task.id`, `agent.id`, `operation_id`, amounts) at the sites tabulated in the A5 report; 4xx `ServiceError` logs at INFO; Court ruling path logs its steps. | Task Board and Central Bank spans share one trace id with `task.id` set. | S |
| 6.4 | `just observability-up/down` running `grafana/otel-lgtm` with an httpcheck receiver on the seven `/health` URLs. | One feeder/worker cycle shows a task's trace, correlated logs and seven health series in Grafana. | S |

### Phase 7 — Vision and release proof (master plan §4)

| # | Item | Done when | Size |
|---|---|---|---|
| 7.1 | Real judge evaluation (B13): small declared precise/vague × good/bad corpus through the configured 3-judge profile; retain outputs, latency, cost, one unavailable-provider result. | Dated evidence note under `docs/plans/`. | M |
| 7.2 | G18 non-math classification workload with inputs and multiple outputs; two workers, repeated rounds, persisted outcome-based adaptation; report with sample counts and uncertainty. | Measured report, no scripted winner. | L |
| 7.3 | Docs sweep: README proxy count, run/LAN/supervise/backup instructions, API specs vs routes. | Spot-checked divergences gone. | S |
| 7.4 | Final isolated root `just ci-quiet` plus the public journeys (B09–B12, B14, B16). | Exit 0; journeys recorded. | M |

### Deferred (explicitly not in this plan)

- **Kubernetes / kind.** Only as one all-in-one image after the UI frontend leaves `services/ui/data` and a single `data_root` exists. Not needed for the LAN goal.
- **Per-service containers.** Blocked by co-location constraints; the Q-6 descope stands.
- **Exhaustive fault matrices, mutation-test expansion, second migration paths.** Excluded by the master plan.

## Verification run on 2026-10-30

The commit hook runs `just ci-quiet` in the session's current directory and blocked the first commit attempt during this review (session directory was `services/identity`, so the identity gate ran). Cause: every service's `code-audit` gate (pip-audit) failed on two advisories published after the 2026-10-25 green run, `msgpack 1.2.1` (GHSA-j586-36cw-2gc2, fixed in 1.2.3) and `joserfc 1.7.1` (GHSA-jpxq-7h8p-2q76, GHSA-9pg5-8c7x-5hfx, fixed in 1.7.6). No code or test failed. Fix applied the same way as commit `e8c7fd0`: constraint floors raised to `msgpack>=1.2.3` in all seven service `pyproject.toml` files and `joserfc>=1.7.6` in central-bank, task-board and court, then each service re-locked with `uv sync --all-extras`.

| Gate | Result |
|---|---|
| Phase 0 structure and PATH policy | exit 0 |
| libs service-commons, service-clients, service-auth `just ci-quiet` | exit 0, 0, 0 |
| identity, central-bank, task-board, reputation, court, db-gateway, ui `just ci-quiet` (before fix) | exit 1 ×7, all at `code-audit` |
| the same seven after the re-lock | exit 0 ×7 |
| agents, tools `just ci-quiet` | exit 0, 0 |
| root `just ci-quiet` after the re-lock | see below |

Root `just ci-quiet` was started after the re-lock and **aborted at 7½ minutes** at the owner's instruction (the review brief caps any test run at ten minutes). It had passed Phase 0, Phase 1 (libs), Phase 2 identity and central-bank, and the architecture tests before the abort; no gate failed. The remaining root phases are the same per-component gates listed above plus the cross-service, E2E and browser suites, which were last green on 2026-10-25 and are not affected by a lockfile floor bump. A full root run (about 50 minutes) is still owed before the project is declared complete, as the master plan requires.

Operational defect found while doing this: `.claude/settings.json` runs `scripts/ci-quiet-hook.sh` before every Bash command containing `git commit`, with a 300 s timeout, and the hook runs `just ci-quiet` in the session's current directory. From the repo root that gate takes about 50 minutes, so it cannot finish inside the timeout. Observed behaviour during this review: when the session directory was a service directory the hook ran that service's gate and blocked the first commit attempt on the `code-audit` failure above; when the session directory was the repo root (commit `032aea2`) the hook ran the root gate, hit the 300 s timeout, Claude Code terminated the hook together with its whole `just ci-quiet` subtree (verified: nothing survived at 301 s), and the commit proceeded without a completed gate. The commit hook is therefore not an effective gate for root-level commits today. Add to Phase 1: make the hook run only the component gates for the staged paths within the timeout, or raise the timeout to cover the root gate and accept the wait. Until then the root gate must be run and recorded by hand before any release claim.

## Decisions needed from the owner

1. Approve editing shared libraries, which AGENTS.md lists as not edited directly: `libs/service-commons` for the telemetry helper (Phase 6) and `libs/service-auth` for the standalone SDK split (Phase 5, item 5.1).
2. Approve one Docker container for the observability viewer (Phase 6).
3. LAN UI: keep loopback-only (recommended until a login exists) or amend Q-3 (Phase 1 note).
4. Whether agent balances and earnings are public via the UI (today they are, while the Bank restricts them to the owner).
5. TLS on the LAN (security finding M3): accept clear-text HTTP on a trusted home network for now (recommended), or add one TLS reverse proxy in front of the four exposed ports as a Phase 1 item.

## Traceability to the review brief

The review brief (tracked verbatim as [`2026-10-30-review-findings/review-brief.md`](2026-10-30-review-findings/review-brief.md); the working copy is the untracked `@GOAL.md` at the repo root) sets these requirements. Each is mapped to where this plan satisfies it.

| Brief requirement | Where satisfied |
|---|---|
| Use the handover documents as a checklist and verify what was actually implemented | "Handover checklist result": all twenty gaps G01–G20 re-verified against code with file:line evidence |
| Find what is missing or where the logic is bad | "Confirmed bugs (ranked)" and the per-gap status column |
| Only specific tests via the justfile, no mutation testing, abort anything over ten minutes | "Verification run on 2026-10-30": component gates capped at 600 s, root gate aborted at 7½ min, no mutation suite run |
| Opus subagents on aspects chosen by the reviewer | Eight audits, reports in `2026-10-30-review-findings/` |
| A user-experience agent: intuitive posting and tracking, click-through from dashboards to agents and tasks | A4 report; Phase 4 items 4.1–4.7 |
| Observability gap fully specified, OpenTelemetry, lightweight and thin, all logs in one place | "Observability design (A5)"; Phase 6 |
| An agent that assesses how easy it is to program a poster agent and a worker agent | A6 report (ratings 2/5 poster, 3/5 worker); Phase 5; `fund-agent` in item 1.3 |
| Security review, no data leaks | "Security and data-leak review (A7)"; Phase 0 items 0.4, 0.6, 0.8; Phase 2 item 2.6 |
| A working, running economy that auto-recovers to some extent, single instance per service | Bugs 1–7; Phase 0 items 0.1–0.5; Phase 1 item 1.5 (supervision) |
| Assessment of running in Kubernetes-in-Docker | A8 verdict in "Not in the handover" and "Deferred": only an all-in-one image is viable until the UI frontend leaves `services/ui/data` and a single data root exists |
| Run all services on one machine with any computer on the network able to register and post tasks (most important) | "Not in the handover" (bind host, funding, supervision); Phase 1 items 1.1–1.4 |
| Thorough but fair, important things only, no invented issues, robustness over nice-to-haves | Phases ordered robustness first; P3 UX polish and kind deferred; non-findings listed under security |
| Code as the primary source of information | Every finding cites file:line; bugs 1, 2 and 5 re-verified by the lead |
| Outcome: a completion plan for finishing the project | This document, Phases 0–7 with done-criteria, plus the owner decisions list |

## Suggested sequencing

Phase 0 and Phase 1 together make the stack usable and robust on the LAN with today's feature set, and are small. Phase 2 and 3 are the bulk of the remaining product work and match the master plan's §1 and §2 order. Phase 4 and 5 can run in parallel lanes against the Phase 2/3 APIs as they land. Phase 6 is independent and can start as soon as the approvals arrive. Phase 7 last.
