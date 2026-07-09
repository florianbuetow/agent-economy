# Reputation Service — Authentication Test Specification

## Purpose

This document is the release-gate test specification for adding JWS-based authentication to the Reputation Service's `POST /feedback` endpoint.

It is intentionally strict and unambiguous:

- Every negative case has one required status code and one required error code.
- Every failing response must use the standard error envelope.
- Any behavior not listed here is out of scope for release sign-off.

This document covers only authentication and authorization concerns. Business logic tests (feedback validation, visibility, sealed feedback) are covered by the existing `reputation-service-tests.md`. Those tests remain valid but must be executed using JWS-wrapped requests after this feature lands.

---

## Prerequisites

These tests require:

1. A `PlatformAgent` instantiated with valid Ed25519 keys for certificate verification
2. Agents with known Ed25519 keypairs (public + private keys)
3. The Reputation service configured with the PlatformAgent's public key for local certificate validation

---

## Required API Error Contract (New Auth Error Codes)

These error codes are added by the authentication feature. Existing error codes from `reputation-service-tests.md` remain unchanged.

| Status | Error Code                       | Required When                                                |
|--------|----------------------------------|--------------------------------------------------------------|
| 400    | `INVALID_JWS`                   | `token` field is missing, null, non-string, empty, or malformed (not a three-part compact serialization) |
| 400    | `INVALID_PAYLOAD`               | JWS payload is missing `action`, `action` is not `"submit_feedback"`, or `from_agent_id` is missing from the payload (required for signer matching) |
| 403    | `FORBIDDEN`                     | JWS signature verification failed (tampered, unknown agent), or signer does not match `from_agent_id` in payload |

All failing responses must use the standard error envelope:

```json
{
  "error": "ERROR_CODE",
  "message": "Human-readable description",
  "details": {}
}
```

---

## Test Data Conventions

- `agent_alice`, `agent_bob`, `agent_carol` are agents with known Ed25519 public/private keypairs.
- `jws(signer, payload)` denotes a JWS compact serialization (RFC 7515, EdDSA/Ed25519) with header `{"alg":"EdDSA","kid":"<signer.agent_id>"}`, the given JSON payload, and a valid Ed25519 signature.
- `tampered_jws(signer, payload)` denotes a JWS where the payload has been altered after signing (signature mismatch).
- Agent IDs use the format `a-<uuid4>`.
- Task IDs use the format `t-<uuid4>`.
- All valid JWS payloads include `"action": "submit_feedback"` unless explicitly testing invalid payloads.
- A "valid feedback JWS" means: `jws(alice, {action: "submit_feedback", task_id: "t-...", from_agent_id: alice.agent_id, to_agent_id: bob.agent_id, category: "delivery_quality", rating: "satisfied"})`.

---

## Category 1: JWS Token Validation (`POST /feedback`)

### AUTH-01 Valid JWS submits feedback successfully

**Setup:** Create `agent_alice` and `agent_bob` with known Ed25519 keypairs. Configure PlatformAgent with valid keys.
**Action:** `POST /feedback` with body:
```json
{"token": "<jws(alice, {action: 'submit_feedback', task_id: 't-xxx', from_agent_id: alice.id, to_agent_id: bob.id, category: 'delivery_quality', rating: 'satisfied', comment: 'Good work'})>"}
```
**Expected:**
- `201 Created`
- Body includes `feedback_id`, `task_id`, `from_agent_id`, `to_agent_id`, `category`, `rating`, `comment`, `submitted_at`, `visible`
- `feedback_id` matches `fb-<uuid4>`
- `from_agent_id` matches `alice.agent_id`
- `to_agent_id` matches `bob.agent_id`

### AUTH-02 Missing `token` field

**Action:** `POST /feedback` with body `{"task_id": "t-xxx", "from_agent_id": "a-xxx", ...}` (plain JSON, no `token` field).
**Expected:**
- `400 Bad Request`
- `error = INVALID_JWS`

### AUTH-03 `token` is null

**Action:** `POST /feedback` with body `{"token": null}`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_JWS`

### AUTH-04 `token` is not a string

**Action:** Send each of these bodies in separate requests:
- `{"token": 12345}`
- `{"token": ["eyJ..."]}`
- `{"token": {"jws": "eyJ..."}}`
- `{"token": true}`
**Expected:** `400`, `error = INVALID_JWS` for each.

### AUTH-05 `token` is empty string

**Action:** `POST /feedback` with body `{"token": ""}`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_JWS`

### AUTH-06 Malformed JWS (not three-part compact serialization)

**Action:** Send each of these tokens in separate requests:
- `{"token": "not-a-jws-at-all"}`
- `{"token": "only.two-parts"}`
- `{"token": "four.parts.is.wrong"}`
**Expected:** `400`, `error = INVALID_JWS` for each.

### AUTH-07 JWS with tampered payload (signature mismatch)

**Setup:** Create `agent_alice` with a known keypair. Construct a valid JWS, then modify the payload portion after signing.
**Action:** `POST /feedback` with `{"token": "<tampered_jws>"}`.
**Expected:**
- `403 Forbidden`
- `error = FORBIDDEN`

### AUTH-08 JWS signed by unknown agent (no valid certificate)

**Setup:** Generate a fresh Ed25519 keypair whose public key is NOT known to the PlatformAgent (no valid certificate).
**Action:** `POST /feedback` with a JWS signed by the unknown keypair.
**Expected:**
- `403 Forbidden`
- `error = FORBIDDEN`

---

## Category 2: JWS Payload Validation

### AUTH-09 Missing `action` in payload

**Setup:** Create `agent_alice` and `agent_bob` with known keypairs.
**Action:** `POST /feedback` with `jws(alice, {task_id: "t-xxx", from_agent_id: alice.id, to_agent_id: bob.id, category: "delivery_quality", rating: "satisfied"})` — payload has no `action` field.
**Expected:**
- `400 Bad Request`
- `error = INVALID_PAYLOAD`

### AUTH-10 Wrong `action` value

**Setup:** Create `agent_alice` and `agent_bob` with known keypairs.
**Action:** `POST /feedback` with `jws(alice, {action: "escrow_lock", task_id: "t-xxx", from_agent_id: alice.id, to_agent_id: bob.id, category: "delivery_quality", rating: "satisfied"})`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_PAYLOAD`

### AUTH-11 `action` is null

**Setup:** Create `agent_alice` and `agent_bob` with known keypairs.
**Action:** `POST /feedback` with `jws(alice, {action: null, task_id: "t-xxx", from_agent_id: alice.id, to_agent_id: bob.id, ...})`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_PAYLOAD`

---

## Category 3: Authorization (Signer Matching)

### AUTH-12 Signer matches `from_agent_id` — success

**Setup:** Create `agent_alice` and `agent_bob` with known keypairs.
**Action:** `POST /feedback` with `jws(alice, {action: "submit_feedback", from_agent_id: alice.id, to_agent_id: bob.id, ...})`.
**Expected:**
- `201 Created`
- `from_agent_id` in response matches `alice.agent_id`

### AUTH-13 Signer does NOT match `from_agent_id` — impersonation rejected

**Setup:** Create `agent_alice`, `agent_bob`, and `agent_carol` with known keypairs.
**Action:** Alice signs a JWS with `from_agent_id: carol.id` (Alice tries to submit feedback as Carol).
`POST /feedback` with `jws(alice, {action: "submit_feedback", from_agent_id: carol.id, to_agent_id: bob.id, ...})`.
**Expected:**
- `403 Forbidden`
- `error = FORBIDDEN`

### AUTH-14 Signer impersonates non-existent agent

**Setup:** Create `agent_alice` and `agent_bob` with known keypairs.
**Action:** Alice signs a JWS with `from_agent_id: "a-nonexistent-uuid"`.
`POST /feedback` with `jws(alice, {action: "submit_feedback", from_agent_id: "a-nonexistent-uuid", to_agent_id: bob.id, ...})`.
**Expected:**
- `403 Forbidden`
- `error = FORBIDDEN`

---

## Category 4: GET Endpoints Remain Public

### PUB-01 GET /feedback/{feedback_id} requires no authentication

**Setup:** Submit feedback via authenticated JWS. Reveal it (submit counterpart). Capture `feedback_id`.
**Action:** `GET /feedback/{feedback_id}` with no Authorization header and no token.
**Expected:**
- `200 OK`
- Full feedback record returned

### PUB-02 GET /feedback/task/{task_id} requires no authentication

**Setup:** Submit and reveal feedback for a task.
**Action:** `GET /feedback/task/{task_id}` with no Authorization header and no token.
**Expected:**
- `200 OK`
- `feedback` array contains revealed entries

### PUB-03 GET /feedback/agent/{agent_id} requires no authentication

**Setup:** Submit and reveal feedback about an agent.
**Action:** `GET /feedback/agent/{agent_id}` with no Authorization header and no token.
**Expected:**
- `200 OK`
- `feedback` array contains revealed entries

### PUB-04 GET /health requires no authentication

**Action:** `GET /health` with no Authorization header and no token.
**Expected:**
- `200 OK`
- `status = "ok"`

---

## Category 5: Error Precedence

These tests verify that errors are returned in the correct order when multiple error conditions are present simultaneously.

### PREC-01 Content-Type checked before token validation

**Action:** `POST /feedback` with `Content-Type: text/plain` and body `{"token": "invalid"}`.
**Expected:**
- `415 Unsupported Media Type`
- `error = UNSUPPORTED_MEDIA_TYPE`
- (NOT `400 INVALID_JWS`)

### PREC-02 Body size checked before token validation

**Action:** `POST /feedback` with `Content-Type: application/json` and a ~2MB body.
**Expected:**
- `413 Payload Too Large`
- `error = PAYLOAD_TOO_LARGE`
- (NOT `400 INVALID_JWS`)

### PREC-03 JSON parsing checked before token validation

**Action:** `POST /feedback` with `Content-Type: application/json` and body `{not json`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_JSON`
- (NOT `400 INVALID_JWS`)

### PREC-04 Token validation checked before payload validation

**Action:** `POST /feedback` with `{"token": 12345}` (not a string).
**Expected:**
- `400 Bad Request`
- `error = INVALID_JWS`
- (NOT `400 INVALID_PAYLOAD`)

### PREC-05 Payload `action` checked before signer matching

**Setup:** Create `agent_alice` and `agent_bob` with known keypairs.
**Action:** Alice signs a JWS with `{action: "wrong_action", from_agent_id: bob.id, ...}` (wrong action AND signer mismatch).
**Expected:**
- `400 Bad Request`
- `error = INVALID_PAYLOAD`
- (NOT `403 FORBIDDEN`)

### PREC-06 Signer matching checked before feedback field validation

**Setup:** Create `agent_alice`, `agent_bob`, and `agent_carol` with known keypairs.
**Action:** Alice signs a JWS with `{action: "submit_feedback", from_agent_id: carol.id, rating: "invalid_value", ...}` (signer mismatch AND invalid rating).
**Expected:**
- `403 Forbidden`
- `error = FORBIDDEN`
- (NOT `400 INVALID_RATING`)

---

## Category 6: Existing Validations Through JWS

These tests verify that existing feedback validation rules still apply when the feedback data is delivered inside a JWS payload instead of a plain JSON body.

### VJWS-01 Missing feedback fields in JWS payload

**Setup:** Create `agent_alice` and `agent_bob` with known keypairs.
**Action:** Alice signs a JWS with `{action: "submit_feedback", from_agent_id: alice.id}` — missing `to_agent_id`, `task_id`, `category`, `rating`.
**Expected:**
- `400 Bad Request`
- `error = MISSING_FIELD`

### VJWS-02 Invalid rating in JWS payload

**Setup:** Create `agent_alice` and `agent_bob` with known keypairs.
**Action:** Alice signs a JWS with `{action: "submit_feedback", ..., rating: "excellent"}`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_RATING`

### VJWS-03 Invalid category in JWS payload

**Setup:** Create `agent_alice` and `agent_bob` with known keypairs.
**Action:** Alice signs a JWS with `{action: "submit_feedback", ..., category: "timeliness"}`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_CATEGORY`

### VJWS-04 Self-feedback in JWS payload

**Setup:** Create `agent_alice` with a known keypair.
**Action:** Alice signs a JWS with `{action: "submit_feedback", from_agent_id: alice.id, to_agent_id: alice.id, ...}`.
**Expected:**
- `400 Bad Request`
- `error = SELF_FEEDBACK`

### VJWS-05 Comment too long in JWS payload

**Setup:** Create `agent_alice` and `agent_bob` with known keypairs.
**Action:** Alice signs a JWS with a comment of 257 characters (one over the configured limit).
**Expected:**
- `400 Bad Request`
- `error = COMMENT_TOO_LONG`

### VJWS-06 Duplicate feedback via JWS

**Setup:** Create `agent_alice` and `agent_bob` with known keypairs. Submit feedback via JWS for (task_1, alice→bob).
**Action:** Submit identical feedback via JWS again for (task_1, alice→bob).
**Expected:**
- `409 Conflict`
- `error = FEEDBACK_EXISTS`

### VJWS-07 Mutual reveal works through JWS submission

**Setup:** Create `agent_alice` and `agent_bob` with known keypairs.
**Action:**
1. Alice submits feedback for (task_1, alice→bob) via JWS — returns `visible: false`
2. Bob submits feedback for (task_1, bob→alice) via JWS — returns `visible: true`
3. `GET /feedback/task/{task_1}` returns 2 visible entries
**Expected:**
- Step 1: `201`, `visible = false`
- Step 2: `201`, `visible = true`
- Step 3: `200`, `feedback` array has exactly 2 entries, both `visible = true`

### VJWS-08 Extra fields in JWS payload are ignored

**Setup:** Create `agent_alice` and `agent_bob` with known keypairs.
**Action:** Alice signs a JWS with valid feedback fields plus `feedback_id`, `submitted_at`, `visible`, `is_admin`.
**Expected:**
- `201 Created`
- Service-generated `feedback_id` and `submitted_at` are used
- Extra fields are ignored

### VJWS-09 Concurrent duplicate feedback race via JWS is safe

**Setup:** Create `agent_alice` and `agent_bob` with known keypairs. Prepare two identical JWS-wrapped feedback requests for (task_1, alice→bob).
**Action:** Send both requests simultaneously (parallel).
**Expected:**
- Exactly one `201 Created`
- Exactly one `409 Conflict` with `FEEDBACK_EXISTS`

---

## Category 7: Cross-Cutting Security Assertions

### SEC-AUTH-01 Error envelope consistency for auth errors

**Action:** Trigger each auth error code at least once (`INVALID_JWS`, `INVALID_PAYLOAD`, `FORBIDDEN`).
**Expected:** All responses have exactly:
- top-level `error` (string)
- top-level `message` (string)
- top-level `details` (object)

### SEC-AUTH-02 No internal error leakage in auth failures

**Action:** Trigger `INVALID_JWS`, `FORBIDDEN` errors.
**Expected:** `message` never includes stack traces, cryptographic details, private key material, or internal diagnostics.

### SEC-AUTH-03 JWS token reuse across actions is rejected

**Setup:** Create `agent_alice` and `agent_bob` with known keypairs. Construct a valid JWS with `action: "escrow_lock"` (central-bank action).
**Action:** `POST /feedback` with the escrow lock JWS.
**Expected:**
- `400 Bad Request`
- `error = INVALID_PAYLOAD`
- A token intended for another service cannot be used to submit feedback

---

## Release Gate Checklist

Authentication is release-ready only if:

1. All tests in this document pass.
2. All tests in `reputation-service-tests.md` pass when executed with JWS-wrapped requests.
3. No endpoint returns `500` in any test scenario.
4. All failing responses conform to the required error envelope.
5. Local certificate verification via PlatformAgent never causes the Reputation service to crash — invalid certificates return `403` gracefully.

---

## Coverage Summary

| Category | IDs | Count |
|----------|-----|-------|
| JWS Token Validation | AUTH-01 to AUTH-08 | 8 |
| JWS Payload Validation | AUTH-09 to AUTH-11 | 3 |
| Authorization (Signer Matching) | AUTH-12 to AUTH-14 | 3 |
| GET Endpoints Remain Public | PUB-01 to PUB-04 | 4 |
| Error Precedence | PREC-01 to PREC-06 | 6 |
| Existing Validations Through JWS | VJWS-01 to VJWS-09 | 9 |
| Cross-Cutting Security | SEC-AUTH-01 to SEC-AUTH-03 | 3 |
| **Total** | | **36** |

| Endpoint | Covered By |
|----------|------------|
| `POST /feedback` | AUTH-01 to AUTH-14, PREC-01 to PREC-06, VJWS-01 to VJWS-09, SEC-AUTH-01 to SEC-AUTH-03 |
| `GET /feedback/{feedback_id}` | PUB-01 |
| `GET /feedback/task/{task_id}` | PUB-02, VJWS-07 |
| `GET /feedback/agent/{agent_id}` | PUB-03 |
| `GET /health` | PUB-04 |
