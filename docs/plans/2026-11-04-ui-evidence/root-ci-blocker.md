# Resolved root gate failure: test fixture connection race

The final root `just ci-quiet` exited **1** in Task Board unit tests:
`test_sweep_is_idempotent_against_a_concurrent_lazy_evaluation` raised
`sqlite3.IntegrityError: FOREIGN KEY constraint failed` while finalizing a
settlement. 1,119 other Task Board cases passed; elapsed 1527.34 seconds.
`root-ci-failure.log` preserves the complete natural failure. That historical gate did not reach its later phases. The repair subsequently
passed the complete independent root gate; UI-WORK is complete and T-217–T-222
are closed. See `final-root-result.json` and `root-ci-final.log`.
UI CI and the UI/browser/live-journey evidence remain passing.

## Bounded diagnosis

The unchanged deadline module passed all 5 tests in isolation. Twenty-five
fresh invocations of the specific concurrency test then passed (each invocation
is retained in `ate-ui-root-deadline-repeat-processes.log`). These passes do not
replace the failed root gate or establish that the intermittent defect vanished.

A single disposable **injected scheduling probe**, distinct from the natural
failure, proved the shared-connection mechanism:

1. The deadline evaluator's worker thread inserted task event 9 during finalization.
2. The fixture's simulated Bank replay ran on the event-loop thread using the
   same SQLite connection. Its `BEGIN IMMEDIATE` raised "cannot start a
   transaction within a transaction" and its error handler rolled back.
3. The first thread resumed and observed its event no longer existed.
4. Updating the operation's event reference raised the exact foreign-key error.

The probe exited 0 because its diagnostic assertions confirmed this mechanism;
that exit is **not** passing product/acceptance evidence. The exact scratch
program and trace are retained as `ate-ui-gateway-race-probe.py` and `.log`.
It used only a disposable database and did not change repository source.

Relevant boundaries:

- `services/task-board/src/task_board_service/services/async_store.py` dispatches synchronous storage onto worker threads. Each DeadlineEvaluator
  already has a single-worker executor, so two finalizations from that evaluator
  are ordered.
- `services/task-board/tests/fakes/gateway_task_store.py` exposes one SQLite
  connection without synchronization across its request adapter and direct fake
  Bank calls.
- `services/task-board/tests/fakes/native_task_fixture.py:327` runs
  `persist_release` synchronously on the event-loop thread against that same
  writer/connection.
- The production Gateway route at
  `services/db-gateway/src/db_gateway_service/routers/board.py:174` calls its
  synchronous finalizer without awaiting inside that call. The probe does not
  establish a production settlement defect.

## Bounded remediation authorized and implemented

Synchronize access to the fake's shared connection with one per-instance
reentrant lock in `gateway_task_store.py`; make adapter requests and the
`native_task_fixture.py` simulated Bank read/release acquire the same lock.
Add a deterministic fixture regression for concurrent operations. Preserve the
existing test's two-delivery, one-claim, one-settlement and one-event assertions.
No production service, schema, financial rule or dependency change is proposed.

The owner explicitly authorized this test-only extension beyond the UI brief.
The two helper changes now share a per-instance reentrant connection guard;
`close` uses the same guard. Existing acceptance tests and production code are
unchanged. The new `test_gateway_fixture_serialization.py` holds a real pending
event while a Bank replay starts on another thread, verifies the replay waits,
and checks the original one-settlement/one-event financial invariants.

The new test failed first with the exact foreign-key error (`ate-ui-fixture-red.log`).
After repair, it and 22 existing deadline/parity tests passed in 20.53 seconds
(`ate-ui-fixture-green.log`). Style, mypy, pyright and spelling passed
(`ate-ui-fixture-static.log`). `fixture-source-sha256.txt` records the repair.
T-219/T-220 are closed after independent review and the final full root exit 0.
The original root failure remains retained; targeted green is not root green.

## Independent repair review

The independent reviewer approved all three test-only files with zero findings.
The per-instance guard covers adapter requests, simulated Bank read/release and
close. No `await` occurs inside the guard, and the worker's Gateway transaction
cannot wait on the event loop. The regression releases its held finalizer in a
`finally` block, drains worker futures, closes the evaluator and then the store.
All original operation/receipt/credit/transaction/event conservation assertions
remain. The parent-owned final root gate exited 0; no engineering blocker remains.
