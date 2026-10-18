# MP-07 first storage consumer reconciliation

## Plan recorded before editing the consumer

Scope is `services/task-board/tests/unit/test_task_store.py` only. Replace its
dictionary fake with the independently reviewed `GatewayTaskStore` adapter. The
other fake consumers and the original fake remain unchanged.

The original four tests pass against the dictionary fake. The baseline command is
`uv run --no-sync pytest tests/unit/test_task_store.py -q --tb=short --color=no`,
run from `services/task-board` with the isolated UV cache and frozen environment.
Output is `/private/tmp/agent-economy-mp07-first-store-consumer-before.log`.
This historical green proves the old fake contract only; it is not native storage
or lifecycle proof, and no product red failure is claimed for this test adoption.

## Explicit contract reconciliation

| Prior setup/assertion | Native replacement and preserved outcome |
| --- | --- |
| Insert an arbitrary accepted dictionary | Publish a funded open task, persist a native bid, and accept that bid through a native command. The second task still ends accepted. |
| Change the first task directly from open to submitted | Persist bid and acceptance, record an owned asset, then submit through a native command. The first task still ends submitted. |
| A wrong expected status returns integer zero | Issue a fresh native acceptance command with expected open status and the current lifecycle version against the already accepted second task. Require typed `409 invalid_status`, unchanged task, and no new event. |
| Duplicate raw bid raises fake `DuplicateBidError` | A fresh native bid command from the same bidder requires `ServiceError(409, bid_exists)`, matching `TaskOperationWriter._submit_bid`. Existing bid identity, amount, list length, and bid count remain unchanged. |
| Record an asset on an unaccepted task with an arbitrary uploader | Accept a real bid first and record metadata owned by that accepted worker. Preserve asset get/list/count, filename, and identity checks. |
| Fake close is a no-op | Close the owned SQLite connection, permit repeated close, and preserve caller-owned database files. |

Task get/list/count and grouped submitted/accepted counts remain explicit
assertions. Setup registers real Identity rows, creates account funding with an
actual financial receipt, and locks actual escrow before task publication. No
success response or supporting history is fabricated, and no direct SQL history
seed is required. Read-only SQL may inspect stored evidence if needed.

Every adapter constructor receives an explicit temporary path, v3 schema, WAL
configuration, timeout, caller KID, platform/system authority, and fixed clock.
Fixture helpers own their operation identities; the adapter never allocates them.
Acceptance windows are explicit posted terms in each fixture task.

## Validation scope

Run the converted four tests together with all seventeen adapter parity tests,
then Ruff, Mypy, and Pyright for the edited Python file. Existing consumer adoption
is evidence for this bounded suite only. Other router/deadline/legacy fixtures,
independent review of this conversion, and fresh root CI remain outstanding.

## Verification evidence

The converted four consumer tests and all seventeen adapter parity tests passed
together: **21 passed**, exit 0, with no skips or expected failures.

```sh
UV_CACHE_DIR=/tmp/agent-economy-review-uv UV_FROZEN=1 uv run --no-sync pytest tests/unit/test_task_store.py tests/unit/test_mp07_gateway_store_parity.py -q --tb=short --color=no
```

Final log: `/private/tmp/agent-economy-mp07-first-store-consumer-final-green.log`.
Ruff check and format check passed. Mypy passed with local `src` roots supplied
through `MYPYPATH`; Pyright reported zero errors and warnings. Their logs are
`/private/tmp/agent-economy-mp07-first-store-consumer-mypy-final.log` and
`/private/tmp/agent-economy-mp07-first-store-consumer-pyright-final.log`.

An initial Mypy invocation followed the existing `tests.helpers` module and found
an unrelated typing error there. That module was left untouched; fixture public
keys now use cryptography and the existing `service_auth.signing.public_key_to_b64`
utility directly. No assertion or production behavior changed for this adjustment.

Verified `test_task_store.py` SHA-256:
`19ea28eb7bab1ce15bd9050f8183b2c5d3ffd85a32542826132b0d9af062cddb`.
Only this consumer and this reconciliation document changed in the adoption slice.
