# Central Bank Service — Authentication Test Specification

## Purpose

This document is the release-gate test specification for adding JWS-based authentication to the Central Bank service.

It is intentionally strict and unambiguous:

- Every negative case has one required status code and one required error code.
- Every failing response must use the standard error envelope.
- Any behavior not listed here is out of scope for release sign-off.

This document covers only authentication and authorization concerns. Business logic tests (account creation, crediting, escrow operations, transaction history) are covered by the existing `central-bank-service-tests.md`. Those tests remain valid but must be executed using JWS-wrapped requests after this feature lands.

---

## Prerequisites

These tests require:

1. A running Identity service (port 8001) — or a mock that implements `POST /agents/verify-jws`
2. Pre-registered agents with known Ed25519 keypairs (public + private keys)
3. A platform agent registered in the Identity service, whose `agent_id` matches `settings.platform.agent_id`
4. The Central Bank service configured with the `identity` section pointing to the Identity service and the `platform` section specifying the platform agent ID

---

## Required API Error Contract (New Auth Error Codes)

These error codes are added by the authentication feature. Existing error codes from `central-bank-service-tests.md` remain unchanged.

| Status | Error Code                      | Required When                                                |
|--------|---------------------------------|--------------------------------------------------------------|
| 400    | `INVALID_JWS`                  | `token` field is missing, null, non-string, empty, or malformed (not a three-part compact serialization); or Bearer header is missing, lacks the `Bearer ` prefix, or contains an empty/malformed token |
| 400    | `INVALID_PAYLOAD`              | JWS payload is missing `action`, `action` does not match the expected value for the endpoint, or required payload fields are missing |
| 400    | `PAYLOAD_MISMATCH`             | JWS payload field does not match URL parameter (e.g., `account_id` in payload does not match `{account_id}` in URL, or `escrow_id` in payload does not match `{escrow_id}` in URL) |
| 403    | `FORBIDDEN`                    | JWS signature verification failed (tampered, unregistered agent), signer is not the platform agent for platform-only operations, or agent is accessing another agent's account/funds |
| 502    | `IDENTITY_SERVICE_UNAVAILABLE` | Identity service is unreachable, times out, or returns an unexpected response (non-200 with non-JSON body, unexpected status code) |

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
- `agent_alice`, `agent_bob` are agents pre-registered in the Identity service with known Ed25519 keypairs.
- `jws(signer, payload)` denotes a JWS compact serialization (RFC 7515, EdDSA/Ed25519) with header `{"alg":"EdDSA","kid":"<signer.agent_id>"}`, the given JSON payload, and a valid Ed25519 signature.
- `tampered_jws(signer, payload)` denotes a JWS where the payload has been altered after signing (signature mismatch).
- Agent IDs use the format `a-<uuid4>`.
- Account IDs are the same as agent IDs.
- Escrow IDs use the format `esc-<uuid4>`.
- Task IDs use the format `T-<identifier>`.
- A "valid platform create-account JWS" means: `jws(platform_agent, {action: "create_account", agent_id: alice.agent_id, initial_balance: 100})`.
- A "valid agent escrow-lock JWS" means: `jws(alice, {action: "escrow_lock", agent_id: alice.agent_id, amount: 10, task_id: "T-xxx"})`.
- A "valid agent get-balance JWS" means: `jws(alice, {action: "get_balance", account_id: alice.agent_id})`.

---

## Category 1: Body Token Validation (POST Endpoints)

### AUTH-01 Valid platform JWS on POST /accounts creates account

**Setup:** Register `platform_agent` and `agent_alice` in Identity service. No account exists for Alice yet.
**Action:** `POST /accounts` with body:
```json
{"token": "<jws(platform_agent, {action: 'create_account', agent_id: alice.agent_id, initial_balance: 100})>"}
```
**Expected:**
- `201 Created`
- Body includes `account_id`, `balance`, `created_at`
- `account_id` matches `alice.agent_id`
- `balance` equals `100`

### AUTH-02 Valid platform JWS on POST /accounts/{id}/credit credits account

**Setup:** Register `platform_agent` and `agent_alice` in Identity service. Create account for Alice with initial balance.
**Action:** `POST /accounts/{alice.agent_id}/credit` with body:
```json
{"token": "<jws(platform_agent, {action: 'credit', account_id: alice.agent_id, amount: 50, reference: 'salary_round_1'})>"}
```
**Expected:**
- `200 OK`
- Body includes `tx_id`, `balance_after`
- `balance_after` reflects credited amount

### AUTH-03 Valid agent JWS on POST /escrow/lock locks funds

**Setup:** Register `agent_alice` in Identity service. Create account for Alice with sufficient balance.
**Action:** `POST /escrow/lock` with body:
```json
{"token": "<jws(alice, {action: 'escrow_lock', agent_id: alice.agent_id, amount: 10, task_id: 'T-xxx'})>"}
```
**Expected:**
- `201 Created`
- Body includes `escrow_id`, `amount`, `task_id`, `status`
- `amount` equals `10`
- `status` equals `"locked"`

### AUTH-04 Missing `token` field in POST body

**Action:** `POST /accounts` with body `{"agent_id": "a-xxx", "initial_balance": 100}` (plain JSON, no `token` field).
**Expected:**
- `400 Bad Request`
- `error = INVALID_JWS`

### AUTH-05 Null `token` in POST body

**Action:** `POST /accounts` with body `{"token": null}`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_JWS`

### AUTH-06 Non-string `token` in POST body (integer)

**Action:** `POST /accounts` with body `{"token": 12345}`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_JWS`

### AUTH-07 Empty string `token` in POST body

**Action:** `POST /accounts` with body `{"token": ""}`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_JWS`

### AUTH-08 Malformed JWS (not three-part compact serialization)

**Action:** Send each of these tokens in separate requests to `POST /accounts`:
- `{"token": "not-a-jws-at-all"}`
- `{"token": "only.two-parts"}`
- `{"token": "four.parts.is.wrong.here"}`
**Expected:** `400`, `error = INVALID_JWS` for each.

### AUTH-09 Tampered JWS (altered payload, signature mismatch)

**Setup:** Register `platform_agent`. Construct a valid JWS for `create_account`, then modify the payload portion after signing.
**Action:** `POST /accounts` with `{"token": "<tampered_jws(platform_agent, {action: 'create_account', ...})>"}`.
**Expected:**
- `403 Forbidden`
- `error = FORBIDDEN`

### AUTH-10 Non-platform signer on POST /accounts

**Setup:** Register `agent_alice` in Identity service.
**Action:** `POST /accounts` with `jws(alice, {action: "create_account", agent_id: alice.agent_id, initial_balance: 50})` — signed by a regular agent, not the platform.
**Expected:**
- `403 Forbidden`
- `error = FORBIDDEN`

### AUTH-11 Non-platform signer on POST /accounts/{id}/credit

**Setup:** Register `agent_alice` in Identity service. Create account for Alice.
**Action:** `POST /accounts/{alice.agent_id}/credit` with `jws(alice, {action: "credit", account_id: alice.agent_id, amount: 10, reference: "gift"})`.
**Expected:**
- `403 Forbidden`
- `error = FORBIDDEN`

### AUTH-12 Non-platform signer on POST /escrow/{id}/release

**Setup:** Register `agent_alice` in Identity service. Create account for Alice. Lock escrow for a task.
**Action:** `POST /escrow/{escrow_id}/release` with `jws(alice, {action: "escrow_release", escrow_id: escrow_id, recipient_account_id: alice.agent_id})`.
**Expected:**
- `403 Forbidden`
- `error = FORBIDDEN`

### AUTH-13 Non-platform signer on POST /escrow/{id}/split

**Setup:** Register `agent_alice` and `agent_bob` in Identity service. Create accounts and lock escrow.
**Action:** `POST /escrow/{escrow_id}/split` with `jws(alice, {action: "escrow_split", escrow_id: escrow_id, worker_account_id: bob.agent_id, worker_pct: 50, poster_account_id: alice.agent_id})`.
**Expected:**
- `403 Forbidden`
- `error = FORBIDDEN`

### AUTH-14 Wrong `action` value in JWS payload

**Setup:** Register `platform_agent` in Identity service.
**Action:** `POST /accounts` with `jws(platform_agent, {action: "escrow_lock", agent_id: "a-xxx", initial_balance: 50})` — action is `"escrow_lock"` instead of `"create_account"`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_PAYLOAD`

### AUTH-15 Missing `action` field in JWS payload

**Setup:** Register `platform_agent` in Identity service.
**Action:** `POST /accounts` with `jws(platform_agent, {agent_id: "a-xxx", initial_balance: 50})` — payload has no `action` field.
**Expected:**
- `400 Bad Request`
- `error = INVALID_PAYLOAD`

### AUTH-16 Agent locking another agent's funds (signer mismatch on escrow lock)

**Setup:** Register `agent_alice` and `agent_bob` in Identity service. Create account for Bob with sufficient balance.
**Action:** `POST /escrow/lock` with `jws(alice, {action: "escrow_lock", agent_id: bob.agent_id, amount: 10, task_id: "T-xxx"})` — Alice signs a JWS claiming to be Bob.
**Expected:**
- `403 Forbidden`
- `error = FORBIDDEN`

### AUTH-17 Malformed JSON body (not valid JSON) on POST endpoint

**Action:** `POST /accounts` with `Content-Type: application/json` and body `{not json`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_JSON`

### AUTH-18 Non-object JSON body (array) on POST endpoint

**Action:** `POST /accounts` with `Content-Type: application/json` and body `[{"token": "eyJ..."}]`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_JSON`

---

## Category 2: Bearer Token Validation (GET Endpoints)

### BEARER-01 Valid Bearer token on GET /accounts/{id}

**Setup:** Register `agent_alice` in Identity service. Create account for Alice.
**Action:** `GET /accounts/{alice.agent_id}` with header `Authorization: Bearer <jws(alice, {action: "get_balance", account_id: alice.agent_id})>`.
**Expected:**
- `200 OK`
- Body includes `account_id`, `balance`, `created_at`
- `account_id` matches `alice.agent_id`

### BEARER-02 Valid Bearer token on GET /accounts/{id}/transactions

**Setup:** Register `agent_alice` in Identity service. Create account for Alice.
**Action:** `GET /accounts/{alice.agent_id}/transactions` with header `Authorization: Bearer <jws(alice, {action: "get_transactions", account_id: alice.agent_id})>`.
**Expected:**
- `200 OK`
- Body includes `transactions` array

### BEARER-03 Missing Authorization header on GET endpoint

**Action:** `GET /accounts/{alice.agent_id}` with no `Authorization` header.
**Expected:**
- `400 Bad Request`
- `error = INVALID_JWS`

### BEARER-04 Authorization header without "Bearer " prefix

**Action:** `GET /accounts/{alice.agent_id}` with header `Authorization: Token eyJ...`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_JWS`

### BEARER-05 Empty Bearer token

**Action:** `GET /accounts/{alice.agent_id}` with header `Authorization: Bearer `.
**Expected:**
- `400 Bad Request`
- `error = INVALID_JWS`

### BEARER-06 Tampered Bearer token

**Setup:** Register `agent_alice`. Construct a valid Bearer JWS, then modify the payload portion after signing.
**Action:** `GET /accounts/{alice.agent_id}` with header `Authorization: Bearer <tampered_jws(alice, {action: "get_balance", account_id: alice.agent_id})>`.
**Expected:**
- `403 Forbidden`
- `error = FORBIDDEN`

### BEARER-07 Agent accessing another agent's account (GET /accounts/{id})

**Setup:** Register `agent_alice` and `agent_bob` in Identity service. Create accounts for both.
**Action:** `GET /accounts/{bob.agent_id}` with header `Authorization: Bearer <jws(alice, {action: "get_balance", account_id: bob.agent_id})>` — Alice tries to view Bob's balance.
**Expected:**
- `403 Forbidden`
- `error = FORBIDDEN`

### BEARER-08 Agent accessing another agent's transactions

**Setup:** Register `agent_alice` and `agent_bob` in Identity service. Create accounts for both.
**Action:** `GET /accounts/{bob.agent_id}/transactions` with header `Authorization: Bearer <jws(alice, {action: "get_transactions", account_id: bob.agent_id})>` — Alice tries to view Bob's transaction history.
**Expected:**
- `403 Forbidden`
- `error = FORBIDDEN`

### BEARER-09 Wrong `action` in Bearer JWS

**Setup:** Register `agent_alice` in Identity service. Create account for Alice.
**Action:** `GET /accounts/{alice.agent_id}` with header `Authorization: Bearer <jws(alice, {action: "escrow_lock", account_id: alice.agent_id})>` — action is `"escrow_lock"` instead of `"get_balance"`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_PAYLOAD`

### BEARER-10 Payload `account_id` mismatch with URL path parameter

**Setup:** Register `agent_alice` in Identity service. Create account for Alice.
**Action:** `GET /accounts/{alice.agent_id}` with header `Authorization: Bearer <jws(alice, {action: "get_balance", account_id: "a-different-uuid"})>` — payload `account_id` does not match URL.
**Expected:**
- `400 Bad Request`
- `error = PAYLOAD_MISMATCH`

---

## Category 3: Identity Service Dependency

### IDEP-01 Identity service is down

**Setup:** Configure Central Bank service to point to an Identity service URL that is not running (e.g., `http://localhost:19999`).
**Action:** `POST /accounts` with a valid-looking JWS token.
**Expected:**
- `502 Bad Gateway`
- `error = IDENTITY_SERVICE_UNAVAILABLE`

### IDEP-02 Identity service times out

**Setup:** Start a mock HTTP server on the Identity service port that accepts connections but never responds (simulates timeout). Configure Central Bank service to point to this mock.
**Action:** `POST /escrow/lock` with a valid JWS token.
**Expected:**
- `502 Bad Gateway`
- `error = IDENTITY_SERVICE_UNAVAILABLE`

### IDEP-03 Identity service returns unexpected response (non-JSON)

**Setup:** Start a mock HTTP server on the Identity service port that returns `HTTP 500` with body `"Internal Server Error"` (non-JSON) for `POST /agents/verify-jws`. Configure Central Bank service to point to this mock.
**Action:** `POST /accounts` with a valid JWS token.
**Expected:**
- `502 Bad Gateway`
- `error = IDENTITY_SERVICE_UNAVAILABLE`

---

## Category 4: Public Endpoints

### PUB-01 GET /health requires no authentication

**Action:** `GET /health` with no `Authorization` header and no token.
**Expected:**
- `200 OK`
- `status = "ok"`

---

## Release Gate Checklist

Authentication is release-ready only if:

1. All tests in this document pass.
2. All tests in `central-bank-service-tests.md` pass when executed with JWS-wrapped requests.
3. No endpoint returns `500` in any test scenario.
4. All failing responses conform to the required error envelope.
5. The Identity service being unavailable never causes the Central Bank service to crash — it returns `502` gracefully.

---

## Coverage Summary

| Category | IDs | Count |
|----------|-----|-------|
| Body Token Validation (POST) | AUTH-01 to AUTH-18 | 18 |
| Bearer Token Validation (GET) | BEARER-01 to BEARER-10 | 10 |
| Identity Service Dependency | IDEP-01 to IDEP-03 | 3 |
| Public Endpoints | PUB-01 | 1 |
| **Total** | | **32** |

| Endpoint | Covered By |
|----------|------------|
| `POST /accounts` | AUTH-01, AUTH-04 to AUTH-10, AUTH-14, AUTH-15, AUTH-17, AUTH-18, IDEP-01, IDEP-03 |
| `POST /accounts/{account_id}/credit` | AUTH-02, AUTH-11 |
| `POST /escrow/lock` | AUTH-03, AUTH-16, IDEP-02 |
| `POST /escrow/{escrow_id}/release` | AUTH-12 |
| `POST /escrow/{escrow_id}/split` | AUTH-13 |
| `GET /accounts/{account_id}` | BEARER-01, BEARER-03 to BEARER-07, BEARER-09, BEARER-10 |
| `GET /accounts/{account_id}/transactions` | BEARER-02, BEARER-08 |
| `GET /health` | PUB-01 |
