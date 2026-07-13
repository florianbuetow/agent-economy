# Central Bank Service — Authentication Specification

## Purpose

This document specifies how the Central Bank service authenticates operations using JWS tokens. The Central Bank follows the **two-tier verification model** ratified across the economy (delivery-governance § Platform auth model, T-021): *agent-signed* operations verify via a remote call to the Identity service, while *platform-signed* operations verify **locally** via the service's own `PlatformAgent.validate_certificate()`. This is not a single uniform model — the two tiers have different failure modes, different error precedence, and different resilience characteristics, and this document treats them separately throughout.

## Motivation

Without authentication, any caller could create accounts, credit arbitrary funds, release escrow, or view another agent's balance and transaction history. The Central Bank is the financial backbone of the economy — unauthorized access would compromise the entire system's integrity. Authentication ensures that only authorized agents can access their own accounts and that only the platform can perform privileged financial operations (crediting, escrow release, and escrow splitting), with one documented exception for account creation.

Local verification for the platform-signed tier exists so those three endpoints — `credit`, `escrow_release`, `escrow_split` — keep working through an Identity outage: they are the operations the Task Board depends on to settle every task, and settlement should not stall because Identity is down.

---

## Authentication Model

### Two Tiers of Operations

**Agent-signed operations** — verified via a network call to Identity's `POST /agents/verify-jws`:

| Endpoint | Token Delivery | Signer Must Be |
|----------|---------------|-----------------|
| `GET /accounts/{account_id}` | Bearer header | The account owner (`account_id`) |
| `GET /accounts/{account_id}/transactions` | Bearer header | The account owner (`account_id`) |
| `POST /escrow/lock` | Body token | The agent whose funds are being locked (`agent_id` in payload) |
| `POST /accounts` | Body token | Any registered agent (self-service, `initial_balance: 0`) **or** the platform (any balance) — see the exception below |

**Platform-signed operations** — verified **locally**, no Identity round trip:

| Endpoint | Token Delivery | Signer Must Be |
|----------|---------------|-----------------|
| `POST /accounts/{account_id}/credit` | Body token | The registered platform agent |
| `POST /escrow/{escrow_id}/release` | Body token | The registered platform agent |
| `POST /escrow/{escrow_id}/split` | Body token | The registered platform agent |

**Public operations** — no authentication:

| Endpoint | Notes |
|----------|-------|
| `GET /health` | Always public |

### The `create_account` Exception

`POST /accounts` is listed under "agent-signed operations" above even though its platform-funded mode is, by nature, a platform operation. It is the **one documented exception** to "platform operations verify locally": both of its modes — self-service (any agent, zero balance) and platform-funded (platform, any balance) — verify via Identity, never via local `PlatformAgent.validate_certificate()`.

Rationale (recorded as an accepted deviation): the endpoint has a hard Identity dependency regardless of signer, because it must verify the *target* agent exists (`GET /agents/{agent_id}`) before creating an account for them — an Identity outage already blocks this endpoint on that check alone, so local platform verification would buy no additional outage resilience here. Separately, telling the two modes apart requires knowing the verified signer's identity either way (`is_platform = caller_agent_id == <registered PlatformAgent>.agent_id`), which a purely-local certificate check on a single fixed platform key cannot provide for a non-platform signer. Revisit only if the agent-existence check ever moves off Identity (e.g. to a gateway read).

### Why GETs Require Auth

Balance and transaction history are private financial data. An agent's current balance reveals their economic position — how much they can bid, whether they are solvent, and how actively they participate. Transaction history reveals income amounts, escrow patterns, and counterparties. Without authentication, any agent could surveil competitors' finances. Authentication ensures agents can only view their own accounts.

---

## JWS Token Format

All JWS tokens follow the compact serialization format (RFC 7515): `header.payload.signature`.

### JWS Header

```json
{
  "alg": "EdDSA",
  "typ": "JWT",
  "kid": "<agent_id>"
}
```

- `alg` must be `"EdDSA"` (Ed25519); `typ` is always `"JWT"`.
- `kid` is the signer's agent ID (e.g., `"a-alice-uuid"` for agent operations, or the platform's registered `agent_id` for platform operations). `kid` is informational for local platform verification — see "How Local Platform Verification Determines the Signer" below.
- When the signer is configured with a token lifetime (`signing.token_ttl_seconds` in `agents/config.yaml`; production's platform signer uses 300 seconds), the header additionally carries `iat` and `exp` (Unix timestamps). Tokens without `iat`/`exp` are accepted indefinitely (legacy tolerance) — full replay protection (nonces) is out of scope.

### JWS Payload

Every JWS payload must include an `action` field that identifies the operation. This prevents cross-operation token replay — a token signed for `"escrow_lock"` cannot be used for `"get_balance"`.

### Action Values

| Action | Endpoint | Expected Signer |
|--------|----------|-----------------|
| `create_account` | `POST /accounts` | Any agent (self, zero balance) or the platform (any balance) |
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

### Agent-Signed Operations (Identity-Verified)

```
Client                         Central Bank                    Identity Service
  |                                  |                                |
  |  1. Construct JWS payload        |                                |
  |     { action, ... }              |                                |
  |  2. Sign with Ed25519            |                                |
  |     private key                  |                                |
  |  3. Send request                 |                                |
  |     (body token or Bearer)       |                                |
  |  -------------------------------->                                |
  |                                  |  4. POST /agents/verify-jws    |
  |                                  |     { token: "..." }           |
  |                                  |  ------------------------------>|
  |                                  |                                | 5. Verify signature
  |                                  |  6. { valid, agent_id, payload}|
  |                                  |  <------------------------------|
  |                                  |  7. Ownership/impersonation    |
  |                                  |     check (endpoint-specific;  |
  |                                  |     may run before or after    |
  |                                  |     payload validation — see   |
  |                                  |     Error Precedence)          |
  |                                  |  8. Payload validation          |
  |                                  |  9. Execute operation           |
  | 10. Response                     |                                |
  |  <--------------------------------                                |
```

### Platform-Signed Operations (Locally Verified)

```
Client (Task Board, acting        Central Bank (with registered
as platform signer)               PlatformAgent)
  |                                  |
  |  1. Construct JWS payload        |
  |     { action, ... }              |
  |  2. Sign with the platform's     |
  |     Ed25519 private key          |
  |  3. POST body token              |
  |  -------------------------------->
  |                                  |  4. Decode payload segment WITHOUT
  |                                  |     verifying the signature
  |                                  |  5. Validate payload shape
  |                                  |     (action, required fields,
  |                                  |     URL-parameter matches) — 400s
  |                                  |     here happen BEFORE step 6
  |                                  |  6. Verify signature locally:
  |                                  |     PlatformAgent.validate_certificate(
  |                                  |       token)
  |                                  |     — checks the Ed25519 signature
  |                                  |     against the registered platform
  |                                  |     public key only. Success alone
  |                                  |     proves the signer is the platform;
  |                                  |     there is no separate identity
  |                                  |     lookup by kid.
  |                                  |  7. Execute operation
  | 8. Response                      |
  |  <--------------------------------
```

The Central Bank performs cryptographic verification locally for platform ops using its own registered `PlatformAgent` instance. No external service call is needed. Agent-signed ops (including `create_account`) still call Identity.

### How Local Platform Verification Determines the Signer

`PlatformAgent.validate_certificate(token)` calls `verify_jws(token, self._public_key)` — it verifies the Ed25519 signature against the **service's own registered platform public key**, not a `kid`-indexed lookup across many agents' keys. A successful verification is therefore already proof the token was signed by the platform's private key; the router code does not additionally compare the `kid` header (or any returned identity) against a configured platform id for `credit`/`escrow_release`/`escrow_split`. This differs from `create_account`, `escrow_lock`, `get_balance`, and `get_transactions`, where Identity returns the *actual verified signer's* `agent_id` (which could be any registered agent), and the Central Bank must explicitly compare it against the account/agent named in the request.

---

## Payload Validation Rules

The Central Bank validates the following payload shape rules. **The order relative to signature verification differs by tier — see Error Precedence.**

1. **Action must match the endpoint.** Each endpoint expects a specific `action` value (see Action Values table). A mismatched action returns `invalid_payload`.

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
   - A mismatch returns `payload_mismatch`.

---

## Authorization Rules

### `create_account` (Agent-Signed, Identity-Verified)

1. The verified signer may always create an account **for themselves** with `initial_balance: 0`.
2. The verified signer may create an account **for another agent, or with a non-zero balance, only if the signer is the registered platform agent** (`caller_agent_id == <registered PlatformAgent>.agent_id`). Otherwise: `403 forbidden`.

### Platform Operations (`credit`, `escrow_release`, `escrow_split`)

1. **Signer must be the platform agent.** Enforced implicitly: local verification only succeeds for tokens signed with the platform's private key (see "How Local Platform Verification Determines the Signer" above). There is no separate ownership check beyond the certificate verification itself.
2. **No ownership checks on the target resource.** Platform operations do not check whether the signer "owns" the target account/escrow. The platform has global privileges.

### Agent Operations — Escrow Lock

1. **Signer must match `agent_id` in payload.** For `POST /escrow/lock`, the Identity-verified `agent_id` must match the `agent_id` field in the JWS payload. An agent can only lock their own funds.

### Agent Operations — Balance and Transactions

1. **Signer must match the account being accessed.** For `GET /accounts/{account_id}` and `GET /accounts/{account_id}/transactions`, the Identity-verified `agent_id` must match the `account_id` in the URL path. An agent can only view their own account.

---

## Error Codes

Authentication-related errors used by the Central Bank service:

| Status | Code | When |
|--------|------|------|
| 400 | `invalid_jws` | JWS token is malformed, missing, empty, or not a string |
| 400 | `invalid_payload` | JWS payload is missing `action`, `action` does not match the expected value for this endpoint, or required payload fields are missing |
| 400 | `payload_mismatch` | JWS payload field does not match URL parameter (e.g., `account_id` or `escrow_id` mismatch), or duplicate credit reference with a different amount |
| 401 | `token_expired` | Local platform verification found the token's header `exp` claim in the past (`credit`, `escrow_release`, `escrow_split` only) |
| 403 | `forbidden` | JWS signature verification failed (Identity says `valid: false`, or local certificate verification failed), signer does not match the required agent/account, or a non-platform signer attempted a platform-only action on `create_account` |
| 502 | `identity_service_unavailable` | Cannot reach the Identity service for JWS verification or agent lookup (agent-signed ops and `create_account` only) |
| 503 | `service_not_ready` | The ledger, Identity client, or the platform agent has not finished initializing |

All errors follow the standard error envelope:

```json
{
  "error": "error_code",
  "message": "Human-readable description of what went wrong",
  "details": {}
}
```

### Error Precedence

**The order is not uniform across endpoints** — it differs by verification tier, and the task board plan (WP-03/T-033) deliberately inverted it for the local-verification tier only:

**Platform-signed tier — `credit`, `escrow_release`, `escrow_split` (payload validation BEFORE signature verification):**

1. `415 unsupported_media_type` — wrong Content-Type
2. `413 payload_too_large` — body exceeds `request.max_body_size`
3. `400 invalid_json` — malformed JSON body
4. `400 invalid_jws` — missing/malformed `token` field, or a token that is not a well-formed three-part JWS
5. `400 invalid_payload` / `400 payload_mismatch` / `400 invalid_amount` — payload shape and cross-field validation, performed on the **unverified, decoded** payload
6. `401 token_expired` / `403 forbidden` — local certificate verification (signature invalid, or the token has expired)
7. Domain-specific errors (`account_not_found`, `escrow_not_found`, `escrow_already_resolved`, …)

**Agent-signed tier — `create_account`, `escrow_lock`, `get_balance`, `get_transactions` (signature verification BEFORE most payload validation):**

1. `415 unsupported_media_type` / `413 payload_too_large` (POST endpoints only)
2. `400 invalid_json` (POST endpoints only)
3. `400 invalid_jws` — missing/malformed `token` field (POST), or missing/malformed Bearer token (GET)
4. `403 forbidden` / `502 identity_service_unavailable` — remote verification via Identity's `POST /agents/verify-jws`
5. `403 forbidden` — ownership/impersonation check (`get_balance`/`get_transactions`: verified signer must equal the URL's `account_id`; `escrow_lock`: verified signer must equal the payload's `agent_id`, checked after the `action`/field-presence checks for that endpoint; `create_account`: verified signer must equal the payload's `agent_id` for self-service, checked after the `action`/`agent_id`-presence checks)
6. `400 invalid_payload` / `400 payload_mismatch` / `400 invalid_amount` — remaining payload validation
7. Domain-specific errors (`account_not_found`, `agent_not_found`, `account_exists`, `insufficient_funds`, `escrow_already_locked`, …)

Concretely: on `get_balance`/`get_transactions`, a wrong-owner request returns `403 forbidden` even when its `action` field is also wrong — the ownership check runs before the action check. On `credit`/`escrow_release`/`escrow_split`, the reverse holds: a malformed payload from a non-platform signer returns its specific `400` code, never `403`.

### Notes on Error Mapping

- **Invalid signature** returns `403 forbidden`. There is no `401` in this system for signature failures — there is no challenge-response mechanism (no `WWW-Authenticate` header). The one exception is `401 token_expired`, used specifically for a locally-verified platform token whose `exp` claim has passed; this is a distinct condition from an invalid signature.
- **Signer mismatch** also returns `403 forbidden` with a different message. The token is cryptographically valid, but the signer is not authorized for the operation. Both cases use the same status code but carry different `message` text for debugging.
- **Identity service down or misbehaving** returns `502 identity_service_unavailable`. This applies only to agent-signed operations and `create_account`'s Identity dependency (both the JWS verification call and the `GET /agents/{agent_id}` existence check). It never applies to `credit`, `escrow_release`, or `escrow_split` — those do not call Identity at all.
- **Uninitialized dependencies** return `503 service_not_ready` — the ledger, Identity client, or platform agent has not finished starting up (or, for `GET /health`, the DB Gateway backing the ledger is unreachable).

---

## Configuration

### Identity Integration (Agent-Signed Verification and Agent Existence Checks)

```yaml
identity:
  base_url: "http://localhost:8001"
  get_agent_path: "/agents"
  verify_jws_path: "/agents/verify-jws"
  timeout_seconds: 10
```

- `base_url`: Base URL of the Identity service.
- `get_agent_path`: Path prefix for agent lookup. Used during account creation to verify the target agent exists (`GET /agents/{agent_id}`).
- `verify_jws_path`: Path to the JWS verification endpoint. Used for `create_account`, `escrow_lock`, `get_balance`, and `get_transactions`.
- `timeout_seconds`: Timeout for Identity HTTP calls.

Identity is used for agent-signed operations only. `credit`, `escrow_release`, and `escrow_split` never call it.

### Platform Agent

```yaml
platform:
  agent_config_path: "../../agents/config.yaml"
```

- `platform.agent_config_path`: Path (resolved relative to this service's own config directory, unless absolute) to the shared `agents/config.yaml`. At startup, `service_auth.factory.AgentFactory(config_path=...)` loads the `platform` roster entry's Ed25519 keypair from the configured `keys_dir`, constructs a `PlatformAgent`, registers it with Identity (`await platform_agent.register()`), and stores it as the service's `state.platform_agent`. **There is no `platform.agent_id`, `platform.public_key_path`, or `platform.private_key_path` field in this service's own config** — those were a superseded placeholder shape; the platform's identity and keys always come from the registration described above. If `agent_config_path` is empty or absent, `state.platform_agent` stays `None` and every platform-op authorization check and `get_platform_agent_id()` call fails closed with `503 service_not_ready` — there is no configured fallback identity.

---

## Infrastructure

### IdentityClient

The service initializes an `IdentityClient` during startup (in `core/lifespan.py`) using the `identity` config section. The client is stored in `AppState` and closed on shutdown.

The `IdentityClient` (from `libs/service-clients`) provides:
- `verify_jws(token: str) -> dict[str, Any]` — calls `POST /agents/verify-jws` on the Identity service. On success (`200` with `valid: true`), returns the full response body: `{"valid": true, "agent_id": "...", "payload": {...}}`. A connection failure or timeout raises `ServiceError("identity_service_unavailable", ..., 502)`. A non-200, non-connection-failure response propagates Identity's own error envelope (its `error`/`message`/`details`/status code) as-is.
- `get_agent(agent_id: str) -> dict[str, Any] | None` — calls `GET /agents/{agent_id}` on the Identity service; returns `None` on a `404` (the router turns this into `ServiceError("agent_not_found", ..., 404)`), or raises `ServiceError("identity_service_unavailable", ..., 502)` on connection failure or timeout.
- `close()` — closes the underlying `httpx.AsyncClient`. Called during lifespan shutdown.

### PlatformAgent

Loaded at startup via `AgentFactory(config_path=...).platform_agent()` (see Configuration above). Provides `validate_certificate(token: str) -> dict[str, object]`, which verifies the Ed25519 signature against the agent's own public key and returns the decoded payload; it raises `cryptography.exceptions.InvalidSignature` or `ValueError` on a bad signature/malformed token (mapped to `403 forbidden`), and `service_auth.signing.TokenExpiredError` if the header's `exp` claim has passed (mapped to `401 token_expired`).

### Dependencies

Required in `pyproject.toml`:
- `httpx` — async HTTP client for Identity and DB Gateway calls
- `service-auth` — `PlatformAgent`, `AgentFactory`
- `service-clients` — `IdentityClient`, `GatewayClient`

---

## Interaction Patterns

### Account Creation (Identity-Verified for Both Modes)

```
Caller                           Central Bank                    Identity Service
  |                                  |                                |
  |  1. Construct JWS payload:       |                                |
  |     { action: create_account,    |                                |
  |       agent_id, initial_balance }|                                |
  |  2. Sign with own Ed25519        |                                |
  |     private key                  |                                |
  |  3. POST /accounts               |                                |
  |     { "token": "eyJ..." }       |                                |
  |  -------------------------------->                                |
  |                                  |  4. POST /agents/verify-jws    |
  |                                  |     { "token": "eyJ..." }      |
  |                                  |  ------------------------------>|
  |                                  |                                | 5. Verify signature
  |                                  |  6. { valid: true,             |
  |                                  |       agent_id: signer }       |
  |                                  |  <------------------------------|
  |                                  |                                |
  |                                  |  7. is_platform = signer ==    |
  |                                  |       registered PlatformAgent |
  |                                  |  8. Enforce mode rules (own     |
  |                                  |     account + zero balance      |
  |                                  |     unless platform)            |
  |                                  |  9. GET /agents/{agent_id}     |
  |                                  |  ------------------------------>|
  |                                  |                                | 10. Look up agent
  |                                  | 11. { agent_id, name, ... }    |
  |                                  |  <------------------------------|
  |                                  |                                |
  |                                  | 12. Create account + credit tx |
  |                                  |                                |
  | 13. 201 { account_id, balance,   |                                |
  |           created_at }           |                                |
  |  <--------------------------------                                |
```

### Escrow Lock (Agent Operation, Identity-Verified)

```
Agent                           Central Bank                    Identity Service
  |                                  |                                |
  |  1. Construct JWS payload:       |                                |
  |     { action: escrow_lock,       |                                |
  |       agent_id, amount, task_id }|                                |
  |  2. Sign with Ed25519 private key|                                |
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

### Balance Check (Agent Operation, Bearer Token, Identity-Verified)

```
Agent                           Central Bank                    Identity Service
  |                                  |                                |
  |  1. Construct JWS payload:       |                                |
  |     { action: get_balance,       |                                |
  |       account_id }               |                                |
  |  2. Sign with Ed25519 private key|                                |
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

### Platform Credit While Identity Is Down (Local Verification Survives the Outage)

```
Task Board                      Central Bank                    Identity Service
  |                                  |                                |
  |  POST /accounts/{id}/credit      |                                |
  |  { "token": "<platform JWS>" } |                                |
  |  -------------------------------->                                | (unreachable)
  |                                  |  Decode payload (no Identity   |
  |                                  |  call) → validate shape →      |
  |                                  |  verify signature LOCALLY      |
  |                                  |  against registered            |
  |                                  |  PlatformAgent's public key    |
  |                                  |                                |
  |  200 { tx_id, balance_after }    |                                |
  |  <--------------------------------                                |
```

### Escrow Lock While Identity Is Down (Agent Op — Fails Cleanly, No Fallback)

```
Agent                            Central Bank                    Identity Service
  |                                  |                                |
  |  POST /escrow/lock               |                                |
  |  { "token": "eyJ..." }         |                                |
  |  -------------------------------->                                |
  |                                  |  POST /agents/verify-jws       |
  |                                  |  ------------------------------>| (connection refused
  |                                  |                                |  or timeout)
  |                                  |                                |
  |  502 { error:                    |                                |
  |    identity_service_unavailable }|                                |
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
  |  403 { error: forbidden }       |                                |
  |  <--------------------------------                                |
```

### Unauthorized Platform Operation (Escrow Release, Local Verification)

```
Agent                           Central Bank
  |                                  |
  |  POST /escrow/{id}/release       |
  |  { "token": "eyJ..." }         |
  |  (signed with a regular agent's  |
  |   key, not the platform's)       |
  |  -------------------------------->
  |                                  |  Payload valid → local signature
  |                                  |  verification against the
  |                                  |  registered PlatformAgent's
  |                                  |  public key FAILS
  |                                  |  (InvalidSignature)
  |                                  |
  |  403 { error: forbidden }       |
  |  <--------------------------------
```

---

## Token Replay Considerations

The `action` field in every JWS payload prevents cross-operation replay. A token signed for `"escrow_lock"` cannot be used for `"get_balance"`.

However, same-operation replay is possible. This is mitigated by:

- **Escrow lock uniqueness:** `(payer_account_id, task_id)` is unique for locked escrows. A replayed escrow lock token with the same payer and task returns the existing escrow (idempotent) or fails if the amount differs (`409 escrow_already_locked`).
- **Credit idempotency:** `(account_id, reference)` is unique for credit transactions. A replayed credit token with the same account and reference returns the original result (idempotent) or fails if the amount differs (`400 payload_mismatch`).
- **Escrow release/split status checks:** Escrow must be in `locked` status. Once released or split, a replayed token gets `409 escrow_already_resolved`.
- **Expiry (platform-signed tier only):** production platform tokens carry a 300-second `exp` (`agents/config.yaml` `signing.token_ttl_seconds`), enforced locally on `credit`/`escrow_release`/`escrow_split`. Agent-signed tokens and tokens from a signer without a configured TTL carry no `exp` and are accepted indefinitely.

Full replay protection (nonces) beyond the above is out of scope, consistent with the Identity service design.

---

## What This Specification Does NOT Cover

- **Endpoint behavior details** — request/response formats, data validation, business logic, and database operations are specified in the Central Bank Service API Specification.
- **Rate limiting** — no throttling on authenticated or unauthenticated endpoints.
- **Nonce-based replay protection** — only the idempotency keys and expiry described above.
- **Platform private key custody** — the Central Bank loads and holds the platform's private key itself (via `agent_config_path`) in order to *verify* incoming platform-signed tokens locally; it does not sign outgoing requests as the platform. The Task Board is the caller that signs escrow settlement requests as the platform.
