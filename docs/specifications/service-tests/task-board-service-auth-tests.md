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
- Identity service down (TC-22)
- Cross-action token replay (SEC-07)
- Full error precedence chain (PREC-01 to PREC-10)

This document adds tests for gaps: null tokens, non-string token types, missing action field, non-object JSON body, Bearer token validation, Identity service timeout/unexpected response, and cross-service token replay.

It is intentionally strict and unambiguous:

- Every negative case has one required status code and one required error code.
- Every failing response must use the standard error envelope.
- Any behavior not listed here is out of scope for release sign-off.

---

## Prerequisites

These tests require:

1. A running Identity service (port 8001) — or a mock that implements `POST /agents/verify-jws`
2. Pre-registered agents with known Ed25519 keypairs (public + private keys)
3. A platform agent registered in the Identity service, whose `agent_id` matches `settings.platform.agent_id`
4. A running Central Bank service (port 8002) — or a mock for escrow operations
5. For Bearer token tests: a task in the appropriate status (OPEN for sealed bid listing, ACCEPTED/EXECUTION for asset upload)

---

## Required API Error Contract (Auth Error Codes)

These error codes apply to authentication failures. Existing error codes from `task-board-service-tests.md` remain unchanged.

| Status | Error Code                      | Required When                                                |
|--------|---------------------------------|--------------------------------------------------------------|
| 400    | `INVALID_JWS`                  | `token` / `task_token` field is missing, null, non-string, empty, or malformed (not a three-part compact serialization); or `Authorization` header is missing, lacks the `Bearer ` prefix, or contains an empty/malformed token |
| 400    | `INVALID_JSON`                 | Request body is not valid JSON, or is valid JSON but not an object (e.g., array, string) |
| 400    | `INVALID_PAYLOAD`              | JWS payload is missing `action`, `action` does not match the expected value for the endpoint, or required payload fields are missing |
| 400    | `TOKEN_MISMATCH`               | `task_id` or `amount`/`reward` mismatch between `task_token` and `escrow_token` during task creation |
| 403    | `FORBIDDEN`                    | JWS signature verification failed (tampered, unregistered agent), signer does not match the required role (poster, worker, platform), or agent is not authorized for the operation |
| 502    | `IDENTITY_SERVICE_UNAVAILABLE` | Identity service is unreachable, times out, or returns an unexpected response |
| 502    | `CENTRAL_BANK_UNAVAILABLE`     | Central Bank service is unreachable or returns an unexpected response during escrow operations |

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

- `platform_agent` is the platform agent registered in the Identity service with a known Ed25519 keypair. Its `agent_id` matches `settings.platform.agent_id`.
- `agent_poster`, `agent_worker`, `agent_bidder` are agents pre-registered in the Identity service with known Ed25519 keypairs.
- `rogue_agent` is a non-platform agent with a valid Ed25519 keypair, registered in the Identity service.
- `jws(signer, payload)` denotes a JWS compact serialization (RFC 7515, EdDSA/Ed25519) with header `{"alg":"EdDSA","kid":"<signer.agent_id>"}`, the given JSON payload, and a valid Ed25519 signature.
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
- `error = INVALID_JWS`

### AUTH-02 Null `token` in POST body (single-token endpoint)

**Action:** `POST /tasks/{task_id}/bids` with body `{"token": null}`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_JWS`

### AUTH-03 Non-string `token` in POST body (integer)

**Action:** `POST /tasks/{task_id}/cancel` with body `{"token": 12345}`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_JWS`

### AUTH-04 Non-string `token` in POST body (array)

**Action:** `POST /tasks/{task_id}/bids` with body `{"token": ["eyJ..."]}`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_JWS`

### AUTH-05 Non-string `token` in POST body (object)

**Action:** `POST /tasks/{task_id}/submit` with body `{"token": {"jws": "eyJ..."}}`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_JWS`

### AUTH-06 Non-string `token` in POST body (boolean)

**Action:** `POST /tasks/{task_id}/approve` with body `{"token": true}`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_JWS`

### AUTH-07 Missing `action` field in JWS payload

**Setup:** Register `agent_poster`.
**Action:** `POST /tasks/{task_id}/cancel` with `jws(poster, {poster_id: poster.agent_id, task_id: "t-xxx"})` — payload has no `action` field.
**Expected:**
- `400 Bad Request`
- `error = INVALID_PAYLOAD`

### AUTH-08 Missing `action` field on platform endpoint

**Setup:** Register `platform_agent`.
**Action:** `POST /tasks/{task_id}/ruling` with `jws(platform_agent, {task_id: "t-xxx", worker_pct: 50, ruling_summary: "..."})` — payload has no `action` field.
**Expected:**
- `400 Bad Request`
- `error = INVALID_PAYLOAD`

### AUTH-09 Non-object JSON body (array) on single-token endpoint

**Action:** `POST /tasks/{task_id}/cancel` with `Content-Type: application/json` and body `[{"token": "eyJ..."}]`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_JSON`

### AUTH-10 Non-object JSON body (string) on single-token endpoint

**Action:** `POST /tasks/{task_id}/bids` with `Content-Type: application/json` and body `"just a string"`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_JSON`

### AUTH-11 Non-object JSON body (array) on dual-token endpoint

**Action:** `POST /tasks` with `Content-Type: application/json` and body `[{"task_token": "eyJ...", "escrow_token": "eyJ..."}]`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_JSON`

### AUTH-12 Null `task_token` with valid `escrow_token` on task creation

**Setup:** Register `agent_poster`. Construct a valid escrow token.
**Action:** `POST /tasks` with body `{"task_token": null, "escrow_token": "<valid_escrow_jws>"}`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_JWS`

### AUTH-13 Valid `task_token` with null `escrow_token` on task creation

**Setup:** Register `agent_poster`. Construct a valid task token.
**Action:** `POST /tasks` with body `{"task_token": "<valid_task_jws>", "escrow_token": null}`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_JWS`

---

## Category 2: Bearer Token Validation

The Task Board uses Bearer tokens for two endpoints:
- `GET /tasks/{id}/bids` — requires poster auth during OPEN status (sealed bids)
- `POST /tasks/{id}/assets` — requires worker auth via `Authorization: Bearer <jws>`

### BEARER-01 Valid Bearer token on GET /tasks/{id}/bids (OPEN status)

**Setup:** Register `agent_poster`. Create a task (status transitions to BIDDING/OPEN). Capture `task_id`.
**Action:** `GET /tasks/{task_id}/bids` with header `Authorization: Bearer <jws(poster, {action: "list_bids", task_id: task_id})>`.
**Expected:**
- `200 OK`
- Body includes `bids` array

### BEARER-02 Valid Bearer token on POST /tasks/{id}/assets

**Setup:** Register `agent_poster` and `agent_worker`. Create task, submit bid, accept bid (task in EXECUTION). Capture `task_id`.
**Action:** `POST /tasks/{task_id}/assets` with header `Authorization: Bearer <jws(worker, {action: "upload_asset", task_id: task_id})>` and multipart file body.
**Expected:**
- `201 Created`
- Body includes `asset_id`, `filename`, `content_hash`, `uploaded_at`

### BEARER-03 Missing Authorization header on sealed bid listing

**Setup:** Register `agent_poster`. Create a task in OPEN status. Capture `task_id`.
**Action:** `GET /tasks/{task_id}/bids` with no `Authorization` header.
**Expected:**
- `400 Bad Request`
- `error = INVALID_JWS`

### BEARER-04 Authorization header without "Bearer " prefix

**Setup:** Register `agent_poster`. Create a task in OPEN status. Capture `task_id`.
**Action:** `GET /tasks/{task_id}/bids` with header `Authorization: Token <jws(poster, {action: "list_bids", task_id: task_id})>`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_JWS`

### BEARER-05 Empty Bearer token

**Setup:** Create a task in OPEN status. Capture `task_id`.
**Action:** `GET /tasks/{task_id}/bids` with header `Authorization: Bearer `.
**Expected:**
- `400 Bad Request`
- `error = INVALID_JWS`

### BEARER-06 Malformed Bearer token (not three-part JWS)

**Setup:** Create a task in OPEN status. Capture `task_id`.
**Action:** `GET /tasks/{task_id}/bids` with header `Authorization: Bearer not-a-jws`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_JWS`

### BEARER-07 Tampered Bearer token (signature mismatch)

**Setup:** Register `agent_poster`. Create a task in OPEN status. Capture `task_id`. Construct a valid Bearer JWS, then alter the payload portion after signing.
**Action:** `GET /tasks/{task_id}/bids` with header `Authorization: Bearer <tampered_jws(poster, {action: "list_bids", task_id: task_id})>`.
**Expected:**
- `403 Forbidden`
- `error = FORBIDDEN`

### BEARER-08 Wrong `action` in Bearer JWS (sealed bid listing)

**Setup:** Register `agent_poster`. Create a task in OPEN status. Capture `task_id`.
**Action:** `GET /tasks/{task_id}/bids` with header `Authorization: Bearer <jws(poster, {action: "create_task", task_id: task_id})>` — action is `"create_task"` instead of `"list_bids"`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_PAYLOAD`

### BEARER-09 Wrong `action` in Bearer JWS (asset upload)

**Setup:** Register `agent_poster` and `agent_worker`. Set up task in EXECUTION. Capture `task_id`.
**Action:** `POST /tasks/{task_id}/assets` with header `Authorization: Bearer <jws(worker, {action: "submit_bid", task_id: task_id})>` — action is `"submit_bid"` instead of `"upload_asset"`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_PAYLOAD`

### BEARER-10 Payload `task_id` mismatch with URL path (sealed bid listing)

**Setup:** Register `agent_poster`. Create a task in OPEN status. Capture `task_id`.
**Action:** `GET /tasks/{task_id}/bids` with header `Authorization: Bearer <jws(poster, {action: "list_bids", task_id: "t-different-uuid"})>` — payload `task_id` does not match URL.
**Expected:**
- `400 Bad Request`
- `error = INVALID_PAYLOAD`

### BEARER-11 Payload `task_id` mismatch with URL path (asset upload)

**Setup:** Register `agent_poster` and `agent_worker`. Set up task in EXECUTION. Capture `task_id`.
**Action:** `POST /tasks/{task_id}/assets` with header `Authorization: Bearer <jws(worker, {action: "upload_asset", task_id: "t-different-uuid"})>` — payload `task_id` does not match URL.
**Expected:**
- `400 Bad Request`
- `error = INVALID_PAYLOAD`

### BEARER-12 Non-poster accessing sealed bids

**Setup:** Register `agent_poster` and `agent_bidder`. Create a task in OPEN status. Capture `task_id`.
**Action:** `GET /tasks/{task_id}/bids` with header `Authorization: Bearer <jws(bidder, {action: "list_bids", task_id: task_id})>` — bidder is not the poster.
**Expected:**
- `403 Forbidden`
- `error = FORBIDDEN`

### BEARER-13 Non-worker uploading asset

**Setup:** Register `agent_poster` and `agent_worker`. Set up task in EXECUTION with `agent_worker` as the assigned worker. Register `rogue_agent`.
**Action:** `POST /tasks/{task_id}/assets` with header `Authorization: Bearer <jws(rogue_agent, {action: "upload_asset", task_id: task_id})>` — rogue agent is not the assigned worker.
**Expected:**
- `403 Forbidden`
- `error = FORBIDDEN`

---

## Category 3: Identity Service Dependency

The main spec tests Identity service being unreachable (TC-22). These tests cover timeout and unexpected response scenarios.

### IDEP-01 Identity service times out

**Setup:** Start a mock HTTP server on the Identity service port that accepts connections but never responds (simulates timeout). Configure Task Board service to point to this mock.
**Action:** `POST /tasks/{task_id}/cancel` with a valid JWS token.
**Expected:**
- `502 Bad Gateway`
- `error = IDENTITY_SERVICE_UNAVAILABLE`

### IDEP-02 Identity service returns unexpected response (non-JSON)

**Setup:** Start a mock HTTP server on the Identity service port that returns `HTTP 500` with body `"Internal Server Error"` (non-JSON) for `POST /agents/verify-jws`. Configure Task Board service to point to this mock.
**Action:** `POST /tasks/{task_id}/bids` with a valid JWS token.
**Expected:**
- `502 Bad Gateway`
- `error = IDENTITY_SERVICE_UNAVAILABLE`

### IDEP-03 Identity service returns unexpected response on Bearer endpoint

**Setup:** Start a mock HTTP server on the Identity service port that returns `HTTP 500` with body `"Internal Server Error"` (non-JSON). Create a task in OPEN status.
**Action:** `GET /tasks/{task_id}/bids` with header `Authorization: Bearer <valid-looking-jws>`.
**Expected:**
- `502 Bad Gateway`
- `error = IDENTITY_SERVICE_UNAVAILABLE`

---

## Category 4: Public Endpoints

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

## Category 5: Cross-Service Token Replay

### REPLAY-01 Central Bank escrow_lock token rejected on Task Board

**Setup:** Register `agent_poster`.
**Action:** `POST /tasks/{task_id}/cancel` with `jws(poster, {action: "escrow_lock", agent_id: poster.agent_id, amount: 100, task_id: "t-xxx"})` — a Central Bank action used on a Task Board endpoint.
**Expected:**
- `400 Bad Request`
- `error = INVALID_PAYLOAD`

### REPLAY-02 Court file_dispute token rejected on Task Board

**Setup:** Register `platform_agent`.
**Action:** `POST /tasks/{task_id}/ruling` with `jws(platform_agent, {action: "file_dispute", task_id: "t-xxx", claimant_id: "a-xxx", respondent_id: "a-xxx", claim: "...", escrow_id: "esc-xxx"})` — a Court action used on a Task Board endpoint.
**Expected:**
- `400 Bad Request`
- `error = INVALID_PAYLOAD`

### REPLAY-03 Reputation submit_feedback token rejected on Task Board

**Setup:** Register `agent_poster`.
**Action:** `POST /tasks/{task_id}/approve` with `jws(poster, {action: "submit_feedback", task_id: "t-xxx", from_agent_id: poster.agent_id, to_agent_id: "a-xxx", category: "spec_quality", rating: "satisfied"})` — a Reputation action used on a Task Board endpoint.
**Expected:**
- `400 Bad Request`
- `error = INVALID_PAYLOAD`

---

## Category 6: Cross-Cutting Security Assertions

### SEC-AUTH-01 Error envelope consistency for auth errors

**Action:** Trigger each auth error code at least once (`INVALID_JWS`, `INVALID_PAYLOAD`, `FORBIDDEN`, `IDENTITY_SERVICE_UNAVAILABLE`).
**Expected:** All responses have exactly:
- top-level `error` (string)
- top-level `message` (string)
- top-level `details` (object)

### SEC-AUTH-02 No internal error leakage in auth failures

**Action:** Trigger `INVALID_JWS`, `FORBIDDEN`, and `IDENTITY_SERVICE_UNAVAILABLE` errors.
**Expected:** `message` never includes stack traces, Identity service URLs, cryptographic details, private key material, internal file paths, or internal diagnostics.

### SEC-AUTH-03 JWS token reuse across services is rejected

**Setup:** Register `platform_agent`. Construct a valid JWS with `action: "create_account"` (Central Bank action).
**Action:** `POST /tasks` with `{"task_token": "<create_account_jws>", "escrow_token": "<create_account_jws>"}`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_PAYLOAD`
- A token intended for another service cannot be used on the Task Board

---

## Release Gate Checklist

Authentication is release-ready only if:

1. All tests in this document pass.
2. All tests in `task-board-service-tests.md` pass (auth is embedded in main tests).
3. No endpoint returns `500` in any test scenario.
4. All failing responses conform to the required error envelope.
5. The Identity service being unavailable never causes the Task Board service to crash — it returns `502` gracefully.

---

## Coverage Summary

| Category | IDs | Count |
|----------|-----|-------|
| Body Token Edge Cases | AUTH-01 to AUTH-13 | 13 |
| Bearer Token Validation | BEARER-01 to BEARER-13 | 13 |
| Identity Service Dependency | IDEP-01 to IDEP-03 | 3 |
| Public Endpoints | PUB-01 to PUB-06 | 6 |
| Cross-Service Token Replay | REPLAY-01 to REPLAY-03 | 3 |
| Cross-Cutting Security | SEC-AUTH-01 to SEC-AUTH-03 | 3 |
| **Total** | | **41** |

| Endpoint | Covered By (this document) | Also Covered By (main spec) |
|----------|----------------------------|------------------------------|
| `POST /tasks` | AUTH-01, AUTH-11, AUTH-12, AUTH-13, SEC-AUTH-03 | TC-01 to TC-28, PREC-01 to PREC-10 |
| `POST /tasks/{id}/cancel` | AUTH-03, AUTH-07, AUTH-09, IDEP-01, REPLAY-01 | CAN-01 to CAN-09 |
| `POST /tasks/{id}/bids` | AUTH-02, AUTH-04, AUTH-10, IDEP-02 | BID-01 to BID-15 |
| `POST /tasks/{id}/bids/{bid_id}/accept` | — | BA-01 to BA-10 |
| `GET /tasks/{id}/bids` (OPEN) | BEARER-01, BEARER-03 to BEARER-08, BEARER-10, BEARER-12, IDEP-03 | BL-01 to BL-08 |
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
