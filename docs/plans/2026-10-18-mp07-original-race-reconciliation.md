# MP-07 original race-test reconciliation

The original `test_mp07_atomic_task_transitions.py` scenarios remain behavioral proofs of
serialization across two independent Task Board managers sharing one live Gateway. Their required
outcome is unchanged: exactly one contender wins, the loser receives a typed 409 ownership or
version conflict, one lifecycle transition and event are persisted, and no contradictory Bank or
Court effect is created.

The pre-reconciliation run collected three tests and failed all three during setup. Each task
publication was rejected with `400 invalid_payload` because the signed payload omitted the now
required poster-selected `acceptance_window_seconds`. No race, transition assertion, Bank effect,
or Court effect ran, so those failures are fixture-contract drift rather than preserved product
failures.

The fixture contract is reconciled as follows:

- Every task supplies an explicit positive acceptance window.
- Bid, acceptance, submission, approval, dispute, and cancellation tokens carry stable operation
  IDs and the exact expected lifecycle version before signing.
- The two race contenders still pause after reading the same source status through separate real
  `TaskDbClient` instances, then claim against the same live Gateway.
- Each constructed deadline evaluator receives the configured system actor explicitly.
- Bank winners persist the real Gateway receipt under the Board operation ID before finalization.
  Court winners persist the exact claim and parties in the Gateway before finalization. Success-only
  mocks and call counts are not settlement or adjudication evidence.
- Assertions retain the original single-winner, single-event, and no-contradictory-effect checks,
  and add the persisted operation, receipt or claim, escrow outcome, and lifecycle-version facts
  required by the native contract.

The acceptance-versus-acceptance race has no external effect. The acceptance-versus-cancellation
race persists a refund receipt only when cancellation owns the claim; an acceptance winner leaves
the escrow locked. The approval-versus-dispute race persists either the worker payout receipt or
the Court claim, never both. These reconciliations do not broaden MP-07 into receipt-grade Court
rebuttal replay or MP-10 adjudication work.

## Original deadline-race reconciliation

The untouched `test_mp07_deadline_transition_races.py` run collected three scenarios and failed all
three before task setup. Its clock installer still tried to patch `task_bidding.datetime`, a seam
removed when bid eligibility moved into native Gateway commands. The resulting `AttributeError`
means none of the submit/expiry, approve/autoapproval, or dispute/autoapproval races executed; it is
fixture drift rather than product-red evidence.

The old post-evaluation gate is also no longer an honest synchronization point. Native manual
commands claim directly at the Gateway, and the current deadline evaluator claims during
`evaluate_deadline`; pausing after evaluation would pause after ownership had already been decided.
The reconciled READY/RELEASE gate therefore sits on the contender's real
`TaskDbClient.claim_task_operation` boundary. It confirms the source task and expected command, then
pauses immediately before the Gateway claim. The test advances one shared injected clock from one
microsecond before the stored cutoff to exact equality, lets the deadline actor claim and finalize,
then releases the stale contender. No sleeps determine ordering.

The shared clock patches Task Board timestamp consumers and `db_gateway_service.task_time.utc_now_us`,
so publication, frozen cutoffs, manual claims, and automatic claims use the same epoch-microsecond
source. Every setup and contender command carries its native operation ID and expected lifecycle
version, task publication supplies an acceptance window, and every evaluator receives the explicit
configured deadline actor.

Deadline winners persist their actual Bank receipt, ledger leg, escrow resolution, completed Board
operation, and single semantic event in the shared Gateway. Losing manual submissions, approvals,
and disputes must return a 409 ownership/version conflict and create no operation, receipt, Court
claim, or contradictory task event. The exact stored execution or review cutoff must equal the
injected clock at the automatic transition; equality belongs to expiry or autoapproval.
