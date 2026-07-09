# Reputation Service — Authentication Specification

## Purpose

This document specifies how the Reputation service authenticates feedback submissions using certificate verification via a local PlatformAgent. Each service instantiates its own PlatformAgent loaded with its public and private key, and verifies incoming request certificates locally without calling any external service.

## Motivation

Without authentication, any caller can submit feedback impersonating any agent. This makes reputation data worthless — a malicious caller can inflate their own reputation or sabotage competitors. Authentication ensures that only the actual agent can submit feedback in their own name.

---

## Authentication Model

### Two Tiers of Operations

**Agent operations** — require a certificate (request payload signed by the submitting agent's private key):
- `POST /feedback` — submit feedback (certificate must be signed by the agent identified by `from_agent_id` in payload)

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
Agent                      Reputation Service
  |                               |
  |  1. Construct request payload |
  |     { action, task_id,        |
  |       from_agent_id,          |
  |       to_agent_id, ... }      |
  |                               |
  |  2. Sign payload with         |
  |     Ed25519 private key       |
  |     → produces certificate    |
  |                               |
  |  3. POST /feedback            |
  |     { payload + certificate } |
  |  ---------------------------> |
  |                               |  4. PlatformAgent.validate_certificate()
  |                               |     - Decrypt certificate with agent's public key
  |                               |     - Compare decrypted result to request payload
  |                               |  5. Check: signer == from_agent_id
  |                               |  6. Validate feedback fields
  |                               |  7. Store feedback record
  |                               |
  |  8. 201 { feedback_id, ... }  |
  |  <--------------------------- |
```

The Reputation service performs certificate verification locally using its PlatformAgent instance. No external service call is needed for authentication.

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

### After (authenticated — payload + certificate)

The request body contains the payload fields plus a `certificate` field. The certificate is the payload signed by the agent's Ed25519 private key.

```json
{
  "action": "submit_feedback",
  "task_id": "t-550e8400-e29b-41d4-a716-446655440000",
  "from_agent_id": "a-alice-uuid",
  "to_agent_id": "a-bob-uuid",
  "category": "delivery_quality",
  "rating": "satisfied",
  "comment": "Good work",
  "certificate": "<base64-encoded-signature>"
}
```

### Key Design Decisions

- **`from_agent_id` remains in the payload** — the service uses `from_agent_id` to look up the agent's public key for certificate verification. The certificate proves the request was signed by the holder of the corresponding private key.
- **`action` field is required** — set to `"submit_feedback"`. This prevents certificate reuse across different operations (e.g., a certificate signed for an escrow lock cannot be replayed to submit feedback).
- **`certificate` field is required** — contains the Ed25519 signature of the request payload. The service verifies it locally using `PlatformAgent.validate_certificate()`.

---

## Authorization Rules

After the PlatformAgent verifies the certificate is valid:

1. **Certificate must be valid for `from_agent_id`** — if the certificate cannot be verified against the public key associated with `from_agent_id`, return `403 FORBIDDEN`. An agent can only submit feedback in their own name.

2. **No platform operations** — unlike the Central Bank and Task Board, the Reputation service has no platform-only operations. No action requires platform privilege.

3. **No ownership check on reads** — GET endpoints are public. Any caller can query visible feedback.

---

## Agent Existence Verification

When processing `POST /feedback`, the service verifies `from_agent_id` implicitly — a valid certificate proves the sender holds the private key corresponding to the public key associated with `from_agent_id`. Agent existence is proven cryptographically, not by an external lookup.

The service does **not** verify that `to_agent_id` exists. This follows the current design: the reputation service accepts any non-empty string as an agent ID. Feedback about a non-existent agent is inert (no one queries it).

**Rationale:** The calling service (Task Board) already verified both agents exist when managing the task lifecycle. The Reputation service trusts upstream validation, same as it trusts upstream `task_id` validity.

---

## New Error Codes

These errors are added to `POST /feedback`:

| Status | Code                          | When                                                         |
|--------|-------------------------------|--------------------------------------------------------------|
| 400    | `INVALID_CERTIFICATE`        | Certificate is malformed, missing, empty, or not a string    |
| 400    | `INVALID_PAYLOAD`            | Payload is missing `action`, `action` is not `"submit_feedback"`, or `from_agent_id` is missing from the payload (required for certificate verification) |
| 403    | `FORBIDDEN`                  | Certificate verification failed (decrypted certificate does not match request payload), meaning the request was not signed by the claimed agent |

### Error Precedence

Errors are checked in this order (first match wins):

1. `415 UNSUPPORTED_MEDIA_TYPE` — wrong Content-Type
2. `413 PAYLOAD_TOO_LARGE` — body exceeds max size
3. `400 INVALID_JSON` — malformed JSON
4. `400 INVALID_CERTIFICATE` — missing or malformed `certificate` field
5. `400 INVALID_PAYLOAD` — payload missing `action` or `action` is not `"submit_feedback"`, or `from_agent_id` is missing from the payload (required for certificate verification)
6. `403 FORBIDDEN` — certificate verification failed (decrypted certificate does not match request payload)
7. All existing validation errors (`MISSING_FIELD`, `INVALID_RATING`, `SELF_FEEDBACK`, etc.)

### Notes on Error Mapping

- **Invalid certificate** returns `403 FORBIDDEN`. The `PlatformAgent.validate_certificate()` call determined the decrypted certificate does not match the request payload — this is an authentication failure. The request was not signed by the private key corresponding to the claimed agent's public key.
- **Certificate verification is local** — unlike the previous model that delegated to the Identity service, all verification happens in-process via the PlatformAgent. There are no network failures to handle for the authentication step itself. The service does not fall back to unauthenticated mode.

---

## Configuration Changes

### New `platform` Section

Add to `config.yaml`:

```yaml
platform:
  public_key_path: "data/platform_public.pem"
  private_key_path: "data/platform_private.pem"
```

All fields are required — the service must fail to start if any is missing. The key paths point to the Ed25519 key files used to instantiate the PlatformAgent at startup.

### Updated Full Configuration

```yaml
service:
  name: "reputation"
  version: "0.1.0"

server:
  host: "127.0.0.1"
  port: 8004
  log_level: "info"

logging:
  level: "INFO"
  format: "json"

platform:
  public_key_path: "data/platform_public.pem"
  private_key_path: "data/platform_private.pem"

request:
  max_body_size: 1048576

feedback:
  reveal_timeout_seconds: 86400
  max_comment_length: 256
```

**Note:** `max_body_size` moves from the `feedback` section to a new `request` section, matching the Central Bank and Task Board configuration pattern. This ensures the `RequestValidationMiddleware` can access it consistently via `settings.request.max_body_size`.

**Breaking change:** This is a breaking configuration change. Existing `config.yaml` files must be updated — remove `max_body_size` from `feedback` and add it under the new `request` section. The service will fail to start if `max_body_size` appears in the wrong section (Pydantic models use `extra="forbid"`).

### No `get_agent_path`

Unlike the Central Bank, the Reputation service does not need `get_agent_path` because it does not verify `to_agent_id` existence (see "Agent Existence Verification" above).

---

## Infrastructure Changes

### PlatformAgent

The service instantiates a `PlatformAgent` during startup (in `lifespan.py`) using the `platform` config section. The PlatformAgent is loaded with the platform's public and private key from the configured key paths. It is stored in `AppState` and available for the lifetime of the service.

The `PlatformAgent` provides (via `BaseAgent`):
- `validate_certificate(request_payload, certificate) -> bool` — decrypts the certificate using the agent's public key and compares the result to the request payload. Returns `True` if they match (request is authentic), `False` otherwise. This is a local, synchronous operation with no network calls.

No shutdown cleanup is needed — the PlatformAgent holds no network connections or file handles.

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
- **Certificate expiry / replay protection** — certificates have no expiry. Replay protection is out of scope ("replay protection is the caller's responsibility").
- **Platform operations** — no platform-only endpoints exist on the Reputation service.

---

## Interaction Patterns

### Authenticated Feedback Submission

```
Worker                          Reputation Service
  |                                    |
  |  1. Construct request payload:     |
  |     { action: submit_feedback,     |
  |       task_id, from_agent_id,      |
  |       to_agent_id, category,       |
  |       rating, comment }            |
  |                                    |
  |  2. Sign payload with Ed25519      |
  |     private key → certificate      |
  |                                    |
  |  3. POST /feedback                 |
  |     { payload + certificate }      |
  |  --------------------------------->|
  |                                    |  4. PlatformAgent.validate_certificate()
  |                                    |     - Decrypt certificate with agent's public key
  |                                    |     - Compare to request payload
  |                                    |  5. Validate feedback fields
  |                                    |  6. Check uniqueness
  |                                    |  7. Store (sealed)
  |                                    |  8. Check mutual reveal
  |                                    |
  |  9. 201 { feedback_id,            |
  |           visible: false }         |
  |  <---------------------------------|
```

### Impersonation Attempt

```
Mallory                         Reputation Service
  |                                    |
  |  Signs payload with mallory's      |
  |  private key but sets              |
  |  from_agent_id: alice in payload   |
  |                                    |
  |  POST /feedback                    |
  |  { payload + certificate }         |
  |  --------------------------------->|
  |                                    |  PlatformAgent.validate_certificate()
  |                                    |  using alice's public key
  |                                    |  → decrypted cert != payload
  |                                    |  (mallory's key != alice's key)
  |                                    |
  |  403 { error: FORBIDDEN }         |
  |  <---------------------------------|
```
