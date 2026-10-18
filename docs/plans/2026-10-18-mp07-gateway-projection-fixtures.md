# MP-07 Gateway projection fixture reconciliation

## Scope

Reconcile these DB Gateway unit suites with native task publication and operation
contracts:

- `test_schema_additions.py`
- `test_gap_e13_bid_count.py`
- `test_task_dispute_id_column.py`

Source snapshots and SHA-256 hashes are stored under
`/private/tmp/agent-economy-mp07-gateway-projection-before*`. The untouched target log
is `/private/tmp/agent-economy-mp07-gateway-projection-baseline.log`: 16 tests collected,
13 failed and 3 passed. Every failure stopped at `POST /board/tasks` with
`400 missing_field`, before the intended projection assertion.

## Publication reconciliation

All three local task builders will supply a fixture-selected
`acceptance_window_seconds`, make `bidding_deadline` equal
`created_at + bidding_deadline_seconds`, and retain their existing successful creation
assertions. Business clocks are file-local and affect Gateway lifecycle time only; JWT
validation retains real time.

## Per-case changes

### Schema additions

- `test_submit_bid_with_amount` will submit a native versioned bid and preserve the exact
  stored `amount == 125` assertion, including that the signal exceeds reward 100.
- The retired `test_submit_bid_without_amount_uses_zero` compatibility case will become
  `test_submit_bid_without_amount_is_rejected`. A native command without `amount` must
  return `400 invalid_payload` and leave bids, operations, events and task bid count
  unchanged. Historical zero-valued rows remain covered by frozen migration/receipt tests;
  this suite will not restore a generic-writer default.
- Asset content-hash, nullable hash, initial zero counters and Court rebuttal-deadline
  assertions remain unchanged.

### Bid count

All bid creation and duplicate attempts will use the frozen native Board HTTP helper.
The zero, one-per-successful-bid, list-length equality and duplicate-no-increment
assertions remain unchanged. The local task graph retains its exact task, poster and escrow
identities.

### Dispute ID

The two former generic status-write cases will build a real history through native bid,
acceptance, asset, submission and pending dispute, then persist a matching Court claim with
`claim_id = "disp-1"` and finalize the same dispute operation. They will continue to assert
the exact stored/read `dispute_id`. The publication-null case and all frozen partial-schema
rejection/byte-preservation assertions remain unchanged.

The local lifecycle clock advances coherently while bid, acceptance, submission and dispute
remain inside their persisted cutoffs; it does not recreate the old arbitrary 12:00 generic
status mutation. The Court claim carries explicit matching parties, reason and rebuttal
deadline.

## Verification

Run the complete 16-case target, the relevant native Board/Court projection controls, Ruff
and the formatter check. Confirm the frozen native helper remains byte-identical. This
bounded proof does not replace the complete DB Gateway or isolated root quality gates.

## Result

- Untouched target: 13 failed, 3 passed; every failure stopped at invalid publication.
- Publication-only stage: 7 failed, 9 passed; the remaining failures precisely identified
  retired generic bid and status writers.
- Final target: 16 passed in 3.82 seconds.
- Native Board/Court controls: 71 passed across the Board router, Court operation,
  publication and generic-write boundary suites.
- Ruff: all checks passed; all three files are formatted; diff whitespace check passed.
- The frozen native history helper remained byte-identical at SHA-256
  `290f136b3e186c1609309d933cec2aa6fcb646ac29b8339d43d57eaad291d133`.
- No application source, shared fixture, dependency, skip, suppression, Git history or live
  runtime changed. Complete Gateway and isolated root gates remain pending.
