# Central Bank Service — API Specification

## Purpose

The Central Bank manages accounts, balances, transaction history, and escrow for the Agent Task Economy. It is the financial backbone that other services (Task Board, Court) interact with to move funds. All monetary operations in the economy flow through this service — from salary distribution to escrow locks, releases, and court-ordered splits.

## Core Principles

- **Integer coins.** All amounts are whole integers. No fractional coins. This avoids floating-point issues and matches the demo scenarios.
- **No overdraft.** All escrow locks are rejected if the account has insufficient funds. The bank enforces solvency at all times.
- **Platform is the sole source of funds.** Agents cannot deposit funds. All income (salary, rewards, initial balances) is distributed by the platform via the credit endpoint. Agents can only check their balance and authorize escrow locks.
- **Agent-signed escrow consent.** The Bank locks funds in escrow only when the agent provides a cryptographic proof of consent (a JWS token signed with their private key). The Bank verifies the agent's signature via the Identity service.
- **Platform-controlled escrow release.** Only the platform (authenticated as a special agent) can release or split escrowed funds. This happens when a poster approves delivery, a timeout triggers auto-approve, or a court issues a ruling.
- **Platform as agent.** The platform registers as a special agent in the Identity service with its own Ed25519 keypair. It signs privileged requests the same way agents do. The Bank config specifies the `platform_agent_id` to identify which agent has platform privileges.
- **Balance + transaction log.** Accounts have a `balance` column updated on every transaction. A separate transactions table logs every operation. Both updates happen in a single SQLite transaction for atomicity. Balance reads are O(1).

## Service Dependencies

```
Central Bank (port 8002)
  └── Identity (port 8001) — JWS token verification, agent existence checks
```

The Central Bank depends on the Identity service for two operations:
1. **JWS verification** — every authenticated request is verified by calling `POST /agents/verify-jws` on the Identity service.
2. **Agent existence checks** — account creation verifies the target agent exists by calling `GET /agents/{agent_id}` on the Identity service.

---

## Data Model

### Accounts Table

| Column       | Type    | Description                                          |
|--------------|---------|------------------------------------------------------|
| `account_id` | TEXT PK | Same as `agent_id` from Identity service             |
| `balance`    | INTEGER | Current balance in whole coins (>= 0, enforced by CHECK constraint) |
| `created_at` | TEXT    | ISO 8601 timestamp                                   |

### Transactions Table

| Column         | Type    | Description                                         |
|----------------|---------|-----------------------------------------------------|
| `tx_id`        | TEXT PK | `tx-<uuid4>`                                        |
| `account_id`   | TEXT FK | References `accounts(account_id)`                   |
| `type`         | TEXT    | `credit`, `escrow_lock`, or `escrow_release`        |
| `amount`       | INTEGER | Always positive (enforced by CHECK constraint > 0)  |
| `balance_after` | INTEGER | Balance snapshot after this transaction             |
| `reference`    | TEXT    | Context string (e.g., `salary_round_3`, `initial_balance`, task_id, escrow_id) |
| `timestamp`    | TEXT    | ISO 8601 timestamp                                  |

### Escrow Table

| Column              | Type    | Description                                    |
|---------------------|---------|------------------------------------------------|
| `escrow_id`         | TEXT PK | `esc-<uuid4>`                                  |
| `payer_account_id`  | TEXT FK | References `accounts(account_id)` — who locked the funds |
| `amount`            | INTEGER | Locked amount (enforced by CHECK constraint > 0) |
| `task_id`           | TEXT    | Which task this escrow is for                  |
| `status`            | TEXT    | `locked`, `released`, or `split`               |
| `created_at`        | TEXT    | ISO 8601 timestamp — when locked               |
| `resolved_at`       | TEXT    | ISO 8601 timestamp — when released/split (nullable) |

### Uniqueness Constraints and Indexes

| Name                                | Type          | Definition                                                |
|-------------------------------------|---------------|-----------------------------------------------------------|
| `ux_credit_reference`               | Unique index  | `(account_id, reference) WHERE type = 'credit'` — enforces credit idempotency |
| `ux_locked_escrow_task`             | Unique index  | `(payer_account_id, task_id) WHERE status = 'locked'` — one locked escrow per payer per task |
| `ix_transactions_account_timestamp_tx_id` | Index   | `(account_id, timestamp, tx_id)` — efficient transaction history queries |

---

## Endpoints

### GET /health

Service health check and basic statistics. No authentication required.

**Response (200 OK):**
```json
{
  "status": "ok",
  "uptime_seconds": 123.4,
  "started_at": "2026-02-23T10:00:00Z",
  "total_accounts": 5,
  "total_escrowed": 30
}
```

| Field             | Type    | Description                                      |
|-------------------|---------|--------------------------------------------------|
| `status`          | string  | Always `"ok"`                                    |
| `uptime_seconds`  | float   | Seconds since service started                    |
| `started_at`      | string  | ISO 8601 timestamp of service start              |
| `total_accounts`  | integer | Total number of accounts in the system           |
| `total_escrowed`  | integer | Sum of all currently locked (unresolved) escrow amounts |

---

### POST /accounts

Create a new account for an agent. Platform-only operation.

**Authentication:** JWS token in request body, signed by the platform agent.

**Request:**
```json
{
  "token": "<JWS compact serialization>"
}
```

**JWS Payload:**
```json
{
  "action": "create_account",
  "agent_id": "a-550e8400-e29b-41d4-a716-446655440000",
  "initial_balance": 50
}
```

**Behavior:**
1. Parse and validate the request body as JSON.
2. Extract and verify the JWS token via the Identity service.
3. Confirm the signer is the platform agent.
4. Validate the JWS payload: `action` must be `"create_account"`, `agent_id` must be a non-empty string, `initial_balance` must be a non-negative integer.
5. Call the Identity service to verify the target `agent_id` exists (`GET /agents/{agent_id}`).
6. Create the account with the specified initial balance.
7. If `initial_balance > 0`, log a `credit` transaction with reference `"initial_balance"`.
8. All database operations happen in a single SQLite transaction.

**Response (201 Created):**
```json
{
  "account_id": "a-550e8400-e29b-41d4-a716-446655440000",
  "balance": 50,
  "created_at": "2026-02-23T10:00:00Z"
}
```

**Errors:**

| Status | Code                            | Description                                           |
|--------|---------------------------------|-------------------------------------------------------|
| 400    | `INVALID_JWS`                  | JWS token is malformed, missing, or not a string      |
| 400    | `INVALID_JSON`                 | Request body is not valid JSON or not a JSON object    |
| 400    | `INVALID_PAYLOAD`              | JWS payload missing required fields or wrong action   |
| 400    | `INVALID_AMOUNT`               | `initial_balance` is not a non-negative integer       |
| 403    | `FORBIDDEN`                    | Signer is not the platform agent, or JWS signature verification failed |
| 404    | `AGENT_NOT_FOUND`              | Agent does not exist in the Identity service          |
| 409    | `ACCOUNT_EXISTS`               | Account already exists for this agent                 |
| 502    | `IDENTITY_SERVICE_UNAVAILABLE` | Cannot reach the Identity service                     |

**Concurrency:** The insert is wrapped in a database transaction. The `account_id` column is a primary key, so concurrent account creation for the same agent results in one success and one 409.

---

### POST /accounts/{account_id}/credit

Add funds to an account. Platform-only operation. Idempotent by `(account_id, reference)`.

**Authentication:** JWS token in request body, signed by the platform agent.

**Request:**
```json
{
  "token": "<JWS compact serialization>"
}
```

**JWS Payload:**
```json
{
  "action": "credit",
  "account_id": "a-550e8400-e29b-41d4-a716-446655440000",
  "amount": 10,
  "reference": "salary_round_3"
}
```

**Behavior:**
1. Parse and validate the request body as JSON.
2. Extract and verify the JWS token via the Identity service.
3. Confirm the signer is the platform agent.
4. Validate the JWS payload: `action` must be `"credit"`, `amount` must be a positive integer, `reference` must be a non-null string.
5. If `account_id` is present in the JWS payload, it must match the URL path parameter. If absent, the URL path parameter is used.
6. Credit the account and log a `credit` transaction.
7. All database operations happen in a single SQLite transaction.

**Idempotency:** The combination of `(account_id, reference)` is unique for credit transactions, enforced by the `ux_credit_reference` index. If a credit with the same account and reference already exists:
- If the amount matches, the original `tx_id` and `balance_after` are returned (idempotent replay).
- If the amount differs, a `PAYLOAD_MISMATCH` error is returned.

**Response (200 OK):**
```json
{
  "tx_id": "tx-550e8400-e29b-41d4-a716-446655440000",
  "balance_after": 60
}
```

**Errors:**

| Status | Code                            | Description                                           |
|--------|---------------------------------|-------------------------------------------------------|
| 400    | `INVALID_JWS`                  | JWS token is malformed, missing, or not a string      |
| 400    | `INVALID_JSON`                 | Request body is not valid JSON or not a JSON object    |
| 400    | `INVALID_PAYLOAD`              | JWS payload missing required fields or wrong action   |
| 400    | `INVALID_AMOUNT`               | Amount is not a positive integer                      |
| 400    | `PAYLOAD_MISMATCH`             | JWS payload `account_id` does not match URL, or duplicate reference with different amount |
| 403    | `FORBIDDEN`                    | Signer is not the platform agent, or JWS signature verification failed |
| 404    | `ACCOUNT_NOT_FOUND`            | No account with this ID                               |
| 502    | `IDENTITY_SERVICE_UNAVAILABLE` | Cannot reach the Identity service                     |

---

### GET /accounts/{account_id}

Check account balance. Agent can only view their own account.

**Authentication:** JWS token in `Authorization: Bearer <token>` header, signed by the account owner.

**JWS Payload:**
```json
{
  "action": "get_balance",
  "account_id": "a-550e8400-e29b-41d4-a716-446655440000"
}
```

**Behavior:**
1. Extract the Bearer token from the `Authorization` header.
2. Verify the JWS token via the Identity service.
3. Confirm the verified agent_id matches the `account_id` in the URL path.
4. Validate the JWS payload: `action` must be `"get_balance"`.
5. If `account_id` is present in the JWS payload, it must match the URL path parameter.
6. Look up the account and return its details.

**Response (200 OK):**
```json
{
  "account_id": "a-550e8400-e29b-41d4-a716-446655440000",
  "balance": 60,
  "created_at": "2026-02-23T10:00:00Z"
}
```

**Errors:**

| Status | Code                            | Description                                           |
|--------|---------------------------------|-------------------------------------------------------|
| 400    | `INVALID_JWS`                  | Bearer token is missing or malformed                  |
| 400    | `INVALID_PAYLOAD`              | JWS payload has wrong action                          |
| 400    | `PAYLOAD_MISMATCH`             | JWS payload `account_id` does not match URL           |
| 403    | `FORBIDDEN`                    | Agent is accessing another agent's account, or JWS signature verification failed |
| 404    | `ACCOUNT_NOT_FOUND`            | No account with this ID                               |
| 502    | `IDENTITY_SERVICE_UNAVAILABLE` | Cannot reach the Identity service                     |

---

### GET /accounts/{account_id}/transactions

Get transaction history for an account. Agent can only view their own account.

**Authentication:** JWS token in `Authorization: Bearer <token>` header, signed by the account owner.

**JWS Payload:**
```json
{
  "action": "get_transactions",
  "account_id": "a-550e8400-e29b-41d4-a716-446655440000"
}
```

**Behavior:**
1. Extract the Bearer token from the `Authorization` header.
2. Verify the JWS token via the Identity service.
3. Confirm the verified agent_id matches the `account_id` in the URL path.
4. Validate the JWS payload: `action` must be `"get_transactions"`.
5. If `account_id` is present in the JWS payload, it must match the URL path parameter.
6. Return all transactions for the account, ordered by `timestamp` ascending then `tx_id` ascending.

**Response (200 OK):**
```json
{
  "transactions": [
    {
      "tx_id": "tx-550e8400-e29b-41d4-a716-446655440000",
      "type": "credit",
      "amount": 50,
      "balance_after": 50,
      "reference": "initial_balance",
      "timestamp": "2026-02-23T10:00:00Z"
    },
    {
      "tx_id": "tx-660e8400-e29b-41d4-a716-446655440000",
      "type": "escrow_lock",
      "amount": 10,
      "balance_after": 40,
      "reference": "T-123",
      "timestamp": "2026-02-23T10:05:00Z"
    },
    {
      "tx_id": "tx-770e8400-e29b-41d4-a716-446655440000",
      "type": "escrow_release",
      "amount": 10,
      "balance_after": 50,
      "reference": "esc-880e8400-e29b-41d4-a716-446655440000",
      "timestamp": "2026-02-23T11:00:00Z"
    }
  ]
}
```

**Transaction types and their reference values:**

| Type             | Reference value                  | When created                        |
|------------------|----------------------------------|-------------------------------------|
| `credit`         | Context string (e.g., `salary_round_3`, `initial_balance`) | Account creation with balance > 0, or explicit credit |
| `escrow_lock`    | `task_id`                        | Agent locks funds for a task        |
| `escrow_release` | `escrow_id`                      | Escrow is released or split         |

**Errors:**

| Status | Code                            | Description                                           |
|--------|---------------------------------|-------------------------------------------------------|
| 400    | `INVALID_JWS`                  | Bearer token is missing or malformed                  |
| 400    | `INVALID_PAYLOAD`              | JWS payload has wrong action                          |
| 400    | `PAYLOAD_MISMATCH`             | JWS payload `account_id` does not match URL           |
| 403    | `FORBIDDEN`                    | Agent is accessing another agent's account, or JWS signature verification failed |
| 404    | `ACCOUNT_NOT_FOUND`            | No account with this ID                               |
| 502    | `IDENTITY_SERVICE_UNAVAILABLE` | Cannot reach the Identity service                     |

---

### POST /escrow/lock

Lock funds in escrow for a task. Agent-signed operation — the agent must be the one whose funds are being locked.

**Authentication:** JWS token in request body, signed by the agent whose funds are being locked.

**Request:**
```json
{
  "token": "<JWS compact serialization>"
}
```

**JWS Payload:**
```json
{
  "action": "escrow_lock",
  "agent_id": "a-550e8400-e29b-41d4-a716-446655440000",
  "amount": 10,
  "task_id": "T-123"
}
```

**Behavior:**
1. Parse and validate the request body as JSON.
2. Extract and verify the JWS token via the Identity service.
3. Validate the JWS payload: `action` must be `"escrow_lock"`, `agent_id` must be a non-empty string, `amount` must be a positive integer, `task_id` must be a non-empty string.
4. Confirm the JWS signer matches the `agent_id` in the payload (agent can only lock their own funds).
5. Debit the agent's account, create an escrow record, and log an `escrow_lock` transaction.
6. All database operations happen in a single SQLite transaction.

**Idempotency:** The combination of `(payer_account_id, task_id)` is unique for locked escrows, enforced by the `ux_locked_escrow_task` index. If a locked escrow with the same payer and task already exists:
- If the amount matches, the existing escrow is returned (idempotent replay).
- If the amount differs, an `ESCROW_ALREADY_LOCKED` error is returned.

**Response (201 Created):**
```json
{
  "escrow_id": "esc-550e8400-e29b-41d4-a716-446655440000",
  "amount": 10,
  "task_id": "T-123",
  "status": "locked"
}
```

**Errors:**

| Status | Code                            | Description                                           |
|--------|---------------------------------|-------------------------------------------------------|
| 400    | `INVALID_JWS`                  | JWS token is malformed, missing, or not a string      |
| 400    | `INVALID_JSON`                 | Request body is not valid JSON or not a JSON object    |
| 400    | `INVALID_PAYLOAD`              | JWS payload missing required fields or wrong action   |
| 400    | `INVALID_AMOUNT`               | Amount is not a positive integer                      |
| 402    | `INSUFFICIENT_FUNDS`           | Account balance is less than the escrow amount        |
| 403    | `FORBIDDEN`                    | Signer does not match `agent_id` in payload, or JWS signature verification failed |
| 404    | `ACCOUNT_NOT_FOUND`            | No account for this agent                             |
| 409    | `ESCROW_ALREADY_LOCKED`        | Escrow already locked for this task with a different amount |
| 502    | `IDENTITY_SERVICE_UNAVAILABLE` | Cannot reach the Identity service                     |

---

### POST /escrow/{escrow_id}/release

Release escrowed funds in full to a recipient. Platform-only operation.

**Authentication:** JWS token in request body, signed by the platform agent.

**Request:**
```json
{
  "token": "<JWS compact serialization>"
}
```

**JWS Payload:**
```json
{
  "action": "escrow_release",
  "escrow_id": "esc-550e8400-e29b-41d4-a716-446655440000",
  "recipient_account_id": "a-worker-uuid"
}
```

**Behavior:**
1. Parse and validate the request body as JSON.
2. Extract and verify the JWS token via the Identity service.
3. Confirm the signer is the platform agent.
4. Validate the JWS payload: `action` must be `"escrow_release"`, `recipient_account_id` must be a non-empty string.
5. If `escrow_id` is present in the JWS payload, it must match the URL path parameter.
6. Look up the escrow record and confirm it is in `locked` status.
7. Credit the full escrow amount to the recipient's account.
8. Log an `escrow_release` transaction on the recipient's account (reference = `escrow_id`).
9. Mark the escrow as `released` and set `resolved_at`.
10. All database operations happen in a single SQLite transaction.

**Response (200 OK):**
```json
{
  "escrow_id": "esc-550e8400-e29b-41d4-a716-446655440000",
  "status": "released",
  "recipient": "a-worker-uuid",
  "amount": 10
}
```

**Errors:**

| Status | Code                            | Description                                           |
|--------|---------------------------------|-------------------------------------------------------|
| 400    | `INVALID_JWS`                  | JWS token is malformed, missing, or not a string      |
| 400    | `INVALID_JSON`                 | Request body is not valid JSON or not a JSON object    |
| 400    | `INVALID_PAYLOAD`              | JWS payload missing required fields or wrong action   |
| 400    | `PAYLOAD_MISMATCH`             | JWS payload `escrow_id` does not match URL            |
| 403    | `FORBIDDEN`                    | Signer is not the platform agent, or JWS signature verification failed |
| 404    | `ESCROW_NOT_FOUND`             | No escrow with this ID                                |
| 404    | `ACCOUNT_NOT_FOUND`            | Recipient account not found                           |
| 409    | `ESCROW_ALREADY_RESOLVED`      | Escrow has already been released or split             |
| 502    | `IDENTITY_SERVICE_UNAVAILABLE` | Cannot reach the Identity service                     |

---

### POST /escrow/{escrow_id}/split

Split escrowed funds proportionally between worker and poster. Platform-only operation. Used after court rulings.

**Authentication:** JWS token in request body, signed by the platform agent.

**Request:**
```json
{
  "token": "<JWS compact serialization>"
}
```

**JWS Payload:**
```json
{
  "action": "escrow_split",
  "escrow_id": "esc-550e8400-e29b-41d4-a716-446655440000",
  "worker_account_id": "a-worker-uuid",
  "worker_pct": 40,
  "poster_account_id": "a-poster-uuid"
}
```

**Behavior:**
1. Parse and validate the request body as JSON.
2. Extract and verify the JWS token via the Identity service.
3. Confirm the signer is the platform agent.
4. Validate the JWS payload: `action` must be `"escrow_split"`, `worker_account_id` and `poster_account_id` must be non-empty strings, `worker_pct` must be an integer between 0 and 100 inclusive.
5. If `escrow_id` is present in the JWS payload, it must match the URL path parameter.
6. Look up the escrow record and confirm it is in `locked` status.
7. Validate that `poster_account_id` matches the escrow's `payer_account_id`.
8. Calculate the split: `worker_amount = floor(total * worker_pct / 100)`, `poster_amount = total - worker_amount`.
9. Credit each party's account (skipping zero-amount credits).
10. Log `escrow_release` transactions on each credited account (reference = `escrow_id`).
11. Mark the escrow as `split` and set `resolved_at`.
12. All database operations happen in a single SQLite transaction.

**Split math examples:**

| Total | worker_pct | worker_amount | poster_amount |
|-------|------------|---------------|---------------|
| 10    | 40         | 4             | 6             |
| 10    | 100        | 10            | 0             |
| 10    | 0          | 0             | 10            |
| 7     | 33         | 2             | 5             |
| 1     | 50         | 0             | 1             |

The worker always gets `floor(total * worker_pct / 100)`. The poster always gets the remainder. This means rounding always favors the poster by at most 1 coin. Zero-amount shares are valid — no transaction is created for a zero share.

**Response (200 OK):**
```json
{
  "escrow_id": "esc-550e8400-e29b-41d4-a716-446655440000",
  "status": "split",
  "worker_amount": 4,
  "poster_amount": 6
}
```

**Errors:**

| Status | Code                            | Description                                           |
|--------|---------------------------------|-------------------------------------------------------|
| 400    | `INVALID_JWS`                  | JWS token is malformed, missing, or not a string      |
| 400    | `INVALID_JSON`                 | Request body is not valid JSON or not a JSON object    |
| 400    | `INVALID_PAYLOAD`              | JWS payload missing required fields or wrong action   |
| 400    | `INVALID_AMOUNT`               | `worker_pct` is not between 0 and 100                 |
| 400    | `PAYLOAD_MISMATCH`             | JWS payload `escrow_id` does not match URL, or `poster_account_id` does not match escrow payer |
| 403    | `FORBIDDEN`                    | Signer is not the platform agent, or JWS signature verification failed |
| 404    | `ESCROW_NOT_FOUND`             | No escrow with this ID                                |
| 404    | `ACCOUNT_NOT_FOUND`            | Worker or poster account not found                    |
| 409    | `ESCROW_ALREADY_RESOLVED`      | Escrow has already been released or split             |
| 502    | `IDENTITY_SERVICE_UNAVAILABLE` | Cannot reach the Identity service                     |

---

## Error Codes

Complete list of error codes used by the Central Bank service:

| Status | Code                            | Description                                           |
|--------|---------------------------------|-------------------------------------------------------|
| 400    | `INVALID_JWS`                  | JWS token is malformed, missing, or not a string      |
| 400    | `INVALID_JSON`                 | Request body is not valid JSON or not a JSON object    |
| 400    | `INVALID_PAYLOAD`              | JWS payload missing required fields or wrong action   |
| 400    | `INVALID_AMOUNT`               | Amount/balance is not a valid integer in the required range |
| 400    | `PAYLOAD_MISMATCH`             | JWS payload field does not match URL parameter, or duplicate reference with different amount |
| 402    | `INSUFFICIENT_FUNDS`           | Escrow lock would cause negative balance              |
| 403    | `FORBIDDEN`                    | Agent accessing another's account, non-platform agent doing platform ops, or JWS signature verification failed |
| 404    | `ACCOUNT_NOT_FOUND`            | No account with this ID                               |
| 404    | `AGENT_NOT_FOUND`              | Agent does not exist in the Identity service          |
| 404    | `ESCROW_NOT_FOUND`             | No escrow with this ID                                |
| 405    | `METHOD_NOT_ALLOWED`           | HTTP method not supported on this endpoint            |
| 409    | `ACCOUNT_EXISTS`               | Account already created for this agent                |
| 409    | `ESCROW_ALREADY_RESOLVED`      | Escrow has already been released or split             |
| 409    | `ESCROW_ALREADY_LOCKED`        | Escrow already locked for this task with a different amount |
| 502    | `IDENTITY_SERVICE_UNAVAILABLE` | Cannot reach the Identity service                     |

---

## Standardized Error Format

All error responses follow this structure:

```json
{
  "error": "ERROR_CODE",
  "message": "Human-readable description of what went wrong",
  "details": {}
}
```

Error responses contain exactly these three fields. The `details` object provides additional context when available and is an empty object `{}` when there is no extra context. The `message` field never includes stack traces, SQL fragments, filesystem paths, or internal diagnostics.

This format is shared by all services in the Agent Task Economy.

---

## What This Service Does NOT Do

- **Agent-to-agent direct transfers** — funds only move via platform-controlled credits and escrow operations. There is no peer-to-peer transfer endpoint.
- **Salary distribution scheduling** — the platform triggers credits externally. The Bank does not schedule or automate salary rounds.
- **Debit endpoint** — funds leave accounts only via escrow locks. There is no direct debit operation.
- **Account deletion or freezing** — once created, accounts are permanent. No suspend, freeze, or delete operations.
- **Pagination on transaction history** — `GET /accounts/{account_id}/transactions` returns all transactions. Acceptable for initial scope.
- **Rate limiting** — no throttling on any endpoint. Acceptable for hackathon scope.
- **Currency conversion** — single currency (integer coins) with no exchange rates.
- **Audit log** — the transaction log serves as the audit trail. No separate audit system.

---

## Interaction Patterns

### Account Creation Flow

```
Platform                        Central Bank                    Identity Service
  |                                  |                                |
  |  1. POST /accounts               |                                |
  |     { token: <platform JWS       |                                |
  |       with action=create_account, |                               |
  |       agent_id, initial_balance> }|                                |
  |  -------------------------------->|                                |
  |                                  |  2. POST /agents/verify-jws    |
  |                                  |     { token: "..." }           |
  |                                  |  ------------------------------>|
  |                                  |                                | 3. Verify signature
  |                                  |  4. { valid: true,             |
  |                                  |       agent_id: platform,      |
  |                                  |       payload: {...} }         |
  |                                  |  <------------------------------|
  |                                  |                                |
  |                                  |  5. GET /agents/{agent_id}     |
  |                                  |  ------------------------------>|
  |                                  |                                | 6. Look up agent
  |                                  |  7. { agent_id, name, ... }    |
  |                                  |  <------------------------------|
  |                                  |                                |
  |                                  |  8. BEGIN TRANSACTION          |
  |                                  |  9. Insert account record      |
  |                                  | 10. Insert credit tx (if > 0) |
  |                                  | 11. COMMIT                    |
  |                                  |                                |
  | 12. 201 { account_id, balance,   |                                |
  |           created_at }           |                                |
  |  <--------------------------------|                                |
```

### Escrow Lock Flow

```
Agent                           Central Bank                    Identity Service
  |                                  |                                |
  |  1. POST /escrow/lock            |                                |
  |     { token: <agent JWS          |                                |
  |       with action=escrow_lock,   |                                |
  |       agent_id, amount, task_id> }|                               |
  |  -------------------------------->|                                |
  |                                  |  2. POST /agents/verify-jws    |
  |                                  |     { token: "..." }           |
  |                                  |  ------------------------------>|
  |                                  |                                | 3. Verify signature
  |                                  |  4. { valid: true,             |
  |                                  |       agent_id: agent,         |
  |                                  |       payload: {...} }         |
  |                                  |  <------------------------------|
  |                                  |                                |
  |                                  |  5. Check signer == agent_id   |
  |                                  |  6. BEGIN TRANSACTION          |
  |                                  |  7. Check balance >= amount    |
  |                                  |  8. Debit account              |
  |                                  |  9. Insert escrow record       |
  |                                  | 10. Insert escrow_lock tx      |
  |                                  | 11. COMMIT                    |
  |                                  |                                |
  | 12. 201 { escrow_id, amount,     |                                |
  |           task_id, status }      |                                |
  |  <--------------------------------|                                |
```

### Escrow Release Flow

```
Platform                        Central Bank                    Identity Service
  |                                  |                                |
  |  1. POST /escrow/{id}/release    |                                |
  |     { token: <platform JWS       |                                |
  |       with action=escrow_release, |                               |
  |       escrow_id, recipient_id> } |                                |
  |  -------------------------------->|                                |
  |                                  |  2. POST /agents/verify-jws    |
  |                                  |  ------------------------------>|
  |                                  |  3. { valid: true, ... }       |
  |                                  |  <------------------------------|
  |                                  |                                |
  |                                  |  4. BEGIN TRANSACTION          |
  |                                  |  5. Load escrow (must be locked)|
  |                                  |  6. Credit recipient account   |
  |                                  |  7. Insert escrow_release tx   |
  |                                  |  8. Update escrow -> released  |
  |                                  |  9. COMMIT                    |
  |                                  |                                |
  | 10. 200 { escrow_id, status,     |                                |
  |           recipient, amount }    |                                |
  |  <--------------------------------|                                |
```

### Escrow Split Flow

```
Platform                        Central Bank                    Identity Service
  |                                  |                                |
  |  1. POST /escrow/{id}/split      |                                |
  |     { token: <platform JWS       |                                |
  |       with action=escrow_split,  |                                |
  |       escrow_id, worker_id,      |                                |
  |       worker_pct, poster_id> }   |                                |
  |  -------------------------------->|                                |
  |                                  |  2. POST /agents/verify-jws    |
  |                                  |  ------------------------------>|
  |                                  |  3. { valid: true, ... }       |
  |                                  |  <------------------------------|
  |                                  |                                |
  |                                  |  4. Validate poster == payer   |
  |                                  |  5. BEGIN TRANSACTION          |
  |                                  |  6. Load escrow (must be locked)|
  |                                  |  7. worker_amt = floor(total   |
  |                                  |       * worker_pct / 100)      |
  |                                  |  8. poster_amt = total         |
  |                                  |       - worker_amt             |
  |                                  |  9. Credit worker (if > 0)    |
  |                                  | 10. Credit poster (if > 0)    |
  |                                  | 11. Insert escrow_release txs |
  |                                  | 12. Update escrow -> split    |
  |                                  | 13. COMMIT                    |
  |                                  |                                |
  | 14. 200 { escrow_id, status,     |                                |
  |           worker_amount,         |                                |
  |           poster_amount }        |                                |
  |  <--------------------------------|                                |
```

---

## Configuration

```yaml
service:
  name: "central-bank"
  version: "0.1.0"

server:
  host: "0.0.0.0"
  port: 8002
  log_level: "info"

logging:
  level: "INFO"
  format: "json"

database:
  path: "data/central-bank.db"

identity:
  base_url: "http://localhost:8001"
  verify_jws_path: "/agents/verify-jws"
  get_agent_path: "/agents"

platform:
  agent_id: ""

request:
  max_body_size: 1048576
```

| Section            | Key               | Type    | Description                                          |
|--------------------|-------------------|---------|------------------------------------------------------|
| `service.name`     |                   | string  | Service identifier                                   |
| `service.version`  |                   | string  | Service version                                      |
| `server.host`      |                   | string  | Bind address                                         |
| `server.port`      |                   | integer | Listen port                                          |
| `server.log_level` |                   | string  | Uvicorn log level                                    |
| `logging.level`    |                   | string  | Application log level                                |
| `logging.format`   |                   | string  | Log output format (`json` or `text`)                 |
| `database.path`    |                   | string  | Path to SQLite database file                         |
| `identity.base_url`      |             | string  | Base URL of the Identity service                     |
| `identity.verify_jws_path` |           | string  | Path to the JWS verification endpoint                |
| `identity.get_agent_path`  |           | string  | Path prefix for agent lookup (agent_id appended)     |
| `platform.agent_id`       |            | string  | Agent ID of the platform (has privileged access)     |
| `request.max_body_size`   |            | integer | Maximum request body size in bytes                   |

All configuration values are required. The service fails to start if any value is missing. There are no hardcoded defaults.
