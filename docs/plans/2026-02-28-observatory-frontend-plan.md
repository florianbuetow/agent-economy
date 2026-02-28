# Observatory Frontend Dashboard — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the Macro Observatory view — a live React dashboard consuming the Observatory backend API at port 8006.

**Architecture:** Three-column layout (GDPPanel | LiveFeed | Leaderboard) with a persistent VitalsBar and TopNav. Data flows from three hooks: `useMetrics` (polling), `useEventStream` (SSE), `useAgents` (one-shot fetch). All styling via Tailwind CSS mapped to the wireframe's monochrome design tokens.

**Tech Stack:** React 18, TypeScript (strict), Vite, Tailwind CSS v4, React Router v6, native fetch + EventSource.

**Worktree:** `/Users/ryanzidago/Projects/agent-economy-group/.claude/worktrees/observatory-frontend`
**Frontend root:** `services/observatory/frontend/`

---

### Task 1: Scaffold Vite + React + TypeScript project

**Files:**
- Create: `services/observatory/frontend/package.json`
- Create: `services/observatory/frontend/index.html`
- Create: `services/observatory/frontend/vite.config.ts`
- Create: `services/observatory/frontend/tsconfig.json`
- Create: `services/observatory/frontend/tsconfig.app.json`
- Create: `services/observatory/frontend/tsconfig.node.json`
- Create: `services/observatory/frontend/src/main.tsx`
- Create: `services/observatory/frontend/src/App.tsx`
- Create: `services/observatory/frontend/src/index.css`
- Create: `services/observatory/frontend/src/vite-env.d.ts`
- Create: `services/observatory/frontend/.gitignore`

**Step 1: Initialize the project**

```bash
cd services/observatory/frontend
npm create vite@latest . -- --template react-ts
```

If the directory already exists, accept overwrite prompts. This gives us the base scaffold.

**Step 2: Install dependencies**

```bash
cd services/observatory/frontend
npm install react-router-dom
npm install -D tailwindcss @tailwindcss/vite
```

**Step 3: Configure Vite with Tailwind and API proxy**

Replace `vite.config.ts` with:

```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/api": "http://localhost:8006",
      "/health": "http://localhost:8006",
    },
  },
});
```

**Step 4: Configure Tailwind in index.css**

Replace `src/index.css` with:

```css
@import "tailwindcss";

@theme {
  --font-mono: "Courier New", Courier, monospace;
  --font-serif: Georgia, serif;

  --color-bg: #ffffff;
  --color-bg-off: #f7f7f7;
  --color-bg-dark: #eeeeee;
  --color-border: #cccccc;
  --color-border-strong: #333333;
  --color-text: #111111;
  --color-text-mid: #444444;
  --color-text-muted: #888888;
  --color-text-faint: #bbbbbb;
}

@keyframes pulse-dot {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}
```

**Step 5: Set up minimal App.tsx**

Replace `src/App.tsx` with:

```tsx
export default function App() {
  return <div className="font-mono text-text bg-bg min-h-screen">Observatory</div>;
}
```

**Step 6: Set up main.tsx entry point**

Replace `src/main.tsx` with:

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>
);
```

**Step 7: Verify dev server starts**

```bash
cd services/observatory/frontend && npm run dev
```

Visit http://localhost:5173 — should show "Observatory" in monospace.

**Step 8: Commit**

```bash
git add services/observatory/frontend/
git commit -m "feat(observatory): scaffold Vite + React + TypeScript + Tailwind frontend"
```

---

### Task 2: TypeScript types for API responses

**Files:**
- Create: `services/observatory/frontend/src/types.ts`

These types mirror the backend Pydantic schemas in `services/observatory/src/observatory_service/schemas.py`.

**Step 1: Write the types file**

```ts
// types.ts — mirrors backend schemas.py

// --- Metrics ---
export interface RewardDistribution {
  "0_to_10": number;
  "11_to_50": number;
  "51_to_100": number;
  over_100: number;
}

export interface GDPMetrics {
  total: number;
  last_24h: number;
  last_7d: number;
  per_agent: number;
  rate_per_hour: number;
}

export interface AgentMetrics {
  total_registered: number;
  active: number;
  with_completed_tasks: number;
}

export interface TaskMetrics {
  total_created: number;
  completed_all_time: number;
  completed_24h: number;
  open: number;
  in_execution: number;
  disputed: number;
  completion_rate: number;
}

export interface EscrowMetrics {
  total_locked: number;
}

export interface SpecQualityMetrics {
  avg_score: number;
  extremely_satisfied_pct: number;
  satisfied_pct: number;
  dissatisfied_pct: number;
  trend_direction: string;
  trend_delta: number;
}

export interface LaborMarketMetrics {
  avg_bids_per_task: number;
  avg_reward: number;
  task_posting_rate: number;
  acceptance_latency_minutes: number;
  unemployment_rate: number;
  reward_distribution: RewardDistribution;
}

export interface EconomyPhaseMetrics {
  phase: string;
  task_creation_trend: string;
  dispute_rate: number;
}

export interface MetricsResponse {
  gdp: GDPMetrics;
  agents: AgentMetrics;
  tasks: TaskMetrics;
  escrow: EscrowMetrics;
  spec_quality: SpecQualityMetrics;
  labor_market: LaborMarketMetrics;
  economy_phase: EconomyPhaseMetrics;
  computed_at: string;
}

// --- GDP History ---
export interface GDPDataPoint {
  timestamp: string;
  gdp: number;
}

export interface GDPHistoryResponse {
  window: string;
  resolution: string;
  data_points: GDPDataPoint[];
}

// --- Events ---
export interface EventItem {
  event_id: number;
  event_source: string;
  event_type: string;
  timestamp: string;
  task_id: string | null;
  agent_id: string | null;
  summary: string;
  payload: Record<string, unknown>;
}

export interface EventsResponse {
  events: EventItem[];
  has_more: boolean;
  oldest_event_id: number | null;
  newest_event_id: number | null;
}

// --- Agents ---
export interface QualityStats {
  extremely_satisfied: number;
  satisfied: number;
  dissatisfied: number;
}

export interface AgentStats {
  tasks_posted: number;
  tasks_completed_as_worker: number;
  total_earned: number;
  total_spent: number;
  spec_quality: QualityStats;
  delivery_quality: QualityStats;
}

export interface AgentListItem {
  agent_id: string;
  name: string;
  registered_at: string;
  stats: AgentStats;
}

export interface AgentListResponse {
  agents: AgentListItem[];
  total_count: number;
  limit: number;
  offset: number;
}
```

**Step 2: Verify TypeScript compiles**

```bash
cd services/observatory/frontend && npx tsc --noEmit
```

Expected: no errors.

**Step 3: Commit**

```bash
git add services/observatory/frontend/src/types.ts
git commit -m "feat(observatory): add TypeScript types matching backend API schemas"
```

---

### Task 3: API client functions

**Files:**
- Create: `services/observatory/frontend/src/api/client.ts`
- Create: `services/observatory/frontend/src/api/metrics.ts`
- Create: `services/observatory/frontend/src/api/events.ts`
- Create: `services/observatory/frontend/src/api/agents.ts`

**Step 1: Write the base fetch client**

```ts
// api/client.ts
export async function fetchJSON<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`API error: ${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}
```

**Step 2: Write the metrics API**

```ts
// api/metrics.ts
import type { MetricsResponse, GDPHistoryResponse } from "../types";
import { fetchJSON } from "./client";

export function fetchMetrics(): Promise<MetricsResponse> {
  return fetchJSON<MetricsResponse>("/api/metrics");
}

export function fetchGDPHistory(
  window: string = "7d",
  resolution: string = "1h"
): Promise<GDPHistoryResponse> {
  return fetchJSON<GDPHistoryResponse>(
    `/api/metrics/gdp/history?window=${window}&resolution=${resolution}`
  );
}
```

**Step 3: Write the events API**

```ts
// api/events.ts
import type { EventsResponse } from "../types";
import { fetchJSON } from "./client";

export function fetchEvents(limit: number = 20): Promise<EventsResponse> {
  return fetchJSON<EventsResponse>(`/api/events?limit=${limit}`);
}
```

**Step 4: Write the agents API**

```ts
// api/agents.ts
import type { AgentListResponse } from "../types";
import { fetchJSON } from "./client";

export function fetchAgents(
  sortBy: string = "total_earned",
  order: string = "desc",
  limit: number = 10
): Promise<AgentListResponse> {
  return fetchJSON<AgentListResponse>(
    `/api/agents?sort_by=${sortBy}&order=${order}&limit=${limit}`
  );
}
```

**Step 5: Verify TypeScript compiles**

```bash
cd services/observatory/frontend && npx tsc --noEmit
```

**Step 6: Commit**

```bash
git add services/observatory/frontend/src/api/
git commit -m "feat(observatory): add API client functions for metrics, events, agents"
```

---

### Task 4: Custom hooks — useMetrics, useEventStream, useAgents

**Files:**
- Create: `services/observatory/frontend/src/hooks/useMetrics.ts`
- Create: `services/observatory/frontend/src/hooks/useEventStream.ts`
- Create: `services/observatory/frontend/src/hooks/useAgents.ts`

**Step 1: Write useMetrics hook**

Polls `/api/metrics` every 5 seconds. Also fetches GDP history once on mount.

```ts
// hooks/useMetrics.ts
import { useEffect, useState } from "react";
import type { MetricsResponse, GDPHistoryResponse } from "../types";
import { fetchMetrics } from "../api/metrics";
import { fetchGDPHistory } from "../api/metrics";

export function useMetrics(pollInterval = 5000) {
  const [metrics, setMetrics] = useState<MetricsResponse | null>(null);
  const [gdpHistory, setGdpHistory] = useState<GDPHistoryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    async function poll() {
      try {
        const data = await fetchMetrics();
        if (active) {
          setMetrics(data);
          setError(null);
        }
      } catch (e) {
        if (active) setError(e instanceof Error ? e.message : "Unknown error");
      }
    }

    poll();
    const id = setInterval(poll, pollInterval);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, [pollInterval]);

  useEffect(() => {
    let active = true;
    fetchGDPHistory("7d", "1h")
      .then((data) => {
        if (active) setGdpHistory(data);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, []);

  return { metrics, gdpHistory, error };
}
```

**Step 2: Write useEventStream hook**

Opens an `EventSource` to `/api/events/stream`. Supports pause/resume. Caps at 500 events.

```ts
// hooks/useEventStream.ts
import { useEffect, useRef, useState, useCallback } from "react";
import type { EventItem } from "../types";

const MAX_EVENTS = 500;

export function useEventStream() {
  const [events, setEvents] = useState<EventItem[]>([]);
  const [connected, setConnected] = useState(false);
  const [paused, setPaused] = useState(false);
  const pausedRef = useRef(false);

  const togglePause = useCallback(() => {
    setPaused((p) => {
      pausedRef.current = !p;
      return !p;
    });
  }, []);

  useEffect(() => {
    const es = new EventSource("/api/events/stream");

    es.addEventListener("economy_event", (e: MessageEvent) => {
      if (pausedRef.current) return;
      try {
        const event: EventItem = JSON.parse(e.data);
        setEvents((prev) => [event, ...prev].slice(0, MAX_EVENTS));
      } catch {
        // skip malformed events
      }
    });

    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);

    return () => es.close();
  }, []);

  return { events, connected, paused, togglePause };
}
```

**Step 3: Write useAgents hook**

Fetches worker and poster leaderboard data on mount.

```ts
// hooks/useAgents.ts
import { useEffect, useState } from "react";
import type { AgentListItem } from "../types";
import { fetchAgents } from "../api/agents";

export function useAgents() {
  const [workers, setWorkers] = useState<AgentListItem[]>([]);
  const [posters, setPosters] = useState<AgentListItem[]>([]);

  useEffect(() => {
    fetchAgents("total_earned", "desc", 10).then((res) =>
      setWorkers(res.agents)
    ).catch(() => {});
    fetchAgents("total_spent", "desc", 10).then((res) =>
      setPosters(res.agents)
    ).catch(() => {});
  }, []);

  return { workers, posters };
}
```

**Step 4: Verify TypeScript compiles**

```bash
cd services/observatory/frontend && npx tsc --noEmit
```

**Step 5: Commit**

```bash
git add services/observatory/frontend/src/hooks/
git commit -m "feat(observatory): add useMetrics, useEventStream, useAgents hooks"
```

---

### Task 5: Shared UI components — Sparkline, HatchBar, Badge

**Files:**
- Create: `services/observatory/frontend/src/components/Sparkline.tsx`
- Create: `services/observatory/frontend/src/components/HatchBar.tsx`
- Create: `services/observatory/frontend/src/components/Badge.tsx`

These are ported from the wireframe (`artefacts/observability_dashboard_wireframe.jsx`) with Tailwind styling.

**Step 1: Write Sparkline component**

Port from wireframe lines 48-72. SVG polyline from normalized data points.

```tsx
// components/Sparkline.tsx
interface SparklineProps {
  points: number[];
  width?: number;
  height?: number;
  fill?: boolean;
}

export default function Sparkline({
  points,
  width = 120,
  height = 28,
  fill = false,
}: SparklineProps) {
  if (points.length < 2) return null;

  const pts = points
    .map(
      (y, i) =>
        `${(i / (points.length - 1)) * width},${height - y * height}`
    )
    .join(" ");

  return (
    <svg width={width} height={height} className="block">
      {fill && (
        <polygon
          points={`0,${height} ${pts} ${width},${height}`}
          fill="#eeeeee"
          stroke="none"
        />
      )}
      <polyline
        points={pts}
        fill="none"
        stroke="#111111"
        strokeWidth={1.5}
        strokeLinejoin="round"
      />
    </svg>
  );
}
```

**Step 2: Write HatchBar component**

Port from wireframe lines 75-101. Hatched percentage bar.

```tsx
// components/HatchBar.tsx
interface HatchBarProps {
  pct: number;
  height?: number;
}

export default function HatchBar({ pct, height = 14 }: HatchBarProps) {
  return (
    <div
      className="relative w-full border border-border bg-bg-off"
      style={{ height }}
    >
      <div
        className="absolute left-0 top-0 bottom-0 border-r border-border-strong"
        style={{
          width: `${pct}%`,
          backgroundImage:
            "repeating-linear-gradient(45deg, #ccc 0, #ccc 1px, transparent 0, transparent 50%)",
          backgroundSize: "6px 6px",
        }}
      />
    </div>
  );
}
```

**Step 3: Write Badge component**

Port from wireframe lines 104-122.

```tsx
// components/Badge.tsx
interface BadgeProps {
  children: React.ReactNode;
  filled?: boolean;
  style?: React.CSSProperties;
}

export default function Badge({ children, filled = false, style }: BadgeProps) {
  return (
    <span
      className={`inline-block text-[8px] font-mono tracking-wide uppercase px-[5px] py-[2px] border border-border-strong ${
        filled ? "bg-border-strong text-bg" : "bg-bg text-text"
      }`}
      style={style}
    >
      {children}
    </span>
  );
}
```

**Step 4: Verify TypeScript compiles**

```bash
cd services/observatory/frontend && npx tsc --noEmit
```

**Step 5: Commit**

```bash
git add services/observatory/frontend/src/components/Sparkline.tsx services/observatory/frontend/src/components/HatchBar.tsx services/observatory/frontend/src/components/Badge.tsx
git commit -m "feat(observatory): add Sparkline, HatchBar, Badge shared components"
```

---

### Task 6: TopNav component

**Files:**
- Create: `services/observatory/frontend/src/components/TopNav.tsx`

Port from wireframe lines 581-643. "ATE OBSERVATORY" branding + nav tabs. Active tab has bold text and bottom border.

**Step 1: Write TopNav**

```tsx
// components/TopNav.tsx
import { NavLink } from "react-router-dom";

const NAV_ITEMS = [
  { to: "/observatory", label: "Macro Observatory" },
  { to: "/observatory/quarterly", label: "Quarterly Report" },
];

export default function TopNav() {
  return (
    <div className="flex items-center border-b border-border-strong bg-bg px-4 h-10 shrink-0">
      <div className="font-mono text-[11px] font-bold tracking-[2px] uppercase text-text mr-8 pr-8 border-r border-border">
        ATE OBSERVATORY
      </div>
      <div className="flex">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === "/observatory"}
            className={({ isActive }) =>
              `px-4 h-10 flex items-center font-mono text-[10px] uppercase tracking-[1px] border-b-2 ${
                isActive
                  ? "font-bold text-text border-text"
                  : "font-normal text-text-muted border-transparent"
              }`
            }
          >
            {item.label}
          </NavLink>
        ))}
      </div>
    </div>
  );
}
```

**Step 2: Commit**

```bash
git add services/observatory/frontend/src/components/TopNav.tsx
git commit -m "feat(observatory): add TopNav component with ATE OBSERVATORY branding"
```

---

### Task 7: VitalsBar component

**Files:**
- Create: `services/observatory/frontend/src/components/VitalsBar.tsx`

Port from wireframe lines 494-578. Persistent header strip showing key economy metrics with a LIVE indicator.

**Step 1: Write VitalsBar**

```tsx
// components/VitalsBar.tsx
import type { MetricsResponse } from "../types";

interface VitalsBarProps {
  metrics: MetricsResponse | null;
  connected: boolean;
}

interface Vital {
  label: string;
  value: string;
  delta?: string;
  up?: boolean;
}

function formatVitals(m: MetricsResponse): Vital[] {
  return [
    {
      label: "Active Agents",
      value: String(m.agents.active),
    },
    {
      label: "Open Tasks",
      value: String(m.tasks.open),
    },
    {
      label: "Completed (24h)",
      value: String(m.tasks.completed_24h),
    },
    {
      label: "GDP (Total)",
      value: m.gdp.total.toLocaleString(),
      delta: `${m.gdp.rate_per_hour.toFixed(1)}/hr`,
      up: true,
    },
    {
      label: "GDP / Agent",
      value: m.gdp.per_agent.toFixed(1),
    },
    {
      label: "Unemployment",
      value: `${(m.labor_market.unemployment_rate * 100).toFixed(1)}%`,
    },
    {
      label: "Escrow Locked",
      value: `${m.escrow.total_locked.toLocaleString()} ©`,
    },
  ];
}

export default function VitalsBar({ metrics, connected }: VitalsBarProps) {
  const vitals = metrics ? formatVitals(metrics) : [];

  return (
    <div className="flex items-center border-b border-border-strong bg-bg-off px-4 h-[38px] shrink-0">
      {vitals.map((v, i) => (
        <div
          key={v.label}
          className={`flex items-center gap-3 pr-4 mr-4 whitespace-nowrap ${
            i < vitals.length - 1 ? "border-r border-border" : ""
          }`}
        >
          <div>
            <div className="text-[8px] font-mono uppercase tracking-[1.5px] text-text-muted">
              {v.label}
            </div>
            <div className="flex items-baseline gap-1">
              <span className="text-[13px] font-bold font-mono text-text">
                {v.value}
              </span>
              {v.delta && (
                <span className="text-[10px] font-mono text-text-mid">
                  {v.up ? "↑" : "↓"}
                  {v.delta}
                </span>
              )}
            </div>
          </div>
        </div>
      ))}
      <div className="ml-auto flex items-center gap-1.5">
        <div
          className={`w-1.5 h-1.5 rounded-full ${
            connected ? "bg-border-strong" : "bg-text-muted"
          }`}
          style={{ animation: connected ? "pulse-dot 2s infinite" : "none" }}
        />
        <span className="text-[8px] font-mono uppercase tracking-[1.5px] text-text-muted">
          {connected ? "LIVE" : "OFFLINE"}
        </span>
      </div>
    </div>
  );
}
```

**Step 2: Commit**

```bash
git add services/observatory/frontend/src/components/VitalsBar.tsx
git commit -m "feat(observatory): add VitalsBar component with live economy metrics"
```

---

### Task 8: GDPPanel component (left column)

**Files:**
- Create: `services/observatory/frontend/src/components/GDPPanel.tsx`

Port from wireframe lines 646-840. Contains: Economy Output (GDP + sparkline), GDP per Agent, Economy Phase, Labor Market stats, Reward Distribution.

**Step 1: Write GDPPanel**

```tsx
// components/GDPPanel.tsx
import type { MetricsResponse, GDPHistoryResponse } from "../types";
import Sparkline from "./Sparkline";
import HatchBar from "./HatchBar";

interface GDPPanelProps {
  metrics: MetricsResponse | null;
  gdpHistory: GDPHistoryResponse | null;
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[9px] font-mono uppercase tracking-[1.5px] text-text-muted border-b border-border pb-[5px] mb-2">
      {children}
    </div>
  );
}

function Label({ children }: { children: React.ReactNode }) {
  return (
    <span className="text-[9px] font-mono uppercase tracking-[1.5px] text-text-muted">
      {children}
    </span>
  );
}

export default function GDPPanel({ metrics, gdpHistory }: GDPPanelProps) {
  if (!metrics) {
    return (
      <div className="flex flex-col h-full items-center justify-center text-text-muted font-mono text-[10px]">
        Loading...
      </div>
    );
  }

  const m = metrics;

  // Normalize GDP history to 0-1 range for sparkline
  const gdpPoints: number[] = [];
  const gdpAgentPoints: number[] = [];
  if (gdpHistory && gdpHistory.data_points.length > 0) {
    const values = gdpHistory.data_points.map((d) => d.gdp);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const range = max - min || 1;
    for (const v of values) {
      gdpPoints.push((v - min) / range);
    }
    // GDP per agent = GDP / active agents (approximate sparkline)
    const agentCount = m.agents.active || 1;
    const agentValues = values.map((v) => v / agentCount);
    const aMin = Math.min(...agentValues);
    const aMax = Math.max(...agentValues);
    const aRange = aMax - aMin || 1;
    for (const v of agentValues) {
      gdpAgentPoints.push((v - aMin) / aRange);
    }
  }

  const rewardDist = m.labor_market.reward_distribution;
  const totalRewards =
    rewardDist["0_to_10"] +
    rewardDist["11_to_50"] +
    rewardDist["51_to_100"] +
    rewardDist.over_100 || 1;

  return (
    <div className="flex flex-col h-full overflow-y-auto">
      {/* GDP */}
      <div className="p-3 border-b border-border">
        <SectionLabel>Economy Output</SectionLabel>
        <div className="mb-2.5">
          <Label>GDP — Total</Label>
          <div className="text-[28px] font-bold font-mono text-text leading-none mt-0.5">
            {m.gdp.total.toLocaleString()}
          </div>
          <div className="text-[10px] font-mono text-text-mid mt-0.5">
            ↑ {m.gdp.rate_per_hour.toFixed(1)} ©/hr
          </div>
        </div>
        <Sparkline points={gdpPoints} width={180} height={32} fill />
        <div className="text-[8px] font-mono uppercase tracking-[1.5px] text-text-muted mt-0.5">
          7-day output
        </div>
      </div>

      {/* GDP per Agent */}
      <div className="p-3 border-b border-border">
        <Label>GDP per Agent</Label>
        <div className="text-[22px] font-bold font-mono text-text leading-none mt-0.5">
          {m.gdp.per_agent.toFixed(1)}
        </div>
        <div className="text-[10px] font-mono text-text-mid mt-0.5">
          productivity per active agent
        </div>
        <div className="mt-2">
          <Sparkline points={gdpAgentPoints} width={180} height={24} />
        </div>
        <div className="mt-2 p-1.5 bg-bg-off border border-dashed border-border text-[9px] font-mono text-text-muted leading-[1.4]">
          GDP ↑ + GDP/Agent ↑ = real growth
          <br />
          GDP ↑ + GDP/Agent → = volume growth
        </div>
      </div>

      {/* Economy Phase */}
      <div className="p-3 border-b border-border">
        <SectionLabel>Economy Phase</SectionLabel>
        <div className="border border-border-strong p-2 flex items-center gap-2">
          <div className="w-2.5 h-2.5 rounded-full border-2 border-text bg-bg-dark shrink-0" />
          <div>
            <div className="text-[13px] font-bold font-mono uppercase">
              {m.economy_phase.phase}
            </div>
            <div className="text-[9px] font-mono text-text-muted">
              Task creation {m.economy_phase.task_creation_trend}, dispute rate{" "}
              {(m.economy_phase.dispute_rate * 100).toFixed(0)}%
            </div>
          </div>
        </div>
      </div>

      {/* Labor Market */}
      <div className="p-3 border-b border-border">
        <SectionLabel>Labor Market</SectionLabel>
        {[
          { label: "Avg bids / task", value: m.labor_market.avg_bids_per_task.toFixed(1) },
          {
            label: "Acceptance latency",
            value: `${Math.round(m.labor_market.acceptance_latency_minutes)} min`,
          },
          {
            label: "Completion rate",
            value: `${(m.tasks.completion_rate * 100).toFixed(0)}%`,
          },
          { label: "Avg task reward", value: `${Math.round(m.labor_market.avg_reward)} ©` },
        ].map((row) => (
          <div
            key={row.label}
            className="flex justify-between py-1 border-b border-dotted border-border items-baseline"
          >
            <span className="text-[10px] font-mono text-text-mid">
              {row.label}
            </span>
            <span className="text-[11px] font-bold font-mono">{row.value}</span>
          </div>
        ))}
      </div>

      {/* Reward Distribution */}
      <div className="p-3">
        <SectionLabel>Reward Distribution</SectionLabel>
        {[
          { range: "0 – 10 ©", count: rewardDist["0_to_10"] },
          { range: "11 – 50 ©", count: rewardDist["11_to_50"] },
          { range: "51 – 100 ©", count: rewardDist["51_to_100"] },
          { range: "100+ ©", count: rewardDist.over_100 },
        ].map((b) => (
          <div key={b.range} className="mb-1.5">
            <div className="flex justify-between mb-0.5">
              <span className="text-[9px] font-mono text-text-mid">
                {b.range}
              </span>
              <span className="text-[9px] font-mono">
                {Math.round((b.count / totalRewards) * 100)}%
              </span>
            </div>
            <HatchBar pct={Math.round((b.count / totalRewards) * 100)} height={10} />
          </div>
        ))}
      </div>
    </div>
  );
}
```

**Step 2: Commit**

```bash
git add services/observatory/frontend/src/components/GDPPanel.tsx
git commit -m "feat(observatory): add GDPPanel with GDP, sparklines, phase, labor market"
```

---

### Task 9: LiveFeed component (center column)

**Files:**
- Create: `services/observatory/frontend/src/components/LiveFeed.tsx`

Port from wireframe lines 843-1012. SSE-powered real-time event stream with pause/resume, event type badge filters, clickable task/agent links.

**Step 1: Write LiveFeed**

The SSE event shape from the backend (see `services/observatory/src/observatory_service/services/events.py:100-108`):
```json
{
  "event_id": 4521,
  "event_source": "board",
  "event_type": "task.created",
  "timestamp": "...",
  "task_id": "t-abc123",
  "agent_id": "a-def456",
  "summary": "Helix-7 posted \"Summarize macro report\" for 40 ©",
  "payload": {}
}
```

Map `event_type` to badge labels. The `summary` field already has human-readable text.

```tsx
// components/LiveFeed.tsx
import { Link } from "react-router-dom";
import type { EventItem } from "../types";

interface LiveFeedProps {
  events: EventItem[];
  paused: boolean;
  onTogglePause: () => void;
}

const EVENT_TYPE_TO_BADGE: Record<string, string> = {
  "task.created": "TASK",
  "bid.submitted": "BID",
  "task.approved": "PAYOUT",
  "task.accepted": "CONTRACT",
  "task.submitted": "SUBMIT",
  "bank.payout": "PAYOUT",
  "bank.escrow_locked": "ESCROW",
  "bank.escrow_released": "PAYOUT",
  "reputation.feedback_revealed": "REP",
  "identity.agent_registered": "AGENT",
  "court.claim_filed": "DISPUTE",
  "court.ruling_issued": "RULING",
};

const BADGE_STYLES: Record<string, string> = {
  TASK: "border border-border-strong bg-border-strong text-bg",
  BID: "border border-border-strong bg-bg text-text",
  PAYOUT: "border border-border-strong bg-border-strong text-bg",
  CONTRACT: "border border-border-strong bg-bg text-text",
  ESCROW: "border border-dashed border-text-muted bg-bg text-text-muted",
  REP: "border border-dotted border-border-strong bg-bg-dark text-text",
  AGENT: "border border-text-muted bg-bg text-text-mid",
  SUBMIT: "border border-border-strong bg-bg text-text",
  DISPUTE: "border border-border-strong bg-border-strong text-bg",
  RULING: "border border-border-strong bg-bg text-text",
};

const FILTER_OPTIONS = ["ALL", "TASK", "BID", "PAYOUT", "CONTRACT", "ESCROW", "REP"];

function getBadge(eventType: string): string {
  return EVENT_TYPE_TO_BADGE[eventType] || eventType.split(".")[0].toUpperCase();
}

function timeAgo(timestamp: string): string {
  const diff = Date.now() - new Date(timestamp).getTime();
  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ago`;
}

export default function LiveFeed({
  events,
  paused,
  onTogglePause,
}: LiveFeedProps) {
  const [filter, setFilter] = useState<string>("ALL");

  const visible =
    filter === "ALL"
      ? events
      : events.filter((e) => getBadge(e.event_type) === filter);

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Feed header */}
      <div className="p-2 px-3.5 border-b border-border flex items-center gap-2 shrink-0 flex-wrap">
        <div className="text-[9px] font-mono uppercase tracking-[1.5px] text-text-muted mr-2">
          Live Feed
        </div>
        <button
          onClick={onTogglePause}
          className={`px-2 py-[3px] border border-border-strong font-mono text-[9px] tracking-[1px] cursor-pointer ${
            paused
              ? "bg-border-strong text-bg"
              : "bg-bg text-text"
          }`}
        >
          {paused ? "▶ RESUME" : "⏸ PAUSE"}
        </button>
        <div className="flex gap-1 flex-wrap">
          {FILTER_OPTIONS.map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`px-1.5 py-[2px] border border-border font-mono text-[8px] tracking-[0.5px] cursor-pointer text-text-mid ${
                filter === f ? "bg-bg-dark" : "bg-bg"
              }`}
            >
              {f}
            </button>
          ))}
        </div>
      </div>

      {/* Feed stream */}
      <div className="flex-1 overflow-y-auto">
        {visible.length === 0 && (
          <div className="p-8 text-center font-mono text-[10px] text-text-faint">
            {events.length === 0
              ? "Waiting for events..."
              : "No events match filter"}
          </div>
        )}
        {visible.map((ev, i) => {
          const badge = getBadge(ev.event_type);
          return (
            <div
              key={ev.event_id}
              className={`py-2 px-3.5 border-b border-border flex items-start gap-2 ${
                i === 0 ? "bg-bg-off" : "bg-bg"
              }`}
            >
              {/* Badge */}
              <span
                className={`shrink-0 text-[8px] font-mono tracking-[0.5px] px-[5px] py-[2px] mt-[1px] ${
                  BADGE_STYLES[badge] || BADGE_STYLES.TASK
                }`}
              >
                {badge}
              </span>

              {/* Content */}
              <div className="flex-1 min-w-0">
                <div className="text-[11px] font-mono text-text leading-[1.5] flex flex-wrap gap-x-1 items-baseline">
                  <span>{ev.summary}</span>
                  {ev.task_id && (
                    <Link
                      to={`/observatory/tasks/${ev.task_id}`}
                      className="border-b border-dashed border-border-strong hover:underline"
                    >
                      {ev.task_id}
                    </Link>
                  )}
                </div>
              </div>

              {/* Timestamp */}
              <span className="shrink-0 text-[9px] font-mono text-text-faint mt-[2px]">
                {timeAgo(ev.timestamp)}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
```

**IMPORTANT:** This file also needs `useState` imported. Add to the import line:

```tsx
import { useState } from "react";
```

**Step 2: Commit**

```bash
git add services/observatory/frontend/src/components/LiveFeed.tsx
git commit -m "feat(observatory): add LiveFeed component with SSE events, pause/resume, filters"
```

---

### Task 10: Leaderboard component (right column)

**Files:**
- Create: `services/observatory/frontend/src/components/Leaderboard.tsx`

Port from wireframe lines 1014-1311. Workers/posters tabs. Workers show: rank, name (clickable), earned, tasks completed, delivery quality distribution. Posters show: rank, name, spent, tasks posted, spec quality distribution. Under the posters tab, also show the economy-wide spec quality bars.

**Step 1: Write Leaderboard**

```tsx
// components/Leaderboard.tsx
import { useState } from "react";
import { Link } from "react-router-dom";
import type { AgentListItem, MetricsResponse } from "../types";
import HatchBar from "./HatchBar";

interface LeaderboardProps {
  workers: AgentListItem[];
  posters: AgentListItem[];
  metrics: MetricsResponse | null;
}

function QualityDisplay({
  great,
  ok,
  bad,
  prefix,
}: {
  great: number;
  ok: number;
  bad: number;
  prefix?: string;
}) {
  return (
    <span className="text-[9px] font-mono text-text-muted">
      {prefix && `${prefix}: `}★★★{great} ★★{ok} ★{bad}
    </span>
  );
}

export default function Leaderboard({
  workers,
  posters,
  metrics,
}: LeaderboardProps) {
  const [tab, setTab] = useState<"workers" | "posters">("workers");

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Tab toggle */}
      <div className="flex border-b border-border shrink-0">
        {(["workers", "posters"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`flex-1 py-2 border-b-2 font-mono text-[9px] uppercase tracking-[1px] cursor-pointer ${
              tab === t
                ? "font-bold text-text border-text"
                : "font-normal text-text-muted border-transparent"
            } bg-bg border-x-0 border-t-0`}
          >
            {t}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto p-2.5 px-3.5">
        {tab === "workers" ? (
          <>
            <div className="text-[9px] font-mono uppercase tracking-[1.5px] text-text-muted mb-2">
              By Tasks Completed
            </div>
            {workers.map((w, i) => (
              <div
                key={w.agent_id}
                className="py-[7px] border-b border-dotted border-border flex items-start gap-2"
              >
                <span className="text-[11px] font-mono text-text-muted w-3.5 shrink-0">
                  {i + 1}
                </span>
                <div className="flex-1">
                  <div className="flex justify-between items-baseline">
                    <Link
                      to={`/observatory/agents/${w.agent_id}`}
                      className="text-[11px] font-bold font-mono border-b border-dashed border-border-strong hover:underline"
                    >
                      {w.name}
                    </Link>
                    <span className="text-[10px] font-mono">
                      {w.stats.total_earned} ©
                    </span>
                  </div>
                  <div className="flex justify-between mt-0.5">
                    <span className="text-[9px] font-mono text-text-muted">
                      {w.stats.tasks_completed_as_worker} tasks done
                    </span>
                    <QualityDisplay
                      great={w.stats.delivery_quality.extremely_satisfied}
                      ok={w.stats.delivery_quality.satisfied}
                      bad={w.stats.delivery_quality.dissatisfied}
                    />
                  </div>
                </div>
              </div>
            ))}
          </>
        ) : (
          <>
            <div className="text-[9px] font-mono uppercase tracking-[1.5px] text-text-muted mb-2">
              By Tasks Posted
            </div>
            {posters.map((p, i) => (
              <div
                key={p.agent_id}
                className="py-[7px] border-b border-dotted border-border flex items-start gap-2"
              >
                <span className="text-[11px] font-mono text-text-muted w-3.5 shrink-0">
                  {i + 1}
                </span>
                <div className="flex-1">
                  <div className="flex justify-between items-baseline">
                    <Link
                      to={`/observatory/agents/${p.agent_id}`}
                      className="text-[11px] font-bold font-mono border-b border-dashed border-border-strong hover:underline"
                    >
                      {p.name}
                    </Link>
                    <span className="text-[10px] font-mono">
                      {p.stats.total_spent} © spent
                    </span>
                  </div>
                  <div className="flex justify-between mt-0.5">
                    <span className="text-[9px] font-mono text-text-muted">
                      {p.stats.tasks_posted} tasks posted
                    </span>
                    <QualityDisplay
                      great={p.stats.spec_quality.extremely_satisfied}
                      ok={p.stats.spec_quality.satisfied}
                      bad={p.stats.spec_quality.dissatisfied}
                      prefix="spec"
                    />
                  </div>
                </div>
              </div>
            ))}

            {/* Economy spec quality */}
            {metrics && (
              <div className="mt-4">
                <div className="text-[9px] font-mono uppercase tracking-[1.5px] text-text-muted border-b border-border pb-[5px] mb-2">
                  Economy Spec Quality
                </div>
                {[
                  {
                    label: "★★★ Extremely satisfied",
                    pct: Math.round(metrics.spec_quality.extremely_satisfied_pct * 100),
                  },
                  {
                    label: "★★  Satisfied",
                    pct: Math.round(metrics.spec_quality.satisfied_pct * 100),
                  },
                  {
                    label: "★   Dissatisfied",
                    pct: Math.round(metrics.spec_quality.dissatisfied_pct * 100),
                  },
                ].map((r) => (
                  <div key={r.label} className="mb-1.5">
                    <div className="flex justify-between mb-0.5">
                      <span className="text-[9px] font-mono">{r.label}</span>
                      <span className="text-[9px] font-mono">{r.pct}%</span>
                    </div>
                    <HatchBar pct={r.pct} height={9} />
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
```

**Step 2: Commit**

```bash
git add services/observatory/frontend/src/components/Leaderboard.tsx
git commit -m "feat(observatory): add Leaderboard with workers/posters tabs and quality ratings"
```

---

### Task 11: ObservatoryPage — assemble the 3-column layout

**Files:**
- Create: `services/observatory/frontend/src/pages/ObservatoryPage.tsx`

This is the MacroView from the wireframe (lines 1314-1357). Three columns: GDPPanel (210px left), LiveFeed (flex center), Leaderboard (220px right).

**Step 1: Write ObservatoryPage**

```tsx
// pages/ObservatoryPage.tsx
import { useMetrics } from "../hooks/useMetrics";
import { useEventStream } from "../hooks/useEventStream";
import { useAgents } from "../hooks/useAgents";
import GDPPanel from "../components/GDPPanel";
import LiveFeed from "../components/LiveFeed";
import Leaderboard from "../components/Leaderboard";

export default function ObservatoryPage() {
  const { metrics, gdpHistory } = useMetrics();
  const { events, paused, togglePause } = useEventStream();
  const { workers, posters } = useAgents();

  return (
    <div className="flex-1 flex overflow-hidden">
      {/* LEFT: GDP + Labor — fixed width */}
      <div className="w-[210px] shrink-0 border-r border-border overflow-y-auto">
        <GDPPanel metrics={metrics} gdpHistory={gdpHistory} />
      </div>

      {/* CENTER: Live Feed */}
      <div className="flex-1 min-w-0 border-r border-border overflow-hidden flex flex-col">
        <LiveFeed
          events={events}
          paused={paused}
          onTogglePause={togglePause}
        />
      </div>

      {/* RIGHT: Leaderboard */}
      <div className="w-[220px] shrink-0 overflow-hidden flex flex-col">
        <Leaderboard workers={workers} posters={posters} metrics={metrics} />
      </div>
    </div>
  );
}
```

**Step 2: Commit**

```bash
git add services/observatory/frontend/src/pages/ObservatoryPage.tsx
git commit -m "feat(observatory): add ObservatoryPage with 3-column macro dashboard layout"
```

---

### Task 12: Placeholder pages + React Router setup

**Files:**
- Create: `services/observatory/frontend/src/pages/TaskDrilldown.tsx`
- Create: `services/observatory/frontend/src/pages/AgentProfile.tsx`
- Create: `services/observatory/frontend/src/pages/QuarterlyReport.tsx`
- Modify: `services/observatory/frontend/src/App.tsx`
- Modify: `services/observatory/frontend/src/main.tsx`

**Step 1: Write placeholder pages**

```tsx
// pages/TaskDrilldown.tsx
import { useParams, Link } from "react-router-dom";

export default function TaskDrilldown() {
  const { taskId } = useParams();
  return (
    <div className="flex-1 flex flex-col items-center justify-center font-mono text-text-muted gap-4">
      <div className="text-[11px] uppercase tracking-[2px]">
        Task Drilldown — coming soon
      </div>
      <div className="text-[13px] font-bold text-text">{taskId}</div>
      <Link
        to="/observatory"
        className="text-[9px] px-2 py-1 border border-border hover:bg-bg-off"
      >
        ← Back to Observatory
      </Link>
    </div>
  );
}
```

```tsx
// pages/AgentProfile.tsx
import { useParams, Link } from "react-router-dom";

export default function AgentProfile() {
  const { agentId } = useParams();
  return (
    <div className="flex-1 flex flex-col items-center justify-center font-mono text-text-muted gap-4">
      <div className="text-[11px] uppercase tracking-[2px]">
        Agent Profile — coming soon
      </div>
      <div className="text-[13px] font-bold text-text">{agentId}</div>
      <Link
        to="/observatory"
        className="text-[9px] px-2 py-1 border border-border hover:bg-bg-off"
      >
        ← Back to Observatory
      </Link>
    </div>
  );
}
```

```tsx
// pages/QuarterlyReport.tsx
import { Link } from "react-router-dom";

export default function QuarterlyReport() {
  return (
    <div className="flex-1 flex flex-col items-center justify-center font-mono text-text-muted gap-4">
      <div className="text-[11px] uppercase tracking-[2px]">
        Quarterly Report — coming soon
      </div>
      <Link
        to="/observatory"
        className="text-[9px] px-2 py-1 border border-border hover:bg-bg-off"
      >
        ← Back to Observatory
      </Link>
    </div>
  );
}
```

**Step 2: Update App.tsx with layout**

```tsx
// App.tsx
import { Outlet } from "react-router-dom";
import TopNav from "./components/TopNav";
import VitalsBar from "./components/VitalsBar";
import { useMetrics } from "./hooks/useMetrics";
import { useEventStream } from "./hooks/useEventStream";

export default function App() {
  const { metrics } = useMetrics();
  const { connected } = useEventStream();

  return (
    <div className="font-mono text-text bg-bg h-screen flex flex-col">
      <TopNav />
      <VitalsBar metrics={metrics} connected={connected} />
      <Outlet />
    </div>
  );
}
```

**Step 3: Update main.tsx with router**

```tsx
// main.tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider, Navigate } from "react-router-dom";
import App from "./App";
import ObservatoryPage from "./pages/ObservatoryPage";
import TaskDrilldown from "./pages/TaskDrilldown";
import AgentProfile from "./pages/AgentProfile";
import QuarterlyReport from "./pages/QuarterlyReport";
import "./index.css";

const router = createBrowserRouter([
  {
    path: "/",
    element: <App />,
    children: [
      { index: true, element: <Navigate to="/observatory" replace /> },
      { path: "observatory", element: <ObservatoryPage /> },
      { path: "observatory/tasks/:taskId", element: <TaskDrilldown /> },
      { path: "observatory/agents/:agentId", element: <AgentProfile /> },
      { path: "observatory/quarterly", element: <QuarterlyReport /> },
    ],
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>
);
```

**Step 4: Verify TypeScript compiles and dev server starts**

```bash
cd services/observatory/frontend && npx tsc --noEmit && npm run dev
```

**Step 5: Commit**

```bash
git add services/observatory/frontend/src/
git commit -m "feat(observatory): add placeholder pages, React Router, and full App layout"
```

---

### Task 13: Fix the double useEventStream / useMetrics issue

**Problem:** App.tsx creates `useMetrics` and `useEventStream` for the VitalsBar, but ObservatoryPage also creates them. This means two SSE connections and two polling loops.

**Solution:** Lift hooks to App.tsx and pass data down via Outlet context.

**Files:**
- Modify: `services/observatory/frontend/src/App.tsx`
- Modify: `services/observatory/frontend/src/pages/ObservatoryPage.tsx`

**Step 1: Update App.tsx to use Outlet context**

```tsx
// App.tsx
import { Outlet } from "react-router-dom";
import TopNav from "./components/TopNav";
import VitalsBar from "./components/VitalsBar";
import { useMetrics } from "./hooks/useMetrics";
import { useEventStream } from "./hooks/useEventStream";
import { useAgents } from "./hooks/useAgents";
import type { MetricsResponse, GDPHistoryResponse, EventItem, AgentListItem } from "./types";

export interface AppContext {
  metrics: MetricsResponse | null;
  gdpHistory: GDPHistoryResponse | null;
  events: EventItem[];
  paused: boolean;
  togglePause: () => void;
  connected: boolean;
  workers: AgentListItem[];
  posters: AgentListItem[];
}

export default function App() {
  const { metrics, gdpHistory } = useMetrics();
  const { events, connected, paused, togglePause } = useEventStream();
  const { workers, posters } = useAgents();

  const ctx: AppContext = {
    metrics,
    gdpHistory,
    events,
    paused,
    togglePause,
    connected,
    workers,
    posters,
  };

  return (
    <div className="font-mono text-text bg-bg h-screen flex flex-col">
      <TopNav />
      <VitalsBar metrics={metrics} connected={connected} />
      <Outlet context={ctx} />
    </div>
  );
}
```

**Step 2: Update ObservatoryPage to consume context**

```tsx
// pages/ObservatoryPage.tsx
import { useOutletContext } from "react-router-dom";
import type { AppContext } from "../App";
import GDPPanel from "../components/GDPPanel";
import LiveFeed from "../components/LiveFeed";
import Leaderboard from "../components/Leaderboard";

export default function ObservatoryPage() {
  const { metrics, gdpHistory, events, paused, togglePause, workers, posters } =
    useOutletContext<AppContext>();

  return (
    <div className="flex-1 flex overflow-hidden">
      <div className="w-[210px] shrink-0 border-r border-border overflow-y-auto">
        <GDPPanel metrics={metrics} gdpHistory={gdpHistory} />
      </div>
      <div className="flex-1 min-w-0 border-r border-border overflow-hidden flex flex-col">
        <LiveFeed events={events} paused={paused} onTogglePause={togglePause} />
      </div>
      <div className="w-[220px] shrink-0 overflow-hidden flex flex-col">
        <Leaderboard workers={workers} posters={posters} metrics={metrics} />
      </div>
    </div>
  );
}
```

**Step 3: Remove hook imports from ObservatoryPage (no longer needed)**

Verify the old direct hook imports are gone.

**Step 4: Verify compiles**

```bash
cd services/observatory/frontend && npx tsc --noEmit
```

**Step 5: Commit**

```bash
git add services/observatory/frontend/src/App.tsx services/observatory/frontend/src/pages/ObservatoryPage.tsx
git commit -m "refactor(observatory): lift hooks to App.tsx, share via Outlet context"
```

---

### Task 14: Visual QA and final polish

**Step 1: Start the backend**

```bash
cd services/observatory && just dev
```

(In a separate terminal.)

**Step 2: Start the frontend**

```bash
cd services/observatory/frontend && npm run dev
```

**Step 3: Visual QA checklist**

Open http://localhost:5173/observatory and verify:

- [ ] TopNav shows "ATE OBSERVATORY" with monospace font, bold, uppercase
- [ ] VitalsBar shows metrics (or loading state if backend isn't populated)
- [ ] LIVE indicator pulses when SSE connected
- [ ] GDPPanel left column: GDP number, sparkline, economy phase, labor market stats, reward distribution
- [ ] LiveFeed center column: events stream in, pause button works, filter buttons work
- [ ] Leaderboard right column: workers tab shows by earned, posters tab shows by spent
- [ ] Clicking a task link in the feed navigates to `/observatory/tasks/:id` placeholder
- [ ] Clicking an agent name in leaderboard navigates to `/observatory/agents/:id` placeholder
- [ ] Back links on placeholder pages return to `/observatory`
- [ ] Full-height layout with no vertical scrollbar on the page itself (only inner columns scroll)

**Step 4: Fix any visual issues found**

Address any spacing, alignment, or layout issues to match the wireframe.

**Step 5: Commit fixes**

```bash
git add -A services/observatory/frontend/
git commit -m "fix(observatory): visual polish from QA pass"
```

---

### Task 15: Verify build succeeds

**Step 1: Run production build**

```bash
cd services/observatory/frontend && npm run build
```

Expected: `dist/` directory created with `index.html` + `assets/`.

**Step 2: Verify TypeScript strict mode**

```bash
cd services/observatory/frontend && npx tsc --noEmit
```

Expected: no errors.

**Step 3: Commit any tsconfig fixes if needed**

```bash
git add services/observatory/frontend/
git commit -m "chore(observatory): verify production build passes"
```

**Step 4: Push the branch**

```bash
git push -u origin observatory-frontend
```
