# Court Service — Authentication Test Specification

## Purpose

This document is the release-gate test specification for JWS-based authentication on the Court service's three POST endpoints (`POST /disputes/file`, `POST /disputes/{dispute_id}/rebuttal`, `POST /disputes/{dispute_id}/rule`). All GET endpoints are public and require no authentication.

It is intentionally strict and unambiguous:

- Every negative case has one required status code and one required error code.
- Every failing response must use the standard error envelope.
- Any behavior not listed here is out of scope for release sign-off.

This document covers only authentication and authorization concerns. Business logic tests (dispute filing, rebuttal submission, ruling, judge evaluation, side-effects) are covered by the existing `court-service-tests.md`. Those tests remain valid but must be executed using JWS-wrapped requests after this feature lands.

---

## Prerequisites

These tests require:

1. A running Identity service (port 8001) — or a mock that implements `POST /agents/verify-jws`
2. A platform agent with a known Ed25519 keypair (public + private keys), registered in the Identity service
3. The Court service configured with the `identity` section pointing to the Identity service and `platform.agent_id` set to the platform agent's ID
4. A mock Task Board returning valid task data for `GET /tasks/{task_id}` (required by `POST /disputes/file` to fetch task context)

---

## Required API Error Contract (New Auth Error Codes)

These error codes are added by the authentication feature. Existing error codes from `court-service-tests.md` remain unchanged.

| Status | Error Code                       | Required When                                                |
|--------|----------------------------------|--------------------------------------------------------------|
| 400    | `INVALID_JWS`                   | `token` field is missing, null, non-string, empty, or malformed (not a three-part compact serialization) |
| 400    | `INVALID_PAYLOAD`               | JWS payload is missing `action`, or `action` does not match the expected value for the target endpoint |
| 403    | `FORBIDDEN`                     | JWS signature verification failed (tampered, unregistered signer), or signer is not the platform agent (`agent_id != settings.platform.agent_id`) |
| 502    | `IDENTITY_SERVICE_UNAVAILABLE`  | Identity service is unreachable, times out, or returns an unexpected response (non-200 with non-JSON body, unexpected status code) |

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

- `platform_agent` is the platform agent registered in the Identity service with a known Ed25519 keypair. Its `agent_id` matches `settings.platform.agent_id` in the Court service configuration.
- `rogue_agent` is a non-platform agent with a valid Ed25519 keypair, registered in the Identity service. Its `agent_id` does NOT match `settings.platform.agent_id`.
- `jws(signer, payload)` denotes a JWS compact serialization (RFC 7515, EdDSA/Ed25519) with header `{"alg":"EdDSA","kid":"<signer.agent_id>"}`, the given JSON payload, and a valid Ed25519 signature.
- `tampered_jws(signer, payload)` denotes a JWS where the payload has been altered after signing (signature mismatch).
- Agent IDs use the format `a-<uuid4>`.
- Task IDs use the format `t-<uuid4>`.
- Dispute IDs use the format `disp-<uuid4>`.
- Escrow IDs use the format `esc-<uuid4>`.
- A "valid file_dispute JWS" means: `jws(platform_agent, {action: "file_dispute", task_id: "t-...", claimant_id: "a-...", respondent_id: "a-...", claim: "...", escrow_id: "esc-..."})`.
- A "valid submit_rebuttal JWS" means: `jws(platform_agent, {action: "submit_rebuttal", dispute_id: "disp-...", rebuttal: "..."})`.
- A "valid trigger_ruling JWS" means: `jws(platform_agent, {action: "trigger_ruling", dispute_id: "disp-..."})`.
- The mock Task Board returns valid task data for any `GET /tasks/{task_id}` request during dispute filing tests.
- To test `POST /disputes/{dispute_id}/rebuttal` and `POST /disputes/{dispute_id}/rule`, a dispute must first be filed via `POST /disputes/file` with a valid platform JWS.

---

## Category 1: Platform JWS Validation

### AUTH-01 Valid platform JWS on POST /disputes/file

**Setup:** Register `platform_agent` in Identity service. Configure mock Task Board to return valid task data.
**Action:** `POST /disputes/file` with body:
```json
{"token": "<jws(platform_agent, {action: 'file_dispute', task_id: 't-xxx', claimant_id: 'a-alice', respondent_id: 'a-bob', claim: 'Worker did not deliver.', escrow_id: 'esc-xxx'})>"}
```
**Expected:**
- `201 Created`
- Body includes `dispute_id`, `task_id`, `claimant_id`, `respondent_id`, `claim`, `rebuttal`, `status`, `rebuttal_deadline`, `worker_pct`, `ruling_summary`, `escrow_id`, `filed_at`, `rebutted_at`, `ruled_at`, `votes`
- `dispute_id` matches `disp-<uuid4>`
- `status` is `"rebuttal_pending"`
- `rebuttal` is null
- `worker_pct` is null
- `votes` is an empty array

### AUTH-02 Valid platform JWS on POST /disputes/{dispute_id}/rebuttal

**Setup:** Register `platform_agent` in Identity service. File a dispute via AUTH-01. Capture `dispute_id`.
**Action:** `POST /disputes/{dispute_id}/rebuttal` with body:
```json
{"token": "<jws(platform_agent, {action: 'submit_rebuttal', dispute_id: '<dispute_id>', rebuttal: 'The spec was ambiguous.'})>"}
```
**Expected:**
- `200 OK`
- Body includes `dispute_id`, `rebuttal`, `rebutted_at`
- `rebuttal` matches the submitted text
- `rebutted_at` is a valid ISO 8601 timestamp

### AUTH-03 Valid platform JWS on POST /disputes/{dispute_id}/rule

**Setup:** Register `platform_agent` in Identity service. File a dispute and submit a rebuttal. Capture `dispute_id`. Configure mock judge to return a valid vote.
**Action:** `POST /disputes/{dispute_id}/rule` with body:
```json
{"token": "<jws(platform_agent, {action: 'trigger_ruling', dispute_id: '<dispute_id>'})>"}
```
**Expected:**
- `200 OK`
- Body includes `dispute_id`, `status`, `worker_pct`, `ruling_summary`, `ruled_at`, `votes`
- `status` is `"ruled"`
- `worker_pct` is an integer 0-100
- `votes` is a non-empty array

### AUTH-04 Missing `token` field

**Action:** `POST /disputes/file` with body `{"task_id": "t-xxx", "claimant_id": "a-xxx", "respondent_id": "a-xxx", "claim": "...", "escrow_id": "esc-xxx"}` (plain JSON, no `token` field).
**Expected:**
- `400 Bad Request`
- `error = INVALID_JWS`

### AUTH-05 `token` is null

**Action:** `POST /disputes/file` with body `{"token": null}`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_JWS`

### AUTH-06 `token` is not a string

**Action:** Send each of these bodies in separate requests to `POST /disputes/file`:
- `{"token": 12345}`
- `{"token": ["eyJ..."]}`
- `{"token": {"jws": "eyJ..."}}`
- `{"token": true}`
**Expected:** `400`, `error = INVALID_JWS` for each.

### AUTH-07 `token` is empty string

**Action:** `POST /disputes/file` with body `{"token": ""}`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_JWS`

### AUTH-08 Malformed JWS (not three-part compact serialization)

**Action:** Send each of these tokens in separate requests to `POST /disputes/file`:
- `{"token": "not-a-jws-at-all"}`
- `{"token": "only.two-parts"}`
- `{"token": "four.parts.is.wrong"}`
**Expected:** `400`, `error = INVALID_JWS` for each.

### AUTH-09 JWS with tampered payload (signature mismatch)

**Setup:** Register `platform_agent`. Construct a valid JWS, then modify the payload portion after signing.
**Action:** `POST /disputes/file` with `{"token": "<tampered_jws(platform_agent, {action: 'file_dispute', ...})>"}`.
**Expected:**
- `403 Forbidden`
- `error = FORBIDDEN`

### AUTH-10 Non-platform signer on POST /disputes/file

**Setup:** Register `platform_agent` and `rogue_agent` in the Identity service.
**Action:** `POST /disputes/file` with `jws(rogue_agent, {action: "file_dispute", task_id: "t-xxx", claimant_id: "a-alice", respondent_id: "a-bob", claim: "...", escrow_id: "esc-xxx"})`.
**Expected:**
- `403 Forbidden`
- `error = FORBIDDEN`

### AUTH-11 Non-platform signer on POST /disputes/{dispute_id}/rebuttal

**Setup:** Register `platform_agent` and `rogue_agent`. File a dispute via platform JWS. Capture `dispute_id`.
**Action:** `POST /disputes/{dispute_id}/rebuttal` with `jws(rogue_agent, {action: "submit_rebuttal", dispute_id: "<dispute_id>", rebuttal: "..."})`.
**Expected:**
- `403 Forbidden`
- `error = FORBIDDEN`

### AUTH-12 Non-platform signer on POST /disputes/{dispute_id}/rule

**Setup:** Register `platform_agent` and `rogue_agent`. File a dispute and submit a rebuttal via platform JWS. Capture `dispute_id`.
**Action:** `POST /disputes/{dispute_id}/rule` with `jws(rogue_agent, {action: "trigger_ruling", dispute_id: "<dispute_id>"})`.
**Expected:**
- `403 Forbidden`
- `error = FORBIDDEN`

### AUTH-13 Wrong `action` value

**Setup:** Register `platform_agent`.
**Action:** `POST /disputes/file` with `jws(platform_agent, {action: "create_task", task_id: "t-xxx", claimant_id: "a-alice", respondent_id: "a-bob", claim: "...", escrow_id: "esc-xxx"})`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_PAYLOAD`

### AUTH-14 Missing `action` field in payload

**Setup:** Register `platform_agent`.
**Action:** `POST /disputes/file` with `jws(platform_agent, {task_id: "t-xxx", claimant_id: "a-alice", respondent_id: "a-bob", claim: "...", escrow_id: "esc-xxx"})` — payload has no `action` field.
**Expected:**
- `400 Bad Request`
- `error = INVALID_PAYLOAD`

### AUTH-15 Malformed JSON body

**Action:** `POST /disputes/file` with `Content-Type: application/json` and body `{not json`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_JSON`

### AUTH-16 Non-object JSON body

**Action:** `POST /disputes/file` with `Content-Type: application/json` and body `"just a string"`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_JSON`

---

## Category 2: Public Endpoints

### PUB-01 GET /disputes/{dispute_id} requires no authentication

**Setup:** File a dispute via platform JWS. Capture `dispute_id`.
**Action:** `GET /disputes/{dispute_id}` with no Authorization header and no token.
**Expected:**
- `200 OK`
- Full dispute record returned including `dispute_id`, `task_id`, `claimant_id`, `respondent_id`, `claim`, `status`

### PUB-02 GET /disputes requires no authentication

**Action:** `GET /disputes` with no Authorization header and no token.
**Expected:**
- `200 OK`
- Body includes `disputes` array

### PUB-03 GET /health requires no authentication

**Action:** `GET /health` with no Authorization header and no token.
**Expected:**
- `200 OK`
- `status = "ok"`

---

## Category 3: Identity Service Dependency

### IDEP-01 Identity service is down

**Setup:** Configure Court service to point to an Identity service URL that is not running (e.g., `http://localhost:19999`).
**Action:** `POST /disputes/file` with a valid-looking JWS token.
**Expected:**
- `502 Bad Gateway`
- `error = IDENTITY_SERVICE_UNAVAILABLE`

### IDEP-02 Identity service timeout

**Setup:** Start a mock HTTP server on the Identity service port that accepts connections but never responds (simulates timeout). Configure Court service to point to this mock.
**Action:** `POST /disputes/file` with a valid JWS token.
**Expected:**
- `502 Bad Gateway`
- `error = IDENTITY_SERVICE_UNAVAILABLE`

### IDEP-03 Identity service unexpected response

**Setup:** Start a mock HTTP server on the Identity service port that returns `HTTP 500` with body `"Internal Server Error"` (non-JSON) for `POST /agents/verify-jws`. Configure Court service to point to this mock.
**Action:** `POST /disputes/file` with a valid JWS token.
**Expected:**
- `502 Bad Gateway`
- `error = IDENTITY_SERVICE_UNAVAILABLE`

---

## Category 4: Cross-Operation Token Replay

### REPLAY-01 Token signed with action "submit_rebuttal" rejected on POST /disputes/file

**Setup:** Register `platform_agent`.
**Action:** `POST /disputes/file` with `jws(platform_agent, {action: "submit_rebuttal", dispute_id: "disp-xxx", rebuttal: "..."})`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_PAYLOAD`
- A token intended for the rebuttal endpoint cannot be used to file a dispute

### REPLAY-02 Token signed with action "file_dispute" rejected on POST /disputes/{dispute_id}/rule

**Setup:** Register `platform_agent`. File a dispute and submit a rebuttal via platform JWS. Capture `dispute_id`.
**Action:** `POST /disputes/{dispute_id}/rule` with `jws(platform_agent, {action: "file_dispute", task_id: "t-xxx", claimant_id: "a-alice", respondent_id: "a-bob", claim: "...", escrow_id: "esc-xxx"})`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_PAYLOAD`
- A token intended for filing a dispute cannot be used to trigger a ruling

---

## Category 5: Error Precedence

These tests verify that errors are returned in the correct order when multiple error conditions are present simultaneously.

### PREC-01 Content-Type checked before token validation

**Action:** `POST /disputes/file` with `Content-Type: text/plain` and body `{"token": "invalid"}`.
**Expected:**
- `415 Unsupported Media Type`
- `error = UNSUPPORTED_MEDIA_TYPE`
- (NOT `400 INVALID_JWS`)

### PREC-02 Body size checked before token validation

**Action:** `POST /disputes/file` with `Content-Type: application/json` and a body exceeding `request.max_body_size` (e.g., ~2MB).
**Expected:**
- `413 Payload Too Large`
- `error = PAYLOAD_TOO_LARGE`
- (NOT `400 INVALID_JWS`)

### PREC-03 JSON parsing checked before token validation

**Action:** `POST /disputes/file` with `Content-Type: application/json` and body `{not json`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_JSON`
- (NOT `400 INVALID_JWS`)

### PREC-04 Token validation checked before payload validation

**Action:** `POST /disputes/file` with `{"token": 12345}` (not a string).
**Expected:**
- `400 Bad Request`
- `error = INVALID_JWS`
- (NOT `400 INVALID_PAYLOAD`)

### PREC-05 Payload `action` checked before platform signer verification

**Setup:** Register `platform_agent` and `rogue_agent`.
**Action:** `POST /disputes/file` with `jws(rogue_agent, {action: "wrong_action", task_id: "t-xxx", ...})` (wrong action AND non-platform signer).
**Expected:**
- `400 Bad Request`
- `error = INVALID_PAYLOAD`
- (NOT `403 FORBIDDEN`)

### PREC-06 Identity service unavailability checked before payload validation

**Setup:** Configure Court service to point to an Identity service URL that is not running (e.g., `http://localhost:19999`). Generate keypair locally (no registration needed — Identity service is unreachable).
**Action:** `POST /disputes/file` with `jws(local_signer, {action: "wrong_action", task_id: "t-xxx", ...})` — the JWS has a wrong `action` AND the Identity service is down.
**Expected:**
- `502 Bad Gateway`
- `error = IDENTITY_SERVICE_UNAVAILABLE`
- (NOT `400 INVALID_PAYLOAD` — the service cannot decode the payload without verifying the JWS first)

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
**Expected:** `message` never includes stack traces, Identity service URLs, cryptographic details, private key material, or internal diagnostics.

### SEC-AUTH-03 JWS token reuse across services is rejected

**Setup:** Register `platform_agent`. Construct a valid JWS with `action: "escrow_lock"` (Central Bank action).
**Action:** `POST /disputes/file` with the escrow lock JWS.
**Expected:**
- `400 Bad Request`
- `error = INVALID_PAYLOAD`
- A token intended for another service cannot be used to file a dispute

---

## Release Gate Checklist

Authentication is release-ready only if:

1. All tests in this document pass.
2. All tests in `court-service-tests.md` pass when executed with JWS-wrapped requests.
3. No endpoint returns `500` in any test scenario.
4. All failing responses conform to the required error envelope.
5. The Identity service being unavailable never causes the Court service to crash — it returns `502` gracefully.

---

## Coverage Summary

| Category | IDs | Count |
|----------|-----|-------|
| Platform JWS Validation | AUTH-01 to AUTH-16 | 16 |
| Public Endpoints | PUB-01 to PUB-03 | 3 |
| Identity Service Dependency | IDEP-01 to IDEP-03 | 3 |
| Cross-Operation Token Replay | REPLAY-01 to REPLAY-02 | 2 |
| Error Precedence | PREC-01 to PREC-06 | 6 |
| Cross-Cutting Security | SEC-AUTH-01 to SEC-AUTH-03 | 3 |
| **Total** | | **33** |

| Endpoint | Covered By |
|----------|------------|
| `POST /disputes/file` | AUTH-01, AUTH-04 to AUTH-16, IDEP-01 to IDEP-03, REPLAY-01, PREC-01 to PREC-06, SEC-AUTH-01 to SEC-AUTH-03 |
| `POST /disputes/{dispute_id}/rebuttal` | AUTH-02, AUTH-11 |
| `POST /disputes/{dispute_id}/rule` | AUTH-03, AUTH-12, REPLAY-02 |
| `GET /disputes/{dispute_id}` | PUB-01 |
| `GET /disputes` | PUB-02 |
| `GET /health` | PUB-03 |
