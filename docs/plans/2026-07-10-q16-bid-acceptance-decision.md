# Decision: Who accepts bids, and on what rule (Q-16)

**Date:** 2026-07-10
**Status:** RATIFIED
**Ratified by:** plan §5.-1 Step E1 — the §9 recommendation adopted as-is (no owner override)
**Source question:** docs/plans/2026-07-09-target-architecture-and-refactoring-plan.md §9 Q-16

## Question

*(GAP-A15, found 2026-07-10 — the highest-value question in the list.)* No autonomous component
accepts a bid today; only the scripted demo (`tools/src/demo_replay/engine.py:236`) and a human in
the UI proxy do. For the economy to run on its own, the **poster agent** (the feeder) needs an
acceptance loop, and that loop needs a winning-bid rule. The vision implies lowest-price wins
("Bob bids 8 coins, Carol undercuts at 6. Alice accepts Carol" —
`docs/main/agent-task-economy.md § Demo Scenario 1`), but nothing is specified about ties,
reputation weighting, or a reserve price. Two parts: (a) what rule picks the winner? (b) does the
poster accept as soon as a bid arrives, or wait out the bidding window? Part (b) is load-bearing:
accepting the first bid destroys the undercutting the entire thesis rests on, so the feeder must
almost certainly wait.

## Decision

**Wait for the bidding window to close (or a configured quorum), then accept the lowest bid,
breaking ties by delivery-quality reputation.**

## Rationale

Accepting the first bid would eliminate the undercutting dynamic the project's core thesis rests
on (agents competing on price). Waiting for the window (or quorum) to close preserves that
dynamic; breaking ties by delivery-quality reputation gives reputation an economic function
without requiring a full reputation-weighted bid-scoring formula.

## Consequences

- Blocks the new autonomous-acceptance work package.
- Combined with Q-5 (lifecycle automation), this decision gates §8's definition-of-done item 2: a
  task can now travel from posted to ruled without a human or the scripted demo in the loop.
- The bidding-window duration (or quorum threshold) is a required, config-driven setting on the
  poster/feeder side, per the project's no-hardcoded-config rule.
