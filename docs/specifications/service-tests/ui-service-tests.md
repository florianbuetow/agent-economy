# UI Service — Production Release Test Specification

## Purpose

This document is the release-gate test specification for `services/ui` (port 8008), which
replaced the design-only "Observatory" service (see `observatory-service-tests.md`, superseded).
It is intentionally strict and unambiguous:

- Every negative case has one required status code and one required error code.
- Every failing response must use the standard 3-field error envelope.
- Any behavior not listed here is out of scope for release sign-off.

Frontend behavior (vanilla JS under `data/web/assets/`) is covered only where it consumes a
backend contract directly (e.g. trend-string handling); full UI interaction is covered by the
Playwright e2e suite under `services/ui/tests/e2e/`, out of scope for this document's assertions.

---

## Required API Error Contract (Normative for Release)

All failing responses must be JSON in this format:

```json
{
  "error": "snake_case_code",
  "message": "Human-readable description",
  "details": {}
}
```

Required status/error mappings:

| Status | Error Code | Required When |
|--------|------------|----------------|
| 400 | `invalid_parameter` | A query parameter has an invalid type or value (limit, before/after, window, resolution, sort_by, quarter format) |
| 400 | `invalid_status` | `status` query filter on `/api/tasks` is not a canonical lowercase status |
| 400 | `invalid_quarter` | `quarter` does not match `YYYY-QN`, N in 1–4 |
| 403 / 404 | `agent_not_found` | Referenced `agent_id` does not exist (404) |
| 404 | `task_not_found` | Referenced `task_id` does not exist |
| 404 | `no_data` | No economy data exists for the requested quarter |
| 405 | `method_not_allowed` | Unsupported HTTP method on a defined route |
| 413 | `payload_too_large` | JSON-bodied proxy request exceeds `request.max_body_size` |
| 415 | `unsupported_media_type` | `Content-Type` is not `application/json` on a JSON-bodied proxy route |
| 502 | `task_creation_failed` / `bid_acceptance_failed` / `task_approval_failed` / `dispute_filing_failed` | The operator's downstream Task Board call fails |
| 503 | `database_unavailable` | The read-only DB connection was never established |
| 503 | `user_agent_unavailable` | The operator `UserAgent` failed to initialize at startup |
| 503 | `user_agent_not_registered` | `GET /api/proxy/identity` called before the UserAgent has an `agent_id` |
| 500 | `internal_error` | Unhandled exception |

---

## Test Data Conventions

- All test data is inserted directly into a writable handle onto the same SQLite file the
  service's read-only connection observes (`tests/integration/conftest.py` `write_db` fixture) —
  the service itself never writes.
- Agent IDs follow `a-<uuid4>`; task IDs follow `t-<uuid4>`.
- Timestamps are ISO 8601 UTC with a `Z` suffix.
- The injectable clock seam (`ui_service/services/database.py: _clock`) is used to freeze "now"
  for any test asserting a specific trend/window boundary.

---

## Category 1: Health (`GET /health`)

### HEALTH-01 Healthy service reports readable database
**Setup:** Service started against a reachable `economy.db`.
**Action:** `GET /health`.
**Expected:** `200`; `status == "ok"`; `database_readable == true`; `latest_event_id` equals
`MAX(events.event_id)` in the database (or `0` if the events table is empty).

### HEALTH-02 Database unreachable does not fail the health check
**Setup:** `database.path` points at a nonexistent file.
**Action:** `GET /health`.
**Expected:** `200` (service still starts and reports); `database_readable == false`;
`latest_event_id == 0`.

---

## Category 2: Metrics (`GET /api/metrics`)

### MET-01 Metrics response shape
**Action:** `GET /api/metrics` against a seeded economy.
**Expected:** `200`; body contains exactly `gdp`, `agents`, `tasks`, `escrow`, `spec_quality`,
`labor_market`, `economy_phase`, `computed_at` (`extra="forbid"` — no additional fields).

### MET-02 Reward bucket boundary includes 100
**Setup:** Seed a task with `reward = 100`.
**Action:** `GET /api/metrics` before and after inserting the task.
**Expected:** `labor_market.reward_distribution.51_to_100` increments by 1; `over_100` is
unchanged.

### MET-03 Reward bucket boundary excludes 101 from 51_to_100
**Setup:** Seed a task with `reward = 101`.
**Action:** `GET /api/metrics` before and after.
**Expected:** `over_100` increments by 1; `51_to_100` is unchanged.

### MET-04 GDP total combines approved and ruled contributions
**Setup:** One `approved` task (`reward=100`), one `ruled` task (`reward=100`,
`worker_pct=60`).
**Action:** `GET /api/metrics`.
**Expected:** `gdp.total` includes `100 + floor(100 * 60 / 100)` from these two tasks (in
addition to any other seeded GDP).

### MET-05 Delta fields are null with no prior-period data
**Setup:** Clean economy, single fresh task in the current window only.
**Action:** `GET /api/metrics`.
**Expected:** `gdp.delta_1h`, `gdp.delta_24h`, `agents.delta_active`, `tasks.delta_open`,
`tasks.delta_completed_24h`, `escrow.delta_locked`, `labor_market.delta_avg_bids`,
`labor_market.delta_avg_reward` are `null` when their respective previous-period denominator is
zero (not `0` or `NaN`).

### MET-06 `list_agents` avoids N+1 queries
**Setup:** Seed N agents with tasks/feedback.
**Action:** `GET /api/agents` (see also AGT-01), instrumented query count.
**Expected:** Aggregated stats are computed with a bounded, single-digit query count independent
of N (not ~10 queries per agent).

---

## Category 3: Economy Phase

### PHASE-01 Zero-activity economy is `stalled`
**Setup:** No tasks created in the last 60 minutes (economy otherwise may have historical data).
**Action:** `GET /api/metrics`.
**Expected:** `economy_phase.phase == "stalled"`, regardless of `task_creation_trend` or
`dispute_rate` — `stalled` takes priority over every other rule.

### PHASE-02 Increasing trend, low dispute rate is `growing`
**Setup:** Recent activity present; task creation in the last 3.5 days > 1.05× the previous 3.5
days; dispute rate < 10%.
**Expected:** `phase == "growing"`, `task_creation_trend == "increasing"`.

### PHASE-03 Decreasing trend is `contracting` regardless of dispute rate
**Setup:** Recent activity present; task creation ratio < 0.95; dispute rate 0%.
**Expected:** `phase == "contracting"`.

### PHASE-04 High dispute rate is `contracting` regardless of trend
**Setup:** Recent activity present; flat task creation trend; dispute rate > 20%.
**Expected:** `phase == "contracting"`.

### PHASE-05 Residual combinations resolve to `stable` (explicit rule)
**Setup:** Recent activity present. Two sub-cases:
  (a) `task_creation_trend == "increasing"` with dispute rate exactly 12% (matches neither
      `growing`'s `< 10%` nor `contracting`'s `> 20%`);
  (b) `task_creation_trend == "stable"` with dispute rate in the 15–20% band.
**Expected:** Both sub-cases return `phase == "stable"`. This is a normative pass/fail assertion,
not an "undefined behavior" case — any phase other than `stable` for these inputs is a release
blocker.

### PHASE-06 `"increasing"` is a valid trend string end-to-end
**Setup:** Seed data producing `task_creation_trend == "increasing"`.
**Action:** `GET /api/metrics`, and separately exercise `data/web/assets/shared.js`'s
`trendVisual("increasing")`.
**Expected:** API returns `"increasing"` unmodified; the frontend helper returns the
up/green treatment (not the "unrecognized → flat" fallback).

---

## Category 4: GDP History (`GET /api/metrics/gdp/history`)

### GDP-01 Default window and resolution
**Action:** `GET /api/metrics/gdp/history` (no query params).
**Expected:** `200`; `window == "1h"`; `resolution == "1m"`; `data_points` is cumulative and
non-decreasing.

### GDP-02 Invalid window rejected
**Action:** `GET /api/metrics/gdp/history?window=3h`.
**Expected:** `400 invalid_parameter`.

### GDP-03 Invalid resolution rejected
**Action:** `GET /api/metrics/gdp/history?resolution=10s`.
**Expected:** `400 invalid_parameter`.

### GDP-04 Bucketed computation matches per-point semantics
**Setup:** Tasks approved/ruled at known timestamps spanning several buckets.
**Action:** `GET /api/metrics/gdp/history?window=7d&resolution=1h`.
**Expected:** Each bucket's cumulative GDP equals the sum of all approved/ruled contributions
with `approved_at`/`ruled_at` at or before that bucket's timestamp — verified against a
naive per-point reference computation, not just query-count.

---

## Category 5: Sparklines (`GET /api/metrics/sparklines`)

### SPARK-01 Only `24h` window is valid
**Action:** `GET /api/metrics/sparklines?window=1h`.
**Expected:** `400 invalid_parameter`.

### SPARK-02 Sparkline response shape
**Action:** `GET /api/metrics/sparklines?window=24h`.
**Expected:** `200`; `metrics` contains exactly the 10 named series; each series length equals
`len(buckets)`.

### SPARK-03 Unemployment rate sparkline is clamped to [0, 1]
**Setup:** Seed events producing edge-case working/registered counts (e.g. more work-stop events
than work-start events in a bucket).
**Action:** `GET /api/metrics/sparklines?window=24h`.
**Expected:** Every value in `metrics.unemployment_rate` is within `[0.0, 1.0]`.

---

## Category 6: Agents

### AGT-01 List agents, default sort
**Action:** `GET /api/agents`.
**Expected:** `200`; `agents` sorted by `total_earned` descending (default); pagination fields
present.

### AGT-02 Invalid `sort_by` rejected
**Action:** `GET /api/agents?sort_by=made_up_field`.
**Expected:** `400 invalid_parameter`.

### AGT-03 Agent profile not found
**Action:** `GET /api/agents/a-does-not-exist`.
**Expected:** `404 agent_not_found`.

### AGT-04 Agent feed pagination
**Setup:** Seed > `limit` feed-eligible events for one agent.
**Action:** `GET /api/agents/{agent_id}/feed?limit=5`.
**Expected:** `200`; exactly 5 events returned; `has_more == true`; a follow-up request with
`before=<oldest event_id>` returns the next page.

### AGT-05 Feed `limit` is clamped to [1, 200]
**Action:** `GET /api/agents/{agent_id}/feed?limit=10000`.
**Expected:** `200`; at most 200 events returned (no error — clamped, not rejected).

---

## Category 7: Tasks

### TASK-01 Task list filtered by valid status
**Action:** `GET /api/tasks?status=open`.
**Expected:** `200`; every returned task has `status == "open"`.

### TASK-02 Task list rejects invalid status
**Action:** `GET /api/tasks?status=not_a_status`.
**Expected:** `400 invalid_status`.

### TASK-03 Task drilldown not found
**Action:** `GET /api/tasks/t-does-not-exist`.
**Expected:** `404 task_not_found`.

### TASK-04 Task drilldown includes dispute detail when present
**Setup:** A `ruled` task with a filed claim, a rebuttal, and a ruling.
**Action:** `GET /api/tasks/{task_id}`.
**Expected:** `dispute.rebuttal` and `dispute.ruling` are both populated with their respective
fields (`ruling.worker_pct`, `ruling.summary`, `ruling.ruled_at`, etc.).

### TASK-05 Competitive tasks ranked by bid count
**Setup:** Multiple open tasks with differing bid counts.
**Action:** `GET /api/tasks/-/competitive`.
**Expected:** `200`; tasks ordered by `bid_count` descending.

### TASK-06 Uncontested tasks respect `min_age_minutes`
**Setup:** One zero-bid open task 5 minutes old, one zero-bid open task 20 minutes old.
**Action:** `GET /api/tasks/-/uncontested?min_age_minutes=10`.
**Expected:** Only the 20-minute-old task is returned.

---

## Category 8: Events

### EVT-01 Event list default pagination
**Action:** `GET /api/events`.
**Expected:** `200`; `limit` defaults to 50, reverse chronological order.

### EVT-02 `limit` clamped, not rejected, above 200
**Action:** `GET /api/events?limit=5000`.
**Expected:** `200`; at most 200 events returned.

### EVT-03 Non-integer cursor params rejected
**Action:** `GET /api/events?before=not-a-number`.
**Expected:** `400 invalid_parameter`.

### EVT-04 SSE stream delivers new events and resumes from `last_event_id`
**Setup:** Open `GET /api/events/stream?last_event_id=<N>`; insert a new event with
`event_id > N`.
**Expected:** The new event is delivered within `sse.poll_interval_seconds` (config: 1s) of
insertion; keep-alive comments are sent at `sse.keepalive_interval_seconds` (config: 15s) when
idle; at most `sse.batch_size` (config: 50) events are sent per poll cycle.

---

## Category 9: Quarterly Report

### QTR-01 Default quarter is the current quarter
**Action:** `GET /api/quarterly-report` (no `quarter` param).
**Expected:** `200` if data exists for the current quarter; `quarter` in the response matches
`current_quarter_label()`.

### QTR-02 Malformed quarter string rejected
**Action:** `GET /api/quarterly-report?quarter=2026-Q9` and `?quarter=not-a-quarter`.
**Expected:** `400 invalid_quarter` for both.

### QTR-03 No data for a valid-but-empty quarter
**Action:** `GET /api/quarterly-report?quarter=2020-Q1` (a real quarter, no economy data).
**Expected:** `404 no_data`.

---

## Category 10: Proxy — Operator Write Path

### PROXY-01 Identity returns the operator's agent_id
**Setup:** UserAgent initialized and registered at startup.
**Action:** `GET /api/proxy/identity`.
**Expected:** `200`; `agent_id` matches the registered `operator` roster handle's Identity
`agent_id` (not the `platform` agent's id).

### PROXY-02 Identity unavailable when UserAgent failed to initialize
**Setup:** Simulate UserAgent init failure (e.g. Identity unreachable at startup).
**Action:** `GET /api/proxy/identity`.
**Expected:** `503 user_agent_unavailable`.

### PROXY-03 Create task via proxy
**Action:** `POST /api/proxy/tasks` with a valid body (`title`, `spec`, `reward>0`, three
deadline-seconds fields `>0`).
**Expected:** `200`/`201` passthrough of the Task Board response; the created task's `poster_id`
equals the operator's `agent_id`; escrow is locked for the full `reward` (per the two-token
create contract) drawn from the **operator's** account balance.

### PROXY-04 Create task validation — field bounds
**Action:** `POST /api/proxy/tasks` with `title` of 201 characters (over the 200-char limit), or
`reward=0`, or a `spec` of 10,001 characters.
**Expected:** `422` (Pydantic validation) for each malformed field; no task is created.

### PROXY-05 Create task downstream failure surfaces as 502
**Setup:** Task Board returns an error for the create call.
**Action:** `POST /api/proxy/tasks` with an otherwise-valid body.
**Expected:** `502 task_creation_failed`.

### PROXY-06 Accept bid via proxy
**Setup:** An open task posted by the operator with at least one bid.
**Action:** `POST /api/proxy/tasks/{task_id}/bids/{bid_id}/accept`.
**Expected:** `200` passthrough; task transitions to `accepted`.

### PROXY-07 Approve task via proxy
**Setup:** A `submitted` task posted by the operator.
**Action:** `POST /api/proxy/tasks/{task_id}/approve`.
**Expected:** `200` passthrough; task transitions to `approved`; full reward released to the
worker.

### PROXY-08 File dispute via proxy
**Setup:** A `submitted` task posted by the operator.
**Action:** `POST /api/proxy/tasks/{task_id}/dispute` with `{"reason": "..."}` (1–5000 chars).
**Expected:** `200` passthrough; task transitions to `disputed`.

### PROXY-09 Dispute reason validation
**Action:** `POST /api/proxy/tasks/{task_id}/dispute` with `reason=""` or a 5001-char reason.
**Expected:** `422` for both; no dispute filed.

### PROXY-10 Extra fields rejected on proxy request bodies
**Action:** `POST /api/proxy/tasks` with an extra unexpected top-level field.
**Expected:** `422` (`extra="forbid"` on `CreateTaskRequest`).

---

## Category 11: Request Validation Middleware

### VAL-01 Non-JSON content type rejected on `POST /api/proxy/tasks`
**Action:** `POST /api/proxy/tasks` with `Content-Type: text/plain`.
**Expected:** `415 unsupported_media_type` — rejected before the route handler runs.

### VAL-02 Oversized body rejected on `POST /api/proxy/tasks`
**Action:** `POST /api/proxy/tasks` with a body exceeding `request.max_body_size` (1,572,864
bytes).
**Expected:** `413 payload_too_large`.

### VAL-03 Oversized body rejected on `POST /api/proxy/tasks/{id}/dispute`
**Action:** Same as VAL-02, on the dispute route.
**Expected:** `413 payload_too_large`.

### VAL-04 Body-less proxy routes are not subject to JSON validation
**Action:** `POST /api/proxy/tasks/{task_id}/approve` with no body and no `Content-Type` header.
**Expected:** Not rejected by the middleware (no 415/413) — only routed through normal FastAPI
handling.

---

## Category 12: Read Path — Sanctioned Exception (Architecture)

### READ-01 Database connection is opened read-only
**Assertion (static/architecture):** `ui_service/core/lifespan.py`'s `db_uri` literal contains
`?mode=ro`, and `aiosqlite.connect(...)` is called with `uri=True`.
**Expected:** Both hold; a code change dropping either fails this test, not just at runtime.

### READ-02 No write statements exist in the UI service
**Assertion:** No `INSERT`/`UPDATE`/`DELETE` SQL literal appears anywhere under
`src/ui_service/`.
**Expected:** Holds for the full source tree.

### READ-03 `aiosqlite` import is confined to `src/ui_service/`
**Assertion:** The repo-wide `no-aiosqlite-import` / `no-aiosqlite-from-import` semgrep rules
(`config/semgrep/no-direct-sql.yml`) carve out `src/ui_service/` by path as the sole sanctioned
exception; no other service directory imports `aiosqlite`.
**Expected:** `just code-semgrep` (repo-wide) passes with the UI's `aiosqlite` import present and
flags it anywhere else it might appear.

---

## Category 13: Exposure Posture (Q-3)

### SEC-01 Real config binds to loopback only
**Assertion:** The live `services/ui/config.yaml` has `server.host == "127.0.0.1"`.
**Expected:** Holds; a change to `"0.0.0.0"` or any non-loopback address fails this guard.

### SEC-02 No shared-secret header is required or checked
**Assertion:** `/api/proxy/*` routes accept requests with no additional auth header.
**Expected:** A `POST /api/proxy/tasks` with a valid body and no auth header of any kind
succeeds (given a valid UserAgent) — documenting, not silently fixing, the current
unauthenticated posture.

---

## Category 14: Treasury Genesis (Q-9)

### GEN-01 UI startup performs no mint
**Setup:** Stub the agent factory so any bank-mutating call (`create_account`, `credit_account`)
raises if invoked.
**Action:** Run `lifespan()` startup.
**Expected:** Startup completes; the UserAgent registers; no bank call is made; log output
contains no "treasury"/"funding failed" text.

### GEN-02 Zero-balance operator at startup is not an error
**Setup:** Freshly registered operator with no bank account yet (genesis not yet provisioned).
**Action:** Run `lifespan()` startup.
**Expected:** Startup completes without any "UserAgent initialization failed" log line.

### GEN-03 `just provision` funds the operator idempotently
**Action:** Run the treasury-provision CLI (`agents/src/treasury_provision_cli`) twice in
succession against a clean economy.
**Expected:** After the first run, the operator's balance is `genesis_amount` (1,000,000). After
the second run, the balance is unchanged (idempotent via the `(account_id, reference="treasury_
genesis")` replay guard) — no double-credit.

### GEN-04 Genesis targets the operator account, not the platform account
**Action:** Inspect the provisioning CLI's `credit_account` call.
**Expected:** `account_id` is the **operator's** agent id; the `platform` account itself is never
credited by this flow (it mints on demand elsewhere and carries no seeded balance).

---

## Category 15: Configuration

### CFG-01 Missing required config field fails startup
**Setup:** `config.yaml` with any one of `server`, `database`, `sse`, `frontend`, `request`,
`user_agent` sections omitted.
**Expected:** Settings loading raises (fail-fast); the service does not start.

### CFG-02 Partial `user_agent` block backfills from the canonical agents config
**Setup:** `config.yaml`'s `user_agent` block specifies only `agent_config_path`, omitting
`handle`.
**Expected:** `Settings.user_agent.handle` is backfilled from the agents package's own
`config.yaml` `user_agent` section and is a non-empty string; there is no `treasury_balance`
attribute anywhere on `Settings.user_agent`.
