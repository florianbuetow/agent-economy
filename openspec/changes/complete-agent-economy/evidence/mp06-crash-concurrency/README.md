# MP-06 final crash and concurrency proofs

The root14 closure audit identified two explicit acceptance gaps despite a passing combined gate:
authoritative maximum-bound lock/settlement concurrency and an actual migration process death.
These tests extend evidence for already implemented, red-proven behavior. They are not presented
as new pre-implementation failures.

## Authoritative concurrency

`services/db-gateway/tests/unit/test_mp06_money_concurrency.py` adds three cases using barriers and
two independent DbWriter connections. Competing maximum-sized locks yield one winner and one
`insufficient_funds` loser. Parameterized maximum-sized releases/splits yield one winner and one
`escrow_already_resolved` loser. Exact balances, INTEGER storage, escrow transition, ledger leg count,
single event and financial conservation are asserted. Existing plus new money tests:49 passed.

## Actual process death

`tests/runtime/test_migration_process_crash.py` creates real signed public-API work in a disposable
seven-service runtime, stops it, and converts the owned database to the frozen legacy fixture while
preserving all logical rows/types/rowids/sequences. A separate `uv run` child enters actual migration
and pauses at either the after-DROP or after-COPY fault barrier. The parent waits through a selector,
verifies the child PID/group/session against its owned subprocess, then sends SIGKILL to that child.
The uv supervisor reaps it; the test verifies the entire owned group is absent. No schedule sleeps
or ordinary processes are used.

Each case then proves exact logical v0, integrity/FK/money reconciliation, released maintenance
exclusion and an unchanged verified original backup. Because a crash can change raw WAL bytes,
recovery explicitly captures a new backup/source binding before running the CLI again. This is
not an implicit migration retry. The migrated stack restarts with the same identities, reads its
submitted asset and completes signed approval/payout. Original keys/assets and backup stay unchanged.

Parent host run 8037 terminated with exit 0: two cases passed in 21.39s. Four before/after runtime
manifests and two kill records are archived here. `cleanup.json` records 36 distinct process groups,
all drained and absent in a fresh host check. Coverage verification and independent code review
found no remaining gap. Root15 subsequently passed with 888 unchanged source/config hashes,
including both crash cases. T-121 and the remaining MP-06 tasks are closed; see
`../root15-checkpoint.md` for the combined gate and remaining project scope.
