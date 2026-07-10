# Decision: Lifecycle automation (Q-5)

**Date:** 2026-07-10
**Status:** RATIFIED
**Ratified by:** plan §5.-1 Step E1 — the §9 recommendation adopted as-is (no owner override)
**Source question:** docs/plans/2026-07-09-target-architecture-and-refactoring-plan.md §9 Q-5

## Question

Today all deadline transitions are lazy-on-read and nothing ever triggers court rulings (GAP-A1)
or enforces the rebuttal window. Add a config-driven periodic evaluator in Task Board
(recommended: one background loop sweeping deadlines *and* firing platform-signed ruling triggers
after the rebuttal window), or stay lazy-only and accept stalled disputes/stale statuses when
nothing polls?

## Decision

**Add a config-driven periodic evaluator** in Task Board: one background loop that sweeps
deadlines and fires platform-signed ruling triggers after the rebuttal window closes.

## Rationale

Lazy-only evaluation leaves disputes stalled and statuses stale whenever nothing happens to poll
the affected task; a periodic evaluator is the only mechanism that guarantees forward progress
without a human or the scripted demo in the loop.

## Consequences

- Blocks WP-05.2, WP-06.1.
- The evaluator's interval is a required `config.yaml` setting (no hardcoded default, per project
  convention).
- Combined with Q-16 (bid acceptance), this decision gates §8's definition-of-done item 2: a task
  can now travel from posted to ruled without a human or the scripted demo.
