# Observatory Frontend Dashboard — Design

## Scope

Phase 1: Macro Observatory view only. Translates the wireframe (`artefacts/observability_dashboard_wireframe.jsx`) from hardcoded mock data into a live dashboard consuming the backend API (port 8006).

**In scope:** TopNav, VitalsBar, GDPPanel, LiveFeed (SSE), Leaderboard (workers/posters).
**Out of scope:** Landing page, Task Drilldown, Agent Profile, Quarterly Report (placeholder routes only).

## Tech Stack

- React 18 + TypeScript (strict) + Vite
- Tailwind CSS (design tokens mapped to theme config)
- React Router v6
- Native `fetch` + `EventSource` (no axios, no state library)

## Project Structure

```
services/observatory/frontend/
├── index.html
├── package.json
├── vite.config.ts
├── tsconfig.json
├── tailwind.config.ts
├── src/
│   ├── main.tsx              # Entry + router
│   ├── App.tsx               # Root layout (TopNav + VitalsBar + Outlet)
│   ├── index.css             # Tailwind directives + pulse animation
│   ├── types.ts              # API response types
│   ├── api/
│   │   ├── client.ts         # Base fetch wrapper
│   │   ├── metrics.ts        # /api/metrics, /api/metrics/gdp/history
│   │   ├── events.ts         # /api/events
│   │   └── agents.ts         # /api/agents
│   ├── hooks/
│   │   ├── useMetrics.ts     # Polls /api/metrics every 5s + GDP history
│   │   ├── useEventStream.ts # SSE via EventSource, pause/resume, 500 cap
│   │   └── useAgents.ts      # Agent leaderboard data
│   ├── pages/
│   │   ├── ObservatoryPage.tsx   # 3-column macro dashboard
│   │   ├── TaskDrilldown.tsx     # Placeholder
│   │   ├── AgentProfile.tsx      # Placeholder
│   │   └── QuarterlyReport.tsx   # Placeholder
│   └── components/
│       ├── TopNav.tsx
│       ├── VitalsBar.tsx
│       ├── GDPPanel.tsx
│       ├── LiveFeed.tsx
│       ├── Leaderboard.tsx
│       ├── Sparkline.tsx
│       ├── HatchBar.tsx
│       └── Badge.tsx
```

## Data Flow

1. `useMetrics` polls `GET /api/metrics` every 5s. Feeds VitalsBar + GDPPanel.
2. `useMetrics` also fetches `GET /api/metrics/gdp/history?window=7d&resolution=1h` for sparklines.
3. `useEventStream` opens `EventSource(/api/events/stream)`. Events prepend to array (500 cap). Pause skips prepending.
4. `useAgents` fetches `/api/agents?sort_by=total_earned&order=desc&limit=10` (workers) and `sort_by=total_spent` (posters).

## Tailwind Theme

Wireframe design tokens mapped to Tailwind custom theme values:
- Colors: `bg` white, `bgOff` gray-50, `bgDark` gray-100, borders gray-300/gray-800, text gray-900/600/400/300
- Fonts: Courier New monospace (default), Georgia serif (sparse use)
- All typography monospace by default

## Routing

| Route | Component | Status |
|---|---|---|
| `/observatory` | ObservatoryPage | Built |
| `/observatory/tasks/:id` | TaskDrilldown | Placeholder |
| `/observatory/agents/:id` | AgentProfile | Placeholder |
| `/observatory/quarterly` | QuarterlyReport | Placeholder |
| `/` | Redirect to `/observatory` | |

## Event Type Mapping

| `event_type` | Badge |
|---|---|
| `task.created` | TASK |
| `bid.submitted` | BID |
| `task.approved`, `bank.payout` | PAYOUT |
| `task.accepted` | CONTRACT |
| `bank.escrow_locked` | ESCROW |
| `reputation.feedback_revealed` | REP |
| `identity.agent_registered` | AGENT |

## Vite Config

- Proxy `/api` and `/health` to `http://localhost:8006`
- Standard React + TypeScript + Tailwind v4 setup via `@tailwindcss/vite`
