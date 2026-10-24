# Decision Record: Frontend Stack (T-026)

**Date:** 2026-06-13 (ratified by Florian 2026-06-12)
**Status:** DECIDED — stay vanilla JS; React plans archived as SUPERSEDED
**Affects:** T-093 (quarterly report page), T-094 (economy-graph animation), T-095
(leaderboard/earnings/satisfaction UI), parts of T-065 (small-doc sweep)

## Context

Eight-plus plan and mockup documents target a React/TypeScript/Vite frontend for the
observatory/ui service: the observatory frontend dashboard, the economy graph landing
animation, the quarterly report page, the monthly earnings chart + satisfaction colors,
and the NYSE dark theme. That React app was never built. The real UI is
`services/ui` — a FastAPI backend serving vanilla JS assets
(`services/ui/data/web/assets/`), with no `package.json`, no build pipeline, and no
node toolchain anywhere in the repo.

The choice: commit to building the React/TS/Vite pipeline those plans assume, or stay
on vanilla JS and archive the plans.

## Decision

**Stay vanilla JS. Archive every React-targeting plan/mockup doc with a SUPERSEDED
header.**

Archived documents (all carry a SUPERSEDED header pointing back to this record):

- `docs/plans/2026-02-29-observatory-frontend-design.md`
- `docs/plans/2026-02-29-observatory-frontend-plan.md`
- `docs/plans/2026-03-01-economy-graph-design.md`
- `docs/plans/2026-03-01-economy-graph-plan.md`
- `docs/plans/2026-03-01-quarterly-report-frontend-design.md`
- `docs/plans/2026-03-01-quarterly-report-frontend-plan.md`
- `docs/plans/2026-03-01-monthly-earnings-and-satisfaction-colors.md`
- `docs/mockups/nyse-theme-gap-analysis.md`
- `docs/mockups/nyse-theme-implementation-guide.md`

## Rejected Alternative

**Commit to the React/TS/Vite pipeline.** Rejected because no build pipeline exists
(nothing to "finish" — it would be a from-scratch adoption), the current vanilla JS UI
works against live services today, and a second toolchain (node/npm alongside uv)
raises CI and maintenance cost for no user-visible benefit at this stage of the
project.

## Rationale

- **No build pipeline exists** — the React plans assume infrastructure that was never
  created; implementing them means adopting an entire toolchain first.
- **The current UI works** — `services/ui` serves the observatory dashboard from
  vanilla JS assets and passes its service CI.
- **The remaining wanted features are implementable in plain JS** — the quarterly
  report page (T-093), the economy-graph animation (T-094), and the leaderboard/
  monthly-earnings/satisfaction improvements (T-095) do not need React; they will be
  (re)planned against the vanilla JS UI.

## Consequences

- T-093, T-094, T-095 are unblocked: implement in plain JS against `services/ui`;
  the archived React plans may be mined for requirements but not for implementation
  structure.
- The NYSE-theme mockups stay archived unless a vanilla-JS theming effort picks them
  up (tracked via T-065's small-doc sweep).
- Any future move to React requires a new, explicit decision record superseding this
  one.
