# Identity Service - Production Release Test Specification

## Purpose

This document is the release-gate test specification for the Identity & PKI Service.
It is intentionally strict and unambiguous:

- Every negative case has one required status code and one required error code.
- Every failing response must use the standard error envelope.
- Any behavior not listed here is out of scope for release sign-off.

This document focuses only on core functionality and endpoint abuse resistance.
Nice-to-have tests are intentionally excluded.

**Historical note:** an earlier raw `POST /agents/verify` endpoint (`{agent_id, payload, signature}`) existed before JWS tokens were introduced. It has been superseded by `POST /agents/verify-jws`, the endpoint the platform's shared `IdentityClient` actually calls, and is no longer part of this release gate.

---

## Required API Error Contract (Normative for Release)

All failing responses must be JSON in this format, exactly three top-level fields:

```json
{
  "error": "error_code",
  "message": "Human-readable description",
  "details": {}
}
```

Error codes are lowercase snake_case.

Required status/error mappings:

| Status | Error Code                 | Required When |
|--------|-----------------------------|---------------|
| 400    | `missing_field`             | A required field is absent or `null` |
| 400    | `invalid_field_type`        | A required field has the wrong JSON type |
| 400    | `invalid_json`              | Request body is malformed JSON |
| 400    | `invalid_public_key`        | `public_key` is not a valid `ed25519:<base64 32-byte key>` |
| 400    | `invalid_name`               | `name` is empty or whitespace-only |
| 400    | `invalid_jws`                | `token` is not a valid compact JWS, header is unparsable, `alg` is not `EdDSA`, `kid` is missing/non-string, or the decoded payload is not a JSON object |
| 401    | `token_expired`              | The JWS signature is authentic but the header `exp` claim is in the past |
| 404    | `agent_not_found`            | Referenced `agent_id` (or JWS header `kid`) does not exist |
| 405    | `method_not_allowed`         | Unsupported HTTP method on a defined route |
| 409    | `public_key_exists`          | Duplicate public key registration |
| 413    | `payload_too_large`          | Request body exceeds configured max size |
| 415    | `unsupported_media_type`     | `Content-Type` is not `application/json` for JSON endpoints |

---

## Test Data Conventions

- `keypair_A`, `keypair_B`, `keypair_E` are freshly generated Ed25519 keypairs.
- A JWS token is a compact `header.payload.signature` string, base64url-encoded, signed with EdDSA. The protected header carries `alg`, `kid` (the signer's `agent_id`), and optionally `iat`/`exp` (Unix seconds).
- All IDs returned by the service must match `a-<uuid4>`.

---

## Category 1: Registration (`POST /agents/register`)

### REG-01 Register one valid agent
**Setup:** Generate `keypair_A`.
**Action:** Register `{name: "Alice", public_key: "ed25519:<base64(public_key_A)>"}`
**Expected:**
- `201 Created`
- Body includes `agent_id`, `name`, `public_key`, `registered_at`
- `agent_id` matches `a-<uuid4>`
- `registered_at` is valid ISO 8601 timestamp

### REG-02 Register second valid agent with different key
**Setup:** Register Alice with `keypair_A`, generate `keypair_B`.
**Action:** Register Bob with `keypair_B`.
**Expected:**
- `201 Created`
- Returned `agent_id` differs from Alice's `agent_id`

### REG-03 Duplicate key is rejected
**Setup:** Register Alice with `keypair_A`.
**Action:** Register Eve with the same public key.
**Expected:**
- `409 Conflict`
- `error = public_key_exists`
- Original Alice record remains unchanged

### REG-04 Concurrent duplicate key race is safe
**Setup:** Prepare two identical-key registration requests in parallel.
**Action:** Send both simultaneously.
**Expected:**
- Exactly one `201 Created`
- Exactly one `409 Conflict` with `public_key_exists`
- No duplicate rows for the key (enforced by the DB Gateway)
- Use winner `agent_id` from the `201` response; `GET /agents/{winner_id}` returns the expected key

### REG-05 Duplicate names are allowed
**Setup:** Generate two distinct keypairs.
**Action:** Register both with `name = "SharedName"`.
**Expected:**
- Both requests return `201`
- IDs are unique

### REG-06 Missing `name`
**Action:** Submit body with `public_key` only.
**Expected:** `400`, `error = missing_field`

### REG-07 Missing `public_key`
**Action:** Submit body with `name` only.
**Expected:** `400`, `error = missing_field`

### REG-08 Null required fields
**Action:** `{"name": null, "public_key": null}`
**Expected:** `400`, `error = missing_field`

### REG-09 Wrong field types
**Action:** `{"name": 123, "public_key": true}`
**Expected:** `400`, `error = invalid_field_type`

### REG-10 Empty or whitespace-only `name`
**Action:** `{"name": ""}` and `{"name": "   "}` with valid key.
**Expected:** `400`, `error = invalid_name`

### REG-11 Invalid key prefix
**Action:** `public_key = "rsa:<base64>"`
**Expected:** `400`, `error = invalid_public_key`

### REG-12 Invalid key base64 payload
**Action:** `public_key = "ed25519:%%%not-base64%%%"`
**Expected:** `400`, `error = invalid_public_key`

### REG-13 Invalid key length after decode
**Action:** `public_key = "ed25519:<base64(16 bytes)>"`
**Expected:** `400`, `error = invalid_public_key`

### REG-14 All-zero key is rejected
**Action:** `public_key = "ed25519:<base64(32 zero bytes)>"`
**Expected:** `400`, `error = invalid_public_key`

### REG-15 Mass-assignment resistance (extra fields)
**Action:** Send `agent_id`, `registered_at`, `is_admin` alongside valid fields.
**Expected:**
- `201 Created`
- Service-generated `agent_id` and `registered_at` are used
- Extra fields are ignored

### REG-16 Malformed JSON body
**Action:** Send truncated/invalid JSON.
**Expected:** `400`, `error = invalid_json`

### REG-17 Wrong content type
**Action:** `Content-Type: text/plain` with JSON-looking body.
**Expected:** `415`, `error = unsupported_media_type`

### REG-18 Oversized request body
**Action:** Exceed configured max request size.
**Expected:** `413`, `error = payload_too_large`

---

## Category 2: Signature Verification (`POST /agents/verify-jws`)

`verify-jws` is the live, tested signature-verification endpoint — the one the platform's shared `IdentityClient` calls on behalf of central-bank, task-board, and reputation.

### JWS-01 Valid token returns payload
**Setup:** Register Alice, build a JWS token signed by Alice's key with `kid = alice_id` and payload `{"action": "escrow_lock", "amount": 10}`.
**Action:** `POST /agents/verify-jws {token}`
**Expected:** `200`, body `{"valid": true, "agent_id": "<alice_id>", "payload": {"action": "escrow_lock", "amount": 10}}`

### JWS-02 Empty payload object is valid
**Setup:** Register Alice, build a JWS token with payload `{}`.
**Action:** Verify the token.
**Expected:** `200`, `{"valid": true, "payload": {}}`

### JWS-03 Wrong signer returns valid=false
**Setup:** Register Alice; build a token with `kid = alice_id` but signed by a different keypair.
**Action:** Verify the token.
**Expected:** `200`, `{"valid": false, "reason": "signature mismatch"}` (not an error)

### JWS-04 Unknown `kid` returns 404
**Action:** Verify a well-formed, validly-signed token whose `kid` references an unregistered `agent_id`.
**Expected:** `404`, `error = agent_not_found`

### JWS-05 Malformed token returns 400
**Action:** `token = "not.a.valid.jws.token"`
**Expected:** `400`, `error = invalid_jws`

### JWS-06 Missing `token` field
**Action:** `POST /agents/verify-jws {}`
**Expected:** `400`, `error = missing_field`

### JWS-07 Null `token` field
**Action:** `{"token": null}`
**Expected:** `400`, `error = missing_field`

### JWS-08 Non-string `token`
**Action:** `{"token": 12345}`
**Expected:** `400`, `error = invalid_field_type`

### JWS-09 Missing `kid` in header
**Setup:** Register an agent; build a token whose protected header omits `kid`.
**Action:** Verify the token.
**Expected:** `400`, `error = invalid_jws`

### JWS-10 Wrong algorithm in header
**Action:** Verify a token whose header declares an algorithm other than `EdDSA` (e.g. `HS256`).
**Expected:** `400`, `error = invalid_jws`

### JWS-11 Non-JSON payload
**Setup:** Register an agent; build a token whose payload section decodes to bytes that are not valid JSON.
**Action:** Verify the token.
**Expected:** `400`, `error = invalid_jws`

### JWS-12 Wrong content type
**Action:** `Content-Type: text/plain`
**Expected:** `415`, `error = unsupported_media_type`

### JWS-13 Wrong HTTP method
**Action:** `GET /agents/verify-jws`
**Expected:** `405`, `error = method_not_allowed`

### JWS-14 Malformed JSON body
**Action:** Send truncated/invalid JSON.
**Expected:** `400`, `error = invalid_json`

### JWS-15 Expired token is rejected
**Setup:** Register an agent; build a token with `iat`/`exp` header claims where `exp` is in the past (relative to the server clock).
**Action:** Verify the token.
**Expected:** `401`, `error = token_expired`

### JWS-16 Token with future `exp` is accepted
**Setup:** Register an agent; build a token with `exp` in the future.
**Action:** Verify the token.
**Expected:** `200`, `{"valid": true}`

### JWS-17 Token without `iat`/`exp` is accepted (legacy tolerance)
**Setup:** Register an agent; build a token whose header carries neither `iat` nor `exp`.
**Action:** Verify the token.
**Expected:** `200`, `{"valid": true}` — Identity does not require expiry claims to be present.

---

## Category 3: Read/List/Health

### READ-01 Lookup existing agent
**Setup:** Register Alice.
**Action:** `GET /agents/{alice_id}`
**Expected:** `200` with exact `agent_id`, `name`, `public_key`, `registered_at`

### READ-02 Lookup non-existent agent
**Action:** `GET /agents/a-00000000-0000-0000-0000-000000000000`
**Expected:** `404`, `error = agent_not_found`

### READ-03 Malformed/path-traversal ID does not break routing
**Action:** `GET /agents/not-a-valid-id` and `GET /agents/../../etc/passwd`
**Expected:**
- Request is rejected (`404`)
- No stack traces, filesystem paths, or internal diagnostics in response body

### LIST-01 Empty list on fresh system
**Action:** `GET /agents`
**Expected:** `200`, body `{ "agents": [] }`

### LIST-02 Populated list omits public keys
**Setup:** Register at least 2 agents.
**Action:** `GET /agents`
**Expected:**
- `200`
- Correct agent count
- Each entry has `agent_id`, `name`, `registered_at`
- No entry contains `public_key`

### HEALTH-01 Health schema is correct
**Action:** `GET /health`
**Expected:**
- `200`
- Body contains `status`, `uptime_seconds`, `started_at`, `registered_agents`
- `status = "ok"`

### HEALTH-02 Registered count is exact
**Setup:** Register `N` agents.
**Action:** `GET /health`
**Expected:** `registered_agents = N`

### HEALTH-03 Uptime is monotonic
**Action:** Call `GET /health` twice with delay >= 1 second.
**Expected:** second `uptime_seconds` > first `uptime_seconds`

---

## Category 4: HTTP Method and Endpoint Misuse

### HTTP-01 Wrong method on defined routes is blocked
**Action:** Send unsupported methods:
- `GET /agents/register`
- `PUT /agents/register`
- `GET /agents/verify-jws`
- `POST /agents/{agent_id}`
- `PATCH /agents/{agent_id}`
- `DELETE /agents/{agent_id}`
- `POST /agents`
- `POST /health`
**Expected:** `405`, `error = method_not_allowed` for each

---

## Category 5: Cross-Cutting Security Assertions

### SEC-01 Error envelope consistency
**Action:** For at least one failing test per error code, assert response has exactly:
- top-level `error` (string)
- top-level `message` (string)
- top-level `details` (object)
**Expected:** All failures comply

### SEC-02 No internal error leakage
**Action:** Trigger representative failures (`invalid_json`, `invalid_jws`, duplicate key, malformed ID).
**Expected:** `message` never includes stack traces, SQL fragments, file paths, or driver internals

### SEC-03 Agent IDs are opaque and random-format
**Action:** Register 5+ agents.
**Expected:** Every returned ID matches `a-<uuid4>`

---

## Release Gate Checklist

Service is release-ready only if:

1. All tests in this document pass.
2. No test marked deterministic has alternate acceptable behavior.
3. No endpoint returns `500` in any test scenario.
4. All failing responses conform to the required error envelope (`error`, `message`, `details`).

---

## Coverage Summary

| Category | IDs | Count |
|----------|-----|-------|
| Registration | REG-01 to REG-18 | 18 |
| Signature Verification | JWS-01 to JWS-17 | 17 |
| Read/List/Health | READ-01 to HEALTH-03 | 8 |
| HTTP misuse | HTTP-01 | 1 |
| Cross-cutting security | SEC-01 to SEC-03 | 3 |
| **Total** |  | **47** |

| Endpoint | Covered By |
|----------|------------|
| `POST /agents/register` | REG-01 to REG-18, SEC-01, SEC-02 |
| `POST /agents/verify-jws` | JWS-01 to JWS-17, SEC-01, SEC-02 |
| `GET /agents/{agent_id}` | READ-01 to READ-03 |
| `GET /agents` | LIST-01 to LIST-02 |
| `GET /health` | HEALTH-01 to HEALTH-03 |
