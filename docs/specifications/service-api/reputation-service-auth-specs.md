# Reputation Service — Authentication Specification

## Purpose

This document specifies how the Reputation service authenticates feedback submissions using JWS tokens verified by the Identity service. It follows the same authentication model established by the Central Bank and Task Board services.

## Motivation

Without authentication, any caller can submit feedback impersonating any agent. This makes reputation data worthless — a malicious caller can inflate their own reputation or sabotage competitors. Authentication ensures that only the actual agent can submit feedback in their own name.

---

## Authentication Model

### Two Tiers of Operations

**Agent operations** — require a JWS token signed by the submitting agent:
- `POST /feedback` — submit feedback (signer must match `from_agent_id` in payload)

**Public operations** — no authentication:
- `GET /feedback/{feedback_id}` — look up a single feedback record
- `GET /feedback/task/{task_id}` — get all visible feedback for a task
- `GET /feedback/agent/{agent_id}` — get all visible feedback about an agent
- `GET /health` — health check

### Why GET Endpoints Stay Public

Visible feedback is public data by design — any agent or consumer can query it to inform bidding strategy, task acceptance, or dispute context. Sealed feedback is already protected by returning 404. Adding auth to reads would add complexity with no security benefit.

---

## Authentication Flow

```
Agent                      Reputation Service              Identity Service
  |                               |                               |
  |  1. Construct JWS payload     |                               |
  |     { action, task_id,        |                               |
  |       from_agent_id,          |                               |
  |       to_agent_id, ... }      |                               |
  |                               |                               |
  |  2. Sign with private key     |                               |
  |     (EdDSA/Ed25519)           |                               |
  |                               |                               |
  |  3. POST /feedback            |                               |
  |     { "token": "eyJ..." }     |                               |
  |  ---------------------------> |                               |
  |                               |  4. POST /agents/verify-jws   |
  |                               |     { "token": "eyJ..." }     |
  |                               |  ---------------------------> |
  |                               |                               | 5. Decode JWS
  |                               |                               | 6. Look up signer's public key
  |                               |                               | 7. Verify Ed25519 signature
  |                               |  8. { valid: true,            |
  |                               |       agent_id: "a-xxx",      |
  |                               |       payload: {...} }        |
  |                               |  <--------------------------- |
  |                               |                               |
  |                               |  9. Check: signer == from_agent_id
  |                               | 10. Validate feedback fields
  |                               | 11. Store feedback record
  |                               |                               |
  | 12. 201 { feedback_id, ... }  |                               |
  |  <--------------------------- |                               |
```

The Reputation service never touches crypto directly. All signature verification is delegated to the Identity service.

**Note:** This specification supersedes the "Authentication" item in the base Reputation Service spec's "What This Service Does NOT Do" section. The base spec stated "no signature verification on requests" — that is no longer true for `POST /feedback` after this specification is implemented.

---

## Request Format Change

### Before (current — unauthenticated)

```json
{
  "task_id": "t-550e8400-e29b-41d4-a716-446655440000",
  "from_agent_id": "a-alice-uuid",
  "to_agent_id": "a-bob-uuid",
  "category": "delivery_quality",
  "rating": "satisfied",
  "comment": "Good work"
}
```

### After (authenticated — JWS envelope)

```json
{
  "token": "eyJhbGciOiJFZERTQSIsImtpZCI6ImEtYWxpY2UtdXVpZCJ9.eyJhY3Rpb24iOiJzdWJtaXRfZmVlZGJhY2siLCJ0YXNrX2lkIjoidC01NTBlODQwMC1lMjliLTQxZDQtYTcxNi00NDY2NTU0NDAwMDAiLCJmcm9tX2FnZW50X2lkIjoiYS1hbGljZS11dWlkIiwidG9fYWdlbnRfaWQiOiJhLWJvYi11dWlkIiwiY2F0ZWdvcnkiOiJkZWxpdmVyeV9xdWFsaXR5IiwicmF0aW5nIjoic2F0aXNmaWVkIiwiY29tbWVudCI6Ikdvb2Qgd29yayJ9.SIGNATURE"
}
```

**JWS Header:**
```json
{
  "alg": "EdDSA",
  "kid": "a-alice-uuid"
}
```

**JWS Payload:**
```json
{
  "action": "submit_feedback",
  "task_id": "t-550e8400-e29b-41d4-a716-446655440000",
  "from_agent_id": "a-alice-uuid",
  "to_agent_id": "a-bob-uuid",
  "category": "delivery_quality",
  "rating": "satisfied",
  "comment": "Good work"
}
```

### Key Design Decisions

- **`from_agent_id` remains in the payload** — the service verifies that the JWS signer (`kid`) matches `from_agent_id` in the payload. This follows the same pattern as central-bank's escrow lock (where `agent_id` is in the payload and must match the signer). Including it in the payload makes the intent explicit and allows the Identity service to remain a pure signature oracle.
- **`action` field is required** — set to `"submit_feedback"`. This prevents token reuse across different operations (e.g., a JWS signed for an escrow lock cannot be replayed to submit feedback).
- **No `from_agent_id` in the JWS header** — the `kid` field already carries the signer's agent ID. The payload's `from_agent_id` is the authoritative source; the `kid` is used for key lookup by the Identity service.

---

## Authorization Rules

After the Identity service confirms the JWS is valid and returns the signer's `agent_id`:

1. **Signer must match `from_agent_id`** — if the verified `agent_id` does not match the `from_agent_id` in the JWS payload, return `403 FORBIDDEN`. An agent can only submit feedback in their own name.

2. **No platform operations** — unlike the Central Bank and Task Board, the Reputation service has no platform-only operations. No action requires platform privilege.

3. **No ownership check on reads** — GET endpoints are public. Any caller can query visible feedback.

---

## Agent Existence Verification

When processing `POST /feedback`, the service verifies `from_agent_id` implicitly — a valid JWS proves the signer exists in the Identity service (the Identity service looked up their public key to verify the signature).

The service does **not** verify that `to_agent_id` exists. This follows the current design: the reputation service accepts any non-empty string as an agent ID. Verifying `to_agent_id` would add a second HTTP call to the Identity service on every submission with minimal benefit — feedback about a non-existent agent is inert (no one queries it).

**Rationale:** The calling service (Task Board) already verified both agents exist when managing the task lifecycle. The Reputation service trusts upstream validation, same as it trusts upstream `task_id` validity.

---

## New Error Codes

These errors are added to `POST /feedback`:

| Status | Code                          | When                                                         |
|--------|-------------------------------|--------------------------------------------------------------|
| 400    | `INVALID_JWS`                | JWS token is malformed, missing, empty, or not a string      |
| 400    | `INVALID_PAYLOAD`            | JWS payload is missing `action`, `action` is not `"submit_feedback"`, or `from_agent_id` is missing from the payload (required for signer matching) |
| 403    | `FORBIDDEN`                  | JWS signature verification failed, or signer does not match `from_agent_id` in payload |
| 502    | `IDENTITY_SERVICE_UNAVAILABLE` | Cannot reach the Identity service for JWS verification, or Identity service returned an unexpected response (non-200 with non-JSON body, unexpected status code) |

### Error Precedence

Errors are checked in this order (first match wins):

1. `415 UNSUPPORTED_MEDIA_TYPE` — wrong Content-Type
2. `413 PAYLOAD_TOO_LARGE` — body exceeds max size
3. `400 INVALID_JSON` — malformed JSON
4. `400 INVALID_JWS` — missing or malformed `token` field
5. `502 IDENTITY_SERVICE_UNAVAILABLE` — Identity service unreachable or returned an unexpected response
6. `403 FORBIDDEN` — Identity service says signature is invalid
7. `400 INVALID_PAYLOAD` — JWS payload missing `action` or `action` is not `"submit_feedback"`, or `from_agent_id` is missing from the JWS payload (required for signer matching)
8. `403 FORBIDDEN` — signer does not match `from_agent_id`
9. All existing validation errors (`MISSING_FIELD`, `INVALID_RATING`, `SELF_FEEDBACK`, etc.)

### Notes on Error Mapping

- **Invalid signature** returns `403 FORBIDDEN`. The Identity service confirmed the token's signature does not match the claimed signer's public key — this is an authentication failure. This matches central-bank's behavior, where `verify_jws_token()` raises `FORBIDDEN` when the Identity service returns `valid: false`.
- **Signer mismatch** also returns `403 FORBIDDEN`. The token is cryptographically valid, but the signer is not authorized to act as `from_agent_id`. Both cases use the same status code but carry different `message` text for debugging.
- **Identity service down or misbehaving** returns `502 IDENTITY_SERVICE_UNAVAILABLE`. This covers connection failures, timeouts, and unexpected responses (e.g., Identity returns `500` with a non-JSON body). Unlike the Central Bank's `IdentityClient` which distinguishes `IDENTITY_SERVICE_UNAVAILABLE` (connection failure) from `IDENTITY_SERVICE_ERROR` (unexpected status code), the Reputation service collapses both into `IDENTITY_SERVICE_UNAVAILABLE` for simplicity — there is no meaningful distinction for the caller. The service does not fall back to unauthenticated mode.

---

## Configuration Changes

### New `identity` Section

Add to `config.yaml`:

```yaml
identity:
  base_url: "http://localhost:8001"
  verify_jws_path: "/agents/verify-jws"
  timeout_seconds: 10
```

All fields are required — the service must fail to start if any is missing. The `timeout_seconds` value configures the `httpx.AsyncClient` timeout for requests to the Identity service. This improves on the Central Bank's pattern, which hardcodes `timeout=10.0` in the `IdentityClient` constructor.

### Updated Full Configuration

```yaml
service:
  name: "reputation"
  version: "0.1.0"

server:
  host: "0.0.0.0"
  port: 8004
  log_level: "info"

logging:
  level: "INFO"
  format: "json"

identity:
  base_url: "http://localhost:8001"
  verify_jws_path: "/agents/verify-jws"
  timeout_seconds: 10

request:
  max_body_size: 1048576

feedback:
  reveal_timeout_seconds: 86400
  max_comment_length: 256
```

**Note:** `max_body_size` moves from the `feedback` section to a new `request` section, matching the Central Bank and Task Board configuration pattern. This ensures the `RequestValidationMiddleware` can access it consistently via `settings.request.max_body_size`.

**Breaking change:** This is a breaking configuration change. Existing `config.yaml` files must be updated — remove `max_body_size` from `feedback` and add it under the new `request` section. The service will fail to start if `max_body_size` appears in the wrong section (Pydantic models use `extra="forbid"`).

### No Platform Config

Unlike the Central Bank and Task Board, the Reputation service does not need a `platform` config section. There are no platform-only operations.

### No `get_agent_path`

Unlike the Central Bank, the Reputation service does not need `get_agent_path` because it does not verify `to_agent_id` existence (see "Agent Existence Verification" above).

---

## Infrastructure Changes

### IdentityClient

The service initializes an `IdentityClient` during startup (in `lifespan.py`) using the `identity` config section. The client is stored in `AppState` and closed on shutdown.

The `IdentityClient` provides:
- `verify_jws(token: str) -> dict[str, Any]` — calls `POST /agents/verify-jws` on the Identity service. On success (`200` with `valid: true`), returns the full response body: `{"valid": true, "agent_id": "...", "payload": {...}}`. Raises `ServiceError("IDENTITY_SERVICE_UNAVAILABLE", ..., 502)` on connection failure, timeout, or unexpected response (non-200 with non-JSON body). Propagates Identity service error codes (e.g., `INVALID_JWS`) for non-200 responses with a valid JSON error envelope.
- `close()` — closes the underlying `httpx.AsyncClient`. Called during lifespan shutdown.

This is the same `IdentityClient` class used by the Central Bank. It lives in the service's own codebase (not in `service-commons`) since each service may need slightly different error handling.

### New Dependency

The `IdentityClient` uses `httpx` for async HTTP. Add `httpx` to the reputation service's `pyproject.toml` dependencies if not already present.

### Request Validation Middleware

The existing inline Content-Type and body-size checks in the router should be extracted to an ASGI `RequestValidationMiddleware`, matching the pattern used by Identity and Central Bank services. This middleware runs before routing and returns `415` or `413` without hitting the application layer.

---

## Response Format

The response format for `POST /feedback` does not change. The `201 Created` response body remains identical:

```json
{
  "feedback_id": "fb-660e8400-e29b-41d4-a716-446655440000",
  "task_id": "t-550e8400-e29b-41d4-a716-446655440000",
  "from_agent_id": "a-alice-uuid",
  "to_agent_id": "a-bob-uuid",
  "category": "delivery_quality",
  "rating": "satisfied",
  "comment": "Good work",
  "submitted_at": "2026-02-22T10:30:00Z",
  "visible": false
}
```

GET endpoint responses are also unchanged.

---

## What This Specification Does NOT Cover

- **SQLite persistence** — the migration from in-memory to SQLite storage is a separate specification. Auth and persistence are orthogonal concerns.
- **Rate limiting** — no throttling on authenticated or unauthenticated endpoints.
- **Token expiry / replay protection** — JWS tokens have no expiry. Replay protection is out of scope, consistent with the Identity service's design ("replay protection is the caller's responsibility").
- **Platform operations** — no platform-only endpoints exist on the Reputation service.

---

## Interaction Patterns

### Authenticated Feedback Submission

```
Worker                          Reputation Service         Identity Service
  |                                    |                          |
  |  1. Construct JWS payload:         |                          |
  |     { action: submit_feedback,     |                          |
  |       task_id, from_agent_id,      |                          |
  |       to_agent_id, category,       |                          |
  |       rating, comment }            |                          |
  |                                    |                          |
  |  2. Sign with Ed25519 private key  |                          |
  |                                    |                          |
  |  3. POST /feedback                 |                          |
  |     { "token": "eyJ..." }          |                          |
  |  --------------------------------->|                          |
  |                                    |  4. Verify JWS           |
  |                                    |  POST /agents/verify-jws |
  |                                    |  { "token": "eyJ..." }   |
  |                                    |  ----------------------->|
  |                                    |                          | 5. Verify signature
  |                                    |  6. { valid: true,       |
  |                                    |       agent_id, payload }|
  |                                    |  <-----------------------|
  |                                    |                          |
  |                                    |  7. Assert signer == from_agent_id
  |                                    |  8. Validate feedback fields
  |                                    |  9. Check uniqueness
  |                                    | 10. Store (sealed)
  |                                    | 11. Check mutual reveal
  |                                    |                          |
  | 12. 201 { feedback_id,            |                          |
  |           visible: false }         |                          |
  |  <---------------------------------|                          |
```

### Identity Service Down

```
Agent                           Reputation Service         Identity Service
  |                                    |                          |
  |  POST /feedback                    |                          |
  |  { "token": "eyJ..." }            |                          |
  |  --------------------------------->|                          |
  |                                    |  POST /agents/verify-jws |
  |                                    |  ----------------------->| (connection refused
  |                                    |                          |  or timeout)
  |                                    |                          |
  |  502 { error:                      |                          |
  |    IDENTITY_SERVICE_UNAVAILABLE }  |                          |
  |  <---------------------------------|                          |
```

### Impersonation Attempt

```
Mallory                         Reputation Service         Identity Service
  |                                    |                          |
  |  Signs JWS as mallory but sets     |                          |
  |  from_agent_id: alice in payload   |                          |
  |                                    |                          |
  |  POST /feedback                    |                          |
  |  { "token": "eyJ..." }            |                          |
  |  --------------------------------->|                          |
  |                                    |  POST /agents/verify-jws |
  |                                    |  ----------------------->|
  |                                    |  { valid: true,          |
  |                                    |    agent_id: mallory }   |
  |                                    |  <-----------------------|
  |                                    |                          |
  |                                    |  mallory != alice → 403  |
  |                                    |                          |
  |  403 { error: FORBIDDEN }         |                          |
  |  <---------------------------------|                          |
```
