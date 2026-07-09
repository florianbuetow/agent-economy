# Reputation Service - Production Release Test Specification

## Purpose

This document is the release-gate test specification for the Reputation Service.
It is intentionally strict and unambiguous:

- Every negative case has one required status code and one required error code.
- Every failing response must use the standard error envelope.
- Any behavior not listed here is out of scope for release sign-off.

This document focuses only on core functionality and endpoint abuse resistance.
Nice-to-have tests are intentionally excluded.

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

| Status | Error Code                 | Required When |
|--------|----------------------------|---------------|
| 400    | `MISSING_FIELD`            | A required field is absent, `null`, or empty string |
| 400    | `INVALID_FIELD_TYPE`       | A required field has the wrong JSON type |
| 400    | `INVALID_JSON`             | Request body is malformed JSON |
| 400    | `INVALID_RATING`           | Rating is not `dissatisfied`, `satisfied`, or `extremely_satisfied` |
| 400    | `INVALID_CATEGORY`         | Category is not `spec_quality` or `delivery_quality` |
| 400    | `SELF_FEEDBACK`            | `from_agent_id` equals `to_agent_id` |
| 400    | `COMMENT_TOO_LONG`         | Comment exceeds configured max length |
| 404    | `FEEDBACK_NOT_FOUND`       | Referenced `feedback_id` does not exist or is sealed |
| 405    | `METHOD_NOT_ALLOWED`       | Unsupported HTTP method on a defined route |
| 409    | `FEEDBACK_EXISTS`          | Duplicate feedback for same (task_id, from_agent_id, to_agent_id) |
| 413    | `PAYLOAD_TOO_LARGE`        | Request body exceeds configured max size |
| 415    | `UNSUPPORTED_MEDIA_TYPE`   | `Content-Type` is not `application/json` for JSON endpoints |

---

## Test Data Conventions

- Agent IDs use the format `a-<uuid4>` (e.g., `a-550e8400-e29b-41d4-a716-446655440000`).
- Task IDs use the format `t-<uuid4>` (e.g., `t-660e8400-e29b-41d4-a716-446655440000`).
- Feedback IDs returned by the service must match `fb-<uuid4>`.
- All timestamps must be valid ISO 8601.
- The reveal timeout is configured in `config.yaml` as `feedback.reveal_timeout_seconds`.

---

## Category 1: Feedback Submission (`POST /feedback`)

### FB-01 Submit valid feedback (delivery quality)
**Setup:** Generate two agent IDs and a task ID.
**Action:** Submit `{task_id, from_agent_id: alice, to_agent_id: bob, category: "delivery_quality", rating: "satisfied", comment: "Good work"}`
**Expected:**
- `201 Created`
- Body includes `feedback_id`, `task_id`, `from_agent_id`, `to_agent_id`, `category`, `rating`, `comment`, `submitted_at`, `visible`
- `feedback_id` matches `fb-<uuid4>`
- `submitted_at` is valid ISO 8601 timestamp
- `visible` is `false` (counterpart not yet submitted)

### FB-02 Submit valid feedback (spec quality)
**Setup:** Generate two agent IDs and a task ID.
**Action:** Submit `{task_id, from_agent_id: bob, to_agent_id: alice, category: "spec_quality", rating: "extremely_satisfied", comment: "Very clear spec"}`
**Expected:**
- `201 Created`
- `category` is `"spec_quality"`
- `rating` is `"extremely_satisfied"`

### FB-03 Submit feedback without comment
**Action:** Submit valid feedback with `comment` omitted entirely.
**Expected:**
- `201 Created`
- `comment` is `null` in response

### FB-04 Submit feedback with null comment
**Action:** Submit valid feedback with `"comment": null`.
**Expected:**
- `201 Created`
- `comment` is `null` in response

### FB-05 Submit feedback with empty comment
**Action:** Submit valid feedback with `"comment": ""`.
**Expected:**
- `201 Created`
- `comment` is `""` in response

### FB-06 Duplicate feedback is rejected
**Setup:** Submit valid feedback for (task_1, alice, bob).
**Action:** Submit identical feedback again for (task_1, alice, bob).
**Expected:**
- `409 Conflict`
- `error = FEEDBACK_EXISTS`

### FB-07 Same task, reverse direction is allowed
**Setup:** Submit feedback for (task_1, alice→bob).
**Action:** Submit feedback for (task_1, bob→alice).
**Expected:**
- `201 Created`
- Different `feedback_id` from the first submission

### FB-08 Same agents, different task is allowed
**Setup:** Submit feedback for (task_1, alice→bob).
**Action:** Submit feedback for (task_2, alice→bob).
**Expected:**
- `201 Created`
- Different `feedback_id`

### FB-09 Self-feedback is rejected
**Action:** Submit `{from_agent_id: alice, to_agent_id: alice, ...}`
**Expected:**
- `400 Bad Request`
- `error = SELF_FEEDBACK`

### FB-10 Comment exceeding max length is rejected
**Action:** Submit feedback with comment of 257 characters (one over limit).
**Expected:**
- `400 Bad Request`
- `error = COMMENT_TOO_LONG`

### FB-11 Comment at exactly max length is accepted
**Action:** Submit feedback with comment of exactly 256 characters.
**Expected:**
- `201 Created`

### FB-12 Invalid rating value
**Action:** Submit feedback with `rating: "excellent"`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_RATING`

### FB-13 Invalid category value
**Action:** Submit feedback with `category: "timeliness"`.
**Expected:**
- `400 Bad Request`
- `error = INVALID_CATEGORY`

### FB-14 Missing required fields (one at a time)
**Action:** Omit each of `task_id`, `from_agent_id`, `to_agent_id`, `category`, `rating` in separate requests.
**Expected:** `400`, `error = MISSING_FIELD` for each

### FB-15 Null required fields
**Action:** `{"task_id": null, "from_agent_id": null, "to_agent_id": null, "category": null, "rating": null}`
**Expected:** `400`, `error = MISSING_FIELD`

### FB-16 Wrong field types
**Action:** `{"task_id": 123, "from_agent_id": true, "to_agent_id": [], "category": 42, "rating": {}}`
**Expected:** `400`, `error = INVALID_FIELD_TYPE`

### FB-17 Malformed JSON body
**Action:** Send truncated/invalid JSON.
**Expected:** `400`, `error = INVALID_JSON`

### FB-18 Wrong content type
**Action:** `Content-Type: text/plain` with JSON-looking body.
**Expected:** `415`, `error = UNSUPPORTED_MEDIA_TYPE`

### FB-19 Mass-assignment resistance (extra fields)
**Action:** Send `feedback_id`, `submitted_at`, `visible`, `is_admin` alongside valid fields.
**Expected:**
- `201 Created`
- Service-generated `feedback_id` and `submitted_at` are used
- Extra fields are ignored

### FB-20 Concurrent duplicate feedback race is safe
**Setup:** Prepare two identical feedback requests in parallel.
**Action:** Send both simultaneously.
**Expected:**
- Exactly one `201 Created`
- Exactly one `409 Conflict` with `FEEDBACK_EXISTS`

### FB-21 All three rating values are accepted
**Action:** Submit three separate feedbacks (different task IDs) with `dissatisfied`, `satisfied`, `extremely_satisfied`.
**Expected:** All three return `201 Created` with the correct rating echoed back.

### FB-22 Oversized request body
**Action:** Send a ~2MB JSON body to `POST /feedback`.
**Expected:**
- `413 Payload Too Large`
- `error = PAYLOAD_TOO_LARGE`

### FB-23 Duplicate with different rating still rejected
**Setup:** Submit feedback for (task_1, alice→bob) with `rating: "satisfied"`.
**Action:** Submit feedback for (task_1, alice→bob) with `rating: "extremely_satisfied"` and different category.
**Expected:**
- `409 Conflict`
- `error = FEEDBACK_EXISTS`
- Uniqueness is on `(task_id, from_agent_id, to_agent_id)`, not on rating or category

### FB-24 Unicode characters in comment
**Action:** Submit feedback with emoji, CJK, and accented characters in the comment field.
**Expected:**
- `201 Created`
- Comment is preserved exactly as submitted

### FB-25 Empty string agent IDs rejected
**Action:** Submit feedback with `from_agent_id: ""`, `to_agent_id: ""`, and `task_id: ""` in separate requests.
**Expected:** `400`, `error = MISSING_FIELD` for each — empty strings are treated as missing

---

## Category 2: Visibility / Sealed Feedback

### VIS-01 Single-direction feedback is sealed
**Setup:** Submit feedback for (task_1, alice→bob).
**Action:** `GET /feedback/task/{task_1}`
**Expected:**
- `200 OK`
- `feedback` array is empty (sealed, not yet revealed)

### VIS-02 Both directions submitted reveals both
**Setup:** Submit feedback for (task_1, alice→bob), then (task_1, bob→alice).
**Action:** `GET /feedback/task/{task_1}`
**Expected:**
- `200 OK`
- `feedback` array contains exactly 2 entries
- Both feedbacks are present

### VIS-03 Second submission returns visible=true
**Setup:** Submit feedback for (task_1, alice→bob) — returns `visible: false`.
**Action:** Submit feedback for (task_1, bob→alice).
**Expected:**
- `201 Created`
- `visible` is `true`

### VIS-04 Sealed feedback returns 404 on direct lookup
**Setup:** Submit feedback for (task_1, alice→bob), get back `feedback_id`.
**Action:** `GET /feedback/{feedback_id}` (before counterpart is submitted).
**Expected:**
- `404 Not Found`
- `error = FEEDBACK_NOT_FOUND`

### VIS-05 Revealed feedback returns 200 on direct lookup
**Setup:** Submit both directions for task_1, get back a `feedback_id`.
**Action:** `GET /feedback/{feedback_id}`
**Expected:**
- `200 OK`
- Full feedback record returned

### VIS-06 Agent feedback query excludes sealed
**Setup:** Submit feedback for (task_1, alice→bob) only.
**Action:** `GET /feedback/agent/{bob}` (bob is `to_agent_id`)
**Expected:**
- `200 OK`
- `feedback` array is empty

### VIS-07 Agent feedback query includes revealed
**Setup:** Submit both directions for task_1 (alice→bob and bob→alice).
**Action:** `GET /feedback/agent/{bob}`
**Expected:**
- `200 OK`
- `feedback` array contains the feedback where `to_agent_id = bob`

### VIS-08 Revealing does not affect other tasks
**Setup:** Submit both directions for task_1. Submit only alice→bob for task_2.
**Action:** `GET /feedback/task/{task_2}`
**Expected:**
- `200 OK`
- `feedback` array is empty (task_2 feedback is still sealed)

### VIS-09 Timeout reveals sealed feedback
**Setup:** Configure `feedback.reveal_timeout_seconds` to 2 seconds. Submit feedback for (task_1, alice→bob) only (no counterpart).
**Action:** Wait 3 seconds. `GET /feedback/task/{task_1}`
**Expected:**
- `200 OK`
- `feedback` array contains exactly 1 entry (the timed-out feedback)
- The feedback is visible despite no counterpart submission

**Note:** This test requires either a short timeout configuration or a test hook to override `reveal_timeout_seconds`. The default 86400 seconds (24 hours) is too long for automated tests.

---

## Category 3: Feedback Lookup (`GET /feedback/{feedback_id}`)

### READ-01 Lookup revealed feedback
**Setup:** Submit both directions for a task, capture `feedback_id`.
**Action:** `GET /feedback/{feedback_id}`
**Expected:**
- `200 OK`
- Body contains `feedback_id`, `task_id`, `from_agent_id`, `to_agent_id`, `category`, `rating`, `comment`, `submitted_at`, `visible`
- `visible` is `true`

### READ-02 Lookup non-existent feedback
**Action:** `GET /feedback/fb-00000000-0000-0000-0000-000000000000`
**Expected:**
- `404 Not Found`
- `error = FEEDBACK_NOT_FOUND`

### READ-03 Malformed feedback ID
**Action:** `GET /feedback/not-a-valid-id` and `GET /feedback/../../etc/passwd`
**Expected:**
- `404` for each
- No stack traces, filesystem paths, or internal diagnostics in response body

### READ-04 SQL injection in path parameters
**Action:** Send SQL injection strings in path parameters:
- `GET /feedback/' OR '1'='1`
- `GET /feedback/agent/' OR '1'='1`
- `GET /feedback/task/' OR '1'='1`
**Expected:**
- Feedback lookup returns `404`
- Agent and task lookups return `200` with empty arrays
- No SQL fragments, stack traces, or internal diagnostics in any response body

### READ-05 Idempotent read returns identical response
**Setup:** Submit both directions to reveal a feedback record. Capture `feedback_id`.
**Action:** Call `GET /feedback/{feedback_id}` twice.
**Expected:** Both responses are `200` and byte-for-byte identical JSON.

---

## Category 4: Task Feedback (`GET /feedback/task/{task_id}`)

### TASK-01 No feedback for task
**Action:** `GET /feedback/task/{random_task_id}`
**Expected:**
- `200 OK`
- `task_id` matches the requested ID
- `feedback` is an empty array

### TASK-02 Visible feedback appears in task query
**Setup:** Submit both directions for task_1.
**Action:** `GET /feedback/task/{task_1}`
**Expected:**
- `200 OK`
- `feedback` array has exactly 2 entries
- Each entry has `feedback_id`, `from_agent_id`, `to_agent_id`, `category`, `rating`, `comment`, `submitted_at`, `visible`
- Both entries have `visible: true`
- Entries are ordered chronologically by `submitted_at`

---

## Category 5: Agent Feedback (`GET /feedback/agent/{agent_id}`)

### AGENT-01 No feedback about agent
**Action:** `GET /feedback/agent/{random_agent_id}`
**Expected:**
- `200 OK`
- `agent_id` matches the requested ID
- `feedback` is an empty array

### AGENT-02 Feedback about agent from multiple tasks
**Setup:** Submit and reveal feedback for task_1 (alice→bob) and task_2 (carol→bob).
**Action:** `GET /feedback/agent/{bob}`
**Expected:**
- `200 OK`
- `feedback` array has exactly 2 entries (from alice and from carol)

### AGENT-03 Feedback BY agent not included
**Setup:** Submit and reveal feedback for task_1 (alice→bob and bob→alice).
**Action:** `GET /feedback/agent/{bob}`
**Expected:**
- `200 OK`
- `feedback` array has exactly 1 entry (alice's feedback about bob)
- Bob's feedback about alice is NOT included (bob is `from_agent_id`, not `to_agent_id`)

---

## Category 6: Health Endpoint (`GET /health`)

### HEALTH-01 Health schema is correct
**Action:** `GET /health`
**Expected:**
- `200 OK`
- Body contains `status`, `uptime_seconds`, `started_at`, `total_feedback`
- `status = "ok"`

### HEALTH-02 Total feedback count is exact
**Setup:** Submit `N` feedback records.
**Action:** `GET /health`
**Expected:** `total_feedback = N`

### HEALTH-03 Uptime is monotonic
**Action:** Call `GET /health` twice with delay >= 1 second.
**Expected:** second `uptime_seconds` > first `uptime_seconds`

---

## Category 7: HTTP Method and Endpoint Misuse

### HTTP-01 Wrong method on defined routes is blocked
**Action:** Send unsupported methods:
- `GET /feedback` (POST only)
- `PUT /feedback`
- `DELETE /feedback`
- `POST /feedback/{feedback_id}`
- `PUT /feedback/{feedback_id}`
- `DELETE /feedback/{feedback_id}`
- `POST /feedback/task/{task_id}`
- `PUT /feedback/task/{task_id}`
- `DELETE /feedback/task/{task_id}`
- `POST /feedback/agent/{agent_id}`
- `PUT /feedback/agent/{agent_id}`
- `DELETE /feedback/agent/{agent_id}`
- `POST /health`
**Expected:** `405`, `error = METHOD_NOT_ALLOWED` for each

---

## Category 8: Cross-Cutting Security Assertions

### SEC-01 Error envelope consistency
**Action:** For at least one failing test per error code, assert response has exactly:
- top-level `error` (string)
- top-level `message` (string)
- top-level `details` (object)
**Expected:** All failures comply. `details` is an object (may be empty `{}`).

### SEC-02 No internal error leakage
**Action:** Trigger representative failures (`INVALID_JSON`, `SELF_FEEDBACK`, `FEEDBACK_EXISTS`, malformed ID).
**Expected:** `message` never includes stack traces, SQL fragments, file paths, or driver internals

### SEC-03 Feedback IDs are opaque and random-format
**Action:** Submit 5+ feedback records.
**Expected:** Every returned ID matches `fb-<uuid4>`

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
| Feedback Submission | FB-01 to FB-25 | 25 |
| Visibility / Sealed | VIS-01 to VIS-09 | 9 |
| Feedback Lookup | READ-01 to READ-05 | 5 |
| Task Feedback | TASK-01 to TASK-02 | 2 |
| Agent Feedback | AGENT-01 to AGENT-03 | 3 |
| Health | HEALTH-01 to HEALTH-03 | 3 |
| HTTP misuse | HTTP-01 | 1 |
| Cross-cutting security | SEC-01 to SEC-03 | 3 |
| **Total** |  | **51** |

| Endpoint | Covered By |
|----------|------------|
| `POST /feedback` | FB-01 to FB-25, SEC-01, SEC-02 |
| `GET /feedback/{feedback_id}` | READ-01 to READ-05, VIS-04, VIS-05 |
| `GET /feedback/task/{task_id}` | TASK-01, TASK-02, VIS-01, VIS-02, VIS-08, VIS-09, READ-04 |
| `GET /feedback/agent/{agent_id}` | AGENT-01 to AGENT-03, VIS-06, VIS-07, READ-04 |
| `GET /health` | HEALTH-01 to HEALTH-03 |
