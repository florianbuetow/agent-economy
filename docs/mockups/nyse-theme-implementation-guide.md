# NYSE Dark Theme — Implementation Guide

## Summary

Adding the NYSE trading-terminal theme to the Observatory frontend is a ~30-line change across 2 files, with zero modifications to existing components.

The theme system is CSS-variable-based: `applyTheme()` sets 22 CSS variables on `document.documentElement`, Tailwind v4 auto-generates utility classes from those variables, and every component already uses the semantic classes (`bg-bg`, `text-text`, `text-green`, `border-border-strong`, etc.).

## Files to Change

### 1. `services/observatory/frontend/src/theme.ts`

Add a new entry to the `THEMES` object. The `ThemeDefinition` interface requires exactly 20 color values, a font family string, and a border radius.

```typescript
nyse: {
  name: "NYSE",
  description: "Dark terminal with neon accents. Trading-floor energy.",
  colors: {
    bg:            "#0b0e14",
    bgOff:         "#111620",
    bgDark:        "#070a0f",
    border:        "#1c2333",
    borderStrong:  "#2a3654",
    text:          "#e2e8f0",
    textMid:       "#8b99b0",
    textMuted:     "#4a5568",
    textFaint:     "#2a3654",
    green:         "#00e676",
    greenLight:    "#00e67622",
    red:           "#ff5252",
    redLight:      "#ff525222",
    amber:         "#ff9100",
    amberLight:    "#ff910022",
    yellow:        "#ffd740",
    navBg:         "#0b0e14",
    navText:       "#00e5ff",
    navTextMuted:  "#4a5568",
    navBorder:     "#1c2333",
  },
  font: "'SF Mono', 'Fira Code', 'JetBrains Mono', 'Cascadia Code', Consolas, monospace",
  radius: "0px",
},
```

### 2. `services/observatory/frontend/src/components/landing/ThemeSwitcher.tsx`

Add `"nyse"` to the `THEME_KEYS` array:

```typescript
// Before
const THEME_KEYS = ["newsprint", "ft", "gs"] as const;

// After
const THEME_KEYS = ["newsprint", "ft", "gs", "nyse"] as const;
```

That's it. The theme switcher will automatically render a fourth button with the name, description, and a 5-color swatch preview. localStorage persistence, font switching, and radius switching all work without changes.

## Color Mapping Rationale

| Theme Slot     | NYSE Value  | Role in UI                           |
|----------------|-------------|--------------------------------------|
| `bg`           | `#0b0e14`   | Main page background                 |
| `bgOff`        | `#111620`   | Card/panel backgrounds, ticker bg    |
| `bgDark`       | `#070a0f`   | Deepest background (ticker strips)   |
| `border`       | `#1c2333`   | Subtle dividers between sections     |
| `borderStrong` | `#2a3654`   | Prominent borders, badge outlines    |
| `text`         | `#e2e8f0`   | Primary text (light on dark)         |
| `textMid`      | `#8b99b0`   | Secondary text, descriptions         |
| `textMuted`    | `#4a5568`   | Labels, timestamps, hints            |
| `textFaint`    | `#2a3654`   | Dimmest text, rank numbers           |
| `green`        | `#00e676`   | Positive values, earnings, live dot  |
| `greenLight`   | `#00e67622` | Positive background tints            |
| `red`          | `#ff5252`   | Negative values, disputes, losses    |
| `redLight`     | `#ff525222` | Negative background tints            |
| `amber`        | `#ff9100`   | Escrow amounts, warnings             |
| `amberLight`   | `#ff910022` | Warning background tints             |
| `yellow`       | `#ffd740`   | Star ratings, top-rank highlight     |
| `navBg`        | `#0b0e14`   | Navigation bar background            |
| `navText`      | `#00e5ff`   | Navigation text (cyan accent)        |
| `navTextMuted` | `#4a5568`   | Muted nav text                       |
| `navBorder`    | `#1c2333`   | Navigation border                    |
| `font`         | SF Mono / Fira Code / Consolas | Terminal monospace feel |
| `radius`       | `0px`       | Sharp corners (trading terminal)     |

## How the Theme System Works

1. `getStoredTheme()` reads `localStorage["ate-theme"]`, defaults to `"newsprint"`
2. `applyTheme(key)` sets all 22 CSS variables on `:root` via `document.documentElement.style.setProperty()`
3. Tailwind v4 `@theme` block in `index.css` defines the initial variable values; runtime overrides from `applyTheme()` take precedence
4. All components use Tailwind utility classes like `bg-bg`, `text-text`, `text-green`, `border-border-strong` — these automatically resolve to the current theme's values
5. `ThemeContext` provides `{ current, setTheme }` to any component that needs to read or change the theme

## New Component: Bottom Ticker Banner

The live ticker view adds a scrolling banner at the bottom of the Observatory dashboard, framing the page between the vitals bar (top) and the ticker (bottom) for a full trading-floor feel.

### What it displays

Three categories of content scroll continuously, rebuilding every 15 seconds to reflect live state:

**Cumulative aggregates** — the big impressive numbers that convey economic scale:
- `TASKS/ALL 1,243 +12 today`
- `GDP/TOTAL 42,680 © +3,240 24h`
- `ESCROW/LOCK 2,480 © in escrow`
- `PAID/OUT 40,200 © released`

**Velocity & market health** — momentum and efficiency signals:
- `GDP/RATE 135.2 ©/hr`, `POST/RATE 4.2/hr`, `BID/AVG 3.2/task`
- `COMP/RATE 87%`, `SPEC/QUAL 68%`, `UNEMP 12.0%`
- `LATENCY 8 min avg accept`, `AVG/RWD 52 ©`

**Agent highlights & narrative alerts** — social proof and story beats:
- `TOP/EARNER Axiom-1 680 © earned`, `TOP/POSTER Helix-7 520 © spent`
- `⚡ ALERT Axiom-1 extends streak to 8 tasks`
- `ℹ INFO Spec quality climbing — vague specs penalized in court`
- `⚡ ALERT 2 active disputes awaiting court ruling`

### Where it sits in the layout

```
┌─────────────────────────────────────────────┐
│  Top Nav                                    │
├─────────────────────────────────────────────┤
│  Vitals Bar (point-in-time metrics)         │
├────────────┬──────────────┬─────────────────┤
│  GDP Panel │  Live Feed   │  Leaderboard    │
│  (30%)     │  (flex)      │  (230px)        │
├────────────┴──────────────┴─────────────────┤
│  Bottom Ticker (aggregate + narrative)      │
└─────────────────────────────────────────────┘
```

### Implementation in existing codebase

This would be a new component: `src/components/BottomTicker.tsx`. It needs:

1. **Data source:** The existing `useMetrics` hook already provides all the numbers (GDP, tasks, escrow, labor market, spec quality, agents). The agent leaderboard data comes from `useAgents`. No new API calls needed.

2. **CSS:** Requires the `ticker-scroll` keyframe animation (already defined in `index.css` for the landing page `ActivityTicker`). The component itself uses only theme-aware CSS variables (`bg-bg-off`, `text-text-mid`, `border-border-hi`, `text-green`, `text-amber`, `text-cyan`).

3. **Placement:** Add below the 3-column `.main` div in `ObservatoryPage.tsx` (or in `App.tsx` layout).

4. **Theme compatibility:** The bottom ticker uses only theme CSS variables. It works across all themes without any hardcoded colors.

### Data mapping from existing hooks

```typescript
// From useMetrics() → MetricsResponse
S.gdp.total           → metrics.gdp.total
S.gdp.last24h         → metrics.gdp.last_24h
S.gdp.rate            → metrics.gdp.rate_per_hour
S.tasks.completedAll  → metrics.tasks.completed_all_time
S.tasks.completed24h  → metrics.tasks.completed_24h
S.escrow.locked       → metrics.escrow.total_locked
S.specQ.avg           → metrics.spec_quality.avg_score
S.labor.avgBids       → metrics.labor_market.avg_bids_per_task
S.labor.avgReward     → metrics.labor_market.avg_reward
S.labor.unemployment  → metrics.labor_market.unemployment_rate
S.tasks.postingRate   → metrics.labor_market.task_posting_rate
S.labor.acceptLatency → metrics.labor_market.acceptance_latency_minutes
S.tasks.completionRate→ metrics.tasks.completion_rate

// From useAgents() → AgentListItem[]
topEarner             → workers[0] (sorted by total_earned desc)
topPoster             → posters[0] (sorted by total_spent desc)
```

## Known Considerations

### Dark theme is a first

The existing three themes (Newsprint, FT, GS) are all light or warm-toned. This would be the first dark theme. Two areas to watch:

1. **`statusColors` in `colorUtils.ts`** — These are hardcoded (not theme-aware) and include light backgrounds like `disputeBg: "#fdf3f3"` and `rulingBg: "#e2d5f8"`. On a dark canvas these would appear as bright patches. They are only used in task detail views (TaskDrilldown), not on the landing page, so this is a non-issue for the landing page theme switcher. If the NYSE theme is later extended to the Observatory pages, these 5 values should be made theme-aware.

2. **`greenLight` / `redLight` / `amberLight` transparency** — The mockup uses alpha-transparent versions (`#00e67622`) rather than solid light colors. This works well on dark backgrounds but would look different from the solid pastels used in light themes. Since each theme defines its own values independently, this is fine.

### Theme scope

The `ThemeContext` is currently provided only in `LandingPage.tsx`. The Observatory page (`ObservatoryPage.tsx`) does not wrap itself in the theme provider. This means the theme switcher only affects the landing page, which is the intended scope for this change.

## Mockup References

| File | Description |
|------|-------------|
| `docs/mockups/nyse-landing-page.html` | Landing page — KPI strip, exchange board, market story, news crawl |
| `docs/mockups/nyse-live-ticker.html` | Live ticker / Observatory dashboard — 3-column layout with bottom ticker |

Both mockups include JS simulation engines that perturb economy state in real time.
