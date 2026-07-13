# UI Service — API Specification

## Purpose

`services/ui` (port **8008**) is the public face of the Agent Task Economy. It replaces the
former "Observatory" service (a React-based design that used a different, now-unused port and
never shipped past design docs). This document is the current, code-verified contract; the
retired design is at `observatory-service-specs.md` (superseded).

The service has two roles:

1. **Read side (Observatory).** Dashboards, tickers, leaderboards, quarterly reports and
   task/agent drilldowns, computed from a direct read-only connection to the shared economy
   database. Mostly a pure observer.
2. **Write side (Operator).** A small set of `/api/proxy/*` routes let a human, through the
   browser, post tasks, accept bids, approve tasks and file disputes — signed and submitted by a
   registered `UserAgent` economic identity (the **operator**), not by the human directly.

## Core Principles

- **Backend: FastAPI. Frontend: vanilla JS**, served as static HTML/CSS/JS
  (`services/ui/data/web/`). There is no build step, no React, no TypeScript. The observatory
  design's React SPA and its `/live` route are retired entirely (R6/R7) — do not carry them
  forward into new work.
- **Read path is a sanctioned exception to gateway-owned persistence.** Every other reader in
  the system goes through db-gateway's blessed read API. The UI instead opens a **direct
  read-only SQLite connection** (`aiosqlite`, `file:<path>?mode=ro`, `uri=True`) straight to the
  shared database file. This is deliberate and ratified, not a stray shortcut: per
  `docs/plans/2026-07-10-q4-ui-read-path-decision.md` (Q-4), the direct connection is **kept for
  v1, formally documented as the single sanctioned exception** to the no-direct-SQL rule (the
  "observatory pattern"). It is enforced by `config/semgrep/no-direct-sql.yml`, which carves out
  `src/ui_service/` by name, and by an architecture test
  (`tests/architecture/test_readonly_connection.py`) that asserts the connection URI literally
  contains `?mode=ro` and is opened with `uri=True`. The connection never writes; there are no
  `INSERT`/`UPDATE`/`DELETE` statements anywhere in this service.
- **Write path goes through a dedicated operator identity, never a human's own keys.** Every
  `/api/proxy/*` route is executed by a registered `UserAgent` loaded from roster handle
  `operator` (`agents/roster.yaml`), a browser-driven economic agent distinct from the platform
  (treasury/notary) identity. This is the ratified Q-2 decision
  (`docs/plans/2026-07-10-q2-operator-identity-decision.md`) — "Separates treasury power from
  browser actions and makes operator activity attributable in the economy." The platform key is
  reserved for notary operations only (contract co-signing, platform-signed service calls); it no
  longer backs the UI's proxy routes.
- **`/api/proxy/*` is unauthenticated.** There is no API key, session, or shared secret on these
  routes. The security boundary is the server bind address. Per the ratified Q-3 decision
  (`docs/plans/2026-07-10-q3-proxy-exposure-decision.md`): **"Document 127.0.0.1-only as the
  security boundary now; add a shared-secret header only if the UI is ever exposed beyond
  localhost."** `config.yaml`'s `server.host: "127.0.0.1"` is that boundary, guarded by
  `tests/unit/test_exposure_posture.py`. **As-built today: no shared-secret header exists.** This
  is not an oversight to be silently fixed here — it is the accepted posture for the current
  local-single-user deployment, with the header explicitly deferred until non-loopback exposure
  is ever proposed.
- **Startup performs no minting.** The UI process does not create the treasury balance. Treasury
  genesis (1,000,000 coins) is provisioned by the `just provision` bootstrap step
  (`agents/src/treasury_provision_cli`) to the **operator** account, independent of whether the UI
  process is ever started. See "Treasury Genesis" below.

## Service Dependencies

```
ui (8008)
  ├── Shared SQLite database (read-only, mode=ro) ── metrics, agents, tasks, events, quarterly
  ├── identity (8001)     via operator UserAgent    ── register, verify
  ├── central-bank (8002) via operator UserAgent     ── balance reads (through task-board flows)
  └── task-board (8003)   via operator UserAgent     ── post/bid-accept/approve/dispute
```

No direct HTTP dependency on reputation or court; their data reaches the UI only via the shared
database (read side).

## Tech Stack

| Component | Choice |
|---|---|
| Backend framework | FastAPI, `create_app()` factory, same lifespan/config/health pattern as every other service |
| Server | Uvicorn |
| Database access | `aiosqlite`, read-only connection (the sanctioned Q-4 exception) |
| SSE | `sse-starlette` for `GET /api/events/stream` |
| Frontend | Vanilla JS + hand-written HTML/CSS, served via `StaticFiles` with an SPA-style fallback route (`services/ui/src/ui_service/app.py`); no bundler, no framework |
| Write path | `service_auth.AgentFactory` / `UserAgent` (the shared PKI lib) — signs and sends every `/api/proxy/*` action as the `operator` roster identity |

## Configuration (`services/ui/config.yaml`)

All fields are required; there are no defaults (fail-fast on missing config).

| Key | Meaning | As-configured |
|---|---|---|
| `server.host` / `server.port` | Bind address — **the Q-3 security boundary** | `127.0.0.1` / `8008` |
| `database.path` | Path to the shared `economy.db`, opened `mode=ro` | `../db-gateway/data/economy.db` |
| `sse.poll_interval_seconds` | How often the SSE stream polls for new events | `1` |
| `sse.keepalive_interval_seconds` | SSE keep-alive comment interval | `15` |
| `sse.batch_size` | Max events pushed per SSE poll cycle | `50` |
| `frontend.web_root` | Static asset root | `data/web` |
| `request.max_body_size` | Enforced max body size (bytes) on JSON proxy routes | `1572864` (1.5 MiB) |
| `user_agent.agent_config_path` | Path to the agents' shared `config.yaml` (PKI/keys) | `../../agents/config.yaml` |
| `user_agent.handle` | Roster handle the UserAgent registers/signs as — **the Q-2 operator identity** | `operator` |

A partial `user_agent` block in the loaded config (e.g. a test config pointed at via
`CONFIG_PATH`) is backfilled key-by-key from the canonical `user_agent` section of the **UI
service's own real, checked-in `services/ui/config.yaml`** (`ui_service/config.py:
_load_default_user_agent_config` reads `services/ui/config.yaml` directly, not
`agents/config.yaml` — despite the similar-sounding name, this is not the agents' shared config
file). There is no `user_agent.treasury_balance` key — that field was removed with the
startup-mint code (frozen-test exception #10).

## Read Path — Direct Read-Only SQLite (Q-4 sanctioned exception)

- Connection: `aiosqlite.connect(f"file:{settings.database.path}?mode=ro", uri=True)`, opened
  once at startup (`ui_service/core/lifespan.py`) and reused for the process lifetime.
- If the database file is unreachable at startup, the service still starts; every endpoint that
  needs `db` depends on `DbConn` (`ui_service/core/deps.py`), which raises `503
  database_unavailable` when no connection was established.
- `GET /health` additionally reports `database_readable: bool` and `latest_event_id: int` by
  querying `MAX(events.event_id)`; a query failure sets `database_readable: false` without
  failing the health check itself.
- An injectable clock seam (`ui_service/services/database.py: _clock`) lets tests freeze "now" for
  window/trend computations; production always uses the system clock.

## Write Path — Operator Proxy (`/api/proxy/*`)

Every route below is unauthenticated at the HTTP layer (see Core Principles) and is executed by
the `operator` `UserAgent`, which signs the underlying Task Board request with its own Ed25519
key. If the `UserAgent` failed to initialize at startup (e.g. Identity unreachable), every proxy
route returns `503 user_agent_unavailable`.

| Method | Path | Request body | Behavior |
|---|---|---|---|
| `GET` | `/api/proxy/identity` | — | Returns `{"agent_id": "<operator's agent_id>"}`. `503 user_agent_not_registered` if the UserAgent has no `agent_id` yet. |
| `POST` | `/api/proxy/tasks` | `{title, spec, reward, bidding_deadline_seconds, execution_deadline_seconds, review_deadline_seconds}` | Posts a new task as the operator (two-token create + escrow lock, per Task Board contract). `502 task_creation_failed` on downstream failure. |
| `POST` | `/api/proxy/tasks/{task_id}/bids/{bid_id}/accept` | — | Accepts a bid on `task_id` as the operator (poster). `502 bid_acceptance_failed` on downstream failure. |
| `POST` | `/api/proxy/tasks/{task_id}/approve` | — | Approves a submitted task as the operator (poster). `502 task_approval_failed` on downstream failure. |
| `POST` | `/api/proxy/tasks/{task_id}/dispute` | `{reason}` | Files a dispute on a submitted task as the operator (poster). `502 dispute_filing_failed` on downstream failure. |

Request bodies: `CreateTaskRequest` (`title` 1–200 chars, `spec` 1–10,000 chars, `reward > 0`, all
three deadline-seconds fields `> 0`) and `FileDisputeRequest` (`reason` 1–5,000 chars) — both
`extra="forbid"` Pydantic models (`ui_service/schemas.py`). There is no proxy route for
submitting work, uploading assets, or rebutting a dispute — the operator only exercises the
poster-side actions listed above; worker-side flows belong to the agent runtime, not the UI.

**Request validation on the two JSON-bodied proxy routes** (`POST /api/proxy/tasks` and
`POST /api/proxy/tasks/{task_id}/dispute`) is enforced by `RequestValidationMiddleware`
(`ui_service/core/middleware.py`), ahead of FastAPI routing:
- `415 unsupported_media_type` if `Content-Type` is not `application/json`.
- `413 payload_too_large` if the body exceeds `request.max_body_size` (1,572,864 bytes), checked
  incrementally as the body streams in.
The bid-accept and approve routes carry no body and are not subject to this middleware.

## Economy Metrics

### `GET /api/metrics`

Returns a `MetricsResponse`: `gdp`, `agents`, `tasks`, `escrow`, `spec_quality`, `labor_market`,
`economy_phase`, `computed_at`. Computed fresh on every request (no caching) from
`ui_service/services/metrics.py`.

Key derivations (unchanged from the retired observatory design unless noted):

| Field | Derivation |
|---|---|
| `gdp.total` | `SUM(reward)` over `approved` tasks + `SUM(reward * worker_pct / 100)` over `ruled` tasks |
| `agents.active` | Distinct agents (poster or worker) touching a task in the last 30 days |
| `labor_market.unemployment_rate` | `(active_agents − busy_agents) / active_agents`; busy = distinct workers on `accepted`/`submitted` tasks |
| `labor_market.reward_distribution` | Buckets over all tasks, all statuses, **inclusive on both ends**: `0_to_10` = 0–10, `11_to_50` = 11–50, `51_to_100` = 51–100 (**100 is included here**, verified by `tests/integration/test_reward_bucket_boundary.py`), `over_100` = reward > 100 |
| `economy_phase.task_creation_trend` | One of `"increasing"`, `"decreasing"`, `"stable"` — comparing task creation in the last 3.5 days vs. the previous 3.5 days, ±5% tolerance for `"stable"` |

### Economy Phases

`economy_phase.phase` is derived from `task_creation_trend` and `dispute_rate` (disputed+ruled
tasks ÷ total tasks):

| Phase | Condition |
|---|---|
| `stalled` | No tasks created in the last 60 minutes — takes priority over every other rule (`completion-backlog § T-046`, shipped `9f506c9`) |
| `growing` | `task_creation_trend == "increasing"` AND `dispute_rate < 0.10` |
| `contracting` | `task_creation_trend == "decreasing"` OR `dispute_rate > 0.20` |
| `stable` | **Every other combination**, including ones the table above does not name explicitly — e.g. an `increasing` trend at a 12% dispute rate (matches neither `growing`, which needs `< 10%`, nor `contracting`, which needs a declining trend or `> 20%`), or a `flat`/`stable` trend anywhere in the 15–20% dispute-rate band. This residual is an **explicit rule, not a gap**: per the plan's §5.0 correction note, the original spec table states sufficient conditions only and leaves those combinations genuinely uncovered; no ratified decision assigns them to `growing` or `contracting`, so they resolve to `stable` by design (`ui_service/services/metrics.py: compute_economy_phase`). |

`"increasing"` is a valid `task_creation_trend` value end-to-end: the frontend's shared trend
helper (`data/web/assets/shared.js: trendVisual`) recognizes `"increasing"`/`"decreasing"`
explicitly and treats anything else as flat (T-048, verified).

### `GET /api/metrics/gdp/history`

Query params: `window` (`1h`|`24h`|`7d`, default `1h`), `resolution` (`1m`|`5m`|`1h`, default
`1m`). Returns cumulative GDP time series, computed with two bucketed `GROUP BY` aggregation
queries rather than one query per data point (`_gdp_bucket_deltas`). Invalid `window`/`resolution`
→ `400 invalid_parameter`.

### `GET /api/metrics/sparklines`

Query param: `window` (only `"24h"` is valid today — `400 invalid_parameter` otherwise). Returns
10 hourly-bucketed series (`open_tasks`, `in_execution`, `completion_rate`, `disputes_active`,
`escrow_locked`, `avg_bids_per_task`, `avg_reward`, `spec_quality`, `registered_agents`,
`unemployment_rate`) built from the `events` table via `GROUP BY substr(timestamp, 1, 13)`.

## Agents

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/agents` | Paginated (`limit`, `offset`), sortable via `sort_by` (`total_earned`\|`total_spent`\|`tasks_completed`\|`tasks_posted`\|`spec_quality`\|`delivery_quality`) and `order`. `400 invalid_parameter` for an unknown `sort_by`. Single aggregated query — no N+1. |
| `GET` | `/api/agents/{agent_id}` | Full profile: balance, stats, recent tasks, recent feedback. `404 agent_not_found`. |
| `GET` | `/api/agents/{agent_id}/feed` | Agent-scoped activity feed, cursor-paginated via `before`, filterable by `role`/`type`/`time`, `limit` clamped to 1–200. |
| `GET` | `/api/agents/{agent_id}/earnings` | Cumulative earnings time series + summary stats. |

## Tasks

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/tasks` | Paginated, filterable by `status` (must be a canonical lowercase status — `400 invalid_status` otherwise). |
| `GET` | `/api/tasks/-/competitive` | Open (or `status`-filtered) tasks ranked by bid count, `limit` 1–20. |
| `GET` | `/api/tasks/-/uncontested` | Open tasks with zero bids older than `min_age_minutes`. |
| `GET` | `/api/tasks/{task_id}` | Full drilldown: poster/worker, deadlines, timestamps, bids, assets, feedback, dispute (rebuttal + ruling if present). `404 task_not_found`. |

## Events

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/events` | Reverse-chronological, cursor-paginated (`before`/`after`), filterable by `source`/`type`/`agent_id`/`task_id`. `limit` clamped to ≤200; non-integer `limit`/`before`/`after` → `400 invalid_parameter`. |
| `GET` | `/api/events/stream` | Server-Sent Events. Polls at `sse.poll_interval_seconds`, emits keep-alive comments every `sse.keepalive_interval_seconds`, delivers up to `sse.batch_size` events per cycle. Resumable via `?last_event_id=`. |

## Quarterly Report

`GET /api/quarterly-report?quarter=YYYY-QN` (quarter optional, defaults to the current quarter).
`400 invalid_quarter` for a malformed string; `404 no_data` if no economy data exists for that
quarter. Returns GDP (with prior-quarter delta), tasks, labor market, spec quality trend, agent
registrations, and "notable" highlights (highest-value task, most competitive task, top
workers/posters).

## Pages

All five pages exist today as real server-rendered-static HTML + vanilla JS under
`services/ui/data/web/` (not API-only stubs):

| Page | File | Purpose |
|---|---|---|
| Landing | `index.html` + `assets/landing.js` | Public landing page — headline economy stats, GDP ticker |
| Observatory dashboard | `observatory.html` + `assets/observatory.js` | Live dashboard — metrics, economy phase, sparklines, event ticker |
| Task console | `task.html` + `assets/task.js` | Task drilldown + operator actions (post/accept/approve/dispute) via `/api/proxy/*` |
| Quarterly report | `quarterly-report.html` + `assets/quarterly.js` | Quarterly report view (T-093) |
| Agent profile | `agent.html` + `assets/agent.js` | Agent profile, feed, earnings (T-095) |

Static assets are mounted at `/assets`; any unmatched path falls back to `index.html`
(SPA-style routing) or a bare 404 if no `web_root` is configured (`ui_service/app.py:
_mount_frontend`).

## Health

`GET /health` → `{"status": "ok", "uptime_seconds", "started_at", "latest_event_id",
"database_readable"}`. No auth.

## Treasury Genesis

The 1,000,000-coin genesis balance is **not** minted by UI startup. It is provisioned by the
idempotent `just provision` bootstrap command (`agents/src/treasury_provision_cli`, per the
ratified Q-9 decision, `docs/plans/2026-07-10-q9-treasury-bootstrap-decision.md`):

1. Registers the `platform` and `operator` agents with Identity (no-op if already registered).
2. Creates a zero-balance bank account for the operator (`409`-tolerant — idempotent).
3. Platform-signed `credit` of `genesis_amount` (1,000,000) to the operator's account, with
   `reference: "treasury_genesis"` — idempotent via Central Bank's `(account_id, reference)`
   replay guard, so re-running `just provision` is always safe.

Genesis funds the **operator**, not the `platform` account: `platform.credit` mints unboundedly
by design (the platform is the sovereign issuer) and needs no seeded balance, while the operator
needs real funds to post task escrows through `/api/proxy/tasks`. This supersedes any earlier
"the `platform` account is seeded with 1,000,000 coins" wording — see the ratified contract delta
recorded in the target-architecture plan's §5.0 (Q-2 × Q-9 interaction, 2026-07-13).

A zero-balance operator at UI startup (before `just provision` has ever run) is a normal,
non-error state — `ui_service/core/lifespan.py` only registers the UserAgent; it never touches
the bank.

## Error Envelope

All failing responses use the **3-field** envelope everywhere in this service (the 2-field
`{"error","message"}` shape in the retired observatory design is stale):

```json
{
  "error": "snake_case_code",
  "message": "Human-readable description",
  "details": {}
}
```

Error codes observed in this service (non-exhaustive elsewhere, exhaustive for the routes listed
above): `database_unavailable` (503), `invalid_parameter` (400), `invalid_status` (400),
`invalid_quarter` (400), `agent_not_found` (404), `task_not_found` (404), `no_data` (404),
`user_agent_unavailable` (503), `user_agent_not_registered` (503), `task_creation_failed` (502),
`bid_acceptance_failed` (502), `task_approval_failed` (502), `dispute_filing_failed` (502),
`unsupported_media_type` (415), `payload_too_large` (413), `method_not_allowed` (405),
`http_error` (varies), `internal_error` (500).
