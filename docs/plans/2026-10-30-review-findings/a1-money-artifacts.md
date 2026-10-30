# A1 audit: money path and artifacts (G01-G06)

Audited at HEAD 802fe37 (branch `refactor`). Read-only, code-based. Paths are relative to repo root.
Abbreviations: TB = services/task-board/src/task_board_service, GW = services/db-gateway/src/db_gateway_service.

## Summary table

| G-id | Status | One line |
| --- | --- | --- |
| G01 | MISSING | Bid amount is stored as `accepted_bid_amount` but never used. `accepted_reward` is set to the posted budget, and approve, timeout and ruling settle the whole escrow. Court split is floor(budget*pct/100). No accepted-price tests exist. |
| G02 | MISSING | Fresh DBs boot V5 only. `artifacts_v6.sql` and `publication_v6.sql` are loaded only by test fixtures. `ArtifactWriter` and `PrepublicationWriter` are never instantiated. |
| G03 | MISSING | No public route or SDK method for poster inputs. Upload requires status `accepted` and signer == worker. |
| G04 | PARTIAL | In-process cleanup of staged and published bytes is solid. Nothing sweeps `.staging/` or unreferenced `live/` blobs after process death, and a lost metadata response leaves an orphan blob. |
| G05 | MISSING at runtime | Gateway has a V2 `freeze_submission_manifest` path, but Task Board pins `protocol_version` 1. V1 submit only checks that one worker asset exists, so no manifest or hashes are frozen. |
| G06 | PARTIAL | Public nonterminal download and terminal cutoff (410) work. The only terminal bypass is the single roster operator. There is no Court grant and no archive processing. Court reads evidence through the public route only while the task is `disputed`. |

## Evidence per gap

### G01 accepted-bid pricing: MISSING
- **Acceptance writes the budget as the price.** In `GW/services/task_operation_writer.py:712-718`, `accepted_reward` is set to `task["reward"]` and `accepted_bid_amount` to `bid_amount`. Nothing else reads `accepted_bid_amount` for payment.
- **Approve and auto-approve release the full escrow to the worker.** In `GW/services/task_operation_writer.py:980-1039`, `expected_reward` is `accepted_reward`, which equals the budget. The command is an `escrow_release` of the whole escrow, and `expected_escrow.amount` is that same value.
- **Court split is computed on the budget.** `record_ruling` with pct 1..99 issues `escrow_split` (`:994-1003`). Both the Bank and the replay check compute `escrow.amount * pct // 100` (`GW/services/bank_writer.py:525` and `task_operation_writer.py:1579-1583`). The result is floor(budget*pct/100), not floor(accepted_price*pct/100).
- **Bank has no primitive for a partial settlement.** The only operations are lock, full release and percentage split (`services/central-bank/src/central_bank_service/routers/escrow.py:104,165,248` and `bank_writer.py:306,425,499`). Paying price P and refunding budget-P needs a new two-leg "settle(worker_amount)" operation, or a split by exact amounts.
- **The Board passes the budget along as the price.** `TB/services/task_operations.py:78` and `TB/services/deadline_evaluator.py:175,193` forward `accepted_reward` as `reward`.
- **The fix cannot just change `accepted_reward`.** The same field is used as the expected escrow amount in receipt matching (`task_operation_writer.py:1037,1497`). Setting it to the bid amount would make every settlement fail verification. The fix needs separate fields: the escrow or budget amount, and the price.
- **Coverage is missing, and some tests pin the old rule.** No test exercises B01 (budget 100, bid 60). Tests assert `accepted_reward == 100` equals the posted reward, for example `services/task-board/tests/unit/test_asset_manager.py:42` and `test_mp07_gateway_store_parity.py:266`. These need explicit updates.
- **Integer conservation and replay safety hold today.** They hold for the current full-escrow semantics. Receipt matching checks the exact leg amounts, transactions and escrow status at `:1470-1650`.

### G02 schema activation: MISSING
- **Bootstrap creates V5 and admits only V5.** `GW/services/db_writer.py:281-305,342-345` and `GW/schema.py:49-74,94` know states up to `CURRENT_V5`, and V6 is not among them.
- **V6 SQL has no runtime consumer.** `GW/schema_resources/artifacts_v6.sql` and `publication_v6.sql` are referenced only in `services/db-gateway/tests/fakes/mp09_*` and `tests/unit/test_mp09_input_publication.py`.
- **The V6 helpers degrade to V5.** `inspect_acceptance_binding` returns None when the tables are absent (`GW/services/artifact_acceptance.py:262-263`). `reject_governed_protocol_v1_submission` returns early on the same condition (`artifact_submission.py:47-48`).
- **The writers are never constructed.** `ArtifactWriter` (`artifact_writer.py:42`) and `PrepublicationWriter` (`prepublication_writer.py:63`) have no caller. Gateway routes expose only V5 `/assets` (`GW/routers/board.py:415,558,574,614`).
- **The handover doc is accurate here.**

### G03 poster inputs: MISSING
- **Only the assigned worker can upload, and only after acceptance.** Upload eligibility requires `status == "accepted"` and `signer_id == worker_id` (`TB/services/asset_manager.py:224-236`).
- **No route or SDK method exists.** There is no Task Board route for posting intents or inputs (`TB/routers/*.py`). The SDK has `upload_asset`, `list_assets` and `download_asset` only (`libs/service-auth/src/service_auth/mixins/task_board.py:310,425,431`).
- **The handover doc is accurate here.**

### G04 upload recovery: PARTIAL
- **Implemented:** streaming within bounds, staging followed by link to `live/`, fsyncs, and in-process cleanup when an error occurs before metadata is written (`TB/services/asset_manager.py:417-541`). Metadata rejection deletes the published bytes when the row is absent (`:280-315`). Admission is atomic through the Gateway's `expected_lifecycle_version` and `max_files_per_task` (`TB/services/task_db_client.py:794-823`).
- **Missing: startup or periodic recovery.** No code scans `.staging/` or reconciles `live/` blobs against `board_assets`. `TB/core/lifespan.py` starts only the deadline sweep. A crash between `os.link` and metadata insert, or mid-stream, leaves bytes forever.
- **Missing: handling of ambiguous outcomes.** A transport failure on `insert_asset` with an unreadable follow-up re-raises (`:296-298`) and keeps the blob. A client retry mints a new UUID, so the upload has no idempotency key.

### G05 immutable output manifest: MISSING at runtime
- **The Gateway code exists but is unreachable.** `GW/services/task_operation_writer.py:758-760` freezes a manifest only when `protocol_version == 2`. Task Board's contract pins version 1 (`TB/task_operation_contract.py:106,120`, `TB/schemas.py:82`, `TB/services/task_review.py:73`).
- **V1 submit checks only existence.** It requires at least one asset from the worker (`:762-769`) and records no membership or hashes.
- **Membership is fixed only implicitly.** It is frozen in effect because upload is blocked once status leaves `accepted`. There is no manifest record, and inputs are not bound to the accepted terms.
- **The handover doc is accurate.** The helpers are component-tested only.

### G06 download policy: PARTIAL
- **Public nonterminal download works.** `TB/routers/assets.py:92-125` calls `asset_manager._download_asset`, which serves bytes after a size and hash integrity check (`TB/services/asset_manager.py:587-675`).
- **Terminal cutoff works.** `approved`, `ruled`, `cancelled` and `expired` return 410 (`:600-608`). Coverage exists in `services/task-board/tests/unit/test_t197_terminal_asset_authorization.py`.
- **The only terminal bypass is the operator.** A bearer token from the single roster `operator` can read terminal bytes (`TB/services/asset_manager.py:567-585`, `TB/operator_authority.py`). There is no Court grant.
- **Court has no grant path.** It reads evidence anonymously through the public routes (`services/court/src/court_service/services/ruling_orchestrator.py:68-92`). That works only because `disputed` is nonterminal. The reader also truncates and decodes with `errors="replace"`, which belongs to G07.
- **Archive is missing.** No runtime code archives anything. `board_artifact_archive_attempts` exists only in the unused V6 SQL.
- **Nothing purges bytes.** No code deletes files.

## Logic bugs found

The money path has no concrete double-payout or lost-escrow bug under the current fixed-reward semantics.
- Cancel and expiry release the full escrow to the poster.
- Approve, auto-approve and ruling are guarded by status plus `lifecycle_version` compare-and-set (`task_operation_writer.py:700-720`) and exact receipt matching.
- One latent trap and one minor issue follow.

1. **Price and escrow amount share one field.** In `GW/services/task_operation_writer.py:980-1039,1497,1579`, `accepted_reward` is both "what the worker is owed" and "what the escrow must contain". If G01 is fixed only by storing `bid_amount` in `accepted_reward` at line 716, every settlement fails `_financial_effect_matches`, because escrow.amount is the budget. Funds then sit in pending recovery.
2. **Upload retries can duplicate assets.** In `TB/services/asset_manager.py:442,496-500`, every upload attempt mints a fresh UUID with no client idempotency key. When the HTTP response is lost after the Gateway commits, the retry stores a second copy that counts toward `max_files_per_task`. Under V1 submit, both copies become submitted deliverables. This is a minor data issue, not a money issue.
3. **Naming nit, not a bug.** `escrow_matches` at `task_operation_writer.py:1494` is True when the escrow does not match. It is used correctly.

## Recommended work items (ordered)

1. **Split escrow amount from accepted price, and add a Bank "settle" operation.** It credits the worker an exact amount and refunds the rest to the poster atomically, with a receipt. Done when B01 passes through public APIs. Budget 100 with bid 60 pays 60 and refunds 40 on approve and on timeout. A 50% ruling on price 61 pays 30 and refunds 70. An exact retry adds no ledger rows.
2. **Bootstrap V6 on fresh DBs.** Load V5 plus the artifact and publication SQL as the single current schema, and drop the expectation that only V5 is admitted. Done when a fresh Gateway starts and classifies as current with all `board_artifact_*` tables present.
3. **Wire ArtifactWriter into Gateway routes and Task Board streaming upload.** Use reservation, then attempt, then ready, so the bytes follow the reservation. Done when a wrong actor or closed task is rejected and a metadata rejection leaves no `ready` row or blob.
4. **Add poster input flow.** Add a posting intent, input upload and a frozen input manifest before publish, using PrepublicationWriter, Task Board routes and SDK methods. Done when B02's first half passes: a bidder downloads the exact input bytes after publish.
5. **Switch submit to protocol 2 with a frozen output manifest.** Change it in the Task Board contract, the SDK `submit_deliverable` and the agents. Done when a two-file submission's manifest hashes match the downloaded bytes through the SDK, and the upload set cannot change afterwards.
6. **Add upload recovery at startup or on a periodic sweep.** Reconcile reserved or attempted uploads and stray `.staging/` and `live/` files after a crash. Done when a killed-mid-upload restart test leaves no orphan and a retry completes once.
7. **Add a Court evidence grant and the archive sweep.** Court reads the frozen manifest by grant, independent of public access. A background job archives terminal artifacts older than 3 days without purging them. Done when B04 passes: Court reads after terminal, public gets 410, and archived bytes survive a restart.
