# MP-07 Runtime Workload Reconciliation

## Scope

Reconcile the live recovery and migration workloads with the MP-07 public Task
Board command contract while retaining their existing artifact, process-death,
restore, integrity, conservation, identity, and provisioning proofs.

The superseded fixture contract omitted `acceptance_window_seconds`, operation
IDs, lifecycle preconditions, and typed command acknowledgment handling. The
Gateway also now admits schema v3 at startup, so migration coverage must retain
the verified v0-to-v1 and v1-to-v2 phases and add a separately backed and verified
v2-to-v3 phase before restarting services.

## Preserved Baselines

Original bytes are stored under `/private/tmp/mp07-runtime-workload-reconcile/`:

- `test_recovery_live_restore.py.baseline` —
  `b04e266b45ea0655843139ade32328614c9e8477e289be8026fdb1d08640c4a9`
- `test_migration_live_workload.py.baseline` —
  `544a7e73e1522af2ce7878bc8306e5d96146017ddb584e356002a94453f1e31e`
- `test_migration_process_crash.py.baseline` —
  `28829dc362c9cd79708f16f277f9c7889616208b58bab8aad246ce02d1ba7d2e`

The genuine pre-change failure was fixture-level: `_public_workload()` called
`post_task()` without the required explicit acceptance window. The parent runtime
gate collected 185 tests, passed 181, and reported four failures sharing that
first boundary. Its captured log is
`/private/tmp/agent-economy-mp07-runtime-gate.log`.

## Reconciled Contract

- Public task creation supplies an explicit acceptance window.
- Bid, acceptance, submission, and post-migration approval use deterministic
  operation IDs and the exact lifecycle version observed for their source state.
- Every command consumes and validates `TaskBoardCommandResponse` before the next
  transition.
- The recovery workload still ends at `submitted`, with locked escrow and the
  original asset bytes present after restore.
- Migration retains its v0-to-v1 and v1-to-v2 assertions, then takes a new immutable
  backup and verifies v2-to-v3 before service restart.
- Process-death cases still kill the real paused migration child, prove group
  absence and legacy recovery, then complete every migration phase and resume the
  signed public workflow.

## Verification

The four focused cases passed through the Gateway-configured root environment:

```text
PYTHONPATH="/Users/flo/Developer/github/agent-economy:/Users/flo/Developer/github/agent-economy/tests" \
uv run --directory services/db-gateway pytest \
  /Users/flo/Developer/github/agent-economy/tests/runtime/test_recovery_live_restore.py \
  /Users/flo/Developer/github/agent-economy/tests/runtime/test_migration_live_workload.py \
  /Users/flo/Developer/github/agent-economy/tests/runtime/test_migration_process_crash.py \
  -q -x
```

Fresh result: `4 passed in 43.06s`. Ruff lint, Ruff formatting, and
`git diff --check` also pass for the scoped files. Direct standalone Pyright is not
a configured root-runtime gate: the DB Gateway strict configuration cannot resolve
root `scripts`/`tests` imports and reports the files' existing untyped helper
surface. The focused runtime execution and the parent-owned fresh full root suite
remain the applicable validation.

Current source hashes:

- `test_recovery_live_restore.py` —
  `eede1e6289dd13b3edf3bff014001ebc618e3f2d33997af4d8f6f1a51bcb8443`
- `test_migration_live_workload.py` —
  `2fa881e760671c7e4bb9a591428f40f2c128bf993c0d9fd6d3b6957ae57cfc6d`
- `test_migration_process_crash.py` —
  `22c8579277d4ea7bd98317f655608360230ab7563d9be36c1e92568b840650a3`

No material product defect appeared in this lane. No skip, weakened assertion,
direct SQL status bypass, or mocked public mutation effect was introduced. Final
completion remains partial until the parent runs the fresh full root suite.
