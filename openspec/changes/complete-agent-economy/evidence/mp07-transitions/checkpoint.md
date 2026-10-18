# MP-07 transition race proof

Status: T-122/T-123 and tasks7.1–7.4 remain OPEN. Six approved race cases are independently
red-proven. Bid/cutoff, acceptance-window, replay/authority/effect and migration obligations
remain before the relevant production implementation. No production code has changed.

Baseline: a6af0bed5e0475f63b271329bb7f8ea683da9bc3, the root19-verified checkpoint, committed
2026-10-17 at21:57:16 CEST and pushed to origin/refactor with confirmation at21:57:43 CEST.
Amsterdam time was checked immediately before each permitted weekend action; no branch switch,
merge, rebase, cherry-pick or force push occurred. The working tree was clean after that push.

The NEW integration test was then installed from its independently reviewed draft. Only its
introductory docstring changed during installation; no production code changed. Its source and
raw parent result are retained with SHA-256 in initial-proof-index.json.

Command from services/task-board:

```text
UV_CACHE_DIR=/tmp/agent-economy-review-uv UV_FROZEN=1 uv run --no-sync pytest tests/integration/test_mp07_atomic_task_transitions.py::test_concurrent_approve_and_dispute_have_one_serial_winner -vv --tb=short --color=no
```

Parent session27361 is TERMINAL1: one intended failure in0.67s. Both independent Board runtime
graphs performed real Gateway reads of submitted before a controlled barrier released them.
Both actions succeeded. The real persisted history contains task.approved and task.disputed;
the final task has both approved_at and disputed_at, and approval returned status disputed.
Bank release and Court claim boundaries were each invoked once. The required one-success
assertion failed with actual2; this is not a setup, import, timeout or authorization failure.
The task_not_found log during initial create preflight is not the failing assertion.

The test uses real Gateway persistence and signed Board setup. Bank/Court calls are recording
boundaries; it proves conflicting attempted effects, not actual duplicate payment or Court
persistence. Non409 operation errors and thread/barrier failures are explicitly classified apart
from the intended red. One ExitStack protects all allocated clients and every later failure path.

The technical contract in docs/plans/2026-10-17-mp07-transition-contract.md passed critical review.
Its first-test source review and cleanup follow-up are APPROVED. The new test is intentionally
failing in the working tree; root19 describes the earlier verified checkpoint, not this new red
test state. Do not claim the full current working tree or T-122 is complete.

## Expanded six-case proof

The atomic test file now covers approve/dispute, competing acceptances and accept/cancel.
Parent session14225 is TERMINAL1: three intended assertion failures in1.78s. Each competing
action returned success. Persisted histories contain both conflicting semantic events; the
accept/cancel history also attempted a refund. Independent review APPROVED the three cases;
the only follow-up was a module-docstring correction before this final parent run.

The separate deadline file covers stale submit versus execution expiry, and stale manual
approve/dispute versus autoapproval. One shared locked domain clock advances from cutoff-1us
to exact equality only after real contender validation. The deadline actor completes before
the contender resumes. Parent session93980 is TERMINAL1: three intended failures in1.23s.
Submission succeeded after expiry and produced both events; manual approval attempted Bank
after autoapproval; dispute attempted Court after autoapproval. All fail the explicit409 loser
assertion, with no setup, authorization, barrier or timeout failure. Source and evidence review
is APPROVED. Author Ruff/format/Pyright passed, with no new suppressions; author deadline
controls passed15. The latter control run has not yet been independently repeated by the parent.

Both parent commands used UV_CACHE_DIR=/tmp/agent-economy-review-uv UV_FROZEN=1, from
services/task-board, with `uv run --no-sync pytest <file> -q --tb=short --color=no`.
Exact sources and byte-preserved logs are indexed in six-races-proof-index.json. Earlier
initial-proof-index.json remains the immutable first-case record. Bank/Court recording doubles
do not prove actual duplicate settlement or persisted Court claims. The injected task-domain
clock currently exercises Board seams; authoritative Gateway clock predicates remain unimplemented.

Next: complete bid admission and poster-selected acceptance-window tests, then command replay,
authority, effect-evidence and schema/migration proofs. The finalization contract now requires
Gateway-owned persisted Bank/Court facts, not a forwarded success body; that extension awaits
its independent review and test coverage. The full goal remains ACTIVE.

## Final combined baseline tranche

The earlier next-step paragraph is superseded by this combined checkpoint. The final contract
extension passed architectural review. Boundary-test review findings were repaired and approved;
window/expiry review has no blockers after specific error assertions and the stale comment fix.
No production source changed during this tranche. The final nine sources and two combined parent
logs are byte-preserved in final-baseline-proof-index.json; earlier indexes remain historical.

Parent10809 is TERMINAL1:24 product assertion failures and19 controls passed in9.57s. This
includes all six new Board integration files and15 existing deadline/precision controls, so
their shared-process fixture/clock restoration was exercised. The new accept/expiry case proves
acceptance overwrites already-committed expiry at the selected acceptance cutoff, leaving both
events after a recorded refund. Public retries preserve IDs but currently fail to replay; changed
and cross-command reuse/version predicates are missing. Q-M4 tests prove missing/invalid duration
admission, lost duration/deadline projection and premature bid-bearing expiry. Early acceptance,
zero-bid equality expiry, independent bidders and bid-after-acceptance rejection are controls.

Parent83232 is TERMINAL1:3 capability assertion failures and2 controls passed in0.76s. These
represent TWO missing capabilities: explicit v2-to-v3 migration (asserted by two suites) and
native operation claim/finalize routes/policies. The valid-v2 fixture and real signed Gateway
writer controls pass. Post-capability migration preservation/rejection and claim/finalization
assertions remain prepared but unexecuted, not demonstrated behavior failures. There are no
required skips or xfails. Existing Starlette/httpx deprecation is the only reported warning.

Review repairs strengthened live asset/key preservation, exact version/error responses,
immutable replay timestamps, separate Board versus Bank event accounting, distinct Board-capable
caller KIDs, durable blocked-effect checks and consistent fixture review cutoffs. All new files
have passed author/static checks; parent checks also cover the revised files. No root gate was
rerun on intentionally red tests, and no commit or push followed a6af0be.

Remaining requirements and fixture evolution are explicit in proof-matrix.md. In particular,
native finalization cannot be made to trust a recording double: its successful test boundaries
must produce real Gateway evidence. The same domain clock must reach future Gateway transaction
predicates. Legacy pending-effect observation tests are now assigned to the test-only owner;
Court replay/linkage, active-claim admission, concurrency and remaining migration obligations
still precede their respective implementation/closure. The entire Core and Vision goal stays ACTIVE.

## Legacy observation source histories

The legacy migration suite adds three separately executed source-history controls: persisted
worker settlement with missing Board finalization, approved/escrow_pending with still-locked
escrow and a provable provisional retry reference, and an exact persisted Court claim with
still-submitted Board state. Each proves exact task/bid/escrow/receipt/claim facts, current-v2
classification, money/receipt ownership, FK validity and a fresh verified backup AFTER fixture
construction. Court status is the actual rebuttal_pending value. Removed fixture Court rows
have their unowned Court events removed too; financial ownership/attestations remain intact.
Earlier author fixtures using filed or retaining stale Court events are superseded fixture work,
not product defect evidence.

Parent47620 is TERMINAL1: one missing-migration capability failure and three controls pass
in1.02s. Post-migration observation/preservation assertions are unexecuted. Source/log hashes
are in legacy-effects-proof-index.json. This adds three controls to the prior21, and one more
assertion of the SAME missing migration capability, not a new independent migration defect.
Final independent review is pending. The concrete narrow observation column names and diagnostics
are recorded in the technical contract; no native caller/actor/command/client-operation history
is fabricated. No production changes or new Git checkpoint have occurred.

Final legacy review required exact observation columns and protection against invented provenance
inside fact JSON, not just column names. The repaired test has exact flat per-kind fact schemas,
full observed-fact equality and recursive provenance rejection. Three validator controls accompany
the three source-history controls. Parent32472 is TERMINAL1:1 expected missing capability failure,
6 passes. Independent review is APPROVED with no remaining findings. Exact final sources/logs are
in legacy-effects-final2-proof-index.json; earlier legacy index is superseded only for assertion
strength, with raw history retained. The technical contract uses the same exact fields/keysets.

All current test authors/reviewers are finished and all parent handles are terminal. The ten new
test files remain intentionally red and uncommitted. The current tranche has24 exercised product
assertion failures, four assertions of the same two absent interfaces,24 behavior/source/fixture
controls and3 additional validator controls. No migration or post-claim behavior hidden behind a
capability gate is claimed executed. Remaining work is the full proof-matrix.md, production fixes,
consumer reconciliation, independent review and fresh affected/root gates; no package is closed.
