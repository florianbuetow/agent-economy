# Decision: Bid-amount economics (Q-8)

**Date:** 2026-07-10
**Status:** RATIFIED
**Ratified by:** plan §5.-1 Step E1 — the §9 recommendation adopted as-is (no owner override)
**Source question:** docs/plans/2026-07-09-target-architecture-and-refactoring-plan.md §9 Q-8

**Later decision:** [Q-M2 accepted-bid pricing, 2026-10-23](2026-10-23-q-m2-accepted-bid-pricing-decision.md)
resolves the deferred economic policy. The owner selected accepted bids as
prices, refunds at final settlement and immutable per-job rule versions for
future changes. The historical decision below describes the current engineering
baseline until MP-24 implements and verifies that change.

## Question

Bids carry an integer `amount` (ratified R5) but it has zero monetary effect: escrow equals the
full reward at posting, and approval releases the full reward regardless of the winning bid.
Should the winning bid become the actual payment (release bid amount to worker, refund the
difference to poster at settlement) as the vision's undercutting story implies — or stay
signal-only for v1?

## Decision

**Stay signal-only for v1**, documented loudly. Price-forming settlement (winning bid becomes
actual payment, with refund of the difference) is deferred to WP-14 as its own economic change,
with its own tests.

## Rationale

Making the bid amount price-forming is a real economic behavior change (escrow/settlement
mechanics) that deserves dedicated design and test coverage rather than being folded into the
current refactoring pass.

## Consequences

- Blocks WP-14 item.
- Documentation must state clearly and visibly that bid `amount` is currently a signal only and
  does not affect settlement, so this is not mistaken for a bug.
- WP-14 owns the price-forming settlement change (release bid amount to worker, refund difference
  to poster) when it is eventually built.
