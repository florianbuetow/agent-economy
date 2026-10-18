# MP-07 Task Board endpoint fixture reconciliation

Date: 2026-10-18

Scope was limited to
`services/task-board/tests/integration/test_endpoints.py`. No product,
conftest, other test, Git, ordinary data, or ordinary service log was
modified.

## Preserved original

The pre-edit file and its SHA-256 are stored at:

`/private/tmp/mp07-board-endpoint-fixture-original-20261018`

Original file SHA-256:

`78e9a6fdf1c7f47ea2e9a637b63348b8878a7f7d729d6ff7d8ba606b052388a3`

The SHA-256 of the `SHA256SUMS` manifest is:

`3766a94532660d8b9873a55e1d878e00c322db871bb38898b57f95e784aa01c7`

## Baseline

Root CI 21 at `/private/tmp/agent-economy-root-ci-21.log` collected 65 Task
Board integration cases. The embedded-Gateway maintenance-lock case passed;
the full lifecycle case failed at task creation with HTTP 400
`invalid_payload` because `acceptance_window_seconds` was absent. The
remaining 64 Task Board integration cases passed.

This was a superseded fixture request, not a product failure. Because creation
failed, the test never reached its legacy bid, accept, submit, or approve
requests.

## Reconciliation

The task publication now supplies an explicit 1,800-second acceptance window.
Every native lifecycle command supplies a fresh caller-owned operation ID and
the lifecycle version observed from the authoritative preceding response:

- publication returns open/version 0;
- bid uses expected version 0;
- acceptance uses expected version 0 and returns accepted/version 1;
- submission uses expected version 1 and returns submitted/version 2;
- approval uses expected version 2 and returns approved/version 3.

The fixture now creates the worker's real zero-balance Gateway account and
reuses the existing persisted-release helper from
`test_mp07_native_public_lifecycle.py`. That helper proves the Task Board
operation is claimed before the Bank effect, persists an actual Central Bank
release receipt, and lets Task Board finalize from Gateway evidence.

All original assertions remain: HTTP statuses, persisted poster/escrow/open
state, bid worker and amount, accepted worker/status, asset upload, submitted
and approved lifecycle, and final persisted approval timestamp. Additional
assertions prove:

- the release boundary was awaited exactly once with the claimed operation;
- one `escrow_release` receipt exists with the approval source reference;
- the worker received exactly the posted reward once;
- exactly one `task.approved` Board event exists.

## Verification

Commands were run from `services/task-board` with the existing environment and
an isolated live Gateway port.

- Owned endpoint file: **2 passed in 0.87s**
- Full Task Board integration suite: **65 passed in 26.06s**
- Ruff format and lint: passed
- `git diff --check`: passed

Root CI remains the parent lane's final gate.
