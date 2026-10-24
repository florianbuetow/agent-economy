# ADR: OpenSpec Is the Canonical Issue Tracker

**Date:** 2026-07-13
**Status:** Accepted — pointer/summary only; the decision was made and ratified on 2026-07-10.
**Full decision record:** `docs/plans/2026-07-10-q1-tracker-decision.md` (Q-1)
**Source:** `docs/plans/2026-07-09-target-architecture-and-refactoring-plan.md` §9 Q-1

This ADR does not introduce a new decision — it restates, in ADR shape, the already-ratified
Q-1 decision for discoverability alongside the other 2026-07-13 ADRs. See the linked record for
the full rationale.

## Context

`openspec/specs/delivery-governance/spec.md` declares OpenSpec canonical and bans markdown
trackers, yet root `tickets.md` had been reintroduced (2026-06-29) with a `T-001` colliding with
the backlog's own `T-001`, and `AGENTS.md` still referenced beads (already removed as a tool).
Two trackers claiming canonicity is itself a governance defect.

## Decision

**OpenSpec (`openspec/specs/completion-backlog/spec.md`, stable `T-###` IDs) is the canonical
issue tracker.** `tickets.md` was migrated into OpenSpec and deleted. Governance rules
(ratified decisions, ticket-closure gate, failing-test-first) live in
`openspec/specs/delivery-governance/spec.md`.

## Consequences

- No markdown TODO list, root `tickets.md`, or beads (`bd`) tracker is used going forward — all
  retired per this decision (recorded again in project memory as the Q-1 decision, 2026-07-10).
- `openspec/` must stay committed to git (it is not gitignored — verified directly against the
  current `.gitignore`, see `docs/plans/2026-07-13-docs-tracking-policy.md`).
- New work items are added as `#### Scenario: T-###` entries under the completion backlog with
  WHEN/THEN acceptance wording; an item closes only with its own failing-test-first proof plus
  `just ci-quiet` exit 0 from the repo root.
- The eight reconstructed arc42 ADRs under `docs/arc42/` remain undated and are out of scope for
  this documentation sweep; arc42 regeneration is deferred to a later pass.
