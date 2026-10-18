# MP-07 legacy escrow consumer reconciliation

## Plan before test edits

Follow `2026-10-18-mp07-legacy-escrow-retirement.md`. Scope is the three assigned
unit-test consumers only. Production retirement and shared constructor updates
belong to the parent and must follow the converted pre-implementation test run.

Preserve the six raw release/split success, ServiceError identity, and generic
exception translation cases. Their target constructor takes only the Bank client.
The posting-compensation helper is narrowly named
`release_posting_compensation(task_id, escrow_id, poster_id)` and preserves the
existing deterministic compensation operation ID and source reference.

## Contract reconciliation

- Replace arbitrary expired/approved dictionaries and flag-based retries with
  actual Gateway claims on valid source histories. Missing/failed financial
  evidence leaves source business state and pending ownership intact. Real Bank
  receipts authorize completion, and retries retain the same operation ID.
- Preserve poster refund and worker payout checks with persisted receipt legs,
  transactions, account reconciliation, and a single semantic completion event.
  Exact completed replay must issue no further Bank call.
- An unowned open state, a bare flag before its cutoff, and an unsupported disputed
  state cannot authorize payout. Those controls use real histories and no fabricated
  successful Bank response.
- MP-04 lock-receipt parser cases remain unchanged. Native settlement identity is
  now supplied by the Board operation caller and must survive transport/retry;
  different command intents cannot reuse an already claimed identity. Compensation
  remains the explicit task-derived exception. Real independent ruling histories
  cover zero/full/split outcomes; one database never pretends different financial
  terms under one operation ID all succeeded.
- Stored ruling replay builds actual disputed and ruled histories. Same operation,
  original version, ruling ID, percentage and summary replay without settlement.
  Changed valid terms conflict with `operation_id_conflict`; invalid percentages
  retain `invalid_worker_pct`. Split the old mixed malformed/wrong-state case into
  independent malformed-shape and fresh valid wrong-state controls.

All native setup uses the approved Gateway-backed fixture and actual writers.
Any split callback locally persists real Bank evidence. Token validation mocks
bound the coordinator test; they do not claim authenticated HTTP or Court judging.
No production/shared helper, dictionary fake, or unrelated test is edited here.

## Evidence protocol

Retain the untouched baseline, then the converted pre-implementation failures under
`/private/tmp/agent-economy-mp07-legacy-consumers-*.log`. Report that red result and
stop for parent coordination before final green tests. An interface-gated failure
is not reported as proof that every downstream assertion independently ran red.
Independent review and root CI remain separate completion gates.

## Converted pre-implementation proof

The untouched baseline was **13 failed, 23 passed**, exit 1, in
`/private/tmp/agent-economy-mp07-legacy-consumers-before.log`. All thirteen failures
were old ruling inputs lacking native operation identity.

The final converted run collected 39 cases and produced **7 failed, 32 passed**,
exit 1, before production changes:

- Six raw transport cases fail at the target constructor because production still
  requires `store`. This is one constructor-interface gap, not six independent
  transport defects.
- The real intent-separation/compensation case reaches the missing
  `release_posting_compensation` method. Assertions after that call remain gated
  until implementation; no compensation green result is claimed yet.
- The remaining 32 cases pass, including actual native pending/retry ownership,
  receipt parser controls, zero/full/split ruling histories, and immutable ruling
  replay/changed-term/invalid-percentage protections.

Command from `services/task-board`:

```sh
UV_CACHE_DIR=/tmp/agent-economy-review-uv UV_FROZEN=1 uv run --no-sync pytest tests/unit/test_escrow_coordinator.py tests/unit/test_mp04_financial_intents.py tests/unit/test_mp04_ruling_replay.py -q --tb=short --color=no
```

Log: `/private/tmp/agent-economy-mp07-legacy-consumers-converted-final-red.log`
(terminal exit 1, 7 failed / 32 passed in 9.59 seconds).
Ruff has no remaining findings and all three files pass the format check; logs:
`/private/tmp/agent-economy-mp07-legacy-consumers-ruff-final-red.log` and
`/private/tmp/agent-economy-mp07-legacy-consumers-format-final-red.log`.

Frozen test source SHA-256 values:

| File relative to `services/task-board/tests/unit` | SHA-256 |
| --- | --- |
| `test_escrow_coordinator.py` | `18161142cdb8199c381c6641265f3989bc39078d60df5fc5dd12cd7f1ad625dc` |
| `test_mp04_financial_intents.py` | `fefd0e7b86e0d96fd003b152a953d3c8daa7f2aad6ae92cc23abddfe21a1b870` |
| `test_mp04_ruling_replay.py` | `c2a3393f5ac624e78da6d6412268b1ec87cfaa523e351bb83951641d2221d377` |

At this red checkpoint the consumer author stopped for parent coordination.
Production retirement, shared constructor updates, final green verification, type
checks and independent review were still outstanding. No tracker item was closed
by that checkpoint.

## Final verification after parent implementation

After the parent implemented the Bank-only constructor and the explicit posting
compensation method, the **same frozen test sources** passed **39 cases**, exit 0,
in 11.11 seconds. No assertion or test setup was changed for this green run.
The now-reached compensation checks prove the retained deterministic operation
identity and source reference, two same-identity deliveries, and one persisted
compensation receipt. The real native intent-separation scenario also completes
its final expiry and settlement assertions.

Independent consumer run log:
`/private/tmp/agent-economy-mp07-legacy-consumers-final-green.log`.
The command is the same three-file pytest command above.

Scoped checks all exited 0:

- Ruff: `/private/tmp/agent-economy-mp07-legacy-consumers-ruff-green.log`
- Format check: `/private/tmp/agent-economy-mp07-legacy-consumers-format-green.log`
- Mypy: `/private/tmp/agent-economy-mp07-legacy-consumers-mypy-green.log`
- Pyright: `/private/tmp/agent-economy-mp07-legacy-consumers-pyright-green.log`

Mypy used local service/shared-library source roots through `MYPYPATH`; Pyright
reported zero errors and warnings. The three SHA-256 values from the red checkpoint
remain unchanged. This follow-up modified only this reconciliation document;
production and shared-fixture changes belong to the parent implementation.
Independent review, broader service checks, and fresh root CI remain required
before any full MP-07 completion claim.
