# Court Service — Production Release Test Specification

## Purpose

This document is the release-gate test specification for the Court Service.
It is intentionally strict and unambiguous:

- Every negative case has one required status code and one required error code.
- Every failing response must use the standard error envelope.
- Any behavior not listed here is out of scope for release sign-off.

This document covers the full surface: dispute filing, rebuttals, judge panel ruling, escrow side-effects, reputation feedback, lifecycle enforcement, and security assertions.

---

## Required API Error Contract (Normative for Release)

All failing responses must be JSON in this format:

```json
{
  "error": "ERROR_CODE",
  "message": "Human-readable description",
  "details": {}
}
```

Required status/error mappings:

| Status | Error Code                          | Required When |
|--------|-------------------------------------|---------------|
| 400    | `INVALID_JWS`                      | JWS token is malformed, missing, empty, null, or not a string |
| 400    | `INVALID_JSON`                     | Request body is malformed JSON |
| 400    | `INVALID_PAYLOAD`                  | Required fields missing from JWS payload, `action` does not match the endpoint, or field constraints violated (e.g., claim too long, empty rebuttal) |
| 400    | `INVALID_PANEL_SIZE`               | Panel size is even, less than 1, or does not match configured judges count (startup error) |
| 403    | `FORBIDDEN`                        | JWS signature is invalid, or signer is not the platform agent |
| 404    | `DISPUTE_NOT_FOUND`                | No dispute exists with the given `dispute_id` |
| 404    | `TASK_NOT_FOUND`                   | Task does not exist in the Task Board (when filing a dispute) |
| 405    | `METHOD_NOT_ALLOWED`               | Unsupported HTTP method on a defined route |
| 409    | `DISPUTE_ALREADY_EXISTS`           | A dispute has already been filed for this `task_id` |
| 409    | `DISPUTE_ALREADY_RULED`            | Dispute already has a ruling — cannot rule again |
| 409    | `REBUTTAL_ALREADY_SUBMITTED`       | Worker has already submitted a rebuttal for this dispute |
| 409    | `INVALID_DISPUTE_STATUS`           | The requested operation is not valid for the dispute's current status |
| 413    | `PAYLOAD_TOO_LARGE`               | Request body exceeds configured `request.max_body_size` |
| 415    | `UNSUPPORTED_MEDIA_TYPE`           | `Content-Type` is not `application/json` for JSON endpoints |
| 502    | `IDENTITY_SERVICE_UNAVAILABLE`     | Cannot reach the Identity service for JWS verification |
| 502    | `TASK_BOARD_UNAVAILABLE`           | Cannot reach the Task Board to fetch task data or record ruling |
| 502    | `CENTRAL_BANK_UNAVAILABLE`         | Cannot reach the Central Bank to split escrow |
| 502    | `REPUTATION_SERVICE_UNAVAILABLE`   | Cannot reach the Reputation service to record feedback |
| 502    | `JUDGE_UNAVAILABLE`                | LLM provider returned an error, timed out, or produced an unparseable response |

---

## Test Data Conventions

- `platform_agent` is pre-registered in the Identity service with a known Ed25519 keypair. The Court is configured with this agent's ID as `settings.platform.agent_id`.
- `rogue_agent` is a separate agent registered in the Identity service with a different keypair. Used to test non-platform signer rejection.
- `jws(signer, payload)` denotes a JWS compact serialization (RFC 7515, EdDSA/Ed25519) with header `{"alg":"EdDSA","kid":"<signer.agent_id>"}`, the given JSON payload, and a valid Ed25519 signature.
- `tampered_jws(signer, payload)` denotes a JWS where the payload has been altered after signing (signature mismatch). The Identity service returns `valid: false` for these tokens.
- `dispute_id` format: `disp-<uuid4>` (system-generated, 8-4-4-4-12 hex).
- `vote_id` format: `vote-<uuid4>` (system-generated, 8-4-4-4-12 hex).
- `task_id` format: `t-<uuid4>`.
- `claimant_id` and `respondent_id` format: `a-<uuid4>`.
- `escrow_id` format: `esc-<uuid4>`.
- All timestamps must be valid ISO 8601.
- **Mock judge:** For testing, a deterministic mock judge is used that returns a configurable `worker_pct` and `reasoning`. This allows testing ruling mechanics without actual LLM calls. The mock judge is configured per test to return specific values.
- **Mock external services:**
  - **Identity service mock:** Returns `valid: true` with `agent_id` matching the JWS `kid` for correctly signed tokens. Returns `valid: false` for tampered tokens. Raises connection error when testing `IDENTITY_SERVICE_UNAVAILABLE`.
  - **Task Board mock:** Returns task data (task_id, spec, deliverables, title, reward, status) for valid task IDs. Returns 404 for unknown task IDs. Accepts ruling recording. Raises connection error when testing `TASK_BOARD_UNAVAILABLE`.
  - **Central Bank mock:** Accepts escrow split calls with `worker_pct`. Raises connection error when testing `CENTRAL_BANK_UNAVAILABLE`.
  - **Reputation service mock:** Accepts feedback recording calls. Raises connection error when testing `REPUTATION_SERVICE_UNAVAILABLE`.
- Tests that involve external service mocks assume the mock succeeds unless explicitly stated otherwise.
- A "valid file dispute request" means a JWS signed by the platform agent with `action: "file_dispute"` and all required fields present, with the Task Board mock returning valid task data.

---

## Category 1: File Dispute (`POST /disputes/file`)

### FILE-01 File a valid dispute

**Setup:** Configure mock Task Board to return valid task data for `task_id`. Configure mock Identity to verify platform JWS.
**Action:** `POST /disputes/file` with:
- `token`: `jws(platform_agent, {action: "file_dispute", task_id: "t-<uuid4>", claimant_id: "a-<uuid4>", respondent_id: "a-<uuid4>", claim: "The worker did not implement email validation as specified.", escrow_id: "esc-<uuid4>"})`
**Expected:**
- `201 Created`
- `dispute_id` matches `disp-<uuid4>` format
- `status` is `"rebuttal_pending"`

### FILE-02 Response includes all dispute fields

**Setup:** File a valid dispute.
**Expected:**
- Response body includes all fields: `dispute_id`, `task_id`, `claimant_id`, `respondent_id`, `claim`, `rebuttal`, `status`, `rebuttal_deadline`, `worker_pct`, `ruling_summary`, `escrow_id`, `filed_at`, `rebutted_at`, `ruled_at`, `votes`
- `rebuttal` is `null`
- `worker_pct` is `null`
- `ruling_summary` is `null`
- `rebutted_at` is `null`
- `ruled_at` is `null`
- `votes` is `[]`
- `filed_at` is a valid ISO 8601 timestamp
- `rebuttal_deadline` is a valid ISO 8601 timestamp

### FILE-03 Rebuttal deadline is correctly calculated

**Setup:** File a valid dispute.
**Expected:**
- `rebuttal_deadline` equals `filed_at` + configured `disputes.rebuttal_deadline_seconds` (default 86400 seconds)

### FILE-04 Duplicate dispute for same task is rejected

**Setup:** File a valid dispute for `task_id = "t-xxx"`.
**Action:** Attempt to file another dispute for the same `task_id`.
**Expected:**
- `409 Conflict`
- `error = DISPUTE_ALREADY_EXISTS`

### FILE-05 Task not found in Task Board

**Setup:** Configure mock Task Board to return 404 for the given `task_id`.
**Action:** `POST /disputes/file` with a valid JWS referencing the unknown `task_id`.
**Expected:**
- `404 Not Found`
- `error = TASK_NOT_FOUND`

### FILE-06 Missing claim text

**Action:** `POST /disputes/file` with JWS payload that omits `claim` field.
**Expected:**
- `400 Bad Request`
- `error = INVALID_PAYLOAD`

### FILE-07 Empty claim text

**Action:** `POST /disputes/file` with JWS payload containing `claim: ""`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_PAYLOAD`

### FILE-08 Claim too long (exceeds 10,000 characters)

**Action:** `POST /disputes/file` with JWS payload containing `claim` of 10,001 characters.
**Expected:**
- `400 Bad Request`
- `error = INVALID_PAYLOAD`

### FILE-09 Missing task_id

**Action:** `POST /disputes/file` with JWS payload that omits `task_id` field.
**Expected:**
- `400 Bad Request`
- `error = INVALID_PAYLOAD`

### FILE-10 Missing claimant_id

**Action:** `POST /disputes/file` with JWS payload that omits `claimant_id` field.
**Expected:**
- `400 Bad Request`
- `error = INVALID_PAYLOAD`

### FILE-11 Missing respondent_id

**Action:** `POST /disputes/file` with JWS payload that omits `respondent_id` field.
**Expected:**
- `400 Bad Request`
- `error = INVALID_PAYLOAD`

### FILE-12 Missing escrow_id

**Action:** `POST /disputes/file` with JWS payload that omits `escrow_id` field.
**Expected:**
- `400 Bad Request`
- `error = INVALID_PAYLOAD`

### FILE-13 Wrong action value

**Action:** `POST /disputes/file` with JWS payload containing `action: "submit_rebuttal"` instead of `"file_dispute"`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_PAYLOAD`

### FILE-14 Non-platform signer

**Setup:** Register `rogue_agent` with the Identity service. Configure mock Identity to verify the JWS as valid with `agent_id = rogue_agent.id` (not the platform agent).
**Action:** `POST /disputes/file` with `token: jws(rogue_agent, {action: "file_dispute", ...})`.
**Expected:**
- `403 Forbidden`
- `error = FORBIDDEN`

### FILE-15 Tampered JWS

**Action:** `POST /disputes/file` with `token: tampered_jws(platform_agent, {action: "file_dispute", ...})`.
**Expected:**
- `403 Forbidden`
- `error = FORBIDDEN`

### FILE-16 Missing token field

**Action:** `POST /disputes/file` with body `{}` (no `token` field).
**Expected:**
- `400 Bad Request`
- `error = INVALID_JWS`

### FILE-17 Task Board unavailable

**Setup:** Configure mock Task Board to raise a connection error.
**Action:** `POST /disputes/file` with a valid platform-signed JWS.
**Expected:**
- `502 Bad Gateway`
- `error = TASK_BOARD_UNAVAILABLE`

---

## Category 2: Submit Rebuttal (`POST /disputes/{dispute_id}/rebuttal`)

### REB-01 Submit a valid rebuttal

**Setup:** File a valid dispute (status is `rebuttal_pending`).
**Action:** `POST /disputes/{dispute_id}/rebuttal` with:
- `token`: `jws(platform_agent, {action: "submit_rebuttal", dispute_id: "<dispute_id>", rebuttal: "The specification did not define a specific email format."})`
**Expected:**
- `200 OK`
- `rebuttal` field is set to the submitted text
- `rebutted_at` is a valid ISO 8601 timestamp

### REB-02 Dispute not found

**Action:** `POST /disputes/disp-00000000-0000-0000-0000-000000000000/rebuttal` with a valid platform-signed JWS referencing a non-existent `dispute_id`.
**Expected:**
- `404 Not Found`
- `error = DISPUTE_NOT_FOUND`

### REB-03 Rebuttal already submitted

**Setup:** File a valid dispute. Submit a rebuttal.
**Action:** Submit another rebuttal for the same dispute.
**Expected:**
- `409 Conflict`
- `error = REBUTTAL_ALREADY_SUBMITTED`

### REB-04 Dispute not in rebuttal_pending status

**Setup:** File a valid dispute. Submit a rebuttal. Trigger a ruling (status becomes `ruled`).
**Action:** Submit a rebuttal to the now-ruled dispute.
**Expected:**
- `409 Conflict`
- `error = INVALID_DISPUTE_STATUS`

### REB-05 Missing rebuttal text

**Action:** `POST /disputes/{dispute_id}/rebuttal` with JWS payload that omits `rebuttal` field.
**Expected:**
- `400 Bad Request`
- `error = INVALID_PAYLOAD`

### REB-06 Empty rebuttal text

**Action:** `POST /disputes/{dispute_id}/rebuttal` with JWS payload containing `rebuttal: ""`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_PAYLOAD`

### REB-07 Rebuttal too long (exceeds 10,000 characters)

**Action:** `POST /disputes/{dispute_id}/rebuttal` with JWS payload containing `rebuttal` of 10,001 characters.
**Expected:**
- `400 Bad Request`
- `error = INVALID_PAYLOAD`

### REB-08 Wrong action value

**Action:** `POST /disputes/{dispute_id}/rebuttal` with JWS payload containing `action: "file_dispute"` instead of `"submit_rebuttal"`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_PAYLOAD`

### REB-09 Non-platform signer

**Setup:** File a valid dispute. Register `rogue_agent`. Configure mock Identity to verify the JWS as valid with `agent_id = rogue_agent.id`.
**Action:** `POST /disputes/{dispute_id}/rebuttal` with `token: jws(rogue_agent, {action: "submit_rebuttal", ...})`.
**Expected:**
- `403 Forbidden`
- `error = FORBIDDEN`

### REB-10 Dispute status unchanged after rebuttal

**Setup:** File a valid dispute. Submit a valid rebuttal.
**Action:** `GET /disputes/{dispute_id}`
**Expected:**
- `status` is still `"rebuttal_pending"` (rebuttal submission does not change status)
- `rebuttal` is set to the submitted text
- `rebutted_at` is set

---

## Category 3: Trigger Ruling (`POST /disputes/{dispute_id}/rule`)

### RULE-01 Valid ruling with 1 judge

**Setup:** File a valid dispute. Submit a rebuttal. Configure mock judge to return `worker_pct: 70` with reasoning.
**Action:** `POST /disputes/{dispute_id}/rule` with:
- `token`: `jws(platform_agent, {action: "trigger_ruling", dispute_id: "<dispute_id>"})`
**Expected:**
- `200 OK`
- `worker_pct` is `70`
- `ruling_summary` is non-null and non-empty
- `votes` array has exactly 1 entry

### RULE-02 Ruling median is single vote (panel_size=1)

**Setup:** File a valid dispute. Submit a rebuttal. Configure mock judge to return `worker_pct: 65`.
**Action:** Trigger ruling.
**Expected:**
- `worker_pct` on the dispute equals `65` (the single judge's vote is the median)

### RULE-03 Dispute status changes to ruled

**Setup:** File a valid dispute. Submit a rebuttal. Trigger ruling.
**Action:** `GET /disputes/{dispute_id}`
**Expected:**
- `status` is `"ruled"`

### RULE-04 ruled_at timestamp is set

**Setup:** File a valid dispute. Submit a rebuttal. Trigger ruling.
**Expected:**
- `ruled_at` is a valid ISO 8601 timestamp
- `ruled_at` is after `filed_at`

### RULE-05 Vote record has correct structure

**Setup:** File a valid dispute. Submit a rebuttal. Trigger ruling.
**Expected:**
- Each entry in `votes` array contains: `vote_id`, `dispute_id`, `judge_id`, `worker_pct`, `reasoning`, `voted_at`
- `vote_id` matches `vote-<uuid4>` format
- `dispute_id` matches the parent dispute's `dispute_id`
- `judge_id` matches the configured judge identifier (e.g., `"judge-0"`)
- `worker_pct` is an integer 0-100
- `reasoning` is a non-empty string
- `voted_at` is a valid ISO 8601 timestamp

### RULE-06 Ruling calls Central Bank to split escrow

**Setup:** File a valid dispute with known `escrow_id`. Submit a rebuttal. Configure mock judge to return `worker_pct: 70`.
**Action:** Trigger ruling.
**Expected:**
- Mock Central Bank was called with the correct `escrow_id` and `worker_pct: 70`
- Verify the mock was called exactly once

### RULE-07 Ruling calls Reputation to record feedback

**Setup:** File a valid dispute. Submit a rebuttal. Trigger ruling.
**Expected:**
- Mock Reputation service was called to record feedback
- At least two feedback records submitted (spec quality for poster, delivery quality for worker)

### RULE-08 Judge returns 0% — poster gets full refund

**Setup:** File a valid dispute. Submit a rebuttal. Configure mock judge to return `worker_pct: 0`.
**Action:** Trigger ruling.
**Expected:**
- `worker_pct` is `0`
- Mock Central Bank was called with `worker_pct: 0`

### RULE-09 Judge returns 100% — worker gets full payout

**Setup:** File a valid dispute. Submit a rebuttal. Configure mock judge to return `worker_pct: 100`.
**Action:** Trigger ruling.
**Expected:**
- `worker_pct` is `100`
- Mock Central Bank was called with `worker_pct: 100`

### RULE-10 Judge returns 50% — even split

**Setup:** File a valid dispute. Submit a rebuttal. Configure mock judge to return `worker_pct: 50`.
**Action:** Trigger ruling.
**Expected:**
- `worker_pct` is `50`
- Mock Central Bank was called with `worker_pct: 50`

### RULE-11 Judge returns 73% — asymmetric split

**Setup:** File a valid dispute. Submit a rebuttal. Configure mock judge to return `worker_pct: 73`.
**Action:** Trigger ruling.
**Expected:**
- `worker_pct` is `73`
- Mock Central Bank was called with `worker_pct: 73`

### RULE-12 Dispute not found

**Action:** `POST /disputes/disp-00000000-0000-0000-0000-000000000000/rule` with a valid platform-signed JWS referencing a non-existent `dispute_id`.
**Expected:**
- `404 Not Found`
- `error = DISPUTE_NOT_FOUND`

### RULE-13 Already ruled

**Setup:** File a valid dispute. Submit a rebuttal. Trigger ruling (dispute is now `ruled`).
**Action:** Attempt to trigger ruling again.
**Expected:**
- `409 Conflict`
- `error = DISPUTE_ALREADY_RULED`

### RULE-14 Wrong dispute status

**Setup:** File a valid dispute. Trigger ruling (dispute transitions to `ruled`).
**Action:** Attempt to trigger ruling on a dispute in `ruled` status with `action: "trigger_ruling"`.
**Expected:**
- `409 Conflict`
- `error = DISPUTE_ALREADY_RULED` or `error = INVALID_DISPUTE_STATUS`

### RULE-15 Judge unavailable (LLM error)

**Setup:** File a valid dispute. Submit a rebuttal. Configure mock judge to raise an error (simulating LLM failure).
**Action:** Trigger ruling.
**Expected:**
- `502 Bad Gateway`
- `error = JUDGE_UNAVAILABLE`
- Dispute status remains `rebuttal_pending` (ruling rolled back)

### RULE-16 Central Bank unavailable

**Setup:** File a valid dispute. Submit a rebuttal. Configure mock judge to return valid vote. Configure mock Central Bank to raise a connection error.
**Action:** Trigger ruling.
**Expected:**
- `502 Bad Gateway`
- `error = CENTRAL_BANK_UNAVAILABLE`
- Dispute status remains `rebuttal_pending` (ruling rolled back, votes not persisted)

### RULE-17 Reputation service unavailable

**Setup:** File a valid dispute. Submit a rebuttal. Configure mock judge to return valid vote. Configure mock Reputation service to raise a connection error.
**Action:** Trigger ruling.
**Expected:**
- `502 Bad Gateway`
- `error = REPUTATION_SERVICE_UNAVAILABLE`
- Dispute status remains `rebuttal_pending` (ruling rolled back, votes not persisted)

### RULE-18 Wrong action value

**Action:** `POST /disputes/{dispute_id}/rule` with JWS payload containing `action: "file_dispute"` instead of `"trigger_ruling"`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_PAYLOAD`

### RULE-19 Ruling without rebuttal (rebuttal window expired)

**Setup:** File a valid dispute. Do NOT submit a rebuttal.
**Action:** Trigger ruling (rebuttal window has expired or is being skipped by the platform).
**Expected:**
- `200 OK`
- `rebuttal` is `null`
- `rebutted_at` is `null`
- `worker_pct` is set (judge evaluates without rebuttal)
- `status` is `"ruled"`
- `votes` array has the expected number of entries

---

## Category 4: Get Dispute (`GET /disputes/{dispute_id}`)

### GET-01 Get a filed dispute (no ruling yet)

**Setup:** File a valid dispute.
**Action:** `GET /disputes/{dispute_id}`
**Expected:**
- `200 OK`
- `worker_pct` is `null`
- `ruling_summary` is `null`
- `ruled_at` is `null`
- `votes` is `[]`
- `status` is `"rebuttal_pending"`

### GET-02 Get a ruled dispute

**Setup:** File a valid dispute. Submit a rebuttal. Trigger ruling.
**Action:** `GET /disputes/{dispute_id}`
**Expected:**
- `200 OK`
- `worker_pct` is set (integer 0-100)
- `ruling_summary` is non-null and non-empty
- `ruled_at` is a valid ISO 8601 timestamp
- `status` is `"ruled"`
- `votes` array is non-empty

### GET-03 Votes array contains all judge votes with correct structure

**Setup:** File a valid dispute. Submit a rebuttal. Trigger ruling (panel_size=1).
**Action:** `GET /disputes/{dispute_id}`
**Expected:**
- `votes` array has exactly 1 entry
- Each vote contains: `vote_id`, `dispute_id`, `judge_id`, `worker_pct`, `reasoning`, `voted_at`
- `vote_id` matches `vote-<uuid4>`
- `judge_id` is `"judge-0"`
- `worker_pct` is an integer 0-100
- `reasoning` is a non-empty string
- `voted_at` is a valid ISO 8601 timestamp

### GET-04 Dispute not found

**Action:** `GET /disputes/disp-00000000-0000-0000-0000-000000000000`
**Expected:**
- `404 Not Found`
- `error = DISPUTE_NOT_FOUND`

### GET-05 No authentication required (public endpoint)

**Action:** `GET /disputes/{dispute_id}` with no `Authorization` header and no token in body.
**Expected:**
- `200 OK` (if dispute exists)
- No authentication error returned

---

## Category 5: List Disputes (`GET /disputes`)

### LIST-01 Empty list on fresh system

**Action:** `GET /disputes`
**Expected:**
- `200 OK`
- Body `{ "disputes": [] }`

### LIST-02 List all disputes

**Setup:** File 3 disputes (for different tasks).
**Action:** `GET /disputes`
**Expected:**
- `200 OK`
- `disputes` array has 3 entries
- Each entry includes: `dispute_id`, `task_id`, `claimant_id`, `respondent_id`, `status`, `worker_pct`, `filed_at`, `ruled_at`

### LIST-03 Filter by task_id

**Setup:** File disputes for `task_id_A` and `task_id_B`.
**Action:** `GET /disputes?task_id=<task_id_A>`
**Expected:**
- `200 OK`
- `disputes` array contains only the dispute for `task_id_A`

### LIST-04 Filter by status

**Setup:** File a dispute (status: `rebuttal_pending`). File another dispute and rule it (status: `ruled`).
**Action:** `GET /disputes?status=rebuttal_pending`
**Expected:**
- `200 OK`
- `disputes` array contains only disputes with `status = "rebuttal_pending"`

### LIST-05 Filter by both task_id and status

**Setup:** File disputes for `task_id_A` (rebuttal_pending) and `task_id_B` (ruled).
**Action:** `GET /disputes?task_id=<task_id_A>&status=rebuttal_pending`
**Expected:**
- `200 OK`
- `disputes` array contains only the dispute matching both filters

### LIST-06 No authentication required

**Action:** `GET /disputes` with no `Authorization` header and no token.
**Expected:**
- `200 OK`
- No authentication error returned

---

## Category 6: Health (`GET /health`)

### HLTH-01 Health schema is correct

**Action:** `GET /health`
**Expected:**
- `200 OK`
- Body contains: `status`, `uptime_seconds`, `started_at`, `total_disputes`, `active_disputes`
- `status` is `"ok"`

### HLTH-02 total_disputes count is accurate

**Setup:** File `N` disputes.
**Action:** `GET /health`
**Expected:**
- `total_disputes` equals `N`

### HLTH-03 active_disputes equals count of non-ruled disputes

**Setup:** File 3 disputes. Rule 1 of them.
**Action:** `GET /health`
**Expected:**
- `total_disputes` equals `3`
- `active_disputes` equals `2`

### HLTH-04 Uptime is monotonic

**Action:** Call `GET /health` twice with a delay >= 1 second.
**Expected:**
- Second `uptime_seconds` > first `uptime_seconds`

---

## Category 7: HTTP Method Misuse

### HTTP-01 Wrong methods on defined routes are blocked

**Action:** Send unsupported HTTP methods:
- `GET /disputes/file`
- `PUT /disputes/file`
- `DELETE /disputes/file`
- `PUT /disputes/{dispute_id}`
- `DELETE /disputes/{dispute_id}`
- `PATCH /disputes/{dispute_id}`
- `GET /disputes/{dispute_id}/rebuttal`
- `PUT /disputes/{dispute_id}/rebuttal`
- `DELETE /disputes/{dispute_id}/rebuttal`
- `GET /disputes/{dispute_id}/rule`
- `PUT /disputes/{dispute_id}/rule`
- `DELETE /disputes/{dispute_id}/rule`
- `POST /disputes`
- `POST /health`
**Expected:** `405 Method Not Allowed`, `error = METHOD_NOT_ALLOWED` for each

---

## Category 8: Cross-Cutting Security Assertions

### SEC-01 Error envelope consistency

**Action:** For at least one failing test per error code, assert response has exactly:
- top-level `error` (string)
- top-level `message` (string)
**Expected:** All failures comply with the standard error envelope format

### SEC-02 No internal error leakage

**Action:** Trigger representative failures (`INVALID_JSON`, `INVALID_JWS`, `DISPUTE_NOT_FOUND`, `FORBIDDEN`, `JUDGE_UNAVAILABLE`).
**Expected:** `message` never includes stack traces, SQL fragments, file paths, Python tracebacks, or driver internals

### SEC-03 IDs are correctly formatted

**Action:** File 3+ disputes and trigger rulings.
**Expected:**
- Every `dispute_id` matches `disp-<uuid4>` (8-4-4-4-12 hex)
- Every `vote_id` matches `vote-<uuid4>` (8-4-4-4-12 hex)

---

## Category 9: Judge Panel Configuration

### JUDGE-01 Panel size must be odd (even size rejected at startup)

**Setup:** Configure `judges.panel_size: 2` with 2 judges in the array.
**Action:** Start the service.
**Expected:**
- Service refuses to start
- Error references `INVALID_PANEL_SIZE`

### JUDGE-02 Panel size 0 rejected at startup

**Setup:** Configure `judges.panel_size: 0` with 0 judges in the array.
**Action:** Start the service.
**Expected:**
- Service refuses to start
- Error references `INVALID_PANEL_SIZE`

### JUDGE-03 Panel size -1 rejected at startup

**Setup:** Configure `judges.panel_size: -1`.
**Action:** Start the service.
**Expected:**
- Service refuses to start
- Error references `INVALID_PANEL_SIZE`

### JUDGE-04 Each judge must cast exactly one vote (vote count equals panel_size)

**Setup:** Configure `judges.panel_size: 1` with 1 judge. File a valid dispute. Submit a rebuttal. Trigger ruling.
**Action:** `GET /disputes/{dispute_id}`
**Expected:**
- `votes` array has exactly 1 entry
- Vote `judge_id` matches the configured judge ID

### JUDGE-05 Panel size 1 works (single judge)

**Setup:** Configure `judges.panel_size: 1` with 1 judge. File a valid dispute. Submit a rebuttal. Configure mock judge to return `worker_pct: 55`.
**Action:** Trigger ruling.
**Expected:**
- `200 OK`
- `worker_pct` is `55`
- `votes` array has exactly 1 entry

---

## Category 10: Dispute Lifecycle Integration

### LIFE-01 Full lifecycle: file, rebuttal, rule, verify final state

**Setup:** Configure mock judge to return `worker_pct: 70`.
**Action:**
1. `POST /disputes/file` with valid platform-signed JWS -> `201`, status is `rebuttal_pending`
2. `POST /disputes/{dispute_id}/rebuttal` with valid platform-signed JWS -> `200`, `rebuttal` is set, `rebutted_at` is set
3. `POST /disputes/{dispute_id}/rule` with valid platform-signed JWS -> `200`, `worker_pct: 70`, `status: "ruled"`
4. `GET /disputes/{dispute_id}` -> `200`, full dispute with all fields populated
**Expected (final GET):**
- `status` is `"ruled"`
- `claim` is the original claim text
- `rebuttal` is the submitted rebuttal text
- `worker_pct` is `70`
- `ruling_summary` is non-null and non-empty
- `rebuttal_deadline` is set
- `filed_at` < `rebutted_at` < `ruled_at`
- `votes` array has 1 entry with `worker_pct: 70`
- Mock Central Bank was called with `worker_pct: 70`
- Mock Reputation service was called

### LIFE-02 File then rule without rebuttal (window expired)

**Setup:** File a valid dispute. Configure mock judge to return `worker_pct: 80`.
**Action:**
1. `POST /disputes/file` -> `201`, status is `rebuttal_pending`
2. `POST /disputes/{dispute_id}/rule` (no rebuttal submitted) -> `200`
**Expected:**
- `status` is `"ruled"`
- `rebuttal` is `null`
- `rebutted_at` is `null`
- `worker_pct` is `80`
- `votes` array has expected entries

### LIFE-03 Cannot file two disputes for same task

**Setup:** File a valid dispute for `task_id_X`.
**Action:** Attempt to file another dispute for `task_id_X`.
**Expected:**
- `409 Conflict`
- `error = DISPUTE_ALREADY_EXISTS`
- Original dispute is unchanged

### LIFE-04 Cannot submit rebuttal after ruling

**Setup:** File a valid dispute. Trigger ruling (skip rebuttal).
**Action:** Attempt to submit a rebuttal to the now-ruled dispute.
**Expected:**
- `409 Conflict`
- `error = INVALID_DISPUTE_STATUS`

### LIFE-05 Cannot rule twice

**Setup:** File a valid dispute. Submit a rebuttal. Trigger ruling.
**Action:** Attempt to trigger ruling again.
**Expected:**
- `409 Conflict`
- `error = DISPUTE_ALREADY_RULED`

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
| File Dispute | FILE-01 to FILE-17 | 17 |
| Submit Rebuttal | REB-01 to REB-10 | 10 |
| Trigger Ruling | RULE-01 to RULE-19 | 19 |
| Get Dispute | GET-01 to GET-05 | 5 |
| List Disputes | LIST-01 to LIST-06 | 6 |
| Health | HLTH-01 to HLTH-04 | 4 |
| HTTP Method Misuse | HTTP-01 | 1 |
| Cross-Cutting Security | SEC-01 to SEC-03 | 3 |
| Judge Panel Configuration | JUDGE-01 to JUDGE-05 | 5 |
| Dispute Lifecycle Integration | LIFE-01 to LIFE-05 | 5 |
| **Total** |  | **75** |

| Endpoint | Covered By |
|----------|------------|
| `POST /disputes/file` | FILE-01 to FILE-17, SEC-01, SEC-02, SEC-03, LIFE-01, LIFE-02, LIFE-03 |
| `POST /disputes/{dispute_id}/rebuttal` | REB-01 to REB-10, LIFE-01, LIFE-04 |
| `POST /disputes/{dispute_id}/rule` | RULE-01 to RULE-19, LIFE-01, LIFE-02, LIFE-05 |
| `GET /disputes/{dispute_id}` | GET-01 to GET-05, RULE-03, RULE-05, LIFE-01 |
| `GET /disputes` | LIST-01 to LIST-06 |
| `GET /health` | HLTH-01 to HLTH-04 |
