# MP-07 atomic task-transition contract

Status: technical contract from the independent MP-07 pass, revised after the critic rejected
ambiguous migration and delivery semantics. Critic re-review is OKAY for T-122 test implementation.
T-123 remains gated on the required red proofs; this is not production completion or policy change.
MP-04 passed root19 and was committed/pushed as a6af0be. Its source freeze is released.
This document does not close tickets or select Q-M1/Q-M2/Q-M3 policies.

## Accepted policy and current boundary

Each new task explicitly supplies its acceptance-window duration. Early poster acceptance stays
available; bids stop at the bidding cutoff; zero-bid tasks expire immediately at that cutoff;
tasks with bids remain acceptable until the selected window ends. Equality belongs to expiry.
Existing published/accepted cutoffs receive no silent extension. Q-16 governs feeder selection,
not human quorum or ranking. Core payout remains the posted reward, independent of bid amount.

Original v2 baseline: native financial receipts and schema v2 already exist. Task writes lack lifecycle
versions and acceptance windows. Gateway status updates accept optional constraints. Interactive
approval/ruling move money and dispute creation calls Court before a winning state transition.
Gateway authenticates the service but does not expose its verified caller KID to Board writers.

MP-07 owns atomic task claims, authoritative timing/actor/linkage predicates, immutable accepted
terms, exact command replay, persisted effect intent and synchronous finalization. MP-08 adds
background retry/leases/diagnostics independent of task reads. MP-05 owns posting recovery,
MP-09 immutable assets and quotas, and MP-10 receipt-grade Court delivery/adjudication.

## Proposed schema v3

Freeze `current_v2.sql`. Preserve existing domain tables/indexes/rows/rowids. Replace only
`schema_metadata` with version3 and add the tables below. Require explicit verified-backup-bound
`migrate-v2-to-v3`; never chain migrations at startup. Recognize v0/v1/v2 as migration-required.

### Task contract sidecar

`board_task_contracts`, one row per `board_tasks` row:

| Column | Proposed contract |
| --- | --- |
| task_id | Non-null text primary key and FK to task |
| lifecycle_version | Nonnegative integer, initially0 at cutover |
| timing_contract_version | 1 migrated timing; 2 new poster-selected timing |
| acceptance_window_seconds | Null for version1 (not historically supplied); explicit positive integer for version2 |
| bidding_cutoff_us | Nonnegative integer epoch microseconds |
| acceptance_cutoff_us | At least bidding cutoff |
| execution_cutoff_us, review_cutoff_us | Nullable until their phase begins, immutable afterward |
| accepted_reward, accepted_bid_amount | Both null before acceptance or both explicit integers |

Version1 uses acceptance cutoff equal to the original bidding cutoff. Version2 uses bidding
cutoff plus explicit window with checked integer arithmetic. Acceptance freezes task reward and
the chosen bid's amount after proving task/bid/worker linkage. Fixed reward still drives payout.

### Task operations

`board_task_operations` proposed fields:

- Canonical lowercase `op-UUID` primary key; operation contract version1; native v3 commands only.
- Task FK, command, canonical JSON object and SHA-256.
- Non-null verified `caller_kid`; actor kind (agent/platform/system) and ID.
- Source and target status/lifecycle versions.
- Operation state: claimed, blocked or completed.
- Effect kind: none, bank_release, bank_split or court_claim.
- Effect state: none, pending, succeeded or conflict; effect operation ID and result reference.
- Claimed/completed epoch-microsecond timestamps, event FK and result JSON.

A partial unique index allows at most one claimed or blocked operation per task. Add task/operation and
state/effect indexes. Native commands require actor/caller/source/target facts. Pure commands
complete atomically. Claimed external effects retain immutable intent and initially null event
and result; finalization stores the original event/result. Financial effect ID equals Board
operation ID. Court result reference is its claim ID. No migrated observations enter this native
table. Store a safe typed error for permanent effect conflicts; blocked claims retain ownership
until explicit reconciliation, never silently return to ordinary pending retries.

`board_tasks.escrow_pending` is retained to preserve old rows but becomes only a projection;
the native claimed operation is authoritative.

### Legacy effect observations and migration audit

Do not add a transition-cutover singleton or hash mutable task/operation rows. Existing
schema_metadata binds migration to the verified backup. Keep plan/source hashes in its evidence
artifact. Migration and startup audit require exactly one sidecar per task, no orphan sidecar,
and valid linkage/cutoffs. New task publication inserts task and sidecar in one transaction.
Version0 is the explicit historical lifecycle boundary; it is not a reconstructed transition count.

Add a narrow `board_legacy_effect_observations` table only for unresolved cutover anomalies.
Each row has a migration observation ID (not a client op-UUID), task FK, observation kind,
immutable exact observed-fact JSON/hash, existing external receipt/claim reference, effect state,
target-outcome hint and unresolved/resolved flag. Mutable resolution metadata is outside the
observed-fact hash. There is no caller KID, actor, native command hash or replayable client
operation ID. Normal terminal history needs no copied operation record. Existing provisional
financial IDs are external references, not invented Board command identities. Missing provenance
stays absent. Native commands on an unresolved observation return legacy_reconciliation_pending.
MP-08 owns later convergence of these observations; ambiguous contradictory histories fail
migration with actionable diagnostics instead of choosing a winner.

The concrete observation fields are observation_id, task_id, observation_kind,
observed_fact_json, observed_fact_sha256, external_reference, effect_state,
target_outcome_hint and unresolved. Observation IDs use the obs- namespace, never native op-.
The initial kinds are worker_settlement_succeeded, worker_settlement_pending and
court_claim_succeeded, with succeeded/pending effect state and approved/disputed outcome hints.
The external reference is an existing receipt/claim ID or a provable old deterministic settlement
retry reference; it is not a recovered caller-owned Board command identity. Canonical observed-fact
JSON and its SHA-256 bind the actual source facts. Do not infer manual approval versus autoapproval.
Further cancellation/expiry/ruling legacy kinds require their own exact source-history proofs.

V3 has exactly those nine observation columns. Observed facts use closed, flat per-kind schemas;
they cannot hide invented native provenance in extra columns or nested JSON. Every kind records
task_id, task_status, poster_id, worker_id, escrow_id, reward, accepted_bid_id,
accepted_bid_amount and external_reference. The kind-specific additions are:

- worker_settlement_succeeded: escrow_status, resolved_at, recipient_id, settled_amount,
  settlement_source_reference, external_receipt_request_sha256, external_receipt_event_id and
  external_receipt_transaction_id.
- worker_settlement_pending: escrow_status and escrow_pending.
- court_claim_succeeded: claimant_id, respondent_id, reason, claim_status, filed_at and
  rebuttal_deadline.

Each value must equal the actual observed source fact. Recursively reject operation_id,
client_operation_id, caller_kid, actor_kind, actor_id, command, command_sha256 and request_sha256
in fact JSON. Genuine financial receipt evidence remains explicitly external; it does not claim
that the old task transition had a native Board operation. Resolution flips the separate unresolved
flag; any later richer recovery metadata belongs to the separately tested MP-08 contract.

Migration failures use source_not_v2 for a source other than exact v2; inconsistent timing,
accepted bid/worker linkage and disputed claim linkage use task_cutoff_inconsistent,
accepted_bid_linkage_invalid and dispute_claim_linkage_invalid with safe task_id details.
These diagnostics do not authorize a repair or destructive reconciliation.

## Commands and signed protocol

Commands: submit_bid, accept_bid, cancel_task, expire_bidding, expire_acceptance,
submit_deliverable, expire_execution, approve_task, auto_approve, dispute_task, record_ruling.

Public mutation JWS carries explicit caller-owned operation ID, expected lifecycle version,
task ID, action and all command-specific fields. Retries never allocate replacement IDs.
Deadline IDs derive from task, command, source lifecycle version and frozen cutoff.

Gateway command includes protocol version, operation ID, task ID, command, expected status/version,
verified actor kind/ID and exact arguments. Canonical hash covers those semantic fields except
operation ID; it excludes transport JWS metadata, server timestamps, event IDs and current views.
Reject unknown fields and coercions before hashing. Bind amount/parties, selected bid, escrow,
cutoff, reason, ruling percentage/summary as relevant; compare them against stored facts inside
the authoritative transaction.

Gateway receives the actual verified service KID from middleware. Only the configured Task Board
capability can invoke Board commands. Task Board verifies public agent/platform signatures;
Gateway trusts that authenticated service to forward the verified actor and separately enforces:

| Command | Required domain actor |
| --- | --- |
| submit_bid | Bidder, distinct from poster |
| accept/cancel/approve/dispute | Task poster |
| submit_deliverable | Assigned worker |
| expiries/auto_approve | Fixed Task Board deadline system actor |
| record_ruling | Verified configured shared platform signer; matching task/dispute/worker linkage |

Public routes cannot accept a caller-declared system actor. KID names are configuration identities,
not hardcoded authority granted by a display name. Gateway takes caller_kid only from verified
middleware state, never JSON. The fixed deadline actor is accepted only through the configured
Task Board Gateway capability. Current record_ruling proves the shared platform signer, not a
Court-specific signing identity; require ruling_id equal to the task's dispute_id and the exact
task/claim/worker linkage. Court-specific provenance remains MP-10.

## Atomic claims and effects

Within `BEGIN IMMEDIATE`, resolve exact operation replay first. Same ID/hash returns original
result or pending envelope; changed hash conflicts. A different operation cannot take over a
claimed task. Stale version, wrong status, unauthorized actor, broken linkage or cutoff failure
reject before external calls. Suggested typed conflicts: operation_id_conflict, transition_pending
(winner ID), task_version_conflict, invalid_status and cutoff_reached.

Pure lifecycle commands atomically advance status/version, complete the operation and emit one
event. Bid insertion completes its own operation/event without advancing lifecycle_version.
An external-effect claim leaves status/version at source N and records target status/N+1 before
calling Bank or Court. Every lifecycle, bid, asset-metadata, rebuttal and phase-admission writer
checks active native claims and unresolved legacy observations before admitting another action.
Generic status writes cannot bypass these rules. Only the matching finalizer changes status/version,
stores validated effect/result and emits the single semantic transition event.

Exact replay of a claimed pending operation resumes its stored effect intent with the same external
ID; it does not merely return pending forever. Concurrent same-operation deliveries rely on the
Bank receipt or Court unique-task protocol below. Finalizers CAS the operation; a loser reads the
stored completed result. A proven permanent effect conflict becomes blocked/conflict durably.
Ordinary unavailable/ambiguous effects remain claimed/pending for replay and later MP-08 scheduling.
Reads retain source business status and expose nullable pending_transition with operation_id,
command, target_status, target_lifecycle_version, operation_state and effect_state. Unresolved
legacy observations are returned separately in legacy_reconciliations, sorted by observation_id,
with observation_id, observation_kind, effect_state and target_outcome_hint. Neither projection
includes raw observed facts, caller identity or invented native provenance. A task cannot expose
both a native pending transition and unresolved legacy observations as competing recovery owners.

Gateway get/list return the nine task-contract fields alongside these two recovery fields in one
SQLite snapshot. The Task Board client requires every field and rejects malformed or inconsistent
responses. Public detail/list projections preserve them and render bidding, acceptance, execution
and review ISO cutoffs from the stored integer microseconds, not mutable timestamp/duration pairs.

### Finalization uses Gateway-owned evidence

The finalizer receives only the operation ID and the actual middleware-verified caller KID.
It loads the operation, task contract and persisted Bank/Court evidence inside the same
`BEGIN IMMEDIATE` transaction. A forwarded HTTP success body is not durable evidence and cannot
authorize finalization. The configured Board capability and the original verified operation KID
must match; another KID cannot take over an operation implicitly. Cross-key recovery requires a
later explicit contract. Public JSON cannot supply or override the authenticated caller.

The operation must still own its claim, with the recorded source status/version and target
version. Missing effect evidence leaves the claim pending. Proven contradictory evidence blocks
it with a typed conflict and no task/event transition. Matching evidence finalizes task, operation
and one semantic event atomically. A concurrent finalizer or exact replay reads the same stored
completed result. That immutable command result is distinct from a later current task view.
Eligibility was established at claim time: do not reject an already-owned claim merely because
its cutoff passes while its external effect is completing.

For Bank effects, load the native receipt by the Board operation ID. Recompute the financial
canonical request from stored intent and frozen terms, using the existing financial request
contract. Require exact version, kind, hash, source reference, escrow, parties, percentage and
logical amount/ledger legs. Validate the escrow task/payer/amount and resolved outcome against
the receipt. Approval/autoapproval pays the worker; cancellation/expiry returns funds to the
poster; ruling zero/full shares release to the poster/worker, and a partial share splits. Payout
uses accepted_reward after acceptance and published reward before acceptance, never bid amount.
A different native settlement receipt owning that escrow is a permanent winner-referencing conflict,
even when this operation's receipt is absent. Historical observations cannot impersonate a
native receipt for a native command.

For a dispute, load the unique persisted court_claims row by task and compare exact task,
claimant/poster, respondent/frozen worker and reason. Persist its actual claim ID and needed
rebuttal cutoff. Court does not persist escrow; independently validate the Board's frozen escrow
link, payer, amount and locked/unresolved state. A matching claim with inconsistent escrow cannot
finalize. For record_ruling, require task.dispute_id, command.ruling_id and court_claims.claim_id
to agree, validate the exact task/parties, and require the matching Bank receipt. Do not require
a court_rulings row in MP-07: the current Court calls Board before persisting that row. Persisting
an immutable Court decision before delivery belongs to MP-10.

Required negative/control proofs include missing versus mismatched evidence, unrelated receipts,
bid amount versus reward, foreign escrow winners, wrong Court parties/reason/linkage, forged
forwarded success, unauthorized finalizers, changed replay, concurrent finalization, valid ruling
delivery before court_rulings persistence, and claim completion after its original cutoff.

### HTTP boundary for claim and finalization

The reviewed narrow Gateway boundary is `POST /board/task-operations`, authenticated with
the existing gateway.board.write capability. Its exact top-level fields are protocol_version1,
operation_id, task_id, command, expected_status, expected_lifecycle_version, actor_kind,
actor_id and arguments. Each command has an exact argument key set; reject coercions and
unknown fields. Hash all these fields except operation_id. No event, caller_kid, server
timestamp, external success body or mutable current view is accepted in the command.

Resolved arguments bind the existing task facts within the transaction: acceptance includes
bid/worker/escrow/reward/bid amount; approval includes poster/worker/escrow/reward/source
reference; dispute includes poster/worker/escrow/reward/reason; ruling adds ruling ID,
percentage and summary. Automatic actions include their frozen cutoff and use the fixed
system actor. The Gateway derives the effect from that validated command.

Pure completion and exact completed replay return200. An external-effect claim or exact pending
replay returns202. Both return a versioned operation envelope with operation_id, task_id,
command, request_sha256, operation_state, source_status/source_lifecycle_version,
target_status/target_lifecycle_version, effect_kind/effect_state, effect_operation_id,
effect_result_reference, effect_intent, event_id and result. Pending event/result are null.
Bank intent contains its canonical request and hash plus expected escrow task/payer/amount.
Court intent contains exact task/claimant/respondent/reason and the local frozen escrow
predicate; effect_operation_id and financial request hash are null because Court has no such
native receipt identity. A completed result contains task_id, command, status, lifecycle_version,
event_id and only relevant frozen command-specific fields. External-effect results additionally
contain effect_result_reference; pure-command results omit it and their envelope reference is null.

`POST /board/task-operations/{operation_id}/finalize` requires body exactly `{}`. Middleware
supplies the KID. It returns200 for completion/replay,202 for absent compatible evidence,
403 operation_caller_mismatch for a different Board-capable KID,404 operation_not_found,
409 effect_evidence_conflict for contradictory persisted evidence, and409
operation_not_finalizable for a pure or otherwise ineligible operation. A completed external operation
is replayable; that replay is checked before source task eligibility. Repeated blocked operations
retain the original safe conflict facts; they cannot resume as ordinary pending work.

The command route resolves existing identity before current status/version/cutoff: same hash
returns the original completed/pending/blocked result, changed hash returns409
operation_id_conflict. New commands validate active claims/legacy observations, task version,
state, actor, linkage and transaction clock. Existing error JSON shape remains error/message/details.
Other errors are400 invalid_payload or invalid_operation_id;403 forbidden;404 task_not_found
or bid_not_found;409 transition_pending, task_version_conflict, invalid_status, cutoff_reached
or legacy_reconciliation_pending. Pending/conflicting winner references use winning_operation_id
only when an actual competing operation exists.

Add both route templates to GATEWAY_ROUTE_POLICIES. Place methods on the production synchronous
TaskDbClient, not the shared GatewayClient that currently excludes Board. Preserve task-level
public response contracts while exposing operation identity and pending state explicitly; public
exact bid replay returns the original bid with201 and acceptance replay returns its accepted
command facts with200. Public authentication still runs on every retry, even when operation
replay precedes task eligibility checks.

The acceptance response keeps the current task at the top level and includes the original
validated Gateway envelope in a separate typed operation field. After submission, replay may
therefore show top-level status submitted/version2 while operation.result remains accepted/version1
with its original operation and event IDs. It never relabels the later current view as the
immutable acceptance result. Pure acceptance has no external effect intent. Other mutation
response envelopes remain part of their caller rollout; GET/list do not acquire a command result.

Creation samples one explicit timestamp before external effects, checks the composed bidding
and acceptance cutoffs against both integer storage and ISO datetime bounds, and uses that same
timestamp for publication. An unrepresentable acceptance window fails before escrow locking.

MP-05 supersession for new schema-v4 native postings: retain separate intent admission and
atomic publication timestamps. The latter starts the signed bidding/acceptance durations, so
invisible recovery time cannot consume them. Existing published timestamps remain unchanged;
see `2026-10-18-mp05-publication-contract.md`. The pre-effect rule above describes the v3
publication boundary verified by MP-07, not the new durable posting contract.

Public create-task validation retains the established duration error vocabulary: a missing
acceptance_window_seconds is400 invalid_payload; a supplied non-positive, boolean, non-integer
or unrepresentable duration is400 invalid_deadline. The message identifies that field. Both
reject before Bank or publication effects. A new bid at bidding equality is409 cutoff_reached.
Once expiry has completed, a stale acceptance with version0 receives409 task_version_conflict
with details expected_lifecycle_version0, actual_lifecycle_version1 and current_status expired.
Version mismatch is checked before status for a new command after active-claim/legacy admission.

## Version, bid and cutoff rules

The proposal keeps bids from incrementing lifecycle_version. Each bid transaction validates open,
no conflicting claim, and now strictly before bidding cutoff. Acceptance validates the selected
bid in its own transaction. Thus independent bidders need no version churn; a bid serialized
after acceptance is rejected. bid_count is not a version token.

Sample the injected Gateway clock once inside the transaction, as exact integer microseconds:

- Bid: before bidding cutoff.
- Accept: before acceptance cutoff with a valid existing bid; early choice remains legal.
- Bidding expiry: no bid EXISTS and now at/after bidding cutoff.
- Acceptance expiry: a bid EXISTS and now at/after acceptance cutoff.
- Submit: before execution cutoff; equality belongs to execution expiry.
- Approve/dispute: before review cutoff; equality belongs to auto-approval.
- Cancel: open; before bidding cutoff if no bid exists, otherwise before acceptance cutoff.

Determine existence with an authoritative EXISTS query in the same transaction, not cached bid_count.
Submission verifies at least one eligible worker asset in that transaction. These predicates apply
even if no sweep has run. Accepted reward/bid/worker and execution cutoff
are frozen atomically; submission freezes review cutoff. Later mutations cannot change terms.

## Identity, losses and Court handoff

Operation IDs are unique across native Board command kinds within Board's authoritative table.
Financial IDs are independently global across Bank kinds. A financial transition intentionally
uses one ID in both domains; an unrelated Bank receipt under that ID is a permanent effect conflict,
never an alias. No global cross-service registry is proposed.

Loss before/after claim commit is resolved by exact replay; pending replay resumes the same claim.
Bank response loss replays the same MP-04 operation and finalizes only from the validated receipt.
Loss after finalization returns original event/result. Changed content always conflicts.

Court generates its own claim ID and lacks receipt-grade request replay, but court_claims.task_id
is unique. After response loss, query by task and require zero or one result. If present, fetch the
full claim and compare persisted task ID, claimant, respondent and reason exactly. If absent, retry
the identical request; if the specific task-already-disputed conflict occurs, query again. Never
convert a generic duplicate into success. Matching facts bind the claim and permit finalization;
unavailable lookup stays pending, mismatching facts block the operation. Test delayed-first-request
versus retry with a barrier. Court does not persist escrow ID; its response reconstructs escrow
from current Board state. Validate frozen escrow locally, never claim Court observed/persisted it.
Receipt-grade Court hashing/operation propagation stays MP-10.

## Migration preservation and pending work

Migration requires a verified backup, maintenance exclusion and one transaction. Every task gets
a sidecar with lifecycle_version0 as a cutover token, not an invented historical transition count.
Legacy acceptance cutoff equals original bidding cutoff; execution/review cutoffs are preserved
or derived exactly from persisted phase timestamps/durations. Reject inconsistent absolute cutoffs
or unprovable accepted bid/task/worker linkage; never guess or extend timing.

Classify provisional MP-04 settlement IDs without inventing native Board history:

- Matching financial receipt and terminal task: preserve ordinary historical rows, no copied operation.
- Matching receipt but missing task finalization: immutable legacy succeeded-effect observation.
- Terminal escrow_pending with locked escrow and no receipt: legacy pending-effect observation
  with exact facts and the provisional external retry reference if it is provable.
- Resolved escrow without native receipt or attested history: fail migration.
- Posting compensation remains MP-05, not a lifecycle transition.

Matching task/Court history remains historical. A matching Court claim with still-submitted task
becomes a legacy succeeded-effect observation. Missing/mismatched disputed claims fail migration.
The old approved settlement identity is shared by poster approval and auto-approval: record only
the observed worker settlement/approved outcome, never guess command, actor or source version.
Unprovable intent stays absent and cannot authorize an automatic effect. MP-08 must resolve these
observations through a separate explicit internal recovery contract, not native client replay.

## T-122 proof and implementation ownership

Use separate Board instances and Gateway connections with explicit barriers. Assert valid serial
outcomes without forcing a particular winner. Required proofs:

1. Competing acceptances; accept vs cancel; accept vs applicable expiry.
2. Submit vs execution expiry; approve vs dispute; review actions vs auto-approval at equality.
3. Bid vs bidding close; two independent bids both succeed; bid after accepted/claimed task fails.
4. Losing claims emit no transition event and call neither Bank nor Court.
5. Claim/finalization response loss; exact replay; changed-content conflict; Bank receipt recovery.
6. Court observation binds only exact matching facts; impersonation has no writes/events.
7. Legacy migration preserves cutoffs, accepted terms, rows and existing financial ownership.
8. Pending receipts, escrow_pending and matching orphan claims are classified without duplicate effects.
9. Pending exact replay resumes delivery; permanent conflicts remain blocked; concurrent finalizers
   produce one event/result; bids/assets/rebuttals reject active claims and legacy observations.
10. Stale bid_count cannot control expiry; ruling/task/dispute linkage is enforced; delayed Court
    commit versus retry finds one exact claim without pretending Court persisted escrow.
11. Migrated observations contain no invented actor, native command hash or client operation ID.

One schema owner handles v3/classifier/migration/audit. Gateway owner handles caller propagation,
command parsing/writer/routes/clock/predicates. Board owner handles signed fields, typed client,
coordinators and removal of generic lifecycle writes. Test owner proves baseline before fixes;
independent review checks authority, preservation, barriers and policy boundaries. MP-08 owns
only the later operation-keyed retry/lease sidecar; MP-10 owns Court delivery guarantees.

## References

- `MASTERPLAN-TO-COMPLETE-THE-PROJECT.md`, MP-07/MP-08; OpenSpec tasks7.x/9.x.
- `docs/plans/2026-10-16-mp04-mp07-contract-handoff.md`.
- `services/db-gateway/src/db_gateway_service/schema.py` and `schema_resources/current_v2.sql`.
- Gateway `services/board_writer.py` bid/status transactions; `core/middleware.py` caller verification.
- Task Board `task_bidding.py`, `task_review.py`, `task_ruling.py`, `deadline_evaluator.py` and
  `escrow_coordinator.py` for current check-then-act paths and provisional settlement identities.

## First bounded T-122 proof

Independent test-design review selected approve versus dispute as the smallest useful first case.
Both paths currently read submitted, invoke an external effect, then write with expected_status=None.
This proof can use existing interfaces; it must not fail merely because future v3 fields are absent.

Create a NEW `services/task-board/tests/integration/test_mp07_atomic_task_transitions.py` and,
only if needed for reuse, NEW `mp07_transition_harness.py` beside it. Use the existing disposable
live Gateway fixture and normal signed task setup to create a real submitted task with long explicit
deadlines. Instantiate two independent TaskManager/TaskDbClient object graphs, not two clients
over the global app-state singleton. Each store delegates the real first task read then waits at
a two-party barrier after obtaining submitted. Run the two async operations in separate executor
threads/event loops because TaskDbClient is synchronous. All Gateway reads/writes stay real.

Bank/Court recording boundaries prove invocation count, not actual remote settlement/adjudication;
retain that limitation explicitly. They do not control the barrier. Capture both operation outcomes
and persisted task/event facts before asserting: one success, one explicit409 conflict, one semantic
event, no contradictory terminal fields, and no Bank/Court call from the losing operation. Either
serial winner is legal. Broken barriers, thread timeouts, malformed responses,5xx errors and fixture
setup failures are harness/other failures, never the intended red proof.

After this first case proves the harness, cover competing acceptances, accept/cancel, submit/expiry
and the remaining contract matrix. One approve/dispute red does not satisfy all T-122 or authorize
the complete T-123 implementation. Root17/root18 are historical failed/interrupted attempts;
root19 passed and the a6af0be checkpoint is pushed. New test-only source is now intentionally red.
