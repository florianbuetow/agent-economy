# Tickets

Repo-level issue tracker. IDs are `T-###`.

## Open

### T-001 — bug — UI empty-economy phase label: e2e expects STALLED, code emits IDLE
- **Status:** open
- **Priority:** 3 (low)
- **Area:** services/ui
- **Found in:** e2e test `tests/e2e/test_cross_page.py::TestCrossPageEmptyDB::test_x01_empty_db_landing_renders_zero_state`
- **Summary:** For an empty database the landing page renders Economy Phase = `IDLE`, but the
  acceptance test asserts `STALLED`. This is deterministic at any wall-clock value and is **not**
  a time/date bug: `compute_economy_phase` returns `"idle"` whenever no task was created in the
  last 60 minutes (always true for an empty DB), and the frontend shows `S.phase.toUpperCase()`.
  The backend has no `stalled` phase, so the test asserts a label the code never produced.
- **Pre-existing:** yes — the relevant code paths (`compute_economy_phase`, `landing.js`) are
  byte-identical before and after the June 2026 UI refactor; unrelated to that work.
- **Decision needed (product):** what should an empty / zero-activity economy's phase be called?
  Either (a) add/emit a `stalled` phase for zero-activity and map it in the UI, or (b) update the
  acceptance test's expectation to `IDLE`.
- **Repro:** `cd services/ui && uv run pytest tests/e2e/test_cross_page.py::TestCrossPageEmptyDB::test_x01_empty_db_landing_renders_zero_state`
