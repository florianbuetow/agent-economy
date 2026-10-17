# MP-06 schema and migration implementation boundary

This is an implementation handoff for tasks 5.2–5.5, not completion evidence. The approved recovery
artifact engine precedes this work. Use disposable databases, keys, assets and runtime inventories
for every test. Never open or migrate the ordinary economy during development.

## Schema admission

Retain the existing `DbWriter(db_path, busy_timeout_ms, journal_mode, schema_sql)` constructor.
Classify before changing persistent journal mode; construct domain writers only after admission.
Close the connection on every failed classification, bootstrap or journal setup path.

Classification states are empty, exact supported legacy v0, exact current v1, partial, unknown and
future. An empty database needs valid schema SQL and transactional bootstrap. Existing exact v1
may omit schema SQL at the direct constructor seam. Lifespan still requires the configured schema
file to be present and readable. Existing v0 fails with `migration_required`; startup never migrates
it implicitly. Partial, unknown and future layouts fail before readiness.

Freeze the complete pre-MP-06 schema, including the three dispute/rebuttal columns on `board_tasks`
and `event_id` on both `bank_transactions` and `bank_escrow`. Cover both direct full-schema creation
and the historical base followed by those five additive operations. Column position differs between
these two constructions; compare semantic column properties by name, preserving PK ordinal and
index term order, rather than treating column IDs as schema identity. Validate all columns, types,
nullability/defaults, primary/foreign keys, indexes and predicates, table modes, checks, views and
triggers. Unexpected objects or missing constraints cannot masquerade as a supported version.

## Recovery compatibility and migration admission

Artifact audit contract v1 remains frozen, including `unknown_schema` / `unclassified`. Its captured
audit is recomputed exactly during verification; adding schema recognition there would invalidate
existing valid backups. Unknown audit versions fail closed. Add schema/money analysis as a separate
versioned migration-admission audit of the verified disposable clone.

For artifact contract v1, `migration_eligible=False` means that artifact verification performed no
migration admission. It is neither an authorization token nor a schema rejection. Preserve that field
and the original findings. Only the live admission context authorizes mutation; standalone result
objects are diagnostic. The context must be freshly entered by the actual migration command.

The admission context requires an exact backup ID, verified marker/files/audit, complete identity,
matching current/embedded inventory, and the existing exclusive maintenance lock with its recorded
device/inode. Pin source main/WAL files without following symlinks and compare their presence,
identity, size, mode and digest with the backup source evidence **before opening the source for
writes**. Classify and audit only a verified clone at this stage. Reject all relevant artifact findings;
the new classification can resolve the v1 audit's unclassified-schema limitation, but cannot discard
identity, asset, integrity, foreign-key or monetary findings.

Once SQLite is allowed to write, compare retained source inode/path, held lock identity and immutable
backup identity/digest, plus the transaction's logical invariants. Do not demand original WAL bytes
after `BEGIN IMMEDIATE`: SQLite may spill its own transaction before commit. Failed migration must
preserve logical v0 schema and rows and leave the immutable backup untouched; WAL bytes can differ.

## Transaction and monetary bounds

Implement one fixed, checksum-identified v0-to-v1 migration. Execute individual complete SQL
statements inside one explicit transaction; do not call `executescript()` inside it. Audit before
copying, use explicit column lists, and never cast, round, merge or delete invalid historical values.
Rebuild required money tables/constraints, replace active-only escrow uniqueness with lifetime
`(payer_account_id, task_id)` uniqueness, record the version and exact backup identity, validate
structure/foreign keys/integrity/conservation, then commit once. Report invalid rows and duplicate
escrows for reconciliation; do not invent an automatic historical repair policy.

Use the fixed SQLite signed-64-bit representation bound `(1 << 63) - 1`. No new configurable business
caps are required. Writer validation rejects booleans, non-integers, invalid signs and out-of-range
amounts before SQL. SQL constraints keep stored monetary values integer and bounded. Guard credits
using subtraction from the limit so SQLite cannot promote overflowing addition to REAL. Cover
release/split recipient overflow and combined same-account credits. Require exact initial issuance
and authoritative escrow payer even when the poster's split leg is zero. Exact settled lock replay
returns the existing escrow without a second debit; changed replay conflicts. Aggregate supply
reconciliation uses Python integers, not SQLite `SUM` over unbounded totals.

## Required evidence

- Preserve the existing T-120 12 intended failures and six controls.
- Add actual constructor failures for legacy/partial/unknown/future schemas, missing schema,
  journal-mode ordering and cleanup; bootstrap faults leave no application objects.
- Admission failures for absent/tampered/incomplete/identity-invalid backups, inventory mismatch,
  changed main/WAL or WAL presence, replaced/busy lock and unsupported schema occur before any
  writable source connection.
- Inject faults at each migration DDL/copy/index/version boundary; rollback and process restart
  retain logical v0 and all original rows/events. Success preserves IDs, money, assets and keys.
- Preserve artifact-v1 verification/restore compatibility after adding the classifier.
- Retain and extend authoritative monetary no-write and concurrency proofs, then run affected
  Gateway/Bank contracts, independent review and fresh isolated root CI.

Task 5.2 stays open until the actual migration entry point demonstrates backup rejection before a
writable source open. A reviewed artifact foundation permits developing that entry point; it does
not by itself close 5.2 or authorize migration of ordinary data.

## Concrete v0-to-v1 transaction handoff

The public migration entry accepts the inventory, exact backup root/ID, private work root and an
explicit busy timeout. It must enter a fresh backup-bound context itself. An internal helper may
consume that live context, but a detached diagnostic result is never accepted as authority. Align
explicit timeout validation with the existing Gateway contract (strict integer, excluding bool,
0 through 2147483647); zero means immediate lock failure rather than an implicit timeout choice.

The fixed rebuild set is `bank_accounts`, `bank_transactions`, `bank_escrow`, `board_tasks` and
`board_bids`, their indexes, plus new `schema_metadata`. A structural delta test must prove that
v1 changes no other table definition. Drop children first: bids, tasks, transactions, escrow,
accounts. Recreate and copy parent-first: accounts, transactions, escrow, tasks, bids. Use exact
canonical v1 CREATE definitions and index statements, not renamed temporary table definitions.
With foreign keys explicitly disabled before BEGIN and verified afterward, untouched foreign-key
references resolve again when the canonical names are recreated.

Read rows only from the verified disposable clone and insert with explicit rowid plus named column
lists and bound parameters. Preserve values and SQLite storage types without casting or normalization.
Copy by names so both approved legacy column orders work. Do not use ATTACH or executescript for
this transaction. Preserve all other tables, event IDs and `sqlite_sequence`; compare canonical row
counts/digests before and after. Reclassify and audit the live source transaction against the verified
clone before the first DDL, so an intervening logical change cannot be overwritten unnoticed.

The approved metadata schema currently has five fields: singleton, version, migrated-from version,
backup ID and backup manifest SHA-256. Migration writes `(1, 1, 0, exact_id, exact_manifest_hash)`.
Do not silently add another metadata column based on an advisory suggestion. A plan checksum can be
recorded in external migration evidence without changing this already reviewed schema contract.

Validate exact v1 structure, metadata provenance, every preserved row identity/type, foreign keys,
SQLite integrity, financial linkages and conservation inside the transaction. Revalidate the held
source/workspace/backup identities before one commit. After closing the writer, require a fresh
read-only validation before reporting success.

Any commit exception is an uncertain outcome, even if a later read observes v1. Return nonzero,
preserve the primary error and report the observed state (`committed_v1_observed`,
`not_applied_v0_observed`, `inconsistent_state_observed`, or `unreadable_state`) under the still-held
maintenance boundary. Never automatically retry or claim rollback after a possibly completed commit.
Before-commit fault injection must leave exact logical v0 and original rows; raw WAL bytes may differ.
The immutable backup remains unchanged throughout. Retain diagnostic clones/evidence in the private
work root; the source runtime is never automatically restarted or provisioned by migration.

## Connection disposal contract

The migration writer owns an exact base `sqlite3.Connection` with explicit factory, disabled
statement caching, disabled same-thread checking, and `autocommit=True`. It controls the transaction
with explicit SQL `BEGIN IMMEDIATE`, `COMMIT` and `ROLLBACK`; the DB-API commit/rollback methods are
no-ops in this mode. Verify no transaction exists before configuration, that BEGIN starts one,
and that a successful COMMIT ends it. This avoids a dependency on legacy-mode typing suppressions
or interpreter defaults. It does not
return or retain cursors, blobs, or backup handles. Following a failed dynamic rollback or close,
direct base-descriptor cleanup must dispose of the connection before source observation, evidence
publication, or maintenance-lock release. Keep the original failure primary and expose stable safe
cleanup codes (`rollback_method_failed`, `close_method_failed`, `native_close_completed`) in evidence
and error details; private exception notes alone are insufficient for CLI diagnosis. The native
rollback fallback also executes SQL ROLLBACK only while a transaction remains active. Any exception
from COMMIT or closing its cursor retains the uncertain-commit classification without retry.

Fault fixtures that retain the live connection call the real configured connection factory before
installing rollback/close faults. The commit-then-error fixture executes SQL COMMIT, preserving its
original persisted-versus-unapplied observation assertions. These fixture changes follow the
explicit transaction mode; they do not remove fault points or weaken rollback, disposal or
uncertainty assertions.

This is a bounded CPython SQLite connection contract, not an assertion that arbitrary native
destructor failure can be recovered in-process. SQLite's `close_v2` can defer disposal when handles
escape, so exact ownership and no escaped SQLite objects are requirements. Supporting arbitrary
native close failure would require a separately designed fail-stop or process boundary. References:
[CPython 3.13.5 connection cleanup](https://github.com/python/cpython/blob/v3.13.5/Modules/_sqlite/connection.c#L2937-L2980),
[public close implementation](https://github.com/python/cpython/blob/v3.13.5/Modules/_sqlite/connection.c#L3250-L3297),
and [SQLite close and zombie-connection contract](https://www.sqlite.org/c3ref/close.html).
The explicit transaction mode follows
[Python's autocommit contract](https://docs.python.org/3.13/library/sqlite3.html#sqlite3.Connection.autocommit)
and [SQLite's transaction commands](https://www.sqlite.org/lang_transaction.html).
