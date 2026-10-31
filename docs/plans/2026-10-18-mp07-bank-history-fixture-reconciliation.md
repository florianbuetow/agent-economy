# MP-07 Bank History Fixture Reconciliation

## Scope

Reconcile the Central Bank historical-observation integration fixture with the
current Gateway v3 startup boundary. The fixture must retain its explicit,
backup-bound v1-to-v2 receipt migration and all original historical receipt,
settlement, conflict, identity, and no-mutation assertions.

The previous fixture returned the verified v2 database directly to the current
Gateway `DbWriter`. Startup now rejects exact v2 with `migration_required`, so the
fixture needs a second explicit backup and verified v2-to-v3 task-history migration.
The shared Central Bank integration conftest remains unchanged.

## Baseline

The original file is preserved at
`/private/tmp/mp07-bank-history-reconcile/test_mp04_historical_observations.py.baseline`
with SHA-256
`3149a18097f0f3eb30ca360783555edf234fb9a36236ecde6b6bff86fbd6add5`.

Focused pre-change evidence collected all eight cases and failed during the first
fixture setup at `DbWriter._admit_existing_schema`: the deliberately migrated v2
database requires explicit migration before current startup.

## Reconciled Contract

- Create and verify the original v1 backup, run v1-to-v2, and assert exact v2.
- Snapshot the migrated financial tables and verify receipt ownership.
- Create and verify a distinct immutable v2 backup.
- Run explicit v2-to-v3 and assert its committed v3 result and provenance.
- Verify exact v3 classification, unchanged financial history, clean receipt and
  task-contract audits, SQLite integrity and foreign keys, no native task operations,
  and unchanged backup bytes.
- Return the v3 database to the existing service fixture, leaving every public Bank
  historical-observation assertion intact.

## Verification

The owned file passed after reconciliation:

```text
PYTHONPATH="/Users/flo/Developer/github/agent-economy:/Users/flo/Developer/github/agent-economy/services/central-bank/tests" \
uv run --directory services/central-bank pytest \
  /Users/flo/Developer/github/agent-economy/services/central-bank/tests/integration/test_mp04_historical_observations.py \
  -q -x
```

Fresh result: `8 passed in 5.53s`.

After the Bank conftest owner completed and froze that source, the full integration
suite passed with the same environment and
`pytest /Users/flo/Developer/github/agent-economy/services/central-bank/tests/integration -q -x`:
`25 passed in 5.99s`.

Ruff lint, Ruff formatting, Pyright, and `git diff --check` pass for the owned test
file. No genuine product defect appeared. The reconciled source SHA-256 is
`4c1a5c43169a4ff061706f892b05201115960a4a932866c31db81d2754f48d65`.
