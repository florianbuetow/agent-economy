# Migrate Error Codes from SCREAMING_CASE to snake_case

> Read AGENTS.md FIRST for project conventions.
> Use `uv run` for all Python execution. Never use raw python or pip install.
> After EACH task, run the service's `just test` from the service directory.
> After ALL tasks in a service, run `just ci-quiet` from the service directory.
> Commit after each task with a descriptive message.

## Background

The endpoint error handling specification (`docs/specifications/endpoint-error-handling.md`) requires all error codes to be **lowercase snake_case**:

> A stable, machine-readable error code (snake_case). Clients switch on this value.

Currently, all services use **SCREAMING_SNAKE_CASE** (e.g., `"ACCOUNT_NOT_FOUND"` instead of `"account_not_found"`). This must be fixed across all source code AND all test files simultaneously so tests continue to pass.

**This is a mechanical find-and-replace task.** For each error code string, convert it from UPPERCASE to lowercase. Do NOT change logic, status codes, messages, or any other behavior.

## Important Rules

1. **Source and tests must be updated together** — if you change an error code in source, you MUST also change the matching assertion in the test files.
2. **Do NOT change error messages** — only the `error` field (the machine-readable code).
3. **Do NOT change status codes** — only the string value of the error code.
4. **Do NOT change any logic** — this is purely a string casing change.
5. **Comments and docstrings** — update error codes in comments/docstrings too if they reference the old SCREAMING_CASE.
6. **Acceptance test shell scripts** — these also assert on error codes and must be updated.
7. **Use `uv run` for all Python execution** — never raw python or pip install.

## Important Files to Read First

1. `AGENTS.md` — project conventions
2. `docs/specifications/endpoint-error-handling.md` — the specification requiring snake_case

## Error Code Mapping

Every SCREAMING_CASE error code must be converted to its lowercase equivalent. The conversion is mechanical: `ACCOUNT_NOT_FOUND` → `account_not_found`.

---

## Task 1: Identity Service (source + tests)

**Files to modify:**

Source files:
- `services/identity/src/identity_service/routers/agents.py` — `METHOD_NOT_ALLOWED` (3x), `AGENT_NOT_FOUND` (1x)
- `services/identity/src/identity_service/services/agent_registry.py` — `AGENT_NOT_FOUND` (2x)
- `services/identity/src/identity_service/core/exceptions.py` — `METHOD_NOT_ALLOWED`, `HTTP_ERROR`
- `services/identity/src/identity_service/core/middleware.py` — `UNSUPPORTED_MEDIA_TYPE`, `PAYLOAD_TOO_LARGE`

Test files (update assertions to match new lowercase codes):
- `services/identity/tests/unit/routers/test_agents.py`
- `services/identity/tests/unit/routers/test_health.py`
- `services/identity/tests/unit/routers/test_verify_jws.py`

Acceptance test scripts:
- `services/identity/tests/acceptance/test-reg-07.sh` — `MISSING_FIELD`
- `services/identity/tests/acceptance/test-reg-09.sh` — `INVALID_FIELD_TYPE`
- `services/identity/tests/acceptance/test-ver-08.sh` — `INVALID_SIGNATURE_LENGTH`
- `services/identity/tests/acceptance/test-ver-18.sh` — `AGENT_NOT_FOUND`

**Steps:**

1. For each source file, find every SCREAMING_CASE error code string and replace with its lowercase equivalent.
2. For each test file, find every assertion on error codes (`["error"] == "..."` or `.error == "..."`) and update to match.
3. For each acceptance test script, find every `assert_json_eq ".error" "..."` and update the code to lowercase.
4. Run: `cd services/identity && just test`
5. Commit: `refactor(identity): migrate error codes to snake_case`

---

## Task 2: Central Bank Service (source + tests)

**Files to modify:**

Source files:
- `services/central-bank/src/central_bank_service/routers/accounts.py` — `INVALID_JWS` (4x), `INVALID_PAYLOAD` (6x), `INVALID_AMOUNT` (1x), `ACCOUNT_NOT_FOUND` (1x), `METHOD_NOT_ALLOWED` (1x)
- `services/central-bank/src/central_bank_service/routers/escrow.py` — `INVALID_JWS` (6x), `INVALID_PAYLOAD` (5x), `INVALID_AMOUNT` (1x), `METHOD_NOT_ALLOWED` (1x)
- `services/central-bank/src/central_bank_service/routers/helpers.py` — check for any UPPERCASE codes
- `services/central-bank/src/central_bank_service/services/ledger.py` — `ACCOUNT_NOT_FOUND` (3x), `ESCROW_NOT_FOUND` (2x)
- `services/central-bank/src/central_bank_service/core/exceptions.py` — `METHOD_NOT_ALLOWED`, `HTTP_ERROR`
- `services/central-bank/src/central_bank_service/core/middleware.py` — `UNSUPPORTED_MEDIA_TYPE`, `PAYLOAD_TOO_LARGE`

Test files:
- `services/central-bank/tests/unit/routers/test_accounts.py`
- `services/central-bank/tests/unit/routers/test_escrow.py`
- `services/central-bank/tests/unit/routers/test_health.py`
- `services/central-bank/tests/unit/routers/test_review_fixes.py`
- `services/central-bank/tests/unit/routers/test_self_service_account.py`
- `services/central-bank/tests/unit/test_ledger_safety.py`

Acceptance test scripts:
- `services/central-bank/tests/acceptance/test-acct-03.sh` — `ACCOUNT_EXISTS`
- `services/central-bank/tests/acceptance/test-acct-07.sh` — `FORBIDDEN`
- `services/central-bank/tests/acceptance/test-escrow-03.sh` — `FORBIDDEN`

**Steps:**

1. For each source file, replace all SCREAMING_CASE error code strings with lowercase.
2. For each test file, update all assertions on error codes to match.
3. For each acceptance test script, update `assert_json_eq ".error"` values.
4. Run: `cd services/central-bank && just test`
5. Commit: `refactor(central-bank): migrate error codes to snake_case`

---

## Task 3: Task Board Service (source + tests)

This service has the most error codes. Be thorough.

**Files to modify:**

Source files:
- `services/task-board/src/task_board_service/routers/tasks.py` — `INVALID_PAYLOAD` (4x), `METHOD_NOT_ALLOWED` (5x)
- `services/task-board/src/task_board_service/routers/bids.py` — `METHOD_NOT_ALLOWED` (1x)
- `services/task-board/src/task_board_service/routers/validation.py` — check for any UPPERCASE codes
- `services/task-board/src/task_board_service/services/token_validator.py` — `INVALID_JWS` (4x), `FORBIDDEN` (1x)
- `services/task-board/src/task_board_service/services/task_manager.py` — `TASK_NOT_FOUND` (8x), `INVALID_PAYLOAD` (10x), `FORBIDDEN` (8x), `INVALID_REWARD` (1x), `SELF_BID` (1x), `BID_NOT_FOUND` (1x), `CENTRAL_BANK_UNAVAILABLE` (comment), `INSUFFICIENT_FUNDS` (comment)
- `services/task-board/src/task_board_service/services/asset_manager.py` — `INVALID_PAYLOAD` (1x), `FORBIDDEN` (1x), `TASK_NOT_FOUND` (3x), `ASSET_NOT_FOUND` (3x)
- `services/task-board/src/task_board_service/services/escrow_coordinator.py` — update comments referencing UPPERCASE codes
- `services/task-board/src/task_board_service/core/exceptions.py` — `METHOD_NOT_ALLOWED`, `HTTP_ERROR`
- `services/task-board/src/task_board_service/core/middleware.py` — `UNSUPPORTED_MEDIA_TYPE` (2x), `PAYLOAD_TOO_LARGE`
- `services/task-board/src/task_board_service/clients/central_bank_client.py` — check for any UPPERCASE codes

Test files:
- `services/task-board/tests/unit/routers/test_tasks.py` — ~32 assertions
- `services/task-board/tests/unit/routers/test_bids.py` — ~20 assertions
- `services/task-board/tests/unit/routers/test_assets.py` — ~9 assertions
- `services/task-board/tests/unit/routers/test_submission.py` — ~12 assertions
- `services/task-board/tests/unit/routers/test_disputes.py` — ~16 assertions
- `services/task-board/tests/unit/routers/test_lifecycle.py` — ~9 assertions
- `services/task-board/tests/unit/routers/test_health.py` — 1 assertion
- `services/task-board/tests/unit/routers/test_auth.py` — ~31 assertions
- `services/task-board/tests/unit/routers/test_security.py` — ~11 assertions
- `services/task-board/tests/unit/routers/test_validation.py` — ~11 assertions
- `services/task-board/tests/unit/test_token_validator.py` — ~10 assertions
- `services/task-board/tests/unit/test_escrow_coordinator.py` — ~4 assertions
- `services/task-board/tests/unit/test_asset_manager.py` — ~9 assertions
- `services/task-board/tests/unit/clients/test_central_bank_client_errors.py` — ~8 assertions

**Steps:**

1. For each source file, replace all SCREAMING_CASE error code strings with lowercase.
2. For each test file, update all assertions on error codes to match.
3. Also update any `side_effect=Exception("INSUFFICIENT_FUNDS")` patterns to use lowercase.
4. Also update any `side_effect=ServiceError("...", ...)` patterns in tests.
5. Run: `cd services/task-board && just test`
6. Commit: `refactor(task-board): migrate error codes to snake_case`

---

## Task 4: Reputation Service (source + tests)

**Files to modify:**

Source files:
- `services/reputation/src/reputation_service/routers/feedback.py` — all UPPERCASE codes (search the file)
- `services/reputation/src/reputation_service/app.py` — `METHOD_NOT_ALLOWED`, `VALIDATION_ERROR`
- `services/reputation/src/reputation_service/core/middleware.py` — `BAD_REQUEST`, `UNSUPPORTED_MEDIA_TYPE`, `PAYLOAD_TOO_LARGE`
- `services/reputation/src/reputation_service/services/` — any `.py` files with UPPERCASE codes

Test files:
- `services/reputation/tests/unit/routers/test_feedback.py`
- `services/reputation/tests/unit/routers/test_feedback_auth.py` — ~33 assertions
- `services/reputation/tests/unit/routers/test_identity_error_remapping.py` — ~9 assertions
- `services/reputation/tests/unit/test_feedback_service.py` — ~22 assertions
- `services/reputation/tests/unit/test_persistence.py` — 3 assertions
- `services/reputation/tests/helpers.py` — 1 reference
- `services/reputation/tests/integration/test_endpoints.py` — ~9 assertions

Acceptance test scripts:
- `services/reputation/tests/acceptance/test-sec-01.sh` — `MISSING_FIELD`, `INVALID_RATING`, `INVALID_CATEGORY`, `SELF_FEEDBACK`, `FEEDBACK_NOT_FOUND`, `INVALID_JSON`, `UNSUPPORTED_MEDIA_TYPE`, `METHOD_NOT_ALLOWED`, `FEEDBACK_EXISTS`, `INVALID_FIELD_TYPE`, `COMMENT_TOO_LONG`, `PAYLOAD_TOO_LARGE`
- `services/reputation/tests/acceptance/test-http-01.sh` — `METHOD_NOT_ALLOWED`
- `services/reputation/tests/acceptance/test-vis-04.sh` — `FEEDBACK_NOT_FOUND`
- `services/reputation/tests/acceptance/test-fb-10.sh` — `COMMENT_TOO_LONG`
- `services/reputation/tests/acceptance/test-fb-13.sh` — `INVALID_CATEGORY`
- `services/reputation/tests/acceptance/test-fb-14.sh` — `MISSING_FIELD` (5x)
- `services/reputation/tests/acceptance/test-fb-15.sh` — `MISSING_FIELD`
- `services/reputation/tests/acceptance/test-fb-18.sh` — `UNSUPPORTED_MEDIA_TYPE`
- `services/reputation/tests/acceptance/test-fb-25.sh` — `MISSING_FIELD` (3x)
- `services/reputation/tests/acceptance/test-read-04.sh` — `FEEDBACK_NOT_FOUND`

**Steps:**

1. For each source file, replace all SCREAMING_CASE error code strings with lowercase.
2. For each test file, update all assertions to match.
3. For each acceptance test script, update all `assert_json_eq ".error"` values.
4. Run: `cd services/reputation && just test`
5. Commit: `refactor(reputation): migrate error codes to snake_case`

---

## Task 5: Court Service (source + tests)

**Files to modify:**

Source files:
- `services/court/src/court_service/routers/disputes.py` — `TASK_NOT_FOUND`, `DISPUTE_NOT_FOUND` (2x), `METHOD_NOT_ALLOWED` (2x)
- `services/court/src/court_service/routers/validation.py` — `INVALID_JSON` (2x), `INVALID_JWS` (3x), `FORBIDDEN` (1x), `INVALID_PAYLOAD` (2x)
- `services/court/src/court_service/services/dispute_service.py` — `DISPUTE_NOT_FOUND`, `DISPUTE_ALREADY_EXISTS`, `INVALID_DISPUTE_STATUS`, `REBUTTAL_ALREADY_SUBMITTED`
- `services/court/src/court_service/services/ruling_orchestrator.py` — `DISPUTE_NOT_FOUND`, `DISPUTE_ALREADY_RULED`, `DISPUTE_NOT_READY`, `JUDGE_UNAVAILABLE` (3x), `CENTRAL_BANK_UNAVAILABLE`, `REPUTATION_SERVICE_UNAVAILABLE`, `TASK_BOARD_UNAVAILABLE`
- `services/court/src/court_service/core/exceptions.py` — `METHOD_NOT_ALLOWED`, `HTTP_ERROR`
- `services/court/src/court_service/core/middleware.py` — `UNSUPPORTED_MEDIA_TYPE`, `PAYLOAD_TOO_LARGE`

Test files:
- `services/court/tests/unit/routers/test_disputes.py` — ~59 assertions
- `services/court/tests/unit/test_ruling_orchestrator.py` — ~2 assertions
- `services/court/tests/unit/test_validation.py` — ~9 assertions

**Steps:**

1. For each source file, replace all SCREAMING_CASE error code strings with lowercase.
2. For each test file, update all assertions to match.
3. Run: `cd services/court && just test`
4. Commit: `refactor(court): migrate error codes to snake_case`

---

## Task 6: Observatory Service (source + tests)

**Files to modify:**

Source files:
- `services/observatory/src/observatory_service/routers/events.py` — `INVALID_PARAMETER` (3x)
- `services/observatory/src/observatory_service/routers/quarterly.py` — check for any UPPERCASE codes
- `services/observatory/src/observatory_service/core/exceptions.py` — `METHOD_NOT_ALLOWED`, `HTTP_ERROR`

Test files:
- `services/observatory/tests/unit/routers/test_quarterly.py` — `INVALID_QUARTER`, `NO_DATA`
- `services/observatory/tests/unit/routers/test_metrics.py` — 2 assertions
- `services/observatory/tests/unit/routers/test_agents.py` — 2 assertions
- `services/observatory/tests/unit/routers/test_events.py` — 2 assertions
- `services/observatory/tests/unit/routers/test_tasks.py` — 1 assertion
- `services/observatory/tests/unit/test_edge_cases.py` — `NO_DATA`

**Steps:**

1. For each source file, replace all SCREAMING_CASE error code strings with lowercase.
2. For each test file, update all assertions to match.
3. Run: `cd services/observatory && just test`
4. Commit: `refactor(observatory): migrate error codes to snake_case`

---

## Task 7: DB Gateway Service (source + tests)

**Files to modify:**

Source files:
- `services/db-gateway/src/db_gateway_service/services/db_writer.py` — `ACCOUNT_NOT_FOUND` (4x), `ESCROW_NOT_FOUND` (1x), `TASK_NOT_FOUND` (1x)
- `services/db-gateway/src/db_gateway_service/routers/health.py` — `DATABASE_UNAVAILABLE`
- `services/db-gateway/src/db_gateway_service/core/exceptions.py` — `METHOD_NOT_ALLOWED`, `HTTP_ERROR`
- `services/db-gateway/src/db_gateway_service/core/middleware.py` — `UNSUPPORTED_MEDIA_TYPE`, `PAYLOAD_TOO_LARGE`

Test files:
- `services/db-gateway/tests/unit/routers/test_bank.py` — ~31 assertions
- `services/db-gateway/tests/unit/routers/test_board.py` — ~22 assertions
- `services/db-gateway/tests/unit/routers/test_court.py` — ~17 assertions
- `services/db-gateway/tests/unit/routers/test_identity.py` — ~9 assertions
- `services/db-gateway/tests/unit/routers/test_reputation.py` — `FEEDBACK_EXISTS`, `FOREIGN_KEY_VIOLATION`, `MISSING_FIELD`
- `services/db-gateway/tests/unit/test_cross_cutting.py` — ~5 assertions
- `services/db-gateway/tests/unit/test_db_writer.py` — ~3 assertions

**Steps:**

1. For each source file, replace all SCREAMING_CASE error code strings with lowercase.
2. For each test file, update all assertions to match.
3. Run: `cd services/db-gateway && just test`
4. Commit: `refactor(db-gateway): migrate error codes to snake_case`

---

## Task 8: UI Service (source only)

**Files to modify:**

Source files:
- `services/ui/src/ui_service/core/exceptions.py` — `METHOD_NOT_ALLOWED`, `HTTP_ERROR`

Test files:
- `services/ui/tests/unit/routers/test_health.py` — 1 assertion

**Steps:**

1. Replace the two UPPERCASE codes in exceptions.py with lowercase.
2. Update the test assertion if it references an UPPERCASE code.
3. Run: `cd services/ui && just test`
4. Commit: `refactor(ui): migrate error codes to snake_case`

---

## Task 9: Final Verification

After all tasks are complete:

1. Run `just ci-all-quiet` from the project root directory.
2. If any failures, fix them in the affected service and re-run.
3. This is the definitive gate — ALL tests, linting, type checking, and formatting must pass.

---

## Summary

Execute tasks 1 through 9 in order. Each task is scoped to one service.
After each task, run the service's `just test` to verify tests pass.
After Task 8 is complete, run `just ci-all-quiet` from the project root as the final gate.
Do NOT run `just ci-all-quiet` — I will run it on my machine. Just run `just test` per service.
