# Central Bank Service — API Specification

## MP-04 receipt contract amendment

The [2026-10-16 financial receipt contract](../../plans/2026-10-16-mp04-receipt-contract.md)
defines the implemented financial response and replay behavior documented below.
It requires caller-owned operation IDs for positive issuance, credit and every escrow operation,
explicit settlement source references, immutable receipts with actual ledger/event identities,
and a separate current escrow projection. Exact native replay is payload-bound; conflicting
identities return explicit 409 errors. Historical effects receive audited observations, never
invented native receipts. Zero-balance account creation retains its existing natural identity.
The implementation and independent reviews are verified by root19 exit0; T-116/T-117 are closed.
See the active change's evidence/root19-checkpoint.md. Historical observations follow the
[historical contract](../../plans/2026-10-17-mp04-historical-observations.md).

## Purpose

The Central Bank manages accounts, balances, transaction history, and escrow for the Agent Task Economy. It is the financial backbone that other services interact with to move funds. All monetary operations in the economy flow through this service — from account funding to escrow locks, releases, and court-ordered splits. Settlement (releasing or splitting escrow) is orchestrated exclusively by the **Task Board** (R4): on approval, review-timeout auto-approve, cancel/expiry, and after a Court ruling, the Task Board calls this service's escrow endpoints. Court never calls the Central Bank directly. Platform-signed `credit` is also invoked by platform-side tooling (the `fund-feeder` CLI, UI treasury provisioning) outside the task lifecycle.

## Core Principles

- **Integer coins.** Individual monetary values are integers from zero through `9223372036854775807`; credits and locks must be positive. Booleans and fractional values are rejected. Aggregate totals use exact Python integers and may exceed one account's representational bound.
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

MP-06 applies the same strict monetary contract to public routes and both ledger stores. Inputs
above the signed-64-bit representation return 400 `amount_out_of_range`; a credit or settlement
that would exceed a recipient balance returns 409 `amount_out_of_range`. The Gateway-backed store
preserves these codes and statuses rather than converting them to a generic server error or
`escrow_already_resolved`. In-memory settlement checks all recipient credits, including combined
same-account shares, before changing balances or ledger rows. A rejected operation has no financial
side effects. This fixed representation bound does not introduce a monetary supply policy.

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

On the DB Gateway these tables are named `bank_accounts`, `bank_transactions`, and `bank_escrow`.
The implemented receipt tables and constraints are defined in
`services/db-gateway/src/db_gateway_service/schema_resources/current_v2.sql` and summarized by the
linked MP-04 schema-v2 contract. The Central Bank's read-facing field names remain unchanged.

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
| `idx_bank_escrow_lifetime`          | Unique index  | `(payer_account_id, task_id)` for all statuses — one lifetime escrow per payer per task |
| `ix_transactions_account_timestamp_tx_id` | Index   | `(account_id, timestamp, tx_id)` — efficient transaction history queries |

### Financial command identity and responses

The authoritative wire contract is the
[MP-04 receipt contract](../../plans/2026-10-16-mp04-receipt-contract.md), backed by
[schema v2](../../plans/2026-10-16-mp04-schema-v2.md) and the
[historical-observation contract](../../plans/2026-10-17-mp04-historical-observations.md).
The rules below summarize that contract for these endpoints; those documents define the exact
canonical hashes, persisted fields, replay matrix, and historical attestation rules.

- `operation_id` is a caller-owned canonical `op-<UUID>` string. It is required in the signed
  payload for positive account issuance, credit, lock, release, and split. It is forbidden for
  zero-balance account creation. Operation IDs are global across all financial operation kinds.
- A native success returns `outcome: "native_receipt"`, the immutable persisted `receipt`, and a
  fresh `current_escrow` projection. `current_escrow` is null for issuance and credit. For escrow
  operations it contains the actual escrow ID, payer, task, amount, current status, creation time,
  and resolution time. Exact replay returns the original receipt identities and a newly read
  projection; generated candidate IDs from the retry never replace stored IDs.
- Receipt legs carry the exact transaction identity and `balance_after` for positive movements.
  Split always returns ordered `worker` then `poster` legs. A zero allocation remains a logical
  leg with null `transaction_id` and null `balance_after`; that zero leg does not require an
  account to exist.
- Exact historical credit/lock collisions may return `outcome: "legacy_observation"`. Such an
  observation has null operation ownership for the historical effect and cannot prove a newly
  committed native command. Release, split, and positive issuance never return this outcome.

Native response shape (the endpoint sections specify the required kind-specific result and legs):

```json
{
  "outcome": "native_receipt",
  "receipt": {
    "receipt_version": 1,
    "operation_id": "op-00000000-0000-4000-8000-000000000001",
    "operation_kind": "credit",
    "request_sha256": "<64 lowercase hexadecimal characters>",
    "source_reference": "salary_round_3",
    "committed_at": "2026-02-23T10:00:00Z",
    "event_id": 12,
    "escrow_id": null,
    "result": {
      "account_id": "a-550e8400-e29b-41d4-a716-446655440000",
      "amount": 10,
      "balance_after": 60,
      "reference": "salary_round_3"
    },
    "legs": [
      {
        "role": "account",
        "account_id": "a-550e8400-e29b-41d4-a716-446655440000",
        "direction": "credit",
        "amount": 10,
        "transaction_id": "tx-550e8400-e29b-41d4-a716-446655440000",
        "balance_after": 60
      }
    ]
  },
  "current_escrow": null
}
```

Replay and ownership outcomes are distinct:

| Existing effect | Submitted command | Result |
|---|---|---|
| Native receipt with same ID | Same kind and canonical business request | Exact receipt replay; fresh current projection; no mutation |
| Native receipt with same ID | Changed kind or business request | 409 `operation_id_conflict` with the original `winning_operation_id` |
| Native credit/lock natural key | Different operation ID | 409 `financial_operation_already_committed` with the true winning ID |
| Attested legacy credit/lock natural key | Exact observed terms | Explicit `legacy_observation`; historical `operation_id` remains null |
| Attested legacy credit/lock natural key | Changed amount | 409 `legacy_operation_conflict`; `winning_operation_id` is null |
| Attested legacy terminal escrow | New settlement attempt | 409 `historical_settlement_unreplayable`; no movement or invented identity |

An `escrow_already_resolved` error is not a success signal. Exact native settlement replay succeeds
only through its matching stored receipt. A different operation ID, opposing settlement, legacy
terminal settlement, or unowned/corrupt effect returns its explicit conflict or ownership error.

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
  "initial_balance": 50,
  "operation_id": "op-00000000-0000-4000-8000-000000000001"
}
```

`operation_id` is required when `initial_balance` is positive and forbidden when it is zero.

**Behavior (in code order):**
1. Parse the request body as JSON; require a string `token` field.
2. Verify the JWS token via Identity's `POST /agents/verify-jws`.
3. Determine whether the verified signer is the platform: `caller_agent_id == <registered PlatformAgent's agent_id>`.
4. Validate the JWS payload: `action` must be `"create_account"`; `agent_id` must be a non-empty string.
5. **Non-platform signers may only create their own account** — if `agent_id != caller_agent_id`, reject with `403 forbidden`.
6. Validate `initial_balance`: required, must be a non-negative integer.
7. **Non-platform signers must pass `initial_balance: 0`** — a self-service request for a non-zero balance is rejected with `403 forbidden`.
8. Call Identity to verify the target `agent_id` exists (`GET /agents/{agent_id}`).
9. For a positive issuance, require and validate the caller's `operation_id`; for zero balance,
   reject any supplied operation ID.
10. Create the account with the specified initial balance; if `initial_balance > 0`, atomically
    log the `initial_balance` credit, event, receipt, and account credit leg.
11. All database operations happen in a single DB Gateway transaction.

Because step 2 (remote signature verification) runs before steps 4–7 (payload validation), an invalid signature or an unreachable Identity service is reported (`403 forbidden` / `502 identity_service_unavailable`) even for requests whose domain payload is also malformed. Platform operations likewise authenticate the strict profile before domain validation, but do so locally; see the Authentication Specification's Error Precedence section.

**Response (201 Created, zero balance):**
```json
{
  "account_id": "a-550e8400-e29b-41d4-a716-446655440000",
  "balance": 0,
  "created_at": "2026-02-23T10:00:00Z"
}
```

Positive issuance returns the native envelope defined above with
`operation_kind: "account_issuance"`, null `current_escrow`, result fields `account_id`,
`issued_amount`, and `balance_after`, and one positive `account` credit leg carrying the exact
transaction ID. Positive issuance never returns a legacy observation. A different ID for existing
native issuance returns `financial_operation_already_committed`; other account identity, including
historical issuance-like data, remains 409 `account_exists`.

**Errors:**

| Status | Code                            | Description                                           |
|--------|---------------------------------|--------------------------------------------------------|
| 400    | `invalid_jws`                  | JWS token is malformed, missing, or not a string       |
| 400    | `invalid_json`                 | Request body is not valid JSON or not a JSON object    |
| 400    | `invalid_payload`              | JWS payload missing required fields/wrong action, or zero-balance creation carries an operation ID |
| 400    | `invalid_amount`               | `initial_balance` is not a non-negative integer        |
| 400    | `amount_out_of_range`           | `initial_balance` exceeds the signed-64-bit limit |
| 400    | `invalid_operation_id`          | Positive issuance lacks a canonical operation ID |
| 409    | `operation_id_conflict`         | Operation ID already owns a different financial request |
| 409    | `financial_operation_already_committed` | Existing native issuance belongs to another operation ID |
| 403    | `forbidden`                    | JWS signature verification failed; a non-platform signer named a different agent as `agent_id`; or a non-platform signer requested a non-zero `initial_balance` |
| 404    | `agent_not_found`              | Agent does not exist in the Identity service            |
| 409    | `account_exists`               | Account already exists for this agent                   |
| 502    | `identity_service_unavailable` | Cannot reach the Identity service                        |
| 503    | `service_not_ready`            | Ledger or Identity client not yet initialized            |

**Concurrency:** The insert is wrapped in a database transaction. The `account_id` column is a primary key, so concurrent account creation for the same agent results in one success and one `409`.

---

### POST /accounts/{account_id}/credit

Add funds to an account. Platform-only operation, verified **locally**. Native replay is owned by
the caller's global operation ID; `(account_id, reference)` remains the natural key.

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
  "reference": "salary_round_3",
  "operation_id": "op-00000000-0000-4000-8000-000000000002"
}
```

**Behavior (MP-03 strict-profile order):**
1. Parse the request body as JSON; require a string `token` field.
2. Verify the strict JWS profile, original signature and temporal policy locally. Require the authenticated platform `kid`, audience, concrete resource and `credit` action.
3. Validate the authenticated domain payload: an optional `account_id` must match the URL,
   `amount` must be a positive bounded integer excluding booleans, `reference` must be a nonempty
   valid UTF-8 string, and `operation_id` must be canonical.
4. Credit the account and log a `credit` transaction atomically through the Gateway.

Invalid signatures or platform identities return `403 forbidden` before malformed domain-field
errors. Authentic expired or future-issued tokens retain their distinct 401 errors. This supersedes
the historical T-033 payload-first order; there is no unverified domain-payload authorization path.

**Replay:** The operation ID owns native replay; `(account_id, reference)` remains the natural key.
Same ID and same canonical request returns the exact native receipt. A different ID for an existing
native natural key returns 409 `financial_operation_already_committed`, even when amount matches.
An attested legacy natural-key match returns `legacy_observation` only when the amount matches;
changed amount returns `legacy_operation_conflict`.

**Response (200 OK):** The native envelope example in "Financial command identity and responses"
is the credit response. An exact historical collision instead returns the exact typed observation
defined by the historical-observation contract; consumers must not report it as a newly committed
credit.

**Errors:**

| Status | Code                            | Description                                           |
|--------|---------------------------------|--------------------------------------------------------|
| 400    | `invalid_jws`                  | JWS token is malformed, missing, or not a string       |
| 400    | `invalid_json`                 | Request body is not valid JSON or not a JSON object    |
| 400    | `invalid_payload`              | JWS payload missing required fields or wrong action    |
| 400    | `invalid_amount`               | Amount is not a positive integer                       |
| 400    | `amount_out_of_range`           | Credit input exceeds the signed-64-bit limit |
| 400    | `invalid_operation_id`          | Missing or noncanonical operation ID |
| 400    | `payload_mismatch`             | JWS payload `account_id` does not match URL |
| 401    | `token_expired`                | The token's `exp` header claim is in the past           |
| 403    | `forbidden`                    | Local signature verification failed (not signed by the registered platform key) |
| 404    | `account_not_found`            | No account with this ID                                 |
| 409    | `amount_out_of_range`           | Credit would exceed the recipient balance limit |
| 409    | `operation_id_conflict`         | Same operation ID carries changed kind or business terms |
| 409    | `financial_operation_already_committed` | Native natural key belongs to another operation ID |
| 409    | `legacy_operation_conflict`     | Attested historical natural key has different immutable terms |
| 500    | `receipt_ownership_missing` / `receipt_ownership_failed` | Existing effect lacks valid exclusive native or attested historical ownership |
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
  "operation_id": "op-00000000-0000-4000-8000-000000000003",
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
5. Validate `amount` (positive bounded integer), `task_id` (nonempty valid UTF-8 string), and the
   canonical caller-owned `operation_id`.
6. Debit the agent's account, create an escrow record, and log an `escrow_lock` transaction.
7. All database operations happen in a single DB Gateway transaction.

**Replay:** `(payer_account_id, task_id)` remains the natural key, but native replay is owned by
`operation_id`. Same ID and canonical request returns the immutable original lock receipt while
`current_escrow` reports the actual current `locked`, `released`, or `split` state. A different ID
for an existing native lock returns `financial_operation_already_committed`, even when amount
matches. An attested historical match returns a `legacy_observation`; changed amount returns
`legacy_operation_conflict`.

**Response (201 Created):** Native envelope with `operation_kind: "escrow_lock"`, result
`{"escrow_status_at_commit":"locked"}`, one positive `payer` debit leg, the exact committed
escrow/transaction/event IDs, and the current escrow projection. An exact historical collision
returns the typed legacy lock observation instead.

**Errors:**

| Status | Code                            | Description                                           |
|--------|---------------------------------|--------------------------------------------------------|
| 400    | `invalid_jws`                  | JWS token is malformed, missing, or not a string        |
| 400    | `invalid_json`                 | Request body is not valid JSON or not a JSON object     |
| 400    | `invalid_payload`              | JWS payload missing required fields or wrong action      |
| 400    | `invalid_amount`               | Amount is not a positive integer                          |
| 400    | `amount_out_of_range`           | Lock amount exceeds the signed-64-bit limit |
| 400    | `invalid_operation_id`          | Missing or noncanonical operation ID |
| 402    | `insufficient_funds`           | Account balance is less than the escrow amount             |
| 403    | `forbidden`                    | Signature verification failed, or signer does not match `agent_id` in payload |
| 404    | `account_not_found`            | No account for this agent                                    |
| 409    | `operation_id_conflict`         | Same operation ID carries changed kind or business terms |
| 409    | `financial_operation_already_committed` | Native payer/task lock belongs to another operation ID |
| 409    | `legacy_operation_conflict`     | Attested historical lock has a different amount |
| 500    | `receipt_ownership_missing` / `receipt_ownership_failed` | Existing effect lacks valid exclusive native or attested historical ownership |
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
  "operation_id": "op-00000000-0000-4000-8000-000000000004",
  "escrow_id": "esc-550e8400-e29b-41d4-a716-446655440000",
  "recipient_account_id": "a-worker-uuid",
  "source_reference": "task:T-123:approved"
}
```

**Behavior (MP-03 strict-profile order):**
1. Parse the request body as JSON; require a string `token` field.
2. Verify the strict profile, signature and temporal policy locally; require the authenticated platform identity, audience, resource and `escrow_release` action.
3. Validate the canonical `operation_id`, nonempty valid UTF-8 `source_reference`, authenticated
   `recipient_account_id`, and any payload `escrow_id` against the URL.
4. Look up the escrow record and confirm it is in `locked` status.
5. Credit the full escrow amount to the recipient's account.
6. Log an `escrow_release` transaction on the recipient's account (reference = `escrow_id`).
7. Mark the escrow as `released` and set `resolved_at`.
8. All database operations happen in a single DB Gateway transaction.

**Response (200 OK):** Native envelope with `operation_kind: "escrow_release"`, the supplied
source reference, result `{"escrow_status_at_commit":"released"}`, one positive `recipient`
credit leg, exact committed identities, and a released current escrow projection. Exact same-ID
replay returns that immutable receipt and the fresh projection without another credit.

**Errors:**

| Status | Code                            | Description                                           |
|--------|---------------------------------|--------------------------------------------------------|
| 400    | `invalid_jws`                  | JWS token is malformed, missing, or not a string         |
| 400    | `invalid_json`                 | Request body is not valid JSON or not a JSON object      |
| 400    | `invalid_payload`              | JWS payload missing required fields or wrong action       |
| 400    | `payload_mismatch`             | JWS payload `escrow_id` does not match URL                 |
| 400    | `invalid_operation_id`          | Missing or noncanonical operation ID |
| 401    | `token_expired`                | The token's `exp` header claim is in the past               |
| 403    | `forbidden`                    | Local signature verification failed                          |
| 404    | `escrow_not_found`             | No escrow with this ID                                        |
| 404    | `account_not_found`            | Recipient account not found                                   |
| 409    | `amount_out_of_range`           | Release would exceed the recipient balance limit |
| 409    | `operation_id_conflict`         | Same operation ID carries changed kind or business terms |
| 409    | `financial_operation_already_committed` | Another native settlement owns this escrow; details identify the winner |
| 409    | `historical_settlement_unreplayable` | Escrow was terminal at historical cutover; winning ID is null |
| 409    | `escrow_already_resolved`      | Non-replayable resolved-state conflict; never proof of success |
| 500    | `receipt_ownership_missing` / `receipt_ownership_failed` | Settlement ownership cannot be safely established |
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
  "operation_id": "op-00000000-0000-4000-8000-000000000005",
  "escrow_id": "esc-550e8400-e29b-41d4-a716-446655440000",
  "worker_account_id": "a-worker-uuid",
  "worker_pct": 40,
  "poster_account_id": "a-poster-uuid",
  "source_reference": "task:T-123:ruling:r-456"
}
```

**Behavior (MP-03 strict-profile order):**
1. Parse the request body as JSON; require a string `token` field.
2. Verify the strict profile, signature and temporal policy locally; require the authenticated platform identity, audience, resource and `escrow_split` action.
3. Validate the canonical `operation_id`, nonempty valid UTF-8 `source_reference`, authenticated
   worker/poster IDs, percentage, and optional escrow-ID match. `worker_pct` must be an integer
   excluding booleans (`400 invalid_payload` for wrong type) and lie within 0–100 (`400
   invalid_amount` otherwise). Caller-supplied amounts and financial `constraints` are rejected.
4. The store repeats percentage validation before looking up the escrow. With valid authentication, an out-of-range percentage on a nonexistent escrow returns `invalid_amount`; invalid signatures and authentic expiry still precede percentage errors.
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

**Response (200 OK):** Native envelope with `operation_kind: "escrow_split"`, the supplied source
reference, result `{"escrow_status_at_commit":"split","worker_pct":40}`, ordered `worker` and
`poster` credit legs, exact committed identities, and a split current escrow projection. Positive
legs contain transaction/balance fields. A zero leg retains its requested logical account ID but
has null transaction and balance fields and performs no account-existence check.

**Errors:**

| Status | Code                            | Description                                           |
|--------|---------------------------------|--------------------------------------------------------|
| 400    | `invalid_jws`                  | JWS token is malformed, missing, or not a string         |
| 400    | `invalid_json`                 | Request body is not valid JSON or not a JSON object      |
| 400    | `invalid_payload`              | JWS payload missing required fields, wrong action, or non-integer `worker_pct` |
| 400    | `invalid_amount`               | `worker_pct` is an integer but not between 0 and 100        |
| 400    | `payload_mismatch`             | JWS payload `escrow_id` does not match URL, or `poster_account_id` does not match escrow payer |
| 400    | `invalid_operation_id`          | Missing or noncanonical operation ID |
| 401    | `token_expired`                | The token's `exp` header claim is in the past                |
| 403    | `forbidden`                    | Local signature verification failed                           |
| 404    | `escrow_not_found`             | No escrow with this ID                                         |
| 404    | `account_not_found`            | Worker or poster account not found                             |
| 409    | `amount_out_of_range`           | Split credits would exceed a recipient balance limit, including combined same-account shares |
| 409    | `operation_id_conflict`         | Same operation ID carries changed kind or business terms |
| 409    | `financial_operation_already_committed` | Another native settlement owns this escrow; details identify the winner |
| 409    | `historical_settlement_unreplayable` | Escrow was terminal at historical cutover; winning ID is null |
| 409    | `escrow_already_resolved`      | Non-replayable resolved-state conflict; never proof of success |
| 500    | `receipt_ownership_missing` / `receipt_ownership_failed` | Settlement ownership cannot be safely established |
| 503    | `service_not_ready`            | Ledger or platform agent not yet initialized                   |

---

## Error Codes

Complete list of error codes used by the Central Bank service:

| Status | Code                            | Description                                           |
|--------|---------------------------------|--------------------------------------------------------|
| 400    | `invalid_jws`                  | JWS token is malformed, missing, or not a string          |
| 400    | `invalid_json`                 | Request body is not valid JSON or not a JSON object       |
| 400    | `invalid_payload`              | JWS payload missing required fields or wrong action        |
| 400    | `invalid_amount`               | Amount/balance has an invalid type or sign, or integer `worker_pct` lies outside 0–100 |
| 400    | `amount_out_of_range`           | Monetary input exceeds the signed-64-bit representation |
| 400    | `invalid_operation_id`          | Required positive financial operation ID is missing or noncanonical |
| 400    | `invalid_financial_request`     | Canonical business request is invalid |
| 400    | `payload_mismatch`             | JWS payload field does not match URL parameter, or `poster_account_id` does not match escrow payer |
| 401    | `token_expired`                | Platform-signed token's `exp` header claim is in the past (only reachable on `credit`/`escrow_release`/`escrow_split`, which locally enforce expiry; other endpoints delegate expiry handling to Identity) |
| 402    | `insufficient_funds`           | Escrow lock would cause negative balance                      |
| 403    | `forbidden`                    | Agent accessing another's account/funds, non-platform agent doing a platform op, or JWS signature verification failed |
| 404    | `account_not_found`            | No account with this ID                                        |
| 404    | `agent_not_found`              | Agent does not exist in the Identity service                    |
| 404    | `escrow_not_found`             | No escrow with this ID                                          |
| 405    | `method_not_allowed`           | HTTP method not supported on this endpoint                       |
| 409    | `account_exists`               | Account already created for this agent                            |
| 409    | `amount_out_of_range`           | Credit or settlement would overflow a recipient balance |
| 409    | `escrow_already_resolved`      | Non-replayable settlement state conflict; never proof of success |
| 409    | `escrow_already_locked`        | Non-replayable lock constraint conflict |
| 409    | `operation_id_conflict`         | Operation ID already owns another kind or canonical request; details include the winning ID |
| 409    | `financial_operation_already_committed` | Native credit/lock/settlement effect belongs to another operation ID |
| 409    | `legacy_operation_conflict`     | Attested historical credit/lock has different immutable terms; winning ID is null |
| 409    | `historical_settlement_unreplayable` | Attested historical terminal settlement has no replayable operation ID |
| 413    | `payload_too_large`            | Request body exceeds `request.max_body_size`                       |
| 415    | `unsupported_media_type`       | `Content-Type` is not `application/json`                             |
| 502    | `identity_service_unavailable` | Cannot reach the Identity service (agent-signed ops and `create_account` only; never for `credit`/`escrow_release`/`escrow_split`, which verify locally) |
| 503    | `service_not_ready`            | The ledger, Identity client, or platform agent has not finished initializing, or the DB Gateway is unreachable (e.g. on `GET /health`) |
| 500    | `receipt_ownership_missing`    | Existing effect has neither a native receipt nor an attested historical marker |
| 500    | `receipt_ownership_failed`     | Receipt/marker ownership or historical attestation is corrupt or ambiguous |

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
  |       agent_id, initial_balance, |                                |
  |       operation_id if > 0> }     |                                |
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
  |                                  | 12. If > 0: insert tx/event,  |
  |                                  |     receipt and account leg   |
  |                                  | 13. COMMIT                    |
  |                                  |                                |
  | 14. 201 zero account projection  |                                |
  |     or native issuance envelope  |                                |
  |  <--------------------------------|                                |
```

### Escrow Lock Flow (agent-signed, verified via Identity)

```
Agent                           Central Bank                    Identity Service
  |                                  |                                |
  |  1. POST /escrow/lock            |                                |
  |     { token: <agent JWS          |                                |
  |       with action=escrow_lock,   |                                |
  |       operation_id, agent_id,    |                                |
  |       amount, task_id> }         |                                |
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
  | 14. 201 native receipt or typed  |                                |
  |     historical lock observation  |                                |
  |  <--------------------------------|                                |
```

### Escrow Release Flow (platform-signed, verified LOCALLY — no Identity round trip)

```
Task Board (platform-signed)    Central Bank
  |                                  |
  |  1. POST /escrow/{id}/release    |
  |     { token: <platform JWS       |
  |       with action=escrow_release, |
  |       operation_id, escrow_id,   |
  |       recipient_id, source_ref> }|
  |  -------------------------------->|
  |                                  |  2. Verify strict profile/signature/time LOCALLY
  |                                  |     and require the registered platform kid
  |                                  |  3. Check authenticated audience/resource/action
  |                                  |  4. Validate operation/source/recipient/escrow match
  |                                  |
  |                                  |  5. BEGIN TRANSACTION (gateway)
  |                                  |  6. Load escrow (must be locked)
  |                                  |  7. Credit recipient account
  |                                  |  8. Insert escrow_release tx
  |                                  |  9. Update escrow -> released
  |                                  | 10. COMMIT
  |                                  |
  | 11. 200 native receipt + current |
  |     released escrow projection   |
  |  <--------------------------------|
```

### Escrow Split Flow (platform-signed, verified LOCALLY — no Identity round trip)

```
Task Board (platform-signed)    Central Bank
  |                                  |
  |  1. POST /escrow/{id}/split      |
  |     { token: <platform JWS       |
  |       with action=escrow_split,  |
  |       operation_id, escrow_id,   |
  |       worker_id, worker_pct,     |
  |       poster_id, source_ref> }   |
  |  -------------------------------->|
  |                                  |  2. Verify strict profile/signature/time LOCALLY
  |                                  |     and require the registered platform kid
  |                                  |  3. Check authenticated audience/resource/action
  |                                  |  4. Validate operation/source, worker/poster IDs,
  |                                  |     strict percentage and escrow_id match
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
  | 16. 200 native receipt with      |
  |     ordered worker/poster legs + |
  |     current escrow projection    |
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
