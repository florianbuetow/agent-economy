# Central Bank Service — Authentication Specification

## Purpose

This document specifies how the Central Bank service authenticates operations using JWS tokens verified by the Identity service. It follows the same JWS authentication model established across all services in the Agent Task Economy.

## Motivation

Without authentication, any caller could create accounts, credit arbitrary funds, release escrow, or view another agent's balance and transaction history. The Central Bank is the financial backbone of the economy — unauthorized access would compromise the entire system's integrity. Authentication ensures that only authorized agents can access their own accounts and that only the platform can perform privileged financial operations (account creation, crediting, escrow release, and escrow splitting).

---

## Authentication Model

### Two Tiers of Operations

**Agent-signed operations** — require a JWS token signed by the acting agent:

| Endpoint | Token Delivery | Signer Must Be |
|----------|---------------|-----------------|
| `GET /accounts/{account_id}` | Bearer header | The account owner (`account_id`) |
| `GET /accounts/{account_id}/transactions` | Bearer header | The account owner (`account_id`) |
| `POST /escrow/lock` | Body token | The agent whose funds are being locked (`agent_id` in payload) |

**Platform-signed operations** — require a JWS token signed by the platform agent:

| Endpoint | Token Delivery | Signer Must Be |
|----------|---------------|-----------------|
| `POST /accounts` | Body token | `platform_agent_id` from config |
| `POST /accounts/{account_id}/credit` | Body token | `platform_agent_id` from config |
| `POST /escrow/{escrow_id}/release` | Body token | `platform_agent_id` from config |
| `POST /escrow/{escrow_id}/split` | Body token | `platform_agent_id` from config |

**Public operations** — no authentication:

| Endpoint | Notes |
|----------|-------|
| `GET /health` | Always public |

### Why GETs Require Auth

Balance and transaction history are private financial data. An agent's current balance reveals their economic position — how much they can bid, whether they are solvent, and how actively they participate. Transaction history reveals salary amounts, escrow patterns, and counterparties. Without authentication, any agent could surveil competitors' finances. Authentication ensures agents can only view their own accounts.

---

## JWS Token Format

All JWS tokens follow the compact serialization format (RFC 7515): `header.payload.signature`.

### JWS Header

```json
{
  "alg": "EdDSA",
  "kid": "<agent_id>"
}
```

- `alg` must be `"EdDSA"` (Ed25519)
- `kid` is the signer's agent ID (e.g., `"a-alice-uuid"` for agent operations, or the platform agent ID for platform operations)

### JWS Payload

Every JWS payload must include an `action` field that identifies the operation. This prevents cross-operation token replay — a token signed for `"escrow_lock"` cannot be used for `"get_balance"`.

### Action Values

| Action | Endpoint | Expected Signer |
|--------|----------|-----------------|
| `create_account` | `POST /accounts` | Platform |
| `credit` | `POST /accounts/{account_id}/credit` | Platform |
| `get_balance` | `GET /accounts/{account_id}` | Agent (account owner) |
| `get_transactions` | `GET /accounts/{account_id}/transactions` | Agent (account owner) |
| `escrow_lock` | `POST /escrow/lock` | Agent (fund owner) |
| `escrow_release` | `POST /escrow/{escrow_id}/release` | Platform |
| `escrow_split` | `POST /escrow/{escrow_id}/split` | Platform |

---

## Token Delivery Mechanisms

Tokens are delivered in two ways depending on the endpoint type:

### Body Token (POST Endpoints)

For all POST endpoints, the token is in the JSON request body:

```json
{
  "token": "<JWS compact token>"
}
```

The JWS payload contains the operation data (action, amounts, IDs, etc.). The Central Bank extracts all operation parameters from the verified JWS payload, not from separate body fields.

### Bearer Token (GET Endpoints)

For GET endpoints (`GET /accounts/{account_id}` and `GET /accounts/{account_id}/transactions`), the token is in the HTTP header:

```
Authorization: Bearer <JWS compact token>
```

GET endpoints have no request body. The JWS payload carries the `action` field and optionally the `account_id` for cross-validation against the URL path parameter.

---

## Authentication Flow

```
Client                         Central Bank                    Identity Service
  |                                  |                                |
  |  1. Construct JWS payload        |                                |
  |     { action, ... }              |                                |
  |                                  |                                |
  |  2. Sign with Ed25519            |                                |
  |     private key                  |                                |
  |                                  |                                |
  |  3. Send request                 |                                |
  |     (body token or Bearer)       |                                |
  |  -------------------------------->                                |
  |                                  |  4. POST /agents/verify-jws    |
  |                                  |     { "token": "eyJ..." }      |
  |                                  |  ------------------------------>|
  |                                  |                                |
  |                                  |                5. Decode JWS   |
  |                                  |                6. Look up kid's |
  |                                  |                   public key   |
  |                                  |                7. Verify Ed25519|
  |                                  |                   signature    |
  |                                  |                                |
  |                                  |  8. { valid: true,             |
  |                                  |       agent_id: "a-xxx",       |
  |                                  |       payload: {...} }         |
  |                                  |  <------------------------------|
  |                                  |                                |
  |                                  |  9. Validate action field      |
  |                                  | 10. Check authorization:       |
  |                                  |     Platform ops → signer must |
  |                                  |       match platform_agent_id  |
  |                                  |     Agent ops → signer must    |
  |                                  |       match account owner      |
  |                                  | 11. Validate payload fields    |
  |                                  | 12. Execute operation          |
  |                                  |                                |
  | 13. Response                     |                                |
  |  <--------------------------------                                |
```

The Central Bank never touches crypto directly. All signature verification is delegated to the Identity service.

---

## Payload Validation Rules

After the Identity service confirms the JWS is valid, the Central Bank validates the payload:

1. **Action must match the endpoint.** Each endpoint expects a specific `action` value (see Action Values table). A mismatched action returns `INVALID_PAYLOAD`.

2. **Required payload fields must be present.** Each action has required fields:
   - `create_account`: `agent_id`, `initial_balance`
   - `credit`: `amount`, `reference` (and optionally `account_id`)
   - `get_balance`: (optionally `account_id`)
   - `get_transactions`: (optionally `account_id`)
   - `escrow_lock`: `agent_id`, `amount`, `task_id`
   - `escrow_release`: `recipient_account_id` (and optionally `escrow_id`)
   - `escrow_split`: `worker_account_id`, `worker_pct`, `poster_account_id` (and optionally `escrow_id`)

3. **Payload fields must match URL parameters.** When a payload contains a field that also appears in the URL path, they must match:
   - `account_id` in the payload must match `{account_id}` in the URL (for credit, get_balance, get_transactions)
   - `escrow_id` in the payload must match `{escrow_id}` in the URL (for escrow_release, escrow_split)
   - A mismatch returns `PAYLOAD_MISMATCH`.

---

## Authorization Rules

After the Identity service confirms the JWS is valid and returns the signer's `agent_id`:

### Platform Operations

1. **Signer must be the platform agent.** For `POST /accounts`, `POST /accounts/{account_id}/credit`, `POST /escrow/{escrow_id}/release`, and `POST /escrow/{escrow_id}/split`, the verified `agent_id` must match `settings.platform.agent_id`. If it does not, return `403 FORBIDDEN`.

2. **No ownership checks.** Platform operations do not check whether the signer "owns" the target resource. The platform has global privileges.

### Agent Operations — Escrow Lock

1. **Signer must match `agent_id` in payload.** For `POST /escrow/lock`, the verified `agent_id` must match the `agent_id` field in the JWS payload. An agent can only lock their own funds.

### Agent Operations — Balance and Transactions

1. **Signer must match the account being accessed.** For `GET /accounts/{account_id}` and `GET /accounts/{account_id}/transactions`, the verified `agent_id` must match the `account_id` in the URL path. An agent can only view their own account.

---

## Error Codes

Authentication-related errors used by the Central Bank service:

| Status | Code | When |
|--------|------|------|
| 400 | `INVALID_JWS` | JWS token is malformed, missing, empty, or not a string |
| 400 | `INVALID_PAYLOAD` | JWS payload is missing `action`, `action` does not match the expected value for this endpoint, or required payload fields are missing |
| 400 | `PAYLOAD_MISMATCH` | JWS payload field does not match URL parameter (e.g., `account_id` or `escrow_id` mismatch), or duplicate credit reference with a different amount |
| 403 | `FORBIDDEN` | JWS signature verification failed (Identity says `valid: false`), signer does not match the required agent, or non-platform agent attempting a platform operation |
| 502 | `IDENTITY_SERVICE_UNAVAILABLE` | Cannot reach the Identity service for JWS verification, or Identity service returned an unexpected response (connection failure, timeout, non-200 with non-JSON body) |

All errors follow the standard error envelope:

```json
{
  "error": "ERROR_CODE",
  "message": "Human-readable description of what went wrong",
  "details": {}
}
```

### Error Precedence

Errors are checked in this order (first match wins):

1. `415 UNSUPPORTED_MEDIA_TYPE` — wrong Content-Type
2. `413 PAYLOAD_TOO_LARGE` — body exceeds `request.max_body_size`
3. `400 INVALID_JSON` — malformed JSON body
4. `400 INVALID_JWS` — missing or malformed `token` field (POST), or missing/malformed Bearer token (GET)
5. `502 IDENTITY_SERVICE_UNAVAILABLE` — Identity service unreachable or returned an unexpected response
6. `403 FORBIDDEN` — Identity service says signature is invalid (`valid: false`)
7. `400 INVALID_PAYLOAD` — wrong `action`, missing required payload fields
8. `400 PAYLOAD_MISMATCH` — payload field does not match URL parameter
9. `403 FORBIDDEN` — signer does not match the expected agent (non-platform agent doing platform ops, or agent accessing another's account)
10. Domain-specific errors (`ACCOUNT_NOT_FOUND`, `ACCOUNT_EXISTS`, `INSUFFICIENT_FUNDS`, `ESCROW_NOT_FOUND`, `ESCROW_ALREADY_RESOLVED`, `ESCROW_ALREADY_LOCKED`, `AGENT_NOT_FOUND`, `INVALID_AMOUNT`)

### Notes on Error Mapping

- **Invalid signature** returns `403 FORBIDDEN`. The Identity service confirmed the token's signature does not match the claimed signer's public key — this is an authentication failure. There is no `401` in this system because there is no challenge-response mechanism (no `WWW-Authenticate` header).
- **Signer mismatch** also returns `403 FORBIDDEN` with a different message. The token is cryptographically valid, but the signer is not authorized for the operation. Both cases use the same status code but carry different `message` text for debugging.
- **Identity service down or misbehaving** returns `502 IDENTITY_SERVICE_UNAVAILABLE`. This covers connection failures, timeouts, and unexpected responses (e.g., Identity returns `500` with a non-JSON body). The service does not fall back to unauthenticated mode.

---

## Configuration

### Identity Integration

```yaml
identity:
  base_url: "http://localhost:8001"
  verify_jws_path: "/agents/verify-jws"
  get_agent_path: "/agents"
```

- `base_url`: Base URL of the Identity service.
- `verify_jws_path`: Path to the JWS verification endpoint. Used for all authenticated requests.
- `get_agent_path`: Path prefix for agent lookup. Used during account creation to verify the target agent exists (`GET /agents/{agent_id}`).

### Platform Agent

```yaml
platform:
  agent_id: ""
```

- `platform.agent_id`: The agent ID of the platform, registered with the Identity service. Used to verify incoming platform-signed tokens — the signer's `agent_id` returned by the Identity service must match this value for platform operations.

All fields are required. Missing fields cause startup failure.

---

## Infrastructure

### IdentityClient

The service initializes an `IdentityClient` during startup (in `lifespan.py`) using the `identity` config section. The client is stored in `AppState` and closed on shutdown.

The `IdentityClient` provides:
- `verify_jws(token: str) -> dict[str, Any]` — calls `POST /agents/verify-jws` on the Identity service. On success (`200` with `valid: true`), returns the full response body: `{"valid": true, "agent_id": "...", "payload": {...}}`. Raises `ServiceError("IDENTITY_SERVICE_UNAVAILABLE", ..., 502)` on connection failure, timeout, or unexpected response. Propagates Identity service error codes for non-200 responses with a valid JSON error envelope.
- `get_agent(agent_id: str) -> dict[str, Any]` — calls `GET /agents/{agent_id}` on the Identity service. Used during account creation to verify the target agent exists. Raises `ServiceError("AGENT_NOT_FOUND", ..., 404)` if the agent does not exist, or `ServiceError("IDENTITY_SERVICE_UNAVAILABLE", ..., 502)` on connection failure.
- `close()` — closes the underlying `httpx.AsyncClient`. Called during lifespan shutdown.

### Dependencies

Required in `pyproject.toml`:
- `httpx` — async HTTP client for Identity service calls

---

## Interaction Patterns

### Account Creation (Platform Operation)

```
Platform                        Central Bank                    Identity Service
  |                                  |                                |
  |  1. Construct JWS payload:       |                                |
  |     { action: create_account,    |                                |
  |       agent_id, initial_balance }|                                |
  |                                  |                                |
  |  2. Sign with platform           |                                |
  |     Ed25519 private key          |                                |
  |                                  |                                |
  |  3. POST /accounts               |                                |
  |     { "token": "eyJ..." }       |                                |
  |  -------------------------------->                                |
  |                                  |  4. POST /agents/verify-jws    |
  |                                  |     { "token": "eyJ..." }      |
  |                                  |  ------------------------------>|
  |                                  |                                | 5. Verify signature
  |                                  |  6. { valid: true,             |
  |                                  |       agent_id: platform }     |
  |                                  |  <------------------------------|
  |                                  |                                |
  |                                  |  7. Assert signer == platform  |
  |                                  |  8. GET /agents/{agent_id}     |
  |                                  |  ------------------------------>|
  |                                  |                                | 9. Look up agent
  |                                  |  10. { agent_id, name, ... }   |
  |                                  |  <------------------------------|
  |                                  |                                |
  |                                  | 11. Create account + credit tx |
  |                                  |                                |
  | 12. 201 { account_id, balance,   |                                |
  |           created_at }           |                                |
  |  <--------------------------------                                |
```

### Escrow Lock (Agent Operation)

```
Agent                           Central Bank                    Identity Service
  |                                  |                                |
  |  1. Construct JWS payload:       |                                |
  |     { action: escrow_lock,       |                                |
  |       agent_id, amount, task_id }|                                |
  |                                  |                                |
  |  2. Sign with Ed25519 private key|                                |
  |                                  |                                |
  |  3. POST /escrow/lock            |                                |
  |     { "token": "eyJ..." }       |                                |
  |  -------------------------------->                                |
  |                                  |  4. POST /agents/verify-jws    |
  |                                  |     { "token": "eyJ..." }      |
  |                                  |  ------------------------------>|
  |                                  |                                | 5. Verify signature
  |                                  |  6. { valid: true,             |
  |                                  |       agent_id: agent }        |
  |                                  |  <------------------------------|
  |                                  |                                |
  |                                  |  7. Assert signer == agent_id  |
  |                                  |  8. Check balance >= amount    |
  |                                  |  9. Debit + create escrow      |
  |                                  |                                |
  | 10. 201 { escrow_id, amount,     |                                |
  |           task_id, status }      |                                |
  |  <--------------------------------                                |
```

### Balance Check (Agent Operation, Bearer Token)

```
Agent                           Central Bank                    Identity Service
  |                                  |                                |
  |  1. Construct JWS payload:       |                                |
  |     { action: get_balance,       |                                |
  |       account_id }               |                                |
  |                                  |                                |
  |  2. Sign with Ed25519 private key|                                |
  |                                  |                                |
  |  3. GET /accounts/{account_id}   |                                |
  |     Authorization: Bearer eyJ... |                                |
  |  -------------------------------->                                |
  |                                  |  4. POST /agents/verify-jws    |
  |                                  |     { "token": "eyJ..." }      |
  |                                  |  ------------------------------>|
  |                                  |                                | 5. Verify signature
  |                                  |  6. { valid: true,             |
  |                                  |       agent_id: agent }        |
  |                                  |  <------------------------------|
  |                                  |                                |
  |                                  |  7. Assert signer == account_id|
  |                                  |  8. Look up account            |
  |                                  |                                |
  |  9. 200 { account_id, balance,   |                                |
  |           created_at }           |                                |
  |  <--------------------------------                                |
```

### Identity Service Down

```
Client                          Central Bank                    Identity Service
  |                                  |                                |
  |  POST /escrow/lock               |                                |
  |  { "token": "eyJ..." }         |                                |
  |  -------------------------------->                                |
  |                                  |  POST /agents/verify-jws       |
  |                                  |  ------------------------------>| (connection refused
  |                                  |                                |  or timeout)
  |                                  |                                |
  |  502 { error:                    |                                |
  |    IDENTITY_SERVICE_UNAVAILABLE }|                                |
  |  <--------------------------------                                |
```

### Impersonation Attempt (Escrow Lock)

```
Mallory                         Central Bank                    Identity Service
  |                                  |                                |
  |  Signs JWS as mallory but sets   |                                |
  |  agent_id: alice in payload      |                                |
  |                                  |                                |
  |  POST /escrow/lock               |                                |
  |  { "token": "eyJ..." }         |                                |
  |  -------------------------------->                                |
  |                                  |  POST /agents/verify-jws       |
  |                                  |  ------------------------------>|
  |                                  |  { valid: true,                |
  |                                  |    agent_id: mallory }         |
  |                                  |  <------------------------------|
  |                                  |                                |
  |                                  |  mallory != alice -> 403       |
  |                                  |                                |
  |  403 { error: FORBIDDEN }       |                                |
  |  <--------------------------------                                |
```

### Unauthorized Platform Operation

```
Agent                           Central Bank                    Identity Service
  |                                  |                                |
  |  POST /accounts                  |                                |
  |  { "token": "eyJ..." }         |                                |
  |  (signed by regular agent,       |                                |
  |   not platform)                  |                                |
  |  -------------------------------->                                |
  |                                  |  POST /agents/verify-jws       |
  |                                  |  ------------------------------>|
  |                                  |  { valid: true,                |
  |                                  |    agent_id: a-agent }         |
  |                                  |  <------------------------------|
  |                                  |                                |
  |                                  |  a-agent != platform_id -> 403 |
  |                                  |                                |
  |  403 { error: FORBIDDEN }       |                                |
  |  <--------------------------------                                |
```

---

## Token Replay Considerations

The `action` field in every JWS payload prevents cross-operation replay. A token signed for `"escrow_lock"` cannot be used for `"get_balance"`.

However, same-operation replay is possible. This is mitigated by:

- **Escrow lock uniqueness:** `(payer_account_id, task_id)` is unique for locked escrows. A replayed escrow lock token with the same payer and task returns the existing escrow (idempotent) or fails if the amount differs (`409 ESCROW_ALREADY_LOCKED`).
- **Credit idempotency:** `(account_id, reference)` is unique for credit transactions. A replayed credit token with the same account and reference returns the original result (idempotent) or fails if the amount differs (`400 PAYLOAD_MISMATCH`).
- **Escrow release/split status checks:** Escrow must be in `locked` status. Once released or split, a replayed token gets `409 ESCROW_ALREADY_RESOLVED`.

Full replay protection (timestamps, nonces) is out of scope, consistent with the Identity service design.

---

## What This Specification Does NOT Cover

- **Endpoint behavior details** — request/response formats, data validation, business logic, and database operations are specified in the Central Bank Service API Specification.
- **Rate limiting** — no throttling on authenticated or unauthenticated endpoints.
- **Token expiry** — JWS tokens have no expiry. Replay protection is the caller's responsibility, consistent with the Identity service's design.
- **Platform key management** — the Central Bank does not hold the platform's private key. Unlike the Task Board (which signs outgoing escrow requests as the platform), the Central Bank only verifies incoming platform-signed tokens. The platform's private key is held by the caller (the orchestrator or Task Board).
