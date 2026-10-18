# MP-07 Worker Fixture Reconciliation

## Scope

Reconcile the pre-MP-07 math-worker loop tests with the required
`RuntimeTaskCommands` constructor boundary without changing product behavior.
The owned tests retain their original assertions for status vocabulary, phase
transitions, timeout handling, payouts, disputes, and rebuttals.

## Preserved baseline

Original SHA-256 hashes before editing:

- `agents/tests/unit/math_worker/test_loop.py`:
  `72d1779fe23c2694d95cb35f55f359467a900a0976d0ffbede58e17b5fefc212`
- `agents/tests/unit/math_worker/test_loop_phase_machine.py`:
  `10cd9e6d85938c8b27c491c6c9567f48b6fc44451c734dfaea5d84b4e57233f8`
- `agents/tests/unit/math_worker/test_review_timeout_outcome.py`:
  `c52eba692e93505a6de7a05f20200ed77d05108d879182331a47ce11b806205c`

Fresh targeted baseline command:

```text
UV_CACHE_DIR=/tmp/agent-economy-review-uv UV_FROZEN=1 uv run --no-sync pytest \
  tests/unit/math_worker/test_loop.py \
  tests/unit/math_worker/test_loop_phase_machine.py \
  tests/unit/math_worker/test_review_timeout_outcome.py -q
```

Baseline result: 26 collected, 9 passed, 17 failed. Every failure occurred at
construction with `TypeError: MathWorkerLoop.__init__() missing 1 required
positional argument: 'commands'`. No original behavioral assertion ran and
failed.

## Fixture reconciliation

- Direct phase/polling tests receive an unused `MagicMock` constrained to the
  `RuntimeTaskCommands` interface. Those tests do not cross the command
  boundary.
- Full-cycle tests use a real `FileTaskIntentStore` and
  `RuntimeTaskCommands`, bound to an explicit deterministic Ed25519 public
  identity, registered worker ID, and five explicit service URLs.
- Native bid and submission transports return typed
  `TaskBoardCommandResponse` values. Submission acknowledgments include the
  actual `submitted` target status, lifecycle version, request hash, operation
  record, and frozen worker/task facts required by acknowledgment admission.
- Native full-cycle task IDs use the canonical documented `t-<UUID>` form.
  Short IDs remain only in direct polling/rebuttal tests where no native intent
  is created.
- Full-cycle `get_task` snapshots now include the bid preparation read and the
  submission precondition read. Accepted snapshots explicitly carry current
  lifecycle version, poster, worker, escrow, accepted reward, and selected bid
  amount.

## Superseded assertions

- `test_full_cycle_reaches_approved_with_full_payout` previously asserted
  `submit_deliverable("t-1")`. The public native command contract now also
  requires `operation_id` and `expected_lifecycle_version`. The test still
  asserts exactly one submission for the same selected task and additionally
  verifies canonical operation-ID shape and the frozen accepted-task version.
- Old full-cycle mocks returned untyped dictionaries directly from
  `submit_bid` and `submit_deliverable`. Those internal transport assumptions
  were replaced with typed public SDK responses because the runtime command
  boundary rejects untyped acknowledgments. Outcome assertions were retained.
- Old full-cycle call sequences omitted current task reads performed while
  preparing durable bid/submission intents. Explicit snapshots were added;
  the terminal status and payout supplied to each case were not changed.

## Verification

The reconciliation is ready for independent review when all of the following
pass with `UV_CACHE_DIR=/tmp/agent-economy-review-uv`, `UV_FROZEN=1`, and
`uv run --no-sync`:

1. The three owned test modules together.
2. Ruff format check and Ruff lint over the three tests and their worker fixture.
3. The broader agents unit suite or repository quality gate owned by the
   integrating parent.
