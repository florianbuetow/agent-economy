# MP-04 and MP-07 contract handoff

Status: read-only architecture recommendations collected during root14. This is a draft handoff,
not authorization to mutate frozen source or a ratified acceptance-window policy. MP-06 root14
must finish before implementation. Preserve the masterplan and OpenSpec scope and test-first gates.

## Current boundaries

MP-04 owns durable financial operation identity, canonical request hashing, atomic result receipts,
exact replay and settlement winner conflicts. MP-07 owns task version/status/cutoff predicates and
winning transition claims before external effects. MP-05 posting recovery, MP-08 background effect
reconciliation, MP-09 assets and MP-10 Court handoffs remain separate required work.

Current Gateway settlement commits money but rejects retries after resolution. Central Bank
generates new transaction IDs on each call, public/shared Bank APIs have no operation ID, and Task
Board's escrow coordinator discards results. Current Board generic writes permit optional constraints;
interactive paths can call Bank/Court before winning task-state races. These are implementation
targets, not evidence that future claims or receipt packages are already working.

## Financial receipt proposal

Require explicit caller-owned operation IDs for credit, lock, release, split and nonzero initial
issuance. Zero-balance account creation may retain its natural account identity. A retrying network
method must not allocate a replacement operation ID. Task Board's eventual persisted lifecycle
claim ID should pass unchanged to Bank. Treasury derives stable issuance identity from the account
and explicit genesis reference; funding CLI operations need an explicit or durably stored ID.

Gateway computes a canonical versioned business-request SHA-256, excluding transport timestamps,
new transaction/event IDs and summaries. Include every field that affects the financial result,
including parties, escrow/task identity, amount or percentage, and source reference. Reject
coercions/unknown fields before hashing. Receipt replay precedes any reevaluation of current state.
Caller-controlled financial CAS constraints should be removed in favor of the Gateway's fixed
locked-escrow predicate; lifecycle preconditions belong to the separate claim hash.

Persist a receipt and its logical legs in the same transaction as escrow resolution, ledger legs
and event. Expose operation ID/kind/hash/reference, commit time, event and escrow identity, result,
and exact account/amount/transaction/balance legs. Zero allocations remain visible as logical legs
with null transaction/balance identities and create no ledger rows. Same ID/hash returns the exact
stored result; changed content conflicts. Concurrent opposing settlements produce one winner and
an explicit conflict referencing it. Generic already-resolved errors must never be treated as success.

Pre-v2 rows contain no original caller operation IDs. Do not invent native caller receipts. The
initial architect recommended synthetic observation receipts, then withdrew that recommendation:
preserving legacy rows and rejecting untracked historical settlement retries is safer. Parent review
must resolve how migration records the audited legacy set so a missing post-cutover receipt cannot
masquerade as legacy. An explicit legacy/null-identity response is honest; it is not native replay
proof. Any clarification to historical receipt coverage must remain explicit in the OpenSpec
contract, with preservation and no-double-payment tests; do not silently narrow the plan.

## Task transition proposal

Add domain transition commands carrying operation ID, task ID, command, expected status/version,
actor and command fields. Under `BEGIN IMMEDIATE`, Gateway samples its injected clock and validates
authorization, version/status, cutoff and bid/task/worker linkage. Pure transitions commit in one
transaction; external-effect transitions persist a winning claim first, then call the service and
finalize with the matching receipt. Generic status writes must not bypass these rules.

Persist task version, pending operation, explicit cutoffs and accepted reward/bid/worker terms, plus
a narrow task-operation table containing request hash, source/claimed version, actor, timestamps,
effect identity, result and completion state. MP-08 adds background retry/recovery, not an unrelated
generic workflow engine. Compare deadlines with exact integer time units; equality belongs to the
deadline transition. Exercise independent connections/instances with barriers, not only sequential
calls under a process-local lock.

Q-16 is specifically a feeder selection policy. It does not globally constrain manual acceptance
to quorum or lowest-price selection. The first architecture proposal incorrectly described a grace
window as ratified; the architect corrected it. Q-M4 was subsequently answered on 2026-10-16:
**each poster explicitly specifies the acceptance-window duration**. Retain early human acceptance,
stop bids at bidding cutoff, expire zero-bid tasks immediately, and preserve the selected duration
as published terms for tasks with bids. No silent global duration is selected. Existing published
terms cannot be extended silently. Lifecycle tests and implementation remain required.

## Schema and work sequence

Freeze `current_v1.sql` and keep the exact v0-to-v1 proof reproducible. New schema versions require
explicit, verified-backup-bound migration; artifact audit contract v1 remains unchanged. Classifier
states must distinguish supported old versions from the admitted current version, and startup must
not add columns implicitly.

The architects recommend one schema owner and potentially unified v2 receipt/transition tables after
both T-116 and T-122 have fresh red proof. This remains a coordination proposal: unresolved MP-07
policy must not force guessed columns or block independent MP-04 contract/test work. Sequential
versions remain an alternative if needed. Never let two executors edit schema/migration concurrently.

Next: finalize receipt contracts and historical-cutover semantics; author T-116 behavioral red
tests; obtain Q-M4 for cutoff-dependent T-122 cases; then approve a concrete shared schema/migration
plan before source implementation. Required financial proofs include response loss, exact/changed
replay, zero/full/partial shares, concurrent release versus split, no orphan receipt/event/leg after
failure, and preserved legacy history. Required transition proofs include accept versus cancel/expiry,
submit versus expiry, approve versus dispute/auto-approve, duplicate acceptance, bid versus close,
and no external side effect from a losing claim. Finish with independent review and fresh root CI.

## Critic repairs selected for the next contract draft

The read-only critic rejected the initial ambiguity, not the implementation goal. The following
repairs define the next concrete draft; no source implementation is authorized during root15.

- Use receipt-only schema v2, then transition schema v3. This supersedes the unified-v2 recommendation
  above and keeps the two implementation/review packages bounded. Q-M4 is now resolved, but its
  lifecycle contract, tests and implementation remain separate from financial receipts.
- Classify every pre-v2 money operation, including issuance/credits and locks, not only resolved
  settlements. Migration records immutable observed-fact markers and a deterministic cutover-set
  attestation. It never invents caller operation IDs. A financial effect with neither a native
  receipt nor an attested legacy marker is corruption, not assumed legacy.
- Historical natural-key credit/lock replay can expose a clearly labeled legacy observation with
  null operation identity. Historical settlement retries fail closed with explicit unreplayable
  history, null winning operation ID and observed marker/ledger facts. They never mint, settle again,
  or retroactively bind a new caller operation ID. Native exact-replay guarantees begin at the new
  contract cutover; this unavoidable historical limitation must be explicit in the behavior/spec.
- Separate the immutable `receipt` from a labeled `current_escrow` projection. The receipt stores
  `escrow_status_at_commit`; the projection reports actual current status. Exact-replay assertions
  compare the complete stored receipt, while current projection may legitimately change. An
  ambiguous top-level `status` must not imply a settled escrow is currently locked.
- Logical zero allocation legs retain the intended recipient string but no transaction/balance
  row. Do not foreign-key that recipient directly to a bank account: zero legs need no account.
  Positive legs require a transaction FK, and the writer verifies that its account, amount, balance,
  event and escrow agree. The transaction's existing account FK supplies positive-account integrity.
- Freeze hash fields per operation kind before T-116 source tests. Include all result-affecting
  parties, values and source references; exclude transport/generated metadata and current views.
  Operation identity is global across financial kinds. A unique native-settlement winner per
  escrow is enforced by the database; changed-ID/native conflicts expose the true winner.

Before implementation, specify exact field names, ID validation, legacy marker/audit coverage and
caller propagation in the OpenSpec/API/test contracts. The marker attestation must remain valid
through later schema versions and must not allow a missing native receipt to become historical.
These are technical preservation/replay rules; no price, payout, artifact-disclosure or acceptance-
window policy is selected here.
