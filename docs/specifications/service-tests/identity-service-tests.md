# Identity Service - Production Release Test Specification

## Purpose

This document is the release-gate test specification for the Identity & PKI Service.
It is intentionally strict and unambiguous:

- Every negative case has one required status code and one required error code.
- Every failing response must use the standard error envelope.
- Any behavior not listed here is out of scope for release sign-off.

This document focuses only on core functionality and endpoint abuse resistance.
Nice-to-have tests are intentionally excluded.

---

## Required API Error Contract (Normative for Release)

All failing responses must be JSON in this format:

```json
{
  "error": "ERROR_CODE",
  "message": "Human-readable description"
}
```

Required status/error mappings:

| Status | Error Code                 | Required When |
|--------|----------------------------|---------------|
| 400    | `MISSING_FIELD`            | A required field is absent or `null` |
| 400    | `INVALID_FIELD_TYPE`       | A required field has the wrong JSON type |
| 400    | `INVALID_JSON`             | Request body is malformed JSON |
| 400    | `INVALID_PUBLIC_KEY`       | `public_key` is not a valid `ed25519:<base64 32-byte key>` |
| 400    | `INVALID_BASE64`           | `payload` or `signature` is not valid base64 |
| 400    | `INVALID_SIGNATURE_LENGTH` | Decoded signature length is not exactly 64 bytes |
| 400    | `INVALID_NAME`             | `name` is empty or whitespace-only |
| 404    | `AGENT_NOT_FOUND`          | Referenced `agent_id` does not exist |
| 405    | `METHOD_NOT_ALLOWED`       | Unsupported HTTP method on a defined route |
| 409    | `PUBLIC_KEY_EXISTS`        | Duplicate public key registration |
| 413    | `PAYLOAD_TOO_LARGE`        | Request body exceeds configured max size |
| 415    | `UNSUPPORTED_MEDIA_TYPE`   | `Content-Type` is not `application/json` for JSON endpoints |

---

## Test Data Conventions

- `keypair_A`, `keypair_B`, `keypair_E` are freshly generated Ed25519 keypairs.
- `payload_b64 = base64(payload_bytes)`.
- `signature_b64 = base64(Ed25519.sign(private_key, payload_bytes))`.
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
- `error = PUBLIC_KEY_EXISTS`
- Original Alice record remains unchanged

### REG-04 Concurrent duplicate key race is safe
**Setup:** Prepare two identical-key registration requests in parallel.
**Action:** Send both simultaneously.
**Expected:**
- Exactly one `201 Created`
- Exactly one `409 Conflict` with `PUBLIC_KEY_EXISTS`
- No duplicate rows for the key
- Use winner `agent_id` from the `201` response; `GET /agents/{winner_id}` returns the expected key

### REG-05 Duplicate names are allowed
**Setup:** Generate two distinct keypairs.
**Action:** Register both with `name = "SharedName"`.
**Expected:**
- Both requests return `201`
- IDs are unique

### REG-06 Missing `name`
**Action:** Submit body with `public_key` only.
**Expected:** `400`, `error = MISSING_FIELD`

### REG-07 Missing `public_key`
**Action:** Submit body with `name` only.
**Expected:** `400`, `error = MISSING_FIELD`

### REG-08 Null required fields
**Action:** `{"name": null, "public_key": null}`
**Expected:** `400`, `error = MISSING_FIELD`

### REG-09 Wrong field types
**Action:** `{"name": 123, "public_key": true}`
**Expected:** `400`, `error = INVALID_FIELD_TYPE`

### REG-10 Empty or whitespace-only `name`
**Action:** `{"name": ""}` and `{"name": "   "}` with valid key.
**Expected:** `400`, `error = INVALID_NAME`

### REG-11 Invalid key prefix
**Action:** `public_key = "rsa:<base64>"`
**Expected:** `400`, `error = INVALID_PUBLIC_KEY`

### REG-12 Invalid key base64 payload
**Action:** `public_key = "ed25519:%%%not-base64%%%"`
**Expected:** `400`, `error = INVALID_PUBLIC_KEY`

### REG-13 Invalid key length after decode
**Action:** `public_key = "ed25519:<base64(16 bytes)>"`
**Expected:** `400`, `error = INVALID_PUBLIC_KEY`

### REG-14 All-zero key is rejected
**Action:** `public_key = "ed25519:<base64(32 zero bytes)>"`
**Expected:** `400`, `error = INVALID_PUBLIC_KEY`

### REG-15 Mass-assignment resistance (extra fields)
**Action:** Send `agent_id`, `registered_at`, `is_admin` alongside valid fields.
**Expected:**
- `201 Created`
- Service-generated `agent_id` and `registered_at` are used
- Extra fields are ignored

### REG-16 Malformed JSON body
**Action:** Send truncated/invalid JSON.
**Expected:** `400`, `error = INVALID_JSON`

### REG-17 Wrong content type
**Action:** `Content-Type: text/plain` with JSON-looking body.
**Expected:** `415`, `error = UNSUPPORTED_MEDIA_TYPE`

### REG-18 Oversized request body
**Action:** Exceed configured max request size.
**Expected:** `413`, `error = PAYLOAD_TOO_LARGE`

---

## Category 2: Verification (`POST /agents/verify`)

### VER-01 Valid signature verifies true
**Setup:** Register Alice, sign `payload = b"hello world"` with Alice key.
**Action:** Verify using Alice `agent_id`, `payload_b64`, `signature_b64`.
**Expected:** `200`, body `{ "valid": true, "agent_id": "<alice_id>" }`

### VER-02 Wrong signature verifies false (not an error)
**Setup:** Register Alice and Bob; Bob signs Alice payload.
**Action:** Verify Bob signature under Alice `agent_id`.
**Expected:** `200`, body `{ "valid": false, "reason": "signature mismatch" }`

### VER-03 Tampered payload verifies false
**Setup:** Sign original payload, then alter payload bytes.
**Action:** Verify altered payload with original signature.
**Expected:** `200`, `{ "valid": false, "reason": "signature mismatch" }`

### VER-04 Cross-identity replay attempt fails
**Setup:** Alice signs payload; Eve has different identity.
**Action:** Verify Alice signature using Eve `agent_id`.
**Expected:** `200`, `{ "valid": false, "reason": "signature mismatch" }`

### VER-05 Non-existent `agent_id`
**Action:** Verify with unknown `agent_id`.
**Expected:** `404`, `error = AGENT_NOT_FOUND`

### VER-06 Invalid base64 payload
**Action:** `payload = "%%%not-base64%%%"`.
**Expected:** `400`, `error = INVALID_BASE64`

### VER-07 Invalid base64 signature
**Action:** `signature = "%%%not-base64%%%"`.
**Expected:** `400`, `error = INVALID_BASE64`

### VER-08 Signature too short (decoded length != 64)
**Action:** Base64 for 32-byte signature.
**Expected:** `400`, `error = INVALID_SIGNATURE_LENGTH`

### VER-09 Signature too long (decoded length != 64)
**Action:** Base64 for 128-byte signature.
**Expected:** `400`, `error = INVALID_SIGNATURE_LENGTH`

### VER-10 Missing required fields
**Action:** Omit each of `agent_id`, `payload`, `signature` in separate requests.
**Expected:** `400`, `error = MISSING_FIELD`

### VER-11 Null required fields
**Action:** `{"agent_id": null, "payload": null, "signature": null}`
**Expected:** `400`, `error = MISSING_FIELD`

### VER-12 Wrong field types
**Action:** `{"agent_id": true, "payload": [1], "signature": {"x": 1}}`
**Expected:** `400`, `error = INVALID_FIELD_TYPE`

### VER-13 Empty payload is supported
**Setup:** Sign `payload = b""` with Alice key.
**Action:** Verify with empty payload (`payload = ""`) and correct signature.
**Expected:** `200`, `{ "valid": true, "agent_id": "<alice_id>" }`

### VER-14 Large payload (1 MB) remains valid
**Setup:** Sign 1 MB random payload.
**Action:** Verify payload/signature pair.
**Expected:** `200`, `{ "valid": true, "agent_id": "<alice_id>" }`

### VER-15 Malformed JSON body
**Action:** Send truncated/invalid JSON.
**Expected:** `400`, `error = INVALID_JSON`

### VER-16 Wrong content type
**Action:** `Content-Type: text/plain`
**Expected:** `415`, `error = UNSUPPORTED_MEDIA_TYPE`

### VER-17 Idempotent verification
**Setup:** Valid verification request body.
**Action:** Send identical verify request twice.
**Expected:** Both responses are `200` and byte-for-byte identical JSON

### VER-18 SQL injection string in `agent_id`
**Action:** `agent_id = "' OR '1'='1"`.
**Expected:** `404`, `error = AGENT_NOT_FOUND`

---

## Category 3: Read/List/Health

### READ-01 Lookup existing agent
**Setup:** Register Alice.
**Action:** `GET /agents/{alice_id}`
**Expected:** `200` with exact `agent_id`, `name`, `public_key`, `registered_at`

### READ-02 Lookup non-existent agent
**Action:** `GET /agents/a-00000000-0000-0000-0000-000000000000`
**Expected:** `404`, `error = AGENT_NOT_FOUND`

### READ-03 Malformed/path-traversal ID does not break routing
**Action:** `GET /agents/not-a-valid-id` and `GET /agents/../../etc/passwd`
**Expected:**
- Request is rejected (404)
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
- `GET /agents/verify`
- `POST /agents/{agent_id}`
- `PATCH /agents/{agent_id}`
- `DELETE /agents/{agent_id}`
- `POST /agents`
- `POST /health`
**Expected:** `405`, `error = METHOD_NOT_ALLOWED` for each

---

## Category 5: Cross-Cutting Security Assertions

### SEC-01 Error envelope consistency
**Action:** For at least one failing test per error code, assert response has exactly:
- top-level `error` (string)
- top-level `message` (string)
**Expected:** All failures comply

### SEC-02 No internal error leakage
**Action:** Trigger representative failures (`INVALID_JSON`, `INVALID_BASE64`, duplicate key, malformed ID).
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
4. All failing responses conform to the required error envelope.

---

## Coverage Summary

| Category | IDs | Count |
|----------|-----|-------|
| Registration | REG-01 to REG-18 | 18 |
| Verification | VER-01 to VER-18 | 18 |
| Read/List/Health | READ-01 to HEALTH-03 | 8 |
| HTTP misuse | HTTP-01 | 1 |
| Cross-cutting security | SEC-01 to SEC-03 | 3 |
| **Total** |  | **48** |

| Endpoint | Covered By |
|----------|------------|
| `POST /agents/register` | REG-01 to REG-18, SEC-01, SEC-02 |
| `POST /agents/verify` | VER-01 to VER-18, SEC-01, SEC-02 |
| `GET /agents/{agent_id}` | READ-01 to READ-03 |
| `GET /agents` | LIST-01 to LIST-02 |
| `GET /health` | HEALTH-01 to HEALTH-03 |
