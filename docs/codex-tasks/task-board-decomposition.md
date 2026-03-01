Read these files FIRST before doing anything:
1. AGENTS.md — project conventions, architecture, testing rules
2. services/court/src/court_service/routers/validation.py — reference pattern for shared validation helpers
3. services/court/src/court_service/services/ruling_orchestrator.py — reference pattern for extracted service class
4. services/task-board/src/task_board_service/services/task_manager.py — the god class to decompose
5. services/task-board/src/task_board_service/core/state.py — AppState with __setattr__ magic
6. services/task-board/src/task_board_service/core/lifespan.py — dependency wiring
7. services/task-board/tests/unit/routers/conftest.py — test fixture infrastructure (mock injection pattern)
8. services/task-board/src/task_board_service/routers/tasks.py — has duplicated helpers to extract
9. services/task-board/src/task_board_service/routers/bids.py — has duplicated helpers to extract
10. services/task-board/src/task_board_service/routers/assets.py — has duplicated helper to extract

After reading all files, execute the 7 tasks below IN ORDER.

CRITICAL RULES:
- Working directory for ALL commands: services/task-board/
- Use `uv run` for all Python execution — never use raw python, python3, or pip install
- Do NOT modify any existing test files in tests/ — add new test files only
- All 99 existing tests must remain UNCHANGED and passing after every task
- TDD: write unit tests for extracted classes FIRST, verify they fail, then implement
- Run `just ci-quiet` after every task — this is the definitive validation
- One git commit per task
- No default parameter values anywhere
- All tests must be marked with @pytest.mark.unit

=== TASK 1: Extract Router Validation Helpers ===

Goal: Eliminate duplicated _parse_json_body, _extract_token, _extract_bearer_token across routers into shared module.

Files to create:
- src/task_board_service/routers/validation.py
- tests/unit/routers/test_validation.py

Files to modify:
- src/task_board_service/routers/tasks.py
- src/task_board_service/routers/bids.py
- src/task_board_service/routers/assets.py

Steps:
1. Write tests/unit/routers/test_validation.py with these test cases (all @pytest.mark.unit):
   parse_json_body:
   - test_parse_json_body_valid_object — returns dict
   - test_parse_json_body_invalid_json — raises ServiceError("INVALID_JSON", ..., 400)
   - test_parse_json_body_non_object — raises ServiceError("INVALID_JSON", ..., 400)
   - test_parse_json_body_empty — raises ServiceError("INVALID_JSON", ..., 400)
   extract_token:
   - test_extract_token_valid — returns token string
   - test_extract_token_missing_field — raises ServiceError("INVALID_JWS", ..., 400)
   - test_extract_token_null_value — raises ServiceError("INVALID_JWS", ..., 400)
   - test_extract_token_not_string — raises ServiceError("INVALID_JWS", ..., 400)
   - test_extract_token_empty_string — raises ServiceError("INVALID_JWS", ..., 400)
   extract_bearer_token (required=True):
   - test_extract_bearer_token_required_valid — returns token
   - test_extract_bearer_token_required_missing — raises ServiceError("INVALID_JWS", ..., 400)
   - test_extract_bearer_token_required_wrong_scheme — raises ServiceError("INVALID_JWS", ..., 400)
   - test_extract_bearer_token_required_empty_token — raises ServiceError("INVALID_JWS", ..., 400)
   extract_bearer_token (required=False):
   - test_extract_bearer_token_optional_valid — returns token
   - test_extract_bearer_token_optional_missing — returns None
   - test_extract_bearer_token_optional_wrong_scheme — raises ServiceError("INVALID_JWS", ..., 400)

2. Run: uv run pytest tests/unit/routers/test_validation.py -v (expect FAIL)

3. Create src/task_board_service/routers/validation.py with three functions:
   - parse_json_body(raw_body: bytes) -> dict[str, Any]
   - extract_token(data: dict[str, Any], field_name: str) -> str
   - extract_bearer_token(authorization: str | None, *, required: bool) -> str | None
   Copy exact logic from the existing router helpers. Match error messages exactly. The required param is keyword-only with NO default value.

4. Run: uv run pytest tests/unit/routers/test_validation.py -v (expect PASS)

5. Update routers:
   tasks.py: Remove _parse_json_body and _extract_token. Import from validation. Replace calls.
   bids.py: Remove _parse_json_body, _extract_token, _extract_bearer_token. Import from validation. Replace _extract_bearer_token(auth) with extract_bearer_token(auth, required=False).
   assets.py: Remove _extract_bearer_token. Import from validation. Replace with extract_bearer_token(auth, required=True).

6. Run: just ci-quiet
7. Commit: git commit -m "refactor(task-board): extract shared router validation helpers"

=== TASK 2: Extract EscrowCoordinator ===

Goal: Extract the four escrow methods from TaskManager into EscrowCoordinator.

Files to create:
- src/task_board_service/services/escrow_coordinator.py
- tests/unit/test_escrow_coordinator.py

Files to modify:
- src/task_board_service/services/task_manager.py
- src/task_board_service/core/state.py
- src/task_board_service/core/lifespan.py

Steps:
1. Write tests/unit/test_escrow_coordinator.py (all @pytest.mark.unit). Mock CentralBankClient with AsyncMock. Use real TaskStore with tmp_path for DB tests.
   Test cases:
   - test_release_escrow_success
   - test_release_escrow_service_error_propagates
   - test_release_escrow_generic_error_wraps — Exception becomes ServiceError("CENTRAL_BANK_UNAVAILABLE", ..., 502)
   - test_split_escrow_success
   - test_split_escrow_service_error_propagates
   - test_split_escrow_generic_error_wraps
   - test_try_release_escrow_success — on success, updates task escrow_pending=0
   - test_try_release_escrow_failure — on failure, sets escrow_pending=1, no exception raised
   - test_retry_pending_escrow_not_pending — escrow_pending=0, returned unchanged
   - test_retry_pending_escrow_expired_success — status=expired, escrow_pending=1 → releases to poster_id
   - test_retry_pending_escrow_approved_success — status=approved, escrow_pending=1 → releases to worker_id
   - test_retry_pending_escrow_failure_remains_pending — release fails → escrow_pending stays 1
   - test_retry_pending_escrow_other_status — status=disputed, escrow_pending=1 → unchanged
   Read task_manager.py lines 208-309 for exact logic and error codes.

2. Run: uv run pytest tests/unit/test_escrow_coordinator.py -v (expect FAIL)

3. Create src/task_board_service/services/escrow_coordinator.py
   Constructor: __init__(self, central_bank_client: CentralBankClient, store: TaskStore)
   Methods: release_escrow, split_escrow, try_release_escrow, retry_pending_escrow
   Copy exact logic from TaskManager lines 208-309.

4. Run: uv run pytest tests/unit/test_escrow_coordinator.py -v (expect PASS)

5. Update TaskManager:
   - Add constructor param: escrow_coordinator: EscrowCoordinator
   - Store as self._escrow_coordinator
   - Remove the four methods from TaskManager
   - Replace all calls: self._release_escrow( → self._escrow_coordinator.release_escrow(, etc.
   Call sites: _release_escrow in cancel_task, approve_task, record_ruling.
   _split_escrow in record_ruling. _try_release_escrow in _evaluate_deadline (3 calls).
   _retry_pending_escrow in _evaluate_deadline (1 call).

6. Update state.py:
   - Add field: escrow_coordinator: EscrowCoordinator | None = None
   - Add TYPE_CHECKING import for EscrowCoordinator
   - Extend __setattr__: when central_bank_client is set and not None, also update escrow_coordinator._central_bank_client if escrow_coordinator exists

7. Update lifespan.py:
   - After creating store and central_bank_client, create EscrowCoordinator(central_bank_client=central_bank_client, store=store)
   - Set state.escrow_coordinator = escrow_coordinator
   - Pass escrow_coordinator to TaskManager constructor

8. Run: just ci-quiet
9. Commit: git commit -m "refactor(task-board): extract EscrowCoordinator from TaskManager"

=== TASK 3: Extract TokenValidator ===

Goal: Extract JWS validation and escrow token decoding into TokenValidator.

Files to create:
- src/task_board_service/services/token_validator.py
- tests/unit/test_token_validator.py

Files to modify:
- src/task_board_service/services/task_manager.py
- src/task_board_service/core/state.py
- src/task_board_service/core/lifespan.py

Steps:
1. Write tests/unit/test_token_validator.py (all @pytest.mark.unit). Mock IdentityClient with AsyncMock. Use tests/helpers.py:make_jws_token to create test tokens.
   Test cases:
   validate_jws_token:
   - test_validate_jws_token_empty_token — raises ServiceError("INVALID_JWS")
   - test_validate_jws_token_wrong_format — token with <3 parts raises ServiceError("INVALID_JWS")
   - test_validate_jws_token_identity_unavailable — ConnectionError → ServiceError("IDENTITY_SERVICE_UNAVAILABLE", ..., 502)
   - test_validate_jws_token_identity_service_error — ServiceError propagates
   - test_validate_jws_token_forbidden_tampered — _tampered=True → ServiceError("FORBIDDEN", ..., 403)
   - test_validate_jws_token_missing_action — no "action" → ServiceError("INVALID_PAYLOAD")
   - test_validate_jws_token_wrong_action — action mismatch → ServiceError("INVALID_PAYLOAD")
   - test_validate_jws_token_valid_single_action — returns payload with _signer_id
   - test_validate_jws_token_valid_tuple_action — action matches one of tuple → success
   decode_escrow_token_payload:
   - test_decode_escrow_token_payload_valid — returns decoded dict
   - test_decode_escrow_token_payload_wrong_format — not 3 parts → ServiceError("INVALID_JWS")
   - test_decode_escrow_token_payload_invalid_base64 — ServiceError("INVALID_JWS")
   - test_decode_escrow_token_payload_invalid_json — ServiceError("INVALID_JWS")
   - test_decode_escrow_token_payload_not_object — ServiceError("INVALID_JWS")
   Read task_manager.py lines 416-570 for exact logic.

2. Run: uv run pytest tests/unit/test_token_validator.py -v (expect FAIL)

3. Create src/task_board_service/services/token_validator.py
   Move the module-level _decode_base64url_json function (task_manager.py lines 54-84) into this module.
   Constructor: __init__(self, identity_client: IdentityClient)
   Methods: validate_jws_token, decode_escrow_token_payload
   Copy exact logic from TaskManager lines 416-570.

4. Run: uv run pytest tests/unit/test_token_validator.py -v (expect PASS)

5. Update TaskManager:
   - Add constructor param: token_validator: TokenValidator
   - Store as self._token_validator
   - Remove _validate_jws_token and _decode_escrow_token_payload methods
   - Remove _decode_base64url_json function from task_manager.py
   - Replace calls: self._validate_jws_token( → self._token_validator.validate_jws_token(
   - For _decode_base64url_json usage in create_task: import from token_validator module
   - 10 call sites for _validate_jws_token: create_task, cancel_task, submit_bid, list_bids, accept_bid, upload_asset, submit_deliverable, approve_task, dispute_task, record_ruling
   - 1 call site for _decode_escrow_token_payload: create_task
   - Remove unused imports: base64, json (verify not used elsewhere first)

6. Update state.py:
   - Add field: token_validator: TokenValidator | None = None
   - Add TYPE_CHECKING import for TokenValidator
   - Extend __setattr__: when identity_client is set and not None, also update token_validator._identity_client if token_validator exists

7. Update lifespan.py:
   - Create TokenValidator(identity_client=identity_client)
   - Set state.token_validator = token_validator
   - Pass token_validator to TaskManager constructor

8. Run: just ci-quiet
9. Commit: git commit -m "refactor(task-board): extract TokenValidator from TaskManager"

=== TASK 4: Extract DeadlineEvaluator ===

Goal: Extract deadline evaluation and state transition logic.

Files to create:
- src/task_board_service/services/deadline_evaluator.py
- tests/unit/test_deadline_evaluator.py

Files to modify:
- src/task_board_service/services/task_manager.py
- src/task_board_service/core/lifespan.py

Steps:
1. Write tests/unit/test_deadline_evaluator.py (all @pytest.mark.unit). Use real TaskStore with tmp_path. Mock EscrowCoordinator with AsyncMock. Use freezegun to control datetime.now(UTC).
   Test cases:
   compute_deadline (static method):
   - test_compute_deadline_valid — adds seconds to ISO timestamp
   - test_compute_deadline_none_base — returns None
   evaluate_deadline:
   - test_evaluate_deadline_terminal_status_skipped — status=approved → returned unchanged
   - test_evaluate_deadline_open_no_bids_expired — open task past bidding deadline, 0 bids → status=expired
   - test_evaluate_deadline_open_with_bids_not_expired — open task past bidding deadline, has bids → stays open
   - test_evaluate_deadline_accepted_past_execution — past execution deadline → status=expired
   - test_evaluate_deadline_submitted_past_review — past review deadline → status=approved (auto-approve)
   - test_evaluate_deadline_not_past_deadline — before deadline → unchanged
   - test_evaluate_deadline_retries_pending_escrow — calls escrow_coordinator.retry_pending_escrow when escrow_pending=1
   evaluate_deadlines_batch:
   - test_evaluate_deadlines_batch_processes_all — evaluates all tasks in list
   Read task_manager.py lines 124-130 and lines 311-414 for exact logic.

2. Run: uv run pytest tests/unit/test_deadline_evaluator.py -v (expect FAIL)

3. Create src/task_board_service/services/deadline_evaluator.py
   Constructor: __init__(self, store: TaskStore, escrow_coordinator: EscrowCoordinator)
   Move _TERMINAL_STATUSES frozenset here.
   compute_deadline as @staticmethod.
   Methods: evaluate_deadline, evaluate_deadlines_batch
   Copy exact logic from TaskManager.

4. Run: uv run pytest tests/unit/test_deadline_evaluator.py -v (expect PASS)

5. Update TaskManager:
   - Add constructor param: deadline_evaluator: DeadlineEvaluator
   - Remove _compute_deadline, _evaluate_deadline, _evaluate_deadlines_batch
   - Remove _TERMINAL_STATUSES (moved to deadline_evaluator.py)
   - Replace calls:
     self._evaluate_deadline(task) → self._deadline_evaluator.evaluate_deadline(task)
     self._evaluate_deadlines_batch(tasks) → self._deadline_evaluator.evaluate_deadlines_batch(tasks)
     self._compute_deadline(...) → DeadlineEvaluator.compute_deadline(...) (used in _task_to_response, _task_to_summary, submit_bid)

6. Update lifespan.py:
   - Create DeadlineEvaluator(store=store, escrow_coordinator=escrow_coordinator)
   - Pass deadline_evaluator to TaskManager constructor
   Note: DeadlineEvaluator does NOT need AppState field or __setattr__ propagation — it delegates escrow via EscrowCoordinator which already gets mock propagation.

7. Run: just ci-quiet
8. Commit: git commit -m "refactor(task-board): extract DeadlineEvaluator from TaskManager"

=== TASK 5: Extract AssetManager ===

Goal: Extract asset upload/download/listing into AssetManager.

Files to create:
- src/task_board_service/services/asset_manager.py
- tests/unit/test_asset_manager.py

Files to modify:
- src/task_board_service/services/task_manager.py
- src/task_board_service/routers/assets.py
- src/task_board_service/core/state.py
- src/task_board_service/core/lifespan.py

Steps:
1. Write tests/unit/test_asset_manager.py (all @pytest.mark.unit). Use real TaskStore with tmp_path, real filesystem for asset storage, mock TokenValidator and DeadlineEvaluator.
   Test cases:
   upload_asset:
   - test_upload_asset_success — creates file on disk, returns metadata with SHA256 hash
   - test_upload_asset_file_too_large — ServiceError("FILE_TOO_LARGE", ..., 413)
   - test_upload_asset_too_many_files — ServiceError("TOO_MANY_ASSETS", ..., 409)
   - test_upload_asset_task_not_found — ServiceError("TASK_NOT_FOUND", ..., 404)
   - test_upload_asset_wrong_status — task not accepted → ServiceError("INVALID_STATUS")
   - test_upload_asset_wrong_worker — signer != worker → ServiceError("FORBIDDEN", ..., 403)
   list_assets:
   - test_list_assets_success — returns {"task_id": ..., "assets": [...]}
   - test_list_assets_task_not_found — ServiceError("TASK_NOT_FOUND", ..., 404)
   download_asset:
   - test_download_asset_success — returns (content, content_type, filename)
   - test_download_asset_task_not_found — ServiceError("TASK_NOT_FOUND", ..., 404)
   - test_download_asset_not_found — ServiceError("ASSET_NOT_FOUND", ..., 404)
   - test_download_asset_path_traversal — ServiceError("ASSET_NOT_FOUND", ..., 404)
   count_assets:
   - test_count_assets_delegates — returns store count
   Read task_manager.py lines 1235-1421 and 1859 for exact logic.

2. Run: uv run pytest tests/unit/test_asset_manager.py -v (expect FAIL)

3. Create src/task_board_service/services/asset_manager.py
   Constructor: __init__(self, store, token_validator, deadline_evaluator, asset_storage_path, max_file_size, max_files_per_task) — NO default values.
   Methods: upload_asset, list_assets, download_asset, get_asset (alias), count_assets
   Copy exact logic from TaskManager.

4. Run: uv run pytest tests/unit/test_asset_manager.py -v (expect PASS)

5. Update TaskManager:
   - Add constructor param: asset_manager: AssetManager
   - Remove upload_asset, download_asset, list_assets, get_asset, count_assets methods
   - Remove constructor params: asset_storage_path, max_file_size, max_files_per_task
   - Remove Path(self._asset_storage_path).mkdir(...) from __init__
   - In submit_deliverable: change self.count_assets(task_id) → self._asset_manager.count_assets(task_id)
   - Remove unused import: hashlib

6. Update assets.py router:
   - Change state.task_manager.upload_asset(...) → state.asset_manager.upload_asset(...)
   - Same for list_assets, download_asset
   - Access state.asset_manager

7. Update state.py:
   - Add field: asset_manager: AssetManager | None = None
   - Extend __setattr__ for mock propagation: when identity_client is set, also update asset_manager._token_validator._identity_client if both exist

8. Update lifespan.py:
   - Create AssetManager with all params, set state.asset_manager, pass to TaskManager

9. Run: just ci-quiet
10. Commit: git commit -m "refactor(task-board): extract AssetManager from TaskManager"

=== TASK 6: Remove AppState __setattr__ Magic ===

Goal: Replace __setattr__ magic with explicit mock propagation in test conftest.

Files to create:
- tests/unit/test_state.py

Files to modify:
- src/task_board_service/core/state.py
- src/task_board_service/services/task_manager.py
- tests/unit/routers/conftest.py (ADD lines only — do NOT remove or change existing lines)

Steps:
1. Write tests/unit/test_state.py (all @pytest.mark.unit):
   - test_app_state_init — creates with defaults
   - test_app_state_uptime — uptime_seconds > 0
   - test_app_state_started_at — returns ISO string
   - test_get_app_state_uninitialized — raises RuntimeError
   - test_init_app_state — returns valid AppState
   - test_reset_app_state — after reset, get_app_state raises RuntimeError

2. Remove __setattr__ from state.py (the entire method, currently lines 26-49). Keep all fields and properties.

3. Remove setter methods from TaskManager: set_identity_client, set_central_bank_client, set_platform_signer — they are dead code without __setattr__.

4. Update tests/unit/routers/conftest.py: AFTER the existing mock assignment lines (state.identity_client = mock_identity and state.central_bank_client = mock_bank), ADD these lines BEFORE yield test_app:
   # Propagate mocks to extracted services
   if state.task_manager is not None:
       state.task_manager._identity_client = mock_identity
       state.task_manager._central_bank_client = mock_bank
   if state.token_validator is not None:
       state.token_validator._identity_client = mock_identity
   if state.escrow_coordinator is not None:
       state.escrow_coordinator._central_bank_client = mock_bank

5. Run: just ci-quiet
6. Commit: git commit -m "refactor(task-board): remove AppState __setattr__ magic, wire mocks explicitly"

=== TASK 7: Final Cleanup ===

Goal: Clean up imports and exports, verify final state.

Steps:
1. Clean up TaskManager imports — remove any that are no longer used after all extractions (base64, hashlib, json). Verify each is truly unused before removing.

2. Update src/task_board_service/services/__init__.py to export all new classes:
   AssetManager, DeadlineEvaluator, EscrowCoordinator, TokenValidator, TaskManager

3. Verify line count: wc -l src/task_board_service/services/task_manager.py
   Expected: ~800-900 lines (down from 1,865)

4. Run: just ci-quiet
5. Commit: git commit -m "refactor(task-board): complete TaskManager decomposition cleanup"

=== END OF TASKS ===

After ALL tasks complete, verify:
- just ci-quiet passes (formatting, lint, types, security, all tests)
- All original tests pass unchanged
- TaskManager is ~850 lines focused on task lifecycle orchestration
- Each extracted class has its own unit tests

START NOW by reading AGENTS.md.
