# Chart.js Metrics Panel — Design

## Summary

Add a Chart.js-powered metrics panel to the main Observatory page. The center column is split into two halves: a chart panel (left) and the existing LiveFeed (right). The chart panel has clickable badge tabs to switch between three visualizations: GDP line chart, economy health radar, and task flow doughnut.

## Layout Change

Current layout:
```
[GDP Panel 210px] [LiveFeed flex-1] [Leaderboard 220px]
```

New layout:
```
[GDP Panel 210px] [ChartPanel ~50%] [LiveFeed ~50%] [Leaderboard 220px]
```

The center `flex-1` area is split into two equal halves using a nested flex container.

## ChartPanel Component

New file: `src/components/ChartPanel.tsx`

### Badge Switcher

A row of three clickable badges at the top: `GDP` | `HEALTH` | `TASKS`. Active badge gets `border-border-strong bg-border-strong text-bg`, inactive gets `border-border bg-bg text-text-mid`. Same styling pattern as AgentProfile filter buttons.

### GDP Line Chart

- Data source: `fetchGDPHistory(window, resolution)` — already available via `gdpHistory` in AppContext
- Sub-badges for window selection: `1H` | `24H` | `7D`
- Filled area line chart, green themed
- Follows existing EarningsChart pattern (same tooltip, axis, font styling)
- Resolution mapping: 1H→1m, 24H→5m, 7D→1h

### Economy Health Radar

- Data source: `metrics` from AppContext (already polling every 5s)
- 5 axes normalized to 0-100:
  - Completion rate (already 0-1, multiply by 100)
  - Avg bids/task (normalize: cap at 10 → scale to 100)
  - GDP rate/hr (normalize relative to max reasonable value)
  - Task posting rate (normalize relative to max reasonable value)
  - Employment rate (100 - unemployment_rate * 100)
- Requires registering `RadialLinearScale` with Chart.js
- Single dataset with semi-transparent fill

### Task Flow Doughnut

- Data source: `metrics.tasks` from AppContext
- 4 segments: open, in_execution, completed (completed_all_time), disputed
- Colors: amber (open), blue/text (in execution), green (completed), red (disputed)
- Center text showing total task count
- Requires registering `ArcElement` with Chart.js

## Data Flow

- GDP chart: new `useGDPHistory(window)` hook with polling, OR extend existing `useMetrics` to accept a configurable GDP window
- Health + Tasks charts: reuse existing `useMetrics()` data from AppContext — no new API calls needed
- All charts use `cssVar()` for theme-aware colors

## Files to Create/Modify

1. **Create** `src/components/ChartPanel.tsx` — new component with badge switcher + 3 charts
2. **Modify** `src/pages/ObservatoryPage.tsx` — split center column, add ChartPanel
3. **Modify** `src/api/metrics.ts` — no changes needed (fetchGDPHistory already exists)

## Theming

All charts read colors from CSS custom properties via `cssVar()`. Tooltip uses `tooltipBg`. Font is monospace (`'Courier New', monospace`). Works across newsprint/ft/gs themes.
