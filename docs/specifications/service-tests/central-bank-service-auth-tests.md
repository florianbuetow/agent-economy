# Central Bank Service — Authentication Test Specification

## Purpose

This document is the release-gate test specification for the Central Bank's JWS-based authentication, covering the **two-tier verification model**: agent-signed operations verified remotely via Identity, and platform-signed operations (`credit`, `escrow_release`, `escrow_split`) verified locally via the service's own `PlatformAgent`. `POST /accounts` (`create_account`) is the one documented exception — both of its modes verify via Identity, never locally.

It is intentionally strict and unambiguous:

- Every negative case has one required status code and one required error code.
- Every failing response must use the standard error envelope.
- Any behavior not listed here is out of scope for release sign-off.

This document covers only authentication and authorization concerns. Business logic tests (account creation, crediting, escrow operations, transaction history) are covered by `central-bank-service-tests.md`. Those tests remain valid but must be executed using JWS-wrapped requests after this feature lands.

---

## Prerequisites

These tests require:

1. A `PlatformAgent` instantiated with valid Ed25519 keys, registered as the service's `state.platform_agent` — this is the one and only signer that passes local verification. There is no `platform.agent_id` config value; the platform's identity is whatever this instance registers as.
2. Test agents (`agent_alice`, `agent_bob`) with known Ed25519 public/private keypairs, discoverable via Identity's `POST /agents/verify-jws` for agent-signed operations.
3. For the Identity-down scenarios: an `IdentityClient` pointed at an address that refuses connections (not a mock that returns an error — the point is to prove no network call short-circuits into a canned response).

---

## Required API Error Contract (New Auth Error Codes)

These error codes are added by the authentication feature. Existing error codes from `central-bank-service-tests.md` remain unchanged. All codes are lowercase `snake_case`.

| Status | Error Code                      | Required When                                                |
|--------|-----------------------------------|--------------------------------------------------------------|
| 400    | `invalid_jws`                    | Non-object request body or signed payload, malformed compact/header form, decoded signature length other than 64 bytes, exact header profile, or lifetime bound is invalid |
| 400    | `invalid_payload`                | Action is missing/non-string, authenticated audience/resource/action mismatches, or required domain fields are missing |
| 400    | `payload_mismatch`               | JWS payload field does not match URL parameter (e.g., `account_id` in payload does not match `{account_id}` in URL, or `escrow_id` in payload does not match `{escrow_id}` in URL) |
| 401    | `token_expired`                  | Authentic local or Identity-verified token satisfies `now >= exp + clock_skew_seconds` |
| 401    | `token_not_yet_valid`             | Authentic local or Identity-verified token satisfies `iat > now + clock_skew_seconds` |
| 403    | `forbidden`                      | JWS signature verification failed (tampered, unknown agent, or — for platform ops — not signed by the registered platform key), signer is not authorized for the operation, or agent is accessing another agent's account/funds |
| 502    | `identity_service_unavailable`   | Identity is unreachable during an agent-signed verification call or `create_account`'s agent-existence check (never on `credit`/`escrow_release`/`escrow_split`) |
| 503    | `service_not_ready`              | The ledger, Identity client, or platform agent has not finished initializing |

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

- `platform_agent` is the registered platform agent with a known Ed25519 keypair, instantiated locally as the service's `PlatformAgent`.
- `agent_alice`, `agent_bob` are agents with known Ed25519 public/private keypairs, resolvable via Identity's `verify-jws` for agent-signed operations.
- `jws(signer, payload)` is concise scenario notation for a real Ed25519 compact JWS with exact header `{alg,typ,kid,iat,exp,aud,resource}` and the existing action/domain payload. The fixture call must still receive the endpoint's audience and concrete resource explicitly; the notation does not authorize signer defaults. Happy fixtures always use explicit valid policy values.
- `raw_jws(header, payload, key)` exists only for negative malformed-profile vectors and still creates a real signature. Authentication tests never prove security with a verifier mock. Domain-only mocks must return a complete typed `VerifiedJws(header, payload)` and are not token-conformance evidence.
- `tampered_jws(signer, payload)` denotes a JWS where the payload has been altered after signing (signature mismatch).
- Agent IDs use the format `a-<uuid4>`.
- Account IDs are the same as agent IDs.
- Escrow IDs use the format `esc-<uuid4>`.
- Task IDs use the format `T-<identifier>`.
- A "valid platform create-account JWS" means: `jws(platform_agent, {action: "create_account", agent_id: alice.agent_id, initial_balance: 100})`.
- A "valid agent escrow-lock JWS" means: `jws(alice, {action: "escrow_lock", agent_id: alice.agent_id, amount: 10, task_id: "T-xxx"})`.
- A "valid agent get-balance JWS" means: `jws(alice, {action: "get_balance", account_id: alice.agent_id})`.
- A "valid agent self-service create-account JWS" means: `jws(alice, {action: "create_account", agent_id: alice.agent_id, initial_balance: 0})`.

---

## Category 1: Body Token Validation (POST Endpoints)

### AUTH-01 Valid platform JWS on POST /accounts creates account
**Setup:** `platform_agent` and `agent_alice` are known; no account exists for Alice yet.
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
**Setup:** `platform_agent` and `agent_alice` are known. Create account for Alice with initial balance.
**Action:** `POST /accounts/{alice.agent_id}/credit` with body:
```json
{"token": "<jws(platform_agent, {action: 'credit', account_id: alice.agent_id, amount: 50, reference: 'salary_round_1'})>"}
```
**Expected:**
- `200 OK`
- Body includes `tx_id`, `balance_after`
- `balance_after` reflects credited amount

### AUTH-03 Valid agent JWS on POST /escrow/lock locks funds
**Setup:** `agent_alice` is known. Create account for Alice with sufficient balance.
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
- `error = invalid_jws`

### AUTH-05 Null `token` in POST body

**Action:** `POST /accounts` with body `{"token": null}`.
**Expected:**
- `400 Bad Request`
- `error = invalid_jws`

### AUTH-06 Non-string `token` in POST body (integer)

**Action:** `POST /accounts` with body `{"token": 12345}`.
**Expected:**
- `400 Bad Request`
- `error = invalid_jws`

### AUTH-07 Empty string `token` in POST body

**Action:** `POST /accounts` with body `{"token": ""}`.
**Expected:**
- `400 Bad Request`
- `error = invalid_jws`

### AUTH-08 Malformed JWS (not three-part compact serialization)

**Action:** Send each of these tokens in separate requests to `POST /accounts`:
- `{"token": "not-a-jws-at-all"}`
- `{"token": "only.two-parts"}`
- `{"token": "four.parts.is.wrong.here"}`
**Expected:** `400`, `error = invalid_jws` for each.

### AUTH-09 Tampered JWS (altered payload, signature mismatch)

**Setup:** `platform_agent` is known. Construct a valid JWS for `create_account`, then modify the payload portion after signing.
**Action:** `POST /accounts` with `{"token": "<tampered_jws(platform_agent, {action: 'create_account', ...})>"}`.
**Expected:**
- `403 Forbidden`
- `error = forbidden`

### AUTH-10 Non-platform signer creating another agent's account is rejected

**Setup:** `agent_alice` is known.
**Action:** `POST /accounts` with `jws(alice, {action: "create_account", agent_id: "a-someone-else", initial_balance: 0})` — Alice signs a request naming a different agent as the account owner.
**Expected:**
- `403 Forbidden`
- `error = forbidden`

### AUTH-11 Non-platform signer on POST /accounts/{id}/credit

**Setup:** `agent_alice` is known. Create account for Alice.
**Action:** `POST /accounts/{alice.agent_id}/credit` with `jws(alice, {action: "credit", account_id: alice.agent_id, amount: 10, reference: "gift"})`.
**Expected:**
- `403 Forbidden`
- `error = forbidden`

### AUTH-12 Non-platform signer on POST /escrow/{id}/release

**Setup:** `agent_alice` is known. Create account for Alice. Lock escrow for a task.
**Action:** `POST /escrow/{escrow_id}/release` with `jws(alice, {action: "escrow_release", escrow_id: escrow_id, recipient_account_id: alice.agent_id})`.
**Expected:**
- `403 Forbidden`
- `error = forbidden`

### AUTH-13 Non-platform signer on POST /escrow/{id}/split

**Setup:** `agent_alice` and `agent_bob` are known. Create accounts and lock escrow.
**Action:** `POST /escrow/{escrow_id}/split` with `jws(alice, {action: "escrow_split", escrow_id: escrow_id, worker_account_id: bob.agent_id, worker_pct: 50, poster_account_id: alice.agent_id})`.
**Expected:**
- `403 Forbidden`
- `error = forbidden`

### AUTH-14 Wrong `action` value in JWS payload

**Setup:** `platform_agent` is known.
**Action:** `POST /accounts` with `jws(platform_agent, {action: "escrow_lock", agent_id: "a-xxx", initial_balance: 50})` — action is `"escrow_lock"` instead of `"create_account"`.
**Expected:**
- `400 Bad Request`
- `error = invalid_payload`

### AUTH-15 Missing `action` field in JWS payload

**Setup:** `platform_agent` is known.
**Action:** `POST /accounts` with `jws(platform_agent, {agent_id: "a-xxx", initial_balance: 50})` — payload has no `action` field.
**Expected:**
- `400 Bad Request`
- `error = invalid_payload`

### AUTH-16 Agent locking another agent's funds (signer mismatch on escrow lock)

**Setup:** `agent_alice` and `agent_bob` are known. Create account for Bob with sufficient balance.
**Action:** `POST /escrow/lock` with `jws(alice, {action: "escrow_lock", agent_id: bob.agent_id, amount: 10, task_id: "T-xxx"})` — Alice signs a JWS claiming to be Bob.
**Expected:**
- `403 Forbidden`
- `error = forbidden`

### AUTH-17 Malformed JSON body (not valid JSON) on POST endpoint

**Action:** `POST /accounts` with `Content-Type: application/json` and body `{not json`.
**Expected:**
- `400 Bad Request`
- `error = invalid_json`

### AUTH-18 Non-object JSON body (array) on POST endpoint

**Action:** `POST /accounts` with `Content-Type: application/json` and body `[{"token": "eyJ..."}]`.
**Expected:**
- `400 Bad Request`
- `error = invalid_jws`

### AUTH-19 Self-service create-account is verified via Identity, not locally

**Setup:** `agent_alice` is known, registered only with Identity (not signed by the platform).
**Action:** `POST /accounts` with a valid agent self-service create-account JWS (`initial_balance: 0`, `agent_id: alice.agent_id`).
**Expected:**
- `201 Created` — the request succeeds even though `alice` is not the platform, because self-service `create_account` is an agent-signed operation authorized by the ownership rule (signer creates their own account), not a platform-only check.

---

## Category 2: Bearer Token Validation (GET Endpoints)

### BEARER-01 Valid Bearer token on GET /accounts/{id}

**Setup:** `agent_alice` is known. Create account for Alice.
**Action:** `GET /accounts/{alice.agent_id}` with header `Authorization: Bearer <jws(alice, {action: "get_balance", account_id: alice.agent_id})>`.
**Expected:**
- `200 OK`
- Body includes `account_id`, `balance`, `created_at`
- `account_id` matches `alice.agent_id`

### BEARER-02 Valid Bearer token on GET /accounts/{id}/transactions

**Setup:** `agent_alice` is known. Create account for Alice.
**Action:** `GET /accounts/{alice.agent_id}/transactions` with header `Authorization: Bearer <jws(alice, {action: "get_transactions", account_id: alice.agent_id})>`.
**Expected:**
- `200 OK`
- Body includes `transactions` array

### BEARER-03 Missing Authorization header on GET endpoint

**Action:** `GET /accounts/{alice.agent_id}` with no `Authorization` header.
**Expected:**
- `400 Bad Request`
- `error = invalid_jws`

### BEARER-04 Authorization header without "Bearer " prefix

**Action:** `GET /accounts/{alice.agent_id}` with header `Authorization: Token eyJ...`.
**Expected:**
- `400 Bad Request`
- `error = invalid_jws`

### BEARER-05 Empty Bearer token

**Action:** `GET /accounts/{alice.agent_id}` with header `Authorization: Bearer `.
**Expected:**
- `400 Bad Request`
- `error = invalid_jws`

### BEARER-06 Tampered Bearer token

**Setup:** `agent_alice` is known. Construct a valid Bearer JWS, then modify the payload portion after signing.
**Action:** `GET /accounts/{alice.agent_id}` with header `Authorization: Bearer <tampered_jws(alice, {action: "get_balance", account_id: alice.agent_id})>`.
**Expected:**
- `403 Forbidden`
- `error = forbidden`

### BEARER-07 Agent accessing another agent's account (GET /accounts/{id})

**Setup:** `agent_alice` and `agent_bob` are known. Create accounts for both.
**Action:** `GET /accounts/{bob.agent_id}` with header `Authorization: Bearer <jws(alice, {action: "get_balance", account_id: bob.agent_id})>` — Alice tries to view Bob's balance.
**Expected:**
- `403 Forbidden`
- `error = forbidden`

### BEARER-08 Agent accessing another agent's transactions

**Setup:** `agent_alice` and `agent_bob` are known. Create accounts for both.
**Action:** `GET /accounts/{bob.agent_id}/transactions` with header `Authorization: Bearer <jws(alice, {action: "get_transactions", account_id: bob.agent_id})>` — Alice tries to view Bob's transaction history.
**Expected:**
- `403 Forbidden`
- `error = forbidden`

### BEARER-09 Wrong `action` in Bearer JWS is reported even for the correct owner

**Setup:** `agent_alice` is known. Create account for Alice.
**Action:** `GET /accounts/{alice.agent_id}` with header `Authorization: Bearer <jws(alice, {action: "escrow_lock", account_id: alice.agent_id})>` — action is `"escrow_lock"` instead of `"get_balance"`, but the signer is the correct account owner.
**Expected:**
- `400 Bad Request`
- `error = invalid_payload`

### BEARER-10 Payload `account_id` mismatch with URL path parameter

**Setup:** `agent_alice` is known. Create account for Alice.
**Action:** `GET /accounts/{alice.agent_id}` with header `Authorization: Bearer <jws(alice, {action: "get_balance", account_id: "a-different-uuid"})>` — payload `account_id` does not match URL.
**Expected:**
- `400 Bad Request`
- `error = payload_mismatch`

### BEARER-11 Wrong owner is reported even when the payload is ALSO malformed (ownership check precedes payload validation)

**Setup:** `agent_alice` and `agent_bob` are known. Create accounts for both.
**Action:** `GET /accounts/{bob.agent_id}` with header `Authorization: Bearer <jws(alice, {action: "not_a_real_action"})>` — Alice both accesses the wrong account AND sends an invalid `action`.
**Expected:**
- `403 Forbidden`
- `error = forbidden` — the ownership check (`verified signer == URL account_id`) runs before the `action` payload check on this endpoint, unlike the payload-first ordering used by `credit`/`escrow_release`/`escrow_split` (see Category 3).

---

## Category 3: Error Precedence (Two-Tier Ordering)

Both tiers authenticate the strict profile before relying on protected-header or payload contents. Existing ownership and domain-validation precedence applies only after a typed `VerifiedJws` exists.

### PREC-01 Malformed credit payload from a non-platform signer fails authentication first

**Setup:** `agent_alice` (a regular, non-platform agent) is known. Create a zero-balance account for a worker.
**Action:** `POST /accounts/{worker}/credit` with `jws(alice, {action: "credit", account_id: worker, amount: 10})` — `reference` is missing (malformed payload) AND the signer is not the platform.
**Expected:**
- `403 Forbidden`
- `error = forbidden`; no payload-only validation result is used and balance is unchanged.

### PREC-02 Malformed escrow-release payload from a non-platform signer fails authentication first

**Setup:** `agent_alice` is known. An escrow exists.
**Action:** `POST /escrow/{escrow_id}/release` with `jws(alice, {action: "escrow_release"})` — `recipient_account_id` is missing AND the signer is not the platform.
**Expected:**
- `403 Forbidden`
- `error = forbidden`; escrow remains unchanged.

### PREC-03 Malformed escrow-split payload from a non-platform signer fails authentication first

**Setup:** `agent_alice` is known. An escrow exists.
**Action:** `POST /escrow/{escrow_id}/split` with `jws(alice, {action: "escrow_split", worker_account_id: bob.agent_id})` — `poster_account_id` and `worker_pct` are missing AND the signer is not the platform.
**Expected:**
- `403 Forbidden`
- `error = forbidden`; escrow remains unchanged.

### PREC-04 Well-formed but non-platform-signed credit is 403, not a payload error

**Setup:** `agent_alice` is known. Create a zero-balance account for a worker.
**Action:** `POST /accounts/{worker}/credit` with `jws(alice, {action: "credit", account_id: worker, amount: 10, reference: "r-1"})` — payload is entirely well-formed, but the signer is not the platform.
**Expected:**
- `403 Forbidden`
- `error = forbidden` — once payload validation passes, local signature verification is the deciding factor.

### PREC-05 A well-formed create_account payload from an unreachable-Identity path fails 502, not locally

**Setup:** Identity is unreachable (dead port). `agent_alice` self-service create-account payload is well-formed.
**Action:** `POST /accounts` with a valid agent self-service create-account JWS while Identity is down.
**Expected:**
- `502 Service Unavailable`
- `error = identity_service_unavailable` — `create_account` has no local-verification fallback; unlike `credit`/`escrow_release`/`escrow_split`, it always depends on Identity.

### PREC-06 Malformed create_account payload does NOT beat a bad signature (verify-first tier)

**Setup:** `agent_alice` is known and reachable via Identity, which reports the JWS as invalid (`valid: false`) for a tampered token.
**Action:** `POST /accounts` with a tampered JWS whose payload is also missing `agent_id`.
**Expected:**
- `403 Forbidden`
- `error = forbidden` — signature verification via Identity happens before payload validation on this endpoint, so the invalid-signature error is reported even though the payload is separately malformed.

---

## Category 4: Platform-Signed Operations Survive an Identity Outage

These tests are the operational core of the two-tier model: platform ops must keep working when Identity cannot be reached, because the Task Board depends on them to settle every task.

### RESIL-01 Platform-signed credit succeeds while Identity is down

**Setup:** Identity is unreachable (dead port). `platform_agent` is registered locally with the service. Account exists for a worker.
**Action:** `POST /accounts/{worker}/credit` with a valid platform-signed credit JWS.
**Expected:**
- `200 OK`
- `balance_after` reflects the credited amount
- No call to Identity is made (verified by asserting the Identity port receives no connection, or by an interaction assertion on a mock)

### RESIL-02 Platform-signed escrow release succeeds while Identity is down

**Setup:** Identity is unreachable. Escrow is locked for a payer/worker pair.
**Action:** `POST /escrow/{escrow_id}/release` with a valid platform-signed release JWS.
**Expected:**
- `200 OK`
- `status = "released"`, correct `amount`

### RESIL-03 Platform-signed escrow split succeeds while Identity is down

**Setup:** Identity is unreachable. Escrow is locked for a poster/worker pair.
**Action:** `POST /escrow/{escrow_id}/split` with a valid platform-signed split JWS.
**Expected:**
- `200 OK`
- `worker_amount`/`poster_amount` computed correctly

### RESIL-04 Agent-signed escrow lock fails cleanly (502) while Identity is down — no silent local fallback

**Setup:** Identity is unreachable. Account exists with sufficient balance.
**Action:** `POST /escrow/lock` with a valid agent-signed lock JWS.
**Expected:**
- `502 Service Unavailable`
- `error = identity_service_unavailable` — agent-signed operations have no local-verification fallback; they fail explicitly rather than silently trusting an unverifiable signature.

---

## Category 5: Strict Token Lifetime and Context

### EXP-01 Expired platform token is rejected on credit

**Setup:** `platform_agent` signs a credit token with a short TTL (e.g. 1 second) and the request is sent after it has elapsed. Account exists for the target.
**Action:** `POST /accounts/{account_id}/credit` with the expired token.
**Expected:**
- `401 Unauthorized`
- `error = token_expired`

### EXP-02 Non-expiring legacy token is rejected with no ledger effect

**Setup:** Use the raw-signature fixture to create an authentically platform-signed credit token with no `iat`/`exp`; record balance and transactions.
**Action:** `POST /accounts/{account_id}/credit` with the legacy token.
**Expected:**
- `400 Bad Request`, `error = invalid_jws`
- Balance and transaction history are unchanged.

### MP03-CB-01 Strict types, exact fields, and maximum lifetime

**Action:** Send real-signed local credit tokens covering non-object signed payload, missing/extra header fields, wrong types (including boolean `iat`/`exp`), decoded signature length other than 64 bytes, `exp <= iat`, and lifetime one second above the configured maximum.
**Expected:** Each returns `400 invalid_jws`; balance and transactions remain unchanged.

### MP03-CB-02 Exact future and expiry boundaries

**Action:** With controlled time, send tokens at `iat = now + skew`, `iat = now + skew + 1`, `now = exp + skew - 1`, and `now = exp + skew`.
**Expected:** The equality-within-skew cases succeed; the next future second is `401 token_not_yet_valid`; the exact expiry boundary is `401 token_expired`. Rejected requests have zero effect.

### MP03-CB-03 Wrong audience, method/path resource, and action

**Action:** Send authentic tokens with wrong `aud`, wrong HTTP method in `resource`, wrong concrete path or path id, missing/wrong-type/wrong-value action, and payload fields named `aud`/`resource` that disagree with the protected header.
**Expected:** Each returns `400 invalid_payload`; zero ledger effect.

### MP03-CB-04 Correct platform signature with wrong `kid`

**Action:** Sign a valid credit token with the platform key but place another registered identity in the protected `kid`.
**Expected:** `403 forbidden`; zero ledger effect.

### MP03-CB-05 Malformed Identity success is unavailable

**Action:** For an agent-signed route, make Identity return HTTP 200/`valid:true` while omitting `protected_header`, returning a non-object payload, making `agent_id != protected_header.kid`, substituting `true`/`1`/`1.0`, or claiming success for an invalid originally requested token.
**Expected:** Each returns `502 identity_service_unavailable`; the shared client does not fabricate a payload and the ledger is unchanged.

### MP03-CB-06 Shared, Identity, and local-adapter conformance

**Action:** Run the same real-signed valid, malformed, future, expired, wrong-context, bad-signature, and wrong-platform-`kid` vectors through shared verification, real Identity HTTP verification where applicable, and Central Bank's real local platform adapter.
**Expected:** All applicable paths return the same typed `VerifiedJws` on success and the uniform error mapping on failure. No local platform vector makes an Identity HTTP call.

### MP03-CB-07 Official signer and policy configuration are strict

**Action:** Construct official agent/platform signers with missing, zero, and negative TTL; construct admission policy with missing/zero/negative maximum or negative skew; then load explicit initial `300/300/30` signer-TTL/admission-maximum/skew values.
**Expected:** Invalid configurations fail eagerly. The valid configuration signs all seven exact header fields, its TTL is no greater than Identity's maximum, and its token is accepted by the intended route.

### MP03-CB-08 Route-bound reads accept action-only payloads

**Action:** Request balance and transaction history with strict tokens whose payload contains only the matching string action; bind the concrete account in the protected `resource` path.
**Expected:** Both authentication checks succeed without inventing an `account_id` or other second payload field. Authorization still uses authenticated `kid` and the concrete route subject.

---

## Category 6: Public Endpoints

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
5. Platform-signed operations (`credit`, `escrow_release`, `escrow_split`) verify locally via `PlatformAgent.validate_certificate()` with zero calls to Identity (RESIL-01 to RESIL-03).
6. Agent-signed operations (`escrow_lock`, `get_balance`, `get_transactions`, `create_account`) verify via Identity and fail with `502 identity_service_unavailable` — never silently — when Identity is unreachable (RESIL-04, PREC-05).
7. A grep of the platform-op code paths shows no `verify_jws` call outside `create_account` (delivery-governance T-021 proof).

---

## Coverage Summary

| Category | IDs | Count |
|----------|-----|-------|
| Body Token Validation (POST) | AUTH-01 to AUTH-19 | 19 |
| Bearer Token Validation (GET) | BEARER-01 to BEARER-11 | 11 |
| Error Precedence | PREC-01 to PREC-06 | 6 |
| Identity-Outage Resilience | RESIL-01 to RESIL-04 | 4 |
| Token Expiry | EXP-01 to EXP-02 | 2 |
| MP-03 strict-profile conformance | MP03-CB-01 to MP03-CB-08 | 8 |
| Public Endpoints | PUB-01 | 1 |
| **Total** | | **51** |

| Endpoint | Covered By |
|----------|------------|
| `POST /accounts` | AUTH-01, AUTH-04 to AUTH-10, AUTH-14, AUTH-15, AUTH-17 to AUTH-19, PREC-05, PREC-06 |
| `POST /accounts/{account_id}/credit` | AUTH-02, AUTH-11, PREC-01, PREC-04, RESIL-01, EXP-01, EXP-02 |
| `POST /escrow/lock` | AUTH-03, AUTH-16, RESIL-04 |
| `POST /escrow/{escrow_id}/release` | AUTH-12, PREC-02, RESIL-02 |
| `POST /escrow/{escrow_id}/split` | AUTH-13, PREC-03, RESIL-03 |
| `GET /accounts/{account_id}` | BEARER-01, BEARER-03 to BEARER-07, BEARER-09 to BEARER-11 |
| `GET /accounts/{account_id}/transactions` | BEARER-02, BEARER-08 |
| `GET /health` | PUB-01 |
