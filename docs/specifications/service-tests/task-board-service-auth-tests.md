# Task Board Service — Authentication Test Specification

## Purpose

This document is the release-gate test specification for JWS-based authentication on the Task Board service. It covers authentication and authorization patterns that are **not already tested** in `task-board-service-tests.md`.

The main test spec (`task-board-service-tests.md`) already covers:

- Wrong action values across all endpoints (TC-08, CAN-06, BID-08, BA-06, SUB-07, APP-06, DIS-08, RUL-10)
- Signer mismatch / impersonation (TC-10, CAN-03, BID-07, BA-07, AU-11, SUB-02)
- Tampered JWS (TC-20)
- Empty / malformed JWS token (TC-07, CAN-08, BID-15)
- Missing token field (TC-04, TC-05, TC-06)
- Non-platform signer on ruling (RUL-02)
- Cross-action token replay (SEC-07)
- Full error precedence chain (PREC-01 to PREC-10)

This document adds tests for gaps: null tokens, non-string token types, missing action field, non-object JSON body, Bearer token validation, and cross-service token replay.

It is intentionally strict and unambiguous:

- Every negative case has one required status code and one required error code.
- Every failing response must use the standard error envelope.
- Any behavior not listed here is out of scope for release sign-off.

---

## Prerequisites

These tests require:

1. A `PlatformAgent` instantiated with valid Ed25519 keys for local certificate verification
2. Test agents with known Ed25519 keypairs (public + private keys)
3. The platform agent's `agent_id` matches `settings.platform.agent_id`
4. A running Central Bank service (port 8002) — or a mock for escrow operations
5. For Bearer token tests: a task in the appropriate status (OPEN for sealed bid listing, ACCEPTED/EXECUTION for asset upload)

---

## Required API Error Contract (Auth Error Codes)

These error codes apply to authentication failures. Existing error codes from `task-board-service-tests.md` remain unchanged.

| Status | Error Code                      | Required When                                                |
|--------|---------------------------------|--------------------------------------------------------------|
| 400    | `invalid_jws`                  | Non-object request body or signed payload, malformed compact/header form, decoded signature length other than 64 bytes, exact header profile, or lifetime bound is invalid |
| 400    | `invalid_json`                 | Request body is not valid JSON |
| 400    | `invalid_payload`              | Action is missing/non-string, authenticated audience/resource/action mismatches, or required domain fields are missing |
| 400    | `token_mismatch`               | `task_id` or `amount`/`reward` mismatch between `task_token` and `escrow_token` during task creation |
| 401    | `token_expired`                | Authentic token satisfies `now >= exp + clock_skew_seconds` |
| 401    | `token_not_yet_valid`           | Authentic token satisfies `iat > now + clock_skew_seconds` |
| 403    | `forbidden`                    | Identity-backed or local signature verification fails, authenticated signer does not match the required role, or authenticated local `kid` is not the platform identity |
| 502    | `identity_service_unavailable` | Agent-token verification cannot reach Identity or receives a malformed successful response |
| 502    | `central_bank_unavailable`     | Central Bank service is unreachable or returns an unexpected response during escrow operations |

All failing responses must use the standard error envelope:

```json
{
  "error": "error_code",
  "message": "Human-readable description",
  "details": {}
}
```

---

## Test Data Conventions

- `platform_agent` is the `PlatformAgent` instantiated locally with a known Ed25519 keypair. Its `agent_id` matches `settings.platform.agent_id`. It performs local certificate verification via `validate_certificate()`.
- `agent_poster`, `agent_worker`, `agent_bidder` are test agents with known Ed25519 public/private keypairs.
- `rogue_agent` is a non-platform agent with a valid Ed25519 keypair.
- `jws(signer, payload)` is concise scenario notation for a real Ed25519 compact JWS with exact protected header `{alg,typ,kid,iat,exp,aud="task-board",resource}` and the existing action/domain payload. The fixture call passes the concrete resource explicitly; the notation does not authorize a default.
- `raw_jws(header, payload, key)` creates real signatures only for malformed-profile vectors. Domain-only mocks must return a complete typed `VerifiedJws(header, payload)` and do not prove authentication.
- `tampered_jws(signer, payload)` denotes a JWS where the payload has been altered after signing (signature mismatch).
- Agent IDs use the format `a-<uuid4>`.
- Task IDs use the format `t-<uuid4>`.
- Bid IDs use the format `bid-<uuid4>`.
- Asset IDs use the format `asset-<uuid4>`.
- A "valid create_task JWS" means: `jws(poster, {action: "create_task", poster_id: poster.agent_id, title: "...", spec: "...", reward: 100, ...})`.
- For tests requiring a task in a specific status, use the standard setup sequence: create task → submit bid → accept bid (→ submit deliverable → etc.) as needed.

---

## Category 1: Body Token Edge Cases

These tests cover token format validation patterns not already tested in the main spec.

### AUTH-01 Null `token` in POST body (task creation)

**Action:** `POST /tasks` with body `{"task_token": null, "escrow_token": null}`.
**Expected:**
- `400 Bad Request`
- `error = invalid_jws`

### AUTH-02 Null `token` in POST body (single-token endpoint)

**Action:** `POST /tasks/{task_id}/bids` with body `{"token": null}`.
**Expected:**
- `400 Bad Request`
- `error = invalid_jws`

### AUTH-03 Non-string `token` in POST body (integer)

**Action:** `POST /tasks/{task_id}/cancel` with body `{"token": 12345}`.
**Expected:**
- `400 Bad Request`
- `error = invalid_jws`

### AUTH-04 Non-string `token` in POST body (array)

**Action:** `POST /tasks/{task_id}/bids` with body `{"token": ["eyJ..."]}`.
**Expected:**
- `400 Bad Request`
- `error = invalid_jws`

### AUTH-05 Non-string `token` in POST body (object)

**Action:** `POST /tasks/{task_id}/submit` with body `{"token": {"jws": "eyJ..."}}`.
**Expected:**
- `400 Bad Request`
- `error = invalid_jws`

### AUTH-06 Non-string `token` in POST body (boolean)

**Action:** `POST /tasks/{task_id}/approve` with body `{"token": true}`.
**Expected:**
- `400 Bad Request`
- `error = invalid_jws`

### AUTH-07 Missing `action` field in JWS payload

**Setup:** Create `agent_poster` with known keypair.
**Action:** `POST /tasks/{task_id}/cancel` with `jws(poster, {poster_id: poster.agent_id, task_id: "t-xxx"})` — payload has no `action` field.
**Expected:**
- `400 Bad Request`
- `error = invalid_payload`

### AUTH-08 Missing `action` field on platform endpoint

**Setup:** Instantiate `PlatformAgent` with valid keys.
**Action:** `POST /tasks/{task_id}/ruling` with `jws(platform_agent, {task_id: "t-xxx", worker_pct: 50, ruling_summary: "..."})` — payload has no `action` field.
**Expected:**
- `400 Bad Request`
- `error = invalid_payload`

### AUTH-09 Non-object JSON body (array) on single-token endpoint

**Action:** `POST /tasks/{task_id}/cancel` with `Content-Type: application/json` and body `[{"token": "eyJ..."}]`.
**Expected:**
- `400 Bad Request`
- `error = invalid_jws`

### AUTH-10 Non-object JSON body (string) on single-token endpoint

**Action:** `POST /tasks/{task_id}/bids` with `Content-Type: application/json` and body `"just a string"`.
**Expected:**
- `400 Bad Request`
- `error = invalid_jws`

### AUTH-11 Non-object JSON body (array) on dual-token endpoint

**Action:** `POST /tasks` with `Content-Type: application/json` and body `[{"task_token": "eyJ...", "escrow_token": "eyJ..."}]`.
**Expected:**
- `400 Bad Request`
- `error = invalid_jws`

### AUTH-12 Null `task_token` with valid `escrow_token` on task creation

**Setup:** Create `agent_poster` with known keypair. Construct a valid escrow token.
**Action:** `POST /tasks` with body `{"task_token": null, "escrow_token": "<valid_escrow_jws>"}`.
**Expected:**
- `400 Bad Request`
- `error = invalid_jws`

### AUTH-13 Valid `task_token` with null `escrow_token` on task creation

**Setup:** Create `agent_poster` with known keypair. Construct a valid task token.
**Action:** `POST /tasks` with body `{"task_token": "<valid_task_jws>", "escrow_token": null}`.
**Expected:**
- `400 Bad Request`
- `error = invalid_jws`

---

## Category 2: Bearer Token Validation

The Task Board uses Bearer tokens for two endpoints:
- `GET /tasks/{id}/bids` — requires poster auth during OPEN status (sealed bids)
- `POST /tasks/{id}/assets` — requires worker auth via `Authorization: Bearer <jws>`

### BEARER-01 Valid Bearer token on GET /tasks/{id}/bids (OPEN status)

**Setup:** Create `agent_poster` with known keypair. Create a task (status transitions to BIDDING/OPEN). Capture `task_id`.
**Action:** `GET /tasks/{task_id}/bids` with header `Authorization: Bearer <jws(poster, {action: "list_bids", task_id: task_id})>`.
**Expected:**
- `200 OK`
- Body includes `bids` array

### BEARER-02 Valid Bearer token on POST /tasks/{id}/assets

**Setup:** Create `agent_poster` and `agent_worker` with known keypairs. Create task, submit bid, accept bid (task in EXECUTION). Capture `task_id`.
**Action:** `POST /tasks/{task_id}/assets` with header `Authorization: Bearer <jws(worker, {action: "upload_asset", task_id: task_id})>` and multipart file body.
**Expected:**
- `201 Created`
- Body includes `asset_id`, `filename`, `content_hash`, `uploaded_at`

### BEARER-03 Missing Authorization header on sealed bid listing

**Setup:** Create `agent_poster` with known keypair. Create a task in OPEN status. Capture `task_id`.
**Action:** `GET /tasks/{task_id}/bids` with no `Authorization` header.
**Expected:**
- `400 Bad Request`
- `error = invalid_jws`

### BEARER-04 Authorization header without "Bearer " prefix

**Setup:** Create `agent_poster` with known keypair. Create a task in OPEN status. Capture `task_id`.
**Action:** `GET /tasks/{task_id}/bids` with header `Authorization: Token <jws(poster, {action: "list_bids", task_id: task_id})>`.
**Expected:**
- `400 Bad Request`
- `error = invalid_jws`

### BEARER-05 Empty Bearer token

**Setup:** Create a task in OPEN status. Capture `task_id`.
**Action:** `GET /tasks/{task_id}/bids` with header `Authorization: Bearer `.
**Expected:**
- `400 Bad Request`
- `error = invalid_jws`

### BEARER-06 Malformed Bearer token (not three-part JWS)

**Setup:** Create a task in OPEN status. Capture `task_id`.
**Action:** `GET /tasks/{task_id}/bids` with header `Authorization: Bearer not-a-jws`.
**Expected:**
- `400 Bad Request`
- `error = invalid_jws`

### BEARER-07 Tampered Bearer token (signature mismatch)

**Setup:** Create `agent_poster` with known keypair. Create a task in OPEN status. Capture `task_id`. Construct a valid Bearer JWS, then alter the payload portion after signing.
**Action:** `GET /tasks/{task_id}/bids` with header `Authorization: Bearer <tampered_jws(poster, {action: "list_bids", task_id: task_id})>`.
**Expected:**
- `403 Forbidden`
- `error = forbidden`

### BEARER-08 Wrong `action` in Bearer JWS (sealed bid listing)

**Setup:** Create `agent_poster` with known keypair. Create a task in OPEN status. Capture `task_id`.
**Action:** `GET /tasks/{task_id}/bids` with header `Authorization: Bearer <jws(poster, {action: "create_task", task_id: task_id})>` — action is `"create_task"` instead of `"list_bids"`.
**Expected:**
- `400 Bad Request`
- `error = invalid_payload`

### BEARER-09 Wrong `action` in Bearer JWS (asset upload)

**Setup:** Create `agent_poster` and `agent_worker` with known keypairs. Set up task in EXECUTION. Capture `task_id`.
**Action:** `POST /tasks/{task_id}/assets` with header `Authorization: Bearer <jws(worker, {action: "submit_bid", task_id: task_id})>` — action is `"submit_bid"` instead of `"upload_asset"`.
**Expected:**
- `400 Bad Request`
- `error = invalid_payload`

### BEARER-10 Payload `task_id` mismatch with URL path (sealed bid listing)

**Setup:** Create `agent_poster` with known keypair. Create a task in OPEN status. Capture `task_id`.
**Action:** `GET /tasks/{task_id}/bids` with header `Authorization: Bearer <jws(poster, {action: "list_bids", task_id: "t-different-uuid"})>` — payload `task_id` does not match URL.
**Expected:**
- `400 Bad Request`
- `error = invalid_payload`

### BEARER-11 Payload `task_id` mismatch with URL path (asset upload)

**Setup:** Create `agent_poster` and `agent_worker` with known keypairs. Set up task in EXECUTION. Capture `task_id`.
**Action:** `POST /tasks/{task_id}/assets` with header `Authorization: Bearer <jws(worker, {action: "upload_asset", task_id: "t-different-uuid"})>` — payload `task_id` does not match URL.
**Expected:**
- `400 Bad Request`
- `error = invalid_payload`

### BEARER-12 Non-poster accessing sealed bids

**Setup:** Create `agent_poster` and `agent_bidder` with known keypairs. Create a task in OPEN status. Capture `task_id`.
**Action:** `GET /tasks/{task_id}/bids` with header `Authorization: Bearer <jws(bidder, {action: "list_bids", task_id: task_id})>` — bidder is not the poster.
**Expected:**
- `403 Forbidden`
- `error = forbidden`

### BEARER-13 Non-worker uploading asset

**Setup:** Create `agent_poster` and `agent_worker` with known keypairs. Set up task in EXECUTION with `agent_worker` as the assigned worker. Create `rogue_agent` with known keypair.
**Action:** `POST /tasks/{task_id}/assets` with header `Authorization: Bearer <jws(rogue_agent, {action: "upload_asset", task_id: task_id})>` — rogue agent is not the assigned worker.
**Expected:**
- `403 Forbidden`
- `error = forbidden`

### MP03-TB-01 Strict header types and lifetime bounds

**Action:** Send real-signed agent and platform tokens with a non-object signed payload, missing/extra header fields, wrong types including boolean `iat`/`exp`, decoded signature length other than 64 bytes, `exp <= iat`, and lifetime above the configured maximum.
**Expected:** Each returns `400 invalid_jws`; no task, bid, asset, or ruling effect occurs.

### MP03-TB-02 Exact future and expiry boundaries

**Action:** At controlled time, exercise `iat = now + skew`, `iat = now + skew + 1`, `now = exp + skew - 1`, and `now = exp + skew` on an Identity-backed agent route and the real local platform ruling adapter.
**Expected:** Boundary-within-skew cases succeed; the next future second is `401 token_not_yet_valid`; exact expiry is `401 token_expired`.

### MP03-TB-03 Audience, method/resource, and action

**Action:** Send authentic tokens with wrong audience, wrong HTTP method, wrong concrete path or route id, missing/wrong-type/wrong-value action, and payload `aud`/`resource` fields that disagree with the protected header.
**Expected:** Each returns `400 invalid_payload`; zero domain effect.

### MP03-TB-04 Correct platform signature with wrong `kid`

**Action:** Sign a ruling token with the platform key while placing another registered identity in protected `kid`.
**Expected:** `403 forbidden`; no ruling is recorded.

### MP03-TB-05 Malformed Identity success is unavailable

**Action:** On an agent route, make Identity return HTTP 200/`valid:true` with missing `protected_header`, non-object payload, `agent_id != protected_header.kid`, a `true`/`1`/`1.0` substitution, or success for an invalid originally requested token.
**Expected:** `502 identity_service_unavailable`; no authenticated payload is fabricated and no task effect occurs.

### MP03-TB-06 Shared, Identity, and local-adapter conformance

**Action:** Run the same actual-signature success and failure vectors through shared verification, real Identity HTTP verification where applicable, and Task Board's real local platform adapter.
**Expected:** Success returns the same typed `VerifiedJws`; failures use the uniform mapping. Local platform vectors perform no Identity HTTP request.

### MP03-TB-07 Authentication configuration fails closed

**Action:** Load settings with missing Identity configuration, missing/zero/negative signer TTL or admission maximum, or missing/negative skew; then load explicit initial `300/300/30` values.
**Expected:** Invalid settings fail at startup. The valid settings preserve Identity-backed agent verification and no-HTTP local platform verification.

---

## Category 3: Public Endpoints

### PUB-01 GET /tasks requires no authentication

**Action:** `GET /tasks` with no `Authorization` header and no token.
**Expected:**
- `200 OK`
- Body includes `tasks` array

### PUB-02 GET /tasks/{task_id} requires no authentication

**Setup:** Create a task.
**Action:** `GET /tasks/{task_id}` with no `Authorization` header and no token.
**Expected:**
- `200 OK`
- Body includes task fields

### PUB-03 GET /tasks/{task_id}/bids requires no authentication when task is NOT in OPEN status

**Setup:** Create a task, submit bid, accept bid (task moves past OPEN). Capture `task_id`.
**Action:** `GET /tasks/{task_id}/bids` with no `Authorization` header and no token.
**Expected:**
- `200 OK`
- Body includes `bids` array

### PUB-04 GET /tasks/{task_id}/assets requires no authentication

**Setup:** Create a task. Capture `task_id`.
**Action:** `GET /tasks/{task_id}/assets` with no `Authorization` header and no token.
**Expected:**
- `200 OK`
- Body includes `assets` array

### PUB-05 GET /tasks/{task_id}/assets/{asset_id} requires no authentication

**Setup:** Create a task, upload an asset. Capture `task_id` and `asset_id`.
**Action:** `GET /tasks/{task_id}/assets/{asset_id}` with no `Authorization` header and no token.
**Expected:**
- `200 OK`
- Body includes `asset_id`, `filename`, `content_hash`, `uploaded_at`

### PUB-06 GET /health requires no authentication

**Action:** `GET /health` with no `Authorization` header and no token.
**Expected:**
- `200 OK`
- `status = "ok"`

---

## Category 4: Cross-Service Token Replay

### REPLAY-01 Central Bank escrow_lock token rejected on Task Board

**Setup:** Create `agent_poster` with known keypair.
**Action:** `POST /tasks/{task_id}/cancel` with `jws(poster, {action: "escrow_lock", agent_id: poster.agent_id, amount: 100, task_id: "t-xxx"})` — a Central Bank action used on a Task Board endpoint.
**Expected:**
- `400 Bad Request`
- `error = invalid_payload`

### REPLAY-02 Court file_dispute token rejected on Task Board

**Setup:** Instantiate `PlatformAgent` with valid keys.
**Action:** `POST /tasks/{task_id}/ruling` with `jws(platform_agent, {action: "file_dispute", task_id: "t-xxx", claimant_id: "a-xxx", respondent_id: "a-xxx", claim: "...", escrow_id: "esc-xxx"})` — a Court action used on a Task Board endpoint.
**Expected:**
- `400 Bad Request`
- `error = invalid_payload`

### REPLAY-03 Reputation submit_feedback token rejected on Task Board

**Setup:** Create `agent_poster` with known keypair.
**Action:** `POST /tasks/{task_id}/approve` with `jws(poster, {action: "submit_feedback", task_id: "t-xxx", from_agent_id: poster.agent_id, to_agent_id: "a-xxx", category: "spec_quality", rating: "satisfied"})` — a Reputation action used on a Task Board endpoint.
**Expected:**
- `400 Bad Request`
- `error = invalid_payload`

---

## Category 5: Cross-Cutting Security Assertions

### SEC-AUTH-01 Error envelope consistency for auth errors

**Action:** Trigger each auth error code at least once (`invalid_jws`, `invalid_payload`, `forbidden`).
**Expected:** All responses have exactly:
- top-level `error` (string)
- top-level `message` (string)
- top-level `details` (object)

### SEC-AUTH-02 No internal error leakage in auth failures

**Action:** Trigger `invalid_jws` and `forbidden` errors.
**Expected:** `message` never includes stack traces, cryptographic details, private key material, internal file paths, or internal diagnostics.

### SEC-AUTH-03 JWS token reuse across services is rejected

**Setup:** Instantiate `PlatformAgent` with valid keys. Construct a valid JWS with `action: "create_account"` (Central Bank action).
**Action:** `POST /tasks` with `{"task_token": "<create_account_jws>", "escrow_token": "<create_account_jws>"}`.
**Expected:**
- `400 Bad Request`
- `error = invalid_payload`
- A token intended for another service cannot be used on the Task Board

---

## Release Gate Checklist

Authentication is release-ready only if:

1. All tests in this document pass.
2. All tests in `task-board-service-tests.md` pass (auth is embedded in main tests).
3. No endpoint returns `500` in any test scenario.
4. All failing responses conform to the required error envelope.
5. Local certificate verification via `PlatformAgent.validate_certificate()` never causes the Task Board service to crash on invalid input.

---

## Coverage Summary

| Category | IDs | Count |
|----------|-----|-------|
| Body Token Edge Cases | AUTH-01 to AUTH-13 | 13 |
| Bearer Token Validation | BEARER-01 to BEARER-13 | 13 |
| MP-03 strict-profile conformance | MP03-TB-01 to MP03-TB-07 | 7 |
| Public Endpoints | PUB-01 to PUB-06 | 6 |
| Cross-Service Token Replay | REPLAY-01 to REPLAY-03 | 3 |
| Cross-Cutting Security | SEC-AUTH-01 to SEC-AUTH-03 | 3 |
| **Total** | | **45** |

| Endpoint | Covered By (this document) | Also Covered By (main spec) |
|----------|----------------------------|------------------------------|
| `POST /tasks` | AUTH-01, AUTH-11, AUTH-12, AUTH-13, SEC-AUTH-03 | TC-01 to TC-28, PREC-01 to PREC-10 |
| `POST /tasks/{id}/cancel` | AUTH-03, AUTH-07, AUTH-09, REPLAY-01 | CAN-01 to CAN-09 |
| `POST /tasks/{id}/bids` | AUTH-02, AUTH-04, AUTH-10 | BID-01 to BID-15 |
| `POST /tasks/{id}/bids/{bid_id}/accept` | — | BA-01 to BA-10 |
| `GET /tasks/{id}/bids` (OPEN) | BEARER-01, BEARER-03 to BEARER-08, BEARER-10, BEARER-12 | BL-01 to BL-08 |
| `POST /tasks/{id}/assets` | BEARER-02, BEARER-09, BEARER-11, BEARER-13 | AU-01 to AU-11 |
| `POST /tasks/{id}/submit` | AUTH-05 | SUB-01 to SUB-09, SEC-07 |
| `POST /tasks/{id}/approve` | AUTH-06, REPLAY-03 | APP-01 to APP-09 |
| `POST /tasks/{id}/dispute` | — | DIS-01 to DIS-10 |
| `POST /tasks/{id}/ruling` | AUTH-08, REPLAY-02 | RUL-01 to RUL-13 |
| `GET /tasks` | PUB-01 | — |
| `GET /tasks/{id}` | PUB-02 | — |
| `GET /tasks/{id}/bids` (non-OPEN) | PUB-03 | — |
| `GET /tasks/{id}/assets` | PUB-04 | — |
| `GET /tasks/{id}/assets/{asset_id}` | PUB-05 | — |
| `GET /health` | PUB-06 | HLTH-01 to HLTH-04 |
