# Decision: Hosted CI (Q-14)

**Date:** 2026-07-10
**Status:** RATIFIED
**Ratified by:** plan §5.-1 Step E1 — the §9 recommendation adopted as-is (no owner override)
**Source question:** docs/plans/2026-07-09-target-architecture-and-refactoring-plan.md §9 Q-14

## Question

No `.github/` exists; every gate is manual. Add a minimal GitHub Actions workflow running
`just ci-quiet` on push/PR (mock judges keep it LLM-free), or stay local-only?

## Decision

**Add a minimal GitHub Actions workflow** running `just ci-quiet` on push/PR.

## Rationale

The June inventory already rated this P1, and the repo now has a remote to run it against. Mock
judges (per Q-13) keep the workflow LLM-free.

## Consequences

- Blocks WP-10.3.
- A `.github/workflows/` CI workflow is added, running `just ci-quiet` on push and PR, using the
  mock judge configuration so no live LLM calls are required.
