# MP-07 acceptance contract reconciliation

The breaking v3 task-operation and explicit authority contracts are part of the accepted
masterplan. These changes reconcile valid inputs and obsolete version assumptions; original
red proof remains archived. No required behavioral assertion is skipped or weakened.

## Version classification

Frozen v1/v2 catalogs and their historical migration results stay unchanged. Once v3 is known,
an otherwise-v2 catalog labeled version3 is PARTIAL because task sidecars are missing. It is no
longer a future schema. The future-version test uses version4, preserving rejection of a version
beyond the current implementation. A new test explicitly requires incomplete version3 to reject.

## Explicit authority and valid fixtures

Gateway configuration requires board_authority.platform_public_key_path and deadline_actor_id.
The public key is the economic platform key, distinct from every Gateway service key. Temporary
fixtures generate that separate key in their owned directory and reference its public file; they
do not gain access to production private keys. Task Board deadline_evaluation adds the same
explicit actor_id. Missing/blank/wrong-type cases remain independent rejection proofs.

The Task Board integration fixture supplies these required fields to its live disposable
Gateway and Board configuration. It provisions the same owned economic platform pair before
Gateway startup and reuses it for Board, keeping all Gateway capability pairs separate. This
corrects configuration setup errors before the existing assertions; it does not count those
errors as product red proof or alter race, replay, money or deadline expectations.

Native Court handoff proofs add a distinct temporary gateway-court capability to the existing
Task Board integration fixture. Only that Court credential writes controlled persisted claim
evidence; the Board credential retains only its existing Board capabilities. This fixture setup
models a real Court writer behind a controlled transport boundary and does not grant ordinary
agents or the production Task Board raw Court write authority.

The full public task projection now includes the already-persisted nullable rebuttal deadline
and submission timestamp. Explicit valid row fixtures include those columns, with null before
a dispute. Task creation supplies the null initial phase values to storage; Gateway still owns
their eventual Court-derived values. Existing deadline, state and privacy assertions are unchanged.

The isolated runtime and backup/restore inventory add the explicit Gateway field
board_authority.platform_public_key_path, bound to the existing economic platform public-key
resource. Exact inventory expectations therefore gain this one field (60 becomes61); every
previous field remains required. Ordinary key paths remain untouched, and rendered/restored
configuration must reference the owned runtime/restore target rather than the source tree.

Native task fixtures supply their explicit acceptance window, operation ID and expected lifecycle
version. Claims/finalization use the native operation route; raw fixture preparation is labeled
separately and cannot be claimed as public workflow proof. Existing concurrency, no-effect,
money, cutoff and signature assertions remain required. One shared task-domain clock reaches the
Gateway transaction; successful external-boundary fixtures must persist the evidence needed by
the finalizer. Recorded invocation proof is never silently relabeled as real external settlement.

Each reconciliation is validated in its owning component and then the fresh root gate before
ticket closure. This document does not waive any remaining proof-matrix or Core/Vision requirement.

## Shared task-write guard

The native command guard is extracted unchanged into one domain-specific function, then called
inside the existing bid/status/asset transactions before any write. Existing native operation
tests lock its current behavior; seven new independently reproduced failures require the legacy
entry points to obey the same active-claim and unresolved-observation boundary. This bounded
extraction does not authorize a fallback from native commands to unconditional lifecycle writes.

## Audit positive-fixture corrections

The execution-expiry audit control originally retained submission, dispute and Court history,
which describes a different lifecycle. Its valid input now represents pre-submission execution
expiry: accepted terms, money and assets are retained; submission/review and dispute/ruling
fields and their unrelated Court records are absent. The production audit remains strict.

The Court-progression control now uses the existing Court contract in which ruling_id equals
claim_id, and an explicit UTC ruled_at timestamp. The previous arbitrary ruling identifier and
the string `now` did not describe a valid persisted ruling. These input corrections do not relax
any negative assertion or replace the separately reproduced Board-first ruling startup defect.

## Explicit deadline actor in pre-existing configuration fixtures

The original three-case deadline configuration group now has one setup failure and two passes:
the otherwise-valid interval fixture omits MP-07's required system actor. This is fixture drift,
not new product-red evidence. Add the explicit test actor to the valid section and to the
missing-interval fixture, so the latter continues to isolate the missing interval rather than
passing because a different required field is absent. Preserve the missing-entire-section fixture
and every original assertion. The separate nine actor-validation cases remain unchanged, and all
startup logging stays inside the owned temporary root.
