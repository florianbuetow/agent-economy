# MP-07 requirement audit — verified complete at Root22

The full Core and Vision goal remains active. This audit maps MP-07 evidence; it
does not replace the remaining masterplan. Root19 certifies
the preceding checkpoint only. Root20 failed at a positive fixture missing required
Gateway authority. Its inputs stayed unchanged. Root21 passed all component CIs but failed one remaining legacy Board integration fixture,
now reconciled and reviewed. Root22 has passed with1059 unchanged source/config files,
202 unchanged ordinary logs and verified process cleanup. Final independent item
closure review returned PASS with no gaps; see `../root22-checkpoint.md`.

## Current integration evidence

- Parent native Task Board matrix: **63 passed**, exact indexed log
  `/private/tmp/agent-economy-mp07-board-native-matrix-final.log`, session22381 exit0.
- Parent public SDK/autonomous/crash integration: **104 passed**, exact indexed log
  `/private/tmp/agent-economy-mp07-agent-live-final.log`, session17162 exit0.
- Parent autonomous acceptance plus unattended approval/dispute: **2 passed**,
  `/private/tmp/agent-economy-mp07-autonomous-final.log`, session27218 exit0.
- Parent public cancellation plus approval after both receipt fixes: **2 passed**,
  `/private/tmp/agent-economy-mp07-receipt-live-confirm.log`, session5813 exit0.
- Parent agent CI before the later E2E cleanup/crash additions:765 passed,
  3 pre-existing credential-gated provider skips,94 E2E deselected in that run.
  The author's subsequent agent CI passed767 with3 skips and104 E2E deselected.
  These skips do not prove the required real Court profile.

These runs overlap and their counts must not be added as distinct coverage.
The first process-crash harness passed the live suite but independent review
found failure-cleanup and semantic-proof gaps. The repair is independently
approved on final hashes, and parent session75006 exited0 with ten live cases
passing in9.05s. The104-pass log alone does not certify those later harness edits.

Parent Gateway and Task Board component CI now both exit0 after their remaining
positive settlement fixtures were reconciled. All406 scoped source/test/SQL and
202 ordinary-log hashes stayed identical. Cross-service native fixtures are
independently approved with26 parent passes. The185-case runtime gate passed181
and failed four at one old public workload's missing native SDK arguments. Its
reviewed repair preserves earlier migration proofs and adds a separately verified
v2-to-v3 phase; all four pass independently. Root20 then exposed a missing positive
Gateway authority config. That fixture passes15 parent contract/cleanup tests after
repair. Three other authority fixtures and the Bank historical-migration fixture
were reconciled before root21; parent Identity integration7 and Bank integration25 pass.
Root22 subsequently passed all component gates, Board integration65, agents770,
tools202, cross/runtime211, browser298 and live-agent104. Its three existing credential
skips do not supply real judge-profile evidence. All task rows below now have the
universal gate and final independent closure confirmation. Only these eight task
items and their six named tickets close; the full goal remains active.

## Eleven-command history matrix

All paths below are under `services/task-board/tests/integration/` and executed
in the fresh63-case matrix. Each uses production Gateway persistence. Financial
fault adapters persist real Bank receipts; the separate104-case run additionally
crosses the real public Bank/Court service seams.

| Command | Primary history and effect/event proof |
| --- | --- |
| `submit_bid` | `test_mp07_public_command_replay.py`: original bid after lost response, changed amount/operation conflicts; `test_mp07_bid_admission_races.py`: accepted/cutoff exclusion and independent bids |
| `accept_bid` | `test_mp07_public_command_replay.py`: exact accepted terms; `test_mp07_atomic_task_transitions.py`: competing acceptance and cancellation; `test_mp07_acceptance_result_projection.py`: immutable result after submission |
| `cancel_task` | `test_mp07_native_public_lifecycle.py`: claim before refund, exact receipt, one cancellation event and replay |
| `expire_bidding` | `test_mp07_native_deadlines.py`: authoritative zero-bid predicate, exact cutoff, native refund and one expiry event |
| `expire_acceptance` | `test_mp07_native_deadlines.py`: bid-present selected window, native refund and one expiry event; `test_mp07_accept_expiry_race.py`: stale acceptance loses |
| `submit_deliverable` | `test_mp07_native_public_lifecycle.py`: original submission result after response loss; `test_mp07_deadline_transition_races.py`: submission loses at expiry |
| `expire_execution` | `test_mp07_native_deadlines.py`: fixed-reward refund and one expiry event; `test_mp07_deadline_transition_races.py`: valid serial winner |
| `approve_task` | `test_mp07_native_public_lifecycle.py`: claim-before-effect and one fixed-reward receipt; `test_mp07_native_effect_recovery.py`: missing receipt, failed delivery and lost acknowledgment |
| `auto_approve` | `test_mp07_native_deadlines.py`: exact review cutoff and fixed-reward payout; `test_mp07_deadline_transition_races.py`: poster review cannot beat cutoff |
| `dispute_task` | `test_mp07_native_court_handoff.py`: persisted claim, actual claim identity, missing/mismatched evidence and response loss; `test_mp07_atomic_task_transitions.py`: approval/dispute serial winner |
| `record_ruling` | `test_mp07_native_ruling_handoff.py`: native claim, exact receipt, changed terms and foreign ruling rejection |

## Numbered task assessment

| Task | Evidence assessment | Closure evidence |
| --- | --- | --- |
|7.1 / T-122| Six required barrier races and extra bid races pass in the63-case run; historical red classifications remain in the existing evidence bundles. | Verified at Root22; independent final audit PASS. |
|7.2 / T-123| Native predicates/claims/finalization and write exclusions are implemented. Both actual producer mismatches are repaired with preserved negative controls, real SDK success and green affected component/cross/runtime gates. | Verified at Root22; independent final audit PASS. |
|7.3 / T-123| Explicit poster windows, early acceptance, bidding equality, zero-bid expiry, selected cutoff, immutable terms and feeder forwarding/Q-16 tests pass. | Verified at Root22; independent final audit PASS. |
|7.4 / T-123| Native API amendment and the eleven-command matrix above exist. Actual Bank provenance and optional task metadata are documented. | Verified at Root22; independent final audit PASS. |
|7.5 / T-181| Owner/lease/filesystem/atomic write/ACK/caller tests pass. Reviewed failure-safe process ownership and exact prepared semantics pass ten parent SIGKILL histories on the final harness. | Verified at Root22; independent final audit PASS. |
|7.6 / T-182| Caller journal, exact replay, typed SDK, leased startup/cleanup, crash proof and explicit feeder window are implemented with independent bounded approvals and green cross/runtime proof. | Verified at Root22; independent final audit PASS. |
|2.15 / T-179| Original cause-specific configuration reds cover17 paths. Missing Gateway1/Court3/Board unit2 outputs were recovered byte-for-byte from original execution records and independently verified against source/proof-addition ordering. | Verified at Root22; independent final audit PASS. |
|2.16 / T-180| All17 configuration probes pass; bounded live runs and failed Root20 preserve202 ordinary logs without additions, removals or byte changes. Prior flawed-run log contents remain intact. | Verified at Root22; independent final audit PASS. |

## Preserved limits

Posting intents and compensation remain MP-05; general pending-effect scheduling
and operator recovery remain MP-08; asset publication/manifests remain MP-09;
durable Court evidence/verdict recovery remains MP-10/11. External onboarding,
worker execution/assets/rebuttals/discovery and feeder oracle/posting recovery
remain MP-16/17/18. A local identity lease is not distributed across hosts, and
an internally valid historical journal rollback cannot be detected across new
processes without an external trust anchor. No broader recovery claim is made.
