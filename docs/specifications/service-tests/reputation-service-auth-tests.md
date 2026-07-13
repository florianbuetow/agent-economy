# Reputation Service — Authentication Test Specification

## Purpose

This document is the release-gate test specification for the Reputation Service's two-tier JWS authentication on `POST /feedback`: Tier 1 (ordinary agent operations, verified via Identity) and Tier 2 (platform / `force_visible` operations, verified locally). It supersedes an earlier version of this document written against a "certificate" model that was never implemented.

- Every negative case has one required status code and one required error code.
- Every failing response must use the standard error envelope.
- Any behavior not listed here is out of scope for release sign-off.

This document covers authentication, authorization, and `force_visible` semantics. Ordinary business-logic tests (feedback validation, visibility, sealed feedback) are covered by `reputation-service-tests.md` and remain valid — every scenario there is executed as a JWS-wrapped request in practice, per this spec's envelope.

---

## Prerequisites

These tests require:

1. A real `PlatformAgent` (Ed25519 keypair) for the local Tier-2 verification path — `PlatformAgent.validate_certificate(token)` verifies a JWS token against the platform's **own** public key (a purely local, no-network Ed25519 check; "certificate" here is the code's name for the JWS token, not a distinct format).
2. Agents with known Ed25519 keypairs for Tier-1 (agent-signed) tokens.
3. For Tier 1: either a real Identity service (integration) or a mock `IdentityClient`/`verify_jws` whose success/failure contract matches the real client (`tests/helpers.py::inject_mock_identity`; `tests/unit/routers/test_identity_error_remapping.py` pins that contract explicitly).
4. For the Identity-down proof (AUTH-15/16 below): a real `PlatformAgent` plus a real `IdentityClient` pointed at an unreachable port — not a mock — so the test proves actual network-failure behavior, not a mock's assumption about it.

---

## Required API Error Contract (Auth Error Codes)

These error codes are added by the authentication layer, on top of the business-logic codes in `reputation-service-tests.md`. All codes are snake_case (verified against `routers/feedback.py`, `services/platform_identity_client.py`, `libs/service-clients/src/service_clients/base.py`).

| Status | Error Code                       | Required When                                                |
|--------|-----------------------------------|--------------------------------------------------------------|
| 400    | `invalid_jws`                    | `token` field is missing, null, non-string, empty, malformed (not a three-part compact serialization), or verification succeeded but the header carried no `kid` |
| 400    | `invalid_payload`                | JWS payload is not a JSON object, is missing `action`, `action` is not `"submit_feedback"`, or `from_agent_id` is missing from the payload |
| 403    | `forbidden`                      | JWS signature verification failed (tampered, unknown/unregistered signer, expired token), or the verified signer does not match `from_agent_id` in the payload |
| 502    | `identity_service_unavailable`   | Tier-1 (agent-op) verification could not reach Identity — connection failure, timeout, or a malformed/incomplete 200 response. Never produced by Tier-2 (platform) verification, which makes no network call. |

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

- `agent_alice`, `agent_bob`, `agent_carol` are agents with known Ed25519 public/private keypairs.
- `platform_agent` is the service's own configured `PlatformAgent`, with a known keypair, registered agent id `platform.agent_id`.
- `jws(signer, payload)` denotes a compact JWS serialization (`libs/service-auth/src/service_auth/signing.py::create_jws`) with header `{"alg": "EdDSA", "typ": "JWT", "kid": "<signer.agent_id>"}` (plus `iat`/`exp` when `token_ttl_seconds` is configured), canonical payload (`json.dumps(sort_keys=True, separators=(",", ":"))`), and a genuine Ed25519 signature.
- `tampered_jws(signer, payload)` denotes a JWS where the payload has been altered after signing (signature mismatch).
- Agent IDs use the format `a-<uuid4>`.
- Task IDs use the format `t-<uuid4>`.
- All valid JWS payloads include `"action": "submit_feedback"` unless explicitly testing invalid payloads.
- A "valid feedback JWS" means: `jws(alice, {action: "submit_feedback", task_id: "t-...", from_agent_id: alice.agent_id, to_agent_id: bob.agent_id, category: "delivery_quality", rating: "satisfied"})`.
- A "force_visible feedback JWS" means: `jws(platform_agent, {action: "submit_feedback", task_id: "t-...", from_agent_id: platform_agent.agent_id, to_agent_id: <poster or worker>.agent_id, category: "spec_quality" | "delivery_quality", rating: ...})` — signed by the platform's own key, with `from_agent_id` set to the platform's own id.

---

## Category 1: JWS Token Validation (`POST /feedback`)

### AUTH-01 Valid JWS submits feedback successfully

**Setup:** Create `agent_alice` and `agent_bob` with known Ed25519 keypairs.
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
- `error = invalid_jws`

### AUTH-03 `token` is null

**Action:** `POST /feedback` with body `{"token": null}`.
**Expected:**
- `400 Bad Request`
- `error = invalid_jws`

### AUTH-04 `token` is not a string

**Action:** Send each of these bodies in separate requests:
- `{"token": 12345}`
- `{"token": ["eyJ..."]}`
- `{"token": {"jws": "eyJ..."}}`
- `{"token": true}`
**Expected:** `400`, `error = invalid_jws` for each.

### AUTH-05 `token` is empty string

**Action:** `POST /feedback` with body `{"token": ""}`.
**Expected:**
- `400 Bad Request`
- `error = invalid_jws`

### AUTH-06 Malformed JWS (not three-part compact serialization)

**Action:** Send each of these tokens in separate requests:
- `{"token": "not-a-jws-at-all"}`
- `{"token": "only.two-parts"}`
- `{"token": "four.parts.is.wrong"}`
**Expected:** `400`, `error = invalid_jws` for each.

### AUTH-07 JWS with tampered payload (signature mismatch)

**Setup:** Create `agent_alice` with a known keypair. Construct a valid JWS, then modify the payload portion after signing.
**Action:** `POST /feedback` with `{"token": "<tampered_jws>"}`.
**Expected:**
- `403 Forbidden`
- `error = forbidden`

### AUTH-08 JWS signed by unknown agent (agent tier)

**Setup:** Generate a fresh Ed25519 keypair not registered with Identity.
**Action:** `POST /feedback` with a JWS signed by the unknown keypair (`kid` != the platform's own agent id, so this routes to Identity).
**Expected:**
- `403 Forbidden`
- `error = forbidden`

### AUTH-08b JWS with expired token is rejected

**Setup:** Create `agent_alice` with a known keypair and a configured `token_ttl_seconds` so `iat`/`exp` are stamped into the header. Freeze the signing clock, sign a token, then advance a verification-time clock past `exp`.
**Action:** `POST /feedback` with the expired token.
**Expected:**
- `403 Forbidden`
- `error = forbidden`
- (Verified against `service_auth.signing.TokenExpiredError`, which both `IdentityClient`'s backing verifier and `PlatformJwsVerifier` map to `valid: False` → `403`, the same as a bad signature — not a distinct error code.)

---

## Category 2: JWS Payload Validation

### AUTH-09 Missing `action` in payload

**Setup:** Create `agent_alice` and `agent_bob` with known keypairs.
**Action:** `POST /feedback` with `jws(alice, {task_id: "t-xxx", from_agent_id: alice.id, to_agent_id: bob.id, category: "delivery_quality", rating: "satisfied"})` — payload has no `action` field.
**Expected:**
- `400 Bad Request`
- `error = invalid_payload`

### AUTH-10 Wrong `action` value

**Setup:** Create `agent_alice` and `agent_bob` with known keypairs.
**Action:** `POST /feedback` with `jws(alice, {action: "escrow_lock", task_id: "t-xxx", from_agent_id: alice.id, to_agent_id: bob.id, category: "delivery_quality", rating: "satisfied"})`.
**Expected:**
- `400 Bad Request`
- `error = invalid_payload`

### AUTH-11 `action` is null

**Setup:** Create `agent_alice` and `agent_bob` with known keypairs.
**Action:** `POST /feedback` with `jws(alice, {action: null, task_id: "t-xxx", from_agent_id: alice.id, to_agent_id: bob.id, ...})`.
**Expected:**
- `400 Bad Request`
- `error = invalid_payload`

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
- `error = forbidden`

### AUTH-14 Signer impersonates non-existent agent

**Setup:** Create `agent_alice` and `agent_bob` with known keypairs.
**Action:** Alice signs a JWS with `from_agent_id: "a-nonexistent-uuid"`.
`POST /feedback` with `jws(alice, {action: "submit_feedback", from_agent_id: "a-nonexistent-uuid", to_agent_id: bob.id, ...})`.
**Expected:**
- `403 Forbidden`
- `error = forbidden`

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

Order verified against `routers/feedback.py::submit_feedback_endpoint` and `core/middleware.py::RequestValidationMiddleware`, which runs ahead of the router.

### PREC-01 Content-Type checked before token validation

**Action:** `POST /feedback` with `Content-Type: text/plain` and body `{"token": "invalid"}`.
**Expected:**
- `415 Unsupported Media Type`
- `error = unsupported_media_type`
- (NOT `400 invalid_jws`)

### PREC-02 Body size checked before token validation

**Action:** `POST /feedback` with `Content-Type: application/json` and a ~2MB body.
**Expected:**
- `413 Payload Too Large`
- `error = payload_too_large`
- (NOT `400 invalid_jws`)

### PREC-03 JSON parsing checked before token validation

**Action:** `POST /feedback` with `Content-Type: application/json` and body `{not json`.
**Expected:**
- `400 Bad Request`
- `error = invalid_json`
- (NOT `400 invalid_jws`)

### PREC-04 Token validation checked before verification

**Action:** `POST /feedback` with `{"token": 12345}` (not a string).
**Expected:**
- `400 Bad Request`
- `error = invalid_jws`
- (NOT `403 forbidden`, NOT `400 invalid_payload`)

### PREC-05 Verification checked before payload-shape / kid-presence checks

**Action:** `POST /feedback` with a token whose signature does not verify (any tier).
**Expected:**
- `403 Forbidden`
- `error = forbidden`
- (NOT `400 invalid_jws` for a missing `kid`, NOT `400 invalid_payload`) — verification runs first; the missing-`kid` check only applies to tokens that *passed* verification but returned an empty `agent_id`.

### PREC-06 Payload `action` checked before signer matching

**Setup:** Create `agent_alice` and `agent_bob` with known keypairs.
**Action:** Alice signs a JWS with `{action: "wrong_action", from_agent_id: bob.id, ...}` (wrong action AND signer mismatch).
**Expected:**
- `400 Bad Request`
- `error = invalid_payload`
- (NOT `403 forbidden`)

### PREC-07 Signer matching checked before feedback field validation

**Setup:** Create `agent_alice`, `agent_bob`, and `agent_carol` with known keypairs.
**Action:** Alice signs a JWS with `{action: "submit_feedback", from_agent_id: carol.id, rating: "invalid_value", ...}` (signer mismatch AND invalid rating).
**Expected:**
- `403 Forbidden`
- `error = forbidden`
- (NOT `400 invalid_rating`)

---

## Category 6: Existing Validations Through JWS

These tests verify that existing feedback validation rules still apply when the feedback data is delivered inside a JWS payload instead of a plain JSON body.

### VJWS-01 Missing feedback fields in JWS payload

**Setup:** Create `agent_alice` and `agent_bob` with known keypairs.
**Action:** Alice signs a JWS with `{action: "submit_feedback", from_agent_id: alice.id}` — missing `to_agent_id`, `task_id`, `category`, `rating`.
**Expected:**
- `400 Bad Request`
- `error = missing_field`

### VJWS-02 Invalid rating in JWS payload

**Setup:** Create `agent_alice` and `agent_bob` with known keypairs.
**Action:** Alice signs a JWS with `{action: "submit_feedback", ..., rating: "excellent"}`.
**Expected:**
- `400 Bad Request`
- `error = invalid_rating`

### VJWS-03 Invalid category in JWS payload

**Setup:** Create `agent_alice` and `agent_bob` with known keypairs.
**Action:** Alice signs a JWS with `{action: "submit_feedback", ..., category: "timeliness"}`.
**Expected:**
- `400 Bad Request`
- `error = invalid_category`

### VJWS-04 Self-feedback in JWS payload

**Setup:** Create `agent_alice` with a known keypair.
**Action:** Alice signs a JWS with `{action: "submit_feedback", from_agent_id: alice.id, to_agent_id: alice.id, ...}`.
**Expected:**
- `400 Bad Request`
- `error = self_feedback`

### VJWS-05 Comment too long in JWS payload

**Setup:** Create `agent_alice` and `agent_bob` with known keypairs.
**Action:** Alice signs a JWS with a comment of 257 characters (one over the configured limit).
**Expected:**
- `400 Bad Request`
- `error = comment_too_long`

### VJWS-06 Duplicate feedback via JWS

**Setup:** Create `agent_alice` and `agent_bob` with known keypairs. Submit feedback via JWS for (task_1, alice→bob).
**Action:** Submit identical feedback via JWS again for (task_1, alice→bob).
**Expected:**
- `409 Conflict`
- `error = feedback_exists`

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
- Exactly one `409 Conflict` with `error = feedback_exists`

---

## Category 7: Two-Tier Auth — Identity Outage

Verified against `tests/unit/routers/test_two_tier_feedback_auth.py`, which uses a **real** `PlatformAgent` and a **real** `IdentityClient` pointed at an unreachable port (`http://127.0.0.1:1`) — not mocks — to prove actual network-failure behavior.

### AUTH-15 Platform-signed feedback succeeds while Identity is down

**Setup:** Configure a real `PlatformAgent`. Point `state.identity_client` at an unreachable URL.
**Action:** `POST /feedback` with `jws(platform_agent, {action: "submit_feedback", task_id: "task-1", from_agent_id: platform_agent.id, to_agent_id: "a-worker", category: "delivery_quality", rating: "satisfied", comment: "Court ruling feedback"})`.
**Expected:**
- `201 Created`
- `from_agent_id` matches `platform_agent.agent_id`
- `to_agent_id` matches `"a-worker"`
- `visible` is `true`
- No call to Identity occurs — local verification is the entire auth path for this token.

### AUTH-16 Ordinary agent feedback fails cleanly (not silently bypassed) while Identity is down

**Setup:** Same as AUTH-15. Generate a fresh Ed25519 keypair for `agent_alice` (not the platform key).
**Action:** `POST /feedback` with `jws(alice, {action: "submit_feedback", task_id: "task-2", from_agent_id: alice.id, to_agent_id: "a-bob", category: "delivery_quality", rating: "satisfied", comment: "nice"})`.
**Expected:**
- `502 Bad Gateway`
- `error = identity_service_unavailable`
- The request is rejected outright — it is never silently routed to the local platform verifier or otherwise accepted without real verification.

---

## Category 8: `force_visible` Semantics (Court / Platform-Generated Feedback)

Verified against `tests/unit/routers/test_gap_a6_force_visible_semantics.py`, which replays the exact payload shape `court_service.ruling_orchestrator._record_feedback` sends.

### AUTH-17 Platform feedback with no counterpart is immediately visible

**Setup:** Configure a mock/real platform agent with id `PLATFORM_ID`.
**Action:** `POST /feedback` with `jws(platform, {action: "submit_feedback", task_id: "task-fv-1", from_agent_id: PLATFORM_ID, to_agent_id: <poster>, category: "spec_quality", rating: "dissatisfied", comment: "Court ruling: spec was ambiguous"})` — no reverse pair exists.
**Expected:**
- `201 Created`
- `visible` is `true`
- `from_agent_id` equals `PLATFORM_ID`

### AUTH-18 Control: the same shape from an ordinary agent stays sealed

**Setup:** Same task-shape as AUTH-17 but signed and sent by an ordinary agent (`from_agent_id` = a worker, not the platform).
**Action:** `POST /feedback` with `jws(worker, {action: "submit_feedback", ..., from_agent_id: worker.id, to_agent_id: poster.id, category: "spec_quality"})` — no reverse pair.
**Expected:**
- `201 Created`
- `visible` is `false`

This is the control that makes AUTH-17 meaningful: immediate visibility is specific to a platform-signed, self-`from_agent_id` submission, not to "first feedback on a task" in general.

### AUTH-19 Court ruling category convention (documented, not enforced by Reputation)

**Setup:** Configure the platform agent with id `PLATFORM_ID`. Two agents: `POSTER_ID` (claimant), `WORKER_ID` (respondent).
**Action:**
1. `POST /feedback` with `jws(platform, {action: "submit_feedback", task_id: "task-fv-2", from_agent_id: PLATFORM_ID, to_agent_id: POSTER_ID, category: "spec_quality", rating: "satisfied", comment: "Ruling: spec was clear"})`
2. `POST /feedback` with `jws(platform, {action: "submit_feedback", task_id: "task-fv-2", from_agent_id: PLATFORM_ID, to_agent_id: WORKER_ID, category: "delivery_quality", rating: "dissatisfied", comment: "Ruling: delivery fell short"})`
**Expected:**
- Both `201 Created`, both `visible: true` immediately (no reveal-timeout wait)
- Step 1: `to_agent_id == POSTER_ID`, `category == "spec_quality"`
- Step 2: `to_agent_id == WORKER_ID`, `category == "delivery_quality"`
- `GET /feedback/task/task-fv-2` returns both entries

**Note:** this pins the *convention* court currently follows (`spec_quality → claimant/poster`, `delivery_quality → respondent/worker`) as observed from Reputation's side. Reputation itself places no constraint on which category maps to which `to_agent_id` for a `force_visible` submission — it stores whatever pair it is given, immediately visible. If court's convention changes, this test's expected `(category, to_agent_id)` pairing must change with it; Reputation would accept the new pairing without any code change.

### AUTH-20 A force-visible write does not disturb an unrelated sealed pair on the same task

**Setup:** On `task-fv-3`: submit ordinary, one-sided feedback `worker → poster`, `category: spec_quality` (stays sealed, no counterpart). Capture its `feedback_id`.
**Action:** Submit a platform force-visible write on the same task, a disjoint pair: `platform → poster`, `category: spec_quality`.
**Expected:**
- The platform write returns `201`, `visible: true`.
- The unrelated sealed pair remains sealed: `GET /feedback/{sealed_id}` still returns `404`, and it is absent from `GET /feedback/task/task-fv-3`'s results.
- The platform's own feedback IS present in the task listing.

**Architectural note:** this holds because the reverse-pair lookup in the gateway's atomic reveal (`ReputationWriter._lookup_reverse_feedback_id`) is scoped to the exact `(task_id, from_agent_id, to_agent_id)` triple, not just `task_id` — a platform write's `from_agent_id` is always the platform's own id, never a real task participant, so it can never accidentally match as the "reverse" of an ordinary agent's sealed submission.

---

## Category 9: Cross-Cutting Security Assertions

### SEC-AUTH-01 Error envelope consistency for auth errors

**Action:** Trigger each auth error code at least once (`invalid_jws`, `invalid_payload`, `forbidden`, `identity_service_unavailable`).
**Expected:** All responses have exactly:
- top-level `error` (string)
- top-level `message` (string)
- top-level `details` (object)

### SEC-AUTH-02 No internal error leakage in auth failures

**Action:** Trigger `invalid_jws`, `forbidden` errors.
**Expected:** `message` never includes stack traces, cryptographic details, private key material, or internal diagnostics (verified: `test_identity_error_remapping.py::test_forbidden_message_is_generic` asserts the message contains none of "Agent not found", "keystore", or a filesystem path).

### SEC-AUTH-03 JWS token reuse across actions is rejected

**Setup:** Create `agent_alice` and `agent_bob` with known keypairs. Construct a valid JWS with `action: "escrow_lock"` (central-bank action).
**Action:** `POST /feedback` with the escrow lock JWS.
**Expected:**
- `400 Bad Request`
- `error = invalid_payload`
- A token intended for another service/operation cannot be used to submit feedback

---

## Release Gate Checklist

Authentication is release-ready only if:

1. All tests in this document pass.
2. All tests in `reputation-service-tests.md` pass when executed with JWS-wrapped requests.
3. No endpoint returns `500` in any test scenario.
4. All failing responses conform to the required error envelope.
5. Local (Tier-2) verification via `PlatformJwsVerifier` never causes the Reputation service to crash — invalid or unregistered tokens return `403` gracefully, and Identity being completely unreachable never affects Tier-2 traffic (AUTH-15).
6. Tier-1 (agent-op) verification fails cleanly with `502 identity_service_unavailable` when Identity is unreachable — never silently bypassed to local verification (AUTH-16).

---

## Coverage Summary

| Category | IDs | Count |
|----------|-----|-------|
| JWS Token Validation | AUTH-01 to AUTH-08b | 9 |
| JWS Payload Validation | AUTH-09 to AUTH-11 | 3 |
| Authorization (Signer Matching) | AUTH-12 to AUTH-14 | 3 |
| GET Endpoints Remain Public | PUB-01 to PUB-04 | 4 |
| Error Precedence | PREC-01 to PREC-07 | 7 |
| Existing Validations Through JWS | VJWS-01 to VJWS-09 | 9 |
| Two-Tier Auth — Identity Outage | AUTH-15 to AUTH-16 | 2 |
| `force_visible` Semantics | AUTH-17 to AUTH-20 | 4 |
| Cross-Cutting Security | SEC-AUTH-01 to SEC-AUTH-03 | 3 |
| **Total** | | **44** |

| Endpoint | Covered By |
|----------|------------|
| `POST /feedback` | AUTH-01 to AUTH-20, PREC-01 to PREC-07, VJWS-01 to VJWS-09, SEC-AUTH-01 to SEC-AUTH-03 |
| `GET /feedback/{feedback_id}` | PUB-01 |
| `GET /feedback/task/{task_id}` | PUB-02, VJWS-07, AUTH-19, AUTH-20 |
| `GET /feedback/agent/{agent_id}` | PUB-03 |
| `GET /health` | PUB-04 |
