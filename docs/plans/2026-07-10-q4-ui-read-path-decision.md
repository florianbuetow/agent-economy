# Decision: UI read path (Q-4)

**Date:** 2026-07-10
**Status:** RATIFIED
**Ratified by:** plan §5.-1 Step E1 — the §9 recommendation adopted as-is (no owner override)
**Source question:** docs/plans/2026-07-09-target-architecture-and-refactoring-plan.md §9 Q-4

## Question

Keep the direct read-only SQLite connection (bless it: schema-coupled, breaks in Docker without a
shared volume, needs the semgrep exception made explicit) or migrate UI reads to the gateway read
API (consistent single data path, Docker-clean, requires a new gateway `GET /events` route and
rewriting ~6 query modules)?

## Decision

**Keep the direct read-only connection for v1**, formally documented as the single sanctioned
exception (the observatory pattern). Revisit only if Docker (Q-6) is kept.

## Rationale

The direct read-only path is the cheaper, already-working option. Migrating to the gateway read
API is only justified if Docker deployment is in scope, since the direct connection's main
liability is that it breaks without a shared volume in containerized deployments.

## Consequences

- Blocks WP-08.6, shapes WP-10.
- The semgrep exception for the direct SQLite read connection is made explicit as the sanctioned
  observatory-pattern exception.
- Since Q-6 descopes Docker for v1, this decision is effectively final for the v1 timeframe; it is
  only revisited if Docker deployment returns to scope.
