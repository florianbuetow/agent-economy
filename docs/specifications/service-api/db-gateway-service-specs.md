# Database Gateway Service — API Specification

## Purpose

The Database Gateway is the persistence layer for the Agent Task Economy. It owns the shared `economy.db` SQLite database and is the only process that writes to it. All other services send structured write requests to the gateway, which executes them atomically within SQLite transactions, and read through the gateway's **blessed read API** — one `GET` route per query the rest of the system actually needs, mirroring the write side's one-endpoint-per-operation design (the single exception is the UI service's direct read-only SQLite connection, see §2.2/Q-4 of the target-architecture plan; that is a documented exception, not a second gateway).

Domain services still own their business rules. They validate inputs, enforce authorization, check signatures, and decide state transitions; the gateway translates structured requests into SQL and executes them. What the gateway does add, beyond raw INSERT/UPDATE/SELECT, is real state enforcement in the form of optional `constraints` (compare-and-set) on several write endpoints — see "Compare-and-Set Constraints (`constraints`)" below. That is a deliberate, narrow piece of state-checking logic living in the gateway, not an accident, and this document stops describing the gateway as containing "no business logic" without qualification.

## Core Principles

- **Constraints, not full business logic.** The gateway does not validate signatures, check permissions, or independently decide the task/dispute state machine — those decisions are made by the calling service. It does, however, enforce optional **compare-and-set (`constraints`)** conditions on several write endpoints as real state enforcement (e.g., "only update this task if its `status` is still `open`"), and database constraints (foreign keys, unique indexes) as the baseline safety net beneath that. See "Compare-and-Set Constraints" below.
- **Database constraints are the safety net.** Foreign key violations and unique constraint violations are caught (via `sqlite3`'s structured `exc.sqlite_errorcode`, never message-substring matching) and returned as structured errors. The gateway does not duplicate constraint logic in application code beyond the `constraints` mechanism above.
- **Every write includes an event.** Every mutating endpoint accepts an `event` object in the request body. The gateway inserts the domain write and the event row in the same transaction. No write exists without its corresponding event — this now includes `DELETE /court/rulings/{claim_id}` and `POST /court/claims/{claim_id}/status`, both of which require `event` in the body (see their endpoint docs below).
- **BEGIN IMMEDIATE, single writer, no `await` mid-transaction.** All write transactions use `BEGIN IMMEDIATE` to acquire the write lock upfront. The gateway keeps exactly one SQLite connection (WAL mode, `busy_timeout=5000ms`) for the whole process, shared by every domain writer and the reader; every write method is a plain (non-`async`) `def` that runs `BEGIN IMMEDIATE` through `COMMIT`/`ROLLBACK` to completion without `await`ing anything in between, so no other coroutine's write (or read) can interleave inside an open transaction on the shared connection. This single-writer/no-await invariant is enforced by a static architecture test (`tests/architecture/test_gap_c5_single_writer_invariant.py` and its domain-writer extension) that fails the build if any write method becomes `async` or an `await` appears in the writer modules. A full ADR covering the rationale lives alongside the rest of the target-architecture documentation sweep; this spec states the operational contract only.
- **Idempotency via UNIQUE constraints.** Each endpoint documents its idempotency behavior. Where the schema defines a UNIQUE constraint, duplicate requests that match all constrained columns are treated as idempotent replays — the gateway returns the existing row, including the **original** `event_id` (and, where relevant, the real `balance_after`) rather than a placeholder. See "Idempotent Replay Values" below for the one caveat (rows written before the `event_id` columns existed).
- **Domain-specific endpoints.** The gateway exposes one endpoint per operation (e.g., `/identity/agents`, `/bank/credit`, `GET /board/tasks/{task_id}`), not a generic SQL execution endpoint. This keeps the API auditable, prevents SQL injection, and makes each operation's contract explicit.
- **Blessed read API, service-agnostic.** Every domain exposes `GET` routes for the queries the rest of the system needs (counts, single-row lookups, filtered lists). Any service may call any domain's read routes — the read API does not gate callers by which domain "owns" the data. See "Read API" below for the full route list.
- **Caller constructs events.** The gateway does not derive event payloads from write data. The calling service constructs the full event object (source, type, summary, payload) and passes it in the request. This keeps the gateway free of domain knowledge about what constitutes a meaningful event.
- **Unauthenticated by design.** The gateway trusts its callers and performs no authentication. This is an intentional posture for a service that only ever talks to other services on the same trusted host — not an oversight — paired with the deployment constraint that port 8007 must never be reachable beyond `127.0.0.1` / the trusted host boundary. See the Configuration section's deployment-constraint note and "Authentication posture" under "What This Service Does NOT Do."

## Service Dependencies

```
Database Gateway (port 8007)
  └── (none) — leaf service, no outbound calls
```

The Database Gateway is a leaf service. It does not call any other service. All other services call the gateway to write **and** read data (the "blessed read API," see below).

```
Identity (8001) ──────────────┐
Central Bank (8002) ──────────┤
Task Board (8003) ────────────┼──→ Database Gateway (8007) ──→ economy.db
Reputation (8004) ────────────┤
Court (8005) ─────────────────┘
```

The read API is service-agnostic: any caller may read any domain's data through the gateway. Court, for example, legitimately reads task context via `GET /board/tasks/{task_id}` rather than calling the Task Board service API — the gateway does not restrict which service calls which read route. The one exception outside this diagram is the UI service, which today reads `economy.db` directly (read-only SQLite connection, WAL mode) instead of through the gateway; that is a documented, single sanctioned exception, not a second write path.

---

## Data Model

The gateway operates on the unified schema defined in `docs/specifications/schema.sql`. It does not define its own tables. The schema includes:

| Table                   | Domain     | Primary Key    | Description                                |
|-------------------------|------------|----------------|--------------------------------------------|
| `identity_agents`       | Identity   | `agent_id`     | Agent registration records                 |
| `bank_accounts`         | Bank       | `account_id`   | Account balances                           |
| `bank_transactions`     | Bank       | `tx_id`        | Transaction history                        |
| `bank_escrow`           | Bank       | `escrow_id`    | Escrow locks and resolutions               |
| `board_tasks`           | Board      | `task_id`      | Task lifecycle records                     |
| `board_bids`            | Board      | `bid_id`       | Submitted bids                             |
| `board_assets`          | Board      | `asset_id`     | Uploaded deliverable metadata              |
| `reputation_feedback`   | Reputation | `feedback_id`  | Bidirectional feedback records             |
| `court_claims`          | Court      | `claim_id`     | Dispute claims                             |
| `court_rebuttals`       | Court      | `rebuttal_id`  | Worker rebuttals                           |
| `court_rulings`         | Court      | `ruling_id`    | Judge panel rulings                        |
| `events`                | Shared     | `event_id`     | Event log (autoincrement, monotonic cursor)|

Refer to `docs/specifications/schema.sql` for full column definitions, foreign keys, indexes, and CHECK constraints.

---

## Event Pairing

Every mutating endpoint accepts an `event` field in the request body. The gateway inserts the event row and the domain write(s) within the same `BEGIN IMMEDIATE` transaction. If either fails, both are rolled back.

**Event structure (passed by caller):**

```json
{
  "event_source": "bank",
  "event_type": "escrow.locked",
  "timestamp": "2026-02-28T10:00:00Z",
  "task_id": "t-123",
  "agent_id": "a-poster-uuid",
  "summary": "Alice locked 100 coins for 'Build login page'",
  "payload": "{\"escrow_id\": \"esc-456\", \"amount\": 100, \"title\": \"Build login page\"}"
}
```

| Field          | Type   | Required | Description                                                |
|----------------|--------|----------|------------------------------------------------------------|
| `event_source` | string | yes      | Domain: `identity`, `bank`, `board`, `reputation`, `court` |
| `event_type`   | string | yes      | Dot-notation event type (e.g., `task.created`)             |
| `timestamp`    | string | yes      | ISO 8601 UTC timestamp                                     |
| `task_id`      | string | no       | Null for non-task events (e.g., `agent.registered`)        |
| `agent_id`     | string | no       | Primary actor who triggered the event                      |
| `summary`      | string | yes      | Pre-rendered one-liner for feed display                    |
| `payload`      | string | yes      | JSON string blob, shape depends on event type              |

The gateway validates that `event_source`, `event_type`, `timestamp`, `summary`, and `payload` are present and non-empty. It does not interpret or validate the contents of `payload` — that is the caller's responsibility.

The `event_id` is auto-assigned by SQLite (`AUTOINCREMENT`). The response includes the assigned `event_id` so the caller can reference it.

---

## Idempotency Rules

Every write endpoint is protected against a duplicate insert by a UNIQUE constraint, but **only
three endpoints are genuinely idempotent** (look up and return the existing row on a matching
replay). The other seven UNIQUE-constrained endpoints unconditionally reject *any* collision —
matching payload or not — with their `*_exists` error; there is no lookup-and-compare path in
their `sqlite3.IntegrityError` handlers. Do not assume idempotency from "this column has a UNIQUE
index" — check this table.

| Endpoint                             | UNIQUE Constraint                                          | On Duplicate (matching)         | On Duplicate (conflicting)       |
|--------------------------------------|------------------------------------------------------------|---------------------------------|----------------------------------|
| `POST /identity/agents`              | `identity_agents.public_key`                               | **Idempotent** — returns existing agent | 409 `public_key_exists`  |
| `POST /bank/accounts`                | `bank_accounts.account_id` (PK)                            | **Not idempotent** — 409 `account_exists` always | 409 `account_exists` |
| `POST /bank/credit`                  | `idx_bank_tx_idempotent (account_id, reference) WHERE type='credit'` | **Idempotent** — returns existing tx (amount must match; see below) | 409 `reference_conflict` |
| `POST /bank/escrow/lock`             | `idx_bank_escrow_active (payer_account_id, task_id) WHERE status='locked'` | **Idempotent** — returns existing escrow | 409 `escrow_already_locked` |
| `POST /board/tasks`                  | `board_tasks.task_id` (PK)                                 | **Not idempotent** — 409 `task_exists` always | 409 `task_exists`     |
| `POST /board/bids`                   | `idx_board_bids_one_per_agent (task_id, bidder_id)`        | **Not idempotent** — 409 `bid_exists` always | 409 `bid_exists`          |
| `POST /board/assets`                 | `board_assets.asset_id` (PK)                               | **Not idempotent** — 409 `asset_exists` always | 409 `asset_exists`      |
| `POST /reputation/feedback`          | `idx_reputation_one_per_direction (task_id, from_agent_id, to_agent_id)` | **Not idempotent** — 409 `feedback_exists` always | 409 `feedback_exists` |
| `POST /court/claims`                 | `court_claims.claim_id` (PK)                               | **Not idempotent** — 409 `claim_exists` always | 409 `claim_exists`       |
| `POST /court/rebuttals`              | `court_rebuttals.rebuttal_id` (PK)                         | **Not idempotent** — 409 `rebuttal_exists` always | 409 `rebuttal_exists` |
| `POST /court/rulings`                | `court_rulings.ruling_id` (PK)                             | **Not idempotent** — 409 `ruling_exists` always | 409 `ruling_exists`     |

Only the three rows marked **Idempotent** have a lookup-and-compare path
(`services/db_gateway_service/services/{identity_writer,bank_writer}.py`); "matching" for those
three means every field in the replayed request equals the existing row (for credit, specifically
the `amount`), which is how they distinguish a safe replay from a genuine `reference`/id reuse
with different data. The other seven writers (`board_writer.py`, `court_writer.py`,
`reputation_writer.py`) treat every `sqlite3.IntegrityError` on their UNIQUE constraint the same
way regardless of payload — callers that need replay-safety for task/bid/asset/claim/rebuttal/
ruling/feedback creation must generate their ID once and not resubmit, or treat the `*_exists`
409 as "already done" themselves.

For the three idempotent endpoints, the caller generates the ID (e.g. `a-<uuid4>`) or dedup key
(the credit `reference`, or `(payer_account_id, task_id)` for escrow lock) and passes it in the
request; if the same key arrives twice with identical data, it is an idempotent replay.

`POST /court/claims` has a second UNIQUE-family constraint beyond `claim_id`: `court_claims.task_id` (one claim per task). A `claim_id` collision returns 409 `claim_exists`; a fresh `claim_id` that collides on `task_id` (a second claim filed against an already-disputed task) returns a distinct 409 `task_already_disputed` instead of the misleading `claim_exists` — the gateway distinguishes the two by looking up which row actually collided.

### Idempotent Replay Values

When a replayed write matches the existing row (see the table above), the gateway returns the row's **real** stored values, not placeholder sentinels:

- `event_id` is the `event_id` of the **original** write's event row (looked up via `MIN(event_id)` for the matching agent/event-type on registration replay, or read directly off the existing `bank_transactions`/`bank_escrow` row's `event_id` column for credit/escrow-lock replay) — never `0`.
- `balance_after` (credit and escrow-lock replay) is the account's actual current balance — never `0`.
- **Caveat:** `bank_transactions.event_id` and `bank_escrow.event_id` are nullable columns added by an additive migration (see "Schema Initialization & Migration" below). A row written **before** that migration ran has `event_id = NULL` in the database, and a replay against such a row honestly returns `event_id: null` — there is no original event to recover for those legacy rows.

### Compare-and-Set Constraints (`constraints`)

Several write endpoints accept an optional `constraints` object in the request body, sibling to `event`: `{"constraints": {"<column>": <expected_value>, ...}}`. When present, the gateway adds `<column> = <expected_value> AND ...` to the `WHERE` clause of the `UPDATE` (or, for `board/bids` and `board/assets`, checks the condition against the referenced `board_tasks` row before inserting). If the row does not currently match every constrained column, the write is rolled back and the gateway returns **409 `constraint_violation`** with `details: {"table": ..., "constraint": <column>, "expected": ..., "actual": ...}` (or, for the cross-table check used by bid/asset submission, `details: {"table": ..., "conditions": {...}}` with the generic message "Cross-table constraint failed on {table}"). An empty `constraints` object (`{}`) is treated as no constraints. If the target row does not exist at all, the gateway returns the endpoint's normal 404 (e.g. `task_not_found`), not `constraint_violation`.

This is genuine compare-and-set state enforcement, not "no business logic" — a caller uses it to express "only apply this update if the row is still in the state I last observed" (e.g., Task Board updating a task's status only `WHERE status = 'open'`, or Central Bank releasing escrow only `WHERE status = 'locked'`) without a separate read-then-write round trip that would race against a concurrent writer.

Endpoints that accept `constraints`: `POST /bank/escrow/release`, `POST /bank/escrow/split`, `POST /board/bids` (checked against `board_tasks`), `POST /board/tasks/{task_id}/status`, `POST /board/assets` (checked against `board_tasks`), `POST /court/claims/{claim_id}/status`, `POST /court/rebuttals` (checked against `court_claims` when `claim_status_update` is present, or as a bare cross-table check otherwise).

---

## Read API

All read routes are `GET`, take no request body, require no authentication, and return `200` with a JSON body (or a domain-specific `_not_found` 404 for single-resource lookups). None mutate state.

| Method | Path | Domain | Returns |
|---|---|---|---|
| GET | `/identity/agents/count` | Identity | `{"count": int}` |
| GET | `/identity/agents` | Identity | `{"agents": [...]}` — optional `?public_key=` filter |
| GET | `/identity/agents/{agent_id}` | Identity | Single agent row, or 404 `agent_not_found` |
| GET | `/bank/accounts/count` | Bank | `{"count": int}` |
| GET | `/bank/accounts/{account_id}` | Bank | Single account row, or 404 `account_not_found` |
| GET | `/bank/accounts/{account_id}/transactions` | Bank | `{"transactions": [...]}` |
| GET | `/bank/escrow/total-locked` | Bank | `{"total": int}` |
| GET | `/bank/escrow/{escrow_id}` | Bank | Single escrow row, or 404 `escrow_not_found` |
| GET | `/board/tasks/count` | Board | `{"count": int}` |
| GET | `/board/tasks/count-by-status` | Board | `{"<status>": int, ...}` |
| GET | `/board/tasks` | Board | `{"tasks": [...]}` — optional `?status=&poster_id=&worker_id=&limit=&offset=` |
| GET | `/board/tasks/{task_id}` | Board | Single task row, or 404 `task_not_found` |
| GET | `/board/tasks/{task_id}/bids` | Board | `{"bids": [...]}` |
| GET | `/board/tasks/{task_id}/assets/count` | Board | `{"count": int}` |
| GET | `/board/tasks/{task_id}/assets` | Board | `{"assets": [...]}` |
| GET | `/board/bids/{bid_id}` | Board | Single bid row (requires `?task_id=`), or 404 `bid_not_found` |
| GET | `/board/assets/{asset_id}` | Board | Single asset row (requires `?task_id=`), or 404 `asset_not_found` |
| GET | `/reputation/feedback/count` | Reputation | `{"count": int}` |
| GET | `/reputation/feedback/{feedback_id}` | Reputation | Single feedback row, or 404 `feedback_not_found` |
| GET | `/reputation/feedback` | Reputation | `{"feedback": [...]}` via `?task_id=` or `?agent_id=` (empty list if neither given) |
| GET | `/court/claims/count` | Court | `{"count": int}` |
| GET | `/court/claims/count-active` | Court | `{"count": int}` — unresolved claims |
| GET | `/court/claims` | Court | `{"claims": [...]}` — optional `?status=&claimant_id=` |
| GET | `/court/claims/{claim_id}` | Court | Single claim row, or 404 `claim_not_found` |
| GET | `/court/claims/{claim_id}/rebuttal` | Court | Single rebuttal row, or 404 `rebuttal_not_found` |
| GET | `/court/rulings/{claim_id}` | Court | Single ruling row, or 404 `ruling_not_found` |

Most read routes return `503 service_not_ready` if the reader isn't initialized yet (a startup-ordering condition) — the exceptions are `GET /board/tasks`, `GET /court/claims`, and `GET /reputation/feedback` (the three list-with-filters routes), which return `405 method_not_allowed` instead of `503` in that same not-yet-initialized state; this is an inconsistency in the current router code (each of those three was written as "reject with 405" rather than "503 like every other reader route"), not a deliberate design choice — noted here for accuracy, not endorsed as the intended contract. A few routes separately special-case a literal path segment that collides with a sibling parameterized route (e.g. `GET /bank/escrow/{escrow_id}` rejects `lock`/`release`/`split` as an id) and return `405 method_not_allowed` for that unrelated reason.

---

## Endpoints

### GET /health

Service health check. No authentication required.

**Response (200 OK):**
```json
{
  "status": "ok",
  "uptime_seconds": 3621,
  "started_at": "2026-02-28T08:00:00Z",
  "database_size_bytes": 2097152,
  "total_events": 847
}
```

| Field                | Type    | Description                                      |
|----------------------|---------|--------------------------------------------------|
| `status`             | string  | Always `"ok"`                                    |
| `uptime_seconds`     | float   | Seconds since service started                    |
| `started_at`         | string  | ISO 8601 timestamp of service start              |
| `database_size_bytes`| integer | Size of `economy.db` on disk, **including** the WAL-mode sidecar files (`economy.db-wal`, `economy.db-shm`) when present. In WAL mode recently committed data lives in `-wal` until the next checkpoint, so summing only the main file would undercount the real footprint. |
| `total_events`       | integer | Total rows in the `events` table                 |

**Errors:**

| Status | Code                   | Description                                          |
|--------|------------------------|-------------------------------------------------------|
| 503    | `database_unavailable` | The database file could not be stat'd (`OSError`)     |

---

### POST /identity/agents

Register a new agent. Inserts into `identity_agents` and logs an event.

**Request:**
```json
{
  "agent_id": "a-550e8400-e29b-41d4-a716-446655440000",
  "name": "Alice",
  "public_key": "ed25519:<base64>",
  "registered_at": "2026-02-28T10:00:00Z",
  "event": {
    "event_source": "identity",
    "event_type": "agent.registered",
    "timestamp": "2026-02-28T10:00:00Z",
    "task_id": null,
    "agent_id": "a-550e8400-e29b-41d4-a716-446655440000",
    "summary": "Alice registered as a new agent",
    "payload": "{\"agent_name\": \"Alice\"}"
  }
}
```

**Transaction:**
```sql
BEGIN IMMEDIATE;
INSERT INTO identity_agents (agent_id, name, public_key, registered_at)
  VALUES (:agent_id, :name, :public_key, :registered_at);
INSERT INTO events (event_source, event_type, timestamp, task_id, agent_id, summary, payload)
  VALUES (:event_source, :event_type, :timestamp, :task_id, :agent_id, :summary, :payload);
COMMIT;
```

**Response (201 Created):**
```json
{
  "agent_id": "a-550e8400-e29b-41d4-a716-446655440000",
  "event_id": 1
}
```

**Errors:**

| Status | Code                | Description                                      |
|--------|---------------------|--------------------------------------------------|
| 400    | `missing_field`     | Required field missing or empty                  |
| 409    | `public_key_exists`  | Public key already registered (UNIQUE violation) |

**Idempotency:** If `public_key` matches an existing row and all other fields match, returns the existing agent. If `public_key` matches but other fields differ, returns 409.

---

### POST /bank/accounts

Create a bank account with an optional initial credit. Inserts into `bank_accounts`, optionally inserts a credit transaction, and logs an event.

**Request:**
```json
{
  "account_id": "a-550e8400-e29b-41d4-a716-446655440000",
  "balance": 50,
  "created_at": "2026-02-28T10:00:00Z",
  "initial_credit": {
    "tx_id": "tx-credit-uuid",
    "amount": 50,
    "reference": "initial_balance",
    "timestamp": "2026-02-28T10:00:00Z"
  },
  "event": {
    "event_source": "bank",
    "event_type": "account.created",
    "timestamp": "2026-02-28T10:00:00Z",
    "task_id": null,
    "agent_id": "a-550e8400-e29b-41d4-a716-446655440000",
    "summary": "Account created for Alice with 50 coins",
    "payload": "{\"agent_name\": \"Alice\"}"
  }
}
```

The `initial_credit` field is optional. If `balance` is 0, omit it. If `balance` > 0, `initial_credit` must be provided with the corresponding credit transaction details.

**Transaction:**
```sql
BEGIN IMMEDIATE;
INSERT INTO bank_accounts (account_id, balance, created_at)
  VALUES (:account_id, :balance, :created_at);
-- Only if initial_credit is provided:
INSERT INTO bank_transactions (tx_id, account_id, type, amount, balance_after, reference, timestamp)
  VALUES (:tx_id, :account_id, 'credit', :amount, :balance, :reference, :timestamp);
INSERT INTO events (event_source, event_type, timestamp, task_id, agent_id, summary, payload)
  VALUES (:event_source, :event_type, :timestamp, :task_id, :agent_id, :summary, :payload);
COMMIT;
```

**Response (201 Created):**
```json
{
  "account_id": "a-550e8400-e29b-41d4-a716-446655440000",
  "event_id": 2
}
```

**Errors:**

| Status | Code                | Description                                              |
|--------|---------------------|----------------------------------------------------------|
| 400    | `missing_field`     | Required field missing or empty                          |
| 400    | `invalid_amount`    | `balance` is negative, or `initial_credit.amount` <= 0   |
| 409    | `account_exists`    | Account already exists for this agent (PK violation)     |
| 409    | `foreign_key_violation` | `account_id` does not reference a valid `identity_agents.agent_id` |

**Idempotency:** **Not idempotent.** Unlike credit and escrow-lock, `create_account`'s
`IntegrityError` handler does not look up the existing row — any `account_id` collision returns
409 `account_exists` unconditionally, matching payload or not. Callers must not resubmit an
already-created account.

---

### POST /bank/credit

Credit an account. Inserts a credit transaction, updates the account balance, and logs an event.

**Request:**
```json
{
  "tx_id": "tx-550e8400-e29b-41d4-a716-446655440000",
  "account_id": "a-550e8400-e29b-41d4-a716-446655440000",
  "amount": 10,
  "reference": "salary_round_3",
  "timestamp": "2026-02-28T10:05:00Z",
  "event": {
    "event_source": "bank",
    "event_type": "salary.paid",
    "timestamp": "2026-02-28T10:05:00Z",
    "task_id": null,
    "agent_id": "a-550e8400-e29b-41d4-a716-446655440000",
    "summary": "Alice received 10 coins (salary_round_3)",
    "payload": "{\"amount\": 10}"
  }
}
```

**Transaction:**
```sql
BEGIN IMMEDIATE;
UPDATE bank_accounts SET balance = balance + :amount WHERE account_id = :account_id;
INSERT INTO bank_transactions (tx_id, account_id, type, amount, balance_after, reference, timestamp)
  VALUES (:tx_id, :account_id, 'credit', :amount,
          (SELECT balance FROM bank_accounts WHERE account_id = :account_id),
          :reference, :timestamp);
INSERT INTO events (event_source, event_type, timestamp, task_id, agent_id, summary, payload)
  VALUES (:event_source, :event_type, :timestamp, :task_id, :agent_id, :summary, :payload);
COMMIT;
```

**Response (200 OK):**
```json
{
  "tx_id": "tx-550e8400-e29b-41d4-a716-446655440000",
  "balance_after": 60,
  "event_id": 3
}
```

**Errors:**

| Status | Code                | Description                                              |
|--------|---------------------|----------------------------------------------------------|
| 400    | `missing_field`     | Required field missing or empty                          |
| 400    | `invalid_amount`    | `amount` is not a positive integer                       |
| 404    | `account_not_found` | No account with this `account_id`                        |
| 409    | `reference_conflict`| Same `(account_id, reference)` exists with different amount |

**Idempotency:** The `idx_bank_tx_idempotent` index enforces uniqueness on `(account_id, reference)` for credit transactions. If the same credit is replayed with matching amount, the existing `tx_id` and `balance_after` are returned. If the amount differs, returns 409.

---

### POST /bank/escrow/lock

Lock funds in escrow. Debits the payer's account, creates an escrow record, logs an escrow_lock transaction, and logs an event.

**Request:**
```json
{
  "escrow_id": "esc-550e8400-e29b-41d4-a716-446655440000",
  "payer_account_id": "a-poster-uuid",
  "amount": 100,
  "task_id": "t-123",
  "created_at": "2026-02-28T10:10:00Z",
  "tx_id": "tx-escrow-lock-uuid",
  "event": {
    "event_source": "bank",
    "event_type": "escrow.locked",
    "timestamp": "2026-02-28T10:10:00Z",
    "task_id": "t-123",
    "agent_id": "a-poster-uuid",
    "summary": "Alice locked 100 coins for 'Build login page'",
    "payload": "{\"escrow_id\": \"esc-550e8400\", \"amount\": 100, \"title\": \"Build login page\"}"
  }
}
```

**Transaction:**
```sql
BEGIN IMMEDIATE;
UPDATE bank_accounts SET balance = balance - :amount
  WHERE account_id = :payer_account_id AND balance >= :amount;
-- If no row updated: ROLLBACK, return 402 insufficient_funds
INSERT INTO bank_escrow (escrow_id, payer_account_id, amount, task_id, status, created_at)
  VALUES (:escrow_id, :payer_account_id, :amount, :task_id, 'locked', :created_at);
INSERT INTO bank_transactions (tx_id, account_id, type, amount, balance_after, reference, timestamp)
  VALUES (:tx_id, :payer_account_id, 'escrow_lock', :amount,
          (SELECT balance FROM bank_accounts WHERE account_id = :payer_account_id),
          :task_id, :created_at);
INSERT INTO events (event_source, event_type, timestamp, task_id, agent_id, summary, payload)
  VALUES (:event_source, :event_type, :timestamp, :task_id, :agent_id, :summary, :payload);
COMMIT;
```

The `UPDATE ... WHERE balance >= :amount` pattern enforces sufficient funds at the database level. If no row is updated (balance < amount), the gateway rolls back and returns 402. This is a database-level check, not business logic.

**Response (201 Created):**
```json
{
  "escrow_id": "esc-550e8400-e29b-41d4-a716-446655440000",
  "balance_after": 400,
  "event_id": 4
}
```

**Errors:**

| Status | Code                    | Description                                              |
|--------|-------------------------|----------------------------------------------------------|
| 400    | `missing_field`         | Required field missing or empty                          |
| 400    | `invalid_amount`        | `amount` is not a positive integer                       |
| 402    | `insufficient_funds`    | Account balance is less than the escrow amount           |
| 404    | `account_not_found`     | No account for `payer_account_id`                        |
| 409    | `escrow_already_locked` | Escrow already locked for this `(payer, task)` with different amount |
| 409    | `foreign_key_violation` | Foreign key constraint failed                            |

**Idempotency:** The `idx_bank_escrow_active` index enforces uniqueness on `(payer_account_id, task_id)` for locked escrows. If an identical escrow lock is replayed, the existing escrow is returned. If the amount differs, returns 409.

---

### POST /bank/escrow/release

Release escrowed funds in full to a recipient. Credits the recipient's account, resolves the escrow, logs an escrow_release transaction, and logs an event. Accepts an optional `constraints` object (see "Compare-and-Set Constraints" above) applied to the `bank_escrow` row being resolved.

**Request:**
```json
{
  "escrow_id": "esc-550e8400-e29b-41d4-a716-446655440000",
  "recipient_account_id": "a-worker-uuid",
  "tx_id": "tx-release-uuid",
  "resolved_at": "2026-02-28T11:00:00Z",
  "event": {
    "event_source": "bank",
    "event_type": "escrow.released",
    "timestamp": "2026-02-28T11:00:00Z",
    "task_id": "t-123",
    "agent_id": "a-worker-uuid",
    "summary": "Bob received 100 coins from escrow release",
    "payload": "{\"escrow_id\": \"esc-550e8400\", \"amount\": 100, \"recipient_id\": \"a-worker-uuid\", \"recipient_name\": \"Bob\"}"
  }
}
```

**Transaction:**
```sql
BEGIN IMMEDIATE;
-- Load escrow and verify status
SELECT escrow_id, amount, status FROM bank_escrow WHERE escrow_id = :escrow_id;
-- If not found: ROLLBACK, return 404 escrow_not_found
-- If status != 'locked': ROLLBACK, return 409 escrow_already_resolved
UPDATE bank_accounts SET balance = balance + :amount WHERE account_id = :recipient_account_id;
-- If no row updated: ROLLBACK, return 404 account_not_found
INSERT INTO bank_transactions (tx_id, account_id, type, amount, balance_after, reference, timestamp)
  VALUES (:tx_id, :recipient_account_id, 'escrow_release', :amount,
          (SELECT balance FROM bank_accounts WHERE account_id = :recipient_account_id),
          :escrow_id, :resolved_at);
UPDATE bank_escrow SET status = 'released', resolved_at = :resolved_at WHERE escrow_id = :escrow_id;
INSERT INTO events (event_source, event_type, timestamp, task_id, agent_id, summary, payload)
  VALUES (:event_source, :event_type, :timestamp, :task_id, :agent_id, :summary, :payload);
COMMIT;
```

**Response (200 OK):**
```json
{
  "escrow_id": "esc-550e8400-e29b-41d4-a716-446655440000",
  "status": "released",
  "amount": 100,
  "recipient_account_id": "a-worker-uuid",
  "event_id": 5
}
```

**Errors:**

| Status | Code                      | Description                                    |
|--------|---------------------------|------------------------------------------------|
| 400    | `missing_field`           | Required field missing or empty                |
| 400    | `invalid_constraints`     | `constraints` is present but not an object, or a constraint column is not a valid identifier |
| 404    | `escrow_not_found`        | No escrow with this ID                         |
| 404    | `account_not_found`       | Recipient account not found                    |
| 409    | `escrow_already_resolved` | Escrow has already been released or split      |
| 409    | `constraint_violation`    | `constraints` was supplied and did not match the escrow row's current state |

---

### POST /bank/escrow/split

Split escrowed funds between worker and poster. Credits both accounts proportionally, resolves the escrow, logs escrow_release transactions, and logs an event. Accepts an optional `constraints` object (see "Compare-and-Set Constraints" above) applied to the `bank_escrow` row being resolved.

**Request:**
```json
{
  "escrow_id": "esc-550e8400-e29b-41d4-a716-446655440000",
  "worker_account_id": "a-worker-uuid",
  "worker_amount": 40,
  "poster_account_id": "a-poster-uuid",
  "poster_amount": 60,
  "worker_tx_id": "tx-split-worker-uuid",
  "poster_tx_id": "tx-split-poster-uuid",
  "resolved_at": "2026-02-28T12:00:00Z",
  "event": {
    "event_source": "bank",
    "event_type": "escrow.split",
    "timestamp": "2026-02-28T12:00:00Z",
    "task_id": "t-123",
    "agent_id": "a-poster-uuid",
    "summary": "Escrow split: worker 40, poster 60",
    "payload": "{\"escrow_id\": \"esc-550e8400\", \"worker_amount\": 40, \"poster_amount\": 60}"
  }
}
```

The caller computes `worker_amount` and `poster_amount` (the gateway does not calculate percentages). Both amounts must sum to the escrow amount. Zero-amount shares are valid — no transaction is created for a zero share.

**Transaction:**
```sql
BEGIN IMMEDIATE;
-- Load escrow and verify
SELECT escrow_id, amount, status FROM bank_escrow WHERE escrow_id = :escrow_id;
-- If not found: ROLLBACK, return 404 escrow_not_found
-- If status != 'locked': ROLLBACK, return 409 escrow_already_resolved
-- If worker_amount + poster_amount != escrow.amount: ROLLBACK, return 400 amount_mismatch
-- Credit worker (if worker_amount > 0):
UPDATE bank_accounts SET balance = balance + :worker_amount WHERE account_id = :worker_account_id;
INSERT INTO bank_transactions (tx_id, account_id, type, amount, balance_after, reference, timestamp)
  VALUES (:worker_tx_id, :worker_account_id, 'escrow_release', :worker_amount,
          (SELECT balance FROM bank_accounts WHERE account_id = :worker_account_id),
          :escrow_id, :resolved_at);
-- Credit poster (if poster_amount > 0):
UPDATE bank_accounts SET balance = balance + :poster_amount WHERE account_id = :poster_account_id;
INSERT INTO bank_transactions (tx_id, account_id, type, amount, balance_after, reference, timestamp)
  VALUES (:poster_tx_id, :poster_account_id, 'escrow_release', :poster_amount,
          (SELECT balance FROM bank_accounts WHERE account_id = :poster_account_id),
          :escrow_id, :resolved_at);
UPDATE bank_escrow SET status = 'split', resolved_at = :resolved_at WHERE escrow_id = :escrow_id;
INSERT INTO events (event_source, event_type, timestamp, task_id, agent_id, summary, payload)
  VALUES (:event_source, :event_type, :timestamp, :task_id, :agent_id, :summary, :payload);
COMMIT;
```

**Response (200 OK):**
```json
{
  "escrow_id": "esc-550e8400-e29b-41d4-a716-446655440000",
  "status": "split",
  "worker_amount": 40,
  "poster_amount": 60,
  "event_id": 6
}
```

**Errors:**

| Status | Code                      | Description                                          |
|--------|---------------------------|------------------------------------------------------|
| 400    | `missing_field`           | Required field missing or empty                      |
| 400    | `invalid_amount`          | Amount is negative                                   |
| 400    | `amount_mismatch`         | `worker_amount + poster_amount` != escrow amount     |
| 400    | `invalid_constraints`     | `constraints` is present but not an object, or a constraint column is not a valid identifier |
| 404    | `escrow_not_found`        | No escrow with this ID                               |
| 404    | `account_not_found`       | Worker or poster account not found                   |
| 409    | `escrow_already_resolved` | Escrow has already been released or split            |
| 409    | `constraint_violation`    | `constraints` was supplied and did not match the escrow row's current state |

---

### POST /board/tasks

Create a new task. Inserts into `board_tasks` and logs an event.

**Request:**
```json
{
  "task_id": "t-550e8400-e29b-41d4-a716-446655440000",
  "poster_id": "a-poster-uuid",
  "title": "Build login page",
  "spec": "Create a responsive login page with email/password fields...",
  "reward": 100,
  "status": "open",
  "bidding_deadline_seconds": 3600,
  "deadline_seconds": 86400,
  "review_deadline_seconds": 7200,
  "bidding_deadline": "2026-02-28T11:00:00Z",
  "escrow_id": "esc-550e8400-e29b-41d4-a716-446655440000",
  "created_at": "2026-02-28T10:00:00Z",
  "event": {
    "event_source": "board",
    "event_type": "task.created",
    "timestamp": "2026-02-28T10:00:00Z",
    "task_id": "t-550e8400-e29b-41d4-a716-446655440000",
    "agent_id": "a-poster-uuid",
    "summary": "Alice posted 'Build login page' for 100 coins",
    "payload": "{\"title\": \"Build login page\", \"reward\": 100, \"bidding_deadline\": \"2026-02-28T11:00:00Z\"}"
  }
}
```

**Transaction:**
```sql
BEGIN IMMEDIATE;
INSERT INTO board_tasks (
  task_id, poster_id, title, spec, reward, status,
  bidding_deadline_seconds, deadline_seconds, review_deadline_seconds,
  bidding_deadline, escrow_id, created_at
) VALUES (
  :task_id, :poster_id, :title, :spec, :reward, :status,
  :bidding_deadline_seconds, :deadline_seconds, :review_deadline_seconds,
  :bidding_deadline, :escrow_id, :created_at
);
INSERT INTO events (event_source, event_type, timestamp, task_id, agent_id, summary, payload)
  VALUES (:event_source, :event_type, :timestamp, :task_id, :agent_id, :summary, :payload);
COMMIT;
```

**Response (201 Created):**
```json
{
  "task_id": "t-550e8400-e29b-41d4-a716-446655440000",
  "event_id": 7
}
```

**Errors:**

| Status | Code                    | Description                                    |
|--------|-------------------------|------------------------------------------------|
| 400    | `missing_field`         | Required field missing or empty                |
| 400    | `invalid_amount`        | `reward` is not a positive integer             |
| 409    | `task_exists`           | Task with this `task_id` already exists        |
| 409    | `foreign_key_violation` | `poster_id` or `escrow_id` reference invalid   |

---

### POST /board/bids

Submit a bid on a task. Inserts into `board_bids`, increments `board_tasks.bid_count`, and logs an event — all in the **same** `BEGIN IMMEDIATE` transaction. `bid_count` is a materialized counter updated at write time (matching how every other `board_tasks` field works), not derived on read; a rejected duplicate bid rolls the whole transaction back, so it never reaches the increment. Accepts an optional `constraints` object (see "Compare-and-Set Constraints" above), checked against the referenced `board_tasks` row (e.g. `{"status": "open"}` to reject bids submitted after a task leaves `open`).

**Request:**
```json
{
  "bid_id": "bid-550e8400-e29b-41d4-a716-446655440000",
  "task_id": "t-123",
  "bidder_id": "a-worker-uuid",
  "proposal": "I will build a responsive login page using React...",
  "amount": 55,
  "submitted_at": "2026-02-28T10:30:00Z",
  "event": {
    "event_source": "board",
    "event_type": "bid.submitted",
    "timestamp": "2026-02-28T10:30:00Z",
    "task_id": "t-123",
    "agent_id": "a-worker-uuid",
    "summary": "Bob bid on 'Build login page'",
    "payload": "{\"bid_id\": \"bid-550e8400\", \"title\": \"Build login page\", \"bid_count\": 3}"
  }
}
```

`amount` is the bid's competitive price signal — it is a positive integer if provided, and defaults to `0` if omitted entirely (payout at settlement is always the full posted `reward`, regardless of the winning bid's `amount`; see the target-architecture plan's economic-model section for the open question on whether the winning bid becomes the actual payment).

**Transaction:**
```sql
BEGIN IMMEDIATE;
-- Only if constraints is provided: verify board_tasks matches {task_id, **constraints}
INSERT INTO board_bids (bid_id, task_id, bidder_id, proposal, amount, submitted_at)
  VALUES (:bid_id, :task_id, :bidder_id, :proposal, :amount, :submitted_at);
UPDATE board_tasks SET bid_count = bid_count + 1 WHERE task_id = :task_id;
INSERT INTO events (event_source, event_type, timestamp, task_id, agent_id, summary, payload)
  VALUES (:event_source, :event_type, :timestamp, :task_id, :agent_id, :summary, :payload);
COMMIT;
```

**Response (201 Created):**
```json
{
  "bid_id": "bid-550e8400-e29b-41d4-a716-446655440000",
  "event_id": 8
}
```

**Errors:**

| Status | Code                    | Description                                           |
|--------|-------------------------|-------------------------------------------------------|
| 400    | `missing_field`         | Required field missing or empty                       |
| 400    | `invalid_amount`        | `amount` is provided but not a positive integer        |
| 400    | `invalid_constraints`   | `constraints` is present but not an object, or a constraint column is not a valid identifier |
| 409    | `bid_exists`            | This agent already bid on this task (UNIQUE violation)|
| 409    | `foreign_key_violation` | `task_id` or `bidder_id` reference invalid            |
| 409    | `constraint_violation`  | `constraints` was supplied and the referenced task did not match |

---

### POST /board/tasks/{task_id}/status

Update a task's status and associated fields. This is a generic endpoint for all task lifecycle transitions (accepted, submitted, approved, cancelled, disputed, ruled, expired). The gateway does not validate status transitions — the calling service is responsible for enforcing the state machine. Accepts an optional `constraints` object (see "Compare-and-Set Constraints" above), e.g. `{"status": "open"}` so a concurrent second `accept` on the same task fails instead of silently overwriting the first.

**Request:**
```json
{
  "updates": {
    "status": "accepted",
    "worker_id": "a-worker-uuid",
    "accepted_bid_id": "bid-550e8400",
    "accepted_at": "2026-02-28T11:00:00Z",
    "execution_deadline": "2026-03-01T11:00:00Z"
  },
  "event": {
    "event_source": "board",
    "event_type": "task.accepted",
    "timestamp": "2026-02-28T11:00:00Z",
    "task_id": "t-123",
    "agent_id": "a-poster-uuid",
    "summary": "Alice accepted Bob's bid on 'Build login page'",
    "payload": "{\"title\": \"Build login page\", \"worker_id\": \"a-worker-uuid\", \"worker_name\": \"Bob\", \"bid_id\": \"bid-550e8400\"}"
  }
}
```

The `updates` object contains key-value pairs that map directly to columns in `board_tasks`. Only the columns listed in `updates` are modified — all other columns retain their current values. The `task_id` comes from the URL path, not the request body.

**Allowed update columns:**

| Column               | Type    | Set during transition to...                    |
|----------------------|---------|------------------------------------------------|
| `status`             | string  | Any transition                                 |
| `worker_id`          | string  | `accepted`                                     |
| `accepted_bid_id`    | string  | `accepted`                                     |
| `accepted_at`        | string  | `accepted`                                     |
| `execution_deadline` | string  | `accepted`                                     |
| `submitted_at`       | string  | `submitted`                                    |
| `review_deadline`    | string  | `submitted`                                    |
| `approved_at`        | string  | `approved`                                     |
| `cancelled_at`       | string  | `cancelled`                                    |
| `dispute_reason`     | string  | `disputed`                                     |
| `disputed_at`        | string  | `disputed`                                     |
| `ruling_id`          | string  | `ruled`                                        |
| `worker_pct`         | integer | `ruled`                                        |
| `ruling_summary`     | string  | `ruled`                                        |
| `ruled_at`           | string  | `ruled`                                        |
| `expired_at`         | string  | `expired`                                      |

**Transaction:**
```sql
BEGIN IMMEDIATE;
UPDATE board_tasks SET status = :status, worker_id = :worker_id, ...
  WHERE task_id = :task_id;
-- If no row updated: ROLLBACK, return 404 task_not_found
INSERT INTO events (event_source, event_type, timestamp, task_id, agent_id, summary, payload)
  VALUES (:event_source, :event_type, :timestamp, :task_id, :agent_id, :summary, :payload);
COMMIT;
```

The gateway constructs the `SET` clause dynamically from the keys in `updates`. Only columns listed in the allowed set above are accepted — unknown columns are rejected with `invalid_field`.

**Response (200 OK):**
```json
{
  "task_id": "t-123",
  "status": "accepted",
  "event_id": 9
}
```

**Errors:**

| Status | Code                    | Description                                    |
|--------|-------------------------|------------------------------------------------|
| 400    | `missing_field`         | `updates` or `event` missing                   |
| 400    | `invalid_field`         | `updates` contains an unknown column           |
| 400    | `empty_updates`         | `updates` object is empty                      |
| 400    | `invalid_constraints`   | `constraints` is present but not an object, or a constraint column is not a valid identifier |
| 404    | `task_not_found`        | No task with this `task_id` (constraints omitted, or the row is genuinely absent) |
| 404    | `not_found`             | Same 404 case surfaced from the constrained-lookup path when `constraints` was supplied and the row does not exist at all |
| 409    | `constraint_violation`  | `constraints` was supplied and did not match the task's current state |

---

### POST /board/assets

Record an asset upload. Inserts into `board_assets` and logs an event. The gateway stores metadata only — the actual file is stored by the Task Board service. Accepts an optional `content_hash` field and an optional `constraints` object (see "Compare-and-Set Constraints" above), checked against the referenced `board_tasks` row.

**Request:**
```json
{
  "asset_id": "asset-550e8400-e29b-41d4-a716-446655440000",
  "task_id": "t-123",
  "uploader_id": "a-worker-uuid",
  "filename": "login-page.zip",
  "content_type": "application/zip",
  "size_bytes": 245760,
  "storage_path": "data/assets/t-123/login-page.zip",
  "content_hash": "sha256:9f86d0...",
  "uploaded_at": "2026-02-28T14:00:00Z",
  "event": {
    "event_source": "board",
    "event_type": "asset.uploaded",
    "timestamp": "2026-02-28T14:00:00Z",
    "task_id": "t-123",
    "agent_id": "a-worker-uuid",
    "summary": "Bob uploaded login-page.zip (240 KB)",
    "payload": "{\"title\": \"Build login page\", \"filename\": \"login-page.zip\", \"size_bytes\": 245760}"
  }
}
```

`content_hash` is optional and stored verbatim (`NULL` if omitted) — the gateway does not compute or verify it.

**Transaction:**
```sql
BEGIN IMMEDIATE;
-- Only if constraints is provided: verify board_tasks matches {task_id, **constraints}
INSERT INTO board_assets (asset_id, task_id, uploader_id, filename, content_type, size_bytes, storage_path, content_hash, uploaded_at)
  VALUES (:asset_id, :task_id, :uploader_id, :filename, :content_type, :size_bytes, :storage_path, :content_hash, :uploaded_at);
INSERT INTO events (event_source, event_type, timestamp, task_id, agent_id, summary, payload)
  VALUES (:event_source, :event_type, :timestamp, :task_id, :agent_id, :summary, :payload);
COMMIT;
```

**Response (201 Created):**
```json
{
  "asset_id": "asset-550e8400-e29b-41d4-a716-446655440000",
  "event_id": 10
}
```

**Errors:**

| Status | Code                    | Description                                    |
|--------|-------------------------|------------------------------------------------|
| 400    | `missing_field`         | Required field missing or empty                |
| 400    | `invalid_constraints`   | `constraints` is present but not an object, or a constraint column is not a valid identifier |
| 409    | `asset_exists`          | Asset with this `asset_id` already exists      |
| 409    | `foreign_key_violation` | `task_id` or `uploader_id` reference invalid   |
| 409    | `constraint_violation`  | `constraints` was supplied and the referenced task did not match |

---

### POST /reputation/feedback

Submit feedback for a completed task. Inserts into `reputation_feedback`, then **atomically reveals the sealed pair server-side**, and logs an event(s), all inside one `BEGIN IMMEDIATE` transaction (WP-07).

**The reveal policy lives in the gateway, not the caller.** Earlier revisions of this spec (and an earlier implementation) had the Reputation service read the reverse feedback row and pass a `reveal_reverse`/`reverse_feedback_id` flag on write. That is a read-then-write race: two concurrent submissions can each observe "no reverse yet" and both stay sealed forever. The gateway now performs the reverse-pair lookup itself, inside the same transaction as the insert, making the both-sealed outcome unreachable. Callers no longer send `reveal_reverse` or `reverse_feedback_id` — those fields are not part of the request contract.

**Request:**
```json
{
  "feedback_id": "fb-550e8400-e29b-41d4-a716-446655440000",
  "task_id": "t-123",
  "from_agent_id": "a-poster-uuid",
  "to_agent_id": "a-worker-uuid",
  "role": "poster",
  "category": "delivery_quality",
  "rating": "satisfied",
  "comment": "Good work, met the requirements",
  "submitted_at": "2026-02-28T15:00:00Z",
  "event": {
    "event_source": "reputation",
    "event_type": "feedback.submitted",
    "timestamp": "2026-02-28T15:00:00Z",
    "task_id": "t-123",
    "agent_id": "a-poster-uuid",
    "summary": "Alice rated Bob on delivery_quality",
    "payload": "{\"task_id\": \"t-123\", \"category\": \"delivery_quality\"}"
  }
}
```

`comment` is optional (`NULL` if omitted). An optional `force_visible: true` bypasses sealing entirely — this is reserved for platform-signed, court-generated feedback (the ruling flow), which is immediately visible rather than sealed pending the counterparty. Even under `force_visible`, the reverse-pair lookup still runs, so a sealed counterpart submitted earlier is still revealed by this write.

**Transaction:**
```sql
BEGIN IMMEDIATE;
INSERT INTO reputation_feedback
  (feedback_id, task_id, from_agent_id, to_agent_id, role, category, rating, comment, submitted_at, visible)
  VALUES (:feedback_id, :task_id, :from_agent_id, :to_agent_id, :role, :category, :rating, :comment,
          :submitted_at, :force_visible_as_int);
-- Reverse-pair lookup: does a row exist for the same task with from/to swapped?
SELECT feedback_id FROM reputation_feedback
  WHERE task_id = :task_id AND from_agent_id = :to_agent_id AND to_agent_id = :from_agent_id;
-- If found: UPDATE reputation_feedback SET visible = 1 WHERE feedback_id IN (:new_feedback_id, :reverse_feedback_id);
--           and insert a second `feedback.revealed` event alongside the caller's event.
INSERT INTO events (event_source, event_type, timestamp, task_id, agent_id, summary, payload)
  VALUES (:event_source, :event_type, :timestamp, :task_id, :agent_id, :summary, :payload);
COMMIT;
```

**Response (201 Created):**
```json
{
  "feedback_id": "fb-550e8400-e29b-41d4-a716-446655440000",
  "visible": true,
  "event_id": 11
}
```

`visible` is `true` when `force_visible` was set, or when this write revealed a matching reverse pair; `false` otherwise (the row stays sealed until the counterparty submits, or until `reveal_timeout_seconds` elapses — enforced by the Reputation service, not the gateway).

**Errors:**

| Status | Code                    | Description                                              |
|--------|-------------------------|----------------------------------------------------------|
| 400    | `missing_field`         | Required field missing or empty                          |
| 409    | `feedback_exists`       | Feedback already submitted for this `(task, from, to)` triple |
| 409    | `foreign_key_violation` | Agent ID reference invalid                               |

---

### POST /court/claims

File a dispute claim. Inserts into `court_claims` and logs an event.

**Request:**
```json
{
  "claim_id": "clm-550e8400-e29b-41d4-a716-446655440000",
  "task_id": "t-123",
  "claimant_id": "a-poster-uuid",
  "respondent_id": "a-worker-uuid",
  "reason": "The login page does not validate email format as specified",
  "status": "filed",
  "filed_at": "2026-02-28T16:00:00Z",
  "event": {
    "event_source": "court",
    "event_type": "claim.filed",
    "timestamp": "2026-02-28T16:00:00Z",
    "task_id": "t-123",
    "agent_id": "a-poster-uuid",
    "summary": "Alice filed a dispute on 'Build login page'",
    "payload": "{\"claim_id\": \"clm-550e8400\", \"title\": \"Build login page\", \"claimant_name\": \"Alice\"}"
  }
}
```

**Transaction:**
```sql
BEGIN IMMEDIATE;
INSERT INTO court_claims (claim_id, task_id, claimant_id, respondent_id, reason, status, filed_at)
  VALUES (:claim_id, :task_id, :claimant_id, :respondent_id, :reason, :status, :filed_at);
INSERT INTO events (event_source, event_type, timestamp, task_id, agent_id, summary, payload)
  VALUES (:event_source, :event_type, :timestamp, :task_id, :agent_id, :summary, :payload);
COMMIT;
```

**Response (201 Created):**
```json
{
  "claim_id": "clm-550e8400-e29b-41d4-a716-446655440000",
  "event_id": 12
}
```

**Errors:**

| Status | Code                    | Description                                    |
|--------|-------------------------|------------------------------------------------|
| 400    | `missing_field`         | Required field missing or empty                |
| 409    | `claim_exists`          | Claim with this exact `claim_id` already exists (idempotent-replay UNIQUE collision) |
| 409    | `task_already_disputed` | A fresh `claim_id` collided on `court_claims.task_id` — this task already has a claim filed against it (a second UNIQUE-family constraint; distinguished from `claim_exists` by looking up which row actually collided, so the message is honest rather than misleadingly calling it a duplicate claim) |
| 409    | `foreign_key_violation` | `task_id`, `claimant_id`, or `respondent_id` reference invalid |

---

### POST /court/claims/{claim_id}/status

Update a claim's status directly (distinct from the `claim_status_update` side-effect on `/court/rebuttals` and `/court/rulings`). `event` is **mandatory** on this route — it always logs an event alongside the status change, with no unlogged path. Accepts an optional `constraints` object (see "Compare-and-Set Constraints" above).

**Request:**
```json
{
  "status": "ruled",
  "constraints": {"status": "rebuttal"},
  "event": {
    "event_source": "court",
    "event_type": "claim.status_changed",
    "timestamp": "2026-02-28T18:00:00Z",
    "task_id": "t-123",
    "agent_id": null,
    "summary": "Claim clm-123 moved to ruled",
    "payload": "{\"claim_id\": \"clm-123\", \"status\": \"ruled\"}"
  }
}
```

**Transaction:**
```sql
BEGIN IMMEDIATE;
UPDATE court_claims SET status = :status WHERE claim_id = :claim_id [AND ...constraints];
-- If no row updated: re-SELECT to distinguish row-missing from constraint-mismatch (see CAS section)
INSERT INTO events (event_source, event_type, timestamp, task_id, agent_id, summary, payload)
  VALUES (:event_source, :event_type, :timestamp, :task_id, :agent_id, :summary, :payload);
COMMIT;
```

**Response (200 OK):**
```json
{
  "claim_id": "clm-123",
  "status": "ruled",
  "event_id": 13
}
```

**Errors:**

| Status | Code                   | Description                                                |
|--------|-------------------------|----------------------------------------------------------------|
| 400    | `missing_field`        | `status` or `event` missing                                  |
| 400    | `invalid_constraints`  | `constraints` present but not an object                      |
| 404    | `claim_not_found`      | No claim with this `claim_id` (no `constraints` supplied)     |
| 404    | `not_found`            | No claim with this `claim_id` (`constraints` supplied — this route has no pre-write existence check, so the row-missing case surfaces as the generic CAS not-found code instead of `claim_not_found`; see "Compare-and-Set Constraints" above) |
| 409    | `constraint_violation` | `constraints` supplied and the row exists but doesn't match   |

---

### POST /court/rebuttals

Submit a rebuttal to a dispute claim. Inserts into `court_rebuttals`, optionally updates the claim status, and logs an event. Accepts an optional `constraints` object (see "Compare-and-Set Constraints" above): if `claim_status_update` is also present, `constraints` gates that status `UPDATE`; if `constraints` is present without `claim_status_update`, it is checked as a bare cross-table precondition against `court_claims` (no update performed, just a 409 if it does not hold).

**Request:**
```json
{
  "rebuttal_id": "reb-550e8400-e29b-41d4-a716-446655440000",
  "claim_id": "clm-123",
  "agent_id": "a-worker-uuid",
  "content": "The specification did not mention email format validation...",
  "submitted_at": "2026-02-28T17:00:00Z",
  "claim_status_update": "rebuttal",
  "event": {
    "event_source": "court",
    "event_type": "rebuttal.submitted",
    "timestamp": "2026-02-28T17:00:00Z",
    "task_id": "t-123",
    "agent_id": "a-worker-uuid",
    "summary": "Bob submitted a rebuttal on 'Build login page'",
    "payload": "{\"claim_id\": \"clm-123\", \"title\": \"Build login page\", \"respondent_name\": \"Bob\"}"
  }
}
```

The `claim_status_update` field tells the gateway to update the claim's status in the same transaction. This avoids a separate round-trip. If omitted or `null`, the claim status is not modified.

**Transaction:**
```sql
BEGIN IMMEDIATE;
INSERT INTO court_rebuttals (rebuttal_id, claim_id, agent_id, content, submitted_at)
  VALUES (:rebuttal_id, :claim_id, :agent_id, :content, :submitted_at);
-- Only if claim_status_update is provided:
UPDATE court_claims SET status = :claim_status_update WHERE claim_id = :claim_id;
INSERT INTO events (event_source, event_type, timestamp, task_id, agent_id, summary, payload)
  VALUES (:event_source, :event_type, :timestamp, :task_id, :agent_id, :summary, :payload);
COMMIT;
```

**Response (201 Created):**
```json
{
  "rebuttal_id": "reb-550e8400-e29b-41d4-a716-446655440000",
  "event_id": 13
}
```

**Errors:**

| Status | Code                    | Description                                    |
|--------|-------------------------|------------------------------------------------|
| 400    | `missing_field`         | Required field missing or empty                |
| 400    | `invalid_constraints`   | `constraints` is present but not an object, or a constraint column is not a valid identifier |
| 409    | `rebuttal_exists`       | Rebuttal with this `rebuttal_id` already exists|
| 409    | `foreign_key_violation` | `claim_id` or `agent_id` reference invalid     |
| 409    | `constraint_violation`  | `constraints` was supplied and did not match the claim's current state |

---

### POST /court/rulings

Record a court ruling. Inserts into `court_rulings`, optionally updates the claim status, and logs an event.

**Request:**
```json
{
  "ruling_id": "rul-550e8400-e29b-41d4-a716-446655440000",
  "claim_id": "clm-123",
  "task_id": "t-123",
  "worker_pct": 70,
  "summary": "The specification was ambiguous about email validation...",
  "judge_votes": "[{\"judge_id\": \"judge-0\", \"worker_pct\": 70, \"reasoning\": \"...\"}]",
  "ruled_at": "2026-02-28T18:00:00Z",
  "claim_status_update": "ruled",
  "event": {
    "event_source": "court",
    "event_type": "ruling.delivered",
    "timestamp": "2026-02-28T18:00:00Z",
    "task_id": "t-123",
    "agent_id": null,
    "summary": "Court ruled 70% to worker on 'Build login page'",
    "payload": "{\"ruling_id\": \"rul-550e8400\", \"claim_id\": \"clm-123\", \"worker_pct\": 70, \"summary\": \"The specification was ambiguous...\"}"
  }
}
```

The `claim_status_update` field tells the gateway to update the claim's status in the same transaction. If omitted or `null`, the claim status is not modified.

**Transaction:**
```sql
BEGIN IMMEDIATE;
INSERT INTO court_rulings (ruling_id, claim_id, task_id, worker_pct, summary, judge_votes, ruled_at)
  VALUES (:ruling_id, :claim_id, :task_id, :worker_pct, :summary, :judge_votes, :ruled_at);
-- Only if claim_status_update is provided:
UPDATE court_claims SET status = :claim_status_update WHERE claim_id = :claim_id;
INSERT INTO events (event_source, event_type, timestamp, task_id, agent_id, summary, payload)
  VALUES (:event_source, :event_type, :timestamp, :task_id, :agent_id, :summary, :payload);
COMMIT;
```

**Response (201 Created):**
```json
{
  "ruling_id": "rul-550e8400-e29b-41d4-a716-446655440000",
  "event_id": 14
}
```

**Errors:**

| Status | Code                    | Description                                    |
|--------|-------------------------|------------------------------------------------|
| 400    | `missing_field`         | Required field missing or empty                |
| 409    | `ruling_exists`         | Ruling with this `ruling_id` already exists    |
| 409    | `foreign_key_violation` | `claim_id` or `task_id` reference invalid      |

---

### DELETE /court/rulings/{claim_id}

Deletes a ruling row by `claim_id`. **Contract delta (WP-12):** this route now requires a JSON request body carrying an `event` object, exactly like every `POST` write route — so the deletion emits its event in the **same** `BEGIN IMMEDIATE` transaction as the delete. It is no longer bodyless, and `event` is not optional. Court is the only caller (used during ruling-retry cleanup, T-040).

**Request:**
```json
{
  "event": {
    "event_source": "court",
    "event_type": "ruling.deleted",
    "timestamp": "2026-02-28T18:05:00Z",
    "task_id": "t-123",
    "agent_id": null,
    "summary": "Ruling rul-550e8400 deleted (retry cleanup)",
    "payload": "{\"ruling_id\": \"rul-550e8400\", \"claim_id\": \"clm-123\"}"
  }
}
```

**Transaction:**
```sql
BEGIN IMMEDIATE;
DELETE FROM court_rulings WHERE claim_id = :claim_id;
-- The event is inserted ONLY if the DELETE actually removed a row (rowcount > 0).
-- A claim_id with no matching ruling deletes nothing, writes no event, and still
-- returns 200 with deleted: false — this is not an error.
INSERT INTO events (event_source, event_type, timestamp, task_id, agent_id, summary, payload)
  VALUES (:event_source, :event_type, :timestamp, :task_id, :agent_id, :summary, :payload);
COMMIT;
```

**Response (200 OK — a ruling existed and was deleted):**
```json
{
  "deleted": true,
  "claim_id": "clm-123",
  "event_id": 15
}
```

**Response (200 OK — no ruling existed for this `claim_id`):**
```json
{
  "deleted": false,
  "claim_id": "clm-123",
  "event_id": null
}
```

`deleted` is the caller's signal for whether anything actually happened; `event_id` is `null`
whenever `deleted` is `false`, since no event was written for a no-op delete.

**Errors:**

| Status | Code                    | Description                                                     |
|--------|--------------------------|----------------------------------------------------------------------|
| 400    | `missing_field`         | `event` object missing or incomplete                                 |
| 409    | `foreign_key_violation` | The event's `agent_id` does not reference a valid agent (rolls back the delete too — delete and event insert are one transaction) |

---

## Error Codes

Complete list of error codes used by the Database Gateway service:

| Status | Code                      | Description                                              |
|--------|---------------------------|----------------------------------------------------------|
| 400    | `missing_field`           | Required field missing, null, or empty string            |
| 400    | `invalid_amount`          | Amount is not a valid integer in the required range      |
| 400    | `invalid_field`           | Request contains an unknown or disallowed field name     |
| 400    | `empty_updates`           | `updates` object contains no fields                      |
| 400    | `amount_mismatch`         | Split amounts do not sum to escrow amount                |
| 402    | `insufficient_funds`      | Escrow lock would cause negative balance                 |
| 404    | `account_not_found`       | No account with this ID                                  |
| 404    | `escrow_not_found`        | No escrow with this ID                                   |
| 404    | `task_not_found`          | No task with this ID                                     |
| 409    | `public_key_exists`        | Public key already registered                            |
| 409    | `account_exists`          | Account already exists for this agent                    |
| 409    | `reference_conflict`      | Duplicate credit reference with different amount         |
| 409    | `escrow_already_locked`   | Escrow already locked for this (payer, task) pair        |
| 409    | `escrow_already_resolved` | Escrow has already been released or split                |
| 409    | `task_exists`             | Task with this ID already exists                         |
| 409    | `bid_exists`              | Agent already bid on this task                           |
| 409    | `asset_exists`            | Asset with this ID already exists                        |
| 409    | `feedback_exists`         | Feedback already submitted for this (task, from, to)     |
| 409    | `claim_exists`            | Claim with this ID already exists                        |
| 409    | `rebuttal_exists`         | Rebuttal with this ID already exists                     |
| 409    | `ruling_exists`           | Ruling with this ID already exists                       |
| 409    | `task_already_disputed`   | A Court claim already exists for this task (distinct from `claim_exists`) |
| 409    | `constraint_violation`    | A supplied `constraints` compare-and-set precondition did not match the row's current values |
| 409    | `foreign_key_violation`   | Foreign key constraint failed                            |
| 400    | `invalid_constraints`     | `constraints` is not a non-empty object of valid column identifiers |
| 400    | `invalid_json`            | Request body is not valid JSON, or not a JSON object      |
| 404    | `not_found`               | Generic not-found from a `constraints` check whose target row doesn't exist |
| 404    | `agent_not_found` / `bid_not_found` / `asset_not_found` / `feedback_not_found` / `claim_not_found` / `rebuttal_not_found` / `ruling_not_found` | Domain-specific not-found on a read route |
| 405    | `method_not_allowed`      | Path-segment collision on a route that reuses a literal (read routes) |
| 503    | `service_not_ready`       | Reader/writer not yet initialized (startup ordering)      |
| 503    | `database_unavailable`    | The database file could not be stat'd (health check)      |

---

## Standardized Error Format

All error responses follow this structure:

```json
{
  "error": "error_code",
  "message": "Human-readable description of what went wrong",
  "details": {}
}
```

Error responses contain exactly these three fields. The `details` object provides additional context when available (e.g., which field is missing, which foreign key failed) and is an empty object `{}` when there is no extra context. The `message` field never includes stack traces, SQL fragments, filesystem paths, or internal diagnostics.

This format is shared by all services in the Agent Task Economy.

---

## What This Service Does NOT Do

- **Domain business logic** — no signature verification, no permission checks, no independent state-machine decisions, no deadline calculation. The `constraints` mechanism lets a caller enforce a precondition it already decided on; the gateway never decides the precondition itself.
- **ID generation** — callers generate all IDs (`a-<uuid4>`, `t-<uuid4>`, etc.) and pass them in requests. The gateway never generates IDs (except `event_id`, which is an autoincrement).
- **Event derivation** — the gateway does not construct event summaries or payloads. Callers provide the complete event object.
- **Status-transition validation beyond `constraints`** — `POST /board/tasks/{task_id}/status` accepts any allowed-column values; the calling service enforces which transitions are legal, optionally backed by a `constraints` precondition for exactly-once semantics.
- **Authentication** — the gateway trusts its callers. It must never be reachable beyond the trusted host boundary (see the deployment constraint under Configuration).
- **Rate limiting** — no throttling on any endpoint. SQLite's write serialization (one writer connection, `BEGIN IMMEDIATE`) is the natural rate limiter.
- **Schema migrations** — the gateway loads `docs/specifications/schema.sql` once at startup, gated on an empty-database check; it does not silently swallow a load failure (a schema error now kills startup). Column additions to an existing `economy.db` are handled by explicit additive `ALTER TABLE` statements, not a general migration framework.

Superseded framing, corrected here: earlier drafts of this document said the gateway has "no read endpoints" and "does not serve read requests." That was accurate before the 2026-07 refactor and is not accurate now — see "Read API" above. Pagination on `GET /board/tasks` (`?limit=&offset=`) is the one read route that supports it.

---

## Interaction Patterns

### Task Creation Flow (Poster → Task Board → Gateway)

```
Agent               Task Board               Central Bank          DB Gateway
  |                      |                        |                     |
  |  1. POST /tasks      |                        |                     |
  |  (signed request)    |                        |                     |
  |  ------------------->|                        |                     |
  |                      |  2. Verify JWS         |                     |
  |                      |  (via Identity)        |                     |
  |                      |                        |                     |
  |                      |  3. POST /escrow/lock  |                     |
  |                      |  ------------------->  |                     |
  |                      |                        |  4. POST /bank/     |
  |                      |                        |     escrow/lock     |
  |                      |                        |  ------------------>|
  |                      |                        |                     | 5. BEGIN IMMEDIATE
  |                      |                        |                     | 6. Debit payer
  |                      |                        |                     | 7. Insert escrow
  |                      |                        |                     | 8. Insert tx
  |                      |                        |                     | 9. Insert event
  |                      |                        |                     | 10. COMMIT
  |                      |                        |  11. { escrow_id }  |
  |                      |                        |  <------------------|
  |                      |  12. { escrow_id }     |                     |
  |                      |  <-------------------  |                     |
  |                      |                        |                     |
  |                      |  13. POST /board/tasks |                     |
  |                      |  ------------------------------------------>|
  |                      |                        |                     | 14. BEGIN IMMEDIATE
  |                      |                        |                     | 15. Insert task
  |                      |                        |                     | 16. Insert event
  |                      |                        |                     | 17. COMMIT
  |                      |  18. { task_id }       |                     |
  |                      |  <------------------------------------------|
  |                      |                        |                     |
  |  19. 201 { task }    |                        |                     |
  |  <-------------------|                        |                     |
```

### Dispute Resolution Flow (Court → Gateway)

```
Court Service                                              DB Gateway
  |                                                             |
  |  1. POST /court/claims                                     |
  |  (insert claim + event)                                    |
  |  --------------------------------------------------------->|
  |                                                             | 2. BEGIN IMMEDIATE
  |                                                             | 3. Insert claim
  |                                                             | 4. Insert event
  |                                                             | 5. COMMIT
  |  6. { claim_id, event_id }                                 |
  |  <---------------------------------------------------------|
  |                                                             |
  |  ... worker submits rebuttal ...                           |
  |                                                             |
  |  7. POST /court/rebuttals                                  |
  |  (insert rebuttal + update claim status + event)           |
  |  --------------------------------------------------------->|
  |                                                             | 8. BEGIN IMMEDIATE
  |                                                             | 9. Insert rebuttal
  |                                                             | 10. Update claim status
  |                                                             | 11. Insert event
  |                                                             | 12. COMMIT
  |  13. { rebuttal_id, event_id }                             |
  |  <---------------------------------------------------------|
  |                                                             |
  |  ... LLM judge panel deliberates ...                       |
  |                                                             |
  |  14. POST /court/rulings                                   |
  |  (insert ruling + update claim status + event)             |
  |  --------------------------------------------------------->|
  |                                                             | 15. BEGIN IMMEDIATE
  |                                                             | 16. Insert ruling
  |                                                             | 17. Update claim status
  |                                                             | 18. Insert event
  |                                                             | 19. COMMIT
  |  20. { ruling_id, event_id }                               |
  |  <---------------------------------------------------------|
  |                                                             |
  |  21. POST /bank/escrow/split                               |
  |  (split escrow + event)                                    |
  |  --------------------------------------------------------->|
  |                                                             | 22. BEGIN IMMEDIATE
  |                                                             | 23. Credit worker
  |                                                             | 24. Credit poster
  |                                                             | 25. Insert txs
  |                                                             | 26. Resolve escrow
  |                                                             | 27. Insert event
  |                                                             | 28. COMMIT
  |  29. { escrow_id, worker_amount, poster_amount }           |
  |  <---------------------------------------------------------|
```

### Feedback Submission with Automatic Mutual Reveal

The caller never checks for the reverse pair itself and never sends `reveal_reverse`/`reverse_feedback_id` — those fields do not exist in the current request contract (see "POST /reputation/feedback" above). The gateway performs the reverse-pair lookup itself, inside the write transaction:

```
Reputation Service                                         DB Gateway
  |                                                             |
  |  1. POST /reputation/feedback                              |
  |     { ..., force_visible: false }                          |
  |  --------------------------------------------------------->|
  |                                                             | 2. BEGIN IMMEDIATE
  |                                                             | 3. Insert feedback (visible=0 unless force_visible)
  |                                                             | 4. Look up reverse-direction row for this (task, agents)
  |                                                             | 5. If found: UPDATE both rows visible=1,
  |                                                             |    insert a feedback.revealed event
  |                                                             | 6. Insert the caller's own event
  |                                                             | 7. COMMIT
  |  8. { feedback_id, visible: true, event_id }               |
  |  <---------------------------------------------------------|
```

---

## Configuration

```yaml
service:
  name: "db-gateway"
  version: "0.1.0"

server:
  host: "127.0.0.1"
  port: 8007
  log_level: "info"

logging:
  level: "INFO"
  directory: "data/logs"
  format: "json"

database:
  path: "data/economy.db"
  schema_path: "../../docs/specifications/schema.sql"
  busy_timeout_ms: 5000
  journal_mode: "wal"

request:
  max_body_size: 1048576
```

| Section                | Key                | Type    | Description                                          |
|------------------------|--------------------|---------|------------------------------------------------------|
| `service.name`         |                    | string  | Service identifier                                   |
| `service.version`      |                    | string  | Service version                                      |
| `server.host`          |                    | string  | Bind address — **must stay `127.0.0.1`** (see the deployment constraint below; never `0.0.0.0` in a context where port 8007 is reachable off-host) |
| `server.port`          |                    | integer | Listen port — **8007**. No other port has ever been the gateway's canonical port. |
| `server.log_level`     |                    | string  | Uvicorn log level                                    |
| `logging.level`        |                    | string  | Application log level                                |
| `logging.directory`    |                    | string  | Directory for JSON log files                         |
| `logging.format`       |                    | string  | Log output format (`json` or `text`)                 |
| `database.path`        |                    | string  | Path to the shared `economy.db` file                 |
| `database.schema_path` |                    | string  | Path to `docs/specifications/schema.sql`, loaded once at startup against an empty database (see Schema Initialization & Migration below) |
| `database.busy_timeout_ms` |               | integer | SQLite busy timeout in milliseconds                  |
| `database.journal_mode` |                  | string  | SQLite journal mode (`wal`)                          |
| `request.max_body_size` |                  | integer | Maximum request body size in bytes; enforced by `RequestValidationMiddleware` on POST/PUT/PATCH (413 `payload_too_large` when exceeded, 415 `unsupported_media_type` for a non-`application/json` `Content-Type`) |

All configuration values are required. The service fails to start if any value is missing. There are no hardcoded defaults.

**Deployment constraint (v1, by design, not a gap):** the gateway is unauthenticated (see "Authentication posture" below). Port 8007 must never be bound or exposed beyond `127.0.0.1` / the trusted host boundary. This is a documented posture, not an open item — do not "fix" it by adding auth without a ratified decision to do so.

---

## Endpoint Summary

| Method | Path                           | Domain     | Operation                          | SQL Statements |
|--------|--------------------------------|------------|------------------------------------|----------------|
| GET    | `/health`                      | —          | Health check                       | 2 (SELECT)     |
| POST   | `/identity/agents`             | Identity   | Register agent                     | 2 (INSERT)     |
| POST   | `/bank/accounts`               | Bank       | Create account + optional credit   | 2–3 (INSERT)   |
| POST   | `/bank/credit`                 | Bank       | Credit account                     | 3 (UPDATE + INSERT × 2) |
| POST   | `/bank/escrow/lock`            | Bank       | Lock funds in escrow               | 4 (UPDATE + INSERT × 3) |
| POST   | `/bank/escrow/release`         | Bank       | Release escrow to recipient        | 4 (SELECT + UPDATE × 2 + INSERT × 2) |
| POST   | `/bank/escrow/split`           | Bank       | Split escrow between parties       | 5–7 (SELECT + UPDATE × 3–5 + INSERT × 2–4) |
| POST   | `/board/tasks`                 | Board      | Create task                        | 2 (INSERT)     |
| POST   | `/board/bids`                  | Board      | Submit bid (+ `bid_count` increment) | 3 (INSERT + UPDATE + INSERT) |
| POST   | `/board/tasks/{task_id}/status`| Board      | Update task status + fields        | 2 (UPDATE + INSERT) |
| POST   | `/board/assets`                | Board      | Record asset metadata              | 2 (INSERT)     |
| POST   | `/reputation/feedback`         | Reputation | Submit feedback + auto mutual reveal | 2–4 (INSERT + SELECT + optional UPDATE + INSERT × 1–2) |
| POST   | `/court/claims`                | Court      | File dispute claim                 | 2 (INSERT)     |
| POST   | `/court/claims/{claim_id}/status` | Court   | Update claim status (event mandatory) | 2 (UPDATE + INSERT) |
| POST   | `/court/rebuttals`             | Court      | Submit rebuttal + update claim     | 2–3 (INSERT + UPDATE + INSERT) |
| POST   | `/court/rulings`               | Court      | Record ruling + update claim       | 2–3 (INSERT + UPDATE + INSERT) |
| DELETE | `/court/rulings/{claim_id}`    | Court      | Delete ruling (retry cleanup) + event | 1–2 (DELETE + optional INSERT) |

Every read route listed in "Read API" above is additionally part of this service's contract — omitted from the SQL-statement-count table because each is a single `SELECT`/aggregate query with no transaction.
