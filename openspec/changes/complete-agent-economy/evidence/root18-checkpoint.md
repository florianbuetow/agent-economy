# Root18 runtime migration fixture baseline

Root command: `UV_CACHE_DIR=/tmp/agent-economy-review-uv UV_FROZEN=1 just ci-quiet`.
Parent session86220 is TERMINAL1. It passed structure, all libraries, all seven service components,
all per-service integration, agents and tools. The cross/runtime phase ended3 failed/200 passed;
the browser and final live-agent E2E phases were not reached. Root15 remains the last full exit0.

All three failures occur before the intended migration/crash assertions:

- test_migration_live_workload.py::test_migration_cli_preserves_live_receipts_and_allows_signed_continuation
- test_migration_process_crash.py::test_process_death_preserves_legacy_and_signed_workflow_can_resume[after_drop:board_bids]
- the same process-death case at after_copy:bank_transactions

The shared `_logical_rows` helper selects rowid from every current table. Schema v2 now includes
WITHOUT ROWID receipt/observation tables, so `_install_legacy_fixture` fails with
sqlite3.OperationalError: no such column: rowid. This is fixture drift, not product-red proof.
Independent diagnosis also found the next stale assumption: restarting after only v0-to-v1 would
violate the intentionally v2-only startup contract. The Gateway test spec records the narrow
reconciliation before edits: retain frozen legacy row/crash proof, then use a new verified backup
and the explicit v1-to-v2 command before current-service continuation.

Validation evidence is archived as root-ci-18.log/.exit and before/after/hash-comparison/cleanup/
summary JSON. All931 source/config hashes are identical. The one root-owned isolated stack had
9 process groups; metadata shows drained/no cleanup errors and independent signal0 probes found
all9 absent. No ordinary data, keys or processes were used or signaled.

The successful phase counts include libraries177/104/311, per-service integration7/25/2/12/1/6/246,
agents390 passed/3 credential skips/94 E2E deselections, tools144. Credential skips do not prove real
providers; final94 E2E and298 UI browser tests remain required in the next complete root gate.
The documented two-file reconciliation is implemented: three focused live cases pass in76.77s
(`/private/tmp/agent-economy-mp04-runtime-migration-reconciled.log`) and independent review APPROVED
with no findings. An intermediate helper backup-ID mismatch was a fixture error and is not new
product-red evidence. All original migration/crash assertions remain, with a separate verified
v1 backup and explicit v1-to-v2 migration before continuation. No production code changed.

MP-04 and T-175/T-176 remain open. A separate UI fixture readiness issue is tracked by T-177/T-178.
MP-07's draft stays outside the repository until this baseline is restored.
