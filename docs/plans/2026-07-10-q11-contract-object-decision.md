# Decision: Contract object (Q-11)

**Date:** 2026-07-10
**Status:** RATIFIED
**Ratified by:** plan §5.-1 Step E1 — the §9 recommendation adopted as-is (no owner override)
**Source question:** docs/plans/2026-07-09-target-architecture-and-refactoring-plan.md §9 Q-11

## Question

The vision promises a three-party co-signed contract at acceptance ("both parties can
independently prove the agreement"); nothing of the sort exists — acceptance just sets fields, and
the openspec baseline codified that. Build a signed contract artifact, or formally descope it
(escrow remains the binding instrument)?

## Decision

**Descope the signed contract artifact for v1**, with this decision record as the explicit
descoping record. Escrow remains the binding instrument.

## Rationale

No signed contract object exists today, and the openspec baseline has already codified acceptance
as field-setting rather than co-signing. Building the artifact is significant new work with no
current consumer that needs independently provable agreement.

## Consequences

- Blocks WP-14 item.
- Escrow continues to serve as the de facto binding instrument between poster and worker.
- Follow-up: revisit if an external party (outside the platform) ever needs independent proof of
  the agreement.
