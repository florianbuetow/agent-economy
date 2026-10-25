# UI Service — API Specification

## Purpose

`services/ui` (port **8008**) is the public face of the Agent Task Economy. It replaces the
former "Observatory" service (a React-based design that used a different, now-unused port and
never shipped past design docs). This document is the current, code-verified contract; the
retired design is at `observatory-service-specs.md` (superseded).

The service has two roles:

1. **Read side (Observatory).** Dashboards, tickers, leaderboards, quarterly reports and
   task/agent drilldowns. Task list/detail core fields and `financial_effect` come from one
   public Task Board snapshot; permitted identity, bid, asset, feedback and claim/rebuttal
   enrichment comes from the direct read-only database connection.
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
- **The operator proxy is local-only.** Q-3's single-operator scope remains, with MP-02
  enforcing an allowed Host on every request, a loopback client on every proxy route, and
  an exact same-origin Origin plus custom request marker on every proxy mutation. The marker
  is public browser CSRF defense, not a secret, login or user authentication token. Configuration
  rejects non-loopback binding; forwarding/public deployment has no supported authentication
  mode. Browser rendering treats stored content as text and uses a restrictive CSP.
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
  └── task-board (8003)   via operator UserAgent     ── task reads and post/bid-accept/approve/dispute
```

No direct HTTP dependency on reputation or court; permitted enrichment reaches the UI only via
the shared database.

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

MP-08 narrows the exception for `GET /api/tasks` and `GET /api/tasks/{task_id}`. Those routes
read the complete base task snapshot, including the exact eight-key `financial_effect`, from
the public Task Board API through the existing `UserAgent`. List pagination performs one
ordered `list_tasks(status=...)` read, computes `total_count`, then applies `offset`/`limit` to
that same snapshot. Detail performs one `get_task(task_id)` read. The database may enrich that
snapshot with public identity names, authorized bids, assets, visible feedback, and local
claim/rebuttal narrative; it never replaces status, reward, accepted bid, timestamps, ruling
fields, or settlement truth. Open-task bid details remain sealed according to the upstream
status. Task Board 404 maps to `404 task_not_found`; unavailable or malformed upstream data maps
to `502 task_board_unavailable`; an uninitialized reader maps to
`503 task_board_reader_unavailable`. There is no SQLite fallback for a core task snapshot.

Task detail also returns the exact Board `lifecycle_version`, a required nonnegative
integer. Browser commands bind to that version. Missing, null, boolean, negative or
otherwise malformed upstream versions produce `502 task_board_unavailable` rather than
a task view from which an unsafe command could be prepared.

The reader is created before Identity registration. Registration failure disables mutation
routes but leaves public Task Board reads available, and lifespan shutdown closes the shared
agent/client exactly once.

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

All routes below enforce the local browser admission contract. Economic actions use the
`operator` `UserAgent`, which signs the underlying Task Board request with its own Ed25519
key. If the `UserAgent` failed to initialize at startup (e.g. Identity unreachable), economic
proxy routes return `503 user_agent_unavailable`. The browser configuration route does not
need an initialized operator.

| Method | Path | Request body | Behavior |
|---|---|---|---|
| `GET` | `/api/proxy/browser-config` | — | Returns required `csrf_header_name` and `csrf_header_value`; neither is secret. |
| `GET` | `/api/proxy/identity` | — | Returns `{"agent_id": "<operator's agent_id>"}`. `503 user_agent_not_registered` if the UserAgent has no `agent_id` yet. |
| `GET` | `/api/proxy/tasks/{task_id}/assets/{asset_id}` | — | Signs an exact Task Board asset read as the configured operator. Returns retained file bytes, including completed-task files, as an attachment with private/no-store caching and nosniff. Preserves upstream missing/unavailable errors. The existing loopback/origin proxy admission applies. |
| `GET` | `/api/proxy/tasks/{task_id}/bids` | — | Delegates to the operator's signed public `UserAgent.list_bids` contract. Task Board authorizes poster access to open bids; preserve its 403 rejection. Return canonical amounts and enrich authorized rows with public bidder identity and visible quality. Responses are `Cache-Control: private, no-store`. |
| `POST` | `/api/proxy/task-postings/prepare` | `{title, spec, reward, bidding_deadline_seconds, acceptance_window_seconds, execution_deadline_seconds, review_deadline_seconds}` | Allocates and returns a complete immutable posting record without transport or economic effect. |
| `POST` | `/api/proxy/task-postings/{operation_id}/submit` | Complete prepared posting record | Validates the saved record and submits its exact intent. Preserves native published `201`, awaiting-lock `202`, and blocked-integrity `409` outcomes. |
| `POST` | `/api/proxy/task-postings/{operation_id}/status` | Complete prepared posting record | Validates the saved record and reconciles the same operation identity without allocating a replacement. |
| `POST` | `/api/proxy/tasks/{task_id}/bids/{bid_id}/accept` | `{operation_id, expected_lifecycle_version}` | Accepts a bid on `task_id` as the operator (poster), retaining native response status. `502 bid_acceptance_failed` on transport or malformed response. |
| `POST` | `/api/proxy/tasks/{task_id}/approve` | `{operation_id, expected_lifecycle_version}` | Approves a submitted task as the operator (poster), retaining native response status. `502 task_approval_failed` on transport or malformed response. |
| `POST` | `/api/proxy/tasks/{task_id}/dispute` | `{operation_id, expected_lifecycle_version, reason}` | Files a dispute on a submitted task as the operator (poster), retaining native response status. `502 dispute_filing_failed` on transport or malformed response. |

Request bodies: `TaskPostingPrepareRequest` (`title` 1–200 chars, `spec` 1–10,000 chars,
strict positive integer `reward`, and all four duration fields including the poster-selected
acceptance window), `TaskPostingRecord` (the exact prepared scope, intent and request hash),
`NativeMutationRequest` (caller-owned operation ID and strict nonnegative lifecycle version), and
`FileDisputeRequest` (the same native identity/version plus `reason` 1–5,000 chars)
are `extra="forbid"` Pydantic models
(`ui_service/schemas.py`). There is no proxy route for
submitting work, uploading assets, or rebutting a dispute — the operator only exercises the
poster-side actions listed above; worker-side flows belong to the agent runtime, not the UI.

**Browser admission precedes operator effects and body processing.** Exactly one syntactically
valid Host must identify an explicitly allowed local hostname/IP. Every `/api/proxy` request
must originate from a loopback client. Any supplied Origin must be unique and allowlisted.
POST/PUT/PATCH/DELETE additionally require one Origin matching the current Host, scheme and
effective port, and exactly one configured custom marker header/value. Missing or duplicate
inputs fail closed. Forwarded headers do not grant authority.

`GET /api/proxy/browser-config` supplies the marker for the shipped `ATE.proxyFetch` helper.
The helper validates same-origin proxy write targets before fetching configuration or sending
the marker. Other origins, embedded credentials, fragments and non-mutating methods are rejected.

**Request validation** bounds actual streamed bytes on all mutating requests
(`413 payload_too_large` above `request.max_body_size`, currently 1,572,864 bytes).
All posting prepare/submit/status and native accept/approve/dispute routes require
`Content-Type: application/json` (`415 unsupported_media_type`). Missing or invalid native
identity/version fields fail with 422 before operator transport. Accepted bytes are replayed
unchanged to routing.

The required `browser_security` configuration declares `allowed_hosts`, `allowed_origins`,
`csrf_header_name`, `csrf_header_value` and `content_security_policy` without defaults. Hosts
and origins must be local; header values must be printable ASCII. Successful normal responses
carry the configured CSP and `X-Content-Type-Options: nosniff`. The shipped CSP permits only
same-origin scripts/connections, denies objects/framing/base changes, and retains inline CSS
needed by existing charts. Stored names, titles, comments, summaries and identifiers use the
shared HTML encoder; identifier URL segments are encoded. Uploaded active content is never
embedded or previewed in the operator origin.

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
| `GET` | `/api/tasks` | Paginated with `limit`/`offset`, filterable by exact `poster_id`, `worker_id`, and `status` (canonical lowercase status — `400 invalid_status` otherwise). Filters apply before pagination and `total_count`. |
| `GET` | `/api/tasks/-/competitive` | Open (or `status`-filtered) tasks ranked by bid count, `limit` 1–20. |
| `GET` | `/api/tasks/-/uncontested` | Open tasks with zero bids older than `min_age_minutes`. |
| `GET` | `/api/tasks/{task_id}` | Actor-independent drilldown: poster/worker, deadlines, timestamps, aggregate `bid_count`, status-permitted bids, assets, visible feedback and dispute. Open tasks expose no bid entries; after the open phase, bids include canonical integer `amount`, public bidder identity and visible quality. `404 task_not_found`. |

Asset metadata includes `asset_id`, `uploader_id`, `filename`, `content_type`,
`size_bytes`, `content_hash`, and `uploaded_at`. The task page keeps a Documents
section available throughout the lifecycle, including completion. Text previews
render file bytes as inert text; downloads use the operator proxy. Agent profiles
provide paginated Posted Jobs and Worked Jobs links. `/task.html?view=list`
provides a task browser; `/task.html?task_id=...#documents` opens a task's documents.

The public bid count is independent of permission to read bid details. An observer sees the Bid
phase and a sealed-count message when offers exist, without bidder identities, amounts or private
proposal text. The configured poster loads details through the authorized proxy and retains bidder
display names, visible delivery-quality information, timestamps and exact offered amounts. The
browser must not replace authorization, transport or malformed-response failures with an empty bid
list: preserve the public count, show a visible failure and withhold acceptance controls until valid
authorized details are available.

## Events

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/events` | Reverse-chronological, cursor-paginated (`before`/`after`), filterable by `source`/`type`/`agent_id`/`task_id`. `limit` clamped to ≤200; non-integer `limit`/`before`/`after` → `400 invalid_parameter`. |
| `GET` | `/api/events/stream` | Server-Sent Events. Polls at `sse.poll_interval_seconds`, emits keep-alive comments every `sse.keepalive_interval_seconds`, delivers up to `sse.batch_size` events per cycle. Resumable via `?last_event_id=`. |

MP-12 requires one public event projection for history, initial/resumed SSE and agent feeds. Legacy
and new open-bid events hide actor, bid identity, amount, proposal and identifying free text; unknown
task states fail closed. Agent filters and agent-specific feeds exclude sealed bid membership before
pagination and `has_more`, so a filtered result cannot reveal participation. Once the task leaves the
open phase, legitimate bidder attribution may be shown. Private stored actor metadata must survive
this transition; stripping it permanently from new events would lose losing bidders' public history.

`feedback.submitted` never exposes sealed category, rating, comment or identifying free text through
these public event surfaces. Generic occurrence/count information is allowed. Revealed feedback and
its separate reveal event govern later quality projections; sealed rows must not affect public
quality totals, sorting, profiles, quarterly reports or sparklines. This projection contract does not
replace MP-13's durable reveal-service and timeout requirements.

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

Decoded page requests, the fallback index and the asset mount must resolve within the configured
frontend root. Traversal, absolute paths and symlinks that escape that root return 404 without
serving the external file. Ordinary unknown paths inside the root retain the index fallback.

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
needs real funds to post task escrows through the native `/api/proxy/task-postings/*` protocol.
This supersedes any earlier
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

Local error codes include: `database_unavailable` (503), `invalid_parameter` (400), `invalid_status` (400),
`invalid_quarter` (400), `agent_not_found` (404), `task_not_found` (404), `no_data` (404),
`user_agent_unavailable` (503), `user_agent_not_registered` (503), `task_posting_failed` (502),
`invalid_task_posting_record` (422), `bid_listing_failed` (502), `bid_acceptance_failed` (502),
`task_approval_failed` (502), `dispute_filing_failed` (502),
`unsupported_media_type` (415), `payload_too_large` (413), `method_not_allowed` (405),
`invalid_host` (400), `non_loopback_client` (403), `forbidden_origin` (403), `csrf_failed` (403),
`http_error` (varies), `internal_error` (500).

Validated Task Board errors retain their upstream status and error envelope. A malformed
upstream response becomes the route's 502 error; the proxy does not synthesize missing
publication evidence. Native blocked posting replies must carry the SDK's complete 409
error wrapper before normalization to the browser observation envelope.
