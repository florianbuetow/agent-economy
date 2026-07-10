# Decision: Canonical issue tracker (Q-1)

**Date:** 2026-07-10
**Status:** RATIFIED
**Ratified by:** plan §5.-1 Step E1 — the §9 recommendation adopted as-is (no owner override)
**Source question:** docs/plans/2026-07-09-target-architecture-and-refactoring-plan.md §9 Q-1

## Question

`openspec/specs/delivery-governance/spec.md` declares OpenSpec canonical and bans markdown
trackers; yet root `tickets.md` was reintroduced (2026-06-29) and its `T-001` collides with
backlog `T-001`; `AGENTS.md` still mandates beads (removed). Options: (a) OpenSpec canonical,
migrate and delete `tickets.md`; (b) `tickets.md` canonical, supersede the openspec governance
clause.

## Decision

Option (a): **OpenSpec is the canonical issue tracker.** Migrate `tickets.md` into OpenSpec and
delete `tickets.md`.

## Rationale

The backlog's stable T-IDs already live in OpenSpec. `openspec/` is currently untracked by git —
whichever tracker wins must be committed.

## Consequences

- Blocks WP-01.
- `tickets.md` is migrated into OpenSpec and removed; the `AGENTS.md` reference to beads (already
  removed as a tool) is superseded by this decision as well.
- Follow-up: commit `openspec/` to git as part of WP-01 so the canonical tracker is not itself
  untracked.
