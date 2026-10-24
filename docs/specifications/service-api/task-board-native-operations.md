# Task Board native operation contract (MP-07)

## MP-08 durable financial-effect recovery amendment

The [MP-08 financial recovery contract](../../plans/2026-10-22-mp08-financial-recovery-contract.md)
completes the release/split recovery obligation described by this native-operation
contract. Every native `bank_release` or `bank_split` claim creates its recovery
sidecar atomically with the immutable operation. Task Board then runs one bounded
startup cycle and configured periodic cycles through the protected Gateway queue,
without depending on a public task GET. Direct exact command retries remain
unleased producers and may overlap recovery safely because both paths use the
same immutable Bank operation identity and receipt validation.

Task Board configuration requires `financial_effect_recovery.interval_seconds`
and `max_delivery_attempts_per_cycle`; production uses an explicit cycle bound
of 10. Each process creates a fresh `recovery-<UUID4>` fence. A cycle calls the
Gateway lease route at least once so one due dead-letter evidence check can run,
then processes at most the configured number of leases. Item failure is contained
and later items continue. Receipt lookup precedes Bank dispatch; after dispatch,
Task Board finalizes even when the Bank response is missing, malformed, timed out,
or otherwise ambiguous. Only exact timeout/transport/dependency failures are
reported through the fenced failure route. Evidence conflict blocks without a
second Bank call.

The installed operator CLI uses explicit configuration and the configured
Gateway signer. `effects list` requires explicit state, cursor and limit;
`effects show` and `effects retry` require a canonical operation ID. It prints
one strict validated JSON result to stdout on success. Invalid arguments,
configuration, transport, foreign ownership, malformed results, non-dead-letter
retry, or changed immutable retry identity produce a generic stderr diagnostic
and exit 1, without leaking response bodies or credentials.

This amendment defines the native v3 lifecycle implemented in the `refactor` workspace. It takes
precedence over legacy lifecycle, timing, generic-write and rebuttal-projection descriptions in
the Task Board and Gateway API specifications. Caller rollout and full release verification remain
open in `complete-agent-economy`; this document does not declare MP-07 complete.

## Public requests and replay

The signed payloads for bid submission, bid acceptance, cancellation, deliverable submission,
approval, dispute and ruling recording require:

- `operation_id`: caller-owned canonical `op-<UUID>` string, retained across retries.
- `expected_lifecycle_version`: explicit nonnegative integer, excluding booleans.
- The existing signed `action`, resource binding, task ID and command-specific fields.

The caller signs both identity fields before delivery. It must not replace the operation ID or
expected version when retrying the same command. The Gateway checks exact command replay before
current state/version/cutoff eligibility. An exact completed replay returns its original outcome;
changed semantic fields under the same ID conflict. A different command cannot take over a pending
or blocked task operation.

The public bid endpoint continues returning `201` and its bid response. Its bid ID is derived from
the operation UUID, so exact retries return the same bid. Bids do not advance lifecycle version.
Other public lifecycle mutations return the current task plus a separate `operation` envelope:
`200` for completion or `202` when delivery returns but durable evidence is still pending. If
delivery raises and finalization still finds no evidence, the route returns the typed dependency
error (commonly `502`) while retaining the claimed operation. Retry with the same operation ID.
Pure acceptance and submission complete inside the claim transaction. The immutable operation
result may describe an earlier status/version than the current task returned alongside it.

The Python agent SDK's six native commands return `TaskBoardCommandResponse(status_code, body)`.
The status is the actual HTTP observation: bid201, pure acceptance/submission200, and external-effect
commands200 or202. The SDK rejects malformed success transport/state combinations with
`TaskBoardCommandContractError`, retains non-success responses in `httpx.HTTPStatusError`, and does
not retry automatically. Callers must still validate immutable identity, hash, result and effects
against their frozen intent. Reads and asset methods retain their existing return
shapes. Runtime persistence and recovery are separate from this transport contract.

Task creation uses the separate [MP-05 publication contract](task-board-service-specs.md#mp-05-native-publication-amendment),
including an explicit positive integer `acceptance_window_seconds` chosen by the poster.
The SDK prepares a `TaskPostingIntent` before transport and returns a strict
`TaskPostingResponse` from submit/status. Exact publication replay returns the frozen creation
result; the lifecycle command envelope described here does not replace that protocol.
Durable caller rollout and complete MP-05 verification remain open. Asset uploads,
rebuttal forwarding and read routes do not acquire this lifecycle-command identity contract.

## Timing and immutable terms

New publications use timing contract version 2. Bidding ends at the published bidding cutoff;
the acceptance cutoff equals that cutoff plus the poster's acceptance window. Posters may accept
earlier. New bids are rejected at bidding-cutoff equality. Tasks without bids expire at that
cutoff; bid-bearing tasks stay eligible for acceptance until acceptance-cutoff equality.

Every command checks the persisted integer UTC microsecond cutoff inside its claim transaction.
Automatic expiry/approval becomes eligible at equality; the corresponding interactive action is
no longer eligible. Accepted execution and submission review cutoffs preserve fractional seconds.
Claim-time eligibility is retained while a claimed external effect completes after its cutoff.

Publication freezes timing and reward. Acceptance freezes the chosen worker, bid amount and
accepted reward. Settlement pays the frozen task reward, not the bid amount. Migrated timing-v1
tasks preserve their old cutoff: `acceptance_window_seconds` is null and acceptance cutoff equals
bidding cutoff. Migration never silently supplies a new duration.

## Gateway command endpoint

`POST /board/task-operations` requires the authenticated Board write capability. The body has
exactly `protocol_version`, `operation_id`, `task_id`, `command`, `expected_status`,
`expected_lifecycle_version`, `actor_kind`, `actor_id`, and `arguments`. Protocol version is 1.
Unknown fields and type coercions are rejected before semantic hashing.

| Command | Source → target | Actor | Exact argument fields |
| --- | --- | --- | --- |
| `submit_bid` | open → open | bidder (`agent`) | bid_id, bidder_id, amount |
| `accept_bid` | open → accepted | poster (`agent`) | bid_id, worker_id, escrow_id, reward, bid_amount |
| `cancel_task` | open → cancelled | poster (`agent`) | poster_id, escrow_id, reward, source_reference, cutoff_us |
| `expire_bidding` | open → expired | configured `system` | poster_id, escrow_id, reward, source_reference, cutoff_us |
| `expire_acceptance` | open → expired | configured `system` | poster_id, escrow_id, reward, source_reference, cutoff_us |
| `submit_deliverable` | accepted → submitted | worker (`agent`) | worker_id |
| `expire_execution` | accepted → expired | configured `system` | poster_id, worker_id, escrow_id, reward, source_reference, cutoff_us |
| `approve_task` | submitted → approved | poster (`agent`) | poster_id, worker_id, escrow_id, reward, source_reference |
| `auto_approve` | submitted → approved | configured `system` | poster_id, worker_id, escrow_id, reward, source_reference, cutoff_us |
| `dispute_task` | submitted → disputed | poster (`agent`) | poster_id, worker_id, escrow_id, reward, reason |
| `record_ruling` | disputed → ruled | configured `platform` signer | poster_id, worker_id, escrow_id, reward, ruling_id, worker_pct, summary, source_reference |

Task Board derives these arguments from authenticated public requests and authoritative stored
facts. Gateway separately verifies actor eligibility and exact stored linkage. Caller KID comes
only from verified middleware state; it is never accepted from JSON. Deadline commands use the
configured actor and IDs derived from task, command, source version and frozen cutoff. Public
callers cannot declare themselves system actors. Platform authority comes from the configured
economic platform public key, independently of Gateway transport keys or agent display names.

The response is `200` for `completed` and `202` for `claimed`. The envelope contains:

- Protocol version, operation ID, task ID, command and canonical request SHA-256.
- Source/target statuses and lifecycle versions, and `operation_state`.
- `effect_kind` (`none`, `bank_release`, `bank_split`, `court_claim`) and `effect_state`.
- Nullable effect operation ID, effect result reference and persisted effect intent.
- Nullable event ID and immutable result; pending operations have no completed result/event.

Lifecycle transitions advance version by one. Bid insertion has its own operation/event but
leaves version unchanged. A pure command stores its result and event atomically. An external-effect
claim retains source business status/version and reserves its target before delivery. Competing
writes, asset metadata and rebuttals check this owner before mutation.

## Gateway finalization and failure recovery

`POST /board/task-operations/{operation_id}/finalize` requires the empty JSON object `{}` and the
same verified caller KID that claimed the operation. It accepts no forwarded response, receipt,
actor or task state. A different caller cannot silently take over the operation.

Gateway loads and validates its persisted evidence in one transaction. Bank receipts must match
the claimed operation ID and the frozen financial intent, parties, amount, source reference,
escrow and ledger outcome. Court claims must match the task, parties, reason and frozen escrow
linkage. A matching durable effect completes the task, operation and one semantic event atomically.
Bank event provenance requires the canonical `central-bank` source, the exact settlement event
type and receipt/transaction event linkage. Release/split event `task_id` is optional presentation
metadata: absent/null or the exact escrow-bound task is valid; a foreign non-null task is not.
Authoritative task binding remains the receipt escrow ID and `bank_escrow.task_id`, independently
of that event field. The frozen request hash, parties, amounts, legs and timestamps remain required.
Missing evidence returns the pending envelope. Contradictory evidence produces a durable blocked
conflict without a lifecycle event. Completed external operations finalize by replaying the same
result. Pure commands reject this endpoint with `409 operation_not_finalizable`.

Task Board attempts delivery using the stored intent and checks Gateway evidence before and after
delivery, including after a missing or malformed acknowledgment. It uses the same Bank operation ID
on every retry. A successful response body alone cannot finalize a task. The periodic deadline
sweep still retries deterministic automatic commands without reads; MP-08's separate startup and
periodic financial-effect worker now supplies fenced recovery and protected diagnostics for all
eligible pending release/split commands.

Relevant typed conflicts include `operation_id_conflict`, `operation_caller_mismatch`,
`transition_pending` (with winning operation ID), `task_version_conflict`, `invalid_status`,
`cutoff_reached` and `legacy_reconciliation_pending`. Structural validation and authentication still
precede domain command admission. A blocked operation is exposed by task recovery projections;
it is not a successful completed mutation envelope.

## Task read projections

Detail and summary projections require `lifecycle_version`, `timing_contract_version`,
`acceptance_window_seconds`, `bidding_cutoff_us`, `acceptance_cutoff_us`, nullable
`execution_cutoff_us`, nullable `review_cutoff_us`, nullable `accepted_reward`, nullable
`accepted_bid_amount`, `pending_transition`, and `legacy_reconciliations`.
MP-08 also requires the exact eight-field `financial_effect` object on detail,
summary and current mutation projections. It derives settlement from authoritative
receipt and audited historical evidence independently of task status.

`pending_transition` is null or contains only operation ID, command, target status/version,
operation state and effect state. `legacy_reconciliations` separately lists observation ID/kind,
effect state and target-outcome hint, sorted by observation ID. It never fabricates native operation
identity for old effects. A task cannot have both an active native owner and unresolved legacy
ownership. Strict clients reject missing or contradictory projections instead of inferring values.

Public ISO bidding, acceptance, execution and review deadlines derive from persisted integer
cutoffs. Full task responses include the persisted `rebuttal_deadline` and
`rebuttal_submitted_at`. Retryable automatic evaluation failures are logged and followed by a fresh
read of persisted state. Malformed Gateway projections are rethrown, and a failed reread remains
visible to the caller. A read does not invent a completed outcome after a failed effect.

## Retired generic writes and rebuttal projection

`POST /board/bids` and `POST /board/tasks/{task_id}/status` cannot mutate native lifecycle state.
After structural and ownership checks, known requests receive `409 native_operation_required`.
Active native and unresolved legacy ownership errors retain precedence. Rejections release their
transaction; they create no row, counter change or event. Publication is the only initial-state
bootstrap: open, zero bids, no pending escrow/dispute/phase terms, lifecycle version 0. Incompatible
supplied initial values are rejected, including boolean substitutes for integer zero.

Court rebuttal persistence derives the task from the stored claim and verifies the disputed task,
parties, worker, frozen reason/deadline and ownership before writing. Rebuttal row, actual submitted
timestamp in Board, optional Court status update and one context-correct Court event commit in one
transaction. Lifecycle version does not change. Duplicate rebuttals, conflicting Board markers
and injected failures leave no partial projection. Task Board makes no second timestamp write.

The public rebuttal route returns Court's acknowledgment on success. Transport failure or malformed
acknowledgment JSON yields typed `502 court_unavailable`, even if Court has committed; the stored
projection is preserved. This is not receipt-grade rebuttal replay. Court attempt fencing, exact
rebuttal replay and immutable adjudication delivery remain MP-10.

## Verification boundary

The OpenSpec active checkpoint and writer-retirement evidence index record the focused red/green,
review and live persistence proofs. The original concurrency, migration, restart and caller rollout
gates remain mandatory. Release requires the fresh isolated root `just ci-quiet` gate; historical
root19 results do not certify this worktree.
