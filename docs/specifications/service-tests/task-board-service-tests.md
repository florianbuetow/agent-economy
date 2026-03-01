# Task Board Service — Production Release Test Specification

## Purpose

This document is the release-gate test specification for the Task Board Service.
It is intentionally strict and unambiguous:

- Every negative case has one required status code and one required error code.
- Every failing response must use the standard error envelope.
- Any behavior not listed here is out of scope for release sign-off.

This document covers the full surface: business logic, authentication, authorization, escrow integration, deadline enforcement, sealed bids, and asset management.

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

| Status | Error Code                       | Required When |
|--------|----------------------------------|---------------|
| 400    | `INVALID_JSON`                  | Request body is malformed JSON |
| 400    | `INVALID_JWS`                   | Token field is missing, null, non-string, empty, or not valid three-part compact serialization |
| 400    | `INVALID_PAYLOAD`               | JWS payload is missing `action`, `action` does not match expected value, or required payload fields are missing |
| 400    | `TOKEN_MISMATCH`                | `task_id` or `amount`/`reward` mismatch between `task_token` and `escrow_token` |
| 400    | `INVALID_TASK_ID`               | `task_id` does not match `t-<uuid4>` format |
| 400    | `INVALID_REWARD`                | Reward is not a positive integer |
| 400    | `INVALID_DEADLINE`              | Any deadline value is not a positive integer |
| 400    | `SELF_BID`                      | Poster attempts to bid on their own task |
| 400    | `NO_FILE`                       | No file part in multipart upload request |
| 400    | `NO_ASSETS`                     | Worker submits deliverable with no assets uploaded |
| 400    | `INVALID_REASON`                | Dispute reason is empty or exceeds 10,000 characters |
| 400    | `INVALID_WORKER_PCT`            | `worker_pct` is not an integer 0–100 |
| 402    | `INSUFFICIENT_FUNDS`            | Central Bank reports insufficient funds for escrow lock |
| 403    | `FORBIDDEN`                     | JWS signature is invalid, signer does not match required agent, or signer is not the platform agent |
| 404    | `TASK_NOT_FOUND`                | Referenced `task_id` does not exist |
| 404    | `BID_NOT_FOUND`                 | Referenced `bid_id` does not exist for this task |
| 404    | `ASSET_NOT_FOUND`               | Referenced `asset_id` does not exist for this task |
| 405    | `METHOD_NOT_ALLOWED`            | Unsupported HTTP method on a defined route |
| 409    | `TASK_ALREADY_EXISTS`           | A task with this `task_id` already exists |
| 409    | `INVALID_STATUS`                | Task is in wrong status for the requested operation |
| 409    | `BID_ALREADY_EXISTS`            | This agent already bid on this task |
| 409    | `TOO_MANY_ASSETS`               | Max assets per task reached |
| 413    | `PAYLOAD_TOO_LARGE`             | Request body exceeds configured max body size |
| 413    | `FILE_TOO_LARGE`                | Uploaded file exceeds configured max file size |
| 415    | `UNSUPPORTED_MEDIA_TYPE`        | Wrong `Content-Type` (expected `application/json` or `multipart/form-data` depending on endpoint) |
| 502    | `IDENTITY_SERVICE_UNAVAILABLE`  | Identity service is unreachable, times out, or returns unexpected response |
| 502    | `CENTRAL_BANK_UNAVAILABLE`      | Central Bank is unreachable, times out, or escrow operation failed |

---

## Test Data Conventions

- `agent_alice`, `agent_bob`, `agent_carol` are agents pre-registered in the Identity service with known Ed25519 keypairs.
- `platform_agent` is the platform agent registered in the Identity service, with the private key loaded by the Task Board at startup.
- `jws(signer, payload)` denotes a JWS compact serialization (RFC 7515, EdDSA/Ed25519) with header `{"alg":"EdDSA","kid":"<signer.agent_id>"}`, the given JSON payload, and a valid Ed25519 signature.
- `escrow_jws(signer, payload)` denotes a JWS intended for the Central Bank's `POST /escrow/lock` endpoint.
- `tampered_jws(signer, payload)` denotes a JWS where the payload has been altered after signing (signature mismatch).
- Agent IDs use the format `a-<uuid4>`.
- Task IDs use the format `t-<uuid4>` and are client-generated.
- Bid IDs returned by the service must match `bid-<uuid4>`.
- Asset IDs returned by the service must match `asset-<uuid4>`.
- All timestamps must be valid ISO 8601.
- A "valid task creation request" means both `task_token` and `escrow_token` are present, correctly signed, and cross-validated.
- Tests that involve the Central Bank mock assume escrow operations succeed unless explicitly stated otherwise.
- Tests that involve the Identity service mock assume JWS verification succeeds unless explicitly stated otherwise.

---

## Category 1: Task Creation (`POST /tasks`)

### TC-01 Create a valid task with escrow

**Setup:** Register `agent_alice`. Fund Alice's account. Generate `task_id = "t-<uuid4>"`.
**Action:** `POST /tasks` with:
- `task_token`: `jws(alice, {action: "create_task", task_id, poster_id: alice.id, title: "Implement login page", spec: "Create a login page with...", reward: 100, bidding_deadline_seconds: 86400, deadline_seconds: 3600, review_deadline_seconds: 600})`
- `escrow_token`: `escrow_jws(alice, {action: "escrow_lock", agent_id: alice.id, amount: 100, task_id})`
**Expected:**
- `201 Created`
- Body includes all task fields: `task_id`, `poster_id`, `title`, `spec`, `reward`, `bidding_deadline_seconds`, `deadline_seconds`, `review_deadline_seconds`, `status`, `escrow_id`, `bid_count`, `worker_id`, `accepted_bid_id`, `created_at`, `accepted_at`, `submitted_at`, `approved_at`, `cancelled_at`, `disputed_at`, `dispute_reason`, `ruling_id`, `ruled_at`, `worker_pct`, `ruling_summary`, `expired_at`, `escrow_pending`, `bidding_deadline`, `execution_deadline`, `review_deadline`
- `task_id` matches the client-generated value
- `poster_id` matches `alice.agent_id`
- `status` is `"open"`
- `escrow_id` matches `esc-<uuid4>` format
- `bid_count` is `0`
- `escrow_pending` is `false`
- `worker_id`, `accepted_bid_id`, `accepted_at`, `submitted_at`, `approved_at`, `cancelled_at`, `disputed_at`, `dispute_reason`, `ruling_id`, `ruled_at`, `worker_pct`, `ruling_summary`, `expired_at` are all `null`
- `created_at` is valid ISO 8601 timestamp
- `bidding_deadline` is approximately `created_at + 86400 seconds`
- `execution_deadline` and `review_deadline` are `null`

### TC-02 Duplicate `task_id` is rejected

**Setup:** Create a valid task with `task_id = "t-xxx"`.
**Action:** Attempt to create another task with the same `task_id`.
**Expected:**
- `409 Conflict`
- `error = TASK_ALREADY_EXISTS`

### TC-03 `task_id` format validation

**Action:** Submit task creation with `task_id` values that do not match `t-<uuid4>`:
- `"not-a-uuid"`
- `"a-550e8400-e29b-41d4-a716-446655440000"` (agent ID prefix)
- `"t-invalid"`
- `""` (empty string)
**Expected:** `400`, `error = INVALID_TASK_ID` for each.

### TC-04 Missing `task_token`

**Action:** `POST /tasks` with body `{"escrow_token": "<valid>"}`.
**Expected:** `400`, `error = INVALID_JWS`

### TC-05 Missing `escrow_token`

**Action:** `POST /tasks` with body `{"task_token": "<valid>"}`.
**Expected:** `400`, `error = INVALID_JWS`

### TC-06 Both tokens missing

**Action:** `POST /tasks` with body `{}`.
**Expected:** `400`, `error = INVALID_JWS`

### TC-07 `task_token` is malformed JWS

**Action:** Send each of these `task_token` values with a valid `escrow_token`:
- `"not-a-jws"` (no dots)
- `"only.two-parts"` (two parts)
- `12345` (not a string)
- `null`
- `""` (empty string)
**Expected:** `400`, `error = INVALID_JWS` for each.

### TC-08 Wrong `action` in `task_token`

**Setup:** Register `agent_alice`.
**Action:** Sign `task_token` with `action: "submit_bid"` instead of `"create_task"`.
**Expected:** `400`, `error = INVALID_PAYLOAD`

### TC-09 Missing required fields in `task_token` payload

**Setup:** Register `agent_alice`.
**Action:** Omit each of `task_id`, `poster_id`, `title`, `spec`, `reward`, `bidding_deadline_seconds`, `deadline_seconds`, `review_deadline_seconds` from the `task_token` payload in separate requests.
**Expected:** `400`, `error = INVALID_PAYLOAD` for each.

### TC-10 Signer does not match `poster_id` in `task_token`

**Setup:** Register `agent_alice` and `agent_bob`.
**Action:** Alice signs a `task_token` with `poster_id: bob.id` (impersonation attempt).
**Expected:** `403`, `error = FORBIDDEN`

### TC-11 `task_id` mismatch between tokens

**Setup:** Register `agent_alice`.
**Action:** Sign `task_token` with `task_id: "t-aaa..."` and `escrow_token` with `task_id: "t-bbb..."`.
**Expected:** `400`, `error = TOKEN_MISMATCH`

### TC-12 `reward`/`amount` mismatch between tokens

**Setup:** Register `agent_alice`.
**Action:** Sign `task_token` with `reward: 100` and `escrow_token` with `amount: 50`.
**Expected:** `400`, `error = TOKEN_MISMATCH`

### TC-13 Invalid reward values

**Action:** Submit task creation with these `reward` values in the `task_token`:
- `0`
- `-10`
- `1.5` (float)
- `"one hundred"` (string)
- `null`
**Expected:** `400`, `error = INVALID_REWARD` for each.

### TC-14a Invalid `bidding_deadline_seconds` values

**Action:** Submit task creation with these values for `bidding_deadline_seconds`:
- `0`
- `-3600`
- `1.5` (float)
- `"one hour"` (string)
**Expected:** `400`, `error = INVALID_DEADLINE` for each.

### TC-14b Invalid `deadline_seconds` values

**Action:** Submit task creation with these values for `deadline_seconds` (all other fields valid):
- `0`
- `-3600`
- `1.5` (float)
- `"one hour"` (string)
**Expected:** `400`, `error = INVALID_DEADLINE` for each.

### TC-14c Invalid `review_deadline_seconds` values

**Action:** Submit task creation with these values for `review_deadline_seconds` (all other fields valid):
- `0`
- `-3600`
- `1.5` (float)
- `"one hour"` (string)
**Expected:** `400`, `error = INVALID_DEADLINE` for each.

### TC-15 Title validation

**Action:** Submit task creation with these `title` values:
- `""` (empty string)
- String of 201 characters (one over limit)
**Expected:** `400`, `error = INVALID_PAYLOAD` for each.

### TC-16 Title at exactly max length is accepted

**Action:** Submit task creation with a title of exactly 200 characters.
**Expected:** `201 Created`

### TC-17 Spec validation

**Action:** Submit task creation with these `spec` values:
- `""` (empty string)
- String of 10,001 characters (one over limit)
**Expected:** `400`, `error = INVALID_PAYLOAD` for each.

### TC-18 Spec at exactly max length is accepted

**Action:** Submit task creation with a spec of exactly 10,000 characters.
**Expected:** `201 Created`

### TC-19 Insufficient funds for escrow

**Setup:** Register `agent_alice` with insufficient balance.
**Action:** Attempt to create a task with `reward: 10000` (exceeds balance).
**Expected:** `402`, `error = INSUFFICIENT_FUNDS`

### TC-20 Tampered `task_token`

**Setup:** Register `agent_alice`. Construct a valid `task_token`, then alter the payload after signing.
**Action:** `POST /tasks` with the tampered `task_token`.
**Expected:** `403`, `error = FORBIDDEN`

### TC-21 `task_token` signed by unregistered agent

**Setup:** Generate a keypair that is NOT registered in the Identity service.
**Action:** Sign a `task_token` with the unregistered keypair.
**Expected:** `403`, `error = FORBIDDEN`

### TC-22 Identity service unavailable during task creation

**Setup:** Configure Task Board to point to an Identity service that is not running.
**Action:** `POST /tasks` with valid-looking tokens.
**Expected:** `502`, `error = IDENTITY_SERVICE_UNAVAILABLE`

### TC-23 Central Bank unavailable during escrow lock

**Setup:** Register `agent_alice`. Configure Task Board to point to a Central Bank that is not running.
**Action:** `POST /tasks` with valid tokens (Identity mock succeeds, Central Bank unreachable).
**Expected:** `502`, `error = CENTRAL_BANK_UNAVAILABLE`

### TC-24 Mass-assignment resistance (extra fields in `task_token`)

**Setup:** Register `agent_alice`.
**Action:** Include `status`, `escrow_id`, `worker_id`, `approved_at`, `is_admin` in the `task_token` payload alongside valid fields.
**Expected:**
- `201 Created`
- Service-generated `escrow_id` is used
- `status` is `"open"` (not the attacker's value)
- `worker_id` is `null`
- Extra fields are ignored

### TC-25 Malformed JSON body

**Action:** Send truncated/invalid JSON to `POST /tasks`.
**Expected:** `400`, `error = INVALID_JSON`

### TC-26 Wrong content type

**Action:** `Content-Type: text/plain` with JSON-looking body.
**Expected:** `415`, `error = UNSUPPORTED_MEDIA_TYPE`

### TC-27 Oversized request body

**Action:** Send a body exceeding `request.max_body_size`.
**Expected:** `413`, `error = PAYLOAD_TOO_LARGE`

### TC-28 Escrow rollback on database failure

**Setup:** Register `agent_alice`. Fund Alice's account. Configure the Central Bank mock to succeed on escrow lock. Simulate a database insert failure (e.g., by pre-inserting a row with the same `task_id` via direct DB access so the INSERT fails with a constraint violation, without going through the API's duplicate check).
**Action:** `POST /tasks` with valid tokens.
**Expected:**
- The response is an error (not `201`)
- The Central Bank mock received an escrow release call after the lock call (rollback)
- The escrow is not left in a locked state

---

## Category 2: Task Queries (`GET /tasks`, `GET /tasks/{task_id}`)

### TQ-01 Get a task by ID

**Setup:** Create a valid task.
**Action:** `GET /tasks/{task_id}`
**Expected:**
- `200 OK`
- Full task object returned with all fields matching the creation response

### TQ-02 Get non-existent task

**Action:** `GET /tasks/t-00000000-0000-0000-0000-000000000000`
**Expected:** `404`, `error = TASK_NOT_FOUND`

### TQ-03 Malformed task ID in path

**Action:** `GET /tasks/not-a-valid-id` and `GET /tasks/../../etc/passwd`
**Expected:**
- `404` for each
- No stack traces, filesystem paths, or internal diagnostics in response body

### TQ-04 SQL injection in task ID path

**Action:** `GET /tasks/' OR '1'='1`
**Expected:** `404`, `error = TASK_NOT_FOUND`

### TQ-05 List tasks (empty system)

**Action:** `GET /tasks`
**Expected:**
- `200 OK`
- `{"tasks": []}`

### TQ-06 List tasks returns summary fields

**Setup:** Create at least 2 tasks.
**Action:** `GET /tasks`
**Expected:**
- `200 OK`
- `tasks` array contains all created tasks
- Each entry has summary fields: `task_id`, `poster_id`, `title`, `reward`, `status`, `bid_count`, `worker_id`, `created_at`, `bidding_deadline`, `execution_deadline`, `review_deadline`
- Each entry does NOT include `spec`, `dispute_reason`, `ruling_summary`, or other detail-only fields (full details only via `GET /tasks/{id}`)

### TQ-07 Filter tasks by status

**Setup:** Create one OPEN task and one CANCELLED task.
**Action:** `GET /tasks?status=open`
**Expected:**
- `200 OK`
- `tasks` array contains only the OPEN task

### TQ-08 Filter tasks by `poster_id`

**Setup:** Alice creates task_1. Bob creates task_2.
**Action:** `GET /tasks?poster_id={alice.id}`
**Expected:**
- `200 OK`
- `tasks` array contains only task_1

### TQ-09 Filter tasks by `worker_id`

**Setup:** Create a task, accept Bob's bid (worker_id = bob).
**Action:** `GET /tasks?worker_id={bob.id}`
**Expected:**
- `200 OK`
- `tasks` array contains only the accepted task

### TQ-10 Combined filters (AND logic)

**Setup:** Alice creates task_1 (open) and task_2 (cancelled).
**Action:** `GET /tasks?poster_id={alice.id}&status=open`
**Expected:**
- `200 OK`
- `tasks` array contains only task_1

### TQ-11 Unknown filter values return empty list

**Action:** `GET /tasks?status=nonexistent`
**Expected:**
- `200 OK`
- `tasks` is an empty array (no error)

### TQ-12 GET /tasks requires no authentication

**Action:** `GET /tasks` and `GET /tasks/{id}` with no Authorization header and no token.
**Expected:** `200 OK` for both (public endpoints)

### TQ-13 Idempotent read

**Setup:** Create a task.
**Action:** `GET /tasks/{task_id}` twice.
**Expected:** Both responses are `200` with identical JSON.

---

## Category 3: Task Cancellation (`POST /tasks/{task_id}/cancel`)

### CAN-01 Poster cancels an OPEN task

**Setup:** Alice creates a task (OPEN status).
**Action:** `POST /tasks/{task_id}/cancel` with `jws(alice, {action: "cancel_task", task_id, poster_id: alice.id})`.
**Expected:**
- `200 OK`
- `status` is `"cancelled"`
- `cancelled_at` is valid ISO 8601 timestamp
- Escrow released back to poster (mock Central Bank confirms release)

### CAN-02 Non-poster cannot cancel

**Setup:** Alice creates a task. Register `agent_bob`.
**Action:** Bob attempts to cancel: `jws(bob, {action: "cancel_task", task_id, poster_id: bob.id})`.
**Expected:** `403`, `error = FORBIDDEN`

### CAN-03 Impersonation: Bob signs with `poster_id: alice.id`

**Setup:** Alice creates a task. Register `agent_bob`.
**Action:** `jws(bob, {action: "cancel_task", task_id, poster_id: alice.id})`.
**Expected:** `403`, `error = FORBIDDEN` (signer `bob` != `poster_id` `alice`)

### CAN-04 Cannot cancel non-OPEN task

**Setup:** Create a task and accept a bid (ACCEPTED status).
**Action:** Poster attempts to cancel.
**Expected:** `409`, `error = INVALID_STATUS`

### CAN-05 Cancel non-existent task

**Action:** `POST /tasks/t-00000000-0000-0000-0000-000000000000/cancel` with valid JWS.
**Expected:** `404`, `error = TASK_NOT_FOUND`

### CAN-06 Wrong `action` in cancel token

**Setup:** Alice creates a task.
**Action:** `jws(alice, {action: "approve_task", task_id, poster_id: alice.id})`.
**Expected:** `400`, `error = INVALID_PAYLOAD`

### CAN-07 Central Bank unavailable during escrow release on cancel

**Setup:** Alice creates a task. Configure Central Bank mock to return error on release.
**Action:** Poster cancels.
**Expected:** `502`, `error = CENTRAL_BANK_UNAVAILABLE`

### CAN-08 Malformed token on cancel

**Action:** `POST /tasks/{task_id}/cancel` with `{"token": "not-a-jws"}`.
**Expected:** `400`, `error = INVALID_JWS`

### CAN-09 `task_id` in payload must match URL path

**Setup:** Alice creates task_1 and task_2.
**Action:** `POST /tasks/{task_1}/cancel` with `jws(alice, {action: "cancel_task", task_id: task_2, poster_id: alice.id})`.
**Expected:** `400`, `error = INVALID_PAYLOAD`

---

## Category 4: Bidding (`POST /tasks/{task_id}/bids`)

### BID-01 Submit a valid bid

**Setup:** Alice creates a task (OPEN). Register `agent_bob`.
**Action:** `POST /tasks/{task_id}/bids` with `jws(bob, {action: "submit_bid", task_id, bidder_id: bob.id, proposal: "I will implement this using React..."})`.
**Expected:**
- `201 Created`
- Body includes `bid_id`, `task_id`, `bidder_id`, `proposal`, `submitted_at`
- `bid_id` matches `bid-<uuid4>`
- `bidder_id` matches `bob.agent_id`
- `submitted_at` is valid ISO 8601 timestamp

### BID-02 Multiple agents can bid on same task

**Setup:** Alice creates a task. Register `agent_bob` and `agent_carol`.
**Action:** Bob bids, then Carol bids.
**Expected:**
- Both return `201 Created`
- Different `bid_id` values
- Task's `bid_count` increments to 2

### BID-03 Self-bid is rejected

**Setup:** Alice creates a task.
**Action:** Alice bids on her own task: `jws(alice, {action: "submit_bid", task_id, bidder_id: alice.id, proposal: "..."})`.
**Expected:** `400`, `error = SELF_BID`

### BID-04 Duplicate bid is rejected

**Setup:** Alice creates a task. Bob bids.
**Action:** Bob bids again on the same task.
**Expected:** `409`, `error = BID_ALREADY_EXISTS`

### BID-05 Bid on non-OPEN task is rejected

**Setup:** Create a task, accept a bid (ACCEPTED status).
**Action:** Carol bids.
**Expected:** `409`, `error = INVALID_STATUS`

### BID-06 Bid on non-existent task

**Action:** `POST /tasks/t-00000000-0000-0000-0000-000000000000/bids` with valid JWS.
**Expected:** `404`, `error = TASK_NOT_FOUND`

### BID-07 Signer does not match `bidder_id`

**Setup:** Alice creates a task. Register `agent_bob` and `agent_carol`.
**Action:** Bob signs a bid with `bidder_id: carol.id` (impersonation).
**Expected:** `403`, `error = FORBIDDEN`

### BID-08 Wrong `action` in bid token

**Setup:** Alice creates a task. Register `agent_bob`.
**Action:** `jws(bob, {action: "create_task", task_id, bidder_id: bob.id, proposal: "..."})`.
**Expected:** `400`, `error = INVALID_PAYLOAD`

### BID-09 Missing `proposal` field

**Setup:** Alice creates a task. Register `agent_bob`.
**Action:** `jws(bob, {action: "submit_bid", task_id, bidder_id: bob.id})` — no `proposal` field.
**Expected:** `400`, `error = INVALID_PAYLOAD`

### BID-10 Empty proposal

**Setup:** Alice creates a task. Register `agent_bob`.
**Action:** Submit bid with `proposal: ""`.
**Expected:** `400`, `error = INVALID_PAYLOAD`

### BID-11 Proposal at max length (10,000 characters)

**Setup:** Alice creates a task. Register `agent_bob`.
**Action:** Submit bid with proposal of exactly 10,000 characters.
**Expected:** `201 Created`

### BID-12 Proposal exceeding max length

**Setup:** Alice creates a task. Register `agent_bob`.
**Action:** Submit bid with proposal of 10,001 characters.
**Expected:** `400`, `error = INVALID_PAYLOAD`

### BID-13 `task_id` in payload must match URL path

**Setup:** Alice creates task_1 and task_2. Register `agent_bob`.
**Action:** `POST /tasks/{task_1}/bids` with `jws(bob, {action: "submit_bid", task_id: task_2, bidder_id: bob.id, proposal: "..."})`.
**Expected:** `400`, `error = INVALID_PAYLOAD`

### BID-14 Concurrent duplicate bid race is safe

**Setup:** Alice creates a task. Prepare two identical bid requests from Bob.
**Action:** Send both simultaneously.
**Expected:**
- Exactly one `201 Created`
- Exactly one `409 Conflict` with `BID_ALREADY_EXISTS`

### BID-15 Malformed token on bid

**Action:** `POST /tasks/{task_id}/bids` with `{"token": ""}`.
**Expected:** `400`, `error = INVALID_JWS`

---

## Category 5: Bid Listing (`GET /tasks/{task_id}/bids`)

### BL-01 Poster can list bids during OPEN phase (sealed)

**Setup:** Alice creates a task. Bob bids.
**Action:** `GET /tasks/{task_id}/bids` with `Authorization: Bearer <jws(alice, {action: "list_bids", task_id, poster_id: alice.id})>`.
**Expected:**
- `200 OK`
- `task_id` matches
- `bids` array contains Bob's bid with `bid_id`, `bidder_id`, `proposal`, `submitted_at`

### BL-02 Non-poster cannot list bids during OPEN phase

**Setup:** Alice creates a task. Bob bids.
**Action:** `GET /tasks/{task_id}/bids` with `Authorization: Bearer <jws(bob, {action: "list_bids", task_id, poster_id: bob.id})>`.
**Expected:** `403`, `error = FORBIDDEN`

### BL-03 No auth header during OPEN phase returns error

**Setup:** Alice creates a task.
**Action:** `GET /tasks/{task_id}/bids` with no `Authorization` header.
**Expected:** `400`, `error = INVALID_JWS`

### BL-04 Bids are public after acceptance

**Setup:** Alice creates a task. Bob bids. Alice accepts Bob's bid (ACCEPTED status).
**Action:** `GET /tasks/{task_id}/bids` with no `Authorization` header.
**Expected:**
- `200 OK`
- `bids` array contains Bob's bid

### BL-05 Bids are public for CANCELLED tasks

**Setup:** Alice creates a task. Bob bids. Alice cancels.
**Action:** `GET /tasks/{task_id}/bids` with no `Authorization` header.
**Expected:** `200 OK`, `bids` array contains Bob's bid.

### BL-06 Bids are public for EXPIRED tasks

**Setup:** Create a task with a very short bidding deadline. Bob bids. Let the deadline pass (lazy eval triggers on GET).
**Action:** `GET /tasks/{task_id}/bids` with no `Authorization` header.
**Expected:** `200 OK`, `bids` array contains Bob's bid.

### BL-07 Empty bids list

**Setup:** Alice creates a task (no bids yet).
**Action:** Poster lists bids (with auth).
**Expected:** `200 OK`, `bids` is an empty array.

### BL-08 List bids for non-existent task

**Action:** `GET /tasks/t-00000000-0000-0000-0000-000000000000/bids`
**Expected:** `404`, `error = TASK_NOT_FOUND`

---

## Category 6: Bid Acceptance (`POST /tasks/{task_id}/bids/{bid_id}/accept`)

### BA-01 Poster accepts a bid

**Setup:** Alice creates a task. Bob bids. Capture `bid_id`.
**Action:** `POST /tasks/{task_id}/bids/{bid_id}/accept` with `jws(alice, {action: "accept_bid", task_id, bid_id, poster_id: alice.id})`.
**Expected:**
- `200 OK`
- `status` is `"accepted"`
- `worker_id` is `bob.agent_id`
- `accepted_bid_id` is `bid_id`
- `accepted_at` is valid ISO 8601 timestamp
- `execution_deadline` is approximately `accepted_at + deadline_seconds`

### BA-02 Non-poster cannot accept a bid

**Setup:** Alice creates a task. Bob bids. Register `agent_carol`.
**Action:** Carol attempts to accept: `jws(carol, {action: "accept_bid", task_id, bid_id, poster_id: carol.id})`.
**Expected:** `403`, `error = FORBIDDEN`

### BA-03 Accept non-existent bid

**Setup:** Alice creates a task.
**Action:** `POST /tasks/{task_id}/bids/bid-00000000-0000-0000-0000-000000000000/accept` with valid poster JWS.
**Expected:** `404`, `error = BID_NOT_FOUND`

### BA-04 Cannot accept bid on non-OPEN task

**Setup:** Alice creates a task. Bob bids. Alice accepts. Carol had also bid.
**Action:** Alice attempts to accept Carol's bid (task is already ACCEPTED).
**Expected:** `409`, `error = INVALID_STATUS`

### BA-05 Accept bid on non-existent task

**Action:** `POST /tasks/t-00000000-0000-0000-0000-000000000000/bids/bid-xxx/accept` with valid JWS.
**Expected:** `404`, `error = TASK_NOT_FOUND`

### BA-06 Wrong `action` in accept token

**Setup:** Alice creates a task. Bob bids.
**Action:** `jws(alice, {action: "cancel_task", task_id, bid_id, poster_id: alice.id})`.
**Expected:** `400`, `error = INVALID_PAYLOAD`

### BA-07 Impersonation on accept

**Setup:** Alice creates a task. Bob bids. Register `agent_carol`.
**Action:** Carol signs JWS with `poster_id: alice.id`.
**Expected:** `403`, `error = FORBIDDEN`

### BA-08 `bid_id` in payload must match URL path

**Setup:** Alice creates a task. Bob and Carol both bid.
**Action:** `POST /tasks/{task_id}/bids/{bob_bid_id}/accept` with JWS containing `bid_id: carol_bid_id`.
**Expected:** `400`, `error = INVALID_PAYLOAD`

### BA-09 `task_id` in payload must match URL path

**Setup:** Alice creates task_1 and task_2. Bob bids on task_1.
**Action:** `POST /tasks/{task_1}/bids/{bid_id}/accept` with JWS containing `task_id: task_2`.
**Expected:** `400`, `error = INVALID_PAYLOAD`

### BA-10 Accepting a bid updates `bid_count` correctly

**Setup:** Alice creates a task. Bob and Carol bid (bid_count = 2). Alice accepts Bob's bid.
**Action:** `GET /tasks/{task_id}`
**Expected:** `bid_count` remains `2` (bid_count reflects total bids, not pending bids).

---

## Category 7: Asset Upload (`POST /tasks/{task_id}/assets`)

### AU-01 Worker uploads a file

**Setup:** Alice creates a task. Bob bids and is accepted (ACCEPTED status, worker = Bob).
**Action:** `POST /tasks/{task_id}/assets` with:
- `Authorization: Bearer <jws(bob, {action: "upload_asset", task_id, worker_id: bob.id})>`
- Multipart form data with `file` part: `login-page.zip`, `application/zip`, ~1 KB content.
**Expected:**
- `201 Created`
- Body includes `asset_id`, `task_id`, `uploader_id`, `filename`, `content_type`, `size_bytes`, `uploaded_at`
- `asset_id` matches `asset-<uuid4>`
- `uploader_id` matches `bob.agent_id`
- `filename` is `"login-page.zip"`
- `content_type` is `"application/zip"`
- `size_bytes` matches the file size
- `uploaded_at` is valid ISO 8601 timestamp

### AU-02 Non-worker cannot upload

**Setup:** Create a task, accept Bob. Register `agent_carol`.
**Action:** Carol attempts to upload: `Authorization: Bearer <jws(carol, {action: "upload_asset", task_id, worker_id: carol.id})>`.
**Expected:** `403`, `error = FORBIDDEN`

### AU-03 Poster cannot upload

**Setup:** Alice creates a task, accepts Bob.
**Action:** Alice attempts to upload: `Authorization: Bearer <jws(alice, {action: "upload_asset", task_id, worker_id: alice.id})>`.
**Expected:** `403`, `error = FORBIDDEN`

### AU-04 Cannot upload to non-ACCEPTED task

**Setup:** Create a task (OPEN status, no bid accepted yet).
**Action:** Worker attempts to upload.
**Expected:** `409`, `error = INVALID_STATUS`

### AU-05 File exceeds max size

**Setup:** Create a task, accept Bob. Configure `assets.max_file_size` to a small value.
**Action:** Upload a file exceeding the limit.
**Expected:** `413`, `error = FILE_TOO_LARGE`

### AU-06 Max files per task exceeded

**Setup:** Create a task, accept Bob. Configure `assets.max_files_per_task` to 2. Upload 2 files.
**Action:** Upload a third file.
**Expected:** `409`, `error = TOO_MANY_ASSETS`

### AU-07 No file part in request

**Setup:** Create a task, accept Bob.
**Action:** `POST /tasks/{task_id}/assets` with `Authorization` header but no `file` part in multipart.
**Expected:** `400`, `error = NO_FILE`

### AU-08 Multiple uploads accumulate

**Setup:** Create a task, accept Bob. Upload file_1 and file_2.
**Action:** `GET /tasks/{task_id}/assets`
**Expected:**
- `200 OK`
- `assets` array has 2 entries, both with correct metadata

### AU-09 Upload to non-existent task

**Action:** `POST /tasks/t-00000000-0000-0000-0000-000000000000/assets` with valid JWS and file.
**Expected:** `404`, `error = TASK_NOT_FOUND`

### AU-10 Wrong `action` in upload token

**Setup:** Create a task, accept Bob.
**Action:** `Authorization: Bearer <jws(bob, {action: "submit_bid", task_id, worker_id: bob.id})>`.
**Expected:** `400`, `error = INVALID_PAYLOAD`

### AU-11 Impersonation on upload

**Setup:** Create a task, accept Bob. Register `agent_carol`.
**Action:** Carol signs JWS with `worker_id: bob.id`.
**Expected:** `403`, `error = FORBIDDEN`

---

## Category 8: Asset Retrieval (`GET /tasks/{task_id}/assets`, `GET /tasks/{task_id}/assets/{asset_id}`)

### AR-01 List assets for a task

**Setup:** Create a task, accept Bob. Upload 2 files.
**Action:** `GET /tasks/{task_id}/assets`
**Expected:**
- `200 OK`
- `task_id` matches
- `assets` array has 2 entries, each with `asset_id`, `uploader_id`, `filename`, `content_type`, `size_bytes`, `uploaded_at`
- `uploader_id` matches `bob.agent_id` for both entries

### AR-02 List assets for task with no assets

**Setup:** Create a task, accept Bob (no uploads yet).
**Action:** `GET /tasks/{task_id}/assets`
**Expected:**
- `200 OK`
- `assets` is an empty array

### AR-03 Download an asset

**Setup:** Create a task, accept Bob. Upload a file (`login-page.zip`, content `b"test content"`).
**Action:** `GET /tasks/{task_id}/assets/{asset_id}`
**Expected:**
- `200 OK`
- Response body is the exact file content
- `Content-Type` matches the uploaded MIME type
- `Content-Disposition` includes `filename="login-page.zip"`

### AR-04 Download non-existent asset

**Setup:** Create a task.
**Action:** `GET /tasks/{task_id}/assets/asset-00000000-0000-0000-0000-000000000000`
**Expected:** `404`, `error = ASSET_NOT_FOUND`

### AR-05 Asset endpoints for non-existent task

**Action:**
- `GET /tasks/t-00000000-0000-0000-0000-000000000000/assets`
- `GET /tasks/t-00000000-0000-0000-0000-000000000000/assets/asset-xxx`
**Expected:** `404`, `error = TASK_NOT_FOUND` for both.

### AR-06 Asset endpoints require no authentication

**Setup:** Create a task, accept Bob. Upload a file.
**Action:** `GET /tasks/{task_id}/assets` and `GET /tasks/{task_id}/assets/{asset_id}` with no Authorization header.
**Expected:** `200 OK` for both (public endpoints).

---

## Category 9: Deliverable Submission (`POST /tasks/{task_id}/submit`)

### SUB-01 Worker submits deliverable

**Setup:** Alice creates a task. Bob bids and is accepted. Bob uploads at least one asset.
**Action:** `POST /tasks/{task_id}/submit` with `jws(bob, {action: "submit_deliverable", task_id, worker_id: bob.id})`.
**Expected:**
- `200 OK`
- `status` is `"submitted"`
- `submitted_at` is valid ISO 8601 timestamp
- `review_deadline` is approximately `submitted_at + review_deadline_seconds`

### SUB-02 Non-worker cannot submit

**Setup:** Create a task, accept Bob. Bob uploads assets. Register `agent_carol`.
**Action:** Carol submits: `jws(carol, {action: "submit_deliverable", task_id, worker_id: carol.id})`.
**Expected:** `403`, `error = FORBIDDEN`

### SUB-03 Poster cannot submit

**Setup:** Alice creates a task, accepts Bob. Bob uploads assets.
**Action:** Alice submits: `jws(alice, {action: "submit_deliverable", task_id, worker_id: alice.id})`.
**Expected:** `403`, `error = FORBIDDEN`

### SUB-04 Cannot submit without assets

**Setup:** Create a task, accept Bob. No assets uploaded.
**Action:** Bob submits.
**Expected:** `400`, `error = NO_ASSETS`

### SUB-05 Cannot submit from non-ACCEPTED status

**Setup:** Create a task (OPEN, no bid accepted).
**Action:** Worker attempts to submit.
**Expected:** `409`, `error = INVALID_STATUS`

### SUB-06 Cannot submit from SUBMITTED status (double submit)

**Setup:** Create a task, accept Bob. Bob uploads assets and submits.
**Action:** Bob submits again.
**Expected:** `409`, `error = INVALID_STATUS`

### SUB-07 Wrong `action` in submit token

**Setup:** Create a task, accept Bob. Bob uploads assets.
**Action:** `jws(bob, {action: "upload_asset", task_id, worker_id: bob.id})`.
**Expected:** `400`, `error = INVALID_PAYLOAD`

### SUB-08 Submit on non-existent task

**Action:** `POST /tasks/t-00000000-0000-0000-0000-000000000000/submit` with valid JWS.
**Expected:** `404`, `error = TASK_NOT_FOUND`

### SUB-09 `task_id` in payload must match URL path

**Setup:** Alice creates task_1 and task_2. Bob bids on task_1 and is accepted. Bob uploads an asset to task_1.
**Action:** `POST /tasks/{task_1}/submit` with `jws(bob, {action: "submit_deliverable", task_id: task_2, worker_id: bob.id})`.
**Expected:** `400`, `error = INVALID_PAYLOAD`

---

## Category 10: Approval (`POST /tasks/{task_id}/approve`)

### APP-01 Poster approves deliverable

**Setup:** Full lifecycle to SUBMITTED status (Alice creates, Bob bids, accepted, uploads asset, submits).
**Action:** `POST /tasks/{task_id}/approve` with `jws(alice, {action: "approve_task", task_id, poster_id: alice.id})`.
**Expected:**
- `200 OK`
- `status` is `"approved"`
- `approved_at` is valid ISO 8601 timestamp
- Escrow released to worker (mock Central Bank confirms release to Bob)

### APP-02 Non-poster cannot approve

**Setup:** Full lifecycle to SUBMITTED. Register `agent_carol`.
**Action:** Carol approves: `jws(carol, {action: "approve_task", task_id, poster_id: carol.id})`.
**Expected:** `403`, `error = FORBIDDEN`

### APP-03 Worker cannot approve

**Setup:** Full lifecycle to SUBMITTED.
**Action:** Bob approves: `jws(bob, {action: "approve_task", task_id, poster_id: bob.id})`.
**Expected:** `403`, `error = FORBIDDEN`

### APP-04 Cannot approve non-SUBMITTED task

**Setup:** Create a task (OPEN status).
**Action:** Poster approves.
**Expected:** `409`, `error = INVALID_STATUS`

### APP-05 Cannot approve already-approved task

**Setup:** Full lifecycle to APPROVED.
**Action:** Poster approves again.
**Expected:** `409`, `error = INVALID_STATUS`

### APP-06 Wrong `action` in approve token

**Setup:** Full lifecycle to SUBMITTED.
**Action:** `jws(alice, {action: "dispute_task", task_id, poster_id: alice.id})`.
**Expected:** `400`, `error = INVALID_PAYLOAD`

### APP-07 Central Bank unavailable during escrow release on approve

**Setup:** Full lifecycle to SUBMITTED. Configure Central Bank mock to return error on release.
**Action:** Poster approves.
**Expected:** `502`, `error = CENTRAL_BANK_UNAVAILABLE`

### APP-08 Approve on non-existent task

**Action:** `POST /tasks/t-00000000-0000-0000-0000-000000000000/approve` with valid JWS.
**Expected:** `404`, `error = TASK_NOT_FOUND`

### APP-09 `task_id` in payload must match URL path

**Setup:** Full lifecycle to SUBMITTED on task_1. Alice also creates task_2.
**Action:** `POST /tasks/{task_1}/approve` with `jws(alice, {action: "approve_task", task_id: task_2, poster_id: alice.id})`.
**Expected:** `400`, `error = INVALID_PAYLOAD`

---

## Category 11: Dispute (`POST /tasks/{task_id}/dispute`)

### DIS-01 Poster disputes deliverable

**Setup:** Full lifecycle to SUBMITTED status.
**Action:** `POST /tasks/{task_id}/dispute` with `jws(alice, {action: "dispute_task", task_id, poster_id: alice.id, reason: "The login page does not validate email format."})`.
**Expected:**
- `200 OK`
- `status` is `"disputed"`
- `disputed_at` is valid ISO 8601 timestamp
- `dispute_reason` matches the submitted reason

### DIS-02 Non-poster cannot dispute

**Setup:** Full lifecycle to SUBMITTED. Register `agent_carol`.
**Action:** Carol disputes: `jws(carol, {action: "dispute_task", task_id, poster_id: carol.id, reason: "..."})`.
**Expected:** `403`, `error = FORBIDDEN`

### DIS-03 Worker cannot dispute

**Setup:** Full lifecycle to SUBMITTED.
**Action:** Bob disputes: `jws(bob, {action: "dispute_task", task_id, poster_id: bob.id, reason: "..."})`.
**Expected:** `403`, `error = FORBIDDEN`

### DIS-04 Cannot dispute non-SUBMITTED task

**Setup:** Create a task (OPEN status).
**Action:** Poster disputes.
**Expected:** `409`, `error = INVALID_STATUS`

### DIS-05 Empty dispute reason

**Setup:** Full lifecycle to SUBMITTED.
**Action:** Dispute with `reason: ""`.
**Expected:** `400`, `error = INVALID_REASON`

### DIS-06 Dispute reason exceeding max length

**Setup:** Full lifecycle to SUBMITTED.
**Action:** Dispute with `reason` of 10,001 characters.
**Expected:** `400`, `error = INVALID_REASON`

### DIS-07 Dispute reason at exactly max length (10,000 characters)

**Setup:** Full lifecycle to SUBMITTED.
**Action:** Dispute with `reason` of exactly 10,000 characters.
**Expected:** `200 OK`

### DIS-08 Wrong `action` in dispute token

**Setup:** Full lifecycle to SUBMITTED.
**Action:** `jws(alice, {action: "approve_task", task_id, poster_id: alice.id, reason: "..."})`.
**Expected:** `400`, `error = INVALID_PAYLOAD`

### DIS-09 Dispute on non-existent task

**Action:** `POST /tasks/t-00000000-0000-0000-0000-000000000000/dispute` with valid JWS.
**Expected:** `404`, `error = TASK_NOT_FOUND`

### DIS-10 `task_id` in payload must match URL path

**Setup:** Full lifecycle to SUBMITTED on task_1. Alice also creates task_2.
**Action:** `POST /tasks/{task_1}/dispute` with `jws(alice, {action: "dispute_task", task_id: task_2, poster_id: alice.id, reason: "Mismatch test"})`.
**Expected:** `400`, `error = INVALID_PAYLOAD`

---

## Category 12: Ruling (`POST /tasks/{task_id}/ruling`)

### RUL-01 Platform records a ruling

**Setup:** Full lifecycle to DISPUTED status.
**Action:** `POST /tasks/{task_id}/ruling` with `jws(platform_agent, {action: "record_ruling", task_id, ruling_id: "rul-<uuid4>", worker_pct: 40, ruling_summary: "Worker delivered but omitted email validation..."})`.
**Expected:**
- `200 OK`
- `status` is `"ruled"`
- `ruled_at` is valid ISO 8601 timestamp
- `ruling_id` matches the submitted value
- `worker_pct` is `40`
- `ruling_summary` matches the submitted value

### RUL-02 Non-platform agent cannot record ruling

**Setup:** Full lifecycle to DISPUTED. Register `agent_alice`.
**Action:** Alice attempts: `jws(alice, {action: "record_ruling", task_id, ruling_id: "rul-xxx", worker_pct: 50, ruling_summary: "..."})`.
**Expected:** `403`, `error = FORBIDDEN`

### RUL-03 Cannot rule on non-DISPUTED task

**Setup:** Full lifecycle to SUBMITTED (not disputed).
**Action:** Platform records ruling.
**Expected:** `409`, `error = INVALID_STATUS`

### RUL-04 `worker_pct` boundary: 0 (full poster win)

**Setup:** Full lifecycle to DISPUTED.
**Action:** Ruling with `worker_pct: 0`.
**Expected:** `200 OK`, `worker_pct` is `0`.

### RUL-05 `worker_pct` boundary: 100 (full worker win)

**Setup:** Full lifecycle to DISPUTED.
**Action:** Ruling with `worker_pct: 100`.
**Expected:** `200 OK`, `worker_pct` is `100`.

### RUL-06 Invalid `worker_pct` values

**Setup:** Full lifecycle to DISPUTED.
**Action:** Submit ruling with each of these `worker_pct` values:
- `-1`
- `101`
- `50.5` (float)
- `"fifty"` (string)
- `null`
**Expected:** `400`, `error = INVALID_WORKER_PCT` for each.

### RUL-07 Missing required fields in ruling payload

**Setup:** Full lifecycle to DISPUTED.
**Action:** Omit each of `ruling_id`, `worker_pct`, `ruling_summary` in separate requests.
**Expected:** `400`, `error = INVALID_PAYLOAD` for each.

### RUL-08 Empty `ruling_summary`

**Setup:** Full lifecycle to DISPUTED.
**Action:** Ruling with `ruling_summary: ""`.
**Expected:** `400`, `error = INVALID_PAYLOAD`

### RUL-09 Empty `ruling_id`

**Setup:** Full lifecycle to DISPUTED.
**Action:** Ruling with `ruling_id: ""`.
**Expected:** `400`, `error = INVALID_PAYLOAD`

### RUL-10 Wrong `action` in ruling token

**Setup:** Full lifecycle to DISPUTED.
**Action:** `jws(platform_agent, {action: "approve_task", task_id, ...})`.
**Expected:** `400`, `error = INVALID_PAYLOAD`

### RUL-11 Ruling on non-existent task

**Action:** `POST /tasks/t-00000000-0000-0000-0000-000000000000/ruling` with valid platform JWS.
**Expected:** `404`, `error = TASK_NOT_FOUND`

### RUL-12 Cannot rule twice

**Setup:** Full lifecycle to RULED.
**Action:** Platform records another ruling.
**Expected:** `409`, `error = INVALID_STATUS`

### RUL-13 `task_id` in payload must match URL path

**Setup:** Full lifecycle to DISPUTED on task_1. Alice also creates task_2.
**Action:** `POST /tasks/{task_1}/ruling` with `jws(platform_agent, {action: "record_ruling", task_id: task_2, ruling_id: "rul-<uuid4>", worker_pct: 50, ruling_summary: "Mismatch test"})`.
**Expected:** `400`, `error = INVALID_PAYLOAD`

---

## Category 13: Lifecycle / Deadline Tests

### LIFE-01 Full happy path: create → bid → accept → upload → submit → approve

**Setup:** Register `agent_alice`, `agent_bob`. Fund Alice.
**Action:** Execute the full lifecycle:
1. Alice creates a task (201, status = open)
2. Bob bids (201, bid_id returned)
3. Alice lists bids (200, sealed access with poster auth)
4. Alice accepts Bob's bid (200, status = accepted, worker = bob)
5. Bob uploads an asset (201, asset_id returned)
6. Bob submits deliverable (200, status = submitted)
7. Alice approves (200, status = approved, escrow released to bob)
**Expected:** Each step returns the expected status code and the task transitions through OPEN → ACCEPTED → SUBMITTED → APPROVED.

### LIFE-02 Dispute flow: create → bid → accept → upload → submit → dispute → ruling

**Setup:** Register `agent_alice`, `agent_bob`, `platform_agent`. Fund Alice.
**Action:** Execute the lifecycle through dispute:
1. Alice creates a task
2. Bob bids and is accepted
3. Bob uploads and submits
4. Alice disputes with a reason
5. Platform records a ruling (worker_pct: 60)
**Expected:** Task transitions through OPEN → ACCEPTED → SUBMITTED → DISPUTED → RULED. Final task record has `dispute_reason`, `ruling_id`, `worker_pct`, `ruling_summary` populated.

### LIFE-03 Bidding deadline auto-expires (lazy evaluation)

**Setup:** Alice creates a task with `bidding_deadline_seconds: 1` (1 second).
**Action:** Wait 2 seconds. `GET /tasks/{task_id}`.
**Expected:**
- `200 OK`
- `status` is `"expired"`
- `expired_at` is valid ISO 8601 timestamp
- Escrow released back to poster

### LIFE-04 Execution deadline auto-expires (lazy evaluation)

**Setup:** Alice creates a task with `deadline_seconds: 1`. Bob bids and is accepted.
**Action:** Wait 2 seconds. `GET /tasks/{task_id}`.
**Expected:**
- `200 OK`
- `status` is `"expired"`
- `expired_at` is populated
- Escrow released back to poster

### LIFE-05 Review deadline auto-approves (lazy evaluation)

**Setup:** Alice creates a task with `review_deadline_seconds: 1`. Bob bids, is accepted, uploads, and submits.
**Action:** Wait 2 seconds. `GET /tasks/{task_id}`.
**Expected:**
- `200 OK`
- `status` is `"approved"`
- `approved_at` is populated
- Escrow released to worker (auto-approve protects worker)

### LIFE-06 Lazy evaluation triggers on GET /tasks (list)

**Setup:** Alice creates a task with `bidding_deadline_seconds: 1`.
**Action:** Wait 2 seconds. `GET /tasks`.
**Expected:**
- The task appears in the list with `status: "expired"`

### LIFE-07 Lazy evaluation triggers on status-dependent operations

**Setup:** Alice creates a task with `bidding_deadline_seconds: 1`.
**Action:** Wait 2 seconds. Bob attempts to bid.
**Expected:** `409`, `error = INVALID_STATUS` (task is expired, not open).

### LIFE-08 Concurrent deadline expiration is safe

**Setup:** Create a task with `bidding_deadline_seconds: 1`. Wait for deadline to pass.
**Action:** Send two concurrent `GET /tasks/{task_id}` requests.
**Expected:**
- Both return `200 OK` with `status: "expired"`
- Escrow release is called exactly once (idempotent)

### LIFE-09 Terminal states block all mutations

**Action:** For each terminal state (CANCELLED, APPROVED, RULED, EXPIRED), attempt:
- Cancel (expect `409 INVALID_STATUS`)
- Submit bid (expect `409 INVALID_STATUS`)
- Submit deliverable (expect `409 INVALID_STATUS`)
- Approve (expect `409 INVALID_STATUS`)
- Dispute (expect `409 INVALID_STATUS`)
- Record ruling (expect `409 INVALID_STATUS`)
**Expected:** All attempts return `409`, `error = INVALID_STATUS`.

### LIFE-10 Deadline evaluation does not affect tasks in terminal states

**Setup:** Create a task and cancel it (CANCELLED, terminal). The bidding deadline may still be in the future.
**Action:** `GET /tasks/{task_id}` after the bidding deadline would have passed.
**Expected:**
- `200 OK`
- `status` remains `"cancelled"` (not overwritten to `"expired"`)

### LIFE-11 Operations on ACCEPTED task only (not OPEN, not SUBMITTED)

**Setup:** Create a task, accept Bob (ACCEPTED status).
**Action:**
- Cancel attempt: `409 INVALID_STATUS`
- Bid attempt: `409 INVALID_STATUS`
- Approve attempt: `409 INVALID_STATUS`
- Dispute attempt: `409 INVALID_STATUS`
- Ruling attempt: `409 INVALID_STATUS`
**Expected:** Only upload and submit are valid in ACCEPTED status. All others rejected.

### LIFE-12 Operations on SUBMITTED task only (not ACCEPTED, not OPEN)

**Setup:** Full lifecycle to SUBMITTED.
**Action:**
- Cancel attempt: `409 INVALID_STATUS`
- Bid attempt: `409 INVALID_STATUS`
- Upload attempt: `409 INVALID_STATUS`
- Submit attempt: `409 INVALID_STATUS`
- Ruling attempt: `409 INVALID_STATUS`
**Expected:** Only approve and dispute are valid in SUBMITTED status. All others rejected.

---

## Category 14: Health Endpoint (`GET /health`)

### HEALTH-01 Health schema is correct

**Action:** `GET /health`
**Expected:**
- `200 OK`
- Body contains `status`, `uptime_seconds`, `started_at`, `total_tasks`, `tasks_by_status`
- `status = "ok"`
- `tasks_by_status` contains keys: `open`, `accepted`, `submitted`, `approved`, `cancelled`, `disputed`, `ruled`, `expired`

### HEALTH-02 Total task count is exact

**Setup:** Create `N` tasks.
**Action:** `GET /health`
**Expected:** `total_tasks = N`

### HEALTH-03 Uptime is monotonic

**Action:** Call `GET /health` twice with delay >= 1 second.
**Expected:** second `uptime_seconds` > first `uptime_seconds`

### HEALTH-04 Tasks by status reflects actual state

**Setup:** Create 2 tasks. Cancel 1.
**Action:** `GET /health`
**Expected:** `tasks_by_status.open = 1`, `tasks_by_status.cancelled = 1`, all others = 0.

---

## Category 15: HTTP Method and Endpoint Misuse

### HTTP-01 Wrong method on defined routes is blocked

**Action:** Send unsupported methods:
- `GET /tasks` — `PUT`, `DELETE`, `PATCH` → `405`
- `POST /tasks` — `PUT`, `DELETE`, `PATCH` → `405`
- `GET /tasks/{task_id}` — `PUT`, `DELETE`, `PATCH`, `POST` → `405`
- `POST /tasks/{id}/cancel` — `GET`, `PUT`, `DELETE` → `405`
- `POST /tasks/{id}/bids` — `PUT`, `DELETE`, `PATCH` → `405`
- `GET /tasks/{id}/bids` — `PUT`, `DELETE`, `PATCH` → `405`
- `POST /tasks/{id}/bids/{bid_id}/accept` — `GET`, `PUT`, `DELETE` → `405`
- `POST /tasks/{id}/assets` — `PUT`, `DELETE`, `PATCH` → `405`
- `GET /tasks/{id}/assets` — `PUT`, `DELETE`, `PATCH`, `POST` → `405`
- `GET /tasks/{id}/assets/{asset_id}` — `PUT`, `DELETE`, `PATCH`, `POST` → `405`
- `POST /tasks/{id}/submit` — `GET`, `PUT`, `DELETE` → `405`
- `POST /tasks/{id}/approve` — `GET`, `PUT`, `DELETE` → `405`
- `POST /tasks/{id}/dispute` — `GET`, `PUT`, `DELETE` → `405`
- `POST /tasks/{id}/ruling` — `GET`, `PUT`, `DELETE` → `405`
- `GET /health` — `POST`, `PUT`, `DELETE` → `405`
**Expected:** `405`, `error = METHOD_NOT_ALLOWED` for each.

---

## Category 16: Error Precedence

These tests verify that errors are returned in the documented precedence order when multiple error conditions are present.

### PREC-01 Content-Type checked before token validation

**Action:** `POST /tasks` with `Content-Type: text/plain` and body `{"task_token": "invalid"}`.
**Expected:** `415`, `error = UNSUPPORTED_MEDIA_TYPE` (NOT `400 INVALID_JWS`)

### PREC-02 Body size checked before token validation

**Action:** `POST /tasks` with `Content-Type: application/json` and a body exceeding `request.max_body_size`.
**Expected:** `413`, `error = PAYLOAD_TOO_LARGE` (NOT `400 INVALID_JWS`)

### PREC-03 JSON parsing checked before token validation

**Action:** `POST /tasks` with `Content-Type: application/json` and body `{not json`.
**Expected:** `400`, `error = INVALID_JSON` (NOT `400 INVALID_JWS`)

### PREC-04 Token validation checked before payload validation

**Action:** `POST /tasks/{id}/cancel` with `{"token": 12345}`.
**Expected:** `400`, `error = INVALID_JWS` (NOT `400 INVALID_PAYLOAD`)

### PREC-05 Identity service checked before payload validation

**Setup:** Configure Task Board to point to a non-running Identity service.
**Action:** `POST /tasks/{id}/cancel` with a syntactically valid JWS.
**Expected:** `502`, `error = IDENTITY_SERVICE_UNAVAILABLE` (NOT `400 INVALID_PAYLOAD`)

### PREC-06 Signature validity checked before payload content

**Setup:** Register `agent_alice`. Create a tampered JWS with `action: "wrong_action"`.
**Action:** `POST /tasks/{id}/cancel` with the tampered JWS (invalid signature AND wrong action).
**Expected:** `403`, `error = FORBIDDEN` (NOT `400 INVALID_PAYLOAD`)

### PREC-07 Payload `action` checked before signer matching

**Setup:** Register `agent_alice` and `agent_bob`. Alice creates a task.
**Action:** Bob sends cancel with `jws(bob, {action: "submit_bid", task_id, poster_id: alice.id})` (wrong action AND signer mismatch for poster operations).
**Expected:** `400`, `error = INVALID_PAYLOAD` (NOT `403 FORBIDDEN`)

### PREC-08 Task lookup checked before role-dependent signer matching

**Setup:** Register `agent_alice` and `agent_bob`.
**Action:** Bob cancels non-existent task: `jws(bob, {action: "cancel_task", task_id: "t-00000000-0000-0000-0000-999999999999", poster_id: bob.id})`.
**Expected:** `404`, `error = TASK_NOT_FOUND`

Signer-role matching (step 9: "is this signer the task's poster?") requires loading the task record. Since the task does not exist, `TASK_NOT_FOUND` fires first. The signer's signature validity was already confirmed at step 6.

### PREC-09 Task status checked before domain validation

**Setup:** Create a task and approve it (APPROVED, terminal).
**Action:** Poster disputes: `jws(alice, {action: "dispute_task", task_id, poster_id: alice.id, reason: ""})` (wrong status AND empty reason).
**Expected:** `409`, `error = INVALID_STATUS` (NOT `400 INVALID_REASON`)

### PREC-10 Token mismatch checked before Central Bank errors

**Setup:** Register `agent_alice`. Configure Central Bank to be unavailable.
**Action:** `POST /tasks` with mismatched `task_id` between tokens.
**Expected:** `400`, `error = TOKEN_MISMATCH` (NOT `502 CENTRAL_BANK_UNAVAILABLE`)

---

## Category 17: Cross-Cutting Security Assertions

### SEC-01 Error envelope consistency

**Action:** For at least one failing test per error code, assert response has exactly:
- top-level `error` (string)
- top-level `message` (string)
- top-level `details` (object)
**Expected:** All failures comply. `details` is an object (may be empty `{}`).

### SEC-02 No internal error leakage

**Action:** Trigger representative failures (`INVALID_JSON`, `FORBIDDEN`, `TASK_NOT_FOUND`, `INVALID_STATUS`, `CENTRAL_BANK_UNAVAILABLE`).
**Expected:** `message` never includes stack traces, SQL fragments, file paths, private key material, internal service URLs, or driver internals.

### SEC-03 Task IDs are opaque and client-generated format

**Action:** Create 5+ tasks with client-generated `t-<uuid4>` IDs.
**Expected:** Every returned `task_id` matches the client-generated value and follows `t-<uuid4>` format.

### SEC-04 Bid IDs are opaque and random-format

**Action:** Submit 5+ bids.
**Expected:** Every returned `bid_id` matches `bid-<uuid4>`.

### SEC-05 Asset IDs are opaque and random-format

**Action:** Upload 5+ assets.
**Expected:** Every returned `asset_id` matches `asset-<uuid4>`.

### SEC-06 Escrow IDs match expected format

**Action:** Create 5+ tasks.
**Expected:** Every returned `escrow_id` matches `esc-<uuid4>`.

### SEC-07 Cross-action token replay is rejected

**Setup:** Register `agent_alice` and `agent_bob`. Alice creates a task. Bob submits a bid (captures bid JWS token).
**Action:** Replay Bob's bid JWS against `POST /tasks/{task_id}/submit`.
**Expected:** `400`, `error = INVALID_PAYLOAD` (action is `"submit_bid"`, expected `"submit_deliverable"`)

### SEC-08 SQL injection in path parameters

**Action:** Send SQL injection strings in path parameters:
- `GET /tasks/' OR '1'='1`
- `GET /tasks/' OR '1'='1/bids`
- `GET /tasks/' OR '1'='1/assets`
**Expected:**
- All return `404`
- No SQL fragments, stack traces, or internal diagnostics in any response body

### SEC-09 Path traversal in asset download

**Action:**
- `GET /tasks/{valid_task_id}/assets/../../etc/passwd`
- `GET /tasks/{valid_task_id}/assets/../../../config.yaml`
**Expected:**
- `404`, `error = ASSET_NOT_FOUND`
- No file content leaked from outside the asset store

---

## Release Gate Checklist

Service is release-ready only if:

1. All tests in this document pass.
2. No test marked deterministic has alternate acceptable behavior.
3. No endpoint returns `500` in any test scenario.
4. All failing responses conform to the required error envelope.
5. The Identity service being unavailable never causes the Task Board to crash — it returns `502` gracefully.
6. The Central Bank being unavailable never causes the Task Board to crash — it returns `502` gracefully.
7. All deadline-triggered state transitions work correctly via lazy evaluation.
8. Sealed bids are not accessible to non-posters during OPEN phase.

---

## Coverage Summary

| Category | IDs | Count |
|----------|-----|-------|
| Task Creation | TC-01 to TC-28 (TC-14a/b/c) | 30 |
| Task Queries | TQ-01 to TQ-13 | 13 |
| Task Cancellation | CAN-01 to CAN-09 | 9 |
| Bidding | BID-01 to BID-15 | 15 |
| Bid Listing | BL-01 to BL-08 | 8 |
| Bid Acceptance | BA-01 to BA-10 | 10 |
| Asset Upload | AU-01 to AU-11 | 11 |
| Asset Retrieval | AR-01 to AR-06 | 6 |
| Deliverable Submission | SUB-01 to SUB-09 | 9 |
| Approval | APP-01 to APP-09 | 9 |
| Dispute | DIS-01 to DIS-10 | 10 |
| Ruling | RUL-01 to RUL-13 | 13 |
| Lifecycle / Deadlines | LIFE-01 to LIFE-12 | 12 |
| Health | HEALTH-01 to HEALTH-04 | 4 |
| HTTP Method Misuse | HTTP-01 | 1 |
| Error Precedence | PREC-01 to PREC-10 | 10 |
| Cross-Cutting Security | SEC-01 to SEC-09 | 9 |
| **Total** | | **179** |

| Endpoint | Covered By |
|----------|------------|
| `POST /tasks` | TC-01 to TC-28, PREC-01 to PREC-03, PREC-10, SEC-01, SEC-03, SEC-06 |
| `GET /tasks` | TQ-05 to TQ-11, LIFE-06, TQ-12 |
| `GET /tasks/{task_id}` | TQ-01 to TQ-04, TQ-12, TQ-13, LIFE-03 to LIFE-10 |
| `POST /tasks/{id}/cancel` | CAN-01 to CAN-09, PREC-04 to PREC-09, LIFE-09, LIFE-11 |
| `POST /tasks/{id}/bids` | BID-01 to BID-15, LIFE-07, LIFE-09, LIFE-11, LIFE-12 |
| `GET /tasks/{id}/bids` | BL-01 to BL-08 |
| `POST /tasks/{id}/bids/{bid_id}/accept` | BA-01 to BA-10, LIFE-09, LIFE-11 |
| `POST /tasks/{id}/assets` | AU-01 to AU-11, LIFE-11 |
| `GET /tasks/{id}/assets` | AR-01, AR-02, AR-05, AR-06 |
| `GET /tasks/{id}/assets/{asset_id}` | AR-03 to AR-06, SEC-09 |
| `POST /tasks/{id}/submit` | SUB-01 to SUB-09, LIFE-09, LIFE-11, LIFE-12, SEC-07 |
| `POST /tasks/{id}/approve` | APP-01 to APP-09, LIFE-05, LIFE-09, LIFE-12 |
| `POST /tasks/{id}/dispute` | DIS-01 to DIS-10, LIFE-09, LIFE-12 |
| `POST /tasks/{id}/ruling` | RUL-01 to RUL-13, LIFE-09 |
| `GET /health` | HEALTH-01 to HEALTH-04 |
