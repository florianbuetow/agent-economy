# Database Gateway Service — Production Release Test Specification

## Purpose

This document is the release-gate test specification for the Database Gateway Service.
It is intentionally strict and unambiguous:

- Every negative case has one required status code and one required error code.
- Every failing response must use the standard error envelope.
- Any behavior not listed here is out of scope for release sign-off.

The Database Gateway is unique among the services: it has no authentication, no business logic, and no outbound service calls. Its correctness guarantees are: (1) every write is atomic with its event, (2) idempotency is enforced via database constraints, (3) foreign key and uniqueness violations are caught and returned as structured errors, and (4) concurrent writes are serialized via `BEGIN IMMEDIATE`.

This document focuses only on core functionality, atomicity, idempotency, and constraint enforcement.

---

## Required API Error Contract (Normative for Release)

All failing responses must be JSON in this format:

```json
{
  "error": "ERROR_CODE",
  "message": "Human-readable description",
  "details": {}
}
```

Required status/error mappings:

| Status | Error Code                  | Required When |
|--------|-----------------------------|---------------|
| 400    | `MISSING_FIELD`             | A required field is absent, `null`, or empty string |
| 400    | `INVALID_AMOUNT`            | Amount is not a valid integer in the required range |
| 400    | `INVALID_FIELD`             | `updates` object contains an unknown column name |
| 400    | `EMPTY_UPDATES`             | `updates` object is empty |
| 400    | `AMOUNT_MISMATCH`           | Split amounts do not sum to escrow amount |
| 400    | `INVALID_JSON`              | Request body is malformed JSON |
| 402    | `INSUFFICIENT_FUNDS`        | Escrow lock would cause negative balance |
| 404    | `ACCOUNT_NOT_FOUND`         | No account with this ID |
| 404    | `ESCROW_NOT_FOUND`          | No escrow with this ID |
| 404    | `TASK_NOT_FOUND`            | No task with this ID |
| 405    | `METHOD_NOT_ALLOWED`        | Unsupported HTTP method on a defined route |
| 409    | `PUBLIC_KEY_EXISTS`          | Duplicate public key registration |
| 409    | `ACCOUNT_EXISTS`            | Account already exists for this agent |
| 409    | `REFERENCE_CONFLICT`        | Duplicate credit reference with different amount |
| 409    | `ESCROW_ALREADY_LOCKED`     | Escrow already locked for this (payer, task) pair with different amount |
| 409    | `ESCROW_ALREADY_RESOLVED`   | Escrow has already been released or split |
| 409    | `TASK_EXISTS`               | Task with this ID already exists |
| 409    | `BID_EXISTS`                | Agent already bid on this task |
| 409    | `ASSET_EXISTS`              | Asset with this ID already exists |
| 409    | `FEEDBACK_EXISTS`           | Feedback already submitted for this (task, from, to) triple |
| 409    | `CLAIM_EXISTS`              | Claim with this ID already exists |
| 409    | `REBUTTAL_EXISTS`           | Rebuttal with this ID already exists |
| 409    | `RULING_EXISTS`             | Ruling with this ID already exists |
| 409    | `FOREIGN_KEY_VIOLATION`     | Foreign key constraint failed |
| 413    | `PAYLOAD_TOO_LARGE`         | Request body exceeds configured `request.max_body_size` |
| 415    | `UNSUPPORTED_MEDIA_TYPE`    | `Content-Type` is not `application/json` for JSON endpoints |

---

## Test Data Conventions

- All requests are plain JSON. No JWS tokens — the gateway trusts its callers.
- `valid_event(source, type)` constructs a valid event object with all required fields populated.
- `agent_id` values match `a-<uuid4>`. The test harness generates fresh UUIDs per test.
- `task_id` values match `t-<uuid4>`.
- `escrow_id` values match `esc-<uuid4>`.
- `tx_id` values match `tx-<uuid4>`.
- `bid_id` values match `bid-<uuid4>`.
- `asset_id` values match `asset-<uuid4>`.
- `feedback_id` values match `fb-<uuid4>`.
- `claim_id` values match `clm-<uuid4>`.
- `rebuttal_id` values match `reb-<uuid4>`.
- `ruling_id` values match `rul-<uuid4>`.
- All timestamps are valid ISO 8601 UTC strings.
- `register_agent(name)` is a test helper that calls `POST /identity/agents` with fresh agent data and returns the agent record.
- `create_account(agent_id, balance)` is a test helper that calls `POST /bank/accounts` with an optional initial credit and returns the account record.
- `lock_escrow(payer_id, amount, task_id)` is a test helper that calls `POST /bank/escrow/lock` and returns the escrow record.
- `create_task(poster_id, escrow_id)` is a test helper that calls `POST /board/tasks` with valid task data and returns the task record.
- Every response from a mutating endpoint includes an `event_id` (positive integer) confirming the event was written.
- The database is initialized with the unified schema from `docs/specifications/schema.sql` before each test run. Each test starts with a clean database.

---

## Category 1: Agent Registration (`POST /identity/agents`)

### AGT-01 Register a valid agent
**Action:** `POST /identity/agents` with `{agent_id: "a-<uuid>", name: "Alice", public_key: "ed25519:<base64(32 bytes)>", registered_at: "2026-02-28T10:00:00Z", event: valid_event("identity", "agent.registered")}`.
**Expected:**
- `201 Created`
- Body includes `agent_id` and `event_id`
- `event_id` is a positive integer

### AGT-02 Register two agents with different keys
**Action:** Register Alice and Bob with distinct public keys.
**Expected:**
- Both return `201 Created`
- Both return distinct `event_id` values (monotonically increasing)

### AGT-03 Duplicate public key is rejected
**Setup:** Register Alice with `keypair_A`.
**Action:** Register Bob with the same `public_key` but different `agent_id`.
**Expected:**
- `409 Conflict`
- `error = PUBLIC_KEY_EXISTS`

### AGT-04 Idempotent replay returns existing agent
**Setup:** Register Alice. Capture response.
**Action:** Send the identical request again (same `agent_id`, `name`, `public_key`, `registered_at`).
**Expected:**
- `201 Created` (or `200 OK`)
- Returned `agent_id` matches the original

### AGT-05 Missing required field: `name`
**Action:** Omit `name` from the request body.
**Expected:** `400`, `error = MISSING_FIELD`

### AGT-06 Missing required field: `public_key`
**Action:** Omit `public_key` from the request body.
**Expected:** `400`, `error = MISSING_FIELD`

### AGT-07 Missing required field: `agent_id`
**Action:** Omit `agent_id` from the request body.
**Expected:** `400`, `error = MISSING_FIELD`

### AGT-08 Missing required field: `registered_at`
**Action:** Omit `registered_at` from the request body.
**Expected:** `400`, `error = MISSING_FIELD`

### AGT-09 Missing event object
**Action:** Omit the `event` field entirely.
**Expected:** `400`, `error = MISSING_FIELD`

### AGT-10 Event with missing required fields
**Action:** Send `event` with `event_source` omitted.
**Expected:** `400`, `error = MISSING_FIELD`

### AGT-11 Null required fields
**Action:** `{agent_id: null, name: null, public_key: null, registered_at: null, event: valid_event(...)}`.
**Expected:** `400`, `error = MISSING_FIELD`

### AGT-12 Event is written atomically with the agent
**Setup:** Register a valid agent. Query the `events` table directly (via WAL read).
**Expected:**
- The `events` table contains a row with `event_source = "identity"` and `event_type = "agent.registered"`
- The `event_id` in the response matches the row in the database

### AGT-13 Malformed JSON body
**Action:** Send truncated/invalid JSON.
**Expected:** `400`, `error = INVALID_JSON`

---

## Category 2: Account Creation (`POST /bank/accounts`)

### ACCT-01 Create account with positive initial balance
**Setup:** Register agent `alice`.
**Action:** `POST /bank/accounts` with `{account_id: alice, balance: 50, created_at: "...", initial_credit: {tx_id: "tx-<uuid>", amount: 50, reference: "initial_balance", timestamp: "..."}, event: valid_event("bank", "account.created")}`.
**Expected:**
- `201 Created`
- Body includes `account_id` and `event_id`
- `account_id` equals alice's agent ID

### ACCT-02 Create account with zero balance (no initial_credit)
**Setup:** Register agent `bob`.
**Action:** `POST /bank/accounts` with `{account_id: bob, balance: 0, created_at: "...", event: valid_event("bank", "account.created")}` and no `initial_credit` field.
**Expected:**
- `201 Created`

### ACCT-03 Initial credit transaction is written
**Setup:** Create account for alice with `balance: 50` and `initial_credit`.
**Action:** Query `bank_transactions` table directly.
**Expected:**
- A row exists with `account_id = alice`, `type = "credit"`, `amount = 50`, `reference = "initial_balance"`, `balance_after = 50`

### ACCT-04 Duplicate account is rejected
**Setup:** Create account for alice.
**Action:** Send identical `POST /bank/accounts` with same `account_id`.
**Expected:**
- `409 Conflict`
- `error = ACCOUNT_EXISTS`

### ACCT-05 Foreign key violation: agent does not exist
**Action:** `POST /bank/accounts` with `account_id` that has no corresponding `identity_agents` row.
**Expected:**
- `409 Conflict`
- `error = FOREIGN_KEY_VIOLATION`

### ACCT-06 Negative balance is rejected
**Action:** `POST /bank/accounts` with `balance: -1`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_AMOUNT`

### ACCT-07 Missing required field: `account_id`
**Action:** Omit `account_id`.
**Expected:** `400`, `error = MISSING_FIELD`

### ACCT-08 Missing event object
**Action:** Omit `event` field.
**Expected:** `400`, `error = MISSING_FIELD`

### ACCT-09 Event is written atomically with the account
**Setup:** Create account for alice with initial credit.
**Action:** Query `events` and `bank_accounts` tables.
**Expected:**
- Both the account row and the event row exist
- The `event_id` in the response matches the event row

---

## Category 3: Credit (`POST /bank/credit`)

### CR-01 Valid credit increases balance
**Setup:** Create account for alice with `balance: 100`.
**Action:** `POST /bank/credit` with `{tx_id: "tx-<uuid>", account_id: alice, amount: 10, reference: "salary_round_3", timestamp: "...", event: valid_event("bank", "salary.paid")}`.
**Expected:**
- `200 OK`
- Body includes `tx_id`, `balance_after`, `event_id`
- `balance_after` is `110`

### CR-02 Multiple credits accumulate correctly
**Setup:** Create account for alice with `balance: 0`.
**Action:** Credit alice with `amount: 30, reference: "bonus_1"`, then `amount: 20, reference: "bonus_2"`.
**Expected:**
- First credit: `balance_after` is `30`
- Second credit: `balance_after` is `50`

### CR-03 Idempotent credit returns same tx_id
**Setup:** Credit alice with `amount: 25, reference: "salary_round_1"`. Capture `tx_id`.
**Action:** Send the identical credit request again (same `account_id`, `amount`, `reference`).
**Expected:**
- `200 OK`
- Returned `tx_id` matches the original
- `balance_after` matches the original
- Balance is not double-credited

### CR-04 Duplicate reference with different amount is rejected
**Setup:** Credit alice with `amount: 25, reference: "salary_round_1"`.
**Action:** Credit alice with `amount: 30, reference: "salary_round_1"`.
**Expected:**
- `409 Conflict`
- `error = REFERENCE_CONFLICT`

### CR-05 Account not found
**Action:** `POST /bank/credit` with `account_id` that does not exist.
**Expected:**
- `404 Not Found`
- `error = ACCOUNT_NOT_FOUND`

### CR-06 Zero amount is rejected
**Action:** Credit with `amount: 0`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_AMOUNT`

### CR-07 Negative amount is rejected
**Action:** Credit with `amount: -10`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_AMOUNT`

### CR-08 Missing required field: `reference`
**Action:** Omit `reference`.
**Expected:** `400`, `error = MISSING_FIELD`

### CR-09 Missing required field: `amount`
**Action:** Omit `amount`.
**Expected:** `400`, `error = MISSING_FIELD`

### CR-10 Credit transaction is written to bank_transactions
**Setup:** Credit alice with `amount: 10, reference: "bonus"`.
**Action:** Query `bank_transactions` table.
**Expected:**
- A row exists with matching `tx_id`, `account_id`, `type = "credit"`, `amount = 10`, `reference = "bonus"`

### CR-11 Event is written atomically with the credit
**Setup:** Credit alice.
**Action:** Query `events` table.
**Expected:**
- An event row exists with matching `event_id` from the response

---

## Category 4: Escrow Lock (`POST /bank/escrow/lock`)

### ELOCK-01 Valid escrow lock
**Setup:** Create account for alice with `balance: 100`.
**Action:** `POST /bank/escrow/lock` with `{escrow_id: "esc-<uuid>", payer_account_id: alice, amount: 30, task_id: "t-001", created_at: "...", tx_id: "tx-<uuid>", event: valid_event("bank", "escrow.locked")}`.
**Expected:**
- `201 Created`
- Body includes `escrow_id`, `balance_after`, `event_id`
- `balance_after` is `70`

### ELOCK-02 Balance decreases by lock amount
**Setup:** Create account for alice with `balance: 100`. Lock escrow of `amount: 30`.
**Action:** Query `bank_accounts` table.
**Expected:** `balance` is `70`

### ELOCK-03 Escrow record is created with status locked
**Setup:** Lock escrow from alice.
**Action:** Query `bank_escrow` table.
**Expected:**
- Row exists with matching `escrow_id`, `payer_account_id`, `amount`, `task_id`
- `status` is `"locked"`
- `resolved_at` is null

### ELOCK-04 Escrow lock transaction is written
**Setup:** Lock escrow of `amount: 30` for `task_id: "t-001"` from alice.
**Action:** Query `bank_transactions` table.
**Expected:**
- A row exists with `type = "escrow_lock"`, `amount = 30`, `reference = "t-001"`, `balance_after = 70`

### ELOCK-05 Insufficient funds
**Setup:** Create account for alice with `balance: 10`.
**Action:** Lock escrow with `amount: 50`.
**Expected:**
- `402 Payment Required`
- `error = INSUFFICIENT_FUNDS`

### ELOCK-06 Insufficient funds does not modify balance
**Setup:** Create account for alice with `balance: 10`. Attempt lock with `amount: 50` (fails).
**Action:** Query alice's balance.
**Expected:** `balance` is still `10`

### ELOCK-07 Account not found
**Action:** Lock escrow with `payer_account_id` that does not exist.
**Expected:**
- `404 Not Found`
- `error = ACCOUNT_NOT_FOUND`

### ELOCK-08 Idempotent lock returns same escrow_id
**Setup:** Lock escrow with `amount: 30, task_id: "t-001"`. Capture `escrow_id`.
**Action:** Send the identical lock request again (same `payer_account_id`, `amount`, `task_id`).
**Expected:**
- Returns existing `escrow_id`
- Balance is not double-debited

### ELOCK-09 Duplicate (payer, task) with different amount is rejected
**Setup:** Lock escrow with `amount: 30, task_id: "t-001"` from alice.
**Action:** Lock escrow with `amount: 50, task_id: "t-001"` from alice.
**Expected:**
- `409 Conflict`
- `error = ESCROW_ALREADY_LOCKED`

### ELOCK-10 Zero amount is rejected
**Action:** Lock escrow with `amount: 0`.
**Expected:** `400`, `error = INVALID_AMOUNT`

### ELOCK-11 Negative amount is rejected
**Action:** Lock escrow with `amount: -10`.
**Expected:** `400`, `error = INVALID_AMOUNT`

### ELOCK-12 Missing required field: `task_id`
**Action:** Omit `task_id`.
**Expected:** `400`, `error = MISSING_FIELD`

### ELOCK-13 Missing event object
**Action:** Omit `event` field.
**Expected:** `400`, `error = MISSING_FIELD`

### ELOCK-14 Exact balance lock succeeds
**Setup:** Create account for alice with `balance: 50`.
**Action:** Lock escrow with `amount: 50`.
**Expected:**
- `201 Created`
- `balance_after` is `0`

### ELOCK-15 Multiple escrow locks for different tasks
**Setup:** Create account for alice with `balance: 100`.
**Action:** Lock `amount: 30` for `task_id: "t-001"`, then lock `amount: 20` for `task_id: "t-002"`.
**Expected:**
- Both succeed with `201 Created`
- Final balance is `50`

---

## Category 5: Escrow Release (`POST /bank/escrow/release`)

### EREL-01 Valid full release to recipient
**Setup:** Register alice and bob. Create account for alice with `balance: 100` and bob with `balance: 0`. Lock escrow of `amount: 50` from alice. Capture `escrow_id`.
**Action:** `POST /bank/escrow/release` with `{escrow_id: <escrow_id>, recipient_account_id: bob, tx_id: "tx-<uuid>", resolved_at: "...", event: valid_event("bank", "escrow.released")}`.
**Expected:**
- `200 OK`
- Body includes `escrow_id`, `status`, `amount`, `recipient_account_id`, `event_id`
- `status` is `"released"`
- `amount` is `50`

### EREL-02 Recipient balance increases by escrow amount
**Setup:** Same as EREL-01. Release escrow to bob.
**Action:** Query bob's balance.
**Expected:** `balance` is `50`

### EREL-03 Release creates escrow_release transaction on recipient
**Setup:** Release escrow to bob. Capture `escrow_id`.
**Action:** Query `bank_transactions` for bob.
**Expected:** A row with `type = "escrow_release"`, `reference = <escrow_id>`, `amount = 50`

### EREL-04 Escrow status changes to released
**Setup:** Release escrow to bob.
**Action:** Query `bank_escrow` table.
**Expected:**
- `status` is `"released"`
- `resolved_at` is not null

### EREL-05 Escrow not found
**Action:** Release with `escrow_id` that does not exist.
**Expected:** `404`, `error = ESCROW_NOT_FOUND`

### EREL-06 Already resolved escrow
**Setup:** Lock escrow from alice, release to bob.
**Action:** Attempt to release the same escrow again.
**Expected:** `409`, `error = ESCROW_ALREADY_RESOLVED`

### EREL-07 Recipient account not found
**Setup:** Lock escrow from alice.
**Action:** Release with `recipient_account_id` that does not exist.
**Expected:** `404`, `error = ACCOUNT_NOT_FOUND`

### EREL-08 Missing required field: `recipient_account_id`
**Action:** Omit `recipient_account_id`.
**Expected:** `400`, `error = MISSING_FIELD`

### EREL-09 Missing required field: `escrow_id`
**Action:** Omit `escrow_id`.
**Expected:** `400`, `error = MISSING_FIELD`

---

## Category 6: Escrow Split (`POST /bank/escrow/split`)

### ESPL-01 Even 50/50 split
**Setup:** Create accounts for alice (`balance: 1000`) and bob (`balance: 0`). Lock escrow of `amount: 500` from alice. Capture `escrow_id`.
**Action:** `POST /bank/escrow/split` with `{escrow_id: <escrow_id>, worker_account_id: bob, worker_amount: 250, poster_account_id: alice, poster_amount: 250, worker_tx_id: "tx-<uuid>", poster_tx_id: "tx-<uuid>", resolved_at: "...", event: valid_event("bank", "escrow.split")}`.
**Expected:**
- `200 OK`
- Body includes `escrow_id`, `status`, `worker_amount`, `poster_amount`, `event_id`
- `status` is `"split"`
- `worker_amount` is `250`
- `poster_amount` is `250`

### ESPL-02 Uneven split
**Setup:** Lock escrow of `amount: 100` from alice.
**Action:** Split with `worker_amount: 70, poster_amount: 30`.
**Expected:**
- `200 OK`
- `worker_amount` is `70`, `poster_amount` is `30`

### ESPL-03 Worker gets all (100/0 split)
**Setup:** Lock escrow of `amount: 100` from alice.
**Action:** Split with `worker_amount: 100, poster_amount: 0`.
**Expected:**
- `200 OK`
- `worker_amount` is `100`, `poster_amount` is `0`

### ESPL-04 Poster gets all (0/100 split)
**Setup:** Lock escrow of `amount: 100` from alice.
**Action:** Split with `worker_amount: 0, poster_amount: 100`.
**Expected:**
- `200 OK`
- `worker_amount` is `0`, `poster_amount` is `100`

### ESPL-05 Both account balances updated correctly after split
**Setup:** Create accounts for alice (`balance: 200`) and bob (`balance: 10`). Lock escrow of `amount: 100` from alice. Split with `worker_amount: 60, poster_amount: 40`.
**Action:** Query balances for both.
**Expected:**
- alice's balance is `140` (200 - 100 + 40)
- bob's balance is `70` (10 + 60)

### ESPL-06 Split creates escrow_release transactions on both accounts
**Setup:** Lock escrow of `amount: 100` from alice. Split with `worker_amount: 60, poster_amount: 40` between bob (worker) and alice (poster). Capture `escrow_id`.
**Action:** Query `bank_transactions` for both.
**Expected:**
- bob has `escrow_release` with `amount: 60`, `reference: <escrow_id>`
- alice has `escrow_release` with `amount: 40`, `reference: <escrow_id>`

### ESPL-07 Zero-amount share creates no transaction
**Setup:** Lock escrow of `amount: 100` from alice. Split with `worker_amount: 100, poster_amount: 0`.
**Action:** Query `bank_transactions` for alice (poster).
**Expected:**
- No `escrow_release` transaction for alice with this `escrow_id` as reference

### ESPL-08 Escrow status changes to split
**Setup:** Split escrow.
**Action:** Query `bank_escrow` table.
**Expected:**
- `status` is `"split"`
- `resolved_at` is not null

### ESPL-09 Amounts do not sum to escrow amount
**Setup:** Lock escrow of `amount: 100` from alice.
**Action:** Split with `worker_amount: 60, poster_amount: 60` (sum = 120 != 100).
**Expected:** `400`, `error = AMOUNT_MISMATCH`

### ESPL-10 Escrow not found
**Action:** Split with `escrow_id` that does not exist.
**Expected:** `404`, `error = ESCROW_NOT_FOUND`

### ESPL-11 Already resolved escrow
**Setup:** Lock escrow from alice, release it to bob.
**Action:** Attempt to split the same escrow.
**Expected:** `409`, `error = ESCROW_ALREADY_RESOLVED`

### ESPL-12 Worker account not found
**Setup:** Lock escrow from alice.
**Action:** Split with `worker_account_id` that does not exist.
**Expected:** `404`, `error = ACCOUNT_NOT_FOUND`

### ESPL-13 Poster account not found
**Setup:** Lock escrow from alice.
**Action:** Split with `poster_account_id` that does not exist.
**Expected:** `404`, `error = ACCOUNT_NOT_FOUND`

### ESPL-14 Negative worker_amount is rejected
**Setup:** Lock escrow from alice.
**Action:** Split with `worker_amount: -10, poster_amount: 110`.
**Expected:** `400`, `error = INVALID_AMOUNT`

### ESPL-15 Negative poster_amount is rejected
**Setup:** Lock escrow from alice.
**Action:** Split with `worker_amount: 110, poster_amount: -10`.
**Expected:** `400`, `error = INVALID_AMOUNT`

### ESPL-16 Missing required field: `worker_account_id`
**Action:** Omit `worker_account_id`.
**Expected:** `400`, `error = MISSING_FIELD`

---

## Category 7: Task Creation (`POST /board/tasks`)

### TASK-01 Create a valid task
**Setup:** Register agent alice. Create account. Lock escrow.
**Action:** `POST /board/tasks` with all required fields: `task_id`, `poster_id`, `title`, `spec`, `reward`, `status: "open"`, `bidding_deadline_seconds`, `deadline_seconds`, `review_deadline_seconds`, `bidding_deadline`, `escrow_id`, `created_at`, and `event`.
**Expected:**
- `201 Created`
- Body includes `task_id` and `event_id`

### TASK-02 Duplicate task_id is rejected
**Setup:** Create a task with `task_id: "t-001"`.
**Action:** Create another task with the same `task_id`.
**Expected:** `409`, `error = TASK_EXISTS`

### TASK-03 Foreign key violation: poster_id does not exist
**Action:** Create task with `poster_id` that has no matching `identity_agents` row.
**Expected:** `409`, `error = FOREIGN_KEY_VIOLATION`

### TASK-04 Foreign key violation: escrow_id does not exist
**Action:** Create task with `escrow_id` that has no matching `bank_escrow` row.
**Expected:** `409`, `error = FOREIGN_KEY_VIOLATION`

### TASK-05 Missing required field: `title`
**Action:** Omit `title`.
**Expected:** `400`, `error = MISSING_FIELD`

### TASK-06 Missing required field: `spec`
**Action:** Omit `spec`.
**Expected:** `400`, `error = MISSING_FIELD`

### TASK-07 Missing event object
**Action:** Omit `event`.
**Expected:** `400`, `error = MISSING_FIELD`

### TASK-08 Task and event are written atomically
**Setup:** Create a valid task.
**Action:** Query `board_tasks` and `events` tables.
**Expected:** Both rows exist. Event `event_type` is `"task.created"`.

### TASK-09 Negative reward is rejected
**Action:** Create task with `reward: -1`.
**Expected:** `400`, `error = INVALID_AMOUNT`

---

## Category 8: Bid Submission (`POST /board/bids`)

### BID-01 Submit a valid bid
**Setup:** Register agent bob. Create a task.
**Action:** `POST /board/bids` with `{bid_id: "bid-<uuid>", task_id: <task_id>, bidder_id: bob, proposal: "I will build...", submitted_at: "...", event: valid_event("board", "bid.submitted")}`.
**Expected:**
- `201 Created`
- Body includes `bid_id` and `event_id`

### BID-02 Duplicate bid (same bidder, same task) is rejected
**Setup:** Submit a bid from bob on task t-001.
**Action:** Submit another bid from bob on task t-001 (different `bid_id`).
**Expected:** `409`, `error = BID_EXISTS`

### BID-03 Different bidders on same task succeed
**Setup:** Register bob and carol. Create a task.
**Action:** Submit bids from bob and carol.
**Expected:** Both return `201 Created`

### BID-04 Foreign key violation: task_id does not exist
**Action:** Submit bid with `task_id` that has no matching task.
**Expected:** `409`, `error = FOREIGN_KEY_VIOLATION`

### BID-05 Foreign key violation: bidder_id does not exist
**Action:** Submit bid with `bidder_id` that has no matching agent.
**Expected:** `409`, `error = FOREIGN_KEY_VIOLATION`

### BID-06 Missing required field: `proposal`
**Action:** Omit `proposal`.
**Expected:** `400`, `error = MISSING_FIELD`

### BID-07 Missing event object
**Action:** Omit `event`.
**Expected:** `400`, `error = MISSING_FIELD`

---

## Category 9: Task Status Update (`POST /board/tasks/{task_id}/status`)

### TSTAT-01 Update task to accepted
**Setup:** Create a task in `open` status. Register worker bob.
**Action:** `POST /board/tasks/{task_id}/status` with `{updates: {status: "accepted", worker_id: bob, accepted_bid_id: "bid-<uuid>", accepted_at: "...", execution_deadline: "..."}, event: valid_event("board", "task.accepted")}`.
**Expected:**
- `200 OK`
- Body includes `task_id`, `status`, `event_id`
- `status` is `"accepted"`

### TSTAT-02 Update task to submitted
**Setup:** Create and accept a task.
**Action:** Update with `{updates: {status: "submitted", submitted_at: "...", review_deadline: "..."}, event: valid_event("board", "task.submitted")}`.
**Expected:** `200 OK`, `status` is `"submitted"`

### TSTAT-03 Update task to approved
**Setup:** Create, accept, and submit a task.
**Action:** Update with `{updates: {status: "approved", approved_at: "..."}, event: valid_event("board", "task.approved")}`.
**Expected:** `200 OK`, `status` is `"approved"`

### TSTAT-04 Update task to cancelled
**Setup:** Create a task.
**Action:** Update with `{updates: {status: "cancelled", cancelled_at: "..."}, event: valid_event("board", "task.cancelled")}`.
**Expected:** `200 OK`, `status` is `"cancelled"`

### TSTAT-05 Update task to disputed
**Setup:** Create, accept, and submit a task.
**Action:** Update with `{updates: {status: "disputed", dispute_reason: "The login page...", disputed_at: "..."}, event: valid_event("board", "task.disputed")}`.
**Expected:** `200 OK`, `status` is `"disputed"`

### TSTAT-06 Update task to ruled
**Setup:** Create a disputed task.
**Action:** Update with `{updates: {status: "ruled", ruling_id: "rul-<uuid>", worker_pct: 70, ruling_summary: "Spec was ambiguous...", ruled_at: "..."}, event: valid_event("board", "task.ruled")}`.
**Expected:** `200 OK`, `status` is `"ruled"`

### TSTAT-07 Update task to expired
**Setup:** Create a task.
**Action:** Update with `{updates: {status: "expired", expired_at: "..."}, event: valid_event("board", "task.expired")}`.
**Expected:** `200 OK`, `status` is `"expired"`

### TSTAT-08 Task not found
**Action:** Update `task_id` that does not exist.
**Expected:** `404`, `error = TASK_NOT_FOUND`

### TSTAT-09 Unknown column in updates is rejected
**Action:** Update with `{updates: {status: "approved", nonexistent_column: "value"}, event: valid_event(...)}`.
**Expected:** `400`, `error = INVALID_FIELD`

### TSTAT-10 Empty updates object
**Action:** Update with `{updates: {}, event: valid_event(...)}`.
**Expected:** `400`, `error = EMPTY_UPDATES`

### TSTAT-11 Missing updates object
**Action:** Omit `updates` field entirely.
**Expected:** `400`, `error = MISSING_FIELD`

### TSTAT-12 Missing event object
**Action:** Omit `event` field entirely.
**Expected:** `400`, `error = MISSING_FIELD`

### TSTAT-13 Multiple fields updated in one call
**Setup:** Create a task.
**Action:** Update with `{updates: {status: "accepted", worker_id: bob, accepted_bid_id: "bid-<uuid>", accepted_at: "...", execution_deadline: "..."}, event: ...}`.
**Action:** Query `board_tasks` table.
**Expected:** All five columns are updated in the same row.

### TSTAT-14 Gateway does not validate status transitions
**Setup:** Create a task in `open` status.
**Action:** Update directly to `{updates: {status: "approved", approved_at: "..."}, event: ...}` (skipping `accepted` and `submitted`).
**Expected:** `200 OK` — the gateway accepts the update without validating the transition.

---

## Category 10: Asset Recording (`POST /board/assets`)

### ASSET-01 Record a valid asset
**Setup:** Create a task.
**Action:** `POST /board/assets` with `{asset_id: "asset-<uuid>", task_id: <task_id>, uploader_id: <worker_id>, filename: "login-page.zip", content_type: "application/zip", size_bytes: 245760, storage_path: "data/assets/t-123/login-page.zip", uploaded_at: "...", event: valid_event("board", "asset.uploaded")}`.
**Expected:**
- `201 Created`
- Body includes `asset_id` and `event_id`

### ASSET-02 Duplicate asset_id is rejected
**Setup:** Record an asset with `asset_id: "asset-001"`.
**Action:** Record another asset with the same `asset_id`.
**Expected:** `409`, `error = ASSET_EXISTS`

### ASSET-03 Foreign key violation: task_id does not exist
**Action:** Record asset with `task_id` that has no matching task.
**Expected:** `409`, `error = FOREIGN_KEY_VIOLATION`

### ASSET-04 Foreign key violation: uploader_id does not exist
**Action:** Record asset with `uploader_id` that has no matching agent.
**Expected:** `409`, `error = FOREIGN_KEY_VIOLATION`

### ASSET-05 Missing required field: `filename`
**Action:** Omit `filename`.
**Expected:** `400`, `error = MISSING_FIELD`

### ASSET-06 Missing event object
**Action:** Omit `event`.
**Expected:** `400`, `error = MISSING_FIELD`

### ASSET-07 Multiple assets for the same task succeed
**Setup:** Create a task. Record two assets with different `asset_id` values.
**Expected:** Both return `201 Created`

---

## Category 11: Feedback Submission (`POST /reputation/feedback`)

### FB-01 Submit feedback without reveal
**Setup:** Register alice and bob. Create a task.
**Action:** `POST /reputation/feedback` with `{feedback_id: "fb-<uuid>", task_id: <task_id>, from_agent_id: alice, to_agent_id: bob, role: "poster", category: "delivery_quality", rating: "satisfied", comment: "Good work", submitted_at: "...", reveal_reverse: false, event: valid_event("reputation", "feedback.revealed")}`.
**Expected:**
- `201 Created`
- Body includes `feedback_id`, `visible`, `event_id`
- `visible` is `false`

### FB-02 Submit feedback with mutual reveal
**Setup:** Register alice and bob. Create a task. Submit feedback from bob to alice (no reveal). Capture `feedback_id` of bob's feedback.
**Action:** Submit feedback from alice to bob with `reveal_reverse: true, reverse_feedback_id: <bob_feedback_id>`.
**Expected:**
- `201 Created`
- `visible` is `true`

### FB-03 Mutual reveal sets both feedbacks to visible
**Setup:** Same as FB-02.
**Action:** Query `reputation_feedback` table for both feedbacks.
**Expected:**
- Both rows have `visible = 1`

### FB-04 Duplicate feedback (same task, from, to) is rejected
**Setup:** Submit feedback from alice to bob for task t-001.
**Action:** Submit another feedback from alice to bob for task t-001 (different `feedback_id`).
**Expected:** `409`, `error = FEEDBACK_EXISTS`

### FB-05 Same agents, different tasks succeed
**Setup:** Create two tasks. Submit feedback from alice to bob for each.
**Expected:** Both return `201 Created`

### FB-06 Foreign key violation: from_agent_id does not exist
**Action:** Submit feedback with `from_agent_id` that has no matching agent.
**Expected:** `409`, `error = FOREIGN_KEY_VIOLATION`

### FB-07 Missing required field: `rating`
**Action:** Omit `rating`.
**Expected:** `400`, `error = MISSING_FIELD`

### FB-08 Missing event object
**Action:** Omit `event`.
**Expected:** `400`, `error = MISSING_FIELD`

### FB-09 Null comment is accepted
**Action:** Submit feedback with `comment: null`.
**Expected:** `201 Created`

### FB-10 Feedback without reveal_reverse defaults to sealed
**Action:** Submit feedback with `reveal_reverse: false` (no `reverse_feedback_id`).
**Expected:**
- `201 Created`
- `visible` is `false`
- Database row has `visible = 0`

---

## Category 12: Dispute Claims (`POST /court/claims`)

### CLM-01 File a valid claim
**Setup:** Register alice and bob. Create a task.
**Action:** `POST /court/claims` with `{claim_id: "clm-<uuid>", task_id: <task_id>, claimant_id: alice, respondent_id: bob, reason: "The login page does not validate email format", status: "filed", filed_at: "...", event: valid_event("court", "claim.filed")}`.
**Expected:**
- `201 Created`
- Body includes `claim_id` and `event_id`

### CLM-02 Duplicate claim_id is rejected
**Setup:** File a claim.
**Action:** File another claim with the same `claim_id`.
**Expected:** `409`, `error = CLAIM_EXISTS`

### CLM-03 Foreign key violation: task_id does not exist
**Action:** File claim with `task_id` that has no matching task.
**Expected:** `409`, `error = FOREIGN_KEY_VIOLATION`

### CLM-04 Foreign key violation: claimant_id does not exist
**Action:** File claim with `claimant_id` that has no matching agent.
**Expected:** `409`, `error = FOREIGN_KEY_VIOLATION`

### CLM-05 Foreign key violation: respondent_id does not exist
**Action:** File claim with `respondent_id` that has no matching agent.
**Expected:** `409`, `error = FOREIGN_KEY_VIOLATION`

### CLM-06 Missing required field: `reason`
**Action:** Omit `reason`.
**Expected:** `400`, `error = MISSING_FIELD`

### CLM-07 Missing event object
**Action:** Omit `event`.
**Expected:** `400`, `error = MISSING_FIELD`

### CLM-08 Claim and event are written atomically
**Setup:** File a valid claim.
**Action:** Query `court_claims` and `events` tables.
**Expected:** Both rows exist. Event `event_type` is `"claim.filed"`.

---

## Category 13: Rebuttals (`POST /court/rebuttals`)

### REB-01 Submit a valid rebuttal
**Setup:** Register bob. File a claim.
**Action:** `POST /court/rebuttals` with `{rebuttal_id: "reb-<uuid>", claim_id: <claim_id>, agent_id: bob, content: "The specification did not mention email validation...", submitted_at: "...", event: valid_event("court", "rebuttal.submitted")}`.
**Expected:**
- `201 Created`
- Body includes `rebuttal_id` and `event_id`

### REB-02 Rebuttal with claim_status_update
**Setup:** File a claim with `status: "filed"`.
**Action:** Submit rebuttal with `claim_status_update: "rebuttal"`.
**Expected:**
- `201 Created`
- Query `court_claims`: claim `status` is now `"rebuttal"`

### REB-03 Rebuttal without claim_status_update leaves claim unchanged
**Setup:** File a claim with `status: "filed"`.
**Action:** Submit rebuttal without `claim_status_update` (or `null`).
**Expected:**
- `201 Created`
- Query `court_claims`: claim `status` is still `"filed"`

### REB-04 Duplicate rebuttal_id is rejected
**Setup:** Submit a rebuttal.
**Action:** Submit another rebuttal with the same `rebuttal_id`.
**Expected:** `409`, `error = REBUTTAL_EXISTS`

### REB-05 Foreign key violation: claim_id does not exist
**Action:** Submit rebuttal with `claim_id` that has no matching claim.
**Expected:** `409`, `error = FOREIGN_KEY_VIOLATION`

### REB-06 Foreign key violation: agent_id does not exist
**Action:** Submit rebuttal with `agent_id` that has no matching agent.
**Expected:** `409`, `error = FOREIGN_KEY_VIOLATION`

### REB-07 Missing required field: `content`
**Action:** Omit `content`.
**Expected:** `400`, `error = MISSING_FIELD`

### REB-08 Missing event object
**Action:** Omit `event`.
**Expected:** `400`, `error = MISSING_FIELD`

### REB-09 Rebuttal and claim update are atomic
**Setup:** File a claim. Submit rebuttal with `claim_status_update: "rebuttal"`.
**Action:** Query `court_rebuttals`, `court_claims`, and `events` tables.
**Expected:** All three mutations (rebuttal insert, claim update, event insert) are present.

---

## Category 14: Rulings (`POST /court/rulings`)

### RUL-01 Record a valid ruling
**Setup:** File a claim. Create a task.
**Action:** `POST /court/rulings` with `{ruling_id: "rul-<uuid>", claim_id: <claim_id>, task_id: <task_id>, worker_pct: 70, summary: "The specification was ambiguous...", judge_votes: "[{\"judge_id\": \"judge-0\", \"worker_pct\": 70, \"reasoning\": \"...\"}]", ruled_at: "...", event: valid_event("court", "ruling.delivered")}`.
**Expected:**
- `201 Created`
- Body includes `ruling_id` and `event_id`

### RUL-02 Ruling with claim_status_update
**Setup:** File a claim with `status: "rebuttal"`.
**Action:** Record ruling with `claim_status_update: "ruled"`.
**Expected:**
- `201 Created`
- Query `court_claims`: claim `status` is now `"ruled"`

### RUL-03 Ruling without claim_status_update leaves claim unchanged
**Setup:** File a claim with `status: "rebuttal"`.
**Action:** Record ruling without `claim_status_update`.
**Expected:**
- `201 Created`
- Query `court_claims`: claim `status` is still `"rebuttal"`

### RUL-04 Duplicate ruling_id is rejected
**Setup:** Record a ruling.
**Action:** Record another ruling with the same `ruling_id`.
**Expected:** `409`, `error = RULING_EXISTS`

### RUL-05 Foreign key violation: claim_id does not exist
**Action:** Record ruling with `claim_id` that has no matching claim.
**Expected:** `409`, `error = FOREIGN_KEY_VIOLATION`

### RUL-06 Foreign key violation: task_id does not exist
**Action:** Record ruling with `task_id` that has no matching task.
**Expected:** `409`, `error = FOREIGN_KEY_VIOLATION`

### RUL-07 Missing required field: `summary`
**Action:** Omit `summary`.
**Expected:** `400`, `error = MISSING_FIELD`

### RUL-08 Missing required field: `judge_votes`
**Action:** Omit `judge_votes`.
**Expected:** `400`, `error = MISSING_FIELD`

### RUL-09 Missing event object
**Action:** Omit `event`.
**Expected:** `400`, `error = MISSING_FIELD`

### RUL-10 Ruling and claim update are atomic
**Setup:** File a claim. Record ruling with `claim_status_update: "ruled"`.
**Action:** Query `court_rulings`, `court_claims`, and `events` tables.
**Expected:** All three mutations (ruling insert, claim update, event insert) are present.

---

## Category 15: Health (`GET /health`)

### HLTH-01 Health schema is correct
**Action:** `GET /health`
**Expected:**
- `200 OK`
- Body contains `status`, `uptime_seconds`, `started_at`, `database_size_bytes`, `total_events`
- `status` is `"ok"`
- `uptime_seconds` is a non-negative number
- `started_at` is valid ISO 8601 timestamp
- `database_size_bytes` is a non-negative integer
- `total_events` is a non-negative integer

### HLTH-02 total_events is accurate
**Setup:** Perform `N` writes (each creates one event).
**Action:** `GET /health`
**Expected:** `total_events` equals `N`

### HLTH-03 Uptime is monotonic
**Action:** Call `GET /health` twice with a delay of at least 1 second.
**Expected:** Second `uptime_seconds` is strictly greater than first `uptime_seconds`

### HLTH-04 database_size_bytes is positive after writes
**Setup:** Perform at least one write.
**Action:** `GET /health`
**Expected:** `database_size_bytes` is greater than 0

---

## Category 16: Event Integrity

### EVT-01 Event IDs are monotonically increasing
**Action:** Perform 5 writes in sequence. Collect `event_id` from each response.
**Expected:** Each `event_id` is strictly greater than the previous one.

### EVT-02 Event contains correct source and type
**Setup:** Register an agent (event source: "identity", type: "agent.registered"). Credit an account (event source: "bank", type: "salary.paid").
**Action:** Query `events` table.
**Expected:**
- First event has `event_source = "identity"`, `event_type = "agent.registered"`
- Second event has `event_source = "bank"`, `event_type = "salary.paid"`

### EVT-03 Event task_id and agent_id match request
**Setup:** Create a task with a specific `task_id` and `poster_id`.
**Action:** Query `events` table for the task creation event.
**Expected:**
- `task_id` matches the request
- `agent_id` matches the poster

### EVT-04 Event summary and payload are stored as provided
**Setup:** Register an agent with a specific summary and payload in the event.
**Action:** Query `events` table.
**Expected:** `summary` and `payload` match exactly what was sent in the request.

### EVT-05 Failed write does not create an event
**Setup:** Attempt to create an account for a non-existent agent (foreign key violation).
**Action:** Count events before and after the failed request.
**Expected:** Event count has not increased.

---

## Category 17: Atomicity

### ATOM-01 Account creation + credit + event are all-or-nothing
**Setup:** Attempt to create an account with an `initial_credit` that references a non-existent `tx_id` format (if any check fails).
**Action:** Query all three tables.
**Expected:** Either all rows exist or none exist. No partial state.

### ATOM-02 Escrow lock failure does not leave partial state
**Setup:** Create account for alice with `balance: 10`. Attempt escrow lock with `amount: 50` (insufficient funds).
**Action:** Query `bank_escrow`, `bank_transactions`, and `events` tables.
**Expected:** No escrow row, no transaction row, no event row created.

### ATOM-03 Escrow release failure does not leave partial state
**Setup:** Lock escrow from alice. Attempt to release to a non-existent account.
**Action:** Query `bank_escrow`, `bank_transactions`, and `events` tables.
**Expected:**
- Escrow remains in `locked` status
- No `escrow_release` transaction created
- No event created

### ATOM-04 Split failure does not leave partial state
**Setup:** Lock escrow of `amount: 100`. Attempt to split with amounts that don't sum to 100.
**Action:** Query all tables.
**Expected:** Escrow remains in `locked` status. No transactions or events created.

### ATOM-05 Rebuttal with claim_status_update is atomic
**Setup:** File a claim. Attempt to submit a rebuttal with a foreign key violation in `agent_id`.
**Action:** Query `court_rebuttals`, `court_claims`, and `events` tables.
**Expected:** No rebuttal row, claim status unchanged, no event created.

---

## Category 18: Concurrency

### CONC-01 Concurrent escrow locks serialize correctly
**Setup:** Create account for alice with `balance: 100`.
**Action:** Send two concurrent escrow lock requests for different tasks, each with `amount: 60`.
**Expected:**
- Exactly one succeeds with `201 Created`
- Exactly one fails with `402 INSUFFICIENT_FUNDS`
- alice's balance is `40` (100 - 60)

### CONC-02 Concurrent duplicate agent registrations are safe
**Setup:** Prepare two identical registration requests with the same `public_key`.
**Action:** Send both simultaneously.
**Expected:**
- Exactly one `201 Created`
- Exactly one `409 PUBLIC_KEY_EXISTS`
- No duplicate rows for the public key

### CONC-03 Concurrent credits with same reference are idempotent
**Setup:** Create account for alice.
**Action:** Send two identical credit requests concurrently (same `account_id`, `amount`, `reference`).
**Expected:**
- Both return `200 OK` with the same `tx_id` and `balance_after`
- Balance is credited exactly once

---

## Category 19: HTTP Method and Content Type Misuse

### HTTP-01 Wrong method on defined routes is blocked
**Action:** Send unsupported HTTP methods:
- `GET /identity/agents` (POST only)
- `PUT /identity/agents`
- `DELETE /identity/agents`
- `GET /bank/accounts` (POST only)
- `GET /bank/credit` (POST only)
- `GET /bank/escrow/lock` (POST only)
- `GET /bank/escrow/release` (POST only)
- `GET /bank/escrow/split` (POST only)
- `GET /board/tasks` (POST only — note: this is the write endpoint, not a list endpoint)
- `GET /board/bids` (POST only)
- `GET /board/tasks/{task_id}/status` (POST only)
- `GET /board/assets` (POST only)
- `GET /reputation/feedback` (POST only)
- `GET /court/claims` (POST only)
- `GET /court/rebuttals` (POST only)
- `GET /court/rulings` (POST only)
- `POST /health` (GET only)
**Expected:** `405`, `error = METHOD_NOT_ALLOWED` for each

### HTTP-02 Wrong content type on POST endpoints
**Action:** Send `Content-Type: text/plain` with JSON-looking body to `POST /identity/agents`.
**Expected:** `415`, `error = UNSUPPORTED_MEDIA_TYPE`

### HTTP-03 Oversized request body
**Action:** Send a body exceeding configured `request.max_body_size` to `POST /identity/agents`.
**Expected:** `413`, `error = PAYLOAD_TOO_LARGE`

### HTTP-04 Malformed JSON body on various endpoints
**Action:** Send truncated/invalid JSON to `POST /bank/credit`, `POST /board/tasks`, `POST /court/claims`.
**Expected:** `400`, `error = INVALID_JSON` for each

---

## Category 20: Cross-Cutting Security Assertions

### SEC-01 Error envelope consistency
**Action:** For at least one failing test per error code, assert response has exactly:
- top-level `error` (string)
- top-level `message` (string)
- top-level `details` (object)
**Expected:** All failures comply. `details` is an object (may be empty `{}`).

### SEC-02 No internal error leakage
**Action:** Trigger representative failures (`INVALID_JSON`, `MISSING_FIELD`, `ACCOUNT_NOT_FOUND`, `ESCROW_NOT_FOUND`, `INSUFFICIENT_FUNDS`, `FOREIGN_KEY_VIOLATION`, `AMOUNT_MISMATCH`).
**Expected:** `message` never includes stack traces, SQL fragments, file paths, or driver internals.

### SEC-03 IDs in responses match expected formats
**Action:** Perform writes across all domains.
**Expected:**
- Every returned `event_id` is a positive integer
- All domain IDs in responses match the format passed in the request

### SEC-04 SQL injection string in ID fields
**Action:** Send `agent_id = "' OR '1'='1"` to `POST /identity/agents` and `account_id = "'; DROP TABLE bank_accounts;--"` to `POST /bank/credit`.
**Expected:** Requests fail with appropriate error codes (400 or 404). No SQL injection occurs. Database integrity is preserved.

### SEC-05 No endpoint returns 500 for any documented error scenario
**Action:** Run all tests in this document.
**Expected:** No response has status code `500`.

---

## Release Gate Checklist

Service is release-ready only if:

1. All tests in this document pass.
2. No test marked deterministic has alternate acceptable behavior.
3. No endpoint returns `500` in any test scenario.
4. All failing responses conform to the required error envelope.
5. Every mutating endpoint produces exactly one event per successful write.

---

## Coverage Summary

| Category | IDs | Count |
|----------|-----|-------|
| Agent Registration | AGT-01 to AGT-13 | 13 |
| Account Creation | ACCT-01 to ACCT-09 | 9 |
| Credit | CR-01 to CR-11 | 11 |
| Escrow Lock | ELOCK-01 to ELOCK-15 | 15 |
| Escrow Release | EREL-01 to EREL-09 | 9 |
| Escrow Split | ESPL-01 to ESPL-16 | 16 |
| Task Creation | TASK-01 to TASK-09 | 9 |
| Bid Submission | BID-01 to BID-07 | 7 |
| Task Status Update | TSTAT-01 to TSTAT-14 | 14 |
| Asset Recording | ASSET-01 to ASSET-07 | 7 |
| Feedback Submission | FB-01 to FB-10 | 10 |
| Dispute Claims | CLM-01 to CLM-08 | 8 |
| Rebuttals | REB-01 to REB-09 | 9 |
| Rulings | RUL-01 to RUL-10 | 10 |
| Health | HLTH-01 to HLTH-04 | 4 |
| Event Integrity | EVT-01 to EVT-05 | 5 |
| Atomicity | ATOM-01 to ATOM-05 | 5 |
| Concurrency | CONC-01 to CONC-03 | 3 |
| HTTP Misuse | HTTP-01 to HTTP-04 | 4 |
| Cross-Cutting Security | SEC-01 to SEC-05 | 5 |
| **Total** |  | **178** |

| Endpoint | Covered By |
|----------|------------|
| `GET /health` | HLTH-01 to HLTH-04 |
| `POST /identity/agents` | AGT-01 to AGT-13, CONC-02, EVT-02, EVT-04, SEC-04 |
| `POST /bank/accounts` | ACCT-01 to ACCT-09, ATOM-01, EVT-05 |
| `POST /bank/credit` | CR-01 to CR-11, EVT-02, SEC-04, CONC-03 |
| `POST /bank/escrow/lock` | ELOCK-01 to ELOCK-15, ATOM-02, CONC-01 |
| `POST /bank/escrow/release` | EREL-01 to EREL-09, ATOM-03 |
| `POST /bank/escrow/split` | ESPL-01 to ESPL-16, ATOM-04 |
| `POST /board/tasks` | TASK-01 to TASK-09, EVT-03 |
| `POST /board/bids` | BID-01 to BID-07 |
| `POST /board/tasks/{task_id}/status` | TSTAT-01 to TSTAT-14 |
| `POST /board/assets` | ASSET-01 to ASSET-07 |
| `POST /reputation/feedback` | FB-01 to FB-10 |
| `POST /court/claims` | CLM-01 to CLM-08 |
| `POST /court/rebuttals` | REB-01 to REB-09, ATOM-05 |
| `POST /court/rulings` | RUL-01 to RUL-10 |
