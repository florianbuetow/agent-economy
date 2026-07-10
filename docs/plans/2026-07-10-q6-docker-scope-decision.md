# Decision: Docker scope for v1 (Q-6)

**Date:** 2026-07-10
**Status:** RATIFIED
**Ratified by:** plan §5.-1 Step E1 — the §9 recommendation adopted as-is (no owner override)
**Source question:** docs/plans/2026-07-09-target-architecture-and-refactoring-plan.md §9 Q-6

## Question

Docker mode is broken six independent ways (§3.6) and zero CI covers it. Fix fully (≈WP-10.1,
substantial) or descope (delete compose/Dockerfiles, document local-only until a multi-host need
exists)?

## Decision

**Descope Docker now**; fix it when there is an actual deployment target.

## Rationale

The local 4-tier flow is the only mode anything in the project actually uses today. Fixing all six
independent Docker breakages with zero CI coverage is substantial effort with no current
consumer.

## Consequences

- Blocks WP-10.1.
- compose files and Dockerfiles are removed (or clearly marked unsupported); local-only operation
  is documented as the supported mode until a multi-host deployment need arises.
- Follow-up: this decision is the trigger condition referenced by Q-4 — the UI read path is only
  revisited if Docker scope is reinstated later.
