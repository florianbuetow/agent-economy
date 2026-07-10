# Tickets

Repo-level issue tracker. IDs are `T-###`.

> **Note (2026-07-10):** this file's ID space collides with the canonical backlog in
> `openspec/specs/completion-backlog/spec.md`, which uses the same `T-###` scheme for a
> different set of items. Which tracker is canonical is question **Q-1** in
> `docs/plans/2026-07-09-target-architecture-and-refactoring-plan.md` §9 and is awaiting a
> decision. Do not open new `T-###` tickets here until that is settled.

## Open

_None._

## Closed

### T-001 — bug — UI empty-economy phase label: e2e expects STALLED, code emits IDLE
- **Status:** closed 2026-07-10 (fixed in `9f506c9`)
- **Priority:** 3 (low)
- **Area:** services/ui
- **Found in:** e2e test `tests/e2e/test_cross_page.py::TestCrossPageEmptyDB::test_x01_empty_db_landing_renders_zero_state`
- **Summary:** For an empty database the landing page rendered Economy Phase = `IDLE`, but the
  acceptance test asserts `STALLED`. `compute_economy_phase` returned `"idle"` whenever no task
  was created in the last 60 minutes (always true for an empty DB), and the frontend shows
  `S.phase.toUpperCase()`.
- **Resolution:** the code was wrong, not the test. `stalled` is the label required by both
  `docs/specifications/service-api/observatory-service-specs.md` ("Economy Phases" table) and its
  acceptance cases MET-12/MET-13, and it was ratified as openspec **T-046**. `"idle"` appears
  nowhere in the spec. `compute_economy_phase` now emits `stalled`; no frontend change was needed
  (the phase is rendered generically, and only `growing`/`contracting` carry special styling).
  Integration coverage added in `services/ui/tests/integration/test_economy_phase_stalled.py`.
  The e2e above now passes.
- **Left open deliberately:** the spec's phase table lists only *sufficient* conditions and leaves
  combinations uncovered (e.g. an increasing trend at a 12% dispute rate matches neither `growing`
  nor `contracting`). Those cases keep falling to the residual `stable` branch rather than being
  reclassified by guesswork; defining them is folded into the WP-12 spec sweep.
