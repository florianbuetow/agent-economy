# Court Service — Authentication Specification

## Purpose

This document specifies how the Court service authenticates operations using JWS tokens verified by the Identity service. The Court has the simplest authentication model in the system: all write operations are platform-signed, and all read operations are public.

## Motivation

The Court is an internal service. Agents never interact with it directly — the Task Board orchestrates all dispute operations on behalf of agents. The Task Board files claims on behalf of posters, submits rebuttals on behalf of workers, and triggers rulings after rebuttal windows expire.

Platform-only authentication ensures no agent can directly file a dispute, submit a rebuttal, or trigger a ruling. This prevents agents from manipulating the dispute process — for example, filing fraudulent claims, submitting rebuttals for disputes they are not party to, or triggering premature rulings. All agent authentication happens at the Task Board layer, and the Court trusts that the Task Board has already verified the agent's identity and authorization before forwarding the request with a platform-signed token.

---

## Authentication Model

### Two Tiers of Operations

**Platform-signed operations** — require a JWS token signed by the platform agent (`settings.platform.agent_id`):

| Endpoint | Description |
|----------|-------------|
| `POST /disputes/file` | File a new dispute (Task Board acts on behalf of poster) |
| `POST /disputes/{dispute_id}/rebuttal` | Submit worker's rebuttal (Task Board acts on behalf of worker) |
| `POST /disputes/{dispute_id}/rule` | Trigger the judge panel to evaluate and rule (Task Board triggers after rebuttal or window expiry) |

**Public operations** — no authentication:

| Endpoint | Description |
|----------|-------------|
| `GET /disputes/{dispute_id}` | Get full dispute details including votes and ruling |
| `GET /disputes` | List disputes with optional filters |
| `GET /health` | Health check |

### Why All Writes Are Platform-Only

The Court is an internal service. The Task Board handles agent authentication and forwards authorized requests signed with the platform key. This architecture:

- **Prevents direct agent manipulation** — no agent can file a claim, submit a rebuttal, or trigger a ruling without going through the Task Board's authorization layer.
- **Simplifies Court authentication** — the Court only needs to verify one identity (the platform agent), not the full agent roster.
- **Centralizes dispute orchestration** — the Task Board enforces business rules (task must be in DISPUTED status, rebuttal window timing, etc.) before calling the Court.

### Why All Reads Are Public

Dispute data, votes, and rulings are public by design. Transparency supports market trust and informed decision-making. Any agent can query dispute details to:

- Evaluate the dispute history of a poster or worker before accepting tasks or bids
- Review past rulings to understand how the judge panel evaluates specification quality
- Verify that rulings were applied correctly (escrow splits, reputation feedback)

Adding authentication to reads would add complexity with no security benefit — there is no sensitive data to protect.

---

## JWS Token Format

### JWS Header

```json
{
  "alg": "EdDSA",
  "kid": "<platform_agent_id>"
}
```

- `alg` must be `"EdDSA"` (Ed25519)
- `kid` is the platform agent ID (e.g., `"a-platform-uuid"`). For the Court, this is always the platform agent — no agent-signed tokens are accepted.

### JWS Payload

Every JWS payload must include an `action` field that identifies the operation. This prevents cross-operation token replay — a token signed for `"file_dispute"` cannot be used for `"submit_rebuttal"`.

The payload also carries all operation-specific fields (task IDs, claim text, etc.). The Court extracts these fields from the verified JWS payload rather than from separate request body fields.

### Action Values

| Action | Endpoint | Signer |
|--------|----------|--------|
| `file_dispute` | `POST /disputes/file` | Platform |
| `submit_rebuttal` | `POST /disputes/{dispute_id}/rebuttal` | Platform |
| `trigger_ruling` | `POST /disputes/{dispute_id}/rule` | Platform |

---

## Token Delivery

All authenticated endpoints use **body token** delivery:

```json
{
  "token": "<JWS compact token>"
}
```

There are no Bearer header endpoints. GET endpoints are public and require no token. POST endpoints carry the token in the JSON request body.

This is the simplest token delivery model of all services — no mixed body/header delivery, no multi-token endpoints.

---

## Authentication Flow

### Standard Flow (All POST Endpoints)

```
Task Board                     Court Service                    Identity Service
  |                                  |                                  |
  |  1. Construct JWS payload:       |                                  |
  |     { action: "file_dispute",    |                                  |
  |       task_id, claimant_id,      |                                  |
  |       respondent_id, claim,      |                                  |
  |       escrow_id }                |                                  |
  |                                  |                                  |
  |  2. Sign with platform           |                                  |
  |     Ed25519 private key          |                                  |
  |     Header: { alg: "EdDSA",      |                                  |
  |       kid: "<platform_agent_id>" }                                  |
  |                                  |                                  |
  |  3. POST /disputes/file          |                                  |
  |     { "token": "eyJ..." }        |                                  |
  |  ===============================>|                                  |
  |                                  |  4. POST /agents/verify-jws      |
  |                                  |     { "token": "eyJ..." }        |
  |                                  |  ================================>|
  |                                  |                                  |
  |                                  |                 5. Decode JWS    |
  |                                  |                 6. Look up kid's |
  |                                  |                    public key    |
  |                                  |                 7. Verify Ed25519|
  |                                  |                    signature     |
  |                                  |                                  |
  |                                  |  8. { valid: true,               |
  |                                  |       agent_id: "<platform_id>", |
  |                                  |       payload: {...} }           |
  |                                  |  <================================|
  |                                  |                                  |
  |                                  |  9. Check: agent_id ==           |
  |                                  |     settings.platform.agent_id   |
  |                                  | 10. Validate action field        |
  |                                  | 11. Validate payload fields      |
  |                                  | 12. Execute operation            |
  |                                  |                                  |
  | 13. Response                     |                                  |
  |  <===============================|                                  |
```

The Court never touches crypto directly. All signature verification is delegated to the Identity service.

---

## Authorization Rules

After the Identity service confirms the JWS is valid and returns the signer's `agent_id`:

1. **Signer must be the platform agent** — the verified `agent_id` must match `settings.platform.agent_id`. If it does not, return `403 FORBIDDEN`. No agent-signed tokens are accepted by the Court.

2. **No agent-level authorization checks** — unlike the Task Board (which checks poster/worker roles) and the Reputation service (which checks `from_agent_id` matching), the Court only verifies platform identity. The Task Board is responsible for ensuring the correct agent authorized the operation before calling the Court.

3. **No ownership checks on reads** — GET endpoints are public. Any caller can query dispute data.

---

## Request Format

### All POST Endpoints

All three POST endpoints accept the same envelope:

```json
{
  "token": "<JWS compact token>"
}
```

The JWS payload contains all operation-specific fields. There are no fields outside the token.

### JWS Payload: File Dispute

```json
{
  "action": "file_dispute",
  "task_id": "t-550e8400-e29b-41d4-a716-446655440000",
  "claimant_id": "a-alice-uuid",
  "respondent_id": "a-bob-uuid",
  "claim": "The worker did not implement email validation as specified.",
  "escrow_id": "esc-770e8400-e29b-41d4-a716-446655440000"
}
```

### JWS Payload: Submit Rebuttal

```json
{
  "action": "submit_rebuttal",
  "dispute_id": "disp-990e8400-e29b-41d4-a716-446655440000",
  "rebuttal": "The specification did not define a specific email format."
}
```

The `dispute_id` in the payload must match the `{dispute_id}` URL path parameter.

### JWS Payload: Trigger Ruling

```json
{
  "action": "trigger_ruling",
  "dispute_id": "disp-990e8400-e29b-41d4-a716-446655440000"
}
```

The `dispute_id` in the payload must match the `{dispute_id}` URL path parameter.

---

## Error Codes

### Authentication Errors

| Status | Code                          | When |
|--------|-------------------------------|------|
| 400    | `INVALID_JWS`                | JWS token is malformed, missing, empty, or not a string |
| 400    | `INVALID_PAYLOAD`            | JWS payload is missing `action`, `action` does not match the expected value for this endpoint, or required payload fields are missing |
| 403    | `FORBIDDEN`                  | JWS signature verification failed (Identity says `valid: false`), or signer is not the platform agent |
| 502    | `IDENTITY_SERVICE_UNAVAILABLE` | Cannot reach the Identity service for JWS verification, or Identity service returned an unexpected response (non-200 with non-JSON body, unexpected status code) |

### Error Precedence

Errors are checked in this order (first match wins):

1. `415 UNSUPPORTED_MEDIA_TYPE` — wrong Content-Type (expected `application/json`)
2. `413 PAYLOAD_TOO_LARGE` — body exceeds `request.max_body_size`
3. `400 INVALID_JSON` — malformed JSON body
4. `400 INVALID_JWS` — missing or malformed `token` field
5. `502 IDENTITY_SERVICE_UNAVAILABLE` — Identity service unreachable or returned an unexpected response
6. `403 FORBIDDEN` — Identity service says signature is invalid (`valid: false`)
7. `400 INVALID_PAYLOAD` — wrong `action`, missing required payload fields, or `dispute_id` in payload does not match URL path parameter
8. `403 FORBIDDEN` — signer is not the platform agent (`agent_id != settings.platform.agent_id`)
9. Domain-specific errors (`DISPUTE_NOT_FOUND`, `DISPUTE_ALREADY_EXISTS`, `INVALID_DISPUTE_STATUS`, `REBUTTAL_ALREADY_SUBMITTED`, `DISPUTE_ALREADY_RULED`, `TASK_NOT_FOUND`, etc.)
10. `502` errors from downstream services (`TASK_BOARD_UNAVAILABLE`, `CENTRAL_BANK_UNAVAILABLE`, `REPUTATION_SERVICE_UNAVAILABLE`, `JUDGE_UNAVAILABLE`)

### Notes on Error Mapping

- **Invalid signature** returns `403 FORBIDDEN`. The Identity service confirmed the token's signature does not match the claimed signer's public key — this is an authentication failure. This matches the Central Bank and Task Board behavior.
- **Non-platform signer** also returns `403 FORBIDDEN` with a different message. The token is cryptographically valid, but the signer is not the platform agent. Both cases use the same status code but carry different `message` text for debugging.
- **Identity service down or misbehaving** returns `502 IDENTITY_SERVICE_UNAVAILABLE`. This covers connection failures, timeouts, and unexpected responses (e.g., Identity returns `500` with a non-JSON body). The Court collapses all Identity service failures into this single error code for simplicity. The service does not fall back to unauthenticated mode.

---

## Configuration

### Identity Integration

```yaml
identity:
  base_url: "http://localhost:8001"
  verify_jws_path: "/agents/verify-jws"
```

### Platform Agent

```yaml
platform:
  agent_id: ""
  private_key_path: ""
```

- `platform.agent_id`: The agent ID of the platform, registered with the Identity service. Used to verify that the signer of incoming JWS tokens is the platform agent. Also used as the `kid` for outgoing platform-signed tokens (escrow split, feedback submission, ruling recording).
- `platform.private_key_path`: Absolute path to the Ed25519 private key file (PEM format). Used to sign outgoing requests to the Central Bank, Reputation service, and Task Board. The corresponding public key must be registered with the Identity service under `platform.agent_id`.

All fields are required. Missing fields cause startup failure. No default values.

---

## Infrastructure

### IdentityClient

Initialized during startup (in `lifespan.py`), stored in `AppState`, closed on shutdown.

Provides:
- `verify_jws(token: str) -> dict[str, Any]` — calls `POST /agents/verify-jws` on the Identity service. On success (`200` with `valid: true`), returns the full response body: `{"valid": true, "agent_id": "...", "payload": {...}}`. Raises `ServiceError("IDENTITY_SERVICE_UNAVAILABLE", ..., 502)` on connection failure, timeout, or unexpected response. Propagates Identity service error codes (e.g., `INVALID_JWS`) for non-200 responses with a valid JSON error envelope.
- `close()` — closes the underlying `httpx.AsyncClient`. Called during lifespan shutdown.

### PlatformSigner

Initialized during startup by loading the Ed25519 private key from `platform.private_key_path`.

Provides:
- `sign(payload: dict) -> str` — creates a JWS compact token with `{"alg": "EdDSA", "kid": "<platform_agent_id>"}` header and the provided payload, signed with the platform's Ed25519 private key. Used for outgoing calls to the Central Bank (`POST /escrow/{id}/split`), Reputation service (`POST /feedback`), and Task Board (`POST /tasks/{id}/ruling`).

### Dependencies

Required in `pyproject.toml`:
- `httpx>=0.28.0` — async HTTP client for Identity service calls and outgoing calls to Central Bank, Reputation, and Task Board
- `cryptography>=44.0.0` — Ed25519 key loading
- `joserfc>=1.0.0` — JWS token creation for platform operations

---

## Interaction Patterns

### Platform Files Dispute

```
Task Board                     Court Service                    Identity Service
  |                                  |                                  |
  |  1. Sign JWS as platform:       |                                  |
  |     { action: file_dispute,      |                                  |
  |       task_id, claimant_id,      |                                  |
  |       respondent_id, claim,      |                                  |
  |       escrow_id }                |                                  |
  |                                  |                                  |
  |  2. POST /disputes/file          |                                  |
  |     { "token": "eyJ..." }        |                                  |
  |  ===============================>|                                  |
  |                                  |  3. Verify JWS                   |
  |                                  |  POST /agents/verify-jws         |
  |                                  |  { "token": "eyJ..." }           |
  |                                  |  ================================>|
  |                                  |                                  | 4. Verify signature
  |                                  |  5. { valid: true,               |
  |                                  |       agent_id: platform_id,     |
  |                                  |       payload: {...} }           |
  |                                  |  <================================|
  |                                  |                                  |
  |                                  |  6. Assert agent_id ==           |
  |                                  |     settings.platform.agent_id   |
  |                                  |  7. Validate action ==           |
  |                                  |     "file_dispute"               |
  |                                  |  8. Validate payload fields      |
  |                                  |  9. Create dispute record        |
  |                                  |                                  |
  | 10. 201 { dispute }              |                                  |
  |  <===============================|                                  |
```

### Identity Service Down

```
Task Board                     Court Service                    Identity Service
  |                                  |                                  |
  |  POST /disputes/file             |                                  |
  |  { "token": "eyJ..." }           |                                  |
  |  ===============================>|                                  |
  |                                  |  POST /agents/verify-jws         |
  |                                  |  ================================>| (connection refused
  |                                  |                                  |  or timeout)
  |                                  |                                  |
  |  502 { error:                    |                                  |
  |    IDENTITY_SERVICE_UNAVAILABLE }|                                  |
  |  <===============================|                                  |
```

### Non-Platform Agent Attempt

```
Rogue Agent                    Court Service                    Identity Service
  |                                  |                                  |
  |  Signs JWS with own key         |                                  |
  |  (not the platform key)         |                                  |
  |                                  |                                  |
  |  POST /disputes/file             |                                  |
  |  { "token": "eyJ..." }           |                                  |
  |  ===============================>|                                  |
  |                                  |  POST /agents/verify-jws         |
  |                                  |  ================================>|
  |                                  |  { valid: true,                  |
  |                                  |    agent_id: "a-rogue" }         |
  |                                  |  <================================|
  |                                  |                                  |
  |                                  |  "a-rogue" !=                    |
  |                                  |  settings.platform.agent_id      |
  |                                  |  -> 403                          |
  |                                  |                                  |
  |  403 { error: FORBIDDEN }        |                                  |
  |  <===============================|                                  |
```

---

## Token Replay Considerations

The `action` field in every JWS payload prevents cross-operation replay. A token signed for `"file_dispute"` cannot be used for `"submit_rebuttal"` or `"trigger_ruling"`.

Same-operation replay is mitigated by domain constraints:

- **File dispute replay:** `task_id` uniqueness constraint prevents filing a second dispute for the same task. A replayed `"file_dispute"` token gets `409 DISPUTE_ALREADY_EXISTS`.
- **Submit rebuttal replay:** The rebuttal can only be submitted once per dispute. A replayed `"submit_rebuttal"` token gets `409 REBUTTAL_ALREADY_SUBMITTED`.
- **Trigger ruling replay:** A dispute can only be ruled once. A replayed `"trigger_ruling"` token gets `409 DISPUTE_ALREADY_RULED`.

Full replay protection (timestamps, nonces) is out of scope, consistent with the Identity service design.

---

## What This Specification Does NOT Cover

- **Agent-level authentication** — the Court does not verify agent identity. The Task Board handles agent authentication before calling the Court.
- **Rate limiting** — no throttling on authenticated or unauthenticated endpoints.
- **Token expiry** — JWS tokens have no expiry. Replay protection is out of scope, consistent with the Identity service's design ("replay protection is the caller's responsibility").
- **Outgoing token signing details** — the Court signs outgoing requests to Central Bank, Reputation, and Task Board using the platform key. The specific payload formats for those outgoing tokens are defined in the respective service auth specs, not here.
- **SQLite persistence** — the database schema and migration strategy are separate from authentication concerns.
