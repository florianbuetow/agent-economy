# Decision: Remaining vision economics (Q-15)

**Date:** 2026-07-10
**Status:** RATIFIED
**Ratified by:** plan §5.-1 Step E1 — the §9 recommendation adopted as-is (no owner override)
**Source question:** docs/plans/2026-07-09-target-architecture-and-refactoring-plan.md §9 Q-15

## Question

The remaining, unrecorded T-092 set of vision economics questions: (a) should filing a dispute
cost coins? (b) what happens at zero balance — can a broke agent still bid/work? (c) cap
concurrent contracts per agent, or let reputation regulate? One answer each (or an explicit
"defer, out of v1 scope") closes T-092 with decision records.

## Decision

**Defer all three from v1 mechanics**, recorded here as explicit deferrals:

- (a) Filing a dispute does not cost coins in v1.
- (b) Zero-balance agents are not blocked from bidding/working in v1.
- (c) No cap on concurrent contracts per agent is enforced in v1; reputation is not (yet) used to
  regulate this.

## Rationale

None of these three mechanics have an implementation today, and none are required for the
economy's core loop (post → bid → accept → execute → review/dispute → rule) to function. Deferring
them out of v1 scope avoids speculative economic design ahead of need.

## Consequences

- Closes T-092 with this decision record standing in for all three sub-questions.
- Follow-up: each of the three items (dispute cost, zero-balance handling, concurrent-contract
  caps) remains available to be picked up as its own future work package if the vision's economics
  are revisited post-v1.
