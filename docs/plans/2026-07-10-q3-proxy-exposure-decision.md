# Decision: UI proxy exposure (Q-3)

**Date:** 2026-07-10
**Status:** RATIFIED
**Ratified by:** plan §5.-1 Step E1 — the §9 recommendation adopted as-is (no owner override)
**Source question:** docs/plans/2026-07-09-target-architecture-and-refactoring-plan.md §9 Q-3

## Question

`/api/proxy/*` is unauthenticated; anyone reaching port 8008 can act as platform (today) /
operator (after Q-2) and spend funds. Accept as local-single-user posture (documented), or add a
minimal auth (config-driven shared secret)?

## Decision

**Document 127.0.0.1-only as the security boundary now; add a shared-secret header only if the UI
is ever exposed beyond localhost.**

## Rationale

The current deployment posture is local-single-user; the risk is bounded by localhost-only
exposure. Adding auth machinery now is unneeded complexity for a boundary that documentation
already establishes.

## Consequences

- Blocks WP-08.5.
- WP-08.5 documents the 127.0.0.1-only posture explicitly (including in the operator identity
  work from Q-2).
- Follow-up: if the UI is ever bound to a non-loopback address or exposed via a reverse proxy, a
  config-driven shared-secret header must be added to `/api/proxy/*` before that exposure ships.
