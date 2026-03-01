# Quarterly Report Frontend Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the placeholder quarterly report page with a journal-style editorial layout displaying GDP, Tasks, Labor Market, and Notable Highlights from the economy, with a quarter selector for navigation.

**Architecture:** Single page component fetches data from `GET /api/quarterly-report?quarter=YYYY-QN`. A custom hook manages the fetch. Types are added to the shared types file. The page renders a vertical scroll layout alternating single-column hero sections and two-column detail grids.

**Tech Stack:** React 18, TypeScript, Tailwind CSS v4 (with custom theme tokens in `index.css`)

**Working directory:** `/Users/ryanzidago/Projects/agent-economy-group/agent-economy/.claude/worktrees/quarterly-report`

**Design doc:** `docs/plans/2026-03-01-quarterly-report-frontend-design.md`

---

### Task 1: Add quarterly report types to `types.ts`

**Files:**
- Modify: `services/observatory/frontend/src/types.ts`

**Step 1: Add types at the end of the file**

Add these interfaces after the existing `AgentListResponse` interface:

```typescript
// --- Quarterly Report ---
export interface QuarterlyPeriod {
  start: string;
  end: string;
}

export interface QuarterlyGDP {
  total: number;
  previous_quarter: number;
  delta_pct: number;
  per_agent: number;
}

export interface QuarterlyTasks {
  posted: number;
  completed: number;
  disputed: number;
  completion_rate: number;
}

export interface QuarterlyLaborMarket {
  avg_bids_per_task: number;
  avg_time_to_acceptance_minutes: number;
  avg_reward: number;
}

export interface QuarterlySpecQuality {
  avg_score: number;
  previous_quarter_avg: number;
  delta_pct: number;
}

export interface QuarterlyAgents {
  new_registrations: number;
  total_at_quarter_end: number;
}

export interface NotableTask {
  task_id: string;
  title: string;
  reward?: number;
  bid_count?: number;
}

export interface NotableAgent {
  agent_id: string;
  name: string;
  earned?: number;
  spent?: number;
}

export interface QuarterlyNotable {
  highest_value_task: NotableTask | null;
  most_competitive_task: NotableTask | null;
  top_workers: NotableAgent[];
  top_posters: NotableAgent[];
}

export interface QuarterlyReportResponse {
  quarter: string;
  period: QuarterlyPeriod;
  gdp: QuarterlyGDP;
  tasks: QuarterlyTasks;
  labor_market: QuarterlyLaborMarket;
  spec_quality: QuarterlySpecQuality;
  agents: QuarterlyAgents;
  notable: QuarterlyNotable;
}
```

**Step 2: Verify TypeScript compiles**

Run from `services/observatory/frontend/`:
```bash
npx tsc --noEmit
```
Expected: No errors.

**Step 3: Commit**

```bash
git add services/observatory/frontend/src/types.ts
git commit -m "feat(observatory): add quarterly report TypeScript types"
```

---

### Task 2: Create API client function

**Files:**
- Create: `services/observatory/frontend/src/api/quarterly.ts`

**Step 1: Write the API client**

```typescript
import type { QuarterlyReportResponse } from "../types";
import { fetchJSON } from "./client";

export function fetchQuarterlyReport(
  quarter: string,
): Promise<QuarterlyReportResponse> {
  return fetchJSON<QuarterlyReportResponse>(
    `/api/quarterly-report?quarter=${encodeURIComponent(quarter)}`,
  );
}
```

**Step 2: Verify TypeScript compiles**

Run from `services/observatory/frontend/`:
```bash
npx tsc --noEmit
```
Expected: No errors.

**Step 3: Commit**

```bash
git add services/observatory/frontend/src/api/quarterly.ts
git commit -m "feat(observatory): add quarterly report API client"
```

---

### Task 3: Create `useQuarterlyReport` hook

**Files:**
- Create: `services/observatory/frontend/src/hooks/useQuarterlyReport.ts`

**Step 1: Write the hook**

```typescript
import { useEffect, useState } from "react";
import type { QuarterlyReportResponse } from "../types";
import { fetchQuarterlyReport } from "../api/quarterly";

interface UseQuarterlyReportResult {
  report: QuarterlyReportResponse | null;
  loading: boolean;
  error: string | null;
}

function currentQuarterLabel(): string {
  const now = new Date();
  const q = Math.ceil((now.getMonth() + 1) / 3);
  return `${now.getFullYear()}-Q${q}`;
}

export function useQuarterlyReport(quarter: string): UseQuarterlyReportResult {
  const [report, setReport] = useState<QuarterlyReportResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);

    fetchQuarterlyReport(quarter)
      .then((data) => {
        if (active) {
          setReport(data);
          setLoading(false);
        }
      })
      .catch((e) => {
        if (active) {
          setError(e instanceof Error ? e.message : "Unknown error");
          setReport(null);
          setLoading(false);
        }
      });

    return () => {
      active = false;
    };
  }, [quarter]);

  return { report, loading, error };
}

export { currentQuarterLabel };
```

**Step 2: Verify TypeScript compiles**

Run from `services/observatory/frontend/`:
```bash
npx tsc --noEmit
```
Expected: No errors.

**Step 3: Commit**

```bash
git add services/observatory/frontend/src/hooks/useQuarterlyReport.ts
git commit -m "feat(observatory): add useQuarterlyReport hook"
```

---

### Task 4: Build the QuarterlyReport page

**Files:**
- Modify: `services/observatory/frontend/src/pages/QuarterlyReport.tsx` (replace placeholder entirely)

**Step 1: Replace the placeholder with the full page**

Reference the design doc at `docs/plans/2026-03-01-quarterly-report-frontend-design.md` for the journal-style layout. The page should:

1. **Header** (single column, centered): "AGENT TASK ECONOMY / QUARTERLY REPORT · 2026-Q1" with period dates and quarter navigation arrows
2. **GDP Hero** (single column): Large GDP total number, delta % vs previous quarter, per-agent figure
3. **Tasks + Labor Market** (two columns side by side): Left column shows posted/completed/disputed/completion rate. Right column shows avg bids, acceptance time, avg reward
4. **Notable** (mixed): Two-column for notable tasks (highest value + most competitive), then two-column for top workers + top posters

Quarter navigation: `< Prev` and `Next >` buttons that shift the quarter string. Handle edge cases (no previous data = show error message, not crash).

```tsx
import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuarterlyReport, currentQuarterLabel } from "../hooks/useQuarterlyReport";

function shiftQuarter(quarter: string, delta: number): string {
  const match = quarter.match(/^(\d{4})-Q([1-4])$/);
  if (!match) return quarter;
  let year = parseInt(match[1], 10);
  let q = parseInt(match[2], 10) + delta;
  while (q > 4) { q -= 4; year += 1; }
  while (q < 1) { q += 4; year -= 1; }
  return `${year}-Q${q}`;
}

function formatPeriod(start: string, end: string): string {
  const s = new Date(start);
  const e = new Date(end);
  const fmt = (d: Date) =>
    d.toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric", timeZone: "UTC" });
  return `${fmt(s)} – ${fmt(e)}`;
}

export default function QuarterlyReport() {
  const [quarter, setQuarter] = useState(currentQuarterLabel);
  const { report, loading, error } = useQuarterlyReport(quarter);

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-[720px] mx-auto px-6 py-10">

        {/* Header */}
        <div className="text-center mb-10">
          <div className="text-[9px] uppercase tracking-[3px] text-text-muted mb-1">
            Agent Task Economy
          </div>
          <div className="text-[14px] uppercase tracking-[2px] text-text font-bold">
            Quarterly Report · {quarter}
          </div>
          {report && (
            <div className="text-[10px] text-text-muted mt-2">
              {formatPeriod(report.period.start, report.period.end)}
            </div>
          )}
          <div className="flex items-center justify-center gap-6 mt-4">
            <button
              onClick={() => setQuarter(shiftQuarter(quarter, -1))}
              className="text-[10px] text-text-muted hover:text-text cursor-pointer"
            >
              ← {shiftQuarter(quarter, -1)}
            </button>
            <button
              onClick={() => setQuarter(shiftQuarter(quarter, 1))}
              className="text-[10px] text-text-muted hover:text-text cursor-pointer"
            >
              {shiftQuarter(quarter, 1)} →
            </button>
          </div>
        </div>

        {/* Loading */}
        {loading && (
          <div className="text-center text-[10px] text-text-muted py-20">
            Loading report...
          </div>
        )}

        {/* Error */}
        {error && !loading && (
          <div className="text-center py-20">
            <div className="text-[10px] text-text-muted">
              No data available for {quarter}
            </div>
            <Link
              to="/observatory"
              className="text-[9px] text-text-muted hover:text-text mt-4 inline-block"
            >
              ← Back to Observatory
            </Link>
          </div>
        )}

        {/* Report Content */}
        {report && !loading && (
          <>
            {/* GDP Hero */}
            <div className="text-center mb-10 py-6 border-t border-b border-border">
              <div className="text-[36px] font-bold text-text leading-none">
                {report.gdp.total.toLocaleString()}
              </div>
              <div className="text-[10px] text-text-muted mt-1">coins produced</div>
              <div className="text-[11px] text-text-mid mt-3">
                {report.gdp.delta_pct >= 0 ? "▲" : "▼"}{" "}
                {Math.abs(report.gdp.delta_pct)}% from previous quarter
                ({report.gdp.previous_quarter.toLocaleString()})
              </div>
              <div className="text-[10px] text-text-muted mt-1">
                {report.gdp.per_agent} per agent
              </div>
            </div>

            {/* Tasks + Labor Market — two columns */}
            <div className="grid grid-cols-2 gap-8 mb-10">
              {/* Tasks */}
              <div>
                <div className="text-[9px] uppercase tracking-[2px] text-text-muted border-b border-border pb-1 mb-3">
                  Tasks
                </div>
                <div className="space-y-2">
                  <div className="flex justify-between text-[11px]">
                    <span className="text-text-muted">Posted</span>
                    <span className="text-text font-bold">{report.tasks.posted.toLocaleString()}</span>
                  </div>
                  <div className="flex justify-between text-[11px]">
                    <span className="text-text-muted">Completed</span>
                    <span className="text-text font-bold">{report.tasks.completed.toLocaleString()}</span>
                  </div>
                  <div className="flex justify-between text-[11px]">
                    <span className="text-text-muted">Disputed</span>
                    <span className="text-text font-bold">{report.tasks.disputed}</span>
                  </div>
                  <div className="flex justify-between text-[11px] pt-2 border-t border-border">
                    <span className="text-text-muted">Completion rate</span>
                    <span className="text-text font-bold">
                      {(report.tasks.completion_rate * 100).toFixed(0)}%
                    </span>
                  </div>
                </div>
              </div>

              {/* Labor Market */}
              <div>
                <div className="text-[9px] uppercase tracking-[2px] text-text-muted border-b border-border pb-1 mb-3">
                  Labor Market
                </div>
                <div className="space-y-2">
                  <div className="flex justify-between text-[11px]">
                    <span className="text-text-muted">Avg bids / task</span>
                    <span className="text-text font-bold">{report.labor_market.avg_bids_per_task}</span>
                  </div>
                  <div className="flex justify-between text-[11px]">
                    <span className="text-text-muted">Avg acceptance</span>
                    <span className="text-text font-bold">{report.labor_market.avg_time_to_acceptance_minutes} min</span>
                  </div>
                  <div className="flex justify-between text-[11px]">
                    <span className="text-text-muted">Avg reward</span>
                    <span className="text-text font-bold">{report.labor_market.avg_reward} ¢</span>
                  </div>
                </div>
              </div>
            </div>

            {/* Notable */}
            <div className="mb-10">
              <div className="text-[9px] uppercase tracking-[2px] text-text-muted border-b border-border pb-1 mb-4">
                Notable
              </div>

              {/* Notable tasks — two columns */}
              {(report.notable.highest_value_task || report.notable.most_competitive_task) && (
                <div className="grid grid-cols-2 gap-8 mb-6">
                  {report.notable.highest_value_task && (
                    <div>
                      <div className="text-[9px] text-text-muted uppercase tracking-[1px] mb-1">
                        Highest-Value Task
                      </div>
                      <div className="text-[11px] text-text font-bold">
                        "{report.notable.highest_value_task.title}"
                      </div>
                      <div className="text-[10px] text-text-muted mt-0.5">
                        {report.notable.highest_value_task.reward?.toLocaleString()} coins
                      </div>
                    </div>
                  )}
                  {report.notable.most_competitive_task && (
                    <div>
                      <div className="text-[9px] text-text-muted uppercase tracking-[1px] mb-1">
                        Most-Competitive Task
                      </div>
                      <div className="text-[11px] text-text font-bold">
                        "{report.notable.most_competitive_task.title}"
                      </div>
                      <div className="text-[10px] text-text-muted mt-0.5">
                        {report.notable.most_competitive_task.bid_count} bids
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* Leaderboards — two columns */}
              <div className="grid grid-cols-2 gap-8">
                {/* Top Workers */}
                <div>
                  <div className="text-[9px] text-text-muted uppercase tracking-[1px] mb-2">
                    Top Workers
                  </div>
                  {report.notable.top_workers.length === 0 && (
                    <div className="text-[10px] text-text-faint">No data</div>
                  )}
                  {report.notable.top_workers.map((w, i) => (
                    <div key={w.agent_id} className="flex justify-between text-[11px] py-0.5">
                      <span className="text-text">
                        <span className="text-text-faint mr-1">{i + 1}.</span>
                        {w.name}
                      </span>
                      <span className="text-text-muted">{w.earned?.toLocaleString()} earned</span>
                    </div>
                  ))}
                </div>

                {/* Top Posters */}
                <div>
                  <div className="text-[9px] text-text-muted uppercase tracking-[1px] mb-2">
                    Top Posters
                  </div>
                  {report.notable.top_posters.length === 0 && (
                    <div className="text-[10px] text-text-faint">No data</div>
                  )}
                  {report.notable.top_posters.map((p, i) => (
                    <div key={p.agent_id} className="flex justify-between text-[11px] py-0.5">
                      <span className="text-text">
                        <span className="text-text-faint mr-1">{i + 1}.</span>
                        {p.name}
                      </span>
                      <span className="text-text-muted">{p.spent?.toLocaleString()} spent</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            {/* Footer */}
            <div className="text-center border-t border-border pt-4">
              <Link
                to="/observatory"
                className="text-[9px] text-text-muted hover:text-text"
              >
                ← Back to Observatory
              </Link>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
```

**Step 2: Verify TypeScript compiles**

Run from `services/observatory/frontend/`:
```bash
npx tsc --noEmit
```
Expected: No errors.

**Step 3: Verify the build succeeds**

Run from `services/observatory/frontend/`:
```bash
npm run build
```
Expected: Build completes with no errors.

**Step 4: Commit**

```bash
git add services/observatory/frontend/src/pages/QuarterlyReport.tsx
git commit -m "feat(observatory): build quarterly report page with journal layout"
```

---

### Task 5: Visual verification

**Step 1: Start the observatory service**

Run from `services/observatory/`:
```bash
just run
```

**Step 2: Open browser and verify**

Navigate to `http://localhost:8006/observatory/quarterly`

Verify:
- Header shows quarter label and period dates
- Quarter navigation arrows work (clicking prev/next updates the quarter and refetches)
- GDP hero number displays prominently
- Tasks and Labor Market sections render side-by-side
- Notable section shows tasks and leaderboards
- Error state shows gracefully when navigating to a quarter with no data
- Loading state shows while fetching
- Back link navigates to observatory

**Step 3: Commit any visual fixes**

```bash
git add -u
git commit -m "fix(observatory): polish quarterly report styling"
```
