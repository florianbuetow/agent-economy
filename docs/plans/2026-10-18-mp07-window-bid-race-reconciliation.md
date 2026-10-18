# MP-07 acceptance-window and bid-race reconciliation

The untouched combined run of `test_mp07_acceptance_window.py`,
`test_mp07_bid_admission_races.py`, and `test_mp07_accept_expiry_race.py` collected 17 tests and
failed all 17 before their behavior assertions. Each clock installer still patched
`task_bidding.datetime`, a seam removed when bid and cutoff eligibility moved into native Gateway
commands. These failures are fixture drift; they do not show a current acceptance-window or race
product failure.

The original behavior remains unchanged:

- Every poster supplies a positive explicit acceptance window.
- Bids are legal strictly before the stored bidding cutoff; equality belongs to expiry.
- Tasks without bids expire at bidding equality. Tasks with bids remain open through bidding close
  and expire at acceptance equality.
- Early acceptance and acceptance one microsecond before the selected cutoff remain legal.
- A stale bid or acceptance validated before a competing transition cannot mutate the winner's
  committed task, bid count, accepted terms, escrow, or semantic events.
- Independent bids do not increment lifecycle version and may both serialize successfully before
  cutoff.
- These 17 public-flow cases execute native timing contract v2 only and assert that label explicitly;
  they do not execute migration or claim direct v1 coverage. Migrated timing-v1 preservation is
  proved by the existing [Gateway migration contract](../../services/db-gateway/tests/unit/test_mp07_migration_contract.py),
  including a null acceptance window and acceptance cutoff equal to the historical bidding cutoff,
  with additional classified-history coverage in the
  [legacy-effect migration proof](../../services/db-gateway/tests/unit/test_mp07_legacy_effect_migration.py).
  Keeping those proofs separate prevents native poster-selected windows from silently extending a
  legacy cutoff or rewriting immutable accepted terms.

The reconciled tests use one injected clock for Task Board timestamps and
`db_gateway_service.task_time.utc_now_us`, assert exact stored epoch-microsecond cutoffs, and give
every native bid, acceptance, and deadline command an explicit operation identity, expected version,
and configured actor. Clock shims retain real parsing and cutoff rendering through delegated
`fromisoformat` and `fromtimestamp` methods.

Race READY/RELEASE gates move to the contender's actual
`TaskDbClient.claim_task_operation` boundary. Pausing after `evaluate_deadline` is no longer valid
because the native evaluator claims during that call. The new gate observes the expected source
task and command immediately before the Gateway claim, advances the shared clock without sleeps,
lets the intended competing claim fully commit, and then releases the stale contender.

Every financial deadline winner persists a real Gateway Bank receipt, ledger leg, escrow resolution,
completed Board operation, and one semantic event. Losing commands return a typed 409 ownership or
version conflict and create no operation, receipt, Court claim, bid, counter increment, accepted
term, or contradictory event. Pure bid and acceptance winners remain effect-free and replayable.

All runs in this reconciliation are retained under
`/private/tmp/agent-economy-mp07-window-bid-*.log`. These tests remain MP-07 proofs; receipt-grade
Court rebuttal replay and later recovery scheduling are outside this slice.
