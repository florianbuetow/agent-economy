# MP-04 requirement-to-proof audit

Status: item-level implementation, independent reviews and root19 verification are complete.
Root19 exited0 with932 unchanged hashes and19 root-owned stack groups absent. Independent final
verification returned PASS; T-116/T-117 and tasks6.2–6.4 are closed. This does not close the full
Core/Vision goal.

## Scope and prerequisite evidence

MP-04 follows MP-02 authority and MP-06 schema/financial invariants, already closed with their
own root checkpoints. It supplies financial receipts and replay semantics to later MP-05/07/08
work. It does not prove a winning task transition, posting recovery, background effect recovery,
Court adjudication or real-provider behavior.

The concrete contracts are:

- docs/plans/2026-10-16-mp04-receipt-contract.md
- docs/plans/2026-10-16-mp04-schema-v2.md
- docs/plans/2026-10-17-mp04-historical-observations.md

## Requirement mapping

| Obligation | Implemented behavior and evidence |
| --- | --- |
| T-116: demonstrate the replay gap before repair | checkpoint.md retains the independently reproduced eight-case public Bank/Gateway baseline: real committed release/split response loss, changed intent, lock replay and missing authoritative receipt. Later SDK/history/integrity reds are recorded in their chronological checkpoints. |
| 6.2: stable identity and payload-bound durable results | Gateway test_mp04_request_contract.py, test_mp04_native_receipts.py and routers/test_mp04_operation_identity.py cover canonical requests, global financial IDs, exact replay, changed-content and natural-key conflicts, winning identity and atomic financial/receipt/event effects. The native receipt, ordered logical legs and truthful current projection have separate meanings. |
| 6.2: persisted ownership and historical cutover | test_mp04_schema_v2.py, test_mp04_receipt_audit.py, test_mp04_startup.py, test_mp04_migration.py and test_mp04_migration_cli.py cover schema/readiness, receipt ownership and explicit backup-bound v1-to-v2 migration while frozen v1 remains unchanged. test_mp04_legacy_observations.py covers attested historical facts and rejection of unowned/overlapping/corrupt effects without fabricated caller identities. |
| 6.3: real Bank/SDK/client boundaries | Bank integration test_mp04_replay_baseline.py and test_mp04_historical_observations.py exercise public Bank, the production adapter and signed Gateway/SQLite paths. Bank/SDK/service-clients typed response tests reject malformed native/history success. Funding/demo consumers reject historical observations as proof of a new funding command. |
| 6.3: opposing commands and conservation | test_mp04_public_settlement_race.py adds a public Bank release-versus-split barrier over independent Gateway writers, requiring one real winner, exact winner-referencing conflict, immutable replay, balances and one set of persisted receipt/ledger/event facts. Gateway and memory suites also prove opposing writes; public response-loss cases cover0/50/100 percent splits with zero logical legs retained and no zero-valued ledger row/account requirement. |
| 6.4: in-memory parity and consumer integration | Bank test_mp04_memory_receipts.py, shared-client identity/receipt/history tests, agent financial-intent/funding tests and demo consumer tests cover the same accepted contract. Existing valid callers/fixtures were reconciled without discarding authorization, insufficient-funds, payout or no-duplicate-effect assertions. |
| 6.4: documentation and migration continuation | Bank and Gateway API specifications now replace obsolete flat financial examples and financial caller constraints with actual native/history responses, errors and source references. The two runtime migration tests preserve exact v0-to-v1/crash proof, then use a fresh verified backup and separate v1-to-v2 command before current-service continuation. All these changes have independent approval. |

Gateway unit names above are under services/db-gateway/tests/unit. Bank test names are under
services/central-bank/tests/unit or integration as specified. SDK and client coverage lives under
agents/tests/unit, libs/service-auth/tests/unit and libs/service-clients/tests/unit; demo coverage
is under tools/tests/unit/demo_replay.

## Current bounded validation

- Public Bank race/history/replay: parent22 passed; the full Bank integration phase in root18
  passed25 cases. This closes the earlier literal public-Bank race proof gap at item level.
- Root cross-service tests:26 passed independently before the later root attempts.
- Reconciled live-agent E2E:94 passed, with independent review of all seven edited files and
  parent proof that all10 owned stack groups were absent.
- Runtime migration continuation/crash cases:3 passed after fixture reconciliation and independent
  review. Their temporary raw manifests were purged before parent archival; do not present them
  as retained evidence. Root19 independently reran all3 successfully; its fresh8 manifests/crash
  records are now archived under ../root-ci-19-migration-*.json. The separate cleanup proof verifies
  all56 owned groups absent with drained/error-free metadata. This new evidence is not a
  reconstruction of the purged focused-run manifests.
- Root18 passed all library/service components, per-service integration, agents and tools. Its
  runtime phase found the three fixture errors now repaired;200 other cases passed. It is not a
  passing root gate. See ../root18-checkpoint.md.

Native/history source approvals and exact local log names are in checkpoint.md,
sdk-validation-2026-10-17.md and history-checkpoint-2026-10-17.md. The SDK checkpoint's source
hashes explicitly identify that earlier revision; the final root snapshot will identify current
source. Fresh root19 verification is recorded separately in ../root19-checkpoint.md.

Nineteen terminal logs/observations are also preserved byte-for-byte in completion-logs/.
Its index.json records their original paths, timestamps, categories and SHA-256 hashes. This is
archival of existing evidence, not a new test execution or retrospective failing-first claim.
The original eight-case baseline and initial schema/identity logs remain beside checkpoint.md.

## Evidence limitations retained

- Invalid historical fixtures and duplicate-log capture errors are not product-red proof.
- The incidental native audit-log transaction-ID correction lacks a valid focused pre-fix red.
  It cannot be used as T-116 evidence; the core replay/identity behavior has separate genuine reds.
- Historical posting guards and the added public Bank race are supplemental green boundary proofs,
  not reconstructed historical reds.
- A missing test filename, a temporary test import failure and fixture backup-ID mismatch were
  harness/command errors; none establish a product defect.
- Identity verification is mocked in the public historical Bank harness; Gateway signing and SQLite
  persistence are real. Actual signing/authentication and live-stack boundaries have separate tests.
- Existing credential-dependent skips do not establish real LLM/Court operation. That remains an
  explicit later requirement, with unresolved credentials/budget and policy decisions preserved.

## Verified final gate for this package

Root19 exited0 on the final932-file source/config snapshot, including quarterly navigation and
bounded UI fixture readiness repairs. Every required phase completed:203 cross/runtime,298 UI
browser and94 live-agent cases passed. Before/after hashes are identical. Both root-owned stacks'
19 groups and the separately observed56 migration-test groups are absent with clean metadata.
See ../root19-checkpoint.md and its exact raw artifacts. Independent final verification checked
the actual logs, source hashes, cleanup, red/green proof and acceptance mapping and returned PASS.
The synchronized checklist/backlog close only MP-04 and the two adjacent baseline repairs.
