# MP-07 native coordinator rollout

This continues T-122/T-123 after the Gateway producer and public bid/acceptance tranche.
The entire Core/Vision goal and the remaining MP-07 proof matrix remain required.

## Change boundary

Move submission, approval, cancellation, dispute, ruling and deadline coordination to the
native command protocol. Authentication and signed operation/version validation precede the
claim. Mutable state, actor entitlement, bid/asset linkage and cutoff eligibility belong to
the Gateway transaction. No coordinator can settle money or create a Court claim before winning.

Extract the already-tested bidding operation identity/result formatting into a small shared
Task Board helper when the next caller needs it. Keep existing public bid/acceptance regression
tests as the behavior lock. This is a domain helper, not a generic workflow framework.

Financial delivery receives the store, existing escrow coordinator and validated operation
envelope explicitly. Completed operations cause no new Bank call. A claimed operation first
asks Gateway to finalize from its own evidence. If still pending, deliver exactly its stored
Bank intent with the Board operation ID, then finalize again. A Bank exception also triggers
one finalization attempt, so a persisted receipt can recover a lost acknowledgment. If evidence
is still absent, preserve the typed dependency failure and the durable claim. A successful HTTP
body without matching persisted evidence never changes task state; a returned pending mutation
uses HTTP202 and the source task state. Ordinary completion uses HTTP200.

The delivery helper is HTTP-agnostic. These status mappings apply only to authenticated interactive
mutation routers. Deadline evaluation within GET/list keeps HTTP200 and re-reads the source/current
task projection, showing only reduced pending_transition. It catches and logs dependency failures;
it never returns the full effect intent from a public read. A Gateway outage during that re-read
cannot be represented as a confirmed current or confirmed pending state.

Every finalize response must match the initial claim's immutable operation ID, task, command,
request hash, source/target status and versions, effect kind/ID and stored intent. Pre-finalize
failure prevents Bank dispatch. A durable finalizer conflict takes precedence over a Bank exception;
an unavailable finalizer does not prove that the effect is absent or pending.

Cancellation and open-task expiry select their frozen cutoff from an actual strict Gateway bid-list
read, never cached bid_count. Gateway rechecks EXISTS in the claim transaction. A bid arriving
between read and claim may cause a conflict; that request must make no Bank call or generic status
write. Required tests cover both stale-count directions, this interleaving, exact equality and
deterministic system operation IDs.

A valid task that has never been accepted has no frozen worker/reward from which to build an
approval command. Reject that request locally from the authoritative read, preserving pending
owner, legacy owner, version and status precedence. Do not report malformed Gateway data for a
valid open task and do not invent command fields. In this unbuildable case no canonical approval
hash exists, so a global operation-ID collision is not classified. Histories with frozen terms,
including completed or pending approval replays, still reach Gateway replay first. Submitted
history missing required frozen terms remains an invalid Gateway response.

Mutation responses retain the current task separately from the immutable operation result,
following the already-tested acceptance contract. Public reads expose pending transitions and
legacy observations without fabricated operation provenance. Background scheduling, leases,
retry diagnostics and legacy-observation convergence remain MP-08; Court verdict persistence
and fenced adjudication remain MP-10.

## Evidence and ownership

New public integration proofs use real signed Board/Gateway calls and real persisted Bank
receipts behind the controlled Bank transport boundary. They prove claim-before-effect,
fixed reward rather than bid amount, exact replay, no extra events and stale-request rejection.
Unit proofs isolate delivery ordering, unchanged operation IDs, missing evidence, dependency
failure and lost acknowledgments; they do not claim financial conservation by themselves.

Parent owns shared helpers, routes and integration. Independently owned tests precede each
implementation. Native deadline and Court lanes follow their specific red proofs. Existing
valid fixtures may receive the explicit new fields under the recorded MP-07 reconciliation;
assertions, winner/no-effect checks and cutoff boundaries remain obligations.

Before closure: finish all caller/storage migrations, retire generic lifecycle bypasses, run
the full serial/race/recovery/migration matrix, obtain independent review, and pass fresh isolated
root `just ci-quiet`. A bounded green helper or public action is not package completion.
