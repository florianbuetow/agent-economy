# NYSE Dark Theme — Gap Analysis

## Executive Summary

Adding the NYSE dark theme via the existing theme switcher requires changes to **8 existing files** plus **1 new component** across **two tiers of effort**. The theme system itself is well-architected — adding the color definition is trivial. The real work is replacing ~53 hardcoded color values scattered across components that currently assume a light background, plus building the new bottom ticker banner.

The good news: the Observatory dashboard (GDP panel, vitals bar, leaderboard, top nav) is **almost entirely theme-aware** already. The problematic areas are concentrated in the **EconomyGraph canvas engine** (which only appears on the landing page) and the **LiveFeed badge colors** (which are decorative and actually work fine on dark backgrounds as-is).

The mockup also introduces a **bottom ticker banner** — a continuously scrolling strip of aggregate economy metrics and narrative alerts that frames the Observatory between the vitals bar (top) and ticker (bottom). This is a new component (`BottomTicker.tsx`) that uses only existing hooks and theme-aware CSS variables, so it works across all themes out of the box.

## Tier 1 — Minimal Viable Theme (30 minutes, 3 files)

These changes give you a working dark theme for the Observatory dashboard — the page you actually care about most.

### 1.1 Add theme definition — `src/theme.ts`

Add the `nyse` entry to the `THEMES` object (~25 lines):

```typescript
nyse: {
  name: "NYSE",
  description: "Dark terminal with neon accents. Trading-floor energy.",
  colors: {
    bg:           "#0b0e14",
    bgOff:        "#111620",
    bgDark:       "#070a0f",
    border:       "#1c2333",
    borderStrong: "#2a3654",
    text:         "#e2e8f0",
    textMid:      "#8b99b0",
    textMuted:    "#4a5568",
    textFaint:    "#2a3654",
    green:        "#00e676",
    greenLight:   "#00e67622",
    red:          "#ff5252",
    redLight:     "#ff525222",
    amber:        "#ff9100",
    amberLight:   "#ff910022",
    yellow:       "#ffd740",
    navBg:        "#0b0e14",
    navText:      "#00e5ff",
    navTextMuted: "#4a5568",
    navBorder:    "#1c2333",
  },
  font: "'SF Mono', 'Fira Code', 'JetBrains Mono', Consolas, monospace",
  radius: "0px",
},
```

### 1.2 Register in theme switcher — `src/components/landing/ThemeSwitcher.tsx`

One-line change:

```typescript
// Before
const THEME_KEYS = ["newsprint", "ft", "gs"] as const;

// After
const THEME_KEYS = ["newsprint", "ft", "gs", "nyse"] as const;
```

### 1.3 Fix HatchBar hardcoded fill — `src/components/HatchBar.tsx`

The hatch bar uses a hardcoded `#e6f4e6` (light green) that looks wrong on dark. Replace with the theme's `greenLight` variable:

```tsx
// Before (line ~16)
backgroundColor: "#e6f4e6"

// After
backgroundColor: "var(--color-green-light)"
```

### What works after Tier 1

After these 3 changes, switching to the NYSE theme gives you a fully working dark Observatory dashboard: the GDP panel, vitals bar, leaderboard, top nav, and all their sparklines, badges, and hatch bars render correctly. The theme system handles `bg-bg`, `text-text`, `border-border`, `text-green`, etc. automatically.

### What still looks wrong after Tier 1

Two areas have hardcoded colors that won't respond to the theme switch:

1. **LiveFeed badge colors** — 12 badge types each have a hardcoded hex color. However, these are muted mid-tones (#4a6fa5, #a06080, etc.) that actually look *fine* on both light and dark backgrounds. The only issue is `color: "#fff"` on the active filter buttons, which is white-on-white in a light theme but correct on dark. **Verdict: low priority, works acceptably as-is.**

2. **EconomyGraph** (landing page hero only) — The canvas engine has ~30 hardcoded colors for node tints, wireframe palette, and stroke colors, all assuming a light background. This graph does not appear on the Observatory page, so it doesn't affect the main dashboard. **Verdict: separate scope, landing page only.**

## Tier 2 — Full Polish (2–3 hours, 5 additional files)

These changes fix the remaining hardcoded colors for pixel-perfect dark theme support across all pages.

### 2.1 LiveFeed badge and filter colors — `src/components/LiveFeed.tsx`

**12 badge colors (lines 44–55):** These muted mid-tones work on dark backgrounds without changes. However, for maximum contrast on dark, you could brighten them 15–20%. This is optional.

**3 hardcoded `color: "#fff"` instances (lines 63, 120, 133):** Replace with `"var(--color-text)"` so text adapts to theme:

```tsx
// Lines 63, 120, 133 — replace:
color: "#fff"
// With:
color: "var(--color-bg)"
```

**Filter button hover handlers (lines 116–128):** The `onMouseEnter`/`onMouseLeave` handlers set inline `backgroundColor`, `borderColor`, and `color` using a mix of hardcoded and CSS-var values. Replace `color: "#fff"` with `"var(--color-bg)"`.

### 2.2 GDPPanel phase badge — `src/components/GDPPanel.tsx`

**3 instances of `text: "#fff"` (lines 50, 52, 54):** The `phaseColor()` function returns hardcoded white text for phase badges. Replace with the theme's background color:

```tsx
// Before
{ bg: "var(--color-green)", border: "var(--color-green)", text: "#fff" }

// After
{ bg: "var(--color-green)", border: "var(--color-green)", text: "var(--color-bg)" }
```

### 2.3 Status colors — `src/utils/colorUtils.ts`

**5 hardcoded values (lines 16–25):** These are used in TaskDrilldown (not Observatory main page):

| Value | Current | Issue | Fix |
|-------|---------|-------|-----|
| `statusColors.accepted.text` | `#fff` | White text, fine on dark badge bg | No change needed |
| `statusColors.ruled.text` | `#fff` | White text, fine on dark badge bg | No change needed |
| `statusColors.disputeBg` | `#fdf3f3` | Light pink, glaring on dark bg | Use `var(--color-red-light)` |
| `statusColors.rulingBg` | `#e2d5f8` | Light lavender, glaring on dark bg | New CSS var or `rgba(106,74,128,0.12)` |
| `tooltipBg` | `#111111` | Dark tooltip, invisible on dark bg | Use `var(--color-bg-off)` |

### 2.4 EconomyGraph engine — `src/components/graph/` (types.ts + engine.ts)

This is the largest single block of work, but it only affects the landing page hero animation:

**Wireframe palette `W` (types.ts, 10 values):** Replace with CSS variable reads using `cssVar()` from colorUtils:

```typescript
// Before — hardcoded light palette
const W = { bg: "#ffffff", bgCanvas: "#fafafa", text: "#111111", ... };

// After — reads from current theme
function getWireframePalette() {
  return {
    bg:          cssVar("--color-bg", "#ffffff"),
    bgCanvas:    cssVar("--color-bg-off", "#fafafa"),
    bgNode:      cssVar("--color-bg-dark", "#f0f0f0"),
    border:      cssVar("--color-border", "#cccccc"),
    borderStrong:cssVar("--color-border-strong", "#333333"),
    text:        cssVar("--color-text", "#111111"),
    textMid:     cssVar("--color-text-mid", "#444444"),
    textMuted:   cssVar("--color-text-muted", "#888888"),
    textFaint:   cssVar("--color-text-faint", "#bbbbbb"),
    hatchStroke: cssVar("--color-border", "#aaaaaa"),
  };
}
```

**State tints (types.ts, 12 values):** These pastel tints need dark-aware alternatives. Two approaches:

- **Option A (simpler):** Keep the same colors but reduce opacity so they blend with any background. Change `#fff3b0` → `rgba(255,243,176,0.15)`.
- **Option B (thorough):** Add a `tints` section to `ThemeDefinition` and define per-theme tint palettes.

**Canvas strokes (engine.ts, 4 values):** Replace `rgba(51,51,51,...)` with a CSS-variable-based stroke that reads the border color:

```typescript
// Before
ctx.strokeStyle = "rgba(51,51,51,0.12)";

// After
const strokeBase = cssVar("--color-border-strong", "#333333");
ctx.strokeStyle = strokeBase + "1f"; // append alpha hex
```

**BG_COLOR constant (types.ts):** Replace `"#fafafa"` with `cssVar("--color-bg-off", "#fafafa")`.

## New Component: Bottom Ticker Banner

The live ticker mockup adds a scrolling banner at the bottom of the Observatory dashboard. This is a **new file** (`src/components/BottomTicker.tsx`), not a modification of an existing component.

### What it displays

Three categories of content scroll continuously, rebuilding every 15 seconds:

- **Cumulative aggregates** — `TASKS/ALL 1,243 +12 today`, `GDP/TOTAL 42,680 © +3,240 24h`, `ESCROW/LOCK 2,480 ©`, `PAID/OUT 40,200 ©`
- **Velocity & market health** — `GDP/RATE 135.2 ©/hr`, `POST/RATE 4.2/hr`, `BID/AVG 3.2/task`, `COMP/RATE 87%`, `UNEMP 12.0%`
- **Agent highlights & narrative alerts** — `TOP/EARNER Axiom-1 680 ©`, `⚡ ALERT Axiom-1 extends streak to 8 tasks`

### Gap assessment

No hardcoded colors needed. The component uses only theme CSS variables (`bg-bg-off`, `text-text-mid`, `border-border`, `text-green`, `text-amber`). It reuses the `ticker-scroll` keyframe animation already defined in `index.css` for the landing page `ActivityTicker`. Data comes from the existing `useMetrics` and `useAgents` hooks — no new API calls.

### Placement

Add below the 3-column `.main` div in `ObservatoryPage.tsx`. The outer layout becomes a flex column with the ticker at the bottom:

```tsx
<div className="flex flex-col h-screen">
  <TopNav />
  <VitalsBar />
  <div className="flex flex-1 min-h-0"> {/* 3-column main */}
    <GDPPanel />
    <LiveFeed />
    <Leaderboard />
  </div>
  <BottomTicker />  {/* new */}
</div>
```

### Effort estimate

~45 minutes for a polished implementation. The component itself is straightforward — most of the time is spent formatting the ticker items and tuning the scroll speed.

## Layout Changes (Optional)

The mockup uses a different column layout (30% / flex / 230px) vs the current code (210px / flex / 220px). This is purely a layout preference and is **independent of the theme**. It could be done as a separate change to `ObservatoryPage.tsx`:

```tsx
// Before
<div className="w-[210px] shrink-0 ...">  {/* GDP Panel */}
<div className="w-[220px] shrink-0 ...">  {/* Leaderboard */}

// After — percentage-based GDP panel
<div className="w-[30%] shrink-0 ...">    {/* GDP Panel */}
<div className="w-[230px] shrink-0 ...">  {/* Leaderboard */}
```

This layout change works across all themes, not just NYSE.

## File Change Summary

| File | Tier | Changes | Effort |
|------|------|---------|--------|
| `src/theme.ts` | 1 | Add NYSE theme definition | 5 min |
| `src/components/landing/ThemeSwitcher.tsx` | 1 | Add "nyse" to THEME_KEYS | 1 min |
| `src/components/HatchBar.tsx` | 1 | Replace 1 hardcoded color | 2 min |
| `src/components/BottomTicker.tsx` | 1+ | **New file** — scrolling aggregate ticker | 45 min |
| `src/pages/ObservatoryPage.tsx` | 1+ | Add `<BottomTicker />` + optional layout rebalance (30%/flex/230px) | 10 min |
| `src/components/LiveFeed.tsx` | 2 | Replace 3 `#fff` + hover handlers | 15 min |
| `src/components/GDPPanel.tsx` | 2 | Replace 3 `#fff` in phaseColor | 5 min |
| `src/utils/colorUtils.ts` | 2 | Replace 3 hardcoded status colors | 10 min |
| `src/components/graph/types.ts` | 2 | Replace ~28 hardcoded colors | 45 min |
| `src/components/graph/engine.ts` | 2 | Replace 4 canvas stroke colors | 20 min |

**Tier 1 total: ~8 minutes, 3 files** (theme definition only)
**Tier 1+ (with bottom ticker + layout): ~1 hour, 5 files**
**Tier 2 total: ~2 hours, 5 additional files** (full dark-theme polish)

## Recommendation

Ship Tier 1 first. It gives you a working NYSE dark theme on the Observatory dashboard in under 10 minutes. Then add the bottom ticker banner (Tier 1+) as a feature that works across all themes — it uses only CSS variables and existing hooks, so there are zero dark-theme gaps. Tier 2 can follow as polish when there's time; the badge colors in the live feed are mid-tones that work on dark backgrounds already, and the EconomyGraph is landing-page-only.

## Mockup References

| File | Description |
|------|-------------|
| `docs/mockups/nyse-landing-page.html` | Landing page in NYSE style |
| `docs/mockups/nyse-live-ticker.html` | Live ticker / Observatory dashboard (30% / flex / fixed layout) |
| `docs/mockups/nyse-theme-implementation-guide.md` | Theme slot mapping and instructions |
