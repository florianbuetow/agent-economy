# Missing Specifications Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Write 8 formal specification documents for the Central Bank and Court services, following the established format patterns used by Identity, Task Board, and Reputation specs.

**Architecture:** Documentation-only task. No code changes. Central Bank specs are derived from existing implementation + design docs. Court specs are new designs based on the approved design document at `docs/plans/2026-02-28-missing-specifications-design.md`.

**Tech Stack:** Markdown specification documents.

**Reference documents (read before starting any task):**
- Design doc: `docs/plans/2026-02-28-missing-specifications-design.md`
- Format reference (API spec): `docs/specifications/service-api/identity-service-specs.md`
- Format reference (auth spec): `docs/specifications/service-api/reputation-service-auth-specs.md`
- Format reference (test spec): `docs/specifications/service-tests/identity-service-tests.md`
- Format reference (auth test spec): `docs/specifications/service-tests/reputation-service-auth-tests.md`

---

## Task 1: Central Bank API Specification

**Files:**
- Create: `docs/specifications/service-api/central-bank-service-specs.md`

**Reference inputs:**
- Read: `docs/specifications/service-specs/central-bank-design.md` (existing design doc — source of truth for behavior)
- Read: `docs/specifications/service-api/identity-service-specs.md` (format reference)
- Read: `docs/specifications/service-api/reputation-service-specs.md` (format reference for a service with auth)

**Step 1: Write the API specification**

Create `docs/specifications/service-api/central-bank-service-specs.md` following the identity-service-specs.md structure:

1. **Purpose** — The Central Bank manages accounts, balances, transaction history, and escrow for the Agent Task Economy. Financial backbone.
2. **Core Principles** — Integer coins, no overdraft, platform is sole source of funds, agent-signed escrow consent, platform-controlled escrow release, platform as agent.
3. **Service Dependencies** — Identity service (port 8001) for JWS verification and agent existence checks.
4. **Data Model** — Three tables: Account (account_id, balance, created_at), Transaction (tx_id, account_id, type, amount, balance_after, reference, timestamp), Escrow (escrow_id, payer_account_id, amount, task_id, status, created_at, resolved_at). Document uniqueness constraints and indexes.
5. **Endpoints** — 8 endpoints total:
   - `GET /health` — no auth, returns status + total_accounts + total_escrowed
   - `POST /accounts` — platform JWS (body), creates account with initial balance, verifies agent exists in Identity
   - `POST /accounts/{account_id}/credit` — platform JWS (body), adds funds, idempotent by reference
   - `GET /accounts/{account_id}` — agent JWS (Bearer header), own account only
   - `GET /accounts/{account_id}/transactions` — agent JWS (Bearer header), own account only, ordered by timestamp ASC then tx_id ASC
   - `POST /escrow/lock` — agent JWS (body), locks own funds, idempotent by task_id+amount
   - `POST /escrow/{escrow_id}/release` — platform JWS (body), full payout to recipient
   - `POST /escrow/{escrow_id}/split` — platform JWS (body), proportional split using floor(total * worker_pct / 100), poster gets remainder
6. **Error Codes** — All 13 error codes with status, code, and when. Include: INVALID_JWS, INVALID_JSON, INVALID_PAYLOAD, INVALID_AMOUNT, PAYLOAD_MISMATCH, INSUFFICIENT_FUNDS, FORBIDDEN, ACCOUNT_NOT_FOUND, AGENT_NOT_FOUND, ESCROW_NOT_FOUND, ACCOUNT_EXISTS, ESCROW_ALREADY_RESOLVED, IDENTITY_SERVICE_UNAVAILABLE. Also ESCROW_ALREADY_LOCKED for duplicate task_id with different amount.
7. **Standardized Error Format** — `{"error": "ERROR_CODE", "message": "...", "details": {}}`
8. **What This Service Does NOT Do** — No agent-to-agent transfers, no salary scheduling, no debit endpoint, no account deletion, no pagination.
9. **Interaction Patterns** — Sequence diagrams for: account creation flow, credit flow, escrow lock flow, escrow release flow, escrow split flow.
10. **Configuration** — Full config.yaml structure with all required fields.

For each endpoint, document: request format, response format (with status code), all possible errors with status/code/description, authentication requirements, business logic rules, and idempotency behavior where applicable.

**Step 2: Verify document completeness**

Check that every endpoint from the design doc is covered, every error code is listed, and the format matches the identity-service-specs.md structure.

**Step 3: Commit**

```bash
git add docs/specifications/service-api/central-bank-service-specs.md
git commit -m "docs: add Central Bank API specification"
```

---

## Task 2: Central Bank Authentication Specification

**Files:**
- Create: `docs/specifications/service-api/central-bank-service-auth-specs.md`

**Reference inputs:**
- Read: `docs/specifications/service-api/reputation-service-auth-specs.md` (format reference)
- Read: `docs/specifications/service-api/task-board-service-auth-specs.md` (format reference for complex auth)
- Read: `docs/specifications/service-api/central-bank-service-specs.md` (just created)

**Step 1: Write the auth specification**

Create `docs/specifications/service-api/central-bank-service-auth-specs.md` following the reputation-service-auth-specs.md structure:

1. **Purpose** — How the Central Bank authenticates operations using JWS tokens.
2. **Authentication Model — Two Tiers:**
   - **Agent-signed operations** — `GET /accounts/{id}` (Bearer), `GET /accounts/{id}/transactions` (Bearer), `POST /escrow/lock` (body token). Signer must match the account being accessed or the agent whose funds are locked.
   - **Platform-signed operations** — `POST /accounts` (body token), `POST /accounts/{id}/credit` (body token), `POST /escrow/{id}/release` (body token), `POST /escrow/{id}/split` (body token). Signer must be configured `platform.agent_id`.
   - **Public operations** — `GET /health`.
3. **JWS Token Format** — Header (`alg: EdDSA`, `kid: agent_id`), payload with `action` field, Ed25519 signature.
4. **Action Values** — Table mapping actions to endpoints and expected signers: `create_account`, `credit`, `get_balance`, `get_transactions`, `escrow_lock`, `escrow_release`, `escrow_split`.
5. **Two Token Delivery Mechanisms** — Body token (POST endpoints: `{"token": "eyJ..."}`) vs Bearer token (GET endpoints: `Authorization: Bearer eyJ...`).
6. **Authentication Flow** — Sequence diagram showing: client sends JWS → Bank calls Identity `POST /agents/verify-jws` → Identity returns valid/invalid + agent_id + payload → Bank checks authorization (platform vs agent, account ownership).
7. **Payload Validation** — Bank checks action field matches expected value, required payload fields present, payload fields match URL parameters where applicable.
8. **Authorization Errors** — When and why FORBIDDEN is returned (invalid signature, wrong signer, non-platform agent doing platform ops, agent accessing another's account).

**Step 2: Commit**

```bash
git add docs/specifications/service-api/central-bank-service-auth-specs.md
git commit -m "docs: add Central Bank authentication specification"
```

---

## Task 3: Central Bank Test Specification

**Files:**
- Create: `docs/specifications/service-tests/central-bank-service-tests.md`

**Reference inputs:**
- Read: `docs/specifications/service-tests/identity-service-tests.md` (format reference)
- Read: `docs/specifications/service-tests/reputation-service-tests.md` (format reference)
- Read: `docs/specifications/service-api/central-bank-service-specs.md` (source of truth for behavior)
- Read: `services/central-bank/tests/` (existing test files — to ensure test spec covers what's already tested)

**Step 1: Write the test specification**

Create `docs/specifications/service-tests/central-bank-service-tests.md` following the identity-service-tests.md structure:

1. **Purpose** — Release-gate test specification for the Central Bank Service.
2. **Required API Error Contract** — Table of all status/error code mappings.
3. **Test Data Conventions** — JWS token conventions, agent ID format, tx_id format (`tx-<uuid4>`), escrow_id format (`esc-<uuid4>`), platform agent conventions.
4. **Test Categories:**

   **Category 1: Account Creation (`POST /accounts`)** — Prefix: `ACC-`
   - ACC-01: Create valid account with initial balance > 0
   - ACC-02: Create valid account with initial balance = 0
   - ACC-03: Duplicate account rejected (409, ACCOUNT_EXISTS)
   - ACC-04: Agent not found in Identity (404, AGENT_NOT_FOUND)
   - ACC-05: Non-platform signer rejected (403, FORBIDDEN)
   - ACC-06: Negative initial balance rejected (400, INVALID_AMOUNT)
   - ACC-07: Missing agent_id in payload (400, INVALID_PAYLOAD)
   - ACC-08: Missing initial_balance in payload (400, INVALID_PAYLOAD)
   - ACC-09: Wrong action in payload (400, INVALID_PAYLOAD)
   - ACC-10: Initial balance creates credit transaction with reference "initial_balance"

   **Category 2: Credit (`POST /accounts/{account_id}/credit`)** — Prefix: `CR-`
   - CR-01: Valid credit increases balance
   - CR-02: Idempotent credit with same reference+amount returns same tx_id
   - CR-03: Duplicate reference with different amount rejected (400, PAYLOAD_MISMATCH)
   - CR-04: Account not found (404, ACCOUNT_NOT_FOUND)
   - CR-05: Non-platform signer rejected (403, FORBIDDEN)
   - CR-06: Zero amount rejected (400, INVALID_AMOUNT)
   - CR-07: Negative amount rejected (400, INVALID_AMOUNT)
   - CR-08: Missing reference in payload (400, INVALID_PAYLOAD)
   - CR-09: Payload account_id mismatch with URL (400, PAYLOAD_MISMATCH)

   **Category 3: Balance Query (`GET /accounts/{account_id}`)** — Prefix: `BAL-`
   - BAL-01: Get own balance
   - BAL-02: Account not found (404, ACCOUNT_NOT_FOUND)
   - BAL-03: Wrong agent accessing another's account (403, FORBIDDEN)
   - BAL-04: Wrong action in payload (400, INVALID_PAYLOAD)
   - BAL-05: Payload account_id mismatch with URL (400, PAYLOAD_MISMATCH)
   - BAL-06: Balance reflects credits and escrow locks

   **Category 4: Transaction History (`GET /accounts/{account_id}/transactions`)** — Prefix: `TX-`
   - TX-01: Get transaction history (ordered by timestamp ASC, tx_id ASC)
   - TX-02: Empty transaction list for new account with 0 initial balance
   - TX-03: Account not found (404, ACCOUNT_NOT_FOUND)
   - TX-04: Wrong agent accessing another's history (403, FORBIDDEN)
   - TX-05: History includes credit, escrow_lock, and escrow_release types
   - TX-06: Each transaction has tx_id, type, amount, balance_after, reference, timestamp

   **Category 5: Escrow Lock (`POST /escrow/lock`)** — Prefix: `ESC-`
   - ESC-01: Valid escrow lock
   - ESC-02: Insufficient funds rejected (402, INSUFFICIENT_FUNDS)
   - ESC-03: Account not found (404, ACCOUNT_NOT_FOUND)
   - ESC-04: Agent locking another's funds rejected (403, FORBIDDEN)
   - ESC-05: Idempotent lock with same task_id+amount returns same escrow_id
   - ESC-06: Duplicate task_id with different amount rejected (409, ESCROW_ALREADY_LOCKED)
   - ESC-07: Zero amount rejected (400, INVALID_AMOUNT)
   - ESC-08: Negative amount rejected (400, INVALID_AMOUNT)
   - ESC-09: Missing task_id in payload (400, INVALID_PAYLOAD)
   - ESC-10: Balance decreases by lock amount
   - ESC-11: Escrow lock creates escrow_lock transaction

   **Category 6: Escrow Release (`POST /escrow/{escrow_id}/release`)** — Prefix: `REL-`
   - REL-01: Valid full release to recipient
   - REL-02: Escrow not found (404, ESCROW_NOT_FOUND)
   - REL-03: Already resolved escrow rejected (409, ESCROW_ALREADY_RESOLVED)
   - REL-04: Non-platform signer rejected (403, FORBIDDEN)
   - REL-05: Recipient account not found (404, ACCOUNT_NOT_FOUND)
   - REL-06: Payload escrow_id mismatch with URL (400, PAYLOAD_MISMATCH)
   - REL-07: Recipient balance increases by full escrow amount
   - REL-08: Escrow status changes to "released"
   - REL-09: Release creates escrow_release transaction on recipient

   **Category 7: Escrow Split (`POST /escrow/{escrow_id}/split`)** — Prefix: `SPL-`
   - SPL-01: Valid 50/50 split
   - SPL-02: Valid 80/20 split (worker gets floor, poster gets remainder)
   - SPL-03: Valid 100/0 split (worker gets all)
   - SPL-04: Valid 0/100 split (poster gets all)
   - SPL-05: Escrow not found (404, ESCROW_NOT_FOUND)
   - SPL-06: Already resolved escrow rejected (409, ESCROW_ALREADY_RESOLVED)
   - SPL-07: Non-platform signer rejected (403, FORBIDDEN)
   - SPL-08: worker_pct > 100 rejected (400, INVALID_AMOUNT)
   - SPL-09: worker_pct < 0 rejected (400, INVALID_AMOUNT)
   - SPL-10: Poster account_id must match escrow payer (400, PAYLOAD_MISMATCH)
   - SPL-11: Worker account not found (404, ACCOUNT_NOT_FOUND)
   - SPL-12: Escrow status changes to "split"
   - SPL-13: Both accounts credited correctly (verify balances)
   - SPL-14: Split creates escrow_release transactions on both accounts

   **Category 8: Health (`GET /health`)** — Prefix: `HLTH-`
   - HLTH-01: Health schema correct (status, uptime_seconds, started_at, total_accounts, total_escrowed)
   - HLTH-02: total_accounts reflects actual count
   - HLTH-03: total_escrowed reflects sum of locked escrows only
   - HLTH-04: Uptime is monotonic

   **Category 9: HTTP Method Misuse** — Prefix: `HTTP-`
   - HTTP-01: Wrong methods on all endpoints return 405

   **Category 10: Cross-Cutting Security** — Prefix: `SEC-`
   - SEC-01: Error envelope consistency
   - SEC-02: No internal error leakage
   - SEC-03: IDs are opaque and correctly formatted

5. **Coverage Summary** — Table of test count per category and endpoint coverage.

**Step 2: Commit**

```bash
git add docs/specifications/service-tests/central-bank-service-tests.md
git commit -m "docs: add Central Bank test specification"
```

---

## Task 4: Central Bank Authentication Test Specification

**Files:**
- Create: `docs/specifications/service-tests/central-bank-service-auth-tests.md`

**Reference inputs:**
- Read: `docs/specifications/service-tests/reputation-service-auth-tests.md` (format reference)
- Read: `docs/specifications/service-api/central-bank-service-auth-specs.md` (source of truth)

**Step 1: Write the auth test specification**

Create `docs/specifications/service-tests/central-bank-service-auth-tests.md` following the reputation-service-auth-tests.md structure:

1. **Purpose** — Release-gate tests for JWS authentication on Central Bank endpoints.
2. **Prerequisites** — Running Identity service or mock, pre-registered agents with known keypairs, platform agent configured.
3. **New Auth Error Codes** — INVALID_JWS, INVALID_PAYLOAD, FORBIDDEN, PAYLOAD_MISMATCH, IDENTITY_SERVICE_UNAVAILABLE.
4. **Test Data Conventions** — jws(), tampered_jws(), platform agent conventions.
5. **Test Categories:**

   **Category 1: JWS Token Validation (POST endpoints)** — Prefix: `AUTH-`
   - AUTH-01: Valid platform JWS on POST /accounts succeeds
   - AUTH-02: Missing token field (400, INVALID_JWS)
   - AUTH-03: Null token field (400, INVALID_JWS)
   - AUTH-04: Non-string token (400, INVALID_JWS)
   - AUTH-05: Empty string token (400, INVALID_JWS)
   - AUTH-06: Malformed JWS (not three parts) (400, INVALID_JWS)
   - AUTH-07: Tampered JWS (403, FORBIDDEN)
   - AUTH-08: Non-platform signer on platform endpoint (403, FORBIDDEN)
   - AUTH-09: Wrong action value (400, INVALID_PAYLOAD)
   - AUTH-10: Missing action in payload (400, INVALID_PAYLOAD)

   **Category 2: Bearer Token Validation (GET endpoints)** — Prefix: `BEARER-`
   - BEARER-01: Valid Bearer token on GET /accounts/{id} succeeds
   - BEARER-02: Missing Authorization header (400, INVALID_JWS)
   - BEARER-03: Authorization header without "Bearer " prefix (400, INVALID_JWS)
   - BEARER-04: Tampered Bearer token (403, FORBIDDEN)
   - BEARER-05: Wrong agent accessing another's account (403, FORBIDDEN)

   **Category 3: Identity Service Dependency** — Prefix: `IDEP-`
   - IDEP-01: Identity service down returns 502 (IDENTITY_SERVICE_UNAVAILABLE)
   - IDEP-02: Identity service timeout returns 502
   - IDEP-03: Identity service returns unexpected response returns 502

6. **Coverage Summary**

**Step 2: Commit**

```bash
git add docs/specifications/service-tests/central-bank-service-auth-tests.md
git commit -m "docs: add Central Bank authentication test specification"
```

---

## Task 5: Court Service API Specification

**Files:**
- Create: `docs/specifications/service-api/court-service-specs.md`

**Reference inputs:**
- Read: `docs/plans/2026-02-28-missing-specifications-design.md` (approved design)
- Read: `docs/specifications/service-api/identity-service-specs.md` (format reference)
- Read: `docs/specifications/service-api/task-board-service-specs.md` (format reference for complex service)

**Step 1: Write the API specification**

Create `docs/specifications/service-api/court-service-specs.md` following the established structure:

1. **Purpose** — The Court is the dispute resolution engine. When a poster rejects a deliverable, the Court evaluates specification, deliverables, claim, and rebuttal through an LLM judge panel and issues a proportional payout ruling.
2. **Core Principles** — Ambiguity favors the worker, configurable odd-numbered panel, every judge must vote, court executes side-effects, platform-signed requests only, SQLite persistence.
3. **Service Dependencies** — Identity (JWS verification), Task Board (fetch task data), Central Bank (split escrow), Reputation (record feedback).
4. **Data Model:**
   - Dispute table: dispute_id (disp-<uuid4>), task_id, claimant_id, respondent_id, claim (1-10,000 chars), rebuttal (nullable, 1-10,000 chars), status (filed/rebuttal_pending/judging/ruled), rebuttal_deadline, worker_pct (nullable), ruling_summary (nullable), escrow_id, filed_at, rebutted_at (nullable), ruled_at (nullable).
   - JudgeVote table: vote_id (vote-<uuid4>), dispute_id (FK), judge_id, worker_pct (0-100), reasoning, voted_at.
   - Uniqueness: one dispute per task_id.
5. **Dispute Lifecycle** — ASCII diagram: filed → rebuttal_pending → judging → ruled. Document transitions and triggers.
6. **Endpoints** — 6 endpoints:
   - `POST /disputes/file` — platform JWS (body). Payload: `{action: "file_dispute", task_id, claimant_id, respondent_id, claim, escrow_id}`. Court fetches task data from Task Board to validate task exists and is in disputed status. Creates dispute with status rebuttal_pending. Response 201 with dispute record.
   - `POST /disputes/{dispute_id}/rebuttal` — platform JWS (body). Payload: `{action: "submit_rebuttal", dispute_id, rebuttal}`. Dispute must be in rebuttal_pending status. Sets rebuttal text, updates rebutted_at. Response 200 with updated dispute.
   - `POST /disputes/{dispute_id}/rule` — platform JWS (body). Payload: `{action: "trigger_ruling", dispute_id}`. Dispute must be in rebuttal_pending or judging status. Calls each judge in panel, collects votes, computes median worker_pct, executes side-effects (Central Bank split, Reputation feedback, Task Board ruling update). Response 200 with dispute + votes + ruling.
   - `GET /disputes/{dispute_id}` — no auth. Returns dispute with votes array. If ruled, includes worker_pct and ruling_summary.
   - `GET /disputes` — no auth. Query params: `task_id` (optional), `status` (optional). Returns list of disputes.
   - `GET /health` — no auth. Returns status, uptime_seconds, started_at, total_disputes, active_disputes.
7. **Judge Architecture:**
   - Abstract Judge base class with `async evaluate(dispute_context) -> JudgeVote` method.
   - LLM Judge implementation using LiteLLM.
   - Prompts stored in code file next to judge implementation.
   - Judge prompt receives: task spec, deliverables list, claim text, rebuttal text (if any), core principle "ambiguity favors the worker".
   - Judge must return: worker_pct (0-100), reasoning (string).
   - Panel evaluation: all judges called, all must return votes, median worker_pct computed.
8. **Side-Effects on Ruling:**
   - Central Bank: `POST /escrow/{escrow_id}/split` with worker_pct, worker_account_id = respondent_id, poster_account_id = claimant_id.
   - Reputation: `POST /feedback` for spec_quality (worker rates poster based on ruling — low worker_pct implies bad spec), `POST /feedback` for delivery_quality (poster rates worker based on ruling).
   - Task Board: updates task with ruling_id, worker_pct, ruling_summary, ruled_at.
9. **Error Codes** — All 16 error codes with status/code/when.
10. **Standardized Error Format** — `{"error": "ERROR_CODE", "message": "...", "details": {}}`
11. **What This Service Does NOT Do** — No appeals, no judge recusal, no multi-round deliberation, no partial rulings, no real-time streaming of judge reasoning.
12. **Configuration** — Full config.yaml structure.
13. **Interaction Patterns** — Sequence diagrams for: file dispute flow, submit rebuttal flow, trigger ruling flow (showing all side-effect calls).

**Step 2: Commit**

```bash
git add docs/specifications/service-api/court-service-specs.md
git commit -m "docs: add Court service API specification"
```

---

## Task 6: Court Service Authentication Specification

**Files:**
- Create: `docs/specifications/service-api/court-service-auth-specs.md`

**Reference inputs:**
- Read: `docs/specifications/service-api/reputation-service-auth-specs.md` (format reference)
- Read: `docs/specifications/service-api/court-service-specs.md` (just created)

**Step 1: Write the auth specification**

Create `docs/specifications/service-api/court-service-auth-specs.md` following the reputation-service-auth-specs.md structure:

1. **Purpose** — How the Court authenticates operations using JWS tokens.
2. **Authentication Model — Two Tiers:**
   - **Platform-signed operations** — `POST /disputes/file`, `POST /disputes/{id}/rebuttal`, `POST /disputes/{id}/rule`. All require platform agent signature. The Task Board orchestrates — it files claims on behalf of posters and submits rebuttals on behalf of workers.
   - **Public operations** — `GET /disputes/{id}`, `GET /disputes`, `GET /health`.
3. **Why All Writes Are Platform-Only** — The Court is an internal service. Agents never interact with it directly. The Task Board handles agent authentication and forwards authorized requests to the Court signed by the platform key.
4. **JWS Token Format** — Same as Central Bank: `{"alg":"EdDSA","kid":"<platform_agent_id>"}`.
5. **Action Values** — `file_dispute`, `submit_rebuttal`, `trigger_ruling`.
6. **Authentication Flow** — Sequence diagram showing: Task Board sends platform-signed JWS → Court calls Identity `POST /agents/verify-jws` → Identity validates → Court checks signer is platform.
7. **Auth Error Codes** — INVALID_JWS, INVALID_PAYLOAD, FORBIDDEN, IDENTITY_SERVICE_UNAVAILABLE.

**Step 2: Commit**

```bash
git add docs/specifications/service-api/court-service-auth-specs.md
git commit -m "docs: add Court service authentication specification"
```

---

## Task 7: Court Service Test Specification

**Files:**
- Create: `docs/specifications/service-tests/court-service-tests.md`

**Reference inputs:**
- Read: `docs/specifications/service-tests/identity-service-tests.md` (format reference)
- Read: `docs/specifications/service-tests/task-board-service-tests.md` (format reference for complex service)
- Read: `docs/specifications/service-api/court-service-specs.md` (source of truth)

**Step 1: Write the test specification**

Create `docs/specifications/service-tests/court-service-tests.md` following the established structure:

1. **Purpose** — Release-gate test specification for the Court Service.
2. **Required API Error Contract** — Table of all status/error code mappings.
3. **Test Data Conventions** — JWS token conventions, dispute_id format (disp-<uuid4>), vote_id format (vote-<uuid4>), platform agent, mock judge (deterministic for testing — returns configurable worker_pct), mock external services (Task Board, Central Bank, Reputation).
4. **Test Categories:**

   **Category 1: File Dispute (`POST /disputes/file`)** — Prefix: `FILE-`
   - FILE-01: File valid dispute
   - FILE-02: Dispute already exists for task (409, DISPUTE_ALREADY_EXISTS)
   - FILE-03: Task not found in Task Board (404, TASK_NOT_FOUND)
   - FILE-04: Missing claim text (400, INVALID_PAYLOAD)
   - FILE-05: Claim too long (>10,000 chars) (400, INVALID_PAYLOAD)
   - FILE-06: Missing task_id (400, INVALID_PAYLOAD)
   - FILE-07: Missing claimant_id (400, INVALID_PAYLOAD)
   - FILE-08: Missing respondent_id (400, INVALID_PAYLOAD)
   - FILE-09: Missing escrow_id (400, INVALID_PAYLOAD)
   - FILE-10: Wrong action value (400, INVALID_PAYLOAD)
   - FILE-11: Dispute status is rebuttal_pending after filing
   - FILE-12: Rebuttal deadline is filed_at + configured rebuttal_deadline_seconds

   **Category 2: Submit Rebuttal (`POST /disputes/{dispute_id}/rebuttal`)** — Prefix: `REB-`
   - REB-01: Submit valid rebuttal
   - REB-02: Dispute not found (404, DISPUTE_NOT_FOUND)
   - REB-03: Rebuttal already submitted (409, REBUTTAL_ALREADY_SUBMITTED)
   - REB-04: Dispute not in rebuttal_pending status (409, INVALID_DISPUTE_STATUS)
   - REB-05: Missing rebuttal text (400, INVALID_PAYLOAD)
   - REB-06: Rebuttal too long (>10,000 chars) (400, INVALID_PAYLOAD)
   - REB-07: Dispute rebutted_at is set after submission
   - REB-08: Dispute rebuttal field contains submitted text

   **Category 3: Trigger Ruling (`POST /disputes/{dispute_id}/rule`)** — Prefix: `RULE-`
   - RULE-01: Valid ruling with 1 judge (median = single vote)
   - RULE-02: Dispute not found (404, DISPUTE_NOT_FOUND)
   - RULE-03: Dispute already ruled (409, DISPUTE_ALREADY_RULED)
   - RULE-04: Dispute status not rebuttal_pending (409, INVALID_DISPUTE_STATUS) — but dispute in "filed" status should also be rejected since it hasn't been acknowledged
   - RULE-05: Ruling sets worker_pct and ruling_summary
   - RULE-06: Ruling creates vote records (one per judge)
   - RULE-07: Ruling calls Central Bank to split escrow
   - RULE-08: Ruling calls Reputation to record feedback
   - RULE-09: Judge returns 0% worker_pct — poster gets full refund
   - RULE-10: Judge returns 100% worker_pct — worker gets full payout
   - RULE-11: Judge returns 50% worker_pct — even split
   - RULE-12: Judge unavailable returns 502 (JUDGE_UNAVAILABLE)
   - RULE-13: Central Bank unavailable returns 502 (CENTRAL_BANK_UNAVAILABLE)
   - RULE-14: Reputation service unavailable returns 502 (REPUTATION_SERVICE_UNAVAILABLE)
   - RULE-15: Dispute status changes to "ruled" after ruling
   - RULE-16: Dispute ruled_at is set

   **Category 4: Get Dispute (`GET /disputes/{dispute_id}`)** — Prefix: `GET-`
   - GET-01: Get existing dispute (before ruling)
   - GET-02: Get dispute after ruling includes votes and worker_pct
   - GET-03: Dispute not found (404, DISPUTE_NOT_FOUND)

   **Category 5: List Disputes (`GET /disputes`)** — Prefix: `LIST-`
   - LIST-01: Empty list on fresh system
   - LIST-02: List all disputes
   - LIST-03: Filter by task_id
   - LIST-04: Filter by status

   **Category 6: Health (`GET /health`)** — Prefix: `HLTH-`
   - HLTH-01: Health schema (status, uptime_seconds, started_at, total_disputes, active_disputes)
   - HLTH-02: total_disputes count is accurate
   - HLTH-03: active_disputes counts non-ruled disputes
   - HLTH-04: Uptime is monotonic

   **Category 7: HTTP Method Misuse** — Prefix: `HTTP-`
   - HTTP-01: Wrong methods on defined routes return 405

   **Category 8: Cross-Cutting Security** — Prefix: `SEC-`
   - SEC-01: Error envelope consistency
   - SEC-02: No internal error leakage
   - SEC-03: IDs are opaque and correctly formatted

   **Category 9: Judge Panel Configuration** — Prefix: `JUDGE-`
   - JUDGE-01: Panel size must be odd (startup validation)
   - JUDGE-02: Panel size 0 rejected at startup
   - JUDGE-03: Even panel size rejected at startup
   - JUDGE-04: Each judge must cast a vote (no missing votes in ruling)

5. **Coverage Summary**

**Step 2: Commit**

```bash
git add docs/specifications/service-tests/court-service-tests.md
git commit -m "docs: add Court service test specification"
```

---

## Task 8: Court Service Authentication Test Specification

**Files:**
- Create: `docs/specifications/service-tests/court-service-auth-tests.md`

**Reference inputs:**
- Read: `docs/specifications/service-tests/reputation-service-auth-tests.md` (format reference)
- Read: `docs/specifications/service-api/court-service-auth-specs.md` (source of truth)

**Step 1: Write the auth test specification**

Create `docs/specifications/service-tests/court-service-auth-tests.md` following the reputation-service-auth-tests.md structure:

1. **Purpose** — Release-gate tests for JWS authentication on Court endpoints.
2. **Prerequisites** — Running Identity service or mock, platform agent with known keypair.
3. **New Auth Error Codes** — INVALID_JWS, INVALID_PAYLOAD, FORBIDDEN, IDENTITY_SERVICE_UNAVAILABLE.
4. **Test Data Conventions** — jws(), tampered_jws(), platform agent conventions.
5. **Test Categories:**

   **Category 1: Platform JWS Validation** — Prefix: `AUTH-`
   - AUTH-01: Valid platform JWS on POST /disputes/file succeeds
   - AUTH-02: Valid platform JWS on POST /disputes/{id}/rebuttal succeeds
   - AUTH-03: Valid platform JWS on POST /disputes/{id}/rule succeeds
   - AUTH-04: Missing token field (400, INVALID_JWS)
   - AUTH-05: Null token field (400, INVALID_JWS)
   - AUTH-06: Non-string token (400, INVALID_JWS)
   - AUTH-07: Empty string token (400, INVALID_JWS)
   - AUTH-08: Malformed JWS (400, INVALID_JWS)
   - AUTH-09: Tampered JWS (403, FORBIDDEN)
   - AUTH-10: Non-platform signer (403, FORBIDDEN)
   - AUTH-11: Wrong action value (400, INVALID_PAYLOAD)
   - AUTH-12: Missing action in payload (400, INVALID_PAYLOAD)

   **Category 2: GET Endpoints Are Public** — Prefix: `PUB-`
   - PUB-01: GET /disputes/{id} requires no auth
   - PUB-02: GET /disputes requires no auth
   - PUB-03: GET /health requires no auth

   **Category 3: Identity Service Dependency** — Prefix: `IDEP-`
   - IDEP-01: Identity service down returns 502 (IDENTITY_SERVICE_UNAVAILABLE)
   - IDEP-02: Identity service timeout returns 502
   - IDEP-03: Identity service unexpected response returns 502

6. **Coverage Summary**

**Step 2: Commit**

```bash
git add docs/specifications/service-tests/court-service-auth-tests.md
git commit -m "docs: add Court service authentication test specification"
```

---

## Final Step: Push to Remote

After all 8 documents are committed:

```bash
git push
```

---

## Coverage Summary

| Task | Document | Service |
|------|----------|---------|
| 1 | API Spec | Central Bank |
| 2 | Auth Spec | Central Bank |
| 3 | Test Spec | Central Bank |
| 4 | Auth Test Spec | Central Bank |
| 5 | API Spec | Court |
| 6 | Auth Spec | Court |
| 7 | Test Spec | Court |
| 8 | Auth Test Spec | Court |

Total: 8 specification documents.
