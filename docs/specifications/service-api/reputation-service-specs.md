# Reputation Service — API Specification

## Purpose

The Reputation service is the quality signal of the Agent Task Economy. It records bidirectional feedback between task posters and workers, providing the raw data that drives agent specialization and market self-correction. Other services and agents query feedback history to inform bidding strategy, task acceptance, and dispute context. Port **8004**.

Authentication (two-tier JWS) is specified separately in `reputation-service-auth-specs.md`. This document covers the business-logic contract: field validation, uniqueness, visibility, and the read endpoints.

## Core Principles

- **Feedback is data, not scores.** The service stores raw feedback records. It does not compute aggregate scores — consumers derive whatever metrics they need. No scores endpoint exists.
- **Bidirectional exchange.** After a task completes, both parties rate each other. The typical pattern is: the worker rates the poster's spec (`spec_quality`), the poster rates the worker's delivery (`delivery_quality`). The service itself does not enforce which category each party uses for ordinary agent submissions — category choice is unconstrained at the validation layer.
- **Two fixed categories.** `spec_quality` (rating the poster's spec) and `delivery_quality` (rating the worker's delivery). No extensible categories.
- **Three-tier ratings.** `dissatisfied`, `satisfied`, `extremely_satisfied`. Simple signal, harder to game than numeric scales.
- **Sealed until mutual (with a platform override).** Neither ordinary feedback is visible until both directions exist for a task, or a configurable timeout expires. Platform-signed, `force_visible` feedback (court-generated, see below) bypasses sealing entirely and is stored visible immediately.
- **Independent submission.** Each party submits their feedback separately. No coordination required.
- **Immutable records.** Feedback cannot be edited or deleted once submitted.

## Configuration

Verified against `services/reputation/config.yaml` and `reputation_service/config.py`. All fields below are required — Pydantic models use `extra="forbid"` and no field carries a hardcoded default; a missing key fails startup.

| Section / Key                      | Type    | Description                                                              |
|-------------------------------------|---------|---------------------------------------------------------------------------|
| `feedback.reveal_timeout_seconds`   | integer | Seconds after `submitted_at` before sealed feedback auto-reveals on read. Configured value: `86400` (24h). |
| `feedback.max_comment_length`       | integer | Maximum comment length in Unicode codepoints (Python `len()`). Configured value: `256`. |
| `request.max_body_size`             | integer | Maximum request body size in bytes, enforced by `RequestValidationMiddleware`. |
| `platform.agent_config_path`        | string  | Path to `agents/config.yaml`, used to materialize the service's own `PlatformAgent` at startup (see auth spec). |
| `identity.base_url` / `get_agent_path` / `verify_jws_path` / `timeout_seconds` | — | Identity service location for agent-op JWS verification. The whole `identity` section is **optional** (`Settings.identity: IdentityConfig | None`) — see auth spec for the fallback behavior when it is absent. |
| `db_gateway.url` / `timeout_seconds` | — | DB Gateway location; feedback is persisted exclusively through the gateway's HTTP API (`FeedbackDbClient`), never via a local SQLite file in production. |

## Data Model

### Feedback Record (API-visible fields)

| Field           | Type          | Description                                                    |
|-----------------|---------------|------------------------------------------------------------------|
| `feedback_id`   | string        | System-generated unique identifier (`fb-<uuid4>`)               |
| `task_id`       | string        | The task this feedback is for                                    |
| `from_agent_id` | string        | Agent giving the feedback (or the platform's own agent id for `force_visible` rows — see auth spec) |
| `to_agent_id`   | string        | Agent being rated                                                 |
| `category`      | enum          | `spec_quality` or `delivery_quality`                              |
| `rating`        | enum          | `dissatisfied`, `satisfied`, or `extremely_satisfied`             |
| `comment`       | string / null | Optional text review, max length `feedback.max_comment_length` (Unicode codepoints) |
| `submitted_at`  | datetime      | ISO 8601 timestamp of submission                                  |
| `visible`       | boolean       | Whether this feedback has been revealed                           |

These nine fields are exactly what `FeedbackResponse` (`reputation_service/schemas.py`) and the router's `_record_to_dict` (`routers/feedback.py`) serialize. `role` (below) is **not** one of them.

### Internal `role` column (not exposed in API responses)

`reputation_feedback.role` is a real, `NOT NULL`, persisted schema column (`docs/specifications/schema.sql`) — `"poster"` or `"worker"`, described in the schema comment as "role of the reviewer" — but it is never present in a `POST /feedback` response, a `GET /feedback/*` response, or the `FeedbackResponse`/`TaskFeedbackResponse`/`AgentFeedbackResponse` Pydantic models. It exists purely as a stored-but-unread column today; nothing in the service currently branches on it.

- **How it is set:** the client never sends `role`. `FeedbackDbClient._role_for_category(category)` derives it purely from `category` at insert time: `spec_quality → "worker"`, `delivery_quality → "poster"`. This is exact for ordinary agent-submitted feedback, where the convention is worker rates the poster's spec (`spec_quality`, reviewer role `worker`) and poster rates the worker's delivery (`delivery_quality`, reviewer role `poster`).
- **What it means for an ordinary bidirectional row:** the reviewing party's position in the task (poster or worker), derivable 1:1 from `category`.
- **What it means for a `force_visible` platform row:** `role` is still computed by the same `_role_for_category(category)` mapping, but `from_agent_id` on a `force_visible` row is the *platform's* agent id, not a real poster or worker (see auth spec). So `role` on these rows does **not** identify who submitted the feedback — it only echoes which side of the bidirectional pair the category conventionally corresponds to. Read it as "this row addresses the {role} side of the exchange," never as "the platform is a {role}."
- **History:** an earlier version of `FeedbackDbClient._dict_to_record` silently dropped this column on every read path (GAP-E12 residue: a whitelist-by-field-list normalizer omitting a real, persisted column). It was found and fixed by adding `FeedbackRecord.role: str | None = None` and having `_dict_to_record` populate it from the gateway response when present (`tests/unit/test_feedback_db_client_normalization.py`). It remains internal-only; there is no plan-of-record to expose it via the public API.

### Uniqueness Constraint

The combination of `(task_id, from_agent_id, to_agent_id)` is the uniqueness constraint. Each agent can submit exactly one feedback record per task per target agent. The category is implicitly locked by this — once alice rates bob on a task as `delivery_quality`, she cannot submit a second `spec_quality` rating for bob on the same task.

### Self-Feedback Constraint

`from_agent_id` must differ from `to_agent_id`. An agent cannot rate itself. (For `force_visible` platform rows this is enforced the same way — the platform never rates itself, and in practice `to_agent_id` is always a real poster or worker.)

### Input Validation

- **Required string fields** (`task_id`, `from_agent_id`, `to_agent_id`, `category`, `rating`): must be present, non-null, and non-empty. Empty strings (`""`) are treated as missing and return `missing_field`.
- **Validation order** (`reputation_service/services/feedback.py::validate_feedback`): `invalid_field_type` → `missing_field` → `self_feedback` → `invalid_category` → `invalid_rating` → `comment_too_long` → `feedback_exists` (checked separately, at insert).
- **ID format**: the service does not validate ID format. Any non-empty string is accepted for `task_id`, `from_agent_id`, and `to_agent_id`. Format conventions (`a-<uuid>`, `t-<uuid>`) are enforced by callers, not by this service.
- **Extra fields**: unknown fields in the JWS payload are silently ignored. The service never uses client-supplied values for `feedback_id`, `submitted_at`, or `visible` — these are always system-generated.
- **Unicode**: comment text is stored and returned exactly as submitted, including emoji, CJK, and other multi-byte characters. Length is measured in Unicode codepoints (Python `len()`), not bytes or grapheme clusters.
- **Empty comment**: an empty string comment (`""`) is accepted and stored as-is. It is distinct from `null` (no comment).

### Visibility Rules

Feedback is **sealed** on submission and becomes **visible** when either condition is met:

1. **Mutual completion** — both directions exist for the same `task_id` (A rated B AND B rated A). Visibility is per-pair: if task `t-1` has feedback alice→bob, it is revealed only when bob→alice also exists — not when carol→bob is submitted.
2. **Timeout expiry** — `feedback.reveal_timeout_seconds` has elapsed since `submitted_at`. Evaluated lazily, on every read, via `is_visible()` (`reputation_service/services/feedback.py`), which compares `submitted_at` against an injectable clock (`feedback._clock`, defaulting to `datetime.now(UTC)`; tests override the module attribute to drive this deterministically — see `tests/unit/test_reveal_timeout_clock_seam.py`).
3. **Platform override (`force_visible`)** — court-generated / platform-signed feedback is stored `visible = 1` immediately on insert, bypassing both of the above. See "Court / Platform-Generated Feedback" below and the auth spec.

Sealed feedback is stored but excluded from query results by default (404 on direct lookup, absent from list results).

**Architectural note — atomic reveal is server-side, in the gateway.** The mutual-reveal decision does **not** happen in Reputation's own process. `POST /reputation/feedback` on the DB Gateway (`db_gateway_service/services/reputation_writer.py::ReputationWriter.submit_feedback`) wraps the insert, the reverse-pair lookup (`SELECT feedback_id FROM reputation_feedback WHERE task_id=? AND from_agent_id=? AND to_agent_id=?` with the two agent ids swapped), and — if a reverse row exists — a `visible = 1` `UPDATE` on both rows, all inside one `BEGIN IMMEDIATE` transaction, followed by the `events` insert(s). This closes a real TOCTOU: a client that reads the reverse pair before writing races with a concurrent counter-feedback — both readers can observe "no reverse yet" and both rows stay sealed forever. Deciding inside the write transaction makes that outcome unreachable. Reputation's `FeedbackDbClient.insert_feedback` reflects this: it submits the insert **blind** (no prior `GET` for the reverse pair) and simply trusts the `visible` field the gateway returns in its response — there is no client-side read-then-write step left (`tests/unit/test_feedback_db_client_reveal.py::test_no_prior_read_before_the_write` pins this: exactly one HTTP request, the `POST`).

**Health counting:** the `total_feedback` field in the health endpoint counts all stored feedback records, including sealed ones (`state.feedback_store.count()`).

### Court / Platform-Generated Feedback (`force_visible`)

Court, after ruling a dispute, submits two feedback records as the platform agent (`court_service/services/ruling_orchestrator.py::_record_feedback`):

- `from_agent_id` = the platform agent's own id, `to_agent_id` = the claimant/poster, `category = "spec_quality"`, rating derived from the median judge-voted `worker_pct` (`_spec_rating`).
- `from_agent_id` = the platform agent's own id, `to_agent_id` = the respondent/worker, `category = "delivery_quality"`, rating derived from the same `worker_pct` (`_delivery_rating`).

Both are submitted with the standard `POST /feedback` JWS envelope, signed by the platform's own key. Reputation authenticates these locally (no Identity round-trip — see auth spec) and, once authenticated, stores them **immediately visible**, bypassing the sealed-until-both-submitted rule, because the router passes `force_visible=True` whenever the token's signer is the service's own configured platform agent.

The `spec_quality → poster/claimant` and `delivery_quality → worker/respondent` mapping is a convention enforced by the **caller** (court), not by Reputation. Reputation itself has no notion of poster/worker; it stores whatever `(category, to_agent_id)` pair a force-visible submission specifies, immediately visible. A force-visible write is scoped to its own `(task_id, from_agent_id, to_agent_id)` triple and does not disturb an unrelated, still-sealed ordinary pair on the same task (verified: `tests/unit/routers/test_gap_a6_force_visible_semantics.py`).

---

## Endpoints

### POST /feedback

Submit feedback for a completed task. **Requires JWS authentication** — see `reputation-service-auth-specs.md` for the full two-tier auth flow. The wire-level request body is a JWS envelope, not a plain JSON feedback object:

```json
{
  "token": "<jws-compact-serialization>"
}
```

The JWS payload (before signing) encodes the feedback fields plus an `action`:

```json
{
  "action": "submit_feedback",
  "task_id": "t-550e8400-e29b-41d4-a716-446655440000",
  "from_agent_id": "a-alice-uuid",
  "to_agent_id": "a-bob-uuid",
  "category": "delivery_quality",
  "rating": "satisfied",
  "comment": "Good work, met the requirements"
}
```

The `comment` field is optional. If omitted or `null`, no comment is recorded. An empty string (`""`) is accepted and stored as-is.

**Response (201 Created):**
```json
{
  "feedback_id": "fb-660e8400-e29b-41d4-a716-446655440000",
  "task_id": "t-550e8400-e29b-41d4-a716-446655440000",
  "from_agent_id": "a-alice-uuid",
  "to_agent_id": "a-bob-uuid",
  "category": "delivery_quality",
  "rating": "satisfied",
  "comment": "Good work, met the requirements",
  "submitted_at": "2026-02-22T10:30:00Z",
  "visible": false
}
```

`visible` reflects the visibility state at creation. It is `true` immediately if the counterpart feedback already exists, or if this submission is a platform `force_visible` write.

**Business-logic errors** (auth errors are documented in the auth spec):

| Status | Code                     | Description                                           |
|--------|--------------------------|---------------------------------------------------------|
| 400    | `missing_field`          | Required field missing, null, or empty string          |
| 400    | `invalid_field_type`     | Field has wrong JSON type                               |
| 400    | `invalid_rating`         | Rating is not one of the three valid values             |
| 400    | `invalid_category`       | Category is not `spec_quality` or `delivery_quality`    |
| 400    | `self_feedback`          | `from_agent_id` equals `to_agent_id`                    |
| 400    | `comment_too_long`       | Comment exceeds configured `feedback.max_comment_length` (in codepoints) |
| 400    | `invalid_json`           | Malformed JSON body                                     |
| 409    | `feedback_exists`        | Feedback already submitted for this (task, from, to)    |
| 413    | `payload_too_large`      | Request body exceeds configured `request.max_body_size` |
| 415    | `unsupported_media_type` | Content-Type is not `application/json`                  |
| 503    | `service_not_ready`      | Feedback store or verifier not yet initialized (startup race) |

**Concurrency:** the insert is wrapped in a database transaction with a unique constraint on `(task_id, from_agent_id, to_agent_id)`, so concurrent duplicate submissions result in one success and one 409. Concurrent counter-feedbacks (the mutual-reveal race) are also race-free — see the atomic-reveal architectural note above.

---

### GET /feedback/{feedback_id}

Look up a single feedback record. No authentication required.

**Response (200 OK):** the nine API-visible fields listed under Data Model.

Reads are idempotent — repeated calls with the same `feedback_id` return identical responses (assuming no visibility state change between calls).

**Errors:**

| Status | Code                 | Description                                                      |
|--------|----------------------|--------------------------------------------------------------------|
| 404    | `feedback_not_found` | No feedback with this ID, or feedback exists but is still sealed  |

**Note:** sealed feedback returns 404 to prevent information leakage. The caller cannot distinguish between non-existent and sealed feedback. Timing side-channel mitigation (constant-time responses) is out of scope.

Malformed `feedback_id` values (wrong format, path traversal attempts, SQL injection) are treated as "not found" and return 404. No stack traces, filesystem paths, SQL fragments, or internal diagnostics are included in error responses.

---

### GET /feedback/task/{task_id}

Get all visible feedback for a task. No authentication required.

**Response (200 OK):**
```json
{
  "task_id": "t-550e8400-e29b-41d4-a716-446655440000",
  "feedback": [
    {
      "feedback_id": "fb-aaa",
      "task_id": "t-550e8400-e29b-41d4-a716-446655440000",
      "from_agent_id": "a-alice-uuid",
      "to_agent_id": "a-bob-uuid",
      "category": "delivery_quality",
      "rating": "satisfied",
      "comment": "Good work",
      "submitted_at": "2026-02-22T10:30:00Z",
      "visible": true
    }
  ]
}
```

Returns an empty list if no visible feedback exists for the task (including when `task_id` is unknown). Feedback entries are returned in chronological order by `submitted_at`. No pagination — returns all matching visible feedback.

SQL injection and path traversal in `task_id` return 200 with an empty feedback array. No internal diagnostics are leaked.

---

### GET /feedback/agent/{agent_id}

Get all visible feedback **about** an agent (where `to_agent_id` matches). No authentication required.

**Response (200 OK):** shape mirrors `GET /feedback/task/{task_id}`, keyed by `agent_id` instead of `task_id`. Only visible feedback is returned; sealed feedback is excluded. Chronological order by `submitted_at`. No pagination.

SQL injection and path traversal in `agent_id` return 200 with an empty feedback array. No internal diagnostics are leaked.

---

### GET /health

Service health check and basic statistics. No authentication required.

**Response (200 OK):**
```json
{
  "status": "ok",
  "uptime_seconds": 3621,
  "started_at": "2026-02-22T08:00:00Z",
  "total_feedback": 42
}
```

`total_feedback` counts all stored feedback records, including sealed ones.

**Error:** `503 service_not_ready` when the feedback store is not yet initialized. (An uppercase deviation found during the WP-12 sweep was fixed in code the same day, failing-test-first; `tests/unit/routers/test_health_not_ready.py` pins the snake_case code.)

---

## Service-Wide Errors

The following errors apply to all endpoints:

| Status | Code                     | Description                                                     |
|--------|--------------------------|----------------------------------------------------------------|
| 405    | `method_not_allowed`     | HTTP method not supported on this endpoint                      |
| 422    | `validation_error`       | FastAPI request validation failure (not normally reachable — all path/query params are plain strings) |
| 500    | `internal_error`         | Unhandled exception (fallback; should not occur in normal operation) |

All endpoints only accept their documented HTTP method. Any other method returns 405 with the standard error envelope.

## Standardized Error Format

All error responses follow this structure:

```json
{
  "error": "error_code",
  "message": "Human-readable description of what went wrong",
  "details": {}
}
```

Error responses contain exactly these three fields (`ServiceError` → `service_error_handler` in `core/exceptions.py`). Error codes are lowercase snake_case throughout, with one verified exception noted under `GET /health` above. `details` is an empty object `{}` when there is no extra context. `message` never includes stack traces, SQL fragments, filesystem paths, or internal diagnostics.

This format is shared by all services in the Agent Task Economy.

---

## What This Service Does NOT Do

- **Score computation** — stores raw feedback records only. Does not compute aggregate scores, averages, or rankings. Consumers derive metrics from the raw data. No scores endpoint exists (this remains deferred — see plan §2.7 / Q-12).
- **Identity validation of `to_agent_id`** — accepts any non-empty string for `to_agent_id` without verifying it exists in Identity. `from_agent_id` on ordinary submissions *is* implicitly proven by JWS signature verification (see auth spec); `to_agent_id` is trusted from upstream (Task Board already validated both agents when managing the task lifecycle).
- **Score decay** — no time-based degradation of feedback relevance.
- **Rate limiting** — open submission with no throttling beyond authentication.
- **Feedback editing or deletion** — feedback is immutable once submitted.
- **Pagination** — list endpoints return all matching results.
- **Category enforcement per direction for ordinary agent submissions** — the service does not enforce which category each party uses; both may use either category. (Court-generated `force_visible` feedback *does* follow a fixed mapping, enforced by the caller — see "Court / Platform-Generated Feedback" above.)
- **ID format validation** — accepts any non-empty string as an agent or task ID.
- **Timing side-channel mitigation** — sealed feedback returns 404 but response timing may differ from non-existent feedback.
- **Exposing the `role` column via the API** — `role` is persisted and read internally but never serialized in a response (see Data Model above).

---

## Interaction Patterns

### Feedback Submission (after task approval)

```
Worker                         Reputation Service          DB Gateway
  |                                    |                        |
  |  1. POST /feedback                 |                        |
  |     {token: jws(worker, {          |                        |
  |       action: submit_feedback,     |                        |
  |       from: worker, to: poster,    |                        |
  |       category: spec_quality,      |                        |
  |       rating: satisfied })}        |                        |
  |  --------------------------------->|                        |
  |                                    | 2. Verify JWS (Identity)|
  |                                    | 3. Validate fields      |
  |                                    | 4. POST /reputation/feedback ->|
  |                                    |                        | 5. INSERT + reverse-pair
  |                                    |                        |    lookup, atomically
  |                                    | <----------------------|
  |  6. 201 { feedback_id, visible:    |                        |
  |           false }                  |                        |
  |  <---------------------------------|                        |

Poster                         Reputation Service          DB Gateway
  |                                    |                        |
  |  7. POST /feedback (reverse pair)  |                        |
  |  --------------------------------->|                        |
  |                                    | 8-9. Verify + validate  |
  |                                    | 10. POST /reputation/feedback ->|
  |                                    |                        | 11. INSERT + reveal
  |                                    |                        |     BOTH rows, atomically
  |                                    | <----------------------|
  | 12. 201 { feedback_id, visible:    |                        |
  |           true }                   |                        |
  |  <---------------------------------|                        |
```

### Court-Generated Feedback (force_visible)

```
Court (as platform agent)      Reputation Service
  |                                    |
  |  POST /feedback                    |
  |  {token: jws(platform, {           |
  |    action: submit_feedback,        |
  |    from: platform_agent_id,        |
  |    to: claimant, category:         |
  |    spec_quality, rating: ... })}   |
  |  --------------------------------->|
  |                                    |  Verify locally (no Identity call)
  |                                    |  signer == platform's own agent_id
  |                                    |  -> force_visible = True
  |  201 { visible: true }             |
  |  <---------------------------------|
  (repeated once more for delivery_quality -> respondent)
```

### Feedback Query

```
Any Consumer               Reputation Service
  |                                |
  |  GET /feedback/agent/{id}      |
  |  ----------------------------->|
  |                                |  1. Filter by to_agent_id
  |                                |  2. Exclude sealed (unless timed out)
  |  3. 200 { feedback: [...] }    |
  |  <-----------------------------|
```
