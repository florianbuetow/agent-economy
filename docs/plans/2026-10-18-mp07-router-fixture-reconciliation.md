# MP-07 router fixture reconciliation

The existing router fixture uses a dictionary store and fabricates successful Bank/Court responses.
Its initial health/bid-count probe fails before any route assertion because the required deadline
actor is absent. These are fixture failures, not additional product-red evidence.

Replace that fixture with the approved GatewayTaskStore, one explicit owned SQLite database, the
same platform key used by signed test requests, registered fixture actors, real account funding and
escrow receipts. Use real Gateway Bank/Court writers in successful mock callbacks; transport-failure
overrides remain available. No successful response may silently stand in for a committed effect.
The fixture must close both the initial transport clients and its owned replacement resources.
All configuration, log and asset paths stay below tmp_path. Freezegun-aware UTC time drives both
native command admission and persisted effects.

Common lifecycle helpers supply the explicit poster acceptance window and allocate an operation
identity for each new intent before signing, reading the expected version from the fixture store.
Replay tests must retain the original operation/version explicitly. Generic JWS signing helpers
must not mutate payloads. Direct route-test payloads will receive their own documented required-field
updates; negative tests retain the field/permission failure they are intended to isolate.

Ruling setup must use the actual stored claim identifier. Rebuttal success callbacks atomically
persist the Court row and Board timestamp. Existing tests that override success with a bare body,
use arbitrary ruling identifiers or mutate lifecycle state through the retired generic writer need
explicit reconciliation to native commands or labeled corruption fixtures, preserving observable
behavior and strengthening persisted-effect assertions. Separate signed live Gateway tests continue
to provide authentication/transport evidence; this in-process fixture is not a substitute for them.

The first converted probe exposed a fixture authority defect: the mutable signing-helper key map
accepted a replacement private key under the platform ID. A dedicated test failed with an actual
unauthorized fixture settlement before the fix. The platform path now uses the real loaded
PlatformAgent verifier; other identities use the exact fixture public keys and configured token
policy. This repairs test fidelity, not a production authentication vulnerability.

Rebuild the replacement service graph through its public constructors instead of assigning private
fields. Cancel/await the initial sweep before closing its transport store, then own a new sweep for
the new graph and cancel/await it before adapter cleanup. Dependency overrides mutate the same mock
objects passed into these constructors; no private rebinding is needed.

## Bid route contract reconciliation

Direct bid/accept/cancel payload fixtures add native operation identity/version before signing,
including negative cases whose subject is a different missing field or wrong signer. Duplicate
bid conflicts retain HTTP409 and the one-bid outcome, using native `bid_exists`. Bid cutoff
rejection retains HTTP409 and uses `cutoff_reached` rather than pretending a rejected manual
command already expired the task. Replace wall-clock sleeps with the exact persisted cutoff.

The old native late-accept test asserted that bidding close ended acceptance. Under the accepted
poster-window policy its expiry assertion belongs at the published acceptance cutoff, so the test
now exercises that exact boundary and retains rejection plus subsequent expired-state evidence.
Post-bidding acceptance inside the selected window is independently covered by the thirteen-case
native acceptance-window suite. No duration is silently added by production code.

The five original expiry-with-bids router cases retain expiry, exactly-once poster refund, one
semantic event, zero-bid expiry and before-cutoff non-expiry. The first three now use the selected
acceptance cutoff; their previous bidding-close expectations conflict with the ratified native
window contract. The old generic-update spy is replaced by the actual native operation/event rows.
Controlled frozen time replaces sleeps, and the refund case also verifies its receipt, leg, resolved
escrow, restored balance and byte-identical database after a second read. A separate post-bidding,
pre-acceptance-cutoff control prevents accidentally collapsing the two deadlines again.

## Remaining gap and security fixtures

Gap/security requests receive unrelated required window/operation/version fields before signing.
The expired-bid-list case advances to the actual acceptance cutoff rather than assuming bidding
close expired the task. The multi-error security case is split: malformed command shape is rejected
before native state admission; a validly shaped dispute still rejects an already approved task.
Both cases prove no additional write or effect, retaining the state and validation protections.

The insert-collision compensation fixture now publishes a genuinely funded task for Bob, then
forces Alice's pre-check miss under the same task ID. The real unique-key collision refunds only
Alice's new escrow; Bob's existing task and escrow remain unchanged. This preserves the actual
insert-time constraint and compensation proof without seeding an impossible bare dictionary or
asserting that refunding an already published same-poster escrow is correct. Ambiguous same-poster
publication/replay remains the explicit MP-05 recovery obligation.

## Final verification and review

The first full router run passed 356 cases and exposed one remaining health fixture request without
native cancellation identity. Adding its operation ID/version before signing preserves every
original status-count assertion. A separate long SQL line was wrapped without changing query text.
The final independent full run passed all 357 router cases in 264.53 seconds. Its 215 scoped Python
source hashes and all 202 observed ordinary log hashes stayed identical, with no log additions or
removals. Raw evidence is retained in the active change's
`evidence/mp07-transitions/router-fixture-rollout/` directory.

Independent review additionally required narrow verifier error handling, valid escrow operation
IDs in two earlier-error cases, exact HTTP409 for the collision, and exact approved status after
autoapproval. All were applied and verified; no compatibility fallback or broad error masking
remains in those assertions. The final 66-case review-fix group and 13-case health/gap group pass.
These overlap the full router run and must not be added as independent totals.

This gate does not close MP-07 or substitute for root CI. Three legacy dictionary-store consumers,
public caller rollout and remaining migration/recovery proofs are still pending.
