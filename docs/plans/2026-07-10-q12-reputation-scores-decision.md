# Decision: Reputation score aggregation (Q-12)

**Date:** 2026-07-10
**Status:** RATIFIED
**Ratified by:** plan §5.-1 Step E1 — the §9 recommendation adopted as-is (no owner override)
**Source question:** docs/plans/2026-07-09-target-architecture-and-refactoring-plan.md §9 Q-12

## Question

The vision defines numeric scores (start 100%, drop via rulings); the Reputation service
deliberately stores raw feedback only, and no aggregate exists anywhere — agents can't ask "what's
my standing?", which Scenario 2's specialization loop needs. Add
`GET /reputation/agents/{id}/scores` (service-owned formula), keep consumer-side aggregation, or
defer to WP-14 with the arena?

## Decision

**Add a service-owned `GET /reputation/agents/{id}/scores` endpoint** with a spec'd aggregation
formula, built in WP-14 together with T-091 (its first real consumer).

## Rationale

A service-owned formula keeps the scoring logic in one place rather than duplicated
consumer-side; building it alongside T-091 gives it an immediate real consumer to validate
against, rather than landing speculatively ahead of need.

## Consequences

- No work package in §5 is explicitly named as "Blocks" for this question in §9; it is built in
  WP-14 together with T-091, which becomes its first real consumer (Scenario 2's specialization
  loop).
- The Reputation service gains a formula-owning aggregation endpoint; raw feedback storage is
  unchanged.
