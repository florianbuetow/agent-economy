# Central Bank Service — API Specification

## Purpose

The Central Bank manages accounts, balances, transaction history, and escrow for the Agent Task Economy. It is the financial backbone that other services interact with to move funds. All monetary operations in the economy flow through this service — from account funding to escrow locks, releases, and court-ordered splits. Settlement (releasing or splitting escrow) is orchestrated exclusively by the **Task Board** (R4): on approval, review-timeout auto-approve, cancel/expiry, and after a Court ruling, the Task Board calls this service's escrow endpoints. Court never calls the Central Bank directly. Platform-signed `credit` is also invoked by platform-side tooling (the `fund-feeder` CLI, UI treasury provisioning) outside the task lifecycle.

## Core Principles

- **Integer coins.** All amounts are whole integers. No fractional coins. This avoids floating-point issues and matches the demo scenarios.
- **No overdraft.** All escrow locks are rejected if the account has insufficient funds. The bank enforces solvency at all times.
- **Platform is the sole source of new money.** Agents cannot credit their own balance or another agent's balance. They may create their own account (always at zero balance — see `create_account` below) and consent to escrow locks. All non-zero funding (initial balances, salary/rewards, credits) is issued by the platform via `POST /accounts` (with a balance) or `POST /accounts/{id}/credit`.
- **Two-tier verification model.** Every mutating and read endpoint requires a JWS-signed request, but the verification path differs by operation tier:
  - **Agent-signed operations** — `escrow_lock`, `get_balance`, `get_transactions`, and (as a documented exception, see below) `create_account` in both of its modes — are verified via a network call to Identity's `POST /agents/verify-jws`.
  - **Platform-signed operations** — `credit`, `escrow_release`, `escrow_split` — are verified **locally** by the service's own registered `PlatformAgent.validate_certificate()`. No Identity round trip is made, so these three endpoints keep working through an Identity outage.
  - See the Central Bank Service Authentication Specification for the full model, including the endpoint-by-endpoint error precedence, which differs between the two tiers.
- **`create_account` is the one documented exception to "platform ops verify locally."** It always verifies via Identity (never locally), in both its self-service and platform-funded modes. Rationale: the endpoint has a hard Identity dependency regardless (the target agent's existence is checked via `GET /agents/{agent_id}`), so local verification would buy no outage resilience here, and distinguishing self-service from platform-funded creation requires the verified signer's identity either way. Revisit only if the existence check ever moves off Identity (e.g. to a gateway read).
- **Platform as agent.** The platform is not a config-file string — it is whichever agent the service's own `PlatformAgent` instance registers as at startup (loaded from the roster entry named `platform` via `agent_config_path`; see Configuration). There is no configured `platform.agent_id` fallback: if no `PlatformAgent` is registered, platform-authorization checks fail closed with `503 service_not_ready` rather than silently trusting nothing (or worse, an empty-string placeholder).
- **Balance + transaction log.** Accounts have a `balance` column updated on every transaction. A separate transactions table logs every operation. Both updates happen in a single database transaction on the DB Gateway (`BEGIN IMMEDIATE` … `COMMIT`), atomically. Balance reads are O(1).
- **Gateway-owned persistence.** The Central Bank does not open the SQLite database itself. Its production storage backend, `LedgerDbClient`, is a thin domain facade over the DB Gateway's HTTP API (`libs/service-clients` `GatewayClient`) — the gateway owns `economy.db` and is the only process that writes to it. An `InMemoryLedgerStore` also exists but is test/fallback infrastructure only; it is never wired into the production lifespan.

## Service Dependencies

```
Central Bank (port 8002)
  ├── Identity (port 8001) — JWS verification for agent-signed operations
  │                          (escrow_lock, get_balance, get_transactions, create_account);
  │                          agent-existence check for create_account
  └── DB Gateway (port 8007) — owns the shared SQLite database; every account,
                                transaction, and escrow read/write goes through
                                its HTTP API (bank_accounts / bank_transactions /
                                bank_escrow tables, docs/specifications/schema.sql)
```

The Central Bank depends on the Identity service for two operations, both scoped to agent-signed operations:
1. **JWS verification** — `escrow_lock`, `get_balance`, `get_transactions`, and `create_account` (both modes) are verified by calling `POST /agents/verify-jws` on the Identity service.
2. **Agent existence checks** — `create_account` verifies the target agent exists by calling `GET /agents/{agent_id}` on the Identity service.

`credit`, `escrow_release`, and `escrow_split` do **not** call Identity at all — they are verified locally via the registered `PlatformAgent`.

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
| `reference`    | TEXT    | Context string (e.g., `initial_balance`, a caller-supplied reference, task_id, escrow_id) |
| `timestamp`    | TEXT    | ISO 8601 timestamp                                  |

Note: on the DB Gateway, these tables are named `bank_accounts`, `bank_transactions`, and `bank_escrow` (`docs/specifications/schema.sql`). The Central Bank's own HTTP-facing field names above are unaffected by that internal naming.

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

**Degraded response (503 Service Unavailable):**

`total_accounts`/`total_escrowed` are read from the DB Gateway. If the gateway is unreachable, the endpoint does not crash with a `500` — it returns:

```json
{
  "error": "service_not_ready",
  "message": "Ledger's DB Gateway is unavailable",
  "details": {}
}
```

---

### POST /accounts

Create a new account. Two modes, both verified via Identity (see the Accepted Deviation note in Core Principles):

- **Self-service:** any registered agent may create their own account, at `initial_balance: 0` only.
- **Platform-funded:** the registered platform agent may create an account for any registered agent, at any non-negative `initial_balance`.

**Authentication:** JWS token in request body, verified via Identity's `POST /agents/verify-jws`.

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

**Behavior (in code order):**
1. Parse the request body as JSON; require a string `token` field.
2. Verify the JWS token via Identity's `POST /agents/verify-jws`.
3. Determine whether the verified signer is the platform: `caller_agent_id == <registered PlatformAgent's agent_id>`.
4. Validate the JWS payload: `action` must be `"create_account"`; `agent_id` must be a non-empty string.
5. **Non-platform signers may only create their own account** — if `agent_id != caller_agent_id`, reject with `403 forbidden`.
6. Validate `initial_balance`: required, must be a non-negative integer.
7. **Non-platform signers must pass `initial_balance: 0`** — a self-service request for a non-zero balance is rejected with `403 forbidden`.
8. Call Identity to verify the target `agent_id` exists (`GET /agents/{agent_id}`).
9. Create the account with the specified initial balance; if `initial_balance > 0`, log a `credit` transaction with reference `"initial_balance"`.
10. All database operations happen in a single DB Gateway transaction.

Because step 2 (remote signature verification) runs before steps 4–7 (payload validation), an invalid signature or an unreachable Identity service is reported (`403 forbidden` / `502 identity_service_unavailable`) even for requests whose payload is also malformed. This "verify-first" ordering is the mirror image of `credit`/`escrow_release`/`escrow_split` — see the Authentication Specification's Error Precedence section.

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
|--------|---------------------------------|--------------------------------------------------------|
| 400    | `invalid_jws`                  | JWS token is malformed, missing, or not a string       |
| 400    | `invalid_json`                 | Request body is not valid JSON or not a JSON object    |
| 400    | `invalid_payload`              | JWS payload missing required fields or wrong action    |
| 400    | `invalid_amount`               | `initial_balance` is not a non-negative integer        |
| 403    | `forbidden`                    | JWS signature verification failed; a non-platform signer named a different agent as `agent_id`; or a non-platform signer requested a non-zero `initial_balance` |
| 404    | `agent_not_found`              | Agent does not exist in the Identity service            |
| 409    | `account_exists`               | Account already exists for this agent                   |
| 502    | `identity_service_unavailable` | Cannot reach the Identity service                        |
| 503    | `service_not_ready`            | Ledger or Identity client not yet initialized            |

**Concurrency:** The insert is wrapped in a database transaction. The `account_id` column is a primary key, so concurrent account creation for the same agent results in one success and one `409`.

---

### POST /accounts/{account_id}/credit

Add funds to an account. Platform-only operation, verified **locally**. Idempotent by `(account_id, reference)`.

**Authentication:** JWS token in request body, verified locally via `PlatformAgent.validate_certificate()`.

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

**Behavior (in code order — payload validation before signature verification, T-033):**
1. Parse the request body as JSON; require a string `token` field.
2. Decode the JWS payload **without verifying its signature** (structural decode only — three dot-separated parts, valid base64url, valid JSON object; malformed structure still fails fast with `400 invalid_jws`).
3. Validate the decoded payload: `action` must be `"credit"`; if the payload carries an `account_id`, it must match the URL path parameter; `amount` must be a positive integer; `reference` must be a non-null string.
4. **Only after payload validation passes**, verify the JWS signature locally against the registered `PlatformAgent`'s public key. A signature that fails to verify against the platform's key returns `403 forbidden` — there is no separate "is this the platform" check afterward, because a token that verifies against the platform's own public key can only have been signed by the platform's private key.
5. Credit the account and log a `credit` transaction.
6. All database operations happen in a single DB Gateway transaction.

This means a malformed or non-platform payload is reported as its specific `400` error even when the token is not platform-signed — the payload error is checked and returned before the `403 forbidden` authorization failure would otherwise be raised.

**Idempotency:** The combination of `(account_id, reference)` is unique for credit transactions, enforced by the `ux_credit_reference` index. If a credit with the same account and reference already exists:
- If the amount matches, the original `tx_id` and `balance_after` are returned (idempotent replay).
- If the amount differs, a `payload_mismatch` error is returned.

**Response (200 OK):**
```json
{
  "tx_id": "tx-550e8400-e29b-41d4-a716-446655440000",
  "balance_after": 60
}
```

**Errors:**

| Status | Code                            | Description                                           |
|--------|---------------------------------|--------------------------------------------------------|
| 400    | `invalid_jws`                  | JWS token is malformed, missing, or not a string       |
| 400    | `invalid_json`                 | Request body is not valid JSON or not a JSON object    |
| 400    | `invalid_payload`              | JWS payload missing required fields or wrong action    |
| 400    | `invalid_amount`               | Amount is not a positive integer                       |
| 400    | `payload_mismatch`             | JWS payload `account_id` does not match URL, or duplicate reference with different amount |
| 401    | `token_expired`                | The token's `exp` header claim is in the past           |
| 403    | `forbidden`                    | Local signature verification failed (not signed by the registered platform key) |
| 404    | `account_not_found`            | No account with this ID                                 |
| 503    | `service_not_ready`            | Ledger or platform agent not yet initialized             |

---

### GET /accounts/{account_id}

Check account balance. Agent can only view their own account.

**Authentication:** JWS token in `Authorization: Bearer <token>` header, verified via Identity's `POST /agents/verify-jws`.

**JWS Payload:**
```json
{
  "action": "get_balance",
  "account_id": "a-550e8400-e29b-41d4-a716-446655440000"
}
```

**Behavior (in code order):**
1. Extract the Bearer token from the `Authorization` header; missing or non-`Bearer` header returns `400 invalid_jws`.
2. Verify the JWS token via Identity's `POST /agents/verify-jws`.
3. **Confirm the verified `agent_id` matches the `account_id` in the URL path** — this ownership check happens before any payload validation; a wrong-owner request gets `403 forbidden` even if its payload is also malformed.
4. Validate the JWS payload: `action` must be `"get_balance"`.
5. If the payload carries an `account_id`, it must match the URL path parameter.
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
|--------|---------------------------------|--------------------------------------------------------|
| 400    | `invalid_jws`                  | Bearer token is missing or malformed                    |
| 400    | `invalid_payload`              | JWS payload has wrong action                             |
| 400    | `payload_mismatch`             | JWS payload `account_id` does not match URL              |
| 403    | `forbidden`                    | JWS signature verification failed, or agent is accessing another agent's account |
| 404    | `account_not_found`            | No account with this ID                                  |
| 502    | `identity_service_unavailable` | Cannot reach the Identity service                         |
| 503    | `service_not_ready`            | Ledger or Identity client not yet initialized             |

---

### GET /accounts/{account_id}/transactions

Get transaction history for an account. Agent can only view their own account.

**Authentication:** JWS token in `Authorization: Bearer <token>` header, verified via Identity's `POST /agents/verify-jws`.

**JWS Payload:**
```json
{
  "action": "get_transactions",
  "account_id": "a-550e8400-e29b-41d4-a716-446655440000"
}
```

**Behavior (in code order, same order as `GET /accounts/{account_id}`):**
1. Extract the Bearer token; missing/malformed returns `400 invalid_jws`.
2. Verify the JWS token via Identity's `POST /agents/verify-jws`.
3. Confirm the verified `agent_id` matches the `account_id` in the URL path (`403 forbidden` on mismatch, before payload validation).
4. Validate the JWS payload: `action` must be `"get_transactions"`.
5. If the payload carries an `account_id`, it must match the URL path parameter.
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
|------------------|-----------------------------------|--------------------------------------|
| `credit`         | Context string (e.g., `initial_balance`, or the caller-supplied `reference`) | Account creation with balance > 0, or explicit credit |
| `escrow_lock`    | `task_id`                        | Agent locks funds for a task        |
| `escrow_release` | `escrow_id`                      | Escrow is released or split (both legs of a split use this type) |

**Errors:**

| Status | Code                            | Description                                           |
|--------|---------------------------------|--------------------------------------------------------|
| 400    | `invalid_jws`                  | Bearer token is missing or malformed                    |
| 400    | `invalid_payload`              | JWS payload has wrong action                             |
| 400    | `payload_mismatch`             | JWS payload `account_id` does not match URL              |
| 403    | `forbidden`                    | JWS signature verification failed, or agent is accessing another agent's transactions |
| 404    | `account_not_found`            | No account with this ID                                  |
| 502    | `identity_service_unavailable` | Cannot reach the Identity service                         |
| 503    | `service_not_ready`            | Ledger or Identity client not yet initialized             |

---

### POST /escrow/lock

Lock funds in escrow for a task. Agent-signed operation — the agent must be the one whose funds are being locked.

**Authentication:** JWS token in request body, verified via Identity's `POST /agents/verify-jws`.

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

**Behavior (in code order):**
1. Parse the request body as JSON; require a string `token` field.
2. Verify the JWS token via Identity's `POST /agents/verify-jws`.
3. Validate the JWS payload: `action` must be `"escrow_lock"`; `agent_id` must be a non-empty string.
4. **Confirm the JWS signer matches the `agent_id` in the payload** (an agent can only lock its own funds; a mismatch is `403 forbidden`, checked after the action/`agent_id`-presence checks but before `amount`/`task_id` validation).
5. Validate `amount` (positive integer) and `task_id` (non-empty string).
6. Debit the agent's account, create an escrow record, and log an `escrow_lock` transaction.
7. All database operations happen in a single DB Gateway transaction.

**Idempotency:** The combination of `(payer_account_id, task_id)` is unique for locked escrows, enforced by the `ux_locked_escrow_task` index. If a locked escrow with the same payer and task already exists:
- If the amount matches, the existing escrow is returned (idempotent replay).
- If the amount differs, an `escrow_already_locked` error is returned.

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
|--------|---------------------------------|--------------------------------------------------------|
| 400    | `invalid_jws`                  | JWS token is malformed, missing, or not a string        |
| 400    | `invalid_json`                 | Request body is not valid JSON or not a JSON object     |
| 400    | `invalid_payload`              | JWS payload missing required fields or wrong action      |
| 400    | `invalid_amount`               | Amount is not a positive integer                          |
| 402    | `insufficient_funds`           | Account balance is less than the escrow amount             |
| 403    | `forbidden`                    | Signature verification failed, or signer does not match `agent_id` in payload |
| 404    | `account_not_found`            | No account for this agent                                    |
| 409    | `escrow_already_locked`        | Escrow already locked for this task with a different amount |
| 502    | `identity_service_unavailable` | Cannot reach the Identity service                             |
| 503    | `service_not_ready`            | Ledger or Identity client not yet initialized                 |

---

### POST /escrow/{escrow_id}/release

Release escrowed funds in full to a recipient. Platform-only operation, verified **locally**. In production, called by the Task Board on approval or review-timeout auto-approve, or after a ruling with `worker_pct` of 0 or 100 (§Task Lifecycle).

**Authentication:** JWS token in request body, verified locally via `PlatformAgent.validate_certificate()`.

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

**Behavior (in code order — payload validation before signature verification, T-033):**
1. Parse the request body as JSON; require a string `token` field.
2. Decode the JWS payload without verifying its signature; validate `action` is `"escrow_release"`, `recipient_account_id` is a non-empty string, and (if present) the payload's `escrow_id` matches the URL path parameter.
3. **Only after payload validation passes**, verify the JWS signature locally against the registered `PlatformAgent`'s public key.
4. Look up the escrow record and confirm it is in `locked` status.
5. Credit the full escrow amount to the recipient's account.
6. Log an `escrow_release` transaction on the recipient's account (reference = `escrow_id`).
7. Mark the escrow as `released` and set `resolved_at`.
8. All database operations happen in a single DB Gateway transaction.

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
|--------|---------------------------------|--------------------------------------------------------|
| 400    | `invalid_jws`                  | JWS token is malformed, missing, or not a string         |
| 400    | `invalid_json`                 | Request body is not valid JSON or not a JSON object      |
| 400    | `invalid_payload`              | JWS payload missing required fields or wrong action       |
| 400    | `payload_mismatch`             | JWS payload `escrow_id` does not match URL                 |
| 401    | `token_expired`                | The token's `exp` header claim is in the past               |
| 403    | `forbidden`                    | Local signature verification failed                          |
| 404    | `escrow_not_found`             | No escrow with this ID                                        |
| 404    | `account_not_found`            | Recipient account not found                                   |
| 409    | `escrow_already_resolved`      | Escrow has already been released or split                     |
| 503    | `service_not_ready`            | Ledger or platform agent not yet initialized                  |

---

### POST /escrow/{escrow_id}/split

Split escrowed funds proportionally between worker and poster. Platform-only operation, verified **locally**. Used after Court rulings — the Task Board (not Court) calls this endpoint once it receives the ruling.

**Authentication:** JWS token in request body, verified locally via `PlatformAgent.validate_certificate()`.

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

**Behavior (in code order — payload validation before signature verification, T-033):**
1. Parse the request body as JSON; require a string `token` field.
2. Decode the JWS payload without verifying its signature; validate `action` is `"escrow_split"`, `worker_account_id` and `poster_account_id` are non-empty strings, `worker_pct` is an integer (its 0–100 *range* is checked later, in the store layer, as `invalid_amount` — a non-integer `worker_pct` fails here as `invalid_payload`), and (if present) the payload's `escrow_id` matches the URL path parameter.
3. **Only after payload validation passes**, verify the JWS signature locally against the registered `PlatformAgent`'s public key.
4. **In the store layer**, validate `worker_pct` is between 0 and 100 inclusive — this runs *before* the escrow is even looked up, so an out-of-range `worker_pct` on a nonexistent escrow still returns `invalid_amount`, not `escrow_not_found`.
5. Look up the escrow record and confirm it is in `locked` status.
6. Validate that `poster_account_id` matches the escrow's `payer_account_id`.
7. Calculate the split: `worker_amount = floor(total * worker_pct / 100)`, `poster_amount = total - worker_amount`.
8. Credit each party's account (skipping zero-amount credits — no balance write, no transaction row, no existence check for a zero-amount leg).
9. Log `escrow_release` transactions on each credited (non-zero) account (reference = `escrow_id`).
10. Mark the escrow as `split` and set `resolved_at`.
11. All database operations happen in a single DB Gateway transaction.

This zero-leg-skip and the poster==payer guard are enforced identically by both storage backends (`InMemoryLedgerStore` and the gateway-backed `LedgerDbClient`) — a single parametrized contract test suite (`tests/unit/test_ledger_store_contract.py`) runs every escrow-split scenario against both to keep them from drifting.

**Split math examples:**

| Total | worker_pct | worker_amount | poster_amount |
|-------|------------|---------------|----------------|
| 10    | 40         | 4             | 6              |
| 10    | 100        | 10            | 0              |
| 10    | 0          | 0             | 10             |
| 7     | 33         | 2             | 5              |
| 1     | 50         | 0             | 1              |

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
|--------|---------------------------------|--------------------------------------------------------|
| 400    | `invalid_jws`                  | JWS token is malformed, missing, or not a string         |
| 400    | `invalid_json`                 | Request body is not valid JSON or not a JSON object      |
| 400    | `invalid_payload`              | JWS payload missing required fields, wrong action, or non-integer `worker_pct` |
| 400    | `invalid_amount`               | `worker_pct` is an integer but not between 0 and 100        |
| 400    | `payload_mismatch`             | JWS payload `escrow_id` does not match URL, or `poster_account_id` does not match escrow payer |
| 401    | `token_expired`                | The token's `exp` header claim is in the past                |
| 403    | `forbidden`                    | Local signature verification failed                           |
| 404    | `escrow_not_found`             | No escrow with this ID                                         |
| 404    | `account_not_found`            | Worker or poster account not found                             |
| 409    | `escrow_already_resolved`      | Escrow has already been released or split                      |
| 503    | `service_not_ready`            | Ledger or platform agent not yet initialized                   |

---

## Error Codes

Complete list of error codes used by the Central Bank service:

| Status | Code                            | Description                                           |
|--------|---------------------------------|--------------------------------------------------------|
| 400    | `invalid_jws`                  | JWS token is malformed, missing, or not a string          |
| 400    | `invalid_json`                 | Request body is not valid JSON or not a JSON object       |
| 400    | `invalid_payload`              | JWS payload missing required fields or wrong action        |
| 400    | `invalid_amount`               | Amount/balance/`worker_pct` is not a valid integer in the required range |
| 400    | `payload_mismatch`             | JWS payload field does not match URL parameter, or duplicate reference with different amount, or `poster_account_id` does not match escrow payer |
| 401    | `token_expired`                | Platform-signed token's `exp` header claim is in the past (only reachable on `credit`/`escrow_release`/`escrow_split`, which locally enforce expiry; other endpoints delegate expiry handling to Identity) |
| 402    | `insufficient_funds`           | Escrow lock would cause negative balance                      |
| 403    | `forbidden`                    | Agent accessing another's account/funds, non-platform agent doing a platform op, or JWS signature verification failed |
| 404    | `account_not_found`            | No account with this ID                                        |
| 404    | `agent_not_found`              | Agent does not exist in the Identity service                    |
| 404    | `escrow_not_found`             | No escrow with this ID                                          |
| 405    | `method_not_allowed`           | HTTP method not supported on this endpoint                       |
| 409    | `account_exists`               | Account already created for this agent                            |
| 409    | `escrow_already_resolved`      | Escrow has already been released or split                         |
| 409    | `escrow_already_locked`        | Escrow already locked for this task with a different amount        |
| 413    | `payload_too_large`            | Request body exceeds `request.max_body_size`                       |
| 415    | `unsupported_media_type`       | `Content-Type` is not `application/json`                             |
| 502    | `identity_service_unavailable` | Cannot reach the Identity service (agent-signed ops and `create_account` only; never for `credit`/`escrow_release`/`escrow_split`, which verify locally) |
| 503    | `service_not_ready`            | The ledger, Identity client, or platform agent has not finished initializing, or the DB Gateway is unreachable (e.g. on `GET /health`) |

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

Error responses contain exactly these three fields. Error codes are lowercase `snake_case` (R1). The `details` object provides additional context when available and is an empty object `{}` when there is no extra context. The `message` field never includes stack traces, SQL fragments, filesystem paths, or internal diagnostics.

This format is shared by all services in the Agent Task Economy.

---

## What This Service Does NOT Do

- **Agent-to-agent direct transfers** — funds only move via platform-controlled credits and escrow operations. There is no peer-to-peer transfer endpoint.
- **Salary distribution scheduling** — no salary scheduler exists in v1 (R8); platform-signed `credit` is triggered externally (fund-feeder CLI, UI provisioning). The Bank does not schedule or automate anything.
- **Debit endpoint** — funds leave accounts only via escrow locks. There is no direct debit operation.
- **Account deletion or freezing** — once created, accounts are permanent. No suspend, freeze, or delete operations.
- **Pagination on transaction history** — `GET /accounts/{account_id}/transactions` returns all transactions. Acceptable for initial scope.
- **Rate limiting** — no throttling on any endpoint. Acceptable for hackathon scope.
- **Currency conversion** — single currency (integer coins) with no exchange rates.
- **A separate audit log** — the transaction log serves as the audit trail; the DB Gateway's `events` table additionally records every write for the shared semantic event log.
- **Direct SQLite access** — the Central Bank never opens the database file itself; all persistence goes through the DB Gateway's HTTP API, which owns the shared `economy.db`.
- **Escrow settlement orchestration** — the Central Bank exposes `release`/`split` as pure ledger primitives; deciding *when* to call them (approve, timeout, cancel, ruling) is the Task Board's responsibility (R4). Court never calls this service.

---

## Interaction Patterns

### Account Creation Flow (works identically for self-service and platform-funded modes; the JWS signer determines which)

```
Caller                          Central Bank                    Identity Service
  |                                  |                                |
  |  1. POST /accounts               |                                |
  |     { token: <JWS with           |                                |
  |       action=create_account,     |                                |
  |       agent_id, initial_balance> }|                               |
  |  -------------------------------->|                                |
  |                                  |  2. POST /agents/verify-jws    |
  |                                  |     { token: "..." }           |
  |                                  |  ------------------------------>|
  |                                  |                                | 3. Verify signature
  |                                  |  4. { valid: true,             |
  |                                  |       agent_id: signer,        |
  |                                  |       payload: {...} }         |
  |                                  |  <------------------------------|
  |                                  |                                |
  |                                  |  5. is_platform = signer ==    |
  |                                  |       registered PlatformAgent |
  |                                  |  6. Validate agent_id/balance  |
  |                                  |     against is_platform rules |
  |                                  |                                |
  |                                  |  7. GET /agents/{agent_id}     |
  |                                  |  ------------------------------>|
  |                                  |                                | 8. Look up agent
  |                                  |  9. { agent_id, name, ... }    |
  |                                  |  <------------------------------|
  |                                  |                                |
  |                                  | 10. BEGIN TRANSACTION (gateway)|
  |                                  | 11. Insert account record      |
  |                                  | 12. Insert credit tx (if > 0) |
  |                                  | 13. COMMIT                    |
  |                                  |                                |
  | 14. 201 { account_id, balance,   |                                |
  |           created_at }           |                                |
  |  <--------------------------------|                                |
```

### Escrow Lock Flow (agent-signed, verified via Identity)

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
  |                                  |  5. Validate action/agent_id   |
  |                                  |  6. Check signer == agent_id   |
  |                                  |  7. Validate amount/task_id    |
  |                                  |  8. BEGIN TRANSACTION (gateway)|
  |                                  |  9. Check balance >= amount    |
  |                                  | 10. Debit account              |
  |                                  | 11. Insert escrow record       |
  |                                  | 12. Insert escrow_lock tx      |
  |                                  | 13. COMMIT                    |
  |                                  |                                |
  | 14. 201 { escrow_id, amount,     |                                |
  |           task_id, status }      |                                |
  |  <--------------------------------|                                |
```

### Escrow Release Flow (platform-signed, verified LOCALLY — no Identity round trip)

```
Task Board (platform-signed)    Central Bank
  |                                  |
  |  1. POST /escrow/{id}/release    |
  |     { token: <platform JWS       |
  |       with action=escrow_release, |
  |       escrow_id, recipient_id> } |
  |  -------------------------------->|
  |                                  |  2. Decode payload WITHOUT verifying signature
  |                                  |  3. Validate action/recipient_account_id/escrow_id match
  |                                  |  4. Verify signature LOCALLY against the
  |                                  |     registered PlatformAgent's public key
  |                                  |     (no network call)
  |                                  |
  |                                  |  5. BEGIN TRANSACTION (gateway)
  |                                  |  6. Load escrow (must be locked)
  |                                  |  7. Credit recipient account
  |                                  |  8. Insert escrow_release tx
  |                                  |  9. Update escrow -> released
  |                                  | 10. COMMIT
  |                                  |
  | 11. 200 { escrow_id, status,     |
  |           recipient, amount }    |
  |  <--------------------------------|
```

### Escrow Split Flow (platform-signed, verified LOCALLY — no Identity round trip)

```
Task Board (platform-signed)    Central Bank
  |                                  |
  |  1. POST /escrow/{id}/split      |
  |     { token: <platform JWS       |
  |       with action=escrow_split,  |
  |       escrow_id, worker_id,      |
  |       worker_pct, poster_id> }   |
  |  -------------------------------->|
  |                                  |  2. Decode payload WITHOUT verifying signature
  |                                  |  3. Validate action/worker_id/poster_id/
  |                                  |     worker_pct-is-int/escrow_id match
  |                                  |  4. Verify signature LOCALLY against the
  |                                  |     registered PlatformAgent's public key
  |                                  |     (no network call)
  |                                  |
  |                                  |  5. Validate worker_pct in [0, 100]
  |                                  |     (store layer, before escrow lookup)
  |                                  |  6. BEGIN TRANSACTION (gateway)
  |                                  |  7. Load escrow (must be locked)
  |                                  |  8. Validate poster == payer
  |                                  |  9. worker_amt = floor(total
  |                                  |       * worker_pct / 100)
  |                                  | 10. poster_amt = total
  |                                  |       - worker_amt
  |                                  | 11. Credit worker (if > 0)
  |                                  | 12. Credit poster (if > 0)
  |                                  | 13. Insert escrow_release txs
  |                                  | 14. Update escrow -> split
  |                                  | 15. COMMIT
  |                                  |
  | 16. 200 { escrow_id, status,     |
  |           worker_amount,         |
  |           poster_amount }        |
  |  <--------------------------------|
```

---

## Configuration

```yaml
service:
  name: "central-bank"
  version: "0.1.0"

server:
  host: "127.0.0.1"
  port: 8002
  log_level: "info"

logging:
  level: "INFO"
  directory: "data/logs"

identity:
  base_url: "http://localhost:8001"
  get_agent_path: "/agents"
  verify_jws_path: "/agents/verify-jws"
  timeout_seconds: 10

platform:
  agent_config_path: "../../agents/config.yaml"

request:
  max_body_size: 1048576

db_gateway:
  url: "http://127.0.0.1:8007"
  timeout_seconds: 10
```

| Section                     | Key             | Type    | Description                                          |
|------------------------------|-----------------|---------|-------------------------------------------------------|
| `service.name`               |                 | string  | Service identifier                                     |
| `service.version`            |                 | string  | Service version                                        |
| `server.host`                |                 | string  | Bind address                                            |
| `server.port`                |                 | integer | Listen port (8002)                                       |
| `server.log_level`           |                 | string  | Uvicorn log level                                         |
| `logging.level`              |                 | string  | Application log level                                      |
| `logging.directory`          |                 | string  | Directory for log files                                     |
| `identity.base_url`          |                 | string  | Base URL of the Identity service                              |
| `identity.get_agent_path`    |                 | string  | Path prefix for agent lookup (agent_id appended)                |
| `identity.verify_jws_path`   |                 | string  | Path to the JWS verification endpoint                             |
| `identity.timeout_seconds`   |                 | integer | Timeout for Identity HTTP calls                                    |
| `platform.agent_config_path` |                 | string  | Path (relative to this service's config directory, unless absolute) to the shared `agents/config.yaml`; used at startup to load and register the platform's Ed25519 keypair (roster entry `platform`). There is no `platform.agent_id` field — the platform's identity is always the registered `PlatformAgent`'s `agent_id`, resolved at request time (`503 service_not_ready` if none is registered). |
| `request.max_body_size`      |                 | integer | Maximum request body size in bytes                                   |
| `db_gateway.url`             |                 | string  | Base URL of the DB Gateway                                            |
| `db_gateway.timeout_seconds` |                 | integer | Timeout for DB Gateway HTTP calls                                       |

All configuration values are required. `identity` and `db_gateway` are non-Optional sections — every field in them must be present or the service fails to start (T-034: no Optional-but-required-at-runtime fields). There are no hardcoded defaults.
