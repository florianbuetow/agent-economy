# MP-07 lifecycle-router reconciliation

The untouched submission and lifecycle router run collected 30 tests: 25 passed and five failed.
Three failures were obsolete error expectations. Native submit and bid commands now enforce their
stored cutoff inside the Gateway transaction, so a command attempted after equality returns
`409 cutoff_reached`; it does not require a prior lazy sweep to relabel the task and return
`invalid_status`. Two failures were direct cancellation payloads that omitted the now-required
operation ID and expected lifecycle version. These are fixture-contract mismatches, not evidence of
an incorrect lifecycle transition.

The precise replacements are:

- Late bid and late submission first prove `cutoff_reached`, unchanged source state, and no external
  effect. A subsequent read may run the automatic deadline command, which must persist the exact
  deterministic operation, Bank receipt, escrow refund, restored poster balance, and one expiry
  event.
- Direct cancellation tokens include a fresh operation ID and the task's currently observed lifecycle
  version before signing. Terminal-state mutations still return `invalid_status`; this change only
  admits the request to the native command boundary.
- Native timing-v2 tasks with bids do not expire at bidding close. Their stored acceptance cutoff is
  bidding cutoff plus the explicit poster window. Historical timing-v1 cutoff equality remains a
  separate Gateway migration contract and is not inferred by these router tests.
- Successful approval, autoapproval, cancellation, expiry, and ruling assertions use the fixture's
  real Gateway effects. They verify completed Board operations, matching events, receipts or split
  receipts, ledger legs, escrow state, and balances rather than treating a bare successful mock body
  as finalization evidence.
- If an external effect is unavailable, the native claim retains source business state and the same
  operation identity for recovery. These two files contain no such transport-failure scenario; they
  do not invent a completed task for one. Dedicated effect-recovery tests remain authoritative for
  same-ID recovery behavior.

The shared router fixture already supplies explicit poster windows, native operation fields, owned
Gateway persistence, fixed deadline authority, and persisted Bank/Court effects. It is consumed
read-only. Verification logs and source hashes are retained under
`/private/tmp/agent-economy-mp07-lifecycle-router-*`.

Independent review found a pre-existing `approved`/`completed` compatibility allowance in the
autoapproval response assertion. The parent tightened it to exactly `approved`, matching the
current status contract. The subsequent 66-case review-fix group and full 357-case router gate pass;
the parent's archived source snapshot records this final version, while earlier author logs and
hashes remain explicitly historical evidence.
