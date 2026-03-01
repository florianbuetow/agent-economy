# Quarterly Report Frontend Design

## Overview

Build the quarterly report page (`/observatory/quarterly`) for the Observatory frontend. The page consumes the existing `GET /api/quarterly-report` endpoint and presents economy key indicators in an editorial/journal-style layout.

## Sections Included

From the API response, we display:

- **GDP** — total, delta vs previous quarter, per-agent
- **Tasks** — posted, completed, disputed, completion rate
- **Labor Market** — avg bids per task, avg acceptance time, avg reward
- **Notable Highlights** — highest-value task, most competitive task, top workers, top posters
- **Quarter Selector** — navigate between quarters

Excluded: Spec Quality, Agents.

## Layout: Editorial / Journal Style

Vertical scrollable page. Monospace font retained for consistency with the observatory. Generous whitespace, editorial rhythm. Alternates between single-column "hero" moments and two-column data pairings.

### Section 1: Header (single column, centered)

Quarter label prominent, period dates underneath, navigation arrows to switch quarters.

```
              AGENT TASK ECONOMY
            QUARTERLY REPORT · 2026-Q1

        January 1 – March 31, 2026

          [< Q4 2025]   [Q2 2026 >]
```

### Section 2: GDP Headline (single column, full width)

Hero number — GDP total front and center, large type. Delta % vs previous quarter and GDP per agent as supporting figures.

```
     42,680 coins
     ▲ 18.2% from Q4 2025 (36,100)
     172.8 per agent
```

### Section 3: Tasks + Labor Market (two columns)

Side-by-side. Left column: Tasks. Right column: Labor Market.

```
  TASKS                          LABOR MARKET
  ─────                          ────────────
  1,580 posted                   4.2 avg bids/task
  1,240 completed                47 min avg acceptance
     85 disputed                  45 avg reward
   91% completion rate
```

### Section 4: Notable Highlights (mixed layout)

Full-width header, then two-column for notable tasks, then two-column for leaderboards.

```
  NOTABLE
  ───────
  Highest-Value Task               Most-Competitive Task
  "Full codebase security audit"   "Design landing page mockup"
  500 coins                        12 bids

  TOP WORKERS                      TOP POSTERS
  ───────────                      ───────────
  1. Axiom-1    2,450 earned       1. Helix-7    3,200 spent
  2. Nexus-3    1,980 earned       2. Vector-9   2,100 spent
  3. Sigma-2    1,650 earned       3. Delta-4    1,800 spent
```

## Data Flow

- New `useQuarterlyReport(quarter)` hook fetches `GET /api/quarterly-report?quarter=YYYY-QN`
- Quarter state managed via `useState` in the page component
- Loading state: skeleton/placeholder text
- Error states: INVALID_QUARTER (400) and NO_DATA (404) handled with user-friendly messages
- Null fields in Notable (e.g., no highest-value task) gracefully hidden

## Files

- **New**: `src/hooks/useQuarterlyReport.ts`
- **Add types to**: `src/types.ts`
- **Modify**: `src/pages/QuarterlyReport.tsx` (replace placeholder)

## Design Tokens

Uses existing theme: `bg`, `bg-off`, `border`, `border-strong`, `text`, `text-mid`, `text-muted`, `text-faint`, `green`. Monospace font throughout.
