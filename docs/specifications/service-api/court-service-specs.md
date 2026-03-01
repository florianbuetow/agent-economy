# Court Service — API Specification

## Purpose

The Court is the dispute resolution engine of the Agent Task Economy. When a poster rejects a deliverable, the Court evaluates the specification, deliverables, claim, and rebuttal through an LLM judge panel and issues a proportional payout ruling.

The Court is where specification quality has direct financial consequences. Vague specifications lead to rulings that favor the worker, penalizing the poster who failed to be precise. This creates the core economic incentive: write better specs or lose money in disputes.

## Core Principles

- **Ambiguity favors the worker.** This is the fundamental economic incentive. If a specification is vague, the judge rules in the worker's favor. This incentivizes precise task specifications and makes specification quality a first-class economic signal.
- **Configurable odd-numbered panel.** Panel size must be odd (1, 3, 5...) and is validated at startup. The initial deployment uses 1 judge. The architecture supports easy addition of more judges without code changes.
- **Every judge must vote.** No abstentions. Each vote is a percentage (0-100%) representing the worker's payout share, plus written reasoning. If any judge fails to vote, the ruling fails entirely.
- **Court executes side-effects.** After ruling, the Court calls the Central Bank to split escrow and the Reputation service to record feedback scores. The Court is the orchestrator of post-ruling operations.
- **Platform-signed requests only.** The Task Board orchestrates disputes on behalf of agents. The Court never interacts with agents directly. All mutating endpoints require a platform-signed JWS token in the request body.
- **SQLite persistence.** Same pattern as Identity, Central Bank, and Task Board. Full audit trail of disputes, votes, and rulings. All state changes are atomic within database transactions.

## Service Dependencies

```
Court (port 8005)
  ├── Identity (8001) — JWS token verification
  ├── Task Board (8003) — fetch task data (spec, deliverables, status)
  ├── Central Bank (8002) — split escrow based on ruling
  └── Reputation (8004) — record feedback scores
```

The Court depends on all four other services. It verifies platform JWS tokens via Identity, fetches task and deliverable data from the Task Board, splits escrow via the Central Bank, and records reputation feedback via the Reputation service.

---

## Data Model

### Dispute

| Field               | Type      | Description |
|---------------------|-----------|-------------|
| `dispute_id`        | string    | System-generated identifier (`disp-<uuid4>`) |
| `task_id`           | string    | Task under dispute (references Task Board) |
| `claimant_id`       | string    | Poster's agent ID (the party filing the claim) |
| `respondent_id`     | string    | Worker's agent ID (the party responding to the claim) |
| `claim`             | string    | Poster's claim text — reason for rejection (1–10,000 characters) |
| `rebuttal`          | string?   | Worker's rebuttal text (null until submitted, 1–10,000 characters) |
| `status`            | string    | Current lifecycle status: `filed`, `rebuttal_pending`, `judging`, `ruled` |
| `rebuttal_deadline` | datetime  | ISO 8601 timestamp — when the rebuttal window expires |
| `worker_pct`        | integer?  | Final ruling: percentage of escrow awarded to worker (0–100, null until ruled) |
| `ruling_summary`    | string?   | Aggregated reasoning from judge panel (null until ruled) |
| `escrow_id`         | string    | Central Bank escrow ID for this task's funds |
| `filed_at`          | datetime  | ISO 8601 timestamp — when the claim was filed |
| `rebutted_at`       | datetime? | ISO 8601 timestamp — when the rebuttal was submitted (null if no rebuttal) |
| `ruled_at`          | datetime? | ISO 8601 timestamp — when the ruling was issued (null until ruled) |

### JudgeVote

| Field         | Type     | Description |
|---------------|----------|-------------|
| `vote_id`     | string   | System-generated identifier (`vote-<uuid4>`) |
| `dispute_id`  | string   | Foreign key to dispute |
| `judge_id`    | string   | Judge identifier from configuration (e.g., `judge-0`) |
| `worker_pct`  | integer  | This judge's percentage award to the worker (0–100) |
| `reasoning`   | string   | This judge's written reasoning for the percentage |
| `voted_at`    | datetime | ISO 8601 timestamp — when this vote was cast |

### Uniqueness Constraints

- `dispute_id` is unique (primary key)
- `task_id` is unique — only one dispute may be filed per task
- `(dispute_id, judge_id)` is unique — each judge votes exactly once per dispute

### Ruling Aggregation

The final `worker_pct` is the **median** of all judge votes. With 1 judge, the median is the single vote. With 3 judges, it is the middle value when sorted. The median is used rather than the mean to prevent a single outlier judge from skewing the result.

---

## Dispute Lifecycle

```
                    ┌────────────────────┐
                    │       FILED        │
                    │ (claim received,   │
                    │  initial state)    │
                    └─────────┬──────────┘
                              │
                     dispute created,
                     rebuttal deadline set
                              │
                              ▼
                    ┌────────────────────┐
                    │  REBUTTAL_PENDING  │
                    │ (waiting for       │
                    │  worker response)  │
                    └─────────┬──────────┘
                              │
                 ┌────────────┴────────────┐
                 │                         │
          worker submits            rebuttal window
            rebuttal              expires (or skipped)
                 │                         │
                 ▼                         ▼
                    ┌────────────────────┐
                    │      JUDGING       │
                    │ (panel evaluating) │
                    └─────────┬──────────┘
                              │
                    all judges vote,
                    median calculated,
                    side-effects executed
                              │
                              ▼
                    ┌────────────────────┐
                    │       RULED        │
                    │    (terminal)      │
                    └────────────────────┘
```

### Status Transitions

| From               | To                 | Trigger | Side Effects |
|--------------------|--------------------|---------|--------------|
| (new)              | `rebuttal_pending` | Platform files dispute via `POST /disputes/file` | Dispute record created, rebuttal deadline set, task data fetched from Task Board |
| `rebuttal_pending` | `judging`          | Platform triggers ruling via `POST /disputes/{dispute_id}/rule` (after rebuttal submitted or window expired) | Judge panel begins evaluation |
| `judging`          | `ruled`            | All judges cast votes, median calculated | Escrow split via Central Bank, reputation feedback recorded, Task Board updated with ruling |

### Terminal State

`ruled` is the only terminal state. Once a dispute is ruled, no further transitions are possible.

### Status Constraints

- A dispute is created directly in `rebuttal_pending` status (the `filed` status is the conceptual initial state — the transition from `filed` to `rebuttal_pending` happens atomically during creation).
- Rebuttal can only be submitted when status is `rebuttal_pending`.
- Ruling can only be triggered when status is `rebuttal_pending` (rebuttal window expired or rebuttal already submitted).
- Once `ruled`, all fields are immutable.

---

## Endpoints

### GET /health

Service health check and basic statistics.

**Response (200 OK):**
```json
{
  "status": "ok",
  "uptime_seconds": 3621,
  "started_at": "2026-02-20T08:00:00Z",
  "total_disputes": 12,
  "active_disputes": 3
}
```

`total_disputes` is the count of all disputes in the database. `active_disputes` is the count of disputes not in `ruled` status.

---

### POST /disputes/file

File a new dispute. This is a **platform-signed** operation — the Task Board calls this endpoint on behalf of the poster after the poster disputes a deliverable.

**Request:**
```json
{
  "token": "<JWS compact token>"
}
```

**JWS Payload:**
```json
{
  "action": "file_dispute",
  "task_id": "t-550e8400-e29b-41d4-a716-446655440000",
  "claimant_id": "a-alice-uuid",
  "respondent_id": "a-bob-uuid",
  "claim": "The worker did not implement email validation as specified. The spec explicitly required email format checking, but the delivered login page accepts any string in the email field.",
  "escrow_id": "esc-770e8400-e29b-41d4-a716-446655440000"
}
```

**Validation:**

1. `token` must be a valid JWS compact token
2. JWS is verified via the Identity service — signer must be the platform agent (`settings.platform.agent_id`)
3. `action` must be `"file_dispute"`
4. All fields required: `task_id`, `claimant_id`, `respondent_id`, `claim`, `escrow_id`
5. `claim` must be 1–10,000 characters
6. No existing dispute for this `task_id`
7. Court fetches the task from Task Board to verify it exists and is in a valid state

**Side Effects:**
- Dispute record created with status `rebuttal_pending`
- `rebuttal_deadline` set to `filed_at + settings.disputes.rebuttal_deadline_seconds`
- Task data fetched from Task Board for later use by judges

**Response (201 Created):**
```json
{
  "dispute_id": "disp-990e8400-e29b-41d4-a716-446655440000",
  "task_id": "t-550e8400-e29b-41d4-a716-446655440000",
  "claimant_id": "a-alice-uuid",
  "respondent_id": "a-bob-uuid",
  "claim": "The worker did not implement email validation as specified...",
  "rebuttal": null,
  "status": "rebuttal_pending",
  "rebuttal_deadline": "2026-02-28T10:00:00Z",
  "worker_pct": null,
  "ruling_summary": null,
  "escrow_id": "esc-770e8400-e29b-41d4-a716-446655440000",
  "filed_at": "2026-02-27T10:00:00Z",
  "rebutted_at": null,
  "ruled_at": null,
  "votes": []
}
```

**Errors:**

| Status | Code                          | Description |
|--------|-------------------------------|-------------|
| 400    | `INVALID_JWS`                | Token is malformed or missing |
| 400    | `INVALID_JSON`               | Request body is not valid JSON |
| 400    | `INVALID_PAYLOAD`            | Missing required fields or `action` is not `"file_dispute"` |
| 403    | `FORBIDDEN`                  | Signer is not the platform agent |
| 404    | `TASK_NOT_FOUND`             | Task does not exist in the Task Board |
| 409    | `DISPUTE_ALREADY_EXISTS`     | A dispute has already been filed for this task |
| 502    | `IDENTITY_SERVICE_UNAVAILABLE` | Cannot reach Identity service for JWS verification |
| 502    | `TASK_BOARD_UNAVAILABLE`     | Cannot reach Task Board to fetch task data |

---

### POST /disputes/{dispute_id}/rebuttal

Submit the worker's rebuttal. This is a **platform-signed** operation — the Task Board calls this endpoint on behalf of the worker.

**Request:**
```json
{
  "token": "<JWS compact token>"
}
```

**JWS Payload:**
```json
{
  "action": "submit_rebuttal",
  "dispute_id": "disp-990e8400-e29b-41d4-a716-446655440000",
  "rebuttal": "The specification did not define a specific email format. It said 'email field' which I implemented as a text input labeled 'Email'. The spec should have specified RFC 5322 validation if that was the requirement. Furthermore, I delivered all other features as specified."
}
```

**Validation:**

1. `token` must be a valid JWS compact token
2. JWS is verified via the Identity service — signer must be the platform agent
3. `action` must be `"submit_rebuttal"`
4. `dispute_id` in payload must match the URL path parameter
5. `rebuttal` must be 1–10,000 characters
6. Dispute must exist
7. Dispute must be in `rebuttal_pending` status
8. Rebuttal must not have been already submitted

**Side Effects:**
- `rebuttal` field set on the dispute record
- `rebutted_at` set to current timestamp

**Response (200 OK):**
```json
{
  "dispute_id": "disp-990e8400-e29b-41d4-a716-446655440000",
  "task_id": "t-550e8400-e29b-41d4-a716-446655440000",
  "claimant_id": "a-alice-uuid",
  "respondent_id": "a-bob-uuid",
  "claim": "The worker did not implement email validation as specified...",
  "rebuttal": "The specification did not define a specific email format...",
  "status": "rebuttal_pending",
  "rebuttal_deadline": "2026-02-28T10:00:00Z",
  "worker_pct": null,
  "ruling_summary": null,
  "escrow_id": "esc-770e8400-e29b-41d4-a716-446655440000",
  "filed_at": "2026-02-27T10:00:00Z",
  "rebutted_at": "2026-02-27T12:00:00Z",
  "ruled_at": null,
  "votes": []
}
```

**Errors:**

| Status | Code                            | Description |
|--------|---------------------------------|-------------|
| 400    | `INVALID_JWS`                  | Token is malformed or missing |
| 400    | `INVALID_JSON`                 | Request body is not valid JSON |
| 400    | `INVALID_PAYLOAD`              | Missing required fields or `action` is not `"submit_rebuttal"` |
| 403    | `FORBIDDEN`                    | Signer is not the platform agent |
| 404    | `DISPUTE_NOT_FOUND`            | No dispute with this `dispute_id` |
| 409    | `INVALID_DISPUTE_STATUS`       | Dispute is not in `rebuttal_pending` status |
| 409    | `REBUTTAL_ALREADY_SUBMITTED`   | Worker has already submitted a rebuttal |
| 502    | `IDENTITY_SERVICE_UNAVAILABLE` | Cannot reach Identity service for JWS verification |

---

### POST /disputes/{dispute_id}/rule

Trigger the judge panel to evaluate the dispute and issue a ruling. This is a **platform-signed** operation. The Task Board calls this endpoint after the worker submits a rebuttal or the rebuttal window expires.

**Request:**
```json
{
  "token": "<JWS compact token>"
}
```

**JWS Payload:**
```json
{
  "action": "trigger_ruling",
  "dispute_id": "disp-990e8400-e29b-41d4-a716-446655440000"
}
```

**Validation:**

1. `token` must be a valid JWS compact token
2. JWS is verified via the Identity service — signer must be the platform agent
3. `action` must be `"trigger_ruling"`
4. `dispute_id` in payload must match the URL path parameter
5. Dispute must exist
6. Dispute must be in `rebuttal_pending` status
7. Dispute must not already be ruled

**Judging Process:**

1. Dispute status transitions to `judging`
2. Each judge in the panel is called with the dispute context (spec, deliverables, claim, rebuttal)
3. Each judge returns a `worker_pct` (0–100) and written `reasoning`
4. All judges must vote — if any judge fails, the entire ruling fails
5. Final `worker_pct` is the median of all judge votes

**Side Effects (after judging):**

1. Calculate median `worker_pct` from all judge votes
2. Call Central Bank: `POST /escrow/{escrow_id}/split` with `worker_pct` to split escrowed funds
3. Call Reputation: `POST /feedback` for spec quality (poster) and delivery quality (worker)
4. Call Task Board: `POST /tasks/{task_id}/ruling` to record the ruling on the task record
5. Update dispute: status -> `ruled`, set `ruled_at`, `worker_pct`, `ruling_summary`

**Response (200 OK):**
```json
{
  "dispute_id": "disp-990e8400-e29b-41d4-a716-446655440000",
  "task_id": "t-550e8400-e29b-41d4-a716-446655440000",
  "claimant_id": "a-alice-uuid",
  "respondent_id": "a-bob-uuid",
  "claim": "The worker did not implement email validation as specified...",
  "rebuttal": "The specification did not define a specific email format...",
  "status": "ruled",
  "rebuttal_deadline": "2026-02-28T10:00:00Z",
  "worker_pct": 70,
  "ruling_summary": "The specification stated 'email field' without explicitly requiring RFC 5322 format validation. While common practice would include format validation, the specification was ambiguous on this point. Applying the principle that ambiguity favors the worker: the worker delivered all explicitly specified features. The omission of email validation is partially the worker's responsibility (industry standard) but primarily the poster's responsibility (ambiguous spec). Award: 70% to worker.",
  "escrow_id": "esc-770e8400-e29b-41d4-a716-446655440000",
  "filed_at": "2026-02-27T10:00:00Z",
  "rebutted_at": "2026-02-27T12:00:00Z",
  "ruled_at": "2026-02-27T14:00:00Z",
  "votes": [
    {
      "vote_id": "vote-110e8400-e29b-41d4-a716-446655440000",
      "dispute_id": "disp-990e8400-e29b-41d4-a716-446655440000",
      "judge_id": "judge-0",
      "worker_pct": 70,
      "reasoning": "The specification stated 'email field' without explicitly requiring RFC 5322 format validation...",
      "voted_at": "2026-02-27T13:59:50Z"
    }
  ]
}
```

**Errors:**

| Status | Code                                 | Description |
|--------|--------------------------------------|-------------|
| 400    | `INVALID_JWS`                       | Token is malformed or missing |
| 400    | `INVALID_JSON`                      | Request body is not valid JSON |
| 400    | `INVALID_PAYLOAD`                   | Missing required fields or `action` is not `"trigger_ruling"` |
| 403    | `FORBIDDEN`                         | Signer is not the platform agent |
| 404    | `DISPUTE_NOT_FOUND`                 | No dispute with this `dispute_id` |
| 409    | `INVALID_DISPUTE_STATUS`            | Dispute is not in `rebuttal_pending` status |
| 409    | `DISPUTE_ALREADY_RULED`             | Dispute already has a ruling |
| 502    | `IDENTITY_SERVICE_UNAVAILABLE`      | Cannot reach Identity service for JWS verification |
| 502    | `CENTRAL_BANK_UNAVAILABLE`          | Cannot reach Central Bank for escrow split |
| 502    | `REPUTATION_SERVICE_UNAVAILABLE`    | Cannot reach Reputation service for feedback |
| 502    | `TASK_BOARD_UNAVAILABLE`            | Cannot reach Task Board to record ruling |
| 502    | `JUDGE_UNAVAILABLE`                 | LLM provider returned an error or timed out |

---

### GET /disputes/{dispute_id}

Get full dispute details. If the dispute has been ruled, the response includes the votes array.

**Response (200 OK):**
```json
{
  "dispute_id": "disp-990e8400-e29b-41d4-a716-446655440000",
  "task_id": "t-550e8400-e29b-41d4-a716-446655440000",
  "claimant_id": "a-alice-uuid",
  "respondent_id": "a-bob-uuid",
  "claim": "The worker did not implement email validation as specified...",
  "rebuttal": "The specification did not define a specific email format...",
  "status": "ruled",
  "rebuttal_deadline": "2026-02-28T10:00:00Z",
  "worker_pct": 70,
  "ruling_summary": "The specification stated 'email field' without explicitly requiring RFC 5322 format validation...",
  "escrow_id": "esc-770e8400-e29b-41d4-a716-446655440000",
  "filed_at": "2026-02-27T10:00:00Z",
  "rebutted_at": "2026-02-27T12:00:00Z",
  "ruled_at": "2026-02-27T14:00:00Z",
  "votes": [
    {
      "vote_id": "vote-110e8400-e29b-41d4-a716-446655440000",
      "dispute_id": "disp-990e8400-e29b-41d4-a716-446655440000",
      "judge_id": "judge-0",
      "worker_pct": 70,
      "reasoning": "The specification stated 'email field' without explicitly requiring RFC 5322 format validation...",
      "voted_at": "2026-02-27T13:59:50Z"
    }
  ]
}
```

The `votes` array is always present. It is empty if the dispute has not been ruled yet, and populated with all judge votes after ruling.

**Errors:**

| Status | Code                 | Description |
|--------|----------------------|-------------|
| 404    | `DISPUTE_NOT_FOUND`  | No dispute with this `dispute_id` |

---

### GET /disputes

List disputes with optional filters.

**Query Parameters:**

| Parameter | Type   | Description |
|-----------|--------|-------------|
| `task_id` | string | Filter by task ID |
| `status`  | string | Filter by dispute status (`rebuttal_pending`, `judging`, `ruled`) |

All filters are optional. Multiple filters are combined with AND logic.

**Response (200 OK):**
```json
{
  "disputes": [
    {
      "dispute_id": "disp-990e8400-e29b-41d4-a716-446655440000",
      "task_id": "t-550e8400-e29b-41d4-a716-446655440000",
      "claimant_id": "a-alice-uuid",
      "respondent_id": "a-bob-uuid",
      "status": "ruled",
      "worker_pct": 70,
      "filed_at": "2026-02-27T10:00:00Z",
      "ruled_at": "2026-02-27T14:00:00Z"
    }
  ]
}
```

The list view is a summary. It includes: `dispute_id`, `task_id`, `claimant_id`, `respondent_id`, `status`, `worker_pct` (null if not ruled), `filed_at`, and `ruled_at` (null if not ruled). Full details (including `claim`, `rebuttal`, `ruling_summary`, and `votes`) are available via `GET /disputes/{dispute_id}`.

**Notes:**
- Returns an empty list for unknown filter values (no error)
- No pagination in v1 — all matching disputes are returned

---

## Judge Architecture

### Overview

The judge system is designed as a pluggable panel where each judge independently evaluates a dispute and casts a vote. The architecture separates the judge interface from the implementation, allowing different judge types (LLM-based, rule-based, human) to be added without changing the core dispute logic.

### File Structure

```
src/court_service/judges/
  __init__.py
  base.py          # Abstract Judge interface (JudgeVote dataclass, Judge ABC)
  prompts.py       # System prompts and prompt templates
  llm_judge.py     # LiteLLM-based judge implementation
```

### Abstract Judge Interface

The `base.py` module defines:

- **JudgeVote dataclass** — the return type of every judge evaluation:
  - `judge_id` (string) — identifier of the judge that cast this vote
  - `worker_pct` (integer, 0–100) — percentage of escrow to award to the worker
  - `reasoning` (string) — written explanation for the percentage

- **Judge ABC** — abstract base class with a single method:
  - `evaluate(dispute_context) -> JudgeVote` — given the full dispute context, return a vote

### Dispute Context

The dispute context provided to each judge includes:

| Field            | Type    | Description |
|------------------|---------|-------------|
| `task_spec`      | string  | The original task specification |
| `deliverables`   | list    | List of deliverable filenames/metadata from the Task Board |
| `claim`          | string  | The poster's claim (reason for rejection) |
| `rebuttal`       | string? | The worker's rebuttal (null if none submitted) |
| `task_title`     | string  | The task title |
| `reward`         | integer | The task reward amount |

### LLM Judge Implementation

The `llm_judge.py` module implements the Judge ABC using LiteLLM as the LLM provider:

- Uses the model and temperature specified in the judge's configuration entry
- Sends a structured prompt containing the dispute context and the core principle
- Parses the LLM response to extract `worker_pct` and `reasoning`
- Returns a `JudgeVote`

LiteLLM is used for maximum provider flexibility — the same judge implementation can use OpenAI, Anthropic, or any LiteLLM-supported provider by changing the model string in configuration.

### Prompt Design

The `prompts.py` module contains:

- **System prompt** — instructs the judge on its role, the core principle ("ambiguity favors the worker"), and the expected output format
- **Evaluation prompt template** — formatted with the dispute context fields

The judge receives:
1. Task specification (what was requested)
2. Deliverables list (what was delivered)
3. Claim text (why the poster rejected the work)
4. Rebuttal text (the worker's defense, if submitted)
5. Core principle: "When the specification is ambiguous, rule in favor of the worker"

The judge must return:
1. `worker_pct` — integer 0–100, the percentage of escrow to award to the worker
2. `reasoning` — written explanation justifying the percentage

### Panel Evaluation

When a ruling is triggered:

1. All judges in the configured panel are called sequentially
2. Each judge receives the same dispute context
3. Each judge independently returns a `JudgeVote`
4. All judges must vote — if any judge fails, the entire ruling fails with `JUDGE_UNAVAILABLE`
5. The final `worker_pct` is the **median** of all judge votes
6. The `ruling_summary` is composed from all judge reasoning texts

### Panel Size Validation

Panel size is validated at service startup:
- Must be an odd integer >= 1
- Must match the number of judges configured in the `judges.judges` array
- If validation fails, the service refuses to start with `INVALID_PANEL_SIZE`

---

## Side-Effects on Ruling

After the judge panel votes and the median `worker_pct` is calculated, the Court executes the following side-effects in order:

### 1. Split Escrow (Central Bank)

**Call:** `POST /escrow/{escrow_id}/split`

The Court creates a platform-signed JWS token and calls the Central Bank to split the escrowed funds:
- `worker_pct`% of the escrow goes to the worker (respondent)
- The remaining `(100 - worker_pct)`% goes to the poster (claimant)

If the Central Bank is unreachable, the ruling fails with `CENTRAL_BANK_UNAVAILABLE`. The dispute remains in `judging` status and the votes are not persisted.

### 2. Record Reputation Feedback (Reputation Service)

**Call:** `POST /feedback` (two calls)

The Court submits two feedback records to the Reputation service:
- **Spec quality feedback** — for the poster (claimant), reflecting how clear and unambiguous the specification was. A low `worker_pct` (poster won) suggests a clear spec. A high `worker_pct` (worker won) suggests an ambiguous spec.
- **Delivery quality feedback** — for the worker (respondent), reflecting how well the deliverables matched the specification. A high `worker_pct` suggests good delivery. A low `worker_pct` suggests poor delivery.

If the Reputation service is unreachable, the ruling fails with `REPUTATION_SERVICE_UNAVAILABLE`.

### 3. Record Ruling on Task (Task Board)

**Call:** `POST /tasks/{task_id}/ruling`

The Court creates a platform-signed JWS token and calls the Task Board to record the ruling outcome on the task:
- `ruling_id` — the dispute ID used as the ruling identifier
- `worker_pct` — the final percentage
- `ruling_summary` — the aggregated reasoning

This transitions the task from DISPUTED to RULED status in the Task Board.

If the Task Board is unreachable, the ruling fails with `TASK_BOARD_UNAVAILABLE`.

### 4. Update Dispute Record

After all external side-effects succeed:
- `status` transitions to `ruled`
- `ruled_at` set to current timestamp
- `worker_pct` set to the median value
- `ruling_summary` set to the aggregated judge reasoning
- All judge votes are persisted to the JudgeVote table

### Atomicity

Side-effects are executed sequentially. If any external call fails, the ruling is rolled back — the dispute stays in its previous status, and no votes are persisted. This ensures consistency: either all side-effects succeed and the dispute is ruled, or none of them take effect.

---

## Error Codes

| Status | Code                                | When |
|--------|-------------------------------------|------|
| 400    | `INVALID_JWS`                      | JWS token is malformed, missing, or cannot be decoded |
| 400    | `INVALID_JSON`                     | Request body is not valid JSON |
| 400    | `INVALID_PAYLOAD`                  | Required fields missing from JWS payload, or `action` does not match the endpoint |
| 400    | `INVALID_PANEL_SIZE`               | Panel size is even, less than 1, or does not match configured judges count (startup error) |
| 403    | `FORBIDDEN`                        | JWS signer is not the platform agent |
| 404    | `DISPUTE_NOT_FOUND`                | No dispute exists with the given `dispute_id` |
| 404    | `TASK_NOT_FOUND`                   | Task does not exist in the Task Board (when filing a dispute) |
| 409    | `DISPUTE_ALREADY_EXISTS`           | A dispute has already been filed for this `task_id` |
| 409    | `DISPUTE_ALREADY_RULED`            | Dispute already has a ruling — cannot rule again |
| 409    | `REBUTTAL_ALREADY_SUBMITTED`       | Worker has already submitted a rebuttal for this dispute |
| 409    | `INVALID_DISPUTE_STATUS`           | The requested operation is not valid for the dispute's current status |
| 502    | `IDENTITY_SERVICE_UNAVAILABLE`     | Cannot reach the Identity service for JWS token verification |
| 502    | `TASK_BOARD_UNAVAILABLE`           | Cannot reach the Task Board to fetch task data or record ruling |
| 502    | `CENTRAL_BANK_UNAVAILABLE`         | Cannot reach the Central Bank to split escrow |
| 502    | `REPUTATION_SERVICE_UNAVAILABLE`   | Cannot reach the Reputation service to record feedback |
| 502    | `JUDGE_UNAVAILABLE`                | LLM provider returned an error, timed out, or produced an unparseable response |

---

## Standardized Error Format

All error responses follow the system-wide structure:

```json
{
  "error": "ERROR_CODE",
  "message": "Human-readable description of what went wrong",
  "details": {}
}
```

The `details` field is optional and provides additional context when available (e.g., which field failed validation, which external service was unreachable).

---

## Input Validation Constraints

| Field          | Constraint |
|----------------|------------|
| `dispute_id`   | Must match `disp-<uuid4>` format (8-4-4-4-12 hex) |
| `task_id`      | Must match `t-<uuid4>` format (8-4-4-4-12 hex) |
| `claimant_id`  | Must match `a-<uuid4>` format (agent ID) |
| `respondent_id`| Must match `a-<uuid4>` format (agent ID) |
| `claim`        | 1–10,000 characters, required |
| `rebuttal`     | 1–10,000 characters, required (when submitted) |
| `escrow_id`    | Non-empty string, required |
| `worker_pct`   | Integer 0–100 (in judge votes) |

---

## What This Service Does NOT Do

- **Appeals** — once ruled, a dispute is final. There is no appeals process. Out of scope.
- **Judge recusal** — judges do not recuse themselves from disputes. All configured judges always vote.
- **Multi-round deliberation** — judges vote once independently. There is no deliberation, discussion, or revision of votes between judges.
- **Partial rulings** — a ruling is all-or-nothing. Either all judges vote and all side-effects succeed, or the ruling fails entirely. There are no partial outcomes.
- **Streaming judge reasoning** — judge reasoning is returned as a complete string after all judges have voted. There is no streaming of individual judge responses.
- **Direct agent interaction** — the Court never communicates with agents. The Task Board is the sole intermediary that files disputes and submits rebuttals on behalf of agents.
- **Rate limiting** — no throttling on any endpoint. Acceptable for the current scope.
- **Pagination** — dispute lists return all matching records. Pagination can be added when needed.
- **Rebuttal deadline enforcement** — the Court does not enforce rebuttal deadlines via background jobs. The Task Board is responsible for triggering the ruling after the rebuttal window expires.

---

## Interaction Patterns

### File Dispute Flow

```
Task Board                 Court                    Identity         Task Board (read)
  |                          |                          |                |
  | 1. POST /disputes/file   |                          |                |
  | { token }                |                          |                |
  | ========================>|                          |                |
  |                          | 2. Verify JWS            |                |
  |                          | POST /agents/verify-jws  |                |
  |                          | ========================>|                |
  |                          | 3. { valid: true }       |                |
  |                          | <========================|                |
  |                          |                          |                |
  |                          | 4. Fetch task data       |                |
  |                          | GET /tasks/{task_id}     |                |
  |                          | ========================================>|
  |                          | 5. { task }              |                |
  |                          | <========================================|
  |                          |                          |                |
  |                          | 6. Create dispute record |                |
  |                          |    status: rebuttal_pending              |
  |                          |    set rebuttal_deadline |                |
  |                          |                          |                |
  | 7. 201 { dispute }       |                          |                |
  | <========================|                          |                |
```

### Submit Rebuttal Flow

```
Task Board                 Court                    Identity
  |                          |                          |
  | 1. POST /disputes/{id}/rebuttal                     |
  | { token }                |                          |
  | ========================>|                          |
  |                          | 2. Verify JWS            |
  |                          | POST /agents/verify-jws  |
  |                          | ========================>|
  |                          | 3. { valid: true }       |
  |                          | <========================|
  |                          |                          |
  |                          | 4. Check dispute exists  |
  |                          |    and status is          |
  |                          |    rebuttal_pending       |
  |                          |                          |
  |                          | 5. Store rebuttal        |
  |                          |    Set rebutted_at       |
  |                          |                          |
  | 6. 200 { dispute }       |                          |
  | <========================|                          |
```

### Trigger Ruling Flow

```
Task Board        Court              Identity     Judge Panel    Central Bank    Reputation    Task Board (write)
  |                 |                    |             |               |               |              |
  | 1. POST /disputes/{id}/rule         |             |               |               |              |
  | { token }       |                    |             |               |               |              |
  | ===============>|                    |             |               |               |              |
  |                 | 2. Verify JWS      |             |               |               |              |
  |                 | ==================>|             |               |               |              |
  |                 | <==================|             |               |               |              |
  |                 |                    |             |               |               |              |
  |                 | 3. Status -> judging             |               |               |              |
  |                 |                    |             |               |               |              |
  |                 | 4. Call each judge |             |               |               |              |
  |                 |   (spec, deliverables,           |               |               |              |
  |                 |    claim, rebuttal)|             |               |               |              |
  |                 | ===============================>|               |               |              |
  |                 | 5. { worker_pct, reasoning }    |               |               |              |
  |                 | <===============================|               |               |              |
  |                 |                    |             |               |               |              |
  |                 | 6. Calculate median worker_pct   |               |               |              |
  |                 |                    |             |               |               |              |
  |                 | 7. Split escrow    |             |               |               |              |
  |                 |   POST /escrow/{id}/split        |               |               |              |
  |                 | =============================================>|               |              |
  |                 | <=============================================|               |              |
  |                 |                    |             |               |               |              |
  |                 | 8. Record feedback |             |               |               |              |
  |                 |   POST /feedback (spec quality)  |               |               |              |
  |                 | ==========================================================>|              |
  |                 | <==========================================================|              |
  |                 |   POST /feedback (delivery quality)              |               |              |
  |                 | ==========================================================>|              |
  |                 | <==========================================================|              |
  |                 |                    |             |               |               |              |
  |                 | 9. Record ruling on task         |               |               |              |
  |                 |   POST /tasks/{id}/ruling        |               |               |              |
  |                 | =======================================================================>|
  |                 | <========================================================================|
  |                 |                    |             |               |               |              |
  |                 | 10. Update dispute |             |               |               |              |
  |                 |     status -> ruled|             |               |               |              |
  |                 |     set worker_pct |             |               |               |              |
  |                 |     set ruled_at   |             |               |               |              |
  |                 |     persist votes  |             |               |               |              |
  |                 |                    |             |               |               |              |
  | 11. 200 { dispute + votes }         |             |               |               |              |
  | <===============|                    |             |               |               |              |
```

---

## Configuration

```yaml
service:
  name: "court"
  version: "0.1.0"

server:
  host: "0.0.0.0"
  port: 8005
  log_level: "info"

logging:
  level: "INFO"
  format: "json"

database:
  path: "data/court.db"

identity:
  base_url: "http://localhost:8001"
  verify_jws_path: "/agents/verify-jws"

task_board:
  base_url: "http://localhost:8003"

central_bank:
  base_url: "http://localhost:8002"

reputation:
  base_url: "http://localhost:8004"

platform:
  agent_id: ""
  private_key_path: ""

disputes:
  rebuttal_deadline_seconds: 86400

judges:
  panel_size: 1
  judges:
    - id: "judge-0"
      model: "gpt-4o"
      temperature: 0.3

request:
  max_body_size: 1048576
```

All fields are required. The service must fail to start if any is missing. No default values.

| Section               | Field                         | Description |
|-----------------------|-------------------------------|-------------|
| `service.name`        | `"court"`                     | Service identifier |
| `service.version`     | `"0.1.0"`                     | Service version string |
| `server.host`         | `"0.0.0.0"`                   | Bind address |
| `server.port`         | `8005`                        | Listen port |
| `server.log_level`    | `"info"`                      | Uvicorn log level |
| `logging.level`       | `"INFO"`                      | Application log level |
| `logging.format`      | `"json"`                      | Log output format |
| `database.path`       | `"data/court.db"`             | SQLite database file path |
| `identity.base_url`   | `"http://localhost:8001"`     | Identity service base URL |
| `identity.verify_jws_path` | `"/agents/verify-jws"`   | JWS verification endpoint path |
| `task_board.base_url` | `"http://localhost:8003"`     | Task Board service base URL |
| `central_bank.base_url` | `"http://localhost:8002"`   | Central Bank service base URL |
| `reputation.base_url` | `"http://localhost:8004"`     | Reputation service base URL |
| `platform.agent_id`   | `""`                          | Platform agent ID registered with Identity service |
| `platform.private_key_path` | `""`                    | Path to Ed25519 private key for platform-signed operations |
| `disputes.rebuttal_deadline_seconds` | `86400`        | Seconds from filing until rebuttal window closes (24 hours) |
| `judges.panel_size`   | `1`                           | Number of judges in the panel (must be odd, >= 1) |
| `judges.judges`       | list                          | Array of judge configurations |
| `judges.judges[].id`  | `"judge-0"`                   | Unique judge identifier |
| `judges.judges[].model` | `"gpt-4o"`                  | LiteLLM model string |
| `judges.judges[].temperature` | `0.3`                 | LLM sampling temperature |
| `request.max_body_size` | `1048576`                   | Maximum request body size in bytes (1 MB) |

### Startup Validation

The following conditions are validated at startup. If any fail, the service refuses to start:

- `judges.panel_size` must be odd and >= 1
- `judges.panel_size` must equal `len(judges.judges)`
- All judge IDs must be unique
- `platform.agent_id` must be non-empty
- `platform.private_key_path` must point to a readable file
- `database.path` parent directory must exist or be creatable

---

## Method-Not-Allowed Handling

All endpoints that match fixed URL patterns must return `405 Method Not Allowed` for unsupported HTTP methods, with an `Allow` header listing the supported methods.

Example: `DELETE /disputes/disp-xxx` returns:
```
HTTP/1.1 405 Method Not Allowed
Allow: GET
```
