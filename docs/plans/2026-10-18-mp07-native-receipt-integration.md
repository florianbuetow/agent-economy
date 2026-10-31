# MP-07 Native Receipt Integration Repair

## Outcome

Gateway task-operation finalization now recognizes the exact `central-bank` event provenance
written by the production Central Bank client. Approval and ruling settlement receipts retain their
original canonical request JSON and SHA-256 evidence; the repair does not rewrite receipts or relax
financial-conservation checks.

## Reproduction evidence

The disposable acceptance runtime archived at
`/private/var/folders/d5/vbvfgyfs4757w9nrv35rn_7m0000gn/T/agent-economy-b267fd711664474f930c83acbcd44ac4-2ey04s4q`
completed task acceptance and submission, then reproduced the defect during approval:

- task: `t-ce260a31-7c72-48dc-a77d-846f4fdc31a7`
- operation: `op-28076a85-781a-5195-b374-48932fa8f50e`
- Bank release: HTTP 200 with receipt hash
  `249f7bb3fbe741dcd3ac69e7e16ecc76fbb2df6545d083f8a07ebf4a2d25a650`
- Gateway finalization: HTTP 409 `effect_evidence_conflict`

Read-only inspection showed matching operation kind, canonical request JSON, request hash, source
reference, escrow ID, payer, task, amount, terminal escrow status, result JSON, event ID, event type,
recipient leg, transaction, balance snapshot, and settlement timestamp. The sole mismatch was
`events.event_source`: the producer stored `central-bank`, while MP-07 finalization required `bank`.

A fresh live run after that source correction exposed a second mismatch in
`/private/tmp/mp07-agent-e2e-postfix-live/runtime-3f0455da7b9242f3a3f1fc8ea2001efb-f377oth9`.
Operation `op-15492af8-763c-42cb-a021-0e5c9c743013` again had matching receipt, escrow, leg,
transaction, hash, event source, event type, and timestamps, but its event `task_id` was null. This
is the exact shape emitted by the production `GatewayClient.escrow_release` method.

## Root cause

MP-04 intentionally changed the production `GatewayClient` Bank event source to `central-bank` and
added a Central Bank integration assertion for that persisted value. MP-07's new
`TaskOperationWriter._receipt_matches` was developed with older direct-writer fixtures that still
used `bank`, so its event-provenance predicate rejected a valid native receipt after the money
effect had already committed. The startup task-contract audit repeated the same stale predicate in
its completed-ruling and resolved-legacy-settlement receipt checks, so otherwise valid databases
also failed audit and restart admission after the fixture source was corrected.

Those consumers also treated the nullable event `task_id` presentation field as the authoritative
financial task binding. The actual binding is the receipt's `escrow_id` joined to
`bank_escrow.task_id`; finalization already checks that escrow task, payer, amount, status, and
resolution timestamp before inspecting the event. Positive receipt legs additionally bind their
transaction to the receipt event ID and escrow reference.

The producer contract is canonical: a Bank receipt event produced by the Central Bank service has
source `central-bank`. The consumer must require that exact value together with the existing exact
event type and task binding.

## Repair

1. Add a regression that claims an approval with `TaskOperationWriter`, persists the corresponding
   native release with the real `DbWriter`/`BankWriter` path and `central-bank` provenance, proves
   money and receipt audits are clean, then finalizes the same operation.
2. Require exact `central-bank` provenance in the MP-07 receipt matcher.
3. Require the same exact provenance in native-ruling and legacy-settlement startup audits.
4. Permit an event task binding to be null or the exact escrow-bound task; reject every foreign
   non-null task ID in finalization and startup audits.
5. Capture the real production `GatewayClient` release payload and assert that it emits canonical
   `central-bank` source with no invented event task ID.
6. Reconcile only positive MP-07 settlement fixtures that still used the pre-MP-04 `bank` label.
7. Preserve the receipt request SHA-256 before and after task finalization.

## Validation contract

The existing negative controls remain mandatory:

- wrong recipient or amount remains an evidence conflict;
- wrong event source, event type, or event task remains an evidence conflict;
- the pre-MP-04 `bank` source remains invalid for finalization and both startup-audit paths;
- a foreign non-null event task remains invalid, while the producer's null presentation field is
  accepted only alongside the exact receipt-to-escrow task binding;
- receipt, transaction, and escrow timestamp disagreement remains an evidence conflict;
- a settlement owned by another operation reports the true winning operation;
- later authorized positive balance changes do not invalidate an immutable receipt.

No receipt, leg, transaction, escrow, event, or operation row is rewritten during successful
finalization except the Board-owned task transition, Board event, and Board operation result.

## Verification evidence

- The original Central Bank source regression failed with `effect_evidence_conflict` before the
  source correction and passed afterward.
- A fresh live receipt with canonical source and null event task reproduced the same conflict until
  the nullable presentation-field correction. Replaying the repaired `_receipt_matches` directly
  against that read-only artifact returned `True` for the original operation.
- The targeted public SDK confirmation at
  `/private/tmp/agent-economy-mp07-receipt-live-confirm/runtime-1ca5f28b48d14a9f9b815f21b463d271-qby5zib7`
  passed approval and cancellation: `2 passed`.
- The DB Gateway MP-07 unit set passed: `176 passed`.
- The production `GatewayClient` payload regression passed and proves release events use
  `central-bank` while omitting `task_id`.
- Ruff, formatting, mypy, and pyright passed for the changed Gateway source and regressions; Ruff,
  formatting, and pyright passed for the Central Bank producer regression.

## Fixture reconciliation addendum

The first component CI run after the product repair exposed stale positive fixtures rather than a
new runtime defect. Their settlement emitters still wrote `bank` even though the production Bank
producer and strict consumers use `central-bank`. Product validators, negative controls, receipt
hashes, and test assertions were left unchanged.

Original fixture hashes captured before reconciliation:

- `806f341f12e5eb21313242f5e95a9d14a7c58f40142c1dc253a2dde09e05690d`
  — `services/task-board/tests/fakes/router_gateway_effects.py`
- `d94895687855276f54c0a54c0c1dfc353f3074694bd2a44d41c4ff007e6c7444`
  — `services/task-board/tests/fakes/native_task_fixture.py`
- `bfa7eef7ca880466103ac70d7c33600fb608f0c3fec36977e2c2239de074802b`
  — `services/task-board/tests/unit/test_mp07_gateway_store_parity.py`
- `c2a3393f5ac624e78da6d6412268b1ec87cfaa523e351bb83951641d2221d377`
  — `services/task-board/tests/unit/test_mp04_ruling_replay.py`
- `290f136b3e186c1609309d933cec2aa6fcb646ac29b8339d43d57eaad291d133`
  — `services/db-gateway/tests/fakes/native_board_history.py`
- `266e4b997aefc5351f25f0f4f8cf28efe2468091de6b98bcabab2717279ebb5a`
  — `services/db-gateway/tests/unit/test_feedback_reveal_atomicity.py`

Only positive `escrow.released` and `escrow.split` event-source literals changed. Account creation
and escrow-lock fixture sources remain untouched because task finalization does not consume them.
The exact DB Gateway failures plus feedback setup passed (`13 passed`), the complete set of
previously failing Task Board fixture-dependent files passed (`322 passed`), and representative
post-format Task Board paths passed (`3 passed`). Fixture-only Ruff, formatting, and pyright checks
passed in both components.
