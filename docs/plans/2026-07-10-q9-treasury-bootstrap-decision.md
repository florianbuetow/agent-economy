# Decision: Treasury bootstrap ownership (Q-9)

**Date:** 2026-07-10
**Status:** RATIFIED
**Ratified by:** plan §5.-1 Step E1 — the §9 recommendation adopted as-is (no owner override)
**Source question:** docs/plans/2026-07-09-target-architecture-and-refactoring-plan.md §9 Q-9

## Question

The **UI** mints the 1,000,000-coin treasury at startup (tier 4). Where should genesis live: (a) a
`just provision` bootstrap step / extended fund-feeder CLI (recommended), (b) central-bank startup
config, (c) keep in UI?

## Decision

Option (a): **move treasury genesis to a `just provision` bootstrap step / extended fund-feeder
CLI.**

## Rationale

Explicit, idempotent, and service-neutral; it also fixes the "UI down ⇒ no treasury" dependency
that exists today.

## Consequences

- Blocks the treasury part of WP-08/WP-11.
- The UI no longer mints the treasury on startup; a `just provision` step (or extended fund-feeder
  CLI) performs idempotent genesis independent of any single service being up.
