# MP-06 / T-120 failing-test-first evidence

Date: 2026-10-15

Scope: temporary acceptance proof only. No repository production files, existing
tests, dependencies, tracker state, Git history, ordinary runtime data, or policy
choices Q-M1/Q-M2/Q-M3 were changed.

## Exact baseline and command

- Repository: `/Users/flo/Developer/github/agent-economy`
- Branch baseline observed: `refactor...origin/refactor [ahead 2]`, with extensive
  pre-existing unrelated worktree changes. The DB Gateway paths already modified by
  others were `services/db-gateway/justfile` and
  `services/db-gateway/tests/integration/conftest.py`; neither was touched here.
- Test file: `/tmp/agent-economy-mp06-tests/test_mp06_t120_red.py`
- Pytest marker config: `/tmp/agent-economy-mp06-tests/pytest.ini`
- Command:

  ```text
  cd /Users/flo/Developer/github/agent-economy/services/db-gateway
  UV_CACHE_DIR=/tmp/agent-economy-review-uv uv run pytest -c /tmp/agent-economy-mp06-tests/pytest.ini /tmp/agent-economy-mp06-tests/test_mp06_t120_red.py -q --tb=line
  ```

- Result: exit 1; **12 intended invariant failures, 6 passing controls**, 0 import,
  collection, fixture, or harness failures.
- Static test-file check:

  ```text
  UV_CACHE_DIR=/tmp/agent-economy-review-uv uv run ruff check /tmp/agent-economy-mp06-tests/test_mp06_t120_red.py
  All checks passed!
  ```

## Red failures and observed mutations

1. Wrong escrow payer, zero poster leg: no `payload_mismatch`; worker gained 100,
   escrow became `split`, one release transaction and event were committed.
2. Wrong escrow payer, non-zero poster leg: no `payload_mismatch`; unrelated poster
   gained 40, worker gained 60, escrow became `split`, two release transactions and
   one event were committed.
3. Mismatched issuance: balance 100 plus initial-credit amount 1 was accepted; the
   account, inconsistent transaction, and event committed.
4. Boolean money: Python `True` was accepted by the writer and persisted as integer 1
   in both balance and issuance transaction.
5. Non-integer money: 1.5 was accepted and persisted with SQLite storage class `real`
   in balance, amount, and balance-after.
6. Overflow: crediting 1 to 9223372036854775807 succeeded; balance and transaction
   balance-after changed from SQLite `integer` to imprecise `real`, and an event and
   transaction committed.
7. Unknown schema: a database containing only an unrelated table was accepted as
   ready without error.
8. Partial money schema: a database containing only a reduced `bank_accounts` table
   was accepted as ready without error.
9. Settled release plus exact lock replay: payer was debited another 100, a second
   escrow/lock transaction/event was created.
10. Settled split plus exact lock replay: payer was debited another 100, a second
    escrow/lock transaction/event was created.
11. Settled release plus changed-amount replay: payer was debited another 50, a
    second escrow/lock transaction/event was created.
12. Settled split plus changed-amount replay: payer was debited another 50, a second
    escrow/lock transaction/event was created.

Every monetary rejection test snapshots accounts with `typeof(balance)`, escrows with
`typeof(amount)`, transaction amounts/balance-after with SQLite types, and event count.
The red assertions compare the complete before/after snapshot, so the observed failure
also proves the current unwanted mutations rather than stopping at a missing error.

## Passing controls

The temporary suite passed six controls: matching issuance is auditable and stays
integer; the production HTTP validation helpers reject positive/non-negative boolean
and real values; a fresh database initializes the required identity and money tables.

The repository's existing controls also passed:

```text
UV_CACHE_DIR=/tmp/agent-economy-review-uv uv run pytest \
  tests/unit/routers/test_bank.py::TestAccountCreation::test_initial_credit_transaction_written \
  tests/unit/routers/test_bank.py::TestEscrowLock::test_idempotent_lock_returns_same_escrow_id \
  tests/unit/routers/test_bank.py::TestEscrowLock::test_duplicate_payer_task_different_amount_rejected \
  tests/unit/routers/test_bank.py::TestEscrowSplit::test_amount_mismatch \
  tests/unit/routers/test_bank.py::TestEscrowSplit::test_escrow_status_split \
  -q --tb=short
```

Result: exit 0; **5 passed**. This confirms active-lock idempotency, amount-sum checks,
normal split settlement, and matching initial-credit behavior remain valid controls.

## Authoritative implementation boundaries

- `services/db-gateway/src/db_gateway_service/services/bank_writer.py:32-66` stores
  `balance` and `initial_credit.amount` independently and commits without comparison.
- `bank_writer.py:98-139` performs unchecked `balance + amount`; subsequent `int()`
  conversion can conceal SQLite's promotion to REAL.
- `bank_writer.py:306-319` looks up only `status='locked'` escrows for replay.
- `bank_writer.py:411-429` loads escrow ID, amount, and status, omitting payer identity.
- `bank_writer.py:431-510` validates only split sum, then credits caller-supplied worker
  and poster accounts.
- `services/db-gateway/src/db_gateway_service/services/db_writer.py:72-129` skips schema
  work when schema SQL is absent, treats any application table as a complete database,
  and silently skips migrations for missing tables.
- `services/db-gateway/src/db_gateway_service/core/lifespan.py:36-48` treats a missing
  schema file as `schema_sql=None` and still constructs the writer/readiness state.
- `docs/specifications/schema.sql:31-74` uses INTEGER affinity without `typeof`,
  non-negative, or upper-bound checks and makes escrow uniqueness conditional on
  `status='locked'`.
- `services/db-gateway/src/db_gateway_service/routers/helpers.py:83-104` correctly
  rejects booleans and non-integers at the HTTP helper boundary; writer/schema
  invariants remain absent.

## Smallest coherent MP-06 implementation slice

Do not start constraint migration on a real database until T-121 first provides a
tested, consistent pre-migration backup/restore gate covering the database plus WAL
state and the required asset/key identity manifests. A missing, invalid, or
unrestorable backup must block migration.

After that gate exists, the smallest coherent implementation slice is:

1. Introduce a schema-version table and one ordered transactional migration framework;
   validate required tables, columns, indexes, foreign keys, integrity check, foreign
   key check, and supported version before readiness. Empty and known versions may
   converge; partial/unknown layouts must fail without mutation.
2. Audit/reconcile historical duplicate `(payer_account_id, task_id)` escrows, then
   replace the partial active-only index with lifetime uniqueness and make replay
   lookup status-independent.
3. Rebuild money tables with integer-storage, non-negative, and explicit configured
   bound constraints. Add writer validation and guarded arithmetic so credits,
   releases, and splits cannot cross the configured maximum.
4. Load escrow payer in the authoritative transaction and reject any caller-supplied
   poster mismatch before event or balance writes. Require initial issuance amount to
   equal starting balance and reject zero-balance issuance.
5. Re-run this red suite, the affected DB Gateway/Bank contract suites, migration and
   restore fixtures, then the required isolated service/root gates. T-120 and T-121
   remain open until their separate closure evidence exists.
