# MP-07 agent E2E native contract reconciliation

Date: 2026-10-18

Scope was limited to `agents/tests/e2e/`, excluding
`test_unattended_economy.py` and `test_autonomous_acceptance.py`. No product,
service, shared-library, ordinary runtime data, or ordinary service log was
modified.

## Preserved baseline

The pre-edit owned files were copied byte for byte to:

`/private/tmp/mp07-agent-e2e-native-originals-20261018`

The directory contains `SHA256SUMS` for 24 original files. The SHA-256 of that
manifest is:

`2a67781d06ac14779a5d60cc5fe3020ea784b2026f5ebba2d3b95e6256afe5aa`

The first two baseline launcher attempts did not execute tests. The first used
the repository root as a `uv` project and failed to import `httpx`; the second
used paths relative to `agents/` although the isolated phase command runs from
the repository root. Both were harness invocation errors.

The valid baseline command was:

```text
uv run --no-sync --project agents scripts/isolated_test_runtime.py \
  --artifacts-root /private/tmp/mp07-agent-e2e-baseline \
  --provision \
  --services identity,db-gateway,central-bank,task-board,reputation,court,ui \
  -- uv run --no-sync --project agents pytest agents/tests/e2e -m e2e \
  --ignore=agents/tests/e2e/test_unattended_economy.py \
  --ignore=agents/tests/e2e/test_autonomous_acceptance.py -q --tb=short
```

Runtime:
`/private/tmp/mp07-agent-e2e-baseline/runtime-36ae4e4f140c47938d9bd9c27e37f05f-wx89uwrr`

Result: **40 passed, 49 failed, 3 errors**. Every failure/error stopped at
test construction or request validation: missing SDK
`acceptance_window_seconds`, or missing UI proxy acceptance-window input. No
native command behavior was reached.

## Reconciliation

The owned E2E tests now:

- supply an explicit positive `acceptance_window_seconds` on every task
  publication, including UI proxy payloads;
- allocate an explicit caller-owned `op-UUID` for every native Task Board
  command;
- read and validate `lifecycle_version` from the live task or prior typed ACK
  before each command;
- access `TaskBoardCommandResponse.status_code` and `.body` directly, with an
  explicit transport-status assertion and preserved domain assertions;
- include otherwise-valid native operation identity and lifecycle version in
  forged raw-JWS cases so the request reaches the intended authentication,
  authorization, duplicate, asset, or lifecycle check;
- provide native operation metadata in UI proxy accept/approve/dispute bodies.

`agents/tests/e2e/native_task_contract.py` contains only two narrow primitives:
fresh operation-ID allocation and validated lifecycle-version extraction. It
does not patch the SDK, map response bodies, hide exceptions, retry, or skip.

Modified owned test files:

- `test_agent_startup.py`
- `test_asset_store.py`
- `test_cancellation_escrow_refund.py`
- `test_court_rulings.py`
- `test_dispute_court_handoff.py`
- `test_disputes.py`
- `test_economic_cycle.py`
- `test_economic_invariants.py`
- `test_mp03_token_admission.py`
- `test_mp12_proxy_bids.py`
- `test_platform_auth.py`
- `test_proxy_routes.py`
- `test_reputation.py`
- `test_reputation_sealed.py`
- `test_task_board.py`
- `test_task_board_auth.py`
- `test_wp06_autonomous_ruling.py`

## Verification

Focused live command used the same isolated seven-service stack and ran core
lifecycle, authorization, asset, token-admission, and UI proxy tests.

Runtime:
`/private/tmp/mp07-agent-e2e-focused-1/runtime-74ee03f16703449b9d7d67f4d998f3d8-pkbycebi`

Result: **44 passed, 8 failed**. The eight failures reached live product
behavior; none was an SDK constructor, missing-field, response-mapping, or test
setup failure.

Final full owned command:

```text
UV_CACHE_DIR=/private/tmp/mp07-agent-e2e-uv-cache \
uv run --no-sync --project agents scripts/isolated_test_runtime.py \
  --artifacts-root /private/tmp/mp07-agent-e2e-final \
  --provision \
  --services identity,db-gateway,central-bank,task-board,reputation,court,ui \
  -- uv run --no-sync --project agents pytest agents/tests/e2e -m e2e \
  --ignore=agents/tests/e2e/test_unattended_economy.py \
  --ignore=agents/tests/e2e/test_autonomous_acceptance.py -q --tb=short
```

Runtime:
`/private/tmp/mp07-agent-e2e-final/runtime-bdfeff0ebffd4505a2ad853beb7111f1-idlunv3i`

Result: **73 passed, 19 failed, 0 errors**.

## Actionable product defects

### Financial effect succeeds but operation finalization conflicts

Cancel, approval, automatic expiry/approval, and ruling paths call the Central
Bank successfully, then DB Gateway rejects Task Board finalization with
`409 effect_evidence_conflict`. The original status, payout, refund, deadline,
reputation, and ruling assertions therefore remain failing.

Representative final-run evidence is in
`logs/2026-10-18.log` under the final runtime:

- lines 110-114: escrow split succeeds, operation finalization returns 409,
  Court surfaces `502 task_board_unavailable`;
- lines 239-240: approval release is followed by a finalization conflict;
- lines 626-627: cancellation release is followed by a finalization conflict;
- lines 683, 695, 800 and 855-865: deadline-driven operation claims/finalization
  conflict, so open/accepted/submitted tasks do not reach their required
  terminal states.

This single boundary defect accounts for 18 of the 19 final failures.

### Duplicate-bid error leaks the gateway vocabulary

`test_duplicate_bid_rejected` reaches the duplicate check and receives
`409 bid_exists`. The Task Board API and test specifications require
`409 bid_already_exists`; `bid_exists` is the DB Gateway error vocabulary.
The existing assertion remains unchanged.

## Stop condition

The bounded E2E test-contract reconciliation is complete. The suite has no
remaining native SDK/request-shape incompatibility or response-access error.
The 19 remaining failures require product/service changes outside this task's
ownership; root CI was intentionally not run under the assigned constraints.
