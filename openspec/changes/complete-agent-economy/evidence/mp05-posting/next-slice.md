# MP-05 implementation handoff — not yet started

This read-only architecture handoff follows MP-07's closure gate. It does not
close T-118/T-119 or authorize skipping their genuine failing tests.

## Missing behavior

The SDK currently allocates a new task ID inside each `post_task()` call.
Task Board locks escrow before its first durable publication write. Only a
duplicate-insert error invokes compensation, and compensation failures are
logged without durable retry state. A lost response can therefore create a new
posting, orphan a lock, or refund an already committed matching task.

MP-07 lifecycle operations require an existing task row. Publication needs its
own small durable record before that row exists, rather than weakening the
lifecycle table's foreign key and state rules.

## Proposed complete boundary

An immutable caller-owned posting intent contains protocol version, operation
ID, task ID, poster ID, title, specification, fixed reward, all four explicit
durations (including the poster-selected acceptance window), and a canonical
semantic hash. IDs are allocated and saved before any transport. Fresh JWS
timestamps/signatures do not change the frozen semantic hash. The lock retains
its separate stable task-lock operation ID.

A dedicated Gateway publication record should store the exact canonical
request, caller capability, admission and publication timestamps, task ID, lock operation ID,
nullable escrow/refund references, immutable outcome and safe error state.
It must not require an already published `board_tasks` row. Candidate states:
`awaiting_lock`, `published`, `refund_pending`, `refunded`, `blocked_integrity`.
This requires an explicit verified v3-to-v4 migration and startup audit.

Gateway claim/reconcile endpoints should atomically publish the task, timing
contract and one creation event only from a matching persisted Bank lock
receipt. Exact replay returns the original outcome; changed terms conflict.
Pending records stay out of public task lists/counts and are visible only to
the entitled poster. Direct unrestricted new-task creation must be retired.

Reconciliation must check authoritative receipt and publication state before
any new Bank attempt or refund. A matching already-published task wins over a
stale duplicate-insert observation; it must never be compensated.

The admission timestamp tracks pending work. Sample `published_at_us` only in
the atomic public-task insertion transaction; derive task creation time, its
creation event and bidding/acceptance cutoffs from that value. Thus an invisible
authorization or storage outage does not consume the signed bidding duration.
Exact published replay retains the original timestamp and cutoffs. Existing
published task timing remains unchanged. Server timestamps are outside the
caller semantic hash; signed durations remain frozen.

Reserve the task ID against both publication records and existing tasks before
Bank transport. Different operation IDs cannot claim one task ID. Exact native
replay has its persisted specification hash; a Bank lock alone does not prove
title, specification or durations and must not be adopted into new terms.

With that reservation, ordinary post-lock insert collisions disappear. Storage
errors and response loss remain pending rather than causing compensation.
Legacy orphan locks require a separately persisted, cutover-proven observation:
matching lock receipt and locked escrow, no task, no native publication claim.
Preserve already published legacy tasks without inventing native posting IDs.
Contradictory/corrupt evidence is an integrity blocker, not refund authority.
Legacy compensation must exclude concurrent old writers through the existing
verified migration/maintenance procedure; an arbitrary live absent-row read is
not sufficient provenance.

## Recovery authority: confirmed lock versus absent receipt

MP-03 token expiry remains an admission boundary. Server background recovery
may finalize an already confirmed lock and retry a recorded platform-signed
refund. It must not issue a new poster debit from the publication record alone,
replay an expired poster token, or acquire the poster's private key.

When no matching lock receipt has been observed, retain `awaiting_lock`. This
does not prove no effect: an earlier request may still commit. Caller recovery
loads the original intent and signs fresh task/escrow tokens for the same IDs
and terms. Built-in feeder/demo clients do this after restoring their identity;
the UI retains its operation and supports retry/status; external SDK callers
own persistence. Reconcile receipts before forwarding the fresh lock token.

Do not terminalize or refund merely because a read found no receipt, a timeout
occurred, or a later attempt returned an authorization/funding error. A previous
same-ID dispatch can still win. MP-04's exact replay supplies one Bank receipt.

Refund requires both a confirmed matching lock and an authoritative definitive
publication conflict. Persist `refund_pending` before transport; complete it
only from the matching refund receipt. Automatic abandonment of an unresolved
`awaiting_lock` is not specified. It would need a Bank-side fence against late
locks and an accepted cancellation/retention policy; do not silently add it.

## First T-118 failing cases

1. Nonduplicate insertion failure after lock commit, followed by restart:
   converge to one funded public task or one documented receipt-backed refund.
2. Publication commits but its response is lost: exact replay returns the
   original task without a second lock or any refund.
3. A stale precheck misses an already matching publication: duplicate insertion
   must not refund that published task.
4. Definitive publication failure followed by refund failure/lost response:
   restart converges to one refund and no task.
5. Caller timeout retry preserves original task/operation IDs and exact terms.
6. Kill after server intent but before Bank admission, expire the old token,
   restart server: no unauthorized background debit; caller fresh-signed replay
   produces exactly one lock/task.
7. Late first Bank commit after an initially absent receipt, concurrent with
   fresh same-ID retry: one lock, one task, zero refund.

Then cover real process death at server-intent, lock, publication,
compensation-intent and refund commit boundaries.

## Implementation ownership

First add red proofs without product changes. Then implement Gateway schema,
requests, publication writer/reads/routes and exact receipt validation; Task
Board coordination, owner status and background reconciliation; strict SDK
intent/response types; feeder journal, UI saved intent, and demo run state with
persistent actor keys. Existing MP-07 command behavior stays intact.

Current compensation tests prove only a particular duplicate refund or repeated
manual compensation call; they do not prove a durable recovery trigger.
Current publication tests prove atomic task/timing creation after the lock;
they do not prove pre-lock intent or ambiguous-commit recovery. Run component,
migration, concurrency, kill/restart and isolated root gates before closure.
