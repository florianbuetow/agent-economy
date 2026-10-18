# MP-07 cross-service fixture reconciliation

Date: 2026-10-18

Scope was limited to the cross-service integration fixtures in
`tests/integration`, specifically the Task Board, Reputation, Court, and
no-Gateway contracts plus the shared in-process Gateway helper. No product,
service, shared-library, Git, or ordinary runtime state was modified.

## Preserved originals

The original six owned files were copied byte for byte to:

`/private/tmp/mp07-cross-service-fixtures-originals-20261018`

The SHA-256 of its `SHA256SUMS` manifest is:

`28985f7c0266c619def2ce9d20b730121d568eb115e26ac874cbecead69b0a00`

## Baseline

Command, run from `services/db-gateway`:

```text
PYTHONPATH="$PWD/../..:$PWD/../../tests" \
UV_CACHE_DIR=/private/tmp/mp07-cross-service-uv-cache \
uv run --no-sync pytest ../../tests/integration -q --tb=short
```

Result: **5 passed, 6 failed, 15 errors**.

The baseline did not reach the intended Gateway write contracts. Fourteen
setup/error paths and five cleanup-test failures came from the generated
Gateway config missing the required explicit `board_authority`. The remaining
startup-order failure was sandboxed localhost binding, separate from the
fixture contract.

After adding temporary receiver-owned board authority, the suite reached its
actual stale requests: **17 passed, 9 failed**. All nine failures were fixture
incompatibilities at task publication. The fixtures attempted to publish
`approved` or `disputed` tasks directly, omitted
`acceptance_window_seconds`, or used timing fields that did not form the
native immutable cutoff contract.

## Reconciliation

`gateway_helpers.py` now creates a temporary platform key and supplies the
required explicit `board_authority` paths and deadline actor. All paths remain
owned by the temporary fixture directory and are cleaned by the existing
client lifecycle.

`native_task_fixtures.py` provides private-Gateway fixture operations only:

- deterministic Gateway task-operation clock selection;
- authoritative open task publication with an explicit acceptance window;
- native versioned bid, acceptance, submission, approval, and dispute claims;
- approval finalization from a real persisted Central Bank release receipt;
- dispute finalization from the test's real persisted Court claim.

It does not use raw SQL, generic status updates, response mapping,
monkeypatches over the SDK, parallel fake state, retries, or skips.

### Retired request replacements

- Task publication still asserts HTTP 201 and the same persisted task data,
  but now publishes only `open` and writes the v3 task contract atomically.
- `POST /board/bids` HTTP 201 was replaced by native
  `POST /board/task-operations` HTTP 200. The persisted bid ID, task, bidder,
  amount, and proposal field remain asserted; the native canonical proposal is
  the string form of the amount (`"180"`).
- Generic `POST /board/tasks/{id}/status` acceptance was replaced by native
  bid submission and acceptance. The same accepted status, worker, bid ID,
  accepted timestamp, and execution deadline remain asserted.
- Reputation's direct `approved` seed was replaced by open publication,
  native bid/accept/submit, a claimed approval, a persisted Central Bank
  receipt using `event_source=central-bank`, and empty-body finalization. The
  sealed/reveal feedback assertions are unchanged.
- Court's direct `disputed` seed was replaced by open publication,
  native bid/accept/submit, a claimed dispute, each test's persisted Court
  claim as authoritative evidence, and empty-body finalization. Claim,
  rebuttal, ruling, judge-vote, and event data assertions are unchanged.
- `test_services_without_gateway.py` was not changed; all five unavailable
  semantics continue to pass.

The deterministic clocks retain the historical fixture timestamps and exact
accepted-at assertion without using arbitrarily long acceptance windows.

## Verification

Focused results after reconciliation:

- `test_taskboard_gateway_writes.py`: **4 passed**
- `test_reputation_gateway_writes.py`: **2 passed**
- `test_court_gateway_writes.py`: **3 passed**

Final full cross-service command is the baseline command above, with the
startup-order localhost bind approved for its disposable IsolatedRuntime.

Final result: **26 passed in 8.61s**.

Ruff format and lint passed for the changed integration files. Root CI remains
the parent lane's final gate.
