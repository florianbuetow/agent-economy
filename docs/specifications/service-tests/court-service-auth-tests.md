# Court Service — Authentication Test Specification

## Purpose

This document is the release-gate test specification for JWS-based authentication on the Court service's three POST endpoints (`POST /disputes/file`, `POST /disputes/{dispute_id}/rebuttal`, `POST /disputes/{dispute_id}/rule`). All GET endpoints are public and require no authentication.

It is intentionally strict and unambiguous:

- Every negative case has one required status code and one required error code.
- Every failing response must use the standard error envelope.
- Any behavior not listed here is out of scope for release sign-off.

This document covers authentication and authorization concerns — including the local (non-Identity-HTTP) verification model, the `kid == platform` check, error precedence, and token replay. Business logic (dispute filing, rebuttal submission, ruling, judge evaluation, side effects) is covered by `court-service-tests.md`.

Verified against `services/court/tests/unit/routers/test_disputes.py` (`TestPlatformJWS`, `TestPublicEndpoints`, `TestIdentityDependency`, `TestTokenReplay`, `TestErrorPrecedence`, `TestAuthSecurity`) and `services/court/tests/unit/routers/test_wp06_platform_kid.py`.

---

## Prerequisites

1. A `PlatformAgent` instantiated with valid Ed25519 keys — certificate verification is performed **locally**, no Identity service round trip.
2. The Court service's platform agent id matches the `kid` used for valid tokens in these tests.
3. A mock Task Board returning valid task data for `GET /tasks/{task_id}` (required by `POST /disputes/file`).

---

## Required API Error Contract (Auth-Related Error Codes)

| Status | Error Code                       | Required When                                                |
|--------|-----------------------------------|--------------------------------------------------------------|
| 400    | `invalid_jws`                    | `token` field is missing, null, non-string, empty, or malformed (not a three-part compact serialization) |
| 400    | `invalid_payload`                | JWS payload is missing `action`, `action` does not match the expected value for the target endpoint, or a payload `dispute_id` doesn't match the URL |
| 403    | `forbidden`                      | Local signature verification fails (tampered, unknown signer), or `kid != platform_agent.agent_id` |
| 502    | `identity_service_unavailable`   | Local certificate verification raises an unexpected (non-signature) exception — the name is retained for historical continuity; **no Identity HTTP call is made on any Court endpoint** |

All failing responses use the standard 3-field error envelope:

```json
{
  "error": "error_code",
  "message": "Human-readable description",
  "details": {}
}
```

---

## Test Data Conventions

- `platform_agent` — Ed25519 keypair matching the Court's configured platform identity. Verified **locally**; there is no Identity mock to configure for these tests (a prior draft of this spec described an "Identity service mock" — that description no longer applies).
- `rogue_agent` — a separate keypair whose `agent_id` does **not** match the platform agent. A token "signed" by `rogue_agent` fails local signature verification against the platform's public key (`InvalidSignature`), not merely a `kid` mismatch.
- `jws(signer, payload)` — RFC 7515 compact serialization, header `{"alg":"EdDSA","kid":"<signer.agent_id>"}`, valid Ed25519 signature.
- `tampered_jws(signer, payload)` — payload altered after signing (signature mismatch).
- ID formats: `a-<uuid4>` (agents), `t-<uuid4>` (tasks), `disp-<uuid4>` (disputes), `esc-<uuid4>` (escrow).
- A "valid file_dispute JWS" = `jws(platform_agent, {action: "file_dispute", task_id, claimant_id, respondent_id, claim, escrow_id})`.
- A "valid submit_rebuttal JWS" = `jws(platform_agent, {action: "submit_rebuttal", dispute_id, rebuttal})`.
- A "valid trigger_ruling JWS" = `jws(platform_agent, {action: "trigger_ruling", dispute_id})`.
- To test the rebuttal/rule endpoints, a dispute must first be filed via a valid `file_dispute` JWS.
- **Token expiry is documented but not covered by dedicated acceptance tests in this release.** Since WP-02 (Q-10), tokens signed via `agents/config.yaml`'s `signing.token_ttl_seconds: 300` carry `iat`/`exp` header claims, and an expired token raises `TokenExpiredError` inside local verification — which, because `TokenExpiredError` is not a `ValueError` subclass, surfaces as `502 identity_service_unavailable` rather than `403 forbidden`. No test in `services/court/tests/` currently exercises this path with a real expired token; it is asserted here as documentation only (see `court-service-auth-specs.md`'s "Token Expiry" section). Do not claim a passing test for it that does not exist.

---

## Category 1: Platform JWS Validation

### AUTH-01 Valid platform JWS on POST /disputes/file

**Action:** `POST /disputes/file` with a valid `file_dispute` JWS.
**Expected:** `201`; body includes `dispute_id`, `task_id`, `claimant_id`, `respondent_id`, `claim`, `rebuttal` (null), `status` (`"rebuttal_pending"`), `rebuttal_deadline`, `worker_pct` (null), `ruling_summary` (null), `escrow_id`, `filed_at`, `rebutted_at` (null), `ruled_at` (null), `votes` (`[]`).

### AUTH-02 Valid platform JWS on POST /disputes/{dispute_id}/rebuttal

**Setup:** File a dispute (AUTH-01).
**Expected:** `200`; body includes `dispute_id`, `rebuttal`, `rebutted_at` (ISO 8601), matching the submitted text.

### AUTH-03 Valid platform JWS on POST /disputes/{dispute_id}/rule

**Setup:** File and submit a rebuttal; configure a mock judge to return a valid vote.
**Expected:** `200`; body includes `dispute_id`, `status` (`"ruled"`), `worker_pct` (int 0–100), `ruling_summary`, `ruled_at`, `votes` (non-empty).

### AUTH-04 Missing `token` field
**Action:** body with no `token` field.
**Expected:** `400`, `error = invalid_jws`.

### AUTH-05 `token` is null
**Expected:** `400`, `error = invalid_jws`.

### AUTH-06 `token` is not a string

**Action:** `{"token": 12345}`, `{"token": ["eyJ..."]}`, `{"token": {"jws": "eyJ..."}}`, `{"token": true}` in separate requests.
**Expected:** `400`, `error = invalid_jws` for each.

### AUTH-07 `token` is empty string
**Expected:** `400`, `error = invalid_jws`.

### AUTH-08 Malformed JWS (not three-part compact serialization)

**Action:** `"not-a-jws-at-all"`, `"only.two-parts"`, `"four.parts.is.wrong"` in separate requests.
**Expected:** `400`, `error = invalid_jws` for each.

### AUTH-09 JWS with tampered payload (signature mismatch)
**Expected:** `403`, `error = forbidden`.

### AUTH-10 Non-platform signer on POST /disputes/file

**Action:** `jws(rogue_agent, {action: "file_dispute", ...})`.
**Expected:** `403`, `error = forbidden`. (Local `validate_certificate()` rejects it — signed with the wrong private key entirely, not merely the wrong header.)

### AUTH-11 Non-platform signer on POST /disputes/{dispute_id}/rebuttal
**Expected:** `403`, `error = forbidden`.

### AUTH-12 Non-platform signer on POST /disputes/{dispute_id}/rule
**Expected:** `403`, `error = forbidden`.

### AUTH-13 Wrong `action` value

**Action:** `jws(platform_agent, {action: "create_task", ...})` on `POST /disputes/file`.
**Expected:** `400`, `error = invalid_payload`.

### AUTH-14 Missing `action` field in payload
**Expected:** `400`, `error = invalid_payload`.

### AUTH-15 Malformed JSON body
**Action:** `Content-Type: application/json`, body `{not json`.
**Expected:** `400`, `error = invalid_json`.

### AUTH-16 Non-object JSON body
**Action:** body `"just a string"`.
**Expected:** `400`, `error = invalid_json`.

---

## Category 2: The `kid == platform` Check (Correct Signature, Wrong Header)

Distinct from AUTH-10/11/12: here the signature verifies successfully (correctly signed by the platform's own key), but the JWS protected-header `kid` names a different agent — `_extract_kid(token) != platform_agent.agent_id`.

### KID-01 File dispute: valid signature, wrong header kid

**Setup:** Sign the payload's certificate correctly (so `validate_certificate()` succeeds), but set the JWS protected header's `kid` to a non-platform value.
**Expected:** `403`, `error = forbidden`.

### KID-02 Submit rebuttal: valid signature, wrong header kid
**Expected:** `403`, `error = forbidden`.

### KID-03 Trigger ruling: valid signature, wrong header kid
**Expected:** `403`, `error = forbidden`.

### KID-04 Sanity: correct kid is still accepted (mutation anchor)

**Action:** `POST /disputes/file` with correct signature and `kid == platform_agent.agent_id`.
**Expected:** `201`. This test exists specifically to prove KID-01..03 aren't vacuously passing (i.e., that the `kid` check doesn't reject everything).

---

## Category 3: Local Verification — Unexpected-Failure Path

Tests the `identity_service_unavailable` (502) branch: `validate_certificate()` raising something other than `InvalidSignature`/`ValueError`. Despite the code name, none of these involve an actual Identity service HTTP call — verification is fully local.

### IDEP-01 Unexpected ConnectionError during local verification
**Setup:** Force `validate_certificate()` to raise `ConnectionError`.
**Expected:** `502`, `error = identity_service_unavailable`.

### IDEP-02 Unexpected TimeoutError during local verification
**Expected:** `502`, `error = identity_service_unavailable`.

### IDEP-03 Unexpected RuntimeError during local verification
**Expected:** `502`, `error = identity_service_unavailable`.

---

## Category 4: Public Endpoints

### PUB-01 GET /disputes/{dispute_id} requires no authentication
**Setup:** File a dispute.
**Action:** `GET /disputes/{dispute_id}` with no Authorization header and no token.
**Expected:** `200`; full dispute record.

### PUB-02 GET /disputes requires no authentication
**Expected:** `200`; body includes `disputes` array.

### PUB-03 GET /health requires no authentication
**Expected:** `200`; `status = "ok"`.

---

## Category 5: Cross-Operation / Cross-Service Token Replay

### REPLAY-01 Token signed with action "submit_rebuttal" rejected on POST /disputes/file

**Action:** `jws(platform_agent, {action: "submit_rebuttal", dispute_id: "disp-xxx", rebuttal: "..."})` sent to `/disputes/file`.
**Expected:** `400`, `error = invalid_payload`.

### REPLAY-02 Token signed with action "file_dispute" rejected on POST /disputes/{dispute_id}/rule

**Setup:** File and submit a rebuttal.
**Action:** `jws(platform_agent, {action: "file_dispute", ...})` sent to `/disputes/{dispute_id}/rule`.
**Expected:** `400`, `error = invalid_payload`.

### REPLAY-03 A foreign-service action (e.g. Central Bank's `escrow_lock`) is rejected on POST /disputes/file

**Action:** `jws(platform_agent, {action: "escrow_lock", ...})` sent to `/disputes/file`.
**Expected:** `400`, `error = invalid_payload`. This is a general cross-service replay guard (any unrecognized `action` string is rejected the same way) — it does **not** imply the Court ever legitimately receives Central-Bank-flavored tokens; the Court never calls the Central Bank in either direction (see `court-service-specs.md`).

---

## Category 6: Error Precedence

Verifies the order errors are returned in when multiple conditions apply simultaneously.

### PREC-01 Content-Type checked before token validation

**Action:** `Content-Type: text/plain`, body `{"token": "invalid"}`.
**Expected:** `415`, `error = unsupported_media_type` (**not** `400 invalid_jws`).

### PREC-02 Body size checked before token validation

**Action:** `Content-Type: application/json`, body exceeding `request.max_body_size` (~2 MB).
**Expected:** `413`, `error = payload_too_large` (**not** `400 invalid_jws`).

### PREC-03 JSON parsing checked before token validation

**Action:** `Content-Type: application/json`, body `{not json`.
**Expected:** `400`, `error = invalid_json` (**not** `400 invalid_jws`).

### PREC-04 Token type checked before payload validation

**Action:** `{"token": 12345}` (not a string).
**Expected:** `400`, `error = invalid_jws` (**not** `400 invalid_payload`).

### PREC-05 Signature/kid verification checked before `action` validation

**Action:** `jws(rogue_agent, {action: "wrong_action", task_id: "t-xxx", ...})` — a token with **both** a non-platform signer **and** a wrong `action`.
**Expected:** `403`, `error = forbidden` (**not** `400 invalid_payload`).

> **Correction from an earlier draft:** a previous version of this scenario asserted the opposite — `400 invalid_payload`, not `403` — on the theory that `action` is validated first. That does not match the code: `verify_platform_token` runs local signature verification (and the `kid` check) before the router ever calls `require_action`. With local verification, the signature check *is* the signer check, and a rogue-signed token fails it regardless of what `action` the payload claims. See `services/court/tests/unit/routers/test_disputes.py::test_prec_05_action_checked_before_signer` — despite its old name, its docstring and assertion (`403 forbidden`) now encode this corrected order.

### PREC-06 Local-verification transport error checked before payload validation

**Setup:** Force `validate_certificate()` to raise a generic `ConnectionError`.
**Action:** `POST /disputes/file` with `action: "wrong_action"` in the payload (an unrelated defect that would otherwise be `400 invalid_payload`).
**Expected:** `502`, `error = identity_service_unavailable` — the verification-layer error takes precedence over payload-content validation, which never runs.

---

## Category 7: Cross-Cutting Security Assertions

### SEC-AUTH-01 Error envelope consistency for auth errors

**Action:** Trigger `invalid_jws`, `invalid_payload`, `forbidden` at least once each.
**Expected:** every response has top-level `error` (string), `message` (string), `details` (object).

### SEC-AUTH-02 No internal error leakage in auth failures

**Action:** Trigger `invalid_jws`, `forbidden`, `invalid_payload`.
**Expected:** `message` never includes stack traces, cryptographic key material, or internal diagnostics.

### SEC-AUTH-03 Foreign-action token reuse is rejected

Duplicate of REPLAY-03, retained under its historical ID for continuity with prior CI dashboards: a JWS signed for a Central-Bank-style `escrow_lock` action is rejected on `/disputes/file` with `400 invalid_payload`. Do not read this as evidence the Court accepts or issues escrow-related tokens — it never does.

---

## Release Gate Checklist

1. All tests in this document pass.
2. All tests in `court-service-tests.md` pass when executed with JWS-wrapped requests.
3. No endpoint returns `500` in any test scenario.
4. All failing responses conform to the required 3-field error envelope.
5. No test in this document asserts an `INVALID_PANEL_SIZE` HTTP response — panel-size validation is a config-load-time failure only (see `court-service-tests.md` Category 9).

---

## Coverage Summary

| Category | IDs | Count |
|----------|-----|-------|
| Platform JWS Validation | AUTH-01 to AUTH-16 | 16 |
| `kid == platform` Check | KID-01 to KID-04 | 4 |
| Local Verification Unexpected-Failure | IDEP-01 to IDEP-03 | 3 |
| Public Endpoints | PUB-01 to PUB-03 | 3 |
| Cross-Operation / Cross-Service Replay | REPLAY-01 to REPLAY-03 | 3 |
| Error Precedence | PREC-01 to PREC-06 | 6 |
| Cross-Cutting Security | SEC-AUTH-01 to SEC-AUTH-03 | 3 |
| **Total** | | **38** |

| Endpoint | Covered By |
|----------|------------|
| `POST /disputes/file` | AUTH-01, AUTH-04 to AUTH-16, KID-01, KID-04, IDEP-01 to IDEP-03, REPLAY-01, REPLAY-03, PREC-01 to PREC-06, SEC-AUTH-01 to SEC-AUTH-03 |
| `POST /disputes/{dispute_id}/rebuttal` | AUTH-02, AUTH-11, KID-02 |
| `POST /disputes/{dispute_id}/rule` | AUTH-03, AUTH-12, KID-03, REPLAY-02 |
| `GET /disputes/{dispute_id}` | PUB-01 |
| `GET /disputes` | PUB-02 |
| `GET /health` | PUB-03 |
