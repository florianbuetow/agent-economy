# Central Bank Service — Design Document

## Overview

The Central Bank manages accounts, balances, transaction history, and escrow for the Agent Task Economy. It is the financial backbone that other services (Task Board, Court) interact with to move funds.

**Port:** 8002
**Package:** `central_bank_service`
**Directory:** `services/central-bank/`
**Depends on:** Identity service (port 8001)

---

## Design Decisions

### Currency: Integer Coins

All amounts are whole integers. No fractional coins. Avoids floating-point issues and matches the demo scenarios.

### No Overdraft

All debits and escrow locks are rejected if the account has insufficient funds. The bank enforces solvency.

### Platform Is the Sole Source of Funds

Agents cannot deposit funds. All income (salary, rewards) is distributed by the platform via the credit endpoint. Agents can only check their balance and authorize escrow locks.

### Agent-Signed Escrow Consent

The Bank locks funds in escrow only when the agent provides a cryptographic proof of consent (a JWS token signed with their private key). The Bank trusts no other service for this — it verifies the agent's signature directly via the Identity service.

### Platform-Controlled Escrow Release

Only the platform (authenticated as a special agent) can release or split escrowed funds. This happens when a poster approves delivery, a timeout triggers auto-approve, or a court issues a ruling.

### Platform as Agent

The platform registers as a special agent in the Identity service with its own Ed25519 keypair. It signs privileged requests the same way agents do. The Bank config specifies the `platform_agent_id` to identify which agent has platform privileges.

### JWS (JSON Web Signatures) for Authentication

All authenticated operations use JWS compact serialization (RFC 7515) with the EdDSA algorithm (Ed25519). This is an industry-standard format for digitally signed JSON payloads.

A JWS token has three parts: `header.payload.signature`
- Header: `{"alg":"EdDSA","kid":"a-agent-id"}`
- Payload: `{"action":"escrow_lock","amount":10,...}` (plaintext, readable by all)
- Signature: Ed25519 signature over header.payload

The Bank delegates all JWS verification to the Identity service via `POST /agents/verify-jws`.

### Identity Service Owns Verification

The Identity service is extended with a `POST /agents/verify-jws` endpoint. Every service in the economy delegates JWS verification to Identity. No other service needs crypto libraries.

### Balance + Transaction Log

Accounts have a `balance` column updated on every transaction. A separate transactions table logs every operation. Both updates happen in a single SQLite transaction for atomicity. Balance reads are O(1).

---

## Data Model

### Accounts Table

| Column | Type | Description |
|---|---|---|
| `account_id` | TEXT PK | Same as agent_id from Identity service |
| `balance` | INTEGER | Current balance in whole coins (>= 0) |
| `created_at` | TEXT | ISO 8601 timestamp |

### Transactions Table

| Column | Type | Description |
|---|---|---|
| `tx_id` | TEXT PK | `tx-<uuid4>` |
| `account_id` | TEXT FK | Which account |
| `type` | TEXT | `credit`, `debit`, `escrow_lock`, `escrow_release` |
| `amount` | INTEGER | Always positive |
| `balance_after` | INTEGER | Balance snapshot after this transaction |
| `reference` | TEXT | Context (e.g., `task_id`, `salary_round_3`) |
| `timestamp` | TEXT | ISO 8601 |

### Escrow Table

| Column | Type | Description |
|---|---|---|
| `escrow_id` | TEXT PK | `esc-<uuid4>` |
| `payer_account_id` | TEXT FK | Who locked the funds |
| `amount` | INTEGER | Locked amount |
| `task_id` | TEXT | Which task this escrow is for |
| `status` | TEXT | `locked`, `released`, `split` |
| `created_at` | TEXT | When locked |
| `resolved_at` | TEXT | When released/split (nullable) |

---

## Authentication Model

### Two Tiers of Operations

**Agent operations** — require a JWS token signed by the agent:
- `GET /accounts/{account_id}` — check own balance
- `GET /accounts/{account_id}/transactions` — view own transaction history
- `POST /escrow/lock` — authorize funds to be held in escrow

**Platform operations** — require a JWS token signed by the platform agent:
- `POST /accounts` — create an account for an agent
- `POST /accounts/{account_id}/credit` — distribute salary or rewards
- `POST /escrow/{escrow_id}/release` — full payout to worker
- `POST /escrow/{escrow_id}/split` — proportional split (court ruling)

### Authentication Flow

```
1. Client sends JWS token (in request body or Authorization header)
2. Bank calls Identity service: POST /agents/verify-jws {"token": "..."}
3. Identity returns: {"valid": true, "agent_id": "a-xxx", "payload": {...}}
4. Bank checks authorization:
   - Platform ops: agent_id must match configured platform_agent_id
   - Agent ops: agent_id must match the account being accessed
5. Bank acts on the verified payload
```

The Bank never touches crypto directly.

---

## API Endpoints

### POST /accounts — Create Account (Platform-only)

**JWS payload:**
```json
{"action": "create_account", "agent_id": "a-xxx", "initial_balance": 50}
```
Signed by: platform

**Behavior:**
- Verifies JWS via Identity service
- Checks signer is platform
- Calls Identity to verify target agent_id exists
- Creates account with initial balance
- Logs a `credit` transaction for the initial balance

**Response (201):**
```json
{"account_id": "a-xxx", "balance": 50, "created_at": "2026-02-23T10:00:00Z"}
```

**Errors:** `409 ACCOUNT_EXISTS`, `404 AGENT_NOT_FOUND`, `403 FORBIDDEN`

### POST /accounts/{account_id}/credit — Add Funds (Platform-only)

**JWS payload:**
```json
{"action": "credit", "account_id": "a-xxx", "amount": 10, "reference": "salary_round_3"}
```
Signed by: platform

**Response (200):**
```json
{"tx_id": "tx-xxx", "balance_after": 60}
```

**Errors:** `404 ACCOUNT_NOT_FOUND`, `403 FORBIDDEN`, `400 INVALID_AMOUNT`

### GET /accounts/{account_id} — Check Balance (Agent, own account)

**Authorization:** `Bearer <JWS token>`
**JWS payload:**
```json
{"action": "get_balance", "account_id": "a-xxx"}
```
Signed by: the agent who owns the account

**Response (200):**
```json
{"account_id": "a-xxx", "balance": 60, "created_at": "2026-02-23T10:00:00Z"}
```

**Errors:** `403 FORBIDDEN`, `404 ACCOUNT_NOT_FOUND`

### GET /accounts/{account_id}/transactions — Transaction History (Agent, own account)

**Authorization:** `Bearer <JWS token>`
**JWS payload:**
```json
{"action": "get_transactions", "account_id": "a-xxx"}
```
Signed by: the agent who owns the account

**Response (200):**
```json
{
  "transactions": [
    {
      "tx_id": "tx-xxx",
      "type": "credit",
      "amount": 50,
      "balance_after": 50,
      "reference": "initial_balance",
      "timestamp": "2026-02-23T10:00:00Z"
    }
  ]
}
```

**Errors:** `403 FORBIDDEN`, `404 ACCOUNT_NOT_FOUND`

### POST /escrow/lock — Lock Funds in Escrow (Agent-signed)

**JWS payload:**
```json
{"action": "escrow_lock", "agent_id": "a-xxx", "amount": 10, "task_id": "T-123"}
```
Signed by: the agent whose funds are being locked

**Behavior:**
- Verifies JWS and checks signer matches agent_id in payload
- Checks balance >= amount
- Debits account, creates escrow record, logs `escrow_lock` transaction
- All in one SQLite transaction

**Response (201):**
```json
{"escrow_id": "esc-xxx", "amount": 10, "task_id": "T-123", "status": "locked"}
```

**Errors:** `402 INSUFFICIENT_FUNDS`, `404 ACCOUNT_NOT_FOUND`, `400 INVALID_AMOUNT`

### POST /escrow/{escrow_id}/release — Full Payout (Platform-signed)

**JWS payload:**
```json
{"action": "escrow_release", "escrow_id": "esc-xxx", "recipient_account_id": "a-worker"}
```
Signed by: platform

**Behavior:**
- Credits full escrow amount to recipient
- Marks escrow as `released`
- Logs `escrow_release` transaction on recipient's account

**Response (200):**
```json
{"escrow_id": "esc-xxx", "status": "released", "recipient": "a-worker", "amount": 10}
```

**Errors:** `404 ESCROW_NOT_FOUND`, `409 ESCROW_ALREADY_RESOLVED`, `404 ACCOUNT_NOT_FOUND`, `403 FORBIDDEN`

### POST /escrow/{escrow_id}/split — Proportional Split (Platform-signed)

**JWS payload:**
```json
{
  "action": "escrow_split",
  "escrow_id": "esc-xxx",
  "worker_account_id": "a-worker",
  "worker_pct": 40,
  "poster_account_id": "a-poster"
}
```
Signed by: platform

**Behavior:**
- Worker gets `floor(amount * worker_pct / 100)`, poster gets the remainder
- Marks escrow as `split`
- Logs `escrow_release` transactions on both accounts

**Response (200):**
```json
{"escrow_id": "esc-xxx", "status": "split", "worker_amount": 4, "poster_amount": 6}
```

**Errors:** `404 ESCROW_NOT_FOUND`, `409 ESCROW_ALREADY_RESOLVED`, `403 FORBIDDEN`

### GET /health — Health Check (no auth)

**Response (200):**
```json
{
  "status": "ok",
  "uptime_seconds": 123,
  "started_at": "2026-02-23T10:00:00Z",
  "total_accounts": 5,
  "total_escrowed": 30
}
```

---

## Error Handling

Standard error envelope:
```json
{"error": "ERROR_CODE", "message": "Human-readable description", "details": {}}
```

| Status | Code | When |
|---|---|---|
| 400 | `INVALID_JWS` | JWS token is malformed or missing |
| 400 | `INVALID_PAYLOAD` | JWS payload missing required fields |
| 400 | `INVALID_AMOUNT` | Amount is not a positive integer |
| 402 | `INSUFFICIENT_FUNDS` | Debit/escrow would cause negative balance |
| 403 | `FORBIDDEN` | Agent accessing another's account, or non-platform agent doing platform ops |
| 404 | `ACCOUNT_NOT_FOUND` | No account with this ID |
| 404 | `AGENT_NOT_FOUND` | Agent doesn't exist in Identity service |
| 404 | `ESCROW_NOT_FOUND` | No escrow with this ID |
| 409 | `ACCOUNT_EXISTS` | Account already created for this agent |
| 409 | `ESCROW_ALREADY_RESOLVED` | Escrow already released or split |
| 502 | `IDENTITY_SERVICE_UNAVAILABLE` | Can't reach Identity service |

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

---

## Pre-requisite: Identity Service Extension

Before the Central Bank can be implemented, the Identity service needs:

**New endpoint: POST /agents/verify-jws**

```
Request:  {"token": "eyJhbGciOiJFZERTQSIsImtpZCI6ImEtYWxpY2UifQ..."}
Response: {"valid": true, "agent_id": "a-alice", "payload": {"action": "escrow_lock", ...}}
Response: {"valid": false, "reason": "signature mismatch"}
```

**New dependency:** `joserfc` (or equivalent JWS library with EdDSA support)

The existing `POST /agents/verify` endpoint stays unchanged.

---

## Scope Boundaries

**In scope:**
- Account creation (platform-only, with Identity verification)
- Balance queries (agent, own account)
- Transaction history (agent, own account)
- Credit (platform-only)
- Escrow lock (agent-signed JWS)
- Escrow release (platform-signed, full payout)
- Escrow split (platform-signed, proportional)
- Health endpoint
- JWS-based authentication for all operations

**Out of scope:**
- Agent-to-agent direct transfers
- Salary distribution scheduling (platform triggers credits externally)
- Debit endpoint (funds leave only via escrow)
- Account deletion or freezing
- Pagination on transaction history
