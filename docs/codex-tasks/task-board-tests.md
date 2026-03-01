# Task Board Service — Test Implementation Plan

This plan is for implementing the Task Board service test suite BEFORE any service implementation code is written. The tests must be syntactically valid, CI-compliant, and expected to fail (since the service is not yet implemented).

---

## Files to Read First

1. `AGENTS.md` — project conventions, code style, testing rules
2. `docs/specifications/service-tests/task-board-service-tests.md` — 179 test cases across 17 categories
3. `docs/specifications/service-tests/task-board-service-auth-tests.md` — 41 test cases across 6 categories
4. `docs/specifications/service-api/task-board-service-specs.md` — API specification
5. `docs/specifications/service-api/task-board-service-auth-specs.md` — authentication specification

Reference implementations for test patterns:
- `services/central-bank/tests/` — conftest fixtures, JWS helpers, mock patterns
- `services/reputation/tests/` — feedback test patterns, identity mock patterns

---

## Working Directory

```
services/task-board/
```

---

## Test Inventory

220 test cases total (179 + 41):

| Category | IDs | Count | File |
|----------|-----|-------|------|
| Task Creation | TC-01 to TC-28 (TC-14a/b/c) | 30 | `test_tasks.py` |
| Task Queries | TQ-01 to TQ-13 | 13 | `test_tasks.py` |
| Task Cancellation | CAN-01 to CAN-09 | 9 | `test_tasks.py` |
| Bidding | BID-01 to BID-15 | 15 | `test_bids.py` |
| Bid Listing | BL-01 to BL-08 | 8 | `test_bids.py` |
| Bid Acceptance | BA-01 to BA-10 | 10 | `test_bids.py` |
| Asset Upload | AU-01 to AU-11 | 11 | `test_assets.py` |
| Asset Retrieval | AR-01 to AR-06 | 6 | `test_assets.py` |
| Deliverable Submission | SUB-01 to SUB-09 | 9 | `test_submission.py` |
| Approval | APP-01 to APP-09 | 9 | `test_submission.py` |
| Dispute | DIS-01 to DIS-10 | 10 | `test_disputes.py` |
| Ruling | RUL-01 to RUL-13 | 13 | `test_disputes.py` |
| Lifecycle / Deadlines | LIFE-01 to LIFE-12 | 12 | `test_lifecycle.py` |
| Health | HEALTH-01 to HEALTH-04 | 4 | `test_health.py` |
| HTTP Method Misuse | HTTP-01 | 1 | `test_security.py` |
| Error Precedence | PREC-01 to PREC-10 | 10 | `test_security.py` |
| Cross-Cutting Security | SEC-01 to SEC-09 | 9 | `test_security.py` |
| Body Token Edge Cases | AUTH-01 to AUTH-13 | 13 | `test_auth.py` |
| Bearer Token Validation | BEARER-01 to BEARER-13 | 13 | `test_auth.py` |
| Identity Service Dependency | IDEP-01 to IDEP-03 | 3 | `test_auth.py` |
| Public Endpoints | PUB-01 to PUB-06 | 6 | `test_auth.py` |
| Cross-Service Token Replay | REPLAY-01 to REPLAY-03 | 3 | `test_auth.py` |
| Auth Cross-Cutting Security | SEC-AUTH-01 to SEC-AUTH-03 | 3 | `test_auth.py` |

---

## Files to Create

| File | Purpose |
|------|---------|
| `tests/conftest.py` | Shared config (docstring only) |
| `tests/unit/conftest.py` | Auto-clear settings cache + app state |
| `tests/unit/test_config.py` | Config loading + validation tests |
| `tests/unit/routers/__init__.py` | Package marker |
| `tests/unit/routers/conftest.py` | App fixture, client, JWS helpers, all mocks |
| `tests/unit/routers/test_health.py` | HEALTH-01 to HEALTH-04 |
| `tests/unit/routers/test_tasks.py` | TC-01 to TC-28, TQ-01 to TQ-13, CAN-01 to CAN-09 (52 tests) |
| `tests/unit/routers/test_bids.py` | BID-01 to BID-15, BL-01 to BL-08, BA-01 to BA-10 (33 tests) |
| `tests/unit/routers/test_assets.py` | AU-01 to AU-11, AR-01 to AR-06 (17 tests) |
| `tests/unit/routers/test_submission.py` | SUB-01 to SUB-09, APP-01 to APP-09 (18 tests) |
| `tests/unit/routers/test_disputes.py` | DIS-01 to DIS-10, RUL-01 to RUL-13 (23 tests) |
| `tests/unit/routers/test_lifecycle.py` | LIFE-01 to LIFE-12 (12 tests) |
| `tests/unit/routers/test_security.py` | HTTP-01, PREC-01 to PREC-10, SEC-01 to SEC-09 (20 tests) |
| `tests/unit/routers/test_auth.py` | AUTH-01 to AUTH-13, BEARER-01 to BEARER-13, IDEP-01 to IDEP-03, PUB-01 to PUB-06, REPLAY-01 to REPLAY-03, SEC-AUTH-01 to SEC-AUTH-03 (41 tests) |
| `tests/integration/conftest.py` | Stub |
| `tests/performance/conftest.py` | Stub |

---

## Implementation Sequence

### Step 1: Fixture Infrastructure

Write the conftest files first. These must compile and pass lint even without the service implementation.

**Key challenge**: The test fixtures import from `task_board_service.*` modules that don't exist yet. To make tests CI-compliant before implementation:
- The `app` fixture in `tests/unit/routers/conftest.py` imports `create_app`, `lifespan`, etc.
- These imports will fail at runtime (expected — tests should fail)
- But they must pass static analysis (ruff, mypy)

**Approach**: Since the task board service modules don't exist yet, the imports will be unresolvable. The tests should be written with the correct imports and will fail with `ImportError` when run — this is acceptable per the workflow ("tests are expected to fail"). `ruff check` and `ruff format` will pass because they don't resolve imports.

#### `tests/conftest.py`

Docstring-only file. No fixtures at this level.

#### `tests/unit/conftest.py`

```python
@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """Reset settings singleton between tests."""
    from task_board_service.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
```

#### `tests/unit/routers/conftest.py`

This is the primary fixture file. Must provide:

1. **JWS helper fixtures:**
   - `make_keypair()` — returns `(private_key_hex, public_key_b64)` using `Ed25519PrivateKey.generate()`
   - `platform_keypair` — fixture returning a fixed platform keypair
   - `alice_keypair`, `bob_keypair`, `carol_keypair` — fixture keypairs for test agents
   - `make_jws(private_hex, agent_id, payload_dict)` — creates JWS compact token using `joserfc`
   - `tamper_jws(token)` — alters the payload portion of a JWS after signing (for tamper tests)

2. **Agent ID fixtures:**
   - `platform_agent_id` — fixed UUID for the platform agent
   - `alice_agent_id`, `bob_agent_id`, `carol_agent_id` — fixed UUIDs for test agents
   - `make_agent_id()` — generates `a-<uuid4>` strings

3. **Task lifecycle helpers:**
   - `make_task_id()` — generates `t-<uuid4>` strings
   - `create_task(client, poster_keypair, poster_id, task_id, reward, ...)` — helper that creates a task via POST /tasks and returns the response
   - `submit_bid(client, bidder_keypair, bidder_id, task_id, amount, ...)` — helper that submits a bid
   - `accept_bid(client, poster_keypair, poster_id, task_id, bid_id)` — helper that accepts a bid
   - `upload_asset(client, worker_keypair, worker_id, task_id, filename, content)` — helper that uploads an asset via multipart
   - `submit_deliverable(client, worker_keypair, worker_id, task_id)` — helper that submits deliverable
   - `approve_task(client, poster_keypair, poster_id, task_id)` — helper that approves
   - `file_dispute(client, poster_keypair, poster_id, task_id, reason)` — helper that files dispute
   - `submit_ruling(client, platform_keypair, platform_id, task_id, worker_pct, summary)` — helper that submits ruling

4. **Mock fixtures:**
   - `mock_identity_verify_jws` — patches the Identity service client to return success for valid JWS, with the signer's `agent_id` extracted from the JWS `kid` header. Must be configurable to return failure (for tampered/unregistered tests)
   - `mock_identity_unavailable` — patches the Identity service client to raise a connection error
   - `mock_identity_timeout` — patches the Identity service client to raise a timeout error
   - `mock_identity_unexpected_response` — patches the Identity service client to return HTTP 500 with non-JSON body
   - `mock_central_bank_escrow` — patches the Central Bank client to return success for escrow operations (lock returns `esc-<uuid4>`, release/split return success)
   - `mock_central_bank_insufficient_funds` — patches the Central Bank client to return 402
   - `mock_central_bank_unavailable` — patches the Central Bank client to raise a connection error

5. **App + client fixture:**
   - `app` — creates the FastAPI app via `create_app()` with test config (short deadlines for lifecycle tests, platform agent_id matching the platform fixture)
   - `client` — `httpx.AsyncClient` or `TestClient` bound to the app

**Important**: Use `unittest.mock.patch` or `pytest-mock`'s `mocker` fixture for all mocks. The Identity and Central Bank mocks must be applied at the `httpx.AsyncClient` level inside the Task Board's service layer, NOT at the HTTP transport level.

### Step 2: Config Tests (`test_config.py`)

Standard config loading tests:
- Valid config loads without error
- Missing required sections raise `ValidationError`
- Extra keys raise `ValidationError` (due to `extra="forbid"`)
- Platform `agent_id` must be present
- Identity and Central Bank base URLs must be present
- Deadline defaults are configurable

### Step 3: Health Tests (`test_health.py`)

HEALTH-01 through HEALTH-04. Straightforward — GET /health returns 200, correct schema, counts are accurate, POST returns 405.

### Step 4: Task Tests (`test_tasks.py`)

52 test cases organized into classes:

```python
class TestTaskCreation:          # TC-01 to TC-28 (30 tests)
class TestTaskQueries:           # TQ-01 to TQ-13 (13 tests)
class TestTaskCancellation:      # CAN-01 to CAN-09 (9 tests)
```

**Task Creation (TC-01 to TC-28):**
- TC-01: Happy path — create task with valid dual tokens, assert all response fields
- TC-02: Duplicate task_id rejected (409 TASK_ALREADY_EXISTS)
- TC-03: Invalid task_id format (4 sub-cases, 400 INVALID_TASK_ID)
- TC-04 to TC-06: Missing tokens (400 INVALID_JWS)
- TC-07: Malformed task_token (5 sub-cases, 400 INVALID_JWS)
- TC-08: Wrong action in task_token (400 INVALID_PAYLOAD)
- TC-09: Missing required payload fields (8 sub-cases, 400 INVALID_PAYLOAD)
- TC-10: Signer != poster_id (403 FORBIDDEN)
- TC-11: task_id mismatch between tokens (400 TOKEN_MISMATCH)
- TC-12: reward/amount mismatch between tokens (400 TOKEN_MISMATCH)
- TC-13: Invalid reward values (5 sub-cases, 400 INVALID_REWARD)
- TC-14a/b/c: Invalid deadline values for each deadline field (400 INVALID_DEADLINE)
- TC-15: Escrow lock failure — insufficient funds (402 INSUFFICIENT_FUNDS)
- TC-16: Escrow lock failure — Central Bank unavailable (502 CENTRAL_BANK_UNAVAILABLE)
- TC-17: Title at max length accepted
- TC-18: Title exceeds max length rejected
- TC-19: Spec at max length accepted
- TC-20: Tampered task_token (403 FORBIDDEN)
- TC-21: Escrow signer != task signer (400 TOKEN_MISMATCH or 403 FORBIDDEN)
- TC-22: Identity service unavailable (502 IDENTITY_SERVICE_UNAVAILABLE)
- TC-23: Malformed JSON body (400 INVALID_JSON)
- TC-24: Wrong content type (415 UNSUPPORTED_MEDIA_TYPE)
- TC-25: Oversized request body (413 PAYLOAD_TOO_LARGE)
- TC-26: Mass-assignment — extra fields in payload ignored
- TC-27: Concurrent duplicate task_id race is safe (one 201, one 409)
- TC-28: Empty body (400 INVALID_JWS)

**Task Queries (TQ-01 to TQ-13):**
- TQ-01: Get single task by ID (200, full fields)
- TQ-02: Get non-existent task (404 TASK_NOT_FOUND)
- TQ-03: Malformed task_id in path (404)
- TQ-04: Path traversal in task_id (404, no leakage)
- TQ-05: List tasks empty (200, empty array)
- TQ-06: List tasks populated (200, multiple tasks)
- TQ-07: List tasks filtered by status
- TQ-08: List tasks filtered by poster_id
- TQ-09: List tasks pagination (offset/limit)
- TQ-10: List tasks ordered by created_at descending
- TQ-11: List tasks does not expose internal fields
- TQ-12: Idempotent read (two GETs return identical JSON)
- TQ-13: SQL injection in path parameter (404, no leakage)

**Task Cancellation (CAN-01 to CAN-09):**
- CAN-01: Poster cancels open task (200, status=cancelled)
- CAN-02: Cancel releases escrow (escrow mock called with release)
- CAN-03: Non-poster cannot cancel (403 FORBIDDEN)
- CAN-04: Cancel non-existent task (404 TASK_NOT_FOUND)
- CAN-05: Cancel already cancelled task (409 INVALID_STATUS)
- CAN-06: Wrong action in cancel token (400 INVALID_PAYLOAD)
- CAN-07: Cancel task in wrong status (accepted/execution/etc.) (409 INVALID_STATUS)
- CAN-08: Malformed cancel token (400 INVALID_JWS)
- CAN-09: Cancel with expired bidding deadline still works

### Step 5: Bid Tests (`test_bids.py`)

33 test cases organized into classes:

```python
class TestBidding:        # BID-01 to BID-15 (15 tests)
class TestBidListing:     # BL-01 to BL-08 (8 tests)
class TestBidAcceptance:  # BA-01 to BA-10 (10 tests)
```

**Bidding (BID-01 to BID-15):**
- BID-01: Submit valid bid (201, bid_id, bidder_id, amount, etc.)
- BID-02: Bid on non-existent task (404 TASK_NOT_FOUND)
- BID-03: Bid on cancelled task (409 INVALID_STATUS)
- BID-04: Bid on accepted task (409 INVALID_STATUS)
- BID-05: Duplicate bid by same agent (409 BID_ALREADY_EXISTS)
- BID-06: Multiple bids from different agents (all 201)
- BID-07: Signer != bidder_id (403 FORBIDDEN)
- BID-08: Wrong action (400 INVALID_PAYLOAD)
- BID-09: Missing required payload fields (400 INVALID_PAYLOAD)
- BID-10: Invalid bid amount (0, negative, float, string) (400 INVALID_REWARD)
- BID-11: Self-bid — poster bids on own task (400 SELF_BID)
- BID-12: Bid after bidding deadline expired (409 INVALID_STATUS — task lazily transitions to cancelled)
- BID-13: Concurrent duplicate bid race is safe
- BID-14: Bid increments bid_count on task
- BID-15: Malformed bid token (400 INVALID_JWS)

**Bid Listing (BL-01 to BL-08):**
- BL-01: List bids on task with bids (200, array)
- BL-02: List bids on task with no bids (200, empty array)
- BL-03: List bids on non-existent task (404 TASK_NOT_FOUND)
- BL-04: Sealed bids — during OPEN status, only poster sees bids (requires Bearer auth)
- BL-05: Unsealed bids — after OPEN status, bids are public (no auth needed)
- BL-06: Bid list includes bidder_id, amount, bid_id, submitted_at
- BL-07: Bid list ordered by submitted_at
- BL-08: Non-poster gets 403 on sealed bid listing (OPEN status)

**Bid Acceptance (BA-01 to BA-10):**
- BA-01: Poster accepts valid bid (200, status transitions to accepted, worker_id set)
- BA-02: Accept non-existent bid (404 BID_NOT_FOUND)
- BA-03: Accept bid on non-existent task (404 TASK_NOT_FOUND)
- BA-04: Accept bid on wrong-status task (409 INVALID_STATUS)
- BA-05: Non-poster cannot accept (403 FORBIDDEN)
- BA-06: Wrong action (400 INVALID_PAYLOAD)
- BA-07: Signer != poster_id (403 FORBIDDEN)
- BA-08: Accept sets execution_deadline (accepted_at + deadline_seconds)
- BA-09: Accept sets accepted_at timestamp
- BA-10: Accepting a bid after bidding_deadline (if still open) is allowed

### Step 6: Asset Tests (`test_assets.py`)

17 test cases organized into classes:

```python
class TestAssetUpload:    # AU-01 to AU-11 (11 tests)
class TestAssetRetrieval: # AR-01 to AR-06 (6 tests)
```

**Asset Upload (AU-01 to AU-11):**
- AU-01: Worker uploads asset via multipart (201, asset_id, filename, content_hash, uploaded_at)
- AU-02: Upload to non-existent task (404 TASK_NOT_FOUND)
- AU-03: Upload to task in wrong status (409 INVALID_STATUS — must be ACCEPTED or EXECUTION)
- AU-04: Non-worker cannot upload (403 FORBIDDEN)
- AU-05: No file in multipart request (400 NO_FILE)
- AU-06: File exceeds max size (413 FILE_TOO_LARGE)
- AU-07: Multiple uploads for same task (all 201, different asset_ids)
- AU-08: Too many assets (409 TOO_MANY_ASSETS)
- AU-09: Content hash is SHA-256 hex digest of file content
- AU-10: Asset upload uses Bearer token in Authorization header
- AU-11: Poster cannot upload assets (403 FORBIDDEN)

**Asset Retrieval (AR-01 to AR-06):**
- AR-01: List assets for task (200, array of asset metadata)
- AR-02: List assets for task with no assets (200, empty array)
- AR-03: Download specific asset by ID (200, file content)
- AR-04: Download non-existent asset (404 ASSET_NOT_FOUND)
- AR-05: List assets on non-existent task (404 TASK_NOT_FOUND)
- AR-06: Assets are public (no auth required for GET)

### Step 7: Submission & Approval Tests (`test_submission.py`)

18 test cases organized into classes:

```python
class TestDeliverableSubmission: # SUB-01 to SUB-09 (9 tests)
class TestApproval:              # APP-01 to APP-09 (9 tests)
```

**Deliverable Submission (SUB-01 to SUB-09):**
- SUB-01: Worker submits deliverable (200, status transitions to review, submitted_at set)
- SUB-02: Non-worker cannot submit (403 FORBIDDEN)
- SUB-03: Submit to task in wrong status (409 INVALID_STATUS — must be EXECUTION)
- SUB-04: Submit with no assets uploaded (400 NO_ASSETS)
- SUB-05: Submit sets review_deadline (submitted_at + review_deadline_seconds)
- SUB-06: Submission is idempotent? Or rejected? (Check spec — 409 INVALID_STATUS if already submitted)
- SUB-07: Wrong action (400 INVALID_PAYLOAD)
- SUB-08: Submit after execution deadline expired (409 INVALID_STATUS — task lazily transitions to expired)
- SUB-09: Missing payload fields (400 INVALID_PAYLOAD)

**Approval (APP-01 to APP-09):**
- APP-01: Poster approves deliverable (200, status transitions to completed, approved_at set)
- APP-02: Non-poster cannot approve (403 FORBIDDEN)
- APP-03: Approve task in wrong status (409 INVALID_STATUS — must be REVIEW)
- APP-04: Approve releases escrow to worker (escrow mock called with release to worker)
- APP-05: Approve sets approved_at timestamp
- APP-06: Wrong action (400 INVALID_PAYLOAD)
- APP-07: Signer != poster_id (403 FORBIDDEN)
- APP-08: Approve after review deadline (auto-approved via lazy eval — GET shows completed)
- APP-09: Missing payload fields (400 INVALID_PAYLOAD)

### Step 8: Dispute & Ruling Tests (`test_disputes.py`)

23 test cases organized into classes:

```python
class TestDispute: # DIS-01 to DIS-10 (10 tests)
class TestRuling:  # RUL-01 to RUL-13 (13 tests)
```

**Dispute (DIS-01 to DIS-10):**
- DIS-01: Poster files dispute (200, status transitions to disputed, disputed_at set, dispute_reason stored)
- DIS-02: Non-poster cannot dispute (403 FORBIDDEN)
- DIS-03: Dispute task in wrong status (409 INVALID_STATUS — must be REVIEW)
- DIS-04: Empty dispute reason (400 INVALID_REASON)
- DIS-05: Dispute reason exceeds max length (400 INVALID_REASON)
- DIS-06: Dispute reason at max length accepted
- DIS-07: Dispute sets disputed_at timestamp
- DIS-08: Wrong action (400 INVALID_PAYLOAD)
- DIS-09: Signer != poster_id (403 FORBIDDEN)
- DIS-10: Missing payload fields (400 INVALID_PAYLOAD)

**Ruling (RUL-01 to RUL-13):**
- RUL-01: Platform submits ruling (200, status transitions to ruled, ruling fields set)
- RUL-02: Non-platform cannot submit ruling (403 FORBIDDEN)
- RUL-03: Ruling on task in wrong status (409 INVALID_STATUS — must be DISPUTED)
- RUL-04: worker_pct=100 — full payout to worker (escrow released to worker)
- RUL-05: worker_pct=0 — full refund to poster (escrow released to poster)
- RUL-06: worker_pct=50 — split payout (escrow split 50/50)
- RUL-07: Invalid worker_pct (<0, >100, float, string) (400 INVALID_WORKER_PCT)
- RUL-08: Ruling summary stored
- RUL-09: Ruling sets ruled_at, worker_pct, ruling_summary
- RUL-10: Wrong action (400 INVALID_PAYLOAD)
- RUL-11: Ruling on non-existent task (404 TASK_NOT_FOUND)
- RUL-12: Missing payload fields (400 INVALID_PAYLOAD)
- RUL-13: Central Bank unavailable during escrow operation (502 CENTRAL_BANK_UNAVAILABLE)

### Step 9: Lifecycle Tests (`test_lifecycle.py`)

12 test cases covering deadline-triggered state transitions:

```python
class TestLifecycle: # LIFE-01 to LIFE-12 (12 tests)
```

These tests require short deadline configurations (e.g., 2 second deadlines) and may use `time.sleep()` or `freezegun`/`time-machine` to simulate time passage.

- LIFE-01: Full happy path (create → bid → accept → upload → submit → approve → completed)
- LIFE-02: Full dispute path (create → bid → accept → upload → submit → dispute → ruling → ruled)
- LIFE-03: Bidding deadline expiry — task transitions to cancelled on next access
- LIFE-04: Execution deadline expiry — task transitions to expired on next access
- LIFE-05: Review deadline expiry — auto-approve, task transitions to completed
- LIFE-06: Expired tasks appear with correct status in listing
- LIFE-07: Cannot bid on expired (lazily cancelled) task
- LIFE-08: Cannot submit on expired execution deadline
- LIFE-09: All mutation endpoints reject operations on terminal-status tasks (cancelled, completed, ruled, expired)
- LIFE-10: Task status reflects lazy evaluation on GET
- LIFE-11: Terminal status is permanent — no further transitions
- LIFE-12: Review period — dispute and approve are both available, first one wins

### Step 10: Security Tests (`test_security.py`)

20 test cases organized into classes:

```python
class TestHTTPMethods:         # HTTP-01 (1 test)
class TestErrorPrecedence:     # PREC-01 to PREC-10 (10 tests)
class TestCrossCuttingSecurity: # SEC-01 to SEC-09 (9 tests)
```

**HTTP Method Misuse (HTTP-01):**
Test all wrong methods on all defined routes — every unsupported method returns 405 METHOD_NOT_ALLOWED. Cover at minimum:
- GET /tasks (POST only for creation)
- DELETE /tasks
- DELETE /tasks/{id}
- PATCH /tasks/{id}
- GET /tasks/{id}/cancel
- GET /tasks/{id}/bids (POST only for submission)
- DELETE /tasks/{id}/bids
- GET /tasks/{id}/bids/{bid_id}/accept
- DELETE /tasks/{id}/assets
- GET /tasks/{id}/submit
- GET /tasks/{id}/approve
- GET /tasks/{id}/dispute
- GET /tasks/{id}/ruling
- POST /health

**Error Precedence (PREC-01 to PREC-10):**
- PREC-01: Content-Type checked before token validation (415 before 400)
- PREC-02: Body size checked before token validation (413 before 400)
- PREC-03: JSON parsing checked before token validation (INVALID_JSON before INVALID_JWS)
- PREC-04: Token format checked before payload validation (INVALID_JWS before INVALID_PAYLOAD)
- PREC-05: JWS verification before payload field validation (FORBIDDEN before INVALID_PAYLOAD)
- PREC-06: Action validation before field validation (INVALID_PAYLOAD/action before INVALID_PAYLOAD/fields)
- PREC-07: Signer matching before business logic (FORBIDDEN before INVALID_STATUS)
- PREC-08: Task existence before status validation (TASK_NOT_FOUND before INVALID_STATUS)
- PREC-09: Status validation before business logic (INVALID_STATUS before domain errors)
- PREC-10: Identity unavailable before payload validation (502 before 400)

**Cross-Cutting Security (SEC-01 to SEC-09):**
- SEC-01: Error envelope consistency — all error codes have {error, message, details}
- SEC-02: No internal error leakage — no stack traces, file paths, SQL, driver internals
- SEC-03: Task IDs are client-generated t-<uuid4>
- SEC-04: Bid IDs are opaque bid-<uuid4>
- SEC-05: Asset IDs are opaque asset-<uuid4>
- SEC-06: Escrow IDs match esc-<uuid4>
- SEC-07: Cross-action token replay rejected (bid token replayed on submit endpoint)
- SEC-08: SQL injection in path parameters (404, no leakage)
- SEC-09: Path traversal in asset download (404, no leakage)

### Step 11: Auth Tests (`test_auth.py`)

41 test cases from the auth specification:

```python
class TestBodyTokenEdgeCases:       # AUTH-01 to AUTH-13 (13 tests)
class TestBearerTokenValidation:    # BEARER-01 to BEARER-13 (13 tests)
class TestIdentityDependency:       # IDEP-01 to IDEP-03 (3 tests)
class TestPublicEndpoints:          # PUB-01 to PUB-06 (6 tests)
class TestTokenReplay:              # REPLAY-01 to REPLAY-03 (3 tests)
class TestAuthSecurity:             # SEC-AUTH-01 to SEC-AUTH-03 (3 tests)
```

**Body Token Edge Cases (AUTH-01 to AUTH-13):**
- AUTH-01: Null task_token and escrow_token (400 INVALID_JWS)
- AUTH-02: Null token on single-token endpoint (400 INVALID_JWS)
- AUTH-03: Integer token (400 INVALID_JWS)
- AUTH-04: Array token (400 INVALID_JWS)
- AUTH-05: Object token (400 INVALID_JWS)
- AUTH-06: Boolean token (400 INVALID_JWS)
- AUTH-07: Missing action in JWS payload (400 INVALID_PAYLOAD)
- AUTH-08: Missing action on platform endpoint (400 INVALID_PAYLOAD)
- AUTH-09: Non-object JSON body (array) on single-token endpoint (400 INVALID_JSON)
- AUTH-10: Non-object JSON body (string) on single-token endpoint (400 INVALID_JSON)
- AUTH-11: Non-object JSON body (array) on dual-token endpoint (400 INVALID_JSON)
- AUTH-12: Null task_token with valid escrow_token (400 INVALID_JWS)
- AUTH-13: Valid task_token with null escrow_token (400 INVALID_JWS)

**Bearer Token Validation (BEARER-01 to BEARER-13):**
These tests require tasks in specific statuses. Use the lifecycle helper fixtures.
- BEARER-01: Valid Bearer on sealed bid listing (200)
- BEARER-02: Valid Bearer on asset upload (201)
- BEARER-03: Missing Authorization header (400 INVALID_JWS)
- BEARER-04: Authorization without "Bearer " prefix (400 INVALID_JWS)
- BEARER-05: Empty Bearer token (400 INVALID_JWS)
- BEARER-06: Malformed Bearer token (400 INVALID_JWS)
- BEARER-07: Tampered Bearer token (403 FORBIDDEN)
- BEARER-08: Wrong action in Bearer JWS for sealed bids (400 INVALID_PAYLOAD)
- BEARER-09: Wrong action in Bearer JWS for asset upload (400 INVALID_PAYLOAD)
- BEARER-10: Payload task_id mismatch with URL (sealed bids) (400 INVALID_PAYLOAD)
- BEARER-11: Payload task_id mismatch with URL (asset upload) (400 INVALID_PAYLOAD)
- BEARER-12: Non-poster accessing sealed bids (403 FORBIDDEN)
- BEARER-13: Non-worker uploading asset (403 FORBIDDEN)

**Identity Service Dependency (IDEP-01 to IDEP-03):**
- IDEP-01: Identity timeout (502 IDENTITY_SERVICE_UNAVAILABLE)
- IDEP-02: Identity returns non-JSON 500 (502 IDENTITY_SERVICE_UNAVAILABLE)
- IDEP-03: Identity returns non-JSON 500 on Bearer endpoint (502 IDENTITY_SERVICE_UNAVAILABLE)

**Public Endpoints (PUB-01 to PUB-06):**
- PUB-01: GET /tasks requires no auth (200)
- PUB-02: GET /tasks/{id} requires no auth (200)
- PUB-03: GET /tasks/{id}/bids requires no auth when NOT in OPEN status (200)
- PUB-04: GET /tasks/{id}/assets requires no auth (200)
- PUB-05: GET /tasks/{id}/assets/{asset_id} requires no auth (200)
- PUB-06: GET /health requires no auth (200)

**Cross-Service Token Replay (REPLAY-01 to REPLAY-03):**
- REPLAY-01: Central Bank escrow_lock token rejected (400 INVALID_PAYLOAD)
- REPLAY-02: Court file_dispute token rejected (400 INVALID_PAYLOAD)
- REPLAY-03: Reputation submit_feedback token rejected (400 INVALID_PAYLOAD)

**Auth Cross-Cutting Security (SEC-AUTH-01 to SEC-AUTH-03):**
- SEC-AUTH-01: Error envelope consistency for auth errors
- SEC-AUTH-02: No internal error leakage in auth failures
- SEC-AUTH-03: JWS token reuse across services rejected (400 INVALID_PAYLOAD)

### Step 12: Stubs

`tests/integration/conftest.py` and `tests/performance/conftest.py` — docstring only.

---

## Test Conventions

Every test method must:
- Be marked with `@pytest.mark.unit`
- Have a docstring referencing the test ID (e.g., `"""TC-01: Create a valid task with escrow."""`)
- Follow the three-part structure: **Setup**, **Action**, **Expected**
- Use the conftest fixtures and helpers — do NOT make raw HTTP calls without using the provided helpers
- Assert exact status codes and error codes as specified in the test spec

For lifecycle tests that require time manipulation:
- Use `freezegun` or `time-machine` to advance time without actual sleeping
- Configure test deadlines to be very short (1-2 seconds)
- Alternatively, mock the `datetime.now()` or `time.time()` calls in the service layer

For concurrent race condition tests (TC-27, BID-13):
- Use `asyncio.gather()` or threading to send parallel requests
- Assert one succeeds and one fails with the expected conflict error

---

## Verification

```bash
just ci-quiet
```

Expected outcomes:
- `ruff check` — passes (syntactically valid)
- `ruff format --check` — passes (correctly formatted)
- `pytest` — all tests FAIL (service not implemented) or ERROR (import errors)
- Type checking may show errors for unresolvable imports — this is acceptable

The key requirement: the test files are **syntactically valid and CI-compliant** even though they fail at runtime. If `ruff`, `codespell`, and `semgrep` pass, the tests are ready for the implementation phase.
