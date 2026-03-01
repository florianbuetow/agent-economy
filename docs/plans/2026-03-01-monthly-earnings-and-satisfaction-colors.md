# Monthly Earnings Chart + Satisfaction Color Fix — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a monthly earnings bar chart below the existing cumulative chart, and fix satisfaction chart colors so "Satisfied" uses yellow instead of green.

**Architecture:** Frontend-only changes. Derive monthly earnings from existing cumulative data points (no backend changes). Add a `--color-yellow` CSS variable to the theme system and use it in the QualitySection component.

**Tech Stack:** React, Chart.js (Bar chart), TypeScript, Tailwind CSS

---

### Task 1: Add `--color-yellow` to the theme system

**Files:**
- Modify: `services/observatory/frontend/src/index.css:16-21`
- Modify: `services/observatory/frontend/src/theme.ts:6-24` (ThemeDefinition interface)
- Modify: `services/observatory/frontend/src/theme.ts:26-93` (all three theme objects)
- Modify: `services/observatory/frontend/src/theme.ts:107-135` (applyTheme function)

**Step 1: Add `--color-yellow` CSS variable to index.css**

In `index.css`, after the `--color-green-light` line (line 17), add:

```css
  --color-yellow: #b8860b;
```

**Step 2: Add `yellow` to the ThemeDefinition interface in theme.ts**

Add `yellow: string;` after the `green` property in the `colors` interface.

**Step 3: Add `yellow` values to all three theme objects in theme.ts**

- newsprint: `yellow: "#b8860b"` (dark goldenrod — warm, distinct from green)
- ft: `yellow: "#a67b00"` (warm gold, fits FT salmon palette)
- gs: `yellow: "#9a7b00"` (muted gold, fits GS institutional palette)

**Step 4: Apply `--color-yellow` in the applyTheme function**

Add this line after the `--color-green` line:

```ts
root.style.setProperty("--color-yellow", theme.colors.yellow);
```

**Step 5: Commit**

```bash
git add services/observatory/frontend/src/index.css services/observatory/frontend/src/theme.ts
git commit -m "feat: add --color-yellow CSS variable to theme system"
```

---

### Task 2: Update QualitySection to use yellow for "Satisfied"

**Files:**
- Modify: `services/observatory/frontend/src/pages/AgentProfile.tsx:364-368`

**Step 1: Change the "Satisfied" row color**

In the `rows` array inside QualitySection (around line 365), change:

```ts
{ name: "★★  Satisfied", count: stats.satisfied, barColor: "var(--color-green)", textColor: "text-green" },
```

to:

```ts
{ name: "★★  Satisfied", count: stats.satisfied, barColor: "var(--color-yellow)", textColor: "text-yellow" },
```

**Step 2: Verify visually**

Open http://localhost:5173/observatory/agents/a-00000005-0000-4000-8000-000000000005 and confirm:
- Extremely Satisfied bars are green
- Satisfied bars are yellow/amber
- Dissatisfied bars are red
- All three themes show correct distinct colors

**Step 3: Commit**

```bash
git add services/observatory/frontend/src/pages/AgentProfile.tsx
git commit -m "fix: use yellow for Satisfied rating to distinguish from Extremely Satisfied"
```

---

### Task 3: Add MonthlyEarningsChart component

**Files:**
- Modify: `services/observatory/frontend/src/pages/AgentProfile.tsx:4-13` (imports)
- Modify: `services/observatory/frontend/src/pages/AgentProfile.tsx:22` (Chart.js registration)
- Modify: `services/observatory/frontend/src/pages/AgentProfile.tsx:346` (add new component after EarningsChart)

**Step 1: Update Chart.js imports and registration**

In the imports at the top (lines 4-12), add `BarElement` to the Chart.js import:

```ts
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  Filler,
  Tooltip,
} from "chart.js";
```

Import `Bar` alongside `Line` from react-chartjs-2:

```ts
import { Line, Bar } from "react-chartjs-2";
```

Update the registration line (line 22):

```ts
ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, BarElement, Filler, Tooltip);
```

**Step 2: Add the MonthlyEarningsChart component**

Insert this component after the existing `EarningsChart` component (after line 346, before the `QualitySection` comment):

```tsx
function MonthlyEarningsChart({
  data,
  height = 80,
}: {
  data: { timestamp: string; cumulative: number }[];
  height?: number;
}) {
  if (data.length < 2) {
    return (
      <div
        className="flex items-center justify-center text-[9px] font-mono text-text-faint border border-border border-dashed"
        style={{ height }}
      >
        Not enough data
      </div>
    );
  }

  // Derive monthly totals from cumulative data
  const monthlyMap = new Map<string, number>();
  let prevCumulative = 0;
  for (const point of data) {
    const dt = new Date(point.timestamp);
    const key = `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}`;
    const delta = point.cumulative - prevCumulative;
    monthlyMap.set(key, (monthlyMap.get(key) ?? 0) + delta);
    prevCumulative = point.cumulative;
  }

  const sortedKeys = Array.from(monthlyMap.keys()).sort();
  const labels = sortedKeys.map((k) => {
    const [, m] = k.split("-");
    const monthNames = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    return monthNames[parseInt(m, 10) - 1];
  });
  const values = sortedKeys.map((k) => monthlyMap.get(k) ?? 0);

  const accent = getComputedStyle(document.documentElement).getPropertyValue("--color-green").trim() || "#1a7a1a";
  const textMuted = getComputedStyle(document.documentElement).getPropertyValue("--color-text-muted").trim() || "#888888";
  const borderColor = getComputedStyle(document.documentElement).getPropertyValue("--color-border").trim() || "#cccccc";

  const chartData = {
    labels,
    datasets: [
      {
        data: values,
        backgroundColor: accent + "99",
        borderColor: accent,
        borderWidth: 1,
      },
    ],
  };

  const options = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      tooltip: {
        intersect: false,
        backgroundColor: "#111111",
        titleFont: { family: "'Courier New', monospace", size: 10 },
        bodyFont: { family: "'Courier New', monospace", size: 11 },
        padding: 8,
        displayColors: false,
        callbacks: {
          title: (items: { dataIndex: number }[]) => {
            const i = items[0].dataIndex;
            return sortedKeys[i];
          },
          label: (item: { parsed: { y: number } }) =>
            `${item.parsed.y.toLocaleString()} © earned`,
        },
      },
    },
    scales: {
      x: {
        display: true,
        ticks: {
          font: { family: "'Courier New', monospace", size: 8 },
          color: textMuted,
          maxRotation: 0,
        },
        grid: { display: false },
        border: { color: borderColor },
      },
      y: {
        display: true,
        ticks: {
          font: { family: "'Courier New', monospace", size: 8 },
          color: textMuted,
          maxTicksLimit: 4,
          callback: (val: number | string) => {
            const v = Number(val);
            return v >= 1000 ? `${(v / 1000).toFixed(1)}k` : v;
          },
        },
        grid: { color: borderColor, lineWidth: 0.5 },
        border: { display: false },
        beginAtZero: true,
      },
    },
  } as const;

  return (
    <div style={{ height }}>
      <Bar data={chartData} options={options as Parameters<typeof Bar>[0]["options"]} />
    </div>
  );
}
```

**Step 3: Commit**

```bash
git add services/observatory/frontend/src/pages/AgentProfile.tsx
git commit -m "feat: add MonthlyEarningsChart component with bar chart"
```

---

### Task 4: Wire MonthlyEarningsChart into ReputationPanel

**Files:**
- Modify: `services/observatory/frontend/src/pages/AgentProfile.tsx:436-456` (ReputationPanel, after existing Earnings over Time section)

**Step 1: Add the Monthly Earnings section**

In the `ReputationPanel` component, after the closing `</div>` of the "Earnings over Time" section (after the `{earnings && ( ... )}` block, around line 456), add a new section:

```tsx
      {earnings && earnings.data_points.length >= 2 && (
        <div className="px-3.5 py-3 border-b border-border">
          <div className="text-[9px] font-mono uppercase tracking-[1.5px] text-text-muted border-b border-border pb-1 mb-2">
            Monthly Earnings
          </div>
          <MonthlyEarningsChart data={earnings.data_points} height={80} />
        </div>
      )}
```

**Step 2: Verify visually**

Open http://localhost:5173/observatory/agents/a-00000005-0000-4000-8000-000000000005 and confirm:
- Monthly earnings bar chart appears below the cumulative chart
- Bars show per-month earnings
- Hover tooltip shows month + amount
- Chart scales properly

**Step 3: Commit**

```bash
git add services/observatory/frontend/src/pages/AgentProfile.tsx
git commit -m "feat: wire MonthlyEarningsChart into ReputationPanel"
```

---

### Task 5: Build verification

**Step 1: Run TypeScript build check**

```bash
cd services/observatory/frontend && npx tsc --noEmit
```

Expected: No type errors.

**Step 2: Run dev server and verify**

```bash
cd services/observatory/frontend && npm run dev
```

Open http://localhost:5173/observatory/agents/a-00000005-0000-4000-8000-000000000005 and verify:
- Monthly earnings bar chart below cumulative chart
- Satisfaction colors: green (extremely satisfied), yellow (satisfied), red (dissatisfied)
- All three themes display correctly

**Step 3: Final commit if any fixes needed, then done**
