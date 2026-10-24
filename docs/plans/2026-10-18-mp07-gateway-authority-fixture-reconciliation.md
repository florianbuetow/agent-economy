# MP-07 Gateway authority fixture reconciliation

Date: 2026-10-18

Scope was limited to three real DB Gateway startup fixtures:

- `services/identity/tests/integration/conftest.py`
- `services/identity/tests/integration/test_concurrent_registration_live.py`
- `services/central-bank/tests/integration/conftest.py`

No product, shared-library, Git, ordinary data, or ordinary log path was
modified. Existing concurrent edits, including temporary per-test log paths,
were preserved.

## Preserved originals

The three pre-edit files and their hashes are stored at:

`/private/tmp/mp07-gateway-authority-fixtures-originals-20261018`

The SHA-256 of its `SHA256SUMS` manifest is:

`9c0012d84943fa0434b5dd5acd64b5a04dbe1d139da02a9c63887c4c0779065d`

## Failing positive startup evidence

The positive in-process Identity registration and Central Bank account tests
both errored during fixture setup. `db_gateway_service.config.Settings`
rejected the generated config because required `board_authority` was absent.
No Identity or Bank domain assertion was reached.

The live Identity concurrency test independently failed after 15 seconds
because its DB Gateway subprocess exited at startup with the same missing
`board_authority` validation error. Its socket, process, and duplicate-key
race assertions were not reached.

## Reconciliation

Each fixture now:

- generates a distinct temporary `platform` keypair in its existing owned key
  directory;
- points `board_authority.platform_public_key_path` at that generated
  `platform.pub`;
- explicitly configures
  `deadline_actor_id: system-task-board-deadline`.

The platform authority key is not aliased to the Identity or Central Bank
Gateway transport key. Existing caller capabilities, authentication,
negative configuration behavior, ports, temporary logs, process cleanup, and
application assertions remain unchanged.

## Verification

Previously failing positive paths:

- Identity in-process Gateway startup and persisted registration: **1 passed**
- Central Bank in-process Gateway startup and persisted account: **1 passed**
- Identity live DB Gateway + Identity duplicate-key race: **1 passed**

Full affected suites:

- Identity integration: **7 passed in 2.31s**
- Central Bank integration: **17 passed, 8 errors in 4.42s**

The eight Central Bank errors are a separate historical-schema fixture gap in
`test_mp04_historical_observations.py`. That file explicitly performs only a
v1-to-v2 migration and asserts schema version 2. Current `DbWriter` startup
correctly rejects v2 with `migration_required`; therefore those tests never
reach their historical observation assertions. Repair requires an explicit,
verified v2-to-v3 fixture migration in that separately owned file. No silent
migration was added to the shared Central Bank conftest.

Ruff formatting and lint pass for all three changed files. Root CI remains the
parent lane's final gate after the historical fixture is reconciled.
