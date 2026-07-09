# Observatory Service — Production Release Test Specification

## Purpose

This document is the release-gate test specification for the Observatory Service.
It is intentionally strict and unambiguous:

- Every negative case has one required status code and one required error code.
- Every failing response must use the standard error envelope.
- Any behavior not listed here is out of scope for release sign-off.

The Observatory is **read-only** — it never writes to the database. Tests seed data by inserting directly into the shared SQLite database (simulating what the other five services would write), then assert that the Observatory's API endpoints return correct aggregations, shapes, and behaviors.

Frontend (React) tests are out of scope for this specification. This document covers only the backend API.

---

## Required API Error Contract (Normative for Release)

All failing responses must be JSON in this format:

```json
{
  "error": "ERROR_CODE",
  "message": "Human-readable description"
}
```

Required status/error mappings:

| Status | Error Code           | Required When                                              |
|--------|----------------------|------------------------------------------------------------|
| 400    | `INVALID_PARAMETER`  | A query parameter has an invalid value or type             |
| 404    | `AGENT_NOT_FOUND`    | Referenced `agent_id` does not exist                       |
| 404    | `TASK_NOT_FOUND`     | Referenced `task_id` does not exist                        |
| 404    | `NO_DATA`            | Requested quarter has no economy data                      |
| 400    | `INVALID_QUARTER`    | Malformed quarter string (must be `YYYY-QN` where N is 1–4) |

---

## Test Data Conventions

- All test data is inserted directly into the shared SQLite database before each test or test group.
- Agent IDs follow the format `a-<uuid4>`.
- Task IDs follow the format `t-<uuid4>`.
- Timestamps are ISO 8601 UTC.
- Coin amounts are positive integers.
- The database is reset between test groups (each category starts with a clean database).
- "Seed the standard economy" refers to a reusable fixture that inserts a known set of agents, tasks, bids, feedback, and transactions (defined below).

### Standard Economy Seed Data

The following data is used by multiple test categories. Individual tests may add to it or use a clean database instead.

- **3 agents:** Alice (`a-alice`), Bob (`a-bob`), Charlie (`a-charlie`)
- **5 tasks:**
  - `t-1`: posted by Alice, completed by Bob, reward 100, status `approved`, approved 2h ago
  - `t-2`: posted by Alice, completed by Charlie, reward 50, status `approved`, approved 1h ago
  - `t-3`: posted by Bob, in execution by Charlie, reward 80, status `accepted`, accepted 30m ago
  - `t-4`: posted by Charlie, open for bidding, reward 60, status `open`, created 15m ago, 2 bids
  - `t-5`: posted by Alice, disputed, reward 120, status `ruled`, worker_pct 70, ruled 30m ago
- **Bids:** t-1 has 3 bids (Bob won), t-2 has 2 bids (Charlie won), t-4 has 2 bids (pending)
- **Escrow:** t-3 has 80 locked, t-4 has 60 locked
- **Feedback:** 4 visible feedback records (spec_quality and delivery_quality for t-1 and t-2)
- **Bank transactions:** salary credits, escrow locks, escrow releases matching the task states
- **Events:** 15 events covering agent registrations, task creation, bids, approvals, dispute, ruling
- **Court:** 1 claim on t-5 with rebuttal and ruling (worker_pct=70)

---

## Category 1: Health (`GET /health`)

### HEALTH-01 Health check returns ok
**Setup:** Service is running with a readable database.
**Action:** `GET /health`
**Expected:**
- `200 OK`
- Body includes `status`, `uptime_seconds`, `started_at`, `latest_event_id`, `database_readable`
- `status = "ok"`
- `uptime_seconds >= 0`
- `started_at` is valid ISO 8601
- `database_readable = true`
- `latest_event_id` is a non-negative integer

### HEALTH-02 Health check reports latest event ID
**Setup:** Seed database with 15 events (standard economy).
**Action:** `GET /health`
**Expected:**
- `latest_event_id = 15` (or the highest `event_id` in the seeded data)

---

## Category 2: Metrics (`GET /api/metrics`)

### MET-01 Metrics returns all required fields
**Setup:** Seed the standard economy.
**Action:** `GET /api/metrics`
**Expected:**
- `200 OK`
- Response contains all top-level keys: `gdp`, `agents`, `tasks`, `escrow`, `spec_quality`, `labor_market`, `economy_phase`, `computed_at`
- `computed_at` is valid ISO 8601

### MET-02 GDP total is sum of approved task rewards plus dispute worker payouts
**Setup:** Seed the standard economy (t-1 approved 100, t-2 approved 50, t-5 ruled 70% of 120 = 84).
**Action:** `GET /api/metrics`
**Expected:**
- `gdp.total = 234` (100 + 50 + 84)

### MET-03 GDP per agent is total divided by active agents
**Setup:** Seed the standard economy.
**Action:** `GET /api/metrics`
**Expected:**
- `gdp.per_agent = gdp.total / agents.active`
- Value matches with floating point tolerance of 0.1

### MET-04 Active agents count excludes inactive agents
**Setup:** Seed the standard economy + register a 4th agent "Dave" with no tasks (never posted or worked).
**Action:** `GET /api/metrics`
**Expected:**
- `agents.total_registered = 4`
- `agents.active = 3` (Dave is not active — no participation in any task in the last 30 days)

### MET-05 Tasks by status counts are correct
**Setup:** Seed the standard economy.
**Action:** `GET /api/metrics`
**Expected:**
- `tasks.completed_all_time = 2` (t-1 and t-2 approved)
- `tasks.open >= 1` (t-4 is in bidding)
- `tasks.in_execution >= 1` (t-3 is accepted)
- `tasks.disputed >= 0`

### MET-06 Completion rate is correct
**Setup:** Seed the standard economy (2 approved, 1 disputed/ruled).
**Action:** `GET /api/metrics`
**Expected:**
- `tasks.completion_rate` = 2 / (2 + 1) ≈ 0.667 (±0.01)

### MET-07 Escrow total locked
**Setup:** Seed the standard economy (t-3: 80 locked, t-4: 60 locked).
**Action:** `GET /api/metrics`
**Expected:**
- `escrow.total_locked = 140`

### MET-08 Spec quality percentages sum to 1.0
**Setup:** Seed the standard economy with known feedback ratings.
**Action:** `GET /api/metrics`
**Expected:**
- `spec_quality.extremely_satisfied_pct + spec_quality.satisfied_pct + spec_quality.dissatisfied_pct` ≈ 1.0 (±0.01)

### MET-09 Spec quality only counts visible feedback
**Setup:** Seed the standard economy. Add 2 sealed (invisible) feedback records with `dissatisfied` ratings.
**Action:** `GET /api/metrics`
**Expected:**
- Spec quality percentages are unchanged (sealed feedback is excluded)

### MET-10 Labor market avg bids per task
**Setup:** Seed the standard economy (t-1: 3 bids, t-2: 2 bids, t-4: 2 bids).
**Action:** `GET /api/metrics`
**Expected:**
- `labor_market.avg_bids_per_task` is computed across tasks with bids

### MET-11 Labor market reward distribution buckets
**Setup:** Seed the standard economy (rewards: 100, 50, 80, 60, 120 — all 5 tasks regardless of status).
**Action:** `GET /api/metrics`
**Expected:**
- `labor_market.reward_distribution` has keys `0_to_10`, `11_to_50`, `51_to_100`, `over_100`
- Buckets are inclusive on both ends: `0_to_10` = [0,10], `11_to_50` = [11,50], `51_to_100` = [51,100], `over_100` = [101,∞)
- Bucket counts match: `0_to_10: 0`, `11_to_50: 1` (50), `51_to_100: 2` (80, 60), `over_100: 2` (100, 120)
- All tasks are counted regardless of status (open, accepted, approved, disputed/ruled)

### MET-12 Economy phase is stalled when no recent tasks
**Setup:** Empty database — no tasks at all.
**Action:** `GET /api/metrics`
**Expected:**
- `economy_phase.phase = "stalled"`

### MET-13 Metrics on empty database
**Setup:** Empty database.
**Action:** `GET /api/metrics`
**Expected:**
- `200 OK`
- `gdp.total = 0`
- `agents.total_registered = 0`
- `agents.active = 0`
- `tasks.total_created = 0`
- `economy_phase.phase = "stalled"`

---

## Category 3: GDP History (`GET /api/metrics/gdp/history`)

### GDP-01 Returns data points for 1h window
**Setup:** Seed the standard economy with tasks approved at different times in the last hour.
**Action:** `GET /api/metrics/gdp/history?window=1h&resolution=1m`
**Expected:**
- `200 OK`
- `window = "1h"`, `resolution = "1m"`
- `data_points` is an array with at most 60 entries
- Each entry has `timestamp` (ISO 8601) and `gdp` (integer >= 0)
- `data_points` is sorted by timestamp ascending
- Final data point's `gdp` matches current total GDP

### GDP-02 GDP is monotonically non-decreasing
**Setup:** Seed the standard economy.
**Action:** `GET /api/metrics/gdp/history?window=1h&resolution=1m`
**Expected:**
- Each `data_points[i].gdp >= data_points[i-1].gdp`

### GDP-03 Invalid window parameter
**Action:** `GET /api/metrics/gdp/history?window=2h`
**Expected:**
- `400`, `error = INVALID_PARAMETER`

### GDP-04 Invalid resolution parameter
**Action:** `GET /api/metrics/gdp/history?window=1h&resolution=30s`
**Expected:**
- `400`, `error = INVALID_PARAMETER`

### GDP-05 Empty database returns zero data points
**Setup:** Empty database.
**Action:** `GET /api/metrics/gdp/history?window=1h&resolution=1m`
**Expected:**
- `200 OK`
- `data_points` is an array (may be empty or contain zero-value entries)

---

## Category 4: Events Stream (`GET /api/events/stream`)

### SSE-01 Stream delivers events
**Setup:** Seed the standard economy with 15 events.
**Action:** Open SSE connection to `GET /api/events/stream?last_event_id=0`
**Expected:**
- Receives multiple `economy_event` SSE messages
- Each message `data` is valid JSON with fields: `event_id`, `event_source`, `event_type`, `timestamp`, `summary`, `payload`
- Events arrive in `event_id` ascending order

### SSE-02 Cursor-based resumption
**Setup:** Seed the standard economy with 15 events.
**Action:** Open SSE connection with `last_event_id=10`
**Expected:**
- Only receives events with `event_id > 10`
- First event received has `event_id = 11`

### SSE-03 Stream sends keepalive when no new events
**Setup:** Seed the standard economy. Wait for all events to be delivered.
**Action:** Keep SSE connection open for longer than the keepalive interval.
**Expected:**
- Receives a keepalive comment (`:` prefixed line) within the configured keepalive interval

### SSE-04 Stream includes retry field
**Action:** Open SSE connection to `GET /api/events/stream`
**Expected:**
- Connection includes a `retry:` field with a positive integer (milliseconds)

### SSE-05 New events appear on stream
**Setup:** Open SSE connection. All existing events delivered.
**Action:** Insert a new event into the `events` table.
**Expected:**
- The new event appears on the stream within 3 seconds (poll interval is 1s; 2s tolerance for scheduling jitter)

---

## Category 5: Events History (`GET /api/events`)

### EVT-01 Returns events in reverse chronological order
**Setup:** Seed the standard economy with 15 events.
**Action:** `GET /api/events`
**Expected:**
- `200 OK`
- `events` array is non-empty
- Events are sorted by `event_id` descending (newest first)
- Each event has: `event_id`, `event_source`, `event_type`, `timestamp`, `summary`, `payload`

### EVT-02 Limit parameter works
**Setup:** Seed the standard economy with 15 events.
**Action:** `GET /api/events?limit=3`
**Expected:**
- `events` array has exactly 3 entries
- `has_more = true`

### EVT-03 Before parameter for backward pagination
**Setup:** Seed the standard economy with 15 events.
**Action:** `GET /api/events?before=10&limit=5`
**Expected:**
- All returned events have `event_id < 10`
- At most 5 events returned

### EVT-04 After parameter for forward pagination
**Setup:** Seed the standard economy with 15 events.
**Action:** `GET /api/events?after=10&limit=5`
**Expected:**
- All returned events have `event_id > 10`

### EVT-05 Filter by event source
**Setup:** Seed the standard economy (events from multiple sources).
**Action:** `GET /api/events?source=board`
**Expected:**
- All returned events have `event_source = "board"`

### EVT-06 Filter by event type
**Setup:** Seed the standard economy.
**Action:** `GET /api/events?type=task.created`
**Expected:**
- All returned events have `event_type = "task.created"`

### EVT-07 Filter by agent_id
**Setup:** Seed the standard economy.
**Action:** `GET /api/events?agent_id=a-alice`
**Expected:**
- All returned events have `agent_id = "a-alice"`

### EVT-08 Filter by task_id
**Setup:** Seed the standard economy.
**Action:** `GET /api/events?task_id=t-1`
**Expected:**
- All returned events have `task_id = "t-1"`

### EVT-09 Combined filters
**Setup:** Seed the standard economy.
**Action:** `GET /api/events?source=board&type=task.created`
**Expected:**
- All returned events match both filters

### EVT-10 Empty result set
**Setup:** Seed the standard economy.
**Action:** `GET /api/events?source=nonexistent`
**Expected:**
- `200 OK`
- `events` is empty array
- `has_more = false`

### EVT-11 Invalid limit parameter (below minimum)
**Action:** `GET /api/events?limit=-1`
**Expected:**
- `400`, `error = INVALID_PARAMETER`

### EVT-12 Limit exceeding maximum is silently clamped to 200
**Setup:** Seed 250 events.
**Action:** `GET /api/events?limit=9999`
**Expected:**
- `200 OK` (not 400 — over-limit is clamped, not rejected)
- `events` array has exactly 200 entries
- `has_more = true`

### EVT-13 Non-integer limit is rejected
**Action:** `GET /api/events?limit=abc`
**Expected:**
- `400`, `error = INVALID_PARAMETER`

---

## Category 6: Agent Listing (`GET /api/agents`)

### AGT-01 Returns agents with stats
**Setup:** Seed the standard economy.
**Action:** `GET /api/agents`
**Expected:**
- `200 OK`
- `agents` is non-empty array
- Each agent has: `agent_id`, `name`, `registered_at`, `stats`
- `stats` contains: `tasks_posted`, `tasks_completed_as_worker`, `total_earned`, `total_spent`, `spec_quality`, `delivery_quality`
- `total_count` matches number of agents in database

### AGT-02 Default sort by total_earned descending
**Setup:** Seed the standard economy.
**Action:** `GET /api/agents`
**Expected:**
- Agents are sorted by `stats.total_earned` descending

### AGT-03 Sort by tasks_completed ascending
**Setup:** Seed the standard economy.
**Action:** `GET /api/agents?sort_by=tasks_completed&order=asc`
**Expected:**
- Agents are sorted by `stats.tasks_completed_as_worker` ascending

### AGT-04 Pagination with limit and offset
**Setup:** Seed the standard economy (3 agents).
**Action:** `GET /api/agents?limit=2&offset=0` then `GET /api/agents?limit=2&offset=2`
**Expected:**
- First request returns 2 agents
- Second request returns 1 agent
- No overlap between the two result sets
- `total_count = 3` in both responses

### AGT-05 Invalid sort_by parameter
**Action:** `GET /api/agents?sort_by=nonexistent`
**Expected:**
- `400`, `error = INVALID_PARAMETER`

### AGT-06 Spec quality counts are from visible feedback only
**Setup:** Seed the standard economy. Add sealed feedback for Alice.
**Action:** `GET /api/agents`
**Expected:**
- Alice's `spec_quality` counts exclude the sealed feedback

---

## Category 7: Agent Profile (`GET /api/agents/{agent_id}`)

### PROF-01 Returns full agent profile
**Setup:** Seed the standard economy.
**Action:** `GET /api/agents/a-bob`
**Expected:**
- `200 OK`
- Response includes: `agent_id`, `name`, `registered_at`, `balance`, `stats`, `recent_tasks`, `recent_feedback`
- `agent_id = "a-bob"`

### PROF-02 Stats match known data
**Setup:** Seed the standard economy (Bob completed t-1 for 100 coins, posted t-3).
**Action:** `GET /api/agents/a-bob`
**Expected:**
- `stats.tasks_completed_as_worker >= 1`
- `stats.total_earned >= 100`
- `stats.tasks_posted >= 1`

### PROF-03 Recent tasks are reverse chronological
**Setup:** Seed the standard economy.
**Action:** `GET /api/agents/a-alice`
**Expected:**
- `recent_tasks` is sorted by completion/creation date descending

### PROF-04 Recent feedback only includes visible feedback
**Setup:** Seed the standard economy with visible and sealed feedback for Bob.
**Action:** `GET /api/agents/a-bob`
**Expected:**
- `recent_feedback` contains only visible feedback entries

### PROF-05 Agent not found
**Action:** `GET /api/agents/a-nonexistent`
**Expected:**
- `404`, `error = AGENT_NOT_FOUND`

---

## Category 8: Task Drilldown (`GET /api/tasks/{task_id}`)

### TASK-01 Returns full task lifecycle
**Setup:** Seed the standard economy.
**Action:** `GET /api/tasks/t-1`
**Expected:**
- `200 OK`
- Response includes: `task_id`, `poster`, `worker`, `title`, `spec`, `reward`, `status`, `deadlines`, `timestamps`, `bids`, `assets`, `feedback`, `dispute`
- `task_id = "t-1"`
- `status = "approved"`

### TASK-02 Poster and worker are resolved to names
**Setup:** Seed the standard economy (t-1: posted by Alice, completed by Bob).
**Action:** `GET /api/tasks/t-1`
**Expected:**
- `poster.name = "Alice"` and `poster.agent_id = "a-alice"`
- `worker.name = "Bob"` and `worker.agent_id = "a-bob"`

### TASK-03 Bids include bidder delivery quality
**Setup:** Seed the standard economy with feedback for Bob.
**Action:** `GET /api/tasks/t-1`
**Expected:**
- Each bid in `bids` array has `bidder.delivery_quality` object
- `bidder.delivery_quality` has keys: `extremely_satisfied`, `satisfied`, `dissatisfied`

### TASK-04 Accepted bid is marked
**Setup:** Seed the standard economy (t-1: Bob's bid accepted).
**Action:** `GET /api/tasks/t-1`
**Expected:**
- Exactly one bid has `accepted = true`
- That bid's `bidder.agent_id = "a-bob"`

### TASK-05 Task with dispute includes full dispute data
**Setup:** Seed the standard economy (t-5: disputed, ruled, worker_pct=70).
**Action:** `GET /api/tasks/t-5`
**Expected:**
- `dispute` is not null
- `dispute.claim_id` is present
- `dispute.reason` is non-empty string
- `dispute.rebuttal` is present with `content` and `submitted_at`
- `dispute.ruling` is present with `ruling_id`, `worker_pct = 70`, `summary`, `ruled_at`

### TASK-06 Task without dispute has null dispute field
**Setup:** Seed the standard economy.
**Action:** `GET /api/tasks/t-1` (approved, no dispute)
**Expected:**
- `dispute = null`

### TASK-07 Open task has no worker
**Setup:** Seed the standard economy (t-4: open for bidding).
**Action:** `GET /api/tasks/t-4`
**Expected:**
- `worker = null`
- `status = "open"`

### TASK-08 Feedback only includes visible entries
**Setup:** Seed the standard economy with sealed and visible feedback for t-1.
**Action:** `GET /api/tasks/t-1`
**Expected:**
- `feedback` array only contains entries where the feedback is visible

### TASK-09 Task not found
**Action:** `GET /api/tasks/t-nonexistent`
**Expected:**
- `404`, `error = TASK_NOT_FOUND`

---

## Category 9: Competitive Tasks (`GET /api/tasks/-/competitive`)

### COMP-01 Returns tasks sorted by bid count descending
**Setup:** Seed the standard economy (t-1: 3 bids, t-2: 2 bids, t-4: 2 bids).
**Action:** `GET /api/tasks/-/competitive?limit=5&status=all`
**Expected:**
- `200 OK`
- `tasks` array is sorted by `bid_count` descending
- First entry has `bid_count = 3`

### COMP-02 Status filter defaults to open
**Setup:** Seed the standard economy.
**Action:** `GET /api/tasks/-/competitive`
**Expected:**
- All returned tasks have status `open` (bidding or execution)

### COMP-03 Limit parameter works
**Setup:** Seed the standard economy.
**Action:** `GET /api/tasks/-/competitive?limit=1&status=all`
**Expected:**
- `tasks` array has at most 1 entry

### COMP-04 Empty result when no open tasks
**Setup:** Database with all tasks approved (none open).
**Action:** `GET /api/tasks/-/competitive`
**Expected:**
- `200 OK`
- `tasks` is empty array

---

## Category 10: Uncontested Tasks (`GET /api/tasks/-/uncontested`)

### UNCON-01 Returns tasks with zero bids
**Setup:** Seed a task in bidding state with zero bids, created 20 minutes ago.
**Action:** `GET /api/tasks/-/uncontested?min_age_minutes=10`
**Expected:**
- `200 OK`
- Returned task has `minutes_without_bids >= 20`

### UNCON-02 Excludes tasks with bids
**Setup:** Seed the standard economy (t-4 has 2 bids).
**Action:** `GET /api/tasks/-/uncontested`
**Expected:**
- t-4 does not appear in results

### UNCON-03 Excludes tasks younger than min_age_minutes
**Setup:** Seed a task in bidding state with zero bids, created 5 minutes ago.
**Action:** `GET /api/tasks/-/uncontested?min_age_minutes=10`
**Expected:**
- That task does not appear in results

### UNCON-04 Excludes non-open tasks
**Setup:** Seed the standard economy (t-1 is `approved`, t-3 is `accepted` — neither is in bidding state).
**Action:** `GET /api/tasks/-/uncontested`
**Expected:**
- Neither t-1 nor t-3 appears in results (only tasks in `open` / bidding state qualify)

---

## Category 11: Quarterly Report (`GET /api/quarterly-report`)

### QTR-01 Returns report for current quarter
**Setup:** Seed the standard economy (all activity in current quarter).
**Action:** `GET /api/quarterly-report`
**Expected:**
- `200 OK`
- `quarter` matches current calendar quarter (e.g., `2026-Q1`)
- `period.start` and `period.end` are valid ISO 8601 timestamps
- `gdp.total` matches the economy's GDP for this quarter

### QTR-02 Explicit quarter parameter
**Setup:** Seed the standard economy.
**Action:** `GET /api/quarterly-report?quarter=2026-Q1`
**Expected:**
- `200 OK`
- `quarter = "2026-Q1"`

### QTR-03 GDP delta from previous quarter
**Setup:** Seed tasks in Q4 2025 (total 200 coins) and Q1 2026 (total 234 coins).
**Action:** `GET /api/quarterly-report?quarter=2026-Q1`
**Expected:**
- `gdp.previous_quarter = 200`
- `gdp.delta_pct = 17.0` (±1.0)

### QTR-04 Notable tasks are correct
**Setup:** Seed the standard economy.
**Action:** `GET /api/quarterly-report`
**Expected:**
- `notable.highest_value_task.reward` is the maximum reward among all tasks in the quarter
- `notable.most_competitive_task.bid_count` is the maximum bid count among all tasks in the quarter
- `notable.top_workers` has at most 3 entries, sorted by earnings descending
- `notable.top_posters` has at most 3 entries, sorted by spending descending

### QTR-05 Quarter number out of range
**Action:** `GET /api/quarterly-report?quarter=2026-Q5`
**Expected:**
- `400`, `error = INVALID_QUARTER` (quarters are 1–4 only; Q5 is out of range)

### QTR-06 Malformed quarter string
**Action:** `GET /api/quarterly-report?quarter=Q1-2026`
**Expected:**
- `400`, `error = INVALID_QUARTER`

### QTR-07 Quarter with no data
**Action:** `GET /api/quarterly-report?quarter=2020-Q1`
**Expected:**
- `404`, `error = NO_DATA`

---

## Category 12: SPA Fallback Routing

### SPA-01 Root serves index.html
**Action:** `GET /`
**Expected:**
- `200 OK`
- Response content type is `text/html`

### SPA-02 Frontend route serves index.html
**Action:** `GET /observatory/tasks/t-abc123`
**Expected:**
- `200 OK`
- Response content type is `text/html`
- Same content as `GET /`

### SPA-03 API routes are not caught by SPA fallback
**Action:** `GET /api/metrics`
**Expected:**
- `200 OK`
- Response content type is `application/json`
- Body is not HTML

### SPA-04 Health endpoint is not caught by SPA fallback
**Action:** `GET /health`
**Expected:**
- `200 OK`
- Response content type is `application/json`
- Body contains `status: "ok"`

### SPA-05 Static assets are served correctly
**Setup:** Frontend has been built (dist directory exists with assets).
**Action:** `GET /assets/index-abc123.js` (a known built asset)
**Expected:**
- `200 OK`
- Response content type is `application/javascript` or similar

---

## Category 13: Database Read-Only Enforcement

### RO-01 Service operates with read-only database connection
**Setup:** Seed the standard economy.
**Action:** Verify (via test instrumentation) that the database connection uses `?mode=ro` or equivalent read-only mode.
**Expected:**
- Connection is opened in read-only mode

### RO-02 All endpoints succeed with read-only connection
**Setup:** Seed the standard economy. Open database in read-only mode.
**Action:** Call every API endpoint.
**Expected:**
- All return `200 OK` (or appropriate success status)
- None fail with "attempt to write a readonly database" or similar

---

## Category 14: Edge Cases

### EDGE-01 Empty database — all endpoints return gracefully
**Setup:** Empty database (tables exist but no rows).
**Action:** Call each endpoint.
**Expected:**
- `GET /api/metrics` → `200 OK`, numeric metrics are 0, collections are empty
- `GET /api/events` → `200 OK`, `events` is empty array
- `GET /api/events/stream` → opens successfully, no events delivered
- `GET /api/agents` → `200 OK`, `agents` is empty array
- `GET /api/tasks/-/competitive` → `200 OK`, `tasks` is empty array
- `GET /api/tasks/-/uncontested` → `200 OK`, `tasks` is empty array
- `GET /api/metrics/gdp/history` → `200 OK`, `data_points` is empty or all-zero
- `GET /api/quarterly-report` → `404`, `error = NO_DATA`
- No 500 errors from any endpoint

### EDGE-02 Very long task spec text
**Setup:** Create a task with a 10,000 character spec.
**Action:** `GET /api/tasks/{task_id}`
**Expected:**
- `200 OK`
- `spec` field contains the full 10,000 character text

### EDGE-03 Agent with no activity
**Setup:** Register an agent with no tasks, no bids, no feedback.
**Action:** `GET /api/agents/{agent_id}`
**Expected:**
- `200 OK`
- `stats.tasks_posted = 0`, `stats.tasks_completed_as_worker = 0`, `stats.total_earned = 0`
- `recent_tasks` is empty array
- `recent_feedback` is empty array

### EDGE-04 Task with no bids
**Setup:** Create a task in bidding state with zero bids.
**Action:** `GET /api/tasks/{task_id}`
**Expected:**
- `200 OK`
- `bids` is empty array
- `worker = null`

### EDGE-05 Unicode in agent names and task titles
**Setup:** Create an agent named "测试代理" and a task titled "Résumé des données 📊"
**Action:** `GET /api/agents/{agent_id}` and `GET /api/tasks/{task_id}`
**Expected:**
- `200 OK` for both
- Names and titles are returned with Unicode intact
