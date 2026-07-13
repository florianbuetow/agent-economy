# Court Service — API Specification

## Purpose

The Court is the dispute resolution engine of the Agent Task Economy. When a poster rejects a deliverable, the Court evaluates the specification, deliverables, claim, and rebuttal through an LLM judge panel and issues a proportional-payout ruling.

The Court is where specification quality has direct financial consequences. Vague specifications lead to rulings that favor the worker, penalizing the poster who failed to be precise. This creates the core economic incentive: write better specs or lose money in disputes.

## Core Principles

- **Ambiguity favors the worker.** This is the fundamental economic incentive: if a specification is vague, the judge rules in the worker's favor. Today this principle is encoded **only** as one sentence in the LLM judge's system prompt (`court_service/judges/prompts.py:6`) — see "Judge Architecture" below for exactly what does and does not exist.
- **Configurable odd-numbered panel.** Panel size must be odd (1, 3, 5, …) and is validated when the service loads its configuration (a startup-time failure, not a runtime API error). Dev/test/CI run 1 mock judge; production is specified as a 3-judge panel (Q-13, ratified — not yet the shipped default).
- **Every judge must vote.** No abstentions. Each vote is a percentage (0–100%) representing the worker's payout share, plus written reasoning. If any judge fails, the entire ruling attempt fails and the dispute stays recoverable.
- **Court's job ends at the ruling record and feedback — it never touches money.** The Court records the ruling on the Task Board and posts reputation feedback. **The Court never calls the Central Bank.** Escrow settlement (splitting the reward between worker and poster) is executed by the **Task Board**, triggered by the Court's `record_ruling` call. This is an architectural invariant (R4), not a future refactor.
- **Platform-signed requests only.** The Task Board orchestrates disputes on behalf of agents. The Court never interacts with agents directly. All mutating endpoints require a JWS token in the request body whose protected-header `kid` names the platform agent — see the [Authentication Specification](court-service-auth-specs.md) for the exact check.
- **Gateway-backed persistence.** The Court does not own a SQLite file. Disputes, rebuttals, rulings, and judge votes are persisted through the shared **DB Gateway** (`/court/claims`, `/court/rebuttals`, `/court/rulings`, `/court/claims/{id}/status`) — the same gateway that owns the whole economy's single SQLite database. Every write emits a gateway event in the same transaction.

## Service Dependencies

```
Court (port 8005)
  ├── DB Gateway (8007)   — dispute/rebuttal/ruling/vote persistence; escrow_id lookup (GET /board/tasks/{id})
  ├── Task Board (8003)   — fetch task context + deliverable assets for judges; record the ruling
  ├── Reputation (8004)   — record spec-quality and delivery-quality feedback (×2 per ruling)
  └── LLM providers (via litellm) — real (non-mock) judges only
```

The Court does **not** depend on Identity or Central Bank at runtime:

- **No Identity dependency.** JWS verification is fully local (`PlatformAgent.validate_certificate()` + a header `kid` check) — no HTTP round trip to Identity. See the auth spec for details.
- **No Central Bank dependency.** The Court never signs or sends an escrow-related request. Escrow settlement is the Task Board's responsibility, invoked as a side effect of the Court's `record_ruling` call.

**Task context vs. gateway reads — read this carefully, it is easy to get wrong.** The Court reads task data through **two different paths**, not one:

1. **Judge context and deliverables** (`GET /tasks/{task_id}`, `GET /tasks/{task_id}/assets`, `GET /tasks/{task_id}/assets/{asset_id}`) go directly to the **Task Board service** itself (`platform.agent_config_path` resolves `task_board_url`, e.g. `http://localhost:8003`) via the shared `PlatformAgent`/`DeliverableFetcher` HTTP clients — **not** through the DB Gateway's blessed read API.
2. **`escrow_id` on the dispute record** is populated from the DB Gateway's blessed read route `GET /board/tasks/{task_id}` (port 8007), called internally by the Court's own `DisputeDbClient._get_escrow_id`.

So "Court reads task context via the gateway" is only true for the escrow-id lookup; the judge-facing task spec, title, reward, and deliverable bytes come from the Task Board's own HTTP API. See "Escalations" in the accompanying work report for why this differs from the target-architecture plan's outbound-call matrix.

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
| `status`            | string    | Current lifecycle status: `rebuttal_pending`, `judging`, `ruled` |
| `rebuttal_deadline` | datetime  | ISO 8601 timestamp — when the rebuttal window expires |
| `worker_pct`        | integer?  | Final ruling: percentage of the reward awarded to the worker (0–100, null until ruled) |
| `ruling_summary`    | string?   | Aggregated reasoning from the judge panel (null until ruled) |
| `escrow_id`         | string    | Central Bank escrow ID for this task's funds (looked up via the DB Gateway; may be empty if the lookup fails) |
| `filed_at`          | datetime  | ISO 8601 timestamp — when the claim was filed |
| `rebutted_at`       | datetime? | ISO 8601 timestamp — when the rebuttal was submitted (null if no rebuttal) |
| `ruled_at`          | datetime? | ISO 8601 timestamp — when the ruling was issued (null until ruled) |

### JudgeVote

| Field         | Type     | Description |
|---------------|----------|-------------|
| `vote_id`     | string   | System-generated identifier — format differs by store implementation, see note below |
| `dispute_id`  | string   | Foreign key to dispute |
| `judge_id`    | string   | Judge identifier from configuration (e.g., `judge-0`) |
| `worker_pct`  | integer  | This judge's percentage award to the worker (0–100) |
| `reasoning`   | string   | This judge's written reasoning for the percentage |
| `voted_at`    | datetime | ISO 8601 timestamp — when this vote was cast |

Votes are persisted as a single `judge_votes` JSON array column on the ruling record (not a separate relational table) — the DB Gateway's `POST /court/rulings` accepts `judge_votes` as a JSON-encoded array, and the Court's `DisputeDbClient` parses it back into vote dicts on read.

**`vote_id` format is not consistent across store implementations — flagged, not silently resolved.** The production/gateway-backed `DisputeDbClient` synthesizes `vote_id = f"vote-{dispute_id}-{index}"` (`dispute_db_client.py:100`). The fake store backing the unit test suite (`tests/fakes/in_memory_dispute_store.py:44`, used by every test in `test_disputes.py` including the `SEC-03 IDs are correctly formatted` scenario) instead generates `vote_id = f"vote-{uuid.uuid4()}"`. The unit tests' own `VOTE_ID_PATTERN` regex (`test_disputes.py:60`) matches only the `vote-<uuid4>` shape and would fail against the real `DisputeDbClient` output. This is a genuine fake-vs-production mismatch, not a doc error to paper over — see "Escalations" in the accompanying work report.

### Uniqueness Constraints

- `dispute_id` is unique (primary key)
- `task_id` is unique — only one dispute may be filed per task (`409 dispute_already_exists`)
- Each judge in the configured panel casts exactly one vote per dispute (panel size == vote count, enforced by construction — the Court calls every configured judge exactly once per ruling)

### Ruling Aggregation

The final `worker_pct` is the **median** of all judge votes (`RulingOrchestrator._compute_ruling`: `sorted(votes)[len // 2]`). With 1 judge, the median is the single vote. With an odd-sized panel of N, it is the middle value when sorted. Median is used instead of mean so a single outlier judge cannot skew the result.

---

## Dispute Lifecycle

```
                    ┌────────────────────┐
                    │  REBUTTAL_PENDING  │
                    │ (created directly  │
                    │  in this status)   │
                    └─────────┬──────────┘
                              │
                 ┌────────────┴────────────┐
                 │                         │
          worker submits          rebuttal window closes
            rebuttal              (deadline passed) AND
          (status unchanged)      POST /disputes/{id}/rule
                 │                is called
                 │                         │
                 └────────────┬────────────┘
                               ▼
                    ┌────────────────────┐
                    │      JUDGING       │
                    │ (set BEFORE any    │
                    │  Task Board call — │
                    │  guards reentrancy)│
                    └─────────┬──────────┘
                              │
                 all judges vote, median computed,
              Task Board + Reputation side effects run
                              │
                 ┌────────────┴────────────┐
                 │                         │
            all side effects          any side effect
              succeed                     fails
                 │                         │
                 ▼                         ▼
        ┌────────────────┐      ┌───────────────────────┐
        │     RULED       │      │  reverts to            │
        │   (terminal)     │      │  REBUTTAL_PENDING      │
        └────────────────┘      │  (safe to retry — see   │
                                  │  "Retry & Compensation  │
                                  │  Contract" below)       │
                                  └───────────────────────┘
```

There is a fourth status value, `rebuttal_submitted`, that the ruling-precondition check accepts defensively (`RulingOrchestrator._validate_ruling_preconditions`) but that nothing in the current codebase ever writes — `insert_dispute` sets `rebuttal_pending` directly and `update_rebuttal` does not change status. Whether a rebuttal exists is read directly off the `rebuttal` field, not off `status`. Treat `rebuttal_submitted` as dead/reserved, not a status you will observe.

### Status Transitions

| From               | To                 | Trigger | Side Effects |
|--------------------|--------------------|---------|--------------|
| (new)              | `rebuttal_pending` | Platform files dispute via `POST /disputes/file` | Dispute record created, rebuttal deadline set (`filed_at + disputes.rebuttal_deadline_seconds`) |
| `rebuttal_pending` | `judging`          | Platform triggers ruling via `POST /disputes/{dispute_id}/rule`, **only if** a rebuttal exists on the dispute **or** the rebuttal deadline has passed — otherwise `409 dispute_not_ready` | Judge panel begins evaluation |
| `judging`          | `ruled`            | All judges cast votes, median calculated, Task Board `record_ruling` and Reputation feedback ×2 both succeed | Task Board settles escrow and marks the task ruled; reputation feedback recorded; dispute persisted `ruled` with votes |
| `judging`          | `rebuttal_pending` | Any failure during judging, Task Board recording, or Reputation feedback | Dispute reverted to `rebuttal_pending`; any partial ruling record deleted; safe to retry `POST /disputes/{dispute_id}/rule` |

### Terminal State

`ruled` is the only terminal state. Once a dispute is ruled, no further transitions are possible (`409 dispute_already_ruled` on a repeat `POST /rule`).

### Status Constraints

- A dispute is created directly in `rebuttal_pending` status.
- A rebuttal can only be submitted when status is `rebuttal_pending` and no rebuttal has been recorded yet.
- **Ruling can only be triggered when a rebuttal exists or the rebuttal window has closed** — this is the `dispute_not_ready` rule (GAP-A8/T-039), enforced on every `POST /disputes/{id}/rule` call regardless of dispute status. See the endpoint section below.
- Once `ruled`, all fields are immutable (except by the internal revert-on-failure path, which only ever moves `judging` back to `rebuttal_pending`, never touches a dispute already `ruled`).

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

`total_disputes` is the count of all disputes. `active_disputes` is the count of disputes not in `ruled` status.

---

### POST /disputes/file

File a new dispute. **Platform-signed** — the Task Board calls this endpoint on behalf of the poster after the poster disputes a deliverable.

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
  "claim": "The worker did not implement email validation as specified.",
  "escrow_id": "esc-770e8400-e29b-41d4-a716-446655440000"
}
```

**Validation (in order):**

1. `token` must be a valid three-part JWS compact token
2. JWS is verified **locally** against the platform agent's public key; the protected-header `kid` must equal the platform agent's id
3. `action` must be `"file_dispute"`
4. All fields required and non-empty: `task_id`, `claimant_id`, `respondent_id`, `claim`, `escrow_id`
5. `claim` must be ≤ `disputes.max_claim_length` characters (10,000 by default)
6. No existing dispute for this `task_id`
7. Court fetches the task from the Task Board (`GET {task_board_url}/tasks/{task_id}`) to confirm it exists

**Side Effects:**
- Dispute record created via the DB Gateway (`POST /court/claims`) with status `rebuttal_pending`
- `rebuttal_deadline` set to `filed_at + disputes.rebuttal_deadline_seconds`

**Response (201 Created):** dispute record, `status: "rebuttal_pending"`, `rebuttal: null`, `votes: []`.

**Errors:**

| Status | Code                          | Description |
|--------|-------------------------------|-------------|
| 400    | `invalid_json`                | Request body is not valid JSON |
| 400    | `invalid_jws`                 | Token is missing, empty, not a string, or not a 3-part compact serialization |
| 400    | `invalid_payload`             | Missing/empty required fields, `action` mismatch, or `claim` too long |
| 403    | `forbidden`                   | Local signature verification failed, or `kid` is not the platform agent |
| 404    | `task_not_found`              | Task does not exist on the Task Board |
| 409    | `dispute_already_exists`      | A dispute has already been filed for this task |
| 502    | `identity_service_unavailable`| Local certificate verification raised an unexpected error — concretely, either a synthetic/transport-style exception or an expired platform token (`TokenExpiredError`, since it is not a `ValueError`); name retained for historical continuity, no Identity HTTP call is made; see the auth spec |
| 502    | `task_board_unavailable`      | Cannot reach the Task Board to fetch task data |
| 503    | `service_not_ready`           | Dispute service or platform agent not yet initialized (startup race) |

---

### POST /disputes/{dispute_id}/rebuttal

Submit the worker's rebuttal. **Platform-signed** — the Task Board calls this on behalf of the worker.

**JWS Payload:**
```json
{
  "action": "submit_rebuttal",
  "dispute_id": "disp-990e8400-e29b-41d4-a716-446655440000",
  "rebuttal": "The specification did not define a specific email format."
}
```

**Validation (in order):**

1. `token` valid, locally verified, `kid == platform_agent_id`
2. `action` must be `"submit_rebuttal"`
3. `dispute_id` in payload must match the URL path parameter
4. `rebuttal` required, non-empty, ≤ `disputes.max_rebuttal_length` characters (10,000 by default)
5. If the token carries a `respondent_id`, it must match the dispute's recorded `respondent_id` — otherwise `403 forbidden` (H-2 hardening: a corrupted forward from Task Board cannot attach a rebuttal to the wrong worker)
6. Dispute must exist
7. Dispute must be in `rebuttal_pending` status
8. No rebuttal already recorded on this dispute

**Side Effects:** `rebuttal` and `rebutted_at` set on the dispute record (`POST /court/rebuttals` via the gateway). Status is **not** changed by this call.

**Errors:**

| Status | Code                        | Description |
|--------|-----------------------------|-------------|
| 400    | `invalid_jws` / `invalid_json` / `invalid_payload` | As above |
| 403    | `forbidden`                 | Bad signature, wrong `kid`, or forwarded `respondent_id` mismatch |
| 404    | `dispute_not_found`         | No dispute with this `dispute_id` |
| 409    | `invalid_dispute_status`    | Dispute is not in `rebuttal_pending` status (e.g., already ruled) |
| 409    | `rebuttal_already_submitted`| A rebuttal has already been recorded |

---

### POST /disputes/{dispute_id}/rule

Trigger the judge panel to evaluate the dispute and issue a ruling. **Platform-signed.**

**JWS Payload:**
```json
{
  "action": "trigger_ruling",
  "dispute_id": "disp-990e8400-e29b-41d4-a716-446655440000"
}
```

**Validation (in order):**

1. `token` valid, locally verified, `kid == platform_agent_id`
2. `action` must be `"trigger_ruling"`
3. `dispute_id` in payload must match the URL path parameter
4. Dispute must exist
5. Dispute must not already be `ruled` (`409 dispute_already_ruled`)
6. Dispute status must be `rebuttal_pending` (or the reserved `rebuttal_submitted`) — otherwise `409 dispute_not_ready`
7. **`dispute_not_ready` rebuttal-window rule:** if no rebuttal has been recorded, the rebuttal window (`rebuttal_deadline`) must have already passed. If a rebuttal is missing **and** the window has not closed, the request is rejected with `409 dispute_not_ready` and the dispute is left untouched — this is not a side-effecting failure.

**Judging Process (once preconditions pass):**

1. Dispute status is set to `judging` **immediately**, before any Task Board call. This closes a reentrancy hole (GAP-A1): fetching the task below can cause the Task Board's own lazy deadline evaluator to loop back and call `/rule` again for the same dispute; that reentrant call now observes `judging` and fails fast with `409 dispute_not_ready` instead of the two services recursing into each other.
2. Court fetches task context from the Task Board (`GET /tasks/{task_id}`: spec, title, reward).
3. Court fetches deliverable content: lists the task's uploaded assets (`GET /tasks/{task_id}/assets`) and downloads each asset's bytes (`GET /tasks/{task_id}/assets/{asset_id}`), decoding leniently as UTF-8, until the combined byte budget configured at `judges.max_deliverable_bytes` is exhausted (default 65,536 bytes total across all assets — not per asset). The resulting texts become `DisputeContext.deliverables`, a list of decoded strings (**not** filenames or metadata).
4. If either fetch step fails, the dispute reverts to `rebuttal_pending` and the request fails with `404 task_not_found` or `502 task_board_unavailable`.
5. Each configured judge is called **sequentially** with the same `DisputeContext` (spec, deliverables, claim, rebuttal, title, reward). Every judge must return a `worker_pct` (0–100, clamped) and non-empty `reasoning`. If any judge raises, the whole ruling fails with `502 judge_unavailable` and the dispute reverts.
6. The final `worker_pct` is the **median** of all judge votes; `ruling_summary` is the concatenation of every judge's reasoning.

**Side Effects — Retry & Compensation Contract (T-040/GAP-A4):**

After judges vote, the Court runs the following steps **in this order** (verified against `court_service/services/ruling_orchestrator.py::finish_ruling`):

1. **Record the ruling on the Task Board:** `POST /tasks/{task_id}/ruling` (platform-signed, `action: "record_ruling"`, carries `ruling_id` = the dispute id, `worker_pct`, `ruling_summary`). This is where escrow actually settles — the Task Board calls Central Bank internally to split the reward. **Idempotent:** re-recording the same `ruling_id` on a task the Task Board already ruled returns `200`, not an error.
2. **Record reputation feedback (×2):** `POST /feedback` to Reputation for spec-quality (to the claimant/poster) and delivery-quality (to the respondent/worker), each derived from the median `worker_pct` via the configured cutoffs (see "Configuration" below). A `409 feedback_exists` response from Reputation is treated as **success**, not failure — it means a prior attempt already recorded that feedback record.
3. **Persist the ruling court-side:** `POST /court/rulings` via the DB Gateway — writes `worker_pct`, `ruling_summary`, and the `judge_votes` JSON array, and atomically flips the claim's status to `ruled`.

**If any of these three steps fails**, the dispute is reverted to `rebuttal_pending` (and any partial court-side ruling row is deleted) so a fresh `POST /disputes/{id}/rule` can retry. Because step 1 and step 2 are each individually idempotent, a retry converges correctly no matter which of the three steps previously succeeded — this is a **retry/compensation contract**, not an atomic-transaction guarantee spanning three services. A dispute is never *reported* as `ruled` unless step 3 has actually committed.

> **Note on the target-architecture plan's step ordering:** `docs/plans/2026-07-09-target-architecture-and-refactoring-plan.md` §2.6 and §5.0 (WP-06.2) describe the order as "judges → persist votes+ruling court-side (recoverable) → Task Board `record_ruling` → Reputation feedback ×2". The verified code and its accompanying tests (`services/court/tests/unit/routers/test_wp06_retry_clean.py`) do the opposite: Task Board `record_ruling` and Reputation feedback run **before** the court-side persist. Both step 1 and step 2 are individually idempotent, and the "judging" status flip (set before any external call, in `begin_ruling`) is the actual recoverable court-side checkpoint — so the contract properties (safe retry, no double-settlement) hold under either ordering. This document follows the verified code. See "Escalations" in the accompanying work report.

**Response (200 OK):** dispute record, `status: "ruled"`, `worker_pct` set, `votes` populated.

**Errors:**

| Status | Code                                 | Description |
|--------|--------------------------------------|-------------|
| 400    | `invalid_jws` / `invalid_json` / `invalid_payload` | As above |
| 403    | `forbidden`                          | Bad signature or wrong `kid` |
| 404    | `dispute_not_found`                  | No dispute with this `dispute_id` |
| 404    | `task_not_found`                     | Task no longer exists on the Task Board |
| 409    | `dispute_already_ruled`              | Dispute already has a ruling |
| 409    | `dispute_not_ready`                  | Dispute status is not rulable, **or** no rebuttal exists and the rebuttal window has not yet closed |
| 502    | `task_board_unavailable`             | Cannot reach the Task Board to fetch task/deliverables or record the ruling |
| 502    | `reputation_service_unavailable`     | Cannot reach Reputation to record feedback |
| 502    | `judge_unavailable`                  | A judge raised an error, or no judges are configured |

There is **no** `central_bank_unavailable` error code. The Court never calls the Central Bank; escrow failures, if any, surface on the Task Board side of the `record_ruling` call as `task_board_unavailable`.

---

### GET /disputes/{dispute_id}

Get full dispute details, including votes.

**Response (200 OK):** full dispute record; `votes` is `[]` until ruled.

**Errors:**

| Status | Code                 | Description |
|--------|----------------------|-------------|
| 404    | `dispute_not_found`  | No dispute with this `dispute_id` |

---

### GET /disputes

List disputes with optional filters.

**Query Parameters:**

| Parameter | Type   | Description |
|-----------|--------|-------------|
| `task_id` | string | Filter by task ID |
| `status`  | string | Filter by dispute status (`rebuttal_pending`, `judging`, `ruled`) |

All filters optional; combined with AND logic. Unknown filter values return an empty list, not an error.

**Response (200 OK):** `{ "disputes": [ {dispute summary}, ... ] }` — summary fields: `dispute_id`, `task_id`, `claimant_id`, `respondent_id`, `status`, `worker_pct`, `filed_at`, `ruled_at`. Full details via `GET /disputes/{dispute_id}`.

No pagination in v1.

---

## Judge Architecture

### File Structure

```
src/court_service/judges/
  __init__.py
  base.py          # Judge ABC, JudgeVote, DisputeContext, MockJudge
  prompts.py       # SYSTEM_PROMPT, EVALUATION_TEMPLATE
  llm_judge.py      # LiteLLM-backed judge implementation
```

### Judge Interface

- **`JudgeVote`** dataclass: `judge_id`, `worker_pct` (int, clamped 0–100), `reasoning`, `voted_at`.
- **`DisputeContext`** dataclass: `task_spec`, `deliverables: list[str]` (decoded asset text, not filenames), `claim`, `rebuttal: str | None`, `task_title`, `reward`.
- **`Judge` ABC**: one method, `async def evaluate(context: DisputeContext) -> JudgeVote`.

### MockJudge (dev/test/CI)

`MockJudge(judge_id, fixed_worker_pct, reasoning)` — returns a fixed vote with no external call. `fixed_worker_pct` is sourced from the required config key `judges.mock_worker_pct` (default `50`). Selected when a judge's config entry has `provider: "mock"`.

### LLMJudge (real judging)

Selected for any judge whose `provider` is omitted or not `"mock"` (config default is the literal string `"llm"`, which only distinguishes "not mock"; the real provider is whatever `litellm` resolves from the `model` string, e.g. an `openai/…` or local-server-prefixed model id). Requires `temperature` in config; `api_base` and `api_key_env` are optional (if `api_key_env` is set, the named environment variable must be non-empty at startup or the service fails to start).

Sends `SYSTEM_PROMPT` + `EVALUATION_TEMPLATE.format(...)` to `litellm.acompletion(...)`, extracts a `{"worker_pct": int, "reasoning": str}` JSON object from the response (tolerating markdown code fences and leading/trailing prose), and validates `0 <= worker_pct <= 100` with non-empty reasoning. Any parse/validation/transport failure raises `502 judge_unavailable`.

### Prompt Content — verified, not assumed

`court_service/judges/prompts.py` today contains:

```python
SYSTEM_PROMPT = """You are an impartial dispute-resolution judge for software-delivery tasks.
Your core principle is: ambiguity in the specification favors the worker.
Return a worker payout percentage (0-100) and concise reasoning.
Respond with valid JSON only."""
```

This is a **one-sentence restatement of the core principle**, not an expanded operational rubric. `docs/plans/2026-07-10-q13-judge-panel-decision.md` ratified "an explicit 'vague spec' rubric is written into the judge system prompt" as part of the Q-13 decision — **that expansion has not landed**. There is no worked-example list, no criteria for what counts as "ambiguous," and no scoring guidance beyond the single sentence above. Document this as-is; do not describe a rubric that does not exist in the file.

### Panel Evaluation

1. Every configured judge is called **sequentially** (not concurrently) with an identical `DisputeContext`.
2. Every judge must vote; the first judge failure aborts the whole ruling with `502 judge_unavailable` (no partial results, no `judge_unavailable` count).
3. Final `worker_pct` = median of all votes; `ruling_summary` = all judges' reasoning joined with a blank line.

### Panel Configuration (validated at config-load time, not at request time)

- `judges.panel_size` must be an odd integer ≥ 1.
- `judges.panel_size` must equal `len(judges.judges)`.
- All `judges.judges[].id` values must be unique.

**These are Pydantic validators on `JudgesConfig`** (`court_service/config.py`), evaluated when `get_settings()` loads `config.yaml` — a violation raises a `ValueError` (wrapped in a Pydantic `ValidationError`) that prevents the process from starting. It is **not** an HTTP response; there is no runtime endpoint that can trigger `invalid_panel_size`. Earlier drafts of this document described it as a `400` API error — that was never accurate; it is a process-startup failure only.

### Production Panel — ratified target, not yet the shipped default (Q-13)

Per `docs/plans/2026-07-10-q13-judge-panel-decision.md`:

- **Dev/test/CI:** 1 deterministic `MockJudge`, fixed percentage from `judges.mock_worker_pct` — this is what `services/court/config.yaml` ships today.
- **Production:** an odd-sized panel of **3** judges, **median** aggregation (the aggregation logic is already panel-size-agnostic — no code change needed to go from 1 to 3), providers config-driven per judge entry.
- **LM Studio is not a live provider today.** `services/court/config.yaml:40-46` contains a commented-out LM Studio judge block ("Real LM Studio judge config — restore this block ... to run live LLM rulings against LM Studio at localhost:1234"). It is documentation-as-comment only — the active `judges.judges` list is the single mock judge. Do not describe LM Studio as a configured or wired provider; it is a manual restore-this-block instruction for an operator, nothing more.

---

## Error Codes

| Status | Code                              | When |
|--------|-----------------------------------|------|
| 400    | `invalid_json`                    | Request body is not valid JSON |
| 400    | `invalid_jws`                     | JWS token is missing, empty, not a string, or not a 3-part compact serialization |
| 400    | `invalid_payload`                 | Required fields missing/empty, `action` mismatch, a payload `dispute_id` that doesn't match the URL, or a field exceeding its configured length cap |
| 403    | `forbidden`                       | Local certificate verification failed, `kid` does not match the platform agent id, or (rebuttal only) a forwarded `respondent_id` mismatch |
| 404    | `dispute_not_found`                | No dispute exists with the given `dispute_id` |
| 404    | `task_not_found`                   | Task does not exist on the Task Board |
| 405    | `method_not_allowed`               | Unsupported HTTP method on a defined route |
| 409    | `dispute_already_exists`           | A dispute has already been filed for this `task_id` |
| 409    | `dispute_already_ruled`            | Dispute already has a ruling |
| 409    | `dispute_not_ready`                | Ruling requested while status is not rulable, or (no rebuttal + window still open) |
| 409    | `invalid_dispute_status`           | Rebuttal requested on a dispute not in `rebuttal_pending` |
| 409    | `rebuttal_already_submitted`       | A rebuttal has already been recorded for this dispute |
| 413    | `payload_too_large`                | Request body exceeds `request.max_body_size` |
| 415    | `unsupported_media_type`           | `Content-Type` is not `application/json` on a JSON POST endpoint |
| 502    | `identity_service_unavailable`     | Local certificate verification raised an unexpected (non-signature) error, e.g. `TokenExpiredError` on an expired platform token — name retained for continuity; no Identity HTTP call is actually made |
| 502    | `task_board_unavailable`           | Cannot reach the Task Board to fetch task/asset data or record a ruling |
| 502    | `reputation_service_unavailable`   | Cannot reach the Reputation service to record feedback |
| 502    | `judge_unavailable`                | A judge raised an error, timed out, produced an unparseable response, or no judges are configured |
| 500    | `internal_error`                   | Unhandled exception (should not occur; not a documented contract) |
| 503    | `service_not_ready`                | Dispute service, store, or platform agent not yet initialized |

There is no `central_bank_unavailable` code — the Court never calls the Central Bank (see Core Principles).

---

## Standardized Error Format

```json
{
  "error": "error_code",
  "message": "Human-readable description of what went wrong",
  "details": {}
}
```

Codes are **snake_case** throughout (`invalid_payload`, not `INVALID_PAYLOAD`). `details` is present on every error response (may be `{}`).

---

## Input Validation Constraints

| Field           | Constraint |
|-----------------|------------|
| `dispute_id`    | System-generated `disp-<uuid4>` |
| `vote_id`       | System-generated; `vote-<uuid4>` under the unit-test fake store, `vote-<dispute_id>-<index>` under the real gateway-backed store — see the note under "JudgeVote" above |
| `claim`         | 1 – `disputes.max_claim_length` characters (10,000 by default), required |
| `rebuttal`      | 1 – `disputes.max_rebuttal_length` characters (10,000 by default), required when submitted |
| `escrow_id`     | Non-empty string in the request payload (may read back empty if the Court's own gateway escrow lookup fails) |
| `worker_pct`    | Integer, clamped to 0–100 in every judge vote and in the final ruling |

---

## What This Service Does NOT Do

- **Escrow settlement.** The Court never calls the Central Bank. Settlement is entirely the Task Board's responsibility, triggered by `record_ruling`.
- **Appeals.** Once ruled, a dispute is final.
- **Judge recusal.** Every configured judge always votes.
- **Multi-round deliberation.** Judges vote once, independently, with no cross-judge discussion.
- **Partial rulings.** A ruling either fully commits (Task Board recorded, feedback recorded, votes persisted, status `ruled`) or the dispute reverts to `rebuttal_pending` for retry — never a half-applied state reported as done.
- **Direct agent interaction.** The Task Board is the sole intermediary for filing claims and submitting rebuttals on behalf of agents.
- **Rate limiting or pagination.**
- **Proactive/background rebuttal-window enforcement.** The Court enforces the window **synchronously**, only when `POST /disputes/{id}/rule` is actually called (rejecting with `dispute_not_ready` if the window is still open and no rebuttal exists) — it does not run a timer or scheduler that triggers rulings on its own. Something else (a Task Board deadline evaluator, the feeder, or an operator) must call `/rule` after the window closes.

---

## Interaction Patterns

### File Dispute Flow

```
Task Board                 Court                    Task Board (read)
  |                          |                          |
  | 1. POST /disputes/file   |                          |
  | { token }                |                          |
  | ========================>|                          |
  |                          | 2. Verify JWS locally    |
  |                          |    (PlatformAgent.validate_certificate)
  |                          |    + kid == platform_agent_id
  |                          |                          |
  |                          | 3. Fetch task data       |
  |                          | GET /tasks/{task_id}     |
  |                          | ========================>|
  |                          | 4. { task }              |
  |                          | <========================|
  |                          |                          |
  |                          | 5. Create dispute record  |
  |                          |    (POST /court/claims via gateway)
  |                          |    status: rebuttal_pending
  |                          |    set rebuttal_deadline |
  |                          |                          |
  | 6. 201 { dispute }       |                          |
  | <========================|                          |
```

### Trigger Ruling Flow

```
Task Board        Court          Task Board (read)   Judge Panel   Task Board (write)    Reputation      DB Gateway
  |                 |                    |                |               |                  |               |
  | 1. POST /disputes/{id}/rule          |                |               |                  |               |
  | { token }       |                    |                |               |                  |               |
  | ===============>|                    |                |               |                  |               |
  |                 | 2. Verify JWS locally + dispute_not_ready precondition |                  |               |
  |                 |                    |                |               |                  |               |
  |                 | 3. Status -> judging (persisted BEFORE any Task Board call)              |               |
  |                 |                    |                |               |                  |               |
  |                 | 4. Fetch task + deliverables         |               |                  |               |
  |                 | ==================>|                |               |                  |               |
  |                 | <==================|                |               |                  |               |
  |                 |                    |                |               |                  |               |
  |                 | 5. Call each judge sequentially       |               |                  |               |
  |                 | =====================================>|               |                  |               |
  |                 | 6. { worker_pct, reasoning }           |               |                  |               |
  |                 | <=====================================|               |                  |               |
  |                 |                    |                |               |                  |               |
  |                 | 7. Compute median worker_pct           |               |                  |               |
  |                 |                    |                |               |                  |               |
  |                 | 8. Record ruling (escrow settles Task-Board-side, idempotent by ruling_id)|               |
  |                 | =======================================================>|                  |               |
  |                 | <=======================================================|                  |               |
  |                 |                    |                |               |                  |               |
  |                 | 9. Record feedback x2 (409 feedback_exists == success) |                  |               |
  |                 | ==========================================================================>|               |
  |                 | <==========================================================================|               |
  |                 |                    |                |               |                  |               |
  |                 | 10. Persist votes + ruling, status -> ruled                                                |
  |                 | ============================================================================================>|
  |                 | <============================================================================================|
  |                 |                    |                |               |                  |               |
  | 11. 200 { dispute + votes }          |                |               |                  |               |
  | <===============|                    |                |               |                  |               |
```

If step 4, 5, 8, 9, or 10 fails, the dispute reverts to `rebuttal_pending` (deleting any partial ruling row) and the caller receives the corresponding error; a retry re-runs from step 3.

---

## Configuration

Verified against `services/court/config.yaml` and `services/court/src/court_service/config.py`. All fields are required (no defaults, `extra="forbid"` on every section) unless marked optional.

```yaml
service:
  name: "court"
  version: "0.1.0"

server:
  host: "127.0.0.1"
  port: 8005
  log_level: "info"

logging:
  level: "INFO"
  directory: "data/logs"

platform:
  agent_config_path: "../../agents/config.yaml"

disputes:
  rebuttal_deadline_seconds: 86400
  max_claim_length: 10000
  max_rebuttal_length: 10000
  feedback_extremely_satisfied_cutoff: 80
  feedback_satisfied_cutoff: 40
  feedback_comment_max_length: 256

judges:
  panel_size: 1
  mock_worker_pct: 50
  max_deliverable_bytes: 65536
  judges:
    - id: "judge-0"
      provider: "mock"
      model: "mock-judge"
    # Real LM Studio judge config — commented out, not wired for a real run.

request:
  max_body_size: 1048576

db_gateway:
  url: "http://127.0.0.1:8007"
  timeout_seconds: 10
```

| Section / Field | Description |
|---|---|
| `service.name`, `service.version` | Service identity |
| `server.host`, `server.port`, `server.log_level` | Bind address (8005), Uvicorn log level |
| `logging.level`, `logging.directory` | Application log level and log file directory |
| `platform.agent_config_path` | Path to the shared `agents/config.yaml`, used by `AgentFactory` to load the platform agent's Ed25519 keypair (from that file's `data.keys_dir` + roster) and register it. `platform.agent_id` and `platform.private_key_path` are also present as optional/legacy fields on `PlatformConfig` but are **not read anywhere in `lifespan.py`** — key loading happens exclusively through `agent_config_path`. Do not describe `private_key_path` as the live key-loading mechanism. |
| `disputes.rebuttal_deadline_seconds` | Seconds from filing until the rebuttal window closes (86,400 = 24h) |
| `disputes.max_claim_length`, `disputes.max_rebuttal_length` | Character caps on claim/rebuttal text (10,000 each) |
| `disputes.feedback_extremely_satisfied_cutoff` | `worker_pct` threshold at/above which delivery-quality feedback is `extremely_satisfied` (and, inverted, spec-quality is `dissatisfied`) — required config key, no hardcoded default. Currently `80`. |
| `disputes.feedback_satisfied_cutoff` | `worker_pct` threshold at/above which delivery-quality feedback is `satisfied` — required config key. Currently `40`. |
| `disputes.feedback_comment_max_length` | Character cap applied to the ruling-summary text used as the feedback comment — required config key. Currently `256`. |
| `judges.panel_size` | Number of judges (odd, ≥ 1), validated against `len(judges.judges)` at config-load time |
| `judges.mock_worker_pct` | Fixed `worker_pct` returned by `MockJudge` instances — required config key, no hardcoded default. Currently `50`. |
| `judges.max_deliverable_bytes` | Total byte budget (across all fetched assets combined) for deliverable content included in the judge prompt — required config key. Currently `65536`. |
| `judges.judges[]` | Array of judge entries: `id` (unique), `model`, `provider` (`"mock"` or omitted/other for a real litellm judge), `api_base`/`api_key_env`/`temperature` (required for non-mock judges) |
| `request.max_body_size` | Max request body size in bytes for the JSON-POST validation middleware (1 MiB) |
| `db_gateway.url`, `db_gateway.timeout_seconds` | DB Gateway base URL and HTTP timeout; **required** — the service refuses to start if `db_gateway` is missing from config |

### Startup Validation

The following are validated when the service starts (config load + lifespan), and a failure prevents the process from serving traffic:

- `judges.panel_size` must be odd and ≥ 1, and must equal `len(judges.judges)` — enforced by a Pydantic field validator on `JudgesConfig` (raises before the app object exists; **not** an HTTP response).
- All judge `id` values must be unique.
- `db_gateway` config section must be present.
- The platform agent must register successfully (`AgentFactory(...).platform_agent().register()`); a missing `agent_id` after registration raises `RuntimeError`.
- Every non-mock judge with `api_key_env` set must resolve to a non-empty environment variable, or `ValueError` is raised while building the judge panel.

---

## Method-Not-Allowed Handling

Unsupported methods on defined routes return `405 Method Not Allowed` with the standard error envelope (`error: "method_not_allowed"`), via Starlette's `HTTPException` handler (`court_service/core/exceptions.py::http_exception_handler`). `/disputes/file` and `/disputes` also register explicit method-not-allowed routes for the common wrong-method cases.
