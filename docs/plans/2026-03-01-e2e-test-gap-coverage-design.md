# E2E Test Gap Coverage Design

- **Date**: 2026-03-01
- **Status**: Approved

## Context

A gap analysis comparing the system sequence diagrams (`docs/diagrams/system-sequence-diagrams.md`) against existing e2e tests (`agents/tests/e2e/`) identified 6 gap areas with 16 missing test scenarios.

### What's Well Covered

- Agent registration, JWS verification, idempotency (5 tests)
- Banking basics: accounts, credits, balances, insufficient funds (3 tests)
- Task posting with escrow lock/release, cancellation (2 tests)
- Bidding: submit, accept, competing, sealed visibility (6 tests)
- Auth/state machine guards (9 tests)
- Happy path end-to-end: post through approve (1 test)
- Deadline enforcement: execution, bidding, review timeout (3 tests)
- Economic cycle: earn-then-spend (1 test)
- Mutual reputation feedback exchange (1 test)

### What's Missing

Six gap areas, ordered by severity:

1. **Court dispute lifecycle** — Biggest gap. Existing test gracefully degrades when LLM unavailable. Escrow split, reputation posting, and ruling recording on TaskBoard are untested.
2. **Sealed reputation feedback in e2e** — Tested in per-service integration but never end-to-end.
3. **Asset store read-path** — Only upload tested; download, multi-file, and validation untested.
4. **Platform signature enforcement** — Implicit coverage only through fixtures; no negative tests.
5. **Economic invariants with disputes** — No test for partial payouts flowing back into new tasks.
6. **Insufficient funds at task board level** — Bank-level test exists but not task-board-level.

## Deliverables

### New Test Files

| File | Gap Area | Test Count |
|---|---|---|
| `agents/tests/e2e/test_court_rulings.py` | Court dispute lifecycle (Diagram 5) | 5 |
| `agents/tests/e2e/test_reputation_sealed.py` | Sealed feedback mechanics | 3 |
| `agents/tests/e2e/test_asset_store.py` | Asset upload/download/validation | 3 |
| `agents/tests/e2e/test_platform_auth.py` | Platform signature enforcement | 3 |
| `agents/tests/e2e/test_economic_invariants.py` | System-level economic correctness | 2 |

### Test Specifications

#### 1. Court Dispute Lifecycle (`test_court_rulings.py`)

**test_escrow_split_proportional_payout** (Confirming)
- Setup: poster (5000), worker (0). Post task reward=1000. Full lifecycle to disputed.
- File dispute via platform, submit rebuttal, trigger ruling.
- Assert: worker_balance == `worker_pct * 1000 / 100`, poster_balance == `5000 - 1000 + (100 - worker_pct) * 1000 / 100`. Not just sum==5000.
- Services: Identity, Bank, TaskBoard, Court.

**test_ruling_recorded_on_task_board** (Confirming)
- Same setup as above, drive to ruling.
- Assert: `task["status"] == "ruled"`, `task["ruling_id"]` is non-null, `task["worker_pct"]` matches Court ruling, `task["ruling_summary"]` is non-empty string.
- Services: Identity, Bank, TaskBoard, Court.

**test_court_posts_reputation_feedback** (Confirming)
- Same setup, drive to ruling.
- Query `GET /feedback/task/{task_id}` on Reputation service.
- Assert: two feedback records exist — one with `category="spec_quality"` targeting poster, one with `category="delivery_quality"` targeting worker.
- Services: Identity, Bank, TaskBoard, Court, Reputation.

**test_dispute_proceeds_without_rebuttal** (Edge case)
- Setup: drive to disputed state. File dispute via platform. Do NOT submit rebuttal.
- Trigger ruling immediately.
- Assert: ruling succeeds (status 200) or fails with a specific documented error. Court should handle missing rebuttal.
- Services: Identity, Bank, TaskBoard, Court.

**test_duplicate_dispute_rejected** (Adversarial)
- Setup: drive to disputed state via `poster.dispute_task()`.
- File dispute again via platform `POST /disputes/file` with same task_id.
- Assert: `409 DISPUTE_ALREADY_EXISTS`.
- Services: Identity, Bank, TaskBoard, Court.

#### 2. Sealed Reputation Feedback (`test_reputation_sealed.py`)

**test_sealed_feedback_invisible_until_mutual** (Confirming)
- Setup: full happy path through approval.
- Poster submits feedback to Reputation for worker.
- Query `GET /feedback/task/{task_id}` — assert empty list (feedback is sealed).
- Query `GET /feedback/{feedback_id}` — assert 404.
- Worker submits feedback for poster.
- Query `GET /feedback/task/{task_id}` — assert 2 records, both visible.
- Services: Identity, Bank, TaskBoard, Reputation.

**test_self_feedback_rejected** (Adversarial)
- Setup: register and fund one agent. Complete a task (agent is poster).
- Agent submits feedback where `to_agent_id == from_agent_id`.
- Assert: `400 SELF_FEEDBACK`.
- Services: Identity, Bank, TaskBoard, Reputation.

**test_duplicate_feedback_rejected** (Adversarial)
- Setup: full happy path through approval.
- Poster submits feedback for worker. Succeeds.
- Poster submits identical feedback for worker again.
- Assert: `409 FEEDBACK_EXISTS`.
- Services: Identity, Bank, TaskBoard, Reputation.

#### 3. Asset Store (`test_asset_store.py`)

**test_download_uploaded_asset** (Confirming)
- Setup: post, bid, accept. Worker uploads "result.txt" with content b"Hello World".
- List assets via `GET /tasks/{task_id}/assets`. Get `asset_id`.
- Download via `GET /tasks/{task_id}/assets/{asset_id}`.
- Assert: response body == b"Hello World", content-type matches.
- Services: Identity, Bank, TaskBoard.

**test_multiple_asset_uploads** (Confirming)
- Setup: post, bid, accept. Worker uploads 3 files: "code.py" (100 bytes), "readme.md" (200 bytes), "data.json" (150 bytes).
- List assets. Assert: 3 assets, filenames match, sizes match.
- Services: Identity, Bank, TaskBoard.

**test_submit_without_assets_rejected** (Adversarial)
- Setup: post, bid, accept. Worker does NOT upload any assets.
- Worker calls `submit_deliverable(task_id)`.
- Assert: error response (spec says "requires at least 1 asset").
- Services: Identity, Bank, TaskBoard.

#### 4. Platform Signature Enforcement (`test_platform_auth.py`)

**test_non_platform_cannot_release_escrow** (Adversarial)
- Setup: poster posts task (escrow locked). Regular agent (not platform) signs a JWS with `{ action: "escrow_release", escrow_id, recipient_account_id }`.
- Send to `POST /escrow/{id}/release` on Central Bank.
- Assert: `403 FORBIDDEN`.
- Services: Identity, Bank, TaskBoard.

**test_non_platform_cannot_split_escrow** (Adversarial)
- Setup: same as above. Regular agent signs JWS for escrow split.
- Send to `POST /escrow/{id}/split` on Central Bank.
- Assert: `403 FORBIDDEN`.
- Services: Identity, Bank.

**test_non_platform_cannot_credit_account** (Adversarial)
- Setup: two registered agents. Agent A signs JWS with `{ action: "credit", account_id: B, amount: 1000 }`.
- Send to Central Bank credit endpoint.
- Assert: `403 FORBIDDEN`.
- Services: Identity, Bank.

#### 5. Economic Invariants (`test_economic_invariants.py`)

**test_economic_cycle_with_dispute_partial_payout** (Confirming)
- Setup: A (5000 tokens), B (0 tokens).
- Round 1: A posts task (reward=1000), B bids, A accepts, B delivers, A disputes, Court rules worker_pct=60. B gets 600, A gets 4400.
- Round 2: B posts task (reward=400), A bids, B accepts, A delivers, B approves.
- Assert: A = 4400 + 400 = 4800, B = 600 - 400 = 200 (or similar — exact math depends on ruling).
- Services: All five.

**test_insufficient_funds_cannot_post_task** (Adversarial)
- Setup: agent with balance 100. Tries to post task with reward 500.
- Assert: task creation fails with appropriate error (escrow lock rejected, task not persisted).
- Services: Identity, Bank, TaskBoard.

## Ticket Structure

Each test scenario gets a test ticket. Where the test is expected to fail against current code (revealing a missing implementation), an implementation ticket is created and blocked by the test ticket.

```
E2E Test Gap Coverage (epic)
├── Court Rulings
│   ├── [test] test_escrow_split_proportional_payout
│   │   └── [impl] Wire Court escrow split side-effect
│   ├── [test] test_ruling_recorded_on_task_board
│   │   └── [impl] Wire Court → TaskBoard ruling recording
│   ├── [test] test_court_posts_reputation_feedback
│   │   └── [impl] Wire Court → Reputation feedback posting
│   ├── [test] test_dispute_proceeds_without_rebuttal
│   └── [test] test_duplicate_dispute_rejected
├── Sealed Reputation
│   ├── [test] test_sealed_feedback_invisible_until_mutual
│   ├── [test] test_self_feedback_rejected
│   └── [test] test_duplicate_feedback_rejected
├── Asset Store
│   ├── [test] test_download_uploaded_asset
│   ├── [test] test_multiple_asset_uploads
│   └── [test] test_submit_without_assets_rejected
│       └── [impl] Enforce "at least 1 asset" on submit
├── Platform Auth
│   ├── [test] test_non_platform_cannot_release_escrow
│   ├── [test] test_non_platform_cannot_split_escrow
│   └── [test] test_non_platform_cannot_credit_account
└── Economic Invariants
    ├── [test] test_economic_cycle_with_dispute_partial_payout
    └── [test] test_insufficient_funds_cannot_post_task
```
