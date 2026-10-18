# MP-07 Gateway-backed test storage

## Scope and replacement plan

The dictionary-backed Task Board fake cannot represent native operation ownership,
immutable replay, authoritative receipts, or task contract projections. Add an
isolated test adapter before replacing any existing fake or consumer. Keep the
existing files untouched until this adapter receives independent review.

1. Add failing acceptance tests for the adapter's missing interface, then capture
   the collected failure output before implementation.
2. Reuse `TaskDbClient` for public storage methods, payload mapping, response
   validation, and error handling. Replace only its synchronous request boundary
   with a small dispatcher to production Gateway readers and writers.
3. Delegate native commands to `TaskOperationWriter` with an explicitly injected
   clock. Do not copy lifecycle rules, calculate accepted terms, fabricate receipts,
   invent identities, or turn arbitrary row dictionaries into valid history.
4. Verify native claims, evidence-dependent finalization, immutable replay,
   malformed identities, strict projections, raw-write rejection, explicit clock
   behavior, independent instances, and close/reopen persistence.
5. Integrate router and other fixtures separately, preserving observable assertions
   and documenting superseded legacy contracts. This file does not authorize
   closing MP-07 or claiming the existing fixture suites pass.

## Resource and authority contract

The caller provides an absolute, owned SQLite file path, busy timeout, WAL journal
mode, schema SQL (current v3 for creation or `None` for reopening), clock callable,
Gateway caller KID, and `BoardAuthority`. A temporary-directory fixture owns the
database file's lifetime. Adapter close releases its own connection only; it never
deletes the database, sidecars, or another instance's resources. Literal in-memory
SQLite is unsuitable because production `DbWriter` requires effective WAL mode.

The adapter is a storage fixture, not an authenticated HTTP server. Its caller KID
is explicitly supplied test authority, not proof of JWS verification. Existing live
Gateway tests retain responsibility for authentication, routing, and transport.
The dispatcher rejects unsupported paths rather than contacting any network.

Fixtures receive explicit access to the underlying production writer and reader.
They register identities, create accounts, lock escrow, and persist Bank/Court
effects using those APIs. Native test histories advance through real commands.
Malformed-history tests must label direct SQL as corruption; adapter methods never
silently manufacture missing supporting rows.

## Validation boundary

The focused new tests cover the adapter and production storage boundaries. They do
not replace live HTTP, multi-process, migration, or root CI proof. The original fake,
router helpers, deadline fixtures, and legacy settlement tests remain separate
integration work.

## Focused evidence

The first collected run, before creating the adapter module, produced two test
failures and twelve fixture assertion errors for the missing adapter interface:
`/private/tmp/agent-economy-mp07-store-adapter-red.log`. This is one missing-interface
proof, not fourteen independently reached behavioral failures.

The first implementation run exposed invalid operation IDs in the new fixture;
these were corrected to explicit canonical op-UUID values. Another new assertion
incorrectly attempted to finalize a pure bid operation. The existing native
contract requires `409 operation_not_finalizable`, while exact claim replay returns
the completed operation. Those setup/expectation failures are not product red proof.

A separate injected constructor failure then demonstrated a real resource leak:
`/private/tmp/agent-economy-mp07-store-adapter-cleanup-red.log` reports one failing
connection-closure assertion and sixteen passing controls. Construction now closes
the newly opened writer if dependent initialization raises; database files remain
under the temporary-directory fixture's ownership.

Final focused command, run from `services/task-board`:

```sh
UV_CACHE_DIR=/tmp/agent-economy-review-uv UV_FROZEN=1 uv run --no-sync pytest tests/unit/test_mp07_gateway_store_parity.py -q --tb=short --color=no
```

Result: **17 passed**, exit 0, recorded in
`/private/tmp/agent-economy-mp07-store-adapter-final2-green.log`.
Ruff check and format check pass for both new Python files. Mypy passes with local
source roots supplied through `MYPYPATH`; Pyright reports zero errors and warnings.
Their final logs are `/private/tmp/agent-economy-mp07-store-adapter-mypy-final.log`
and `/private/tmp/agent-economy-mp07-store-adapter-pyright-final.log`.

Verified source SHA-256 values:

- `tests/fakes/gateway_task_store.py`:
  `bccea17de4cee5bd17900747a9eff687850a1b8c25bfbc895cce24b5d0f43d48`
- `tests/unit/test_mp07_gateway_store_parity.py`:
  `bfa7eef7ca880466103ac70d7c33600fb608f0c3fec36977e2c2239de074802b`

Root CI and existing fixture reconciliation remain outstanding. No existing fake,
consumer, production module, dependency, or tracker status changed in this slice.
