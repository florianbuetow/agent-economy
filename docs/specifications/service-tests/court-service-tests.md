# Court Service — Production Release Test Specification

## Purpose

This document is the release-gate test specification for the Court Service. It is intentionally strict and unambiguous:

- Every negative case has one required status code and one required error code.
- Every failing response must use the standard error envelope.
- Any behavior not listed here is out of scope for release sign-off.

This document covers the full surface: dispute filing, rebuttals, judge panel ruling, Task-Board-settled escrow side effects, reputation feedback, lifecycle enforcement, and security assertions. Authentication/authorization mechanics live in `court-service-auth-tests.md`; this document assumes valid platform-signed requests except where the scenario itself is about a rejected request.

Verified against `services/court/tests/unit/routers/test_disputes.py`, `test_wp06_rebuttal_window.py`, `test_wp06_retry_clean.py`, `test_wp06_deliverables.py`, `test_wp06_platform_kid.py`, and `test_config.py` / `test_wp06_config.py`.

---

## Required API Error Contract (Normative for Release)

All failing responses must be JSON in this format:

```json
{
  "error": "error_code",
  "message": "Human-readable description",
  "details": {}
}
```

Error codes are **snake_case**. Required status/error mappings:

| Status | Error Code                          | Required When |
|--------|-------------------------------------|---------------|
| 400    | `invalid_jws`                      | JWS token is malformed, missing, empty, null, or not a string |
| 400    | `invalid_json`                     | Request body is malformed JSON |
| 400    | `invalid_payload`                  | Required fields missing from JWS payload, `action` does not match the endpoint, or field constraints violated (claim/rebuttal too long, empty, payload/URL `dispute_id` mismatch) |
| 403    | `forbidden`                        | JWS signature is invalid, `kid` is not the platform agent, or a forwarded `respondent_id` mismatches the dispute |
| 404    | `dispute_not_found`                | No dispute exists with the given `dispute_id` |
| 404    | `task_not_found`                   | Task does not exist on the Task Board (filing or ruling) |
| 405    | `method_not_allowed`               | Unsupported HTTP method on a defined route |
| 409    | `dispute_already_exists`           | A dispute has already been filed for this `task_id` |
| 409    | `dispute_already_ruled`            | Dispute already has a ruling — cannot rule again |
| 409    | `dispute_not_ready`                | Ruling requested while status is not rulable, or no rebuttal exists and the rebuttal window has not closed |
| 409    | `rebuttal_already_submitted`       | Worker has already submitted a rebuttal for this dispute |
| 409    | `invalid_dispute_status`           | The requested operation is not valid for the dispute's current status |
| 413    | `payload_too_large`               | Request body exceeds configured `request.max_body_size` |
| 415    | `unsupported_media_type`           | `Content-Type` is not `application/json` for JSON endpoints |
| 502    | `identity_service_unavailable`     | Local certificate verification raised an unexpected error (legacy code name; no Identity HTTP call is made — see the auth test spec) |
| 502    | `task_board_unavailable`           | Cannot reach the Task Board to fetch task/asset data or record a ruling |
| 502    | `reputation_service_unavailable`   | Cannot reach the Reputation service to record feedback |
| 502    | `judge_unavailable`                | LLM provider returned an error, timed out, produced an unparseable response, or no judges are configured |

**There is no `central_bank_unavailable` code.** The Court never calls the Central Bank — escrow settlement is entirely the Task Board's responsibility, invoked as a side effect of `record_ruling`. A prior draft of this document included `CENTRAL_BANK_UNAVAILABLE`; it described a call the Court's code does not make and has been removed.

`INVALID_PANEL_SIZE` is **not** a runtime API error. Panel-size validation happens once, at config-load time, as a Pydantic validator failure that prevents the process from starting — it can never be observed as an HTTP response. See Category 9.

---

## Test Data Conventions

- `platform_agent` is instantiated locally with a known Ed25519 keypair; the Court verifies its own signature **locally** (no Identity HTTP call). `settings.platform.agent_id` (populated at startup after registration) is the expected `kid`.
- `rogue_agent` is a separate agent with a different keypair, used to test rejection of non-platform signers.
- `jws(signer, payload)` denotes a JWS compact serialization (RFC 7515, EdDSA/Ed25519) with header `{"alg":"EdDSA","kid":"<signer.agent_id>"}` and a valid Ed25519 signature over the given payload.
- `tampered_jws(signer, payload)` denotes a JWS where the payload has been altered after signing (signature mismatch) — local `validate_certificate()` raises `InvalidSignature`.
- `dispute_id` format: `disp-<uuid4>`. `vote_id` format is **store-dependent**: the unit-test fake store (`tests/fakes/in_memory_dispute_store.py:44`, backing every test in this document) generates `vote-<uuid4>`; the real gateway-backed `DisputeDbClient` (`dispute_db_client.py:100`) generates `vote-<dispute_id>-<index>` instead. Tests in this document assert the fake-store shape (`vote-<uuid4>`) because that is what actually runs in CI — see RULE-05/GET-03/SEC-03 below and "Escalations" in the accompanying work report. `task_id`: `t-<uuid4>`. `claimant_id`/`respondent_id`: `a-<uuid4>`. `escrow_id`: `esc-<uuid4>`.
- All timestamps must be valid ISO 8601.
- **Mock judge:** deterministic, configured per test via `judges.mock_worker_pct` (or an injected mock in unit tests) to return a specific `worker_pct` and `reasoning`, with no external LLM call.
- **Mock external services:**
  - **Task Board mock:** returns task data (`task_id`, `spec`, `title`, `reward`, `deliverables`) for `GET /tasks/{task_id}`; returns `404` for unknown tasks; accepts `POST /tasks/{task_id}/ruling` (`record_ruling`); raises a connection error to test `task_board_unavailable`.
  - **Reputation mock:** accepts `POST /feedback`; can be configured to return `409 {"error": "feedback_exists"}` (treated as success) or raise a connection error to test `reputation_service_unavailable`.
  - **DB Gateway mock:** backs dispute/rebuttal/ruling persistence (`/court/claims`, `/court/rebuttals`, `/court/rulings`) and the `escrow_id` lookup (`/board/tasks/{task_id}`).
- Tests involving external mocks assume the mock succeeds unless explicitly stated otherwise.
- A "valid file dispute request" means a JWS signed by the platform agent (correct `kid`) with `action: "file_dispute"` and all required fields present, with the Task Board mock returning valid task data.
- `expire_rebuttal_window(dispute_id)` is a test-infrastructure helper that backdates a dispute's `rebuttal_deadline` into the past — used wherever a scenario needs the rebuttal window closed without waiting on the real clock (GAP-A8/T-039).

---

## Category 1: File Dispute (`POST /disputes/file`)

### FILE-01 File a valid dispute

**Action:** `POST /disputes/file` with a valid platform-signed `file_dispute` JWS.
**Expected:** `201 Created`; `dispute_id` matches `disp-<uuid4>`; `status` is `"rebuttal_pending"`.

### FILE-02 Response includes all dispute fields

**Expected:** `dispute_id`, `task_id`, `claimant_id`, `respondent_id`, `claim`, `rebuttal` (null), `status`, `rebuttal_deadline`, `worker_pct` (null), `ruling_summary` (null), `escrow_id`, `filed_at`, `rebutted_at` (null), `ruled_at` (null), `votes` (`[]`).

### FILE-03 Rebuttal deadline is correctly calculated

**Expected:** `rebuttal_deadline` equals `filed_at` + `disputes.rebuttal_deadline_seconds` (86,400 by default).

### FILE-04 Duplicate dispute for same task is rejected

**Setup:** File a dispute for `task_id = "t-xxx"`.
**Action:** File another dispute for the same `task_id`.
**Expected:** `409`, `error = dispute_already_exists`.

### FILE-05 Task not found on the Task Board

**Setup:** Mock Task Board returns `404` for the given `task_id`.
**Expected:** `404`, `error = task_not_found`.

### FILE-06 Missing claim text
**Expected:** `400`, `error = invalid_payload`.

### FILE-07 Empty claim text
**Expected:** `400`, `error = invalid_payload`.

### FILE-08 Claim too long (exceeds `disputes.max_claim_length`, 10,000 by default)
**Expected:** `400`, `error = invalid_payload`.

### FILE-09 Missing task_id
**Expected:** `400`, `error = invalid_payload`.

### FILE-10 Missing claimant_id
**Expected:** `400`, `error = invalid_payload`.

### FILE-11 Missing respondent_id
**Expected:** `400`, `error = invalid_payload`.

### FILE-12 Missing escrow_id
**Expected:** `400`, `error = invalid_payload`.

### FILE-13 Wrong action value
**Action:** `action: "submit_rebuttal"` instead of `"file_dispute"`.
**Expected:** `400`, `error = invalid_payload`.

### FILE-14 Non-platform signer

**Action:** `token: jws(rogue_agent, {action: "file_dispute", ...})`.
**Expected:** `403`, `error = forbidden`.

### FILE-15 Tampered JWS

**Action:** `token: tampered_jws(platform_agent, {action: "file_dispute", ...})`.
**Expected:** `403`, `error = forbidden`.

### FILE-16 Missing token field

**Action:** body `{}` (no `token`).
**Expected:** `400`, `error = invalid_jws`.

### FILE-17 Task Board unavailable

**Setup:** Mock Task Board raises a connection error on `GET /tasks/{task_id}`.
**Expected:** `502`, `error = task_board_unavailable`.

---

## Category 2: Submit Rebuttal (`POST /disputes/{dispute_id}/rebuttal`)

### REB-01 Submit a valid rebuttal

**Setup:** File a valid dispute (status `rebuttal_pending`).
**Expected:** `200`; `rebuttal` set to submitted text; `rebutted_at` set (ISO 8601).

### REB-02 Dispute not found
**Expected:** `404`, `error = dispute_not_found`.

### REB-03 Rebuttal already submitted

**Setup:** File a dispute, submit a rebuttal.
**Action:** Submit another rebuttal for the same dispute.
**Expected:** `409`, `error = rebuttal_already_submitted`.

### REB-04 Dispute not in rebuttal_pending status

**Setup:** File a dispute, expire the rebuttal window (`expire_rebuttal_window`), trigger a ruling with no rebuttal (status becomes `ruled`).
**Action:** Submit a rebuttal to the now-ruled dispute.
**Expected:** `409`, `error = invalid_dispute_status`.

### REB-05 Missing rebuttal text
**Expected:** `400`, `error = invalid_payload`.

### REB-06 Empty rebuttal text
**Expected:** `400`, `error = invalid_payload`.

### REB-07 Rebuttal too long (exceeds `disputes.max_rebuttal_length`, 10,000 by default)
**Expected:** `400`, `error = invalid_payload`.

### REB-08 Wrong action value

**Action:** `action: "file_dispute"` instead of `"submit_rebuttal"`.
**Expected:** `400`, `error = invalid_payload`.

### REB-09 Non-platform signer
**Expected:** `403`, `error = forbidden`.

### REB-10 Dispute status unchanged after rebuttal

**Expected:** `status` is still `"rebuttal_pending"` after `GET /disputes/{dispute_id}`; `rebuttal` and `rebutted_at` set.

---

## Category 3: Trigger Ruling (`POST /disputes/{dispute_id}/rule`)

### RULE-01 Valid ruling with 1 judge

**Setup:** File a dispute, submit a rebuttal, configure mock judge `worker_pct: 70`.
**Expected:** `200`; `worker_pct` is `70`; `ruling_summary` non-empty; `votes` has exactly 1 entry.

### RULE-02 Ruling equals single vote (panel_size=1)
**Expected:** `worker_pct` on the dispute equals the single judge's vote.

### RULE-03 Dispute status changes to ruled
**Expected:** `GET /disputes/{dispute_id}` shows `status = "ruled"`.

### RULE-04 ruled_at timestamp is set
**Expected:** `ruled_at` valid ISO 8601, after `filed_at`.

### RULE-05 Vote record has correct structure
**Expected:** each vote has `vote_id` (`vote-<uuid4>` under the fake store this test runs against — see Test Data Conventions), `dispute_id`, `judge_id`, `worker_pct` (0–100 int), `reasoning` (non-empty), `voted_at` (ISO 8601).

### RULE-06 Ruling records on the Task Board (escrow settles there, not at Court)

**Setup:** File a dispute with a known `escrow_id`, submit a rebuttal, configure mock judge `worker_pct: 70`.
**Action:** Trigger ruling.
**Expected:** the platform agent's `record_ruling` call (`POST /tasks/{task_id}/ruling`) was made exactly once, carrying `worker_pct: 70`. The Court itself makes **no** Central Bank call — this is a Task-Board-internal effect of `record_ruling`, not a Court-issued escrow split.

### RULE-07 Ruling calls Reputation to record feedback
**Expected:** at least two feedback submissions (spec quality for claimant, delivery quality for respondent).

### RULE-08 Judge returns 0% — poster favored
**Expected:** `worker_pct` is `0`; `record_ruling` called with `worker_pct: 0`.

### RULE-09 Judge returns 100% — worker favored
**Expected:** `worker_pct` is `100`; `record_ruling` called with `worker_pct: 100`.

### RULE-10 Judge returns 50% — even split
**Expected:** `worker_pct` is `50`.

### RULE-11 Judge returns 73% — asymmetric split
**Expected:** `worker_pct` is `73`.

### RULE-12 Dispute not found
**Expected:** `404`, `error = dispute_not_found`.

### RULE-13 Already ruled (after a rebuttal-based ruling)

**Setup:** File, rebut, rule.
**Action:** Rule again.
**Expected:** `409`, `error = dispute_already_ruled`.

### RULE-14 Already ruled (window-expired, no-rebuttal ruling)

**Setup:** File a dispute. **Expire the rebuttal window** (`expire_rebuttal_window`) — no rebuttal is submitted. Trigger ruling (`200`, dispute now `ruled`).
**Action:** Trigger ruling again on the same dispute.
**Expected:** `409`, `error = dispute_already_ruled`.

> **Wording correction:** an earlier draft of this scenario described "ruling without a rebuttal" as unconditionally allowed, with no mention of the rebuttal window. That was inconsistent with the earlier draft's own RULE-19 row, and is superseded by GAP-A8/T-039 (frozen-test exception #6): ruling without a rebuttal is allowed **only once the rebuttal window has expired**. This scenario now explicitly backdates the deadline before the first ruling call.

### RULE-15 Judge unavailable (LLM error)

**Setup:** File, submit rebuttal, configure mock judge to raise.
**Expected:** `502`, `error = judge_unavailable`; dispute status remains `rebuttal_pending` (reverted, safe to retry).

### RULE-16 Task Board unavailable during ruling (record_ruling failure)

**Setup:** File, submit rebuttal, configure mock judge to return a valid vote, configure the Task Board's `record_ruling` call to raise a connection error.
**Expected:** `502`, `error = task_board_unavailable`; dispute status remains `rebuttal_pending` (reverted, votes not persisted, safe to retry). There is no way to observe a `central_bank_unavailable` error here — the Court never issues an independent escrow call.

### RULE-17 Reputation service unavailable

**Setup:** File, submit rebuttal, configure mock judge to return a valid vote, configure Reputation to raise a connection error (not a `409 feedback_exists`, a genuine transport failure).
**Expected:** `502`, `error = reputation_service_unavailable`; dispute status remains `rebuttal_pending` (reverted, safe to retry).

### RULE-18 Wrong action value

**Action:** `action: "file_dispute"` instead of `"trigger_ruling"`.
**Expected:** `400`, `error = invalid_payload`.

### RULE-19 Ruling without rebuttal (window expired)

**Setup:** File a dispute. Do **not** submit a rebuttal. **Expire the rebuttal window.**
**Action:** Trigger ruling.
**Expected:** `200`; `rebuttal` is `null`; `rebutted_at` is `null`; `worker_pct` set (judge evaluates with a null rebuttal context); `status = "ruled"`; `votes` populated.

> Same wording correction as RULE-14: this only passes once the window has actually elapsed. Attempting this **before** the window closes is a different scenario — see RULE-20.

### RULE-20 dispute_not_ready — no rebuttal, window still open

**Setup:** File a valid dispute. Do **not** submit a rebuttal. Do **not** expire the rebuttal window.
**Action:** Trigger ruling immediately.
**Expected:** `409`, `error = dispute_not_ready`. The dispute is left untouched — `GET /disputes/{dispute_id}` still shows `status = "rebuttal_pending"`; this is not a side-effecting failure and requires no revert.

This is the GAP-A8/T-039 scenario new to this release: ruling is blocked, not allowed, when there is neither a rebuttal nor an expired window.

### RULE-21 Reentrant rule call during task fetch fails fast

**Setup:** File and submit a rebuttal. Configure the Task Board mock's `GET /tasks/{task_id}` handler to itself issue a second `POST /disputes/{dispute_id}/rule` call (simulating the Task Board's own lazy deadline evaluator re-entering mid-flight) before returning task data.
**Expected:** the outer call succeeds (`200`, `status: "ruled"`); the reentrant nested call observes the dispute already `judging` and receives `409 dispute_not_ready` — it does not recurse or double-rule (GAP-A1).

### RULE-22 Retry after a crash between Task Board record and Court's own persist converges exactly once

**Setup:** File and submit a rebuttal, configure a mock judge. Make the court-side ruling persist step fail exactly once (simulating a process kill after the Task Board and Reputation calls already committed but before Court recorded the ruling locally). Task Board's `record_ruling` and Reputation's feedback submission are modeled as genuinely idempotent (deduped by `ruling_id` / by `to_agent_id`+`category`, matching their real behavior).
**Action:** Trigger ruling (fails `502`, dispute reverts to `rebuttal_pending`). Trigger ruling again.
**Expected:** the first attempt returns `502` and the dispute reads back `rebuttal_pending`. The second attempt returns `200`, `status: "ruled"`. Across both attempts, exactly **one** Task Board settlement and exactly **one** feedback pair were recorded — never a double-settlement (T-040/GAP-A4).

### RULE-23 Reputation 409 feedback_exists is treated as success

**Setup:** File and submit a rebuttal, configure a mock judge. Configure Reputation's feedback endpoint to return `409 {"error": "feedback_exists"}` for both submissions (simulating a retried ruling whose feedback already landed).
**Expected:** `200`, `status: "ruled"` — the `409 feedback_exists` response does **not** fail the ruling. Both feedback submissions (spec + delivery) are still attempted.

### RULE-24 Judge context includes fetched deliverable text

**Setup:** File and submit a rebuttal. Configure the deliverable fetcher to return known solution text for the task's uploaded asset. Capture the judge's `DisputeContext` argument.
**Expected:** the fetched deliverable text appears in `context.deliverables` — proving the judge prompt is built from actual uploaded asset content, not just task metadata (GAP-A9).

---

## Category 4: Get Dispute (`GET /disputes/{dispute_id}`)

### GET-01 Get a filed dispute (no ruling yet)
**Expected:** `200`; `worker_pct`/`ruling_summary`/`ruled_at` all `null`; `votes` `[]`; `status = "rebuttal_pending"`.

### GET-02 Get a ruled dispute
**Expected:** `worker_pct` int 0–100; `ruling_summary` non-empty; `ruled_at` ISO 8601; `status = "ruled"`; `votes` non-empty.

### GET-03 Votes array structure (panel_size=1)
**Expected:** 1 entry; fields `vote_id` (`vote-<uuid4>` under the fake store — see Test Data Conventions), `dispute_id`, `judge_id` (`"judge-0"`), `worker_pct`, `reasoning`, `voted_at`.

### GET-04 Dispute not found
**Expected:** `404`, `error = dispute_not_found`.

### GET-05 No authentication required
**Expected:** `200` with no `Authorization` header and no token.

---

## Category 5: List Disputes (`GET /disputes`)

### LIST-01 Empty list on fresh system
**Expected:** `200`, `{ "disputes": [] }`.

### LIST-02 List all disputes
**Setup:** File 3 disputes for different tasks.
**Expected:** 3 entries, each with `dispute_id`, `task_id`, `claimant_id`, `respondent_id`, `status`, `worker_pct`, `filed_at`, `ruled_at`.

### LIST-03 Filter by task_id
**Expected:** only the matching task's dispute.

### LIST-04 Filter by status
**Setup:** One dispute `rebuttal_pending`, another ruled.
**Expected:** `?status=rebuttal_pending` returns only the pending one.

### LIST-05 Filter by both task_id and status
**Expected:** only the dispute matching both.

### LIST-06 No authentication required
**Expected:** `200` with no auth.

---

## Category 6: Health (`GET /health`)

### HLTH-01 Health schema is correct
**Expected:** `status`, `uptime_seconds`, `started_at`, `total_disputes`, `active_disputes`; `status = "ok"`.

### HLTH-02 total_disputes count is accurate
**Setup:** File N disputes.
**Expected:** `total_disputes == N`.

### HLTH-03 active_disputes equals count of non-ruled disputes
**Setup:** File 3, rule 1.
**Expected:** `total_disputes == 3`, `active_disputes == 2`.

### HLTH-04 Uptime is monotonic
**Expected:** a second call ≥1s later reports a larger `uptime_seconds`.

---

## Category 7: HTTP Method Misuse

### HTTP-01 Wrong methods on defined routes are blocked

**Action:** `GET/PUT/DELETE /disputes/file`, `PUT/DELETE/PATCH /disputes/{dispute_id}`, `GET/PUT/DELETE /disputes/{dispute_id}/rebuttal`, `GET/PUT/DELETE /disputes/{dispute_id}/rule`, `POST /disputes`, `POST /health`.
**Expected:** `405`, `error = method_not_allowed` for each.

---

## Category 8: Cross-Cutting Security Assertions

### SEC-01 Error envelope consistency
**Expected:** all failures have top-level `error` (string) and `message` (string).

### SEC-02 No internal error leakage
**Action:** trigger `invalid_json`, `invalid_jws`, `dispute_not_found`, `forbidden`, `judge_unavailable`.
**Expected:** `message` never includes stack traces, SQL fragments, file paths, or driver internals.

### SEC-03 IDs are correctly formatted
**Expected:** every `dispute_id` matches `disp-<uuid4>`; every `vote_id` matches `vote-<uuid4>` (the fake store's shape — see Test Data Conventions; the real gateway-backed store's `vote-<dispute_id>-<index>` shape is not what this test, as written, exercises).

---

## Category 9: Judge Panel Configuration (startup, not runtime)

These are **configuration-load-time** validations (Pydantic `field_validator` on `JudgesConfig`), not HTTP behaviors. Testing them means constructing a config file and calling `get_settings()` directly, not calling an endpoint.

### JUDGE-01 Even panel size rejected at config load
**Setup:** `judges.panel_size: 2` with 2 judges.
**Action:** `get_settings()`.
**Expected:** raises (Pydantic `ValidationError`) — the service cannot start.

### JUDGE-02 Panel size 0 rejected at config load
**Expected:** raises.

### JUDGE-03 Negative panel size rejected at config load
**Expected:** raises.

### JUDGE-04 Judge count must equal panel_size
**Setup:** `panel_size: 1` with exactly 1 judge entry.
**Expected:** `get_settings()` succeeds; `settings.judges.panel_size == 1`, `len(settings.judges.judges) == 1`.

### JUDGE-05 Panel size 1 is valid
**Expected:** `get_settings()` succeeds with `panel_size == 1`.

### JUDGE-06 MockJudge uses the configured mock_worker_pct

**Setup:** Build settings with `judges.mock_worker_pct: 37` and a single `provider: "mock"` judge.
**Action:** `court_service.core.lifespan._build_judges(settings)`.
**Expected:** returns one `MockJudge` whose fixed percentage is `37` — not a hardcoded value.

### JUDGE-07 max_deliverable_bytes is required config

**Setup:** Build settings with `judges.max_deliverable_bytes: 4096`.
**Expected:** `settings.judges.max_deliverable_bytes == 4096` (rejecting the config entirely if the key is absent — no hardcoded default exists in code).

---

## Category 10: Dispute Lifecycle Integration

### LIFE-01 Full lifecycle: file, rebuttal, rule, verify final state

**Setup:** mock judge `worker_pct: 70`.
**Action:** file → `201`, `rebuttal_pending`; rebuttal → `200`, `rebuttal`/`rebutted_at` set; rule → `200`, `worker_pct: 70`, `status: "ruled"`; final `GET` → full dispute.
**Expected:** `status = "ruled"`; original `claim`; submitted `rebuttal`; `worker_pct: 70`; non-empty `ruling_summary`; `filed_at < rebutted_at < ruled_at`; 1 vote with `worker_pct: 70`; Task Board `record_ruling` called; Reputation feedback called.

### LIFE-02 File then rule without rebuttal (window expired)

**Setup:** File a dispute, **expire the rebuttal window**, configure mock judge `worker_pct: 80`.
**Action:** file → `201`; rule (no rebuttal submitted) → `200`.
**Expected:** `status = "ruled"`; `rebuttal`/`rebutted_at` `null`; `worker_pct: 80`.

> Wording correction, same as RULE-14/19: the earlier draft omitted the window-expiry precondition. Attempting this immediately after filing (no window expiry) is RULE-20's `dispute_not_ready` scenario, not this one.

### LIFE-03 Cannot file two disputes for same task
**Expected:** `409`, `error = dispute_already_exists`; original dispute unchanged.

### LIFE-04 Cannot submit rebuttal after ruling

**Setup:** File a dispute, **expire the rebuttal window**, trigger ruling with no rebuttal (status → `ruled`).
**Action:** Attempt to submit a rebuttal to the now-ruled dispute.
**Expected:** `409`, `error = invalid_dispute_status`.

> Wording correction, same class as LIFE-02: ruling without a rebuttal requires the window to have closed first.

### LIFE-05 Cannot rule twice
**Expected:** `409`, `error = dispute_already_ruled`.

---

## Release Gate Checklist

Service is release-ready only if:

1. All tests in this document pass.
2. No test marked deterministic has alternate acceptable behavior.
3. No endpoint returns `500` in any test scenario.
4. All failing responses conform to the required error envelope.
5. `judges.panel_size`/`mock_worker_pct`/`max_deliverable_bytes` and `disputes.feedback_extremely_satisfied_cutoff`/`feedback_satisfied_cutoff`/`feedback_comment_max_length` are all present and required in `config.yaml` — no hardcoded fallback exists in code for any of them.

---

## Coverage Summary

| Category | IDs | Count |
|----------|-----|-------|
| File Dispute | FILE-01 to FILE-17 | 17 |
| Submit Rebuttal | REB-01 to REB-10 | 10 |
| Trigger Ruling | RULE-01 to RULE-24 | 24 |
| Get Dispute | GET-01 to GET-05 | 5 |
| List Disputes | LIST-01 to LIST-06 | 6 |
| Health | HLTH-01 to HLTH-04 | 4 |
| HTTP Method Misuse | HTTP-01 | 1 |
| Cross-Cutting Security | SEC-01 to SEC-03 | 3 |
| Judge Panel Configuration | JUDGE-01 to JUDGE-07 | 7 |
| Dispute Lifecycle Integration | LIFE-01 to LIFE-05 | 5 |
| **Total** |  | **82** |

| Endpoint | Covered By |
|----------|------------|
| `POST /disputes/file` | FILE-01 to FILE-17, SEC-01 to SEC-03, LIFE-01 to LIFE-03 |
| `POST /disputes/{dispute_id}/rebuttal` | REB-01 to REB-10, LIFE-01, LIFE-04 |
| `POST /disputes/{dispute_id}/rule` | RULE-01 to RULE-24, LIFE-01, LIFE-02, LIFE-05 |
| `GET /disputes/{dispute_id}` | GET-01 to GET-05, RULE-03, RULE-05, LIFE-01 |
| `GET /disputes` | LIST-01 to LIST-06 |
| `GET /health` | HLTH-01 to HLTH-04 |
