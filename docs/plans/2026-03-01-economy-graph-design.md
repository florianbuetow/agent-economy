# Economy Graph — Design Document

_2026-03-01. Approved design for the agent economy graph visualization._

---

## Decision: Hero Background

The graph renders as a full-viewport Canvas 2D animation behind the landing page hero section. Dark theme (`#0A0A0F`), colored glowing nodes, autonomous simulation. No click required — visitors see the living economy immediately on page load.

**Why not standalone page:** Hackathon demo = 3-second attention test. The wow must be instant, no navigation click.

**Why not PixiJS/WebGL:** 200 nodes is trivial for Canvas 2D. The wow comes from agent behavior, not rendering tech. Zero new dependencies.

---

## Architecture

One self-contained Canvas engine class + one React wrapper component. The engine owns all simulation state and rendering. React only mounts/unmounts it.

```
frontend/src/components/graph/
├── EconomyGraph.tsx      # React wrapper — mounts canvas, creates engine
├── engine.ts             # Core simulation loop + renderer
├── agents.ts             # Agent state machine
├── tasks.ts              # Task state machine
├── camera.ts             # World→screen transform
├── effects.ts            # Particles, ripples, absorption animation
└── types.ts              # AgentNode, TaskNode, Edge, State enums
```

### Files to modify

- `pages/LandingPage.tsx` — Dark hero container with canvas behind text
- `components/landing/HeroSection.tsx` — White text, transparent bg, backdrop blur
- `index.css` — Dark theme tokens for the hero section

---

## Integration Layout

```
┌──────────────────────────────────────────────────┐
│  ATE │ Agent Task Economy           ● LIVE       │  Header (z-10, transparent)
├──────────────────────────────────────────────────┤
│ ┌──────────────────────────────────────────────┐ │
│ │  Canvas (position absolute, 100vw × 100vh)   │ │
│ │  Dark background #0A0A0F                     │ │
│ │  Agents drifting, orbiting, fleeing...        │ │
│ │                                               │ │
│ │      Hero text (z-10, centered)               │ │
│ │      backdrop-blur + semi-transparent pill     │ │
│ │      White text on dark                        │ │
│ │      [Post a Task]  [Register Agent]          │ │
│ │                                               │ │
│ └──────────────────────────────────────────────┘ │
│  State ticker: 12 searching  8 →task  3 bidding  │
├──────────────────────────────────────────────────┤
│  Light theme sections continue below...          │
└──────────────────────────────────────────────────┘
```

- Canvas: `position: absolute`, fills hero container, `z-0`
- Hero text: `position: relative`, `z-10`, centered, backdrop-blur pill
- Header: Inverted to white-on-dark
- State ticker: Bottom of hero, live agent state counts
- Canvas stays fixed within hero. Light sections scroll over it.

---

## Visual Design (Production Dark Theme)

### Colors

- Background: `#0A0A0F`
- Node borders: White 1px outline
- Edges: White at varying opacity (bid: 0.6, awarded: 0.9)
- Labels: White, Courier New

### Category Colors

| Category | Hex | Glow |
|---|---|---|
| Data | `#3B82F6` | Blue halo |
| Writing | `#8B5CF6` | Purple halo |
| Code | `#10B981` | Green halo |
| Research | `#F59E0B` | Amber halo |
| Design | `#EC4899` | Pink halo |
| General | `#6B7280` | Subtle gray |

### Node Sizing

- Agents: `r = 10 + sqrt(wealth / 1000) * 20`
- Tasks: `r = 18 + sqrt(payoff / 500) * 28`

### Glow Rendering

Canvas 2D `shadowBlur` for per-node glow. If performance is tight at 200 nodes, render glows to an offscreen canvas every few frames and composite in.

---

## Simulation

### Parameters

- 200 nodes total: ~120 agents + ~80 tasks
- World space: 3000 × 3000px
- Zoom: Auto-fit to viewport (no user zoom/pan on hero)
- Fully autonomous — no WebSocket. Randomized timers drive everything.
- Delta time: `dt = min((now - lastFrame) / 1000, 0.05)`

### Agent State Machine

```
searching → approaching → inspecting → orbiting → in_progress → searching
                              ↓                        ↓
                          rejecting                  fleeing → searching
```

States follow the spec exactly:
- **searching**: Perlin-ish drift, `vx *= 0.97` damping, agent repulsion
- **approaching**: Steer toward task inspect radius, `speed = min(2.16, dist * 0.06 + 0.4)`
- **inspecting**: Hover at task edge 0.6–1.4s, 70% commit / 30% reject
- **orbiting**: Circular orbit, `orbitAngle += speed * dt`, bid line to task center
- **in_progress**: Close orbit (winner) or wide orbit (poster)
- **fleeing**: Velocity away from task, `vx *= 0.91` decay, opacity fades
- **rejecting**: Peel away, `vx *= 0.88` decay, opacity 0.5

### Task State Machine

```
open → bidding → awarded → in_progress → complete → respawn
```

### Key Visual Moments

1. **Task absorption**: Task interpolates toward winner over 1.0s, shrinks, fades
2. **Worker growth**: Radius overshoots by 5px then settles (bounce easing)
3. **Fleeing losers**: Scatter away, opacity fading to 0.25
4. **Orbiting bidders**: Concentric circular orbits with bid lines
5. **Coin particles**: 6 gold squares burst from winner on completion
6. **Ripple rings**: 2–3 expanding circles on award and completion
7. **Pulse on open tasks**: Subtle ring at `task.r + 4..14`, pulsing opacity

---

## No User Interaction on Hero

The hero graph is a living screensaver. No zoom, pan, click, or hover. The existing Observatory at `/observatory` is where interactive exploration happens.

---

## Not in Scope

- WebSocket integration (autonomous only)
- New npm dependencies
- Changes to Observatory dashboard or backend
- Mobile layout
- Node filtering or search
