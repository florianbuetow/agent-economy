# Task Board Service — Authentication Specification

## Purpose

This document specifies how the Task Board service authenticates operations using JWS certificates verified locally via the PlatformAgent, and how it signs platform operations for the Central Bank.

The Task Board has the most complex authentication model in the system: it both **verifies** incoming agent certificates locally (like the Central Bank and Reputation services) and **creates** outgoing platform tokens (to call the Central Bank for escrow operations).

---

## Authentication Model

### Three Tiers of Operations

**Agent-signed operations** — require a JWS certificate signed by the acting agent:

| Endpoint | Signer Must Be |
|----------|---------------|
| `POST /tasks` (task_token) | The poster (`poster_id`) |
| `POST /tasks/{id}/cancel` | The poster |
| `POST /tasks/{id}/bids` | The bidder (`bidder_id`) |
| `GET /tasks/{id}/bids` (during OPEN) | The poster |
| `POST /tasks/{id}/bids/{bid_id}/accept` | The poster |
| `POST /tasks/{id}/assets` | The assigned worker (`worker_id`) |
| `POST /tasks/{id}/submit` | The assigned worker |
| `POST /tasks/{id}/approve` | The poster |
| `POST /tasks/{id}/dispute` | The poster |

**Platform-signed operations** — require a JWS certificate signed by the platform agent:

| Endpoint | Purpose |
|----------|---------|
| `POST /tasks/{id}/ruling` | Court records a ruling |

**Public operations** — no authentication:

| Endpoint | Notes |
|----------|-------|
| `GET /health` | Always public |
| `GET /tasks` | Always public |
| `GET /tasks/{id}` | Always public |
| `GET /tasks/{id}/bids` | Public when task is NOT in OPEN status |
| `GET /tasks/{id}/assets` | Always public |
| `GET /tasks/{id}/assets/{asset_id}` | Always public |

### Why Most GETs Stay Public

Task data, bids (after acceptance), and assets are public by design. Any agent can browse available tasks and review past work. This transparency supports informed bidding and reputation evaluation.

The one exception is `GET /tasks/{id}/bids` during the OPEN phase, which requires poster authentication to enforce sealed bids.

---

## JWS Token Format

All JWS tokens follow the compact serialization format: `header.payload.signature`.

### JWS Header

```json
{
  "alg": "EdDSA",
  "typ": "JWT",
  "kid": "<agent_id>",
  "iat": 1792051200,
  "exp": 1792051500,
  "aud": "task-board",
  "resource": "POST /tasks/<concrete-task-id>/bids"
}
```

- The header has exactly `alg`, `typ`, `kid`, `iat`, `exp`, `aud`, and `resource`; missing or extra fields are `400 invalid_jws`.
- `alg="EdDSA"`, `typ="JWT"`; `kid`, `aud`, and `resource` are non-blank strings.
- `iat`/`exp` are JSON integers excluding booleans, `exp > iat`, and lifetime does not exceed the required positive configured maximum.
- `aud` equals Task Board's configured `service.name`. `resource` equals the concrete uppercase `METHOD /path` handled, with no host or query.
- Local platform routes additionally require authenticated `kid == platform_agent.agent_id`. Tokens without expiry claims are rejected.

### JWS Payload

Every JWS payload remains the existing domain object and includes string `action`. `aud` and `resource` stay in the protected header; similarly named domain fields grant no authority. Missing/wrong-type action or a context mismatch is `400 invalid_payload` before any task effect.

### Action Values

| Action | Endpoint | Signer |
|--------|----------|--------|
| `create_task` | `POST /tasks` (task_token) | Poster |
| `escrow_lock` | `POST /tasks` (escrow_token, forwarded to CB) | Poster |
| `cancel_task` | `POST /tasks/{id}/cancel` | Poster |
| `submit_bid` | `POST /tasks/{id}/bids` | Bidder |
| `list_bids` | `GET /tasks/{id}/bids` (OPEN only) | Poster |
| `accept_bid` | `POST /tasks/{id}/bids/{bid_id}/accept` | Poster |
| `upload_asset` | `POST /tasks/{id}/assets` | Worker |
| `submit_deliverable` | `POST /tasks/{id}/submit` | Worker |
| `approve_task` | `POST /tasks/{id}/approve` | Poster |
| `dispute_task` | `POST /tasks/{id}/dispute` | Poster |
| `record_ruling` | `POST /tasks/{id}/ruling` | Platform |
| `escrow_release` | (outgoing to Central Bank) | Platform |

---

## Token Delivery

Tokens are delivered in two ways depending on the endpoint type:

### POST Endpoints (JWS in Body)

For POST endpoints (except asset upload), the token is in the request body:

```json
{
  "token": "<JWS compact token>"
}
```

The `POST /tasks` endpoint is the exception — it uses two tokens:

```json
{
  "task_token": "<JWS compact token>",
  "escrow_token": "<JWS compact token>"
}
```

### Asset Upload (JWS in Header)

For `POST /tasks/{id}/assets`, the body is multipart form data, so the token goes in the HTTP header:

```
Authorization: Bearer <JWS compact token>
```

### GET Endpoints with Auth (JWS in Header)

For `GET /tasks/{id}/bids` during OPEN status:

```
Authorization: Bearer <JWS compact token>
```

---

## Authentication Flow

### Standard Flow (Most Endpoints)

```
Agent                      Task Board
  |                             |
  |  1. Construct JWS payload   |
  |     { action, ... }         |
  |                             |
  |  2. Sign with Ed25519       |
  |     private key             |
  |     → produces certificate  |
  |                             |
  |  3. Send request            |
  |     { "token": "eyJ..." }   |
  |  =========================> |
  |                             |
  |                             |  4. Agent token: Identity client
  |                             |     validates complete success;
  |                             |     platform token: strict local
  |                             |     verification, no HTTP
  |                             |  5. Receive typed VerifiedJws
  |                             |  6. Check aud/resource/action
  |                             |  7. Check authorization
  |                             |     (signer == expected agent)
  |                             |  8. Validate payload fields
  |                             |  9. Execute operation
  |                             |
  | 10. Response                |
  |  <========================= |
```

### Two-Token Flow (Task Creation)

```
Poster                     Task Board                           Central Bank
  |                             |                                     |
  |  POST /tasks                |                                     |
  |  { task_token,              |                                     |
  |    escrow_token }           |                                     |
  |  =========================> |                                     |
  |                             |                                     |
  |                             |  Verify task_token through Identity |
  |                             |  and validate complete VerifiedJws  |
  |                             |                                     |
  |                             |  Validate task_token                |
  |                             |  (action, poster_id,                |
  |                             |   task_id format, etc.)             |
  |                             |                                     |
  |                             |  Cross-validate tokens              |
  |                             |  (task_id match,                    |
  |                             |   amount == reward)                 |
  |                             |                                     |
  |                             |  Forward escrow_token               |
  |                             |  POST /escrow/lock                  |
  |                             |  { "token": escrow_token }          |
  |                             |  =================================> |
  |                             |                                     |
  |                             |  (Central Bank is the admitting     |
  |                             |   receiver and verifies the poster  |
  |                             |   token through Identity)           |
  |                             |                                     |
  |                             |  { escrow_id, status }              |
  |                             |  <================================= |
  |                             |                                     |
  |                             |  Create task record                 |
  |                             |                                     |
  |  201 { task }               |                                     |
  |  <========================= |                                     |
```

**Key point:** The Task Board does not admit `escrow_token` as an authenticated token. It may inspect its signed-looking payload only to cross-check `task_id` and `amount`; this is not a `VerifiedJws` and grants no authority. The protected header targets `aud="central-bank"` and `resource="POST /escrow/lock"`. Central Bank is the admitting receiver and performs full agent verification through Identity before locking funds.

**Escrow token decode errors:** If the `escrow_token` is not valid three-part JWS compact format, it fails at step 4 (`invalid_jws`). If the `escrow_token` has valid three-part format but the payload section is not valid base64url or does not decode to valid JSON, it also fails as `invalid_jws` — the token is structurally malformed. If the payload decodes to valid JSON but is missing `task_id` or `amount`, the cross-validation cannot proceed and the error is `token_mismatch`.

### Platform-Signed Outgoing Calls

When the Task Board needs to release escrow (on approval, cancellation, or timeout), it creates and signs a JWS token as the platform agent:

```
Task Board                                Central Bank (with PlatformAgent)
  |                                             |
  |  1. Construct payload:                      |
  |     { action: "escrow_release",             |
  |       escrow_id: "esc-xxx",                 |
  |       recipient_account_id: "a-xxx" }       |
  |                                             |
  |  2. Sign with platform private key          |
  |     Exact header: aud="central-bank",       |
  |     concrete release resource, platform kid |
  |                                             |
  |  3. POST /escrow/{id}/release               |
  |     { "token": "eyJ..." }                   |
  |  ===========================================>|
  |                                             |
  |                                             |  Strict local verification
  |                                             |  -> VerifiedJws; check
  |                                             |  platform kid and exact
  |                                             |  aud/resource/action
  |                                             |
  |  4. { escrow_id, status: released }         |
  |  <===========================================|
```

---

## Authorization Rules

After the appropriate Identity-backed or strict local path returns `VerifiedJws` and the receiver checks audience, concrete resource, and action:

### Agent Operations

1. **Signer must match the expected agent ID.** The authenticated `header.kid` must match the relevant field in the JWS payload:
   - For poster operations: `kid` must match `poster_id`
   - For worker operations: `kid` must match `worker_id` or `bidder_id`

2. **Agent must have the correct role for the task.** Beyond identity verification:
   - Cancel, accept bid, approve, dispute: signer must be the task's `poster_id`
   - Submit bid: signer must NOT be the task's `poster_id` (no self-bidding)
   - Upload asset, submit deliverable: signer must be the task's `worker_id`

### Platform Operations

1. **Signer must be the platform agent.** For `POST /tasks/{id}/ruling`, the `kid` must match `settings.platform.agent_id`.

2. **No agent-level checks.** Platform operations do not check agent roles — only platform identity.

---

## Error Codes

### Authentication Errors

| Status | Code | When |
|--------|------|------|
| 400 | `invalid_jws` | Non-object request body or signed payload, token transport/compact/header form, decoded signature length other than 64 bytes, exact header fields/types/constants, or lifetime bound is invalid |
| 400 | `invalid_payload` | Action is missing/non-string, authenticated audience/resource/action mismatches, or required domain fields are missing |
| 400 | `token_mismatch` | `task_id` or `amount`/`reward` mismatch between `task_token` and `escrow_token` (task creation only) |
| 401 | `token_expired` | Authentic token satisfies `now >= exp + clock_skew_seconds` |
| 401 | `token_not_yet_valid` | Authentic token satisfies `iat > now + clock_skew_seconds` |
| 403 | `forbidden` | Signature verification fails, authenticated signer does not match the required agent, or authenticated local `kid` is not the platform identity |
| 502 | `identity_service_unavailable` | Agent-token verification cannot reach Identity or Identity returns a malformed successful response |
| 502 | `central_bank_unavailable` | Cannot connect to Central Bank, timeout, or escrow operation failed |

### Error Precedence

Errors are checked in this order (first match wins):

1. `415 unsupported_media_type` — wrong Content-Type (expected `application/json` for most endpoints, `multipart/form-data` for asset upload)
2. `413 payload_too_large` — body exceeds `request.max_body_size` or file exceeds `assets.max_file_size`
3. `400 invalid_json` — malformed JSON body
4. `400 invalid_jws` — missing or malformed token field(s)
5. Strict verification result: `400 invalid_jws`, `401 token_expired`, `401 token_not_yet_valid`, `403 forbidden`, or agent-tier `502 identity_service_unavailable`
6. `400 invalid_payload` — audience/resource/action mismatch, missing required payload fields, or `task_id`/`bid_id` in payload does not match the URL path
7. `400 token_mismatch` — cross-token validation failure (task creation)
8. `403 forbidden` — signer does not match expected agent (poster/worker/platform). See note below on role-dependent checks.
9. `404 task_not_found` — task does not exist
10. `409 invalid_status` — task is in wrong status for this operation
11. Domain-specific validation errors (`self_bid`, `bid_already_exists`, `no_assets`, etc.)
12. `502 central_bank_unavailable` — escrow operation failed

### Notes on Error Mapping

- **Invalid signature** returns `403 forbidden`. Authentic expiry and future issue time use their distinct `401` errors.
- **Signer mismatch** also returns `403 forbidden` with a different message. The certificate is cryptographically valid, but the signer lacks authorization.
- **Role-dependent signer checks and status ordering.** For operations that require a role only assigned in a specific status (e.g., `worker_id` is only set in ACCEPTED status), the status check (step 10) takes priority over the signer-role check (step 8). Example: uploading an asset to an OPEN task (which has no worker) returns `409 invalid_status`, not `403 forbidden`. The signer's identity is verified at step 5 (certificate validity) regardless — this note only applies to the role-authorization check at step 8.
- **Central Bank errors** are returned as `502 central_bank_unavailable`. Specific Central Bank error codes (e.g., `insufficient_funds`) are propagated in the `details` field when available.
- **Identity transport or malformed success** is `502 identity_service_unavailable` on agent routes. The shared client requires complete, consistent `agent_id`, `protected_header`, and object `payload`; it never fabricates a `VerifiedJws`.

---

## Configuration

### Identity Integration

Agent-signed routes require explicit Identity base URL, verify-JWS path, and timeout configuration. Missing or invalid values fail startup. There is no local platform fallback for ordinary agent tokens.

### Central Bank Integration

```yaml
central_bank:
  base_url: "http://localhost:8002"
  escrow_lock_path: "/escrow/lock"
  escrow_release_path: "/escrow/{escrow_id}/release"
  timeout_seconds: 10
```

### Platform Agent

```yaml
platform:
  agent_id: ""
  private_key_path: ""
  public_key_path: ""
```

- `platform.agent_id`: The agent ID of the platform. Used to verify incoming platform-signed certificates and as the `kid` for outgoing platform-signed tokens.
- `platform.private_key_path`: Absolute path to the Ed25519 private key file (PEM format). Used to sign outgoing escrow release/split requests.
- `platform.public_key_path`: Absolute path to the Ed25519 public key file (PEM format). Used by the PlatformAgent to verify incoming certificates via `validate_certificate()`.

All fields are required. Missing fields cause startup failure.

Token policy is also required and explicit. Initial YAML values are a 300-second signer TTL, a 300-second admission maximum, and 30 seconds of clock skew. TTL and maximum lifetime must be positive; skew must be nonnegative. Invalid or missing values fail configuration construction, and no no-expiry mode exists.

---

## Infrastructure

### IdentityClient

Agent-signed routes call Identity and type-strictly bind the complete `valid:true` response to the originally requested token. JSON `true`, `1`, and `1.0` are distinct. An invalid requested token, inconsistent `agent_id`/`protected_header.kid`, substituted type/value, missing field, or non-object payload is `502 identity_service_unavailable`; the client never constructs a payload-only success. A valid response returns `VerifiedJws(header, payload)`.

### PlatformAgent

Initialized during startup by loading the Ed25519 public and private keys from `platform.public_key_path` and `platform.private_key_path`. Stored in `AppState`.

Provides strict local verification returning `VerifiedJws(header, payload)` under an explicit `TokenPolicy`. It verifies the original compact bytes and makes no Identity HTTP request.

### CentralBankClient

Initialized during startup, stored in `AppState`, closed on shutdown.

Provides:
- `lock_escrow(token: str) -> dict[str, Any]` — forwards the poster's escrow token to `POST /escrow/lock`. Returns `{"escrow_id": "...", "amount": N, "task_id": "...", "status": "locked"}`.
- `release_escrow(escrow_id: str, recipient_account_id: str) -> dict[str, Any]` — creates a platform-signed JWS and calls `POST /escrow/{escrow_id}/release`. Returns `{"escrow_id": "...", "status": "released", ...}`.

### PlatformSigner

Initialized during startup by loading the Ed25519 private key from `platform.private_key_path`. Used for outgoing platform-signed calls (e.g., escrow release).

Provides target-explicit signing: the caller supplies audience and concrete resource, the signer requires a positive TTL and registered non-blank `kid`, and the payload supplies the existing action/domain fields. There is no no-expiry or payload-only compatibility form.

### Dependencies

Required in `pyproject.toml`:
- `httpx>=0.28.0` — async HTTP client for Central Bank calls
- `cryptography>=44.0.0` — Ed25519 key loading
- `joserfc>=1.0.0` — JWS token creation for platform operations

---

## Token Replay Considerations

The `action` field in every JWS payload prevents cross-operation replay. A token signed for `"submit_bid"` cannot be used for `"approve_task"`.

However, same-operation replay is possible. For example, the same `"submit_bid"` token could theoretically be sent twice. This is mitigated by:

- **Bid uniqueness constraint:** `(task_id, bidder_id)` is unique, so a replayed bid token gets `409 bid_already_exists`.
- **Status checks:** Most operations require a specific task status. Once a status transition occurs, the same token cannot trigger it again (e.g., a replayed `"approve_task"` token fails because the task is already APPROVED).
- **Escrow idempotency:** The Central Bank enforces `(payer_account_id, task_id)` uniqueness for locked escrow, so a replayed `"escrow_lock"` token gets `409`.

Full replay protection (timestamps, nonces) is out of scope, consistent with the Identity service design.

---

## Interaction: Impersonation Attempt

```
Mallory                    Task Board (with PlatformAgent)
  |                             |
  |  Signs JWS as mallory       |
  |  but sets poster_id: alice  |
  |  in payload                 |
  |                             |
  |  POST /tasks/{id}/approve   |
  |  { "token": "eyJ..." }     |
  |  =========================> |
  |                             |
  |                             |  Identity returns VerifiedJws
  |                             |  (mallory signed it, sig is correct)
  |                             |
  |                             |  kid: "a-mallory" != poster_id: "a-alice"
  |                             |  mallory != alice → 403
  |                             |
  |  403 { error: forbidden }  |
  |  <========================= |
```

## Interaction: Escrow Token Replay

```
Mallory                    Task Board                Central Bank
  |                             |                          |
  |  Captures Alice's           |                          |
  |  escrow_token from a        |                          |
  |  previous task creation     |                          |
  |                             |                          |
  |  POST /tasks                |                          |
  |  { task_token: (mallory's), |                          |
  |    escrow_token: (alice's) }|                          |
  |  =========================> |                          |
  |                             |                          |
  |                             |  Verify task_token       |
  |                             |  (mallory is signer)     |
  |                             |                          |
  |                             |  Cross-validate:         |
  |                             |  task_id mismatch or     |
  |                             |  amount mismatch         |
  |                             |                          |
  |  400 token_mismatch         |                          |
  |  <========================= |                          |
```

Even if `task_id` and `amount` happen to match, the Central Bank will reject the escrow token because it references a `task_id` that already has locked escrow (uniqueness constraint on `payer_account_id + task_id`).
