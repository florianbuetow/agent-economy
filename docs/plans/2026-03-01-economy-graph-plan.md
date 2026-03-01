# Economy Graph Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Render a living Canvas 2D economy simulation behind the landing page hero — 200 autonomous agent/task nodes with full state machine behavior on a dark theme.

**Architecture:** Single Canvas engine class (no framework) wrapped in a React component. The engine owns all simulation state, physics, and rendering. React only mounts/unmounts it via a ref. All files live in `services/observatory/frontend/src/components/graph/`.

**Tech Stack:** TypeScript, Canvas 2D API, requestAnimationFrame, React 19 (wrapper only). Zero new dependencies.

**Working directory:** `.claude/worktrees/economy-graph/services/observatory/frontend/`

**Reference docs:**
- Design: `docs/plans/2026-03-01-economy-graph-design.md`
- Full spec: `../../../../../../graph_spec.md` (at repo group root)
- Wireframe (reference only): `../../../../../../artefacts/graph_wireframe.jsx`

---

## Task 1: Types and Constants

**Files:**
- Create: `src/components/graph/types.ts`

**Step 1: Create the types file**

```typescript
// ─── Agent states ────────────────────────────────────────────────────────────
export type AgentState =
  | "searching"
  | "approaching"
  | "inspecting"
  | "rejecting"
  | "orbiting"
  | "fleeing"
  | "in_progress";

// ─── Task states ─────────────────────────────────────────────────────────────
export type TaskState = "open" | "bidding" | "awarded" | "in_progress" | "complete";

// ─── Categories ──────────────────────────────────────────────────────────────
export type Category = "data" | "writing" | "code" | "research" | "design" | "general";

export const CATEGORIES: Category[] = ["data", "writing", "code", "research", "design", "general"];

export const CATEGORY_COLORS: Record<Category, string> = {
  data: "#3B82F6",
  writing: "#8B5CF6",
  code: "#10B981",
  research: "#F59E0B",
  design: "#EC4899",
  general: "#6B7280",
};

// ─── State badge labels ──────────────────────────────────────────────────────
export const STATE_LABELS: Record<AgentState, string> = {
  searching: "searching\u2026",
  approaching: "\u2192 task",
  inspecting: "inspect\u2026",
  rejecting: "\u2715 not this",
  orbiting: "bidding",
  fleeing: "lost \u2192 next",
  in_progress: "working",
};

// ─── World constants ─────────────────────────────────────────────────────────
export const WORLD_SIZE = 3000;
export const BG_COLOR = "#0A0A0F";

// ─── Agent names ─────────────────────────────────────────────────────────────
export const AGENT_PREFIXES = [
  "Helix", "Nexus", "Axiom", "Vector", "Sigma",
  "Delta", "Quark", "Prism", "Cipher", "Nova",
  "Orbit", "Pulse", "Cortex", "Flux", "Aether",
  "Zenith", "Vortex", "Synth", "Rune", "Atlas",
];

export const TASK_NAMES = [
  "Summarize report", "Classify data", "Write docs",
  "Generate tests", "Parse JSON", "Draft email",
  "Analyze logs", "Score leads", "Build query",
  "Translate text", "Extract entities", "Review code",
  "Format output", "Clean dataset", "Tag images",
  "Rank results", "Map schema", "Audit config",
  "Merge records", "Validate input",
];

// ─── Entity interfaces ──────────────────────────────────────────────────────
export interface AgentNode {
  id: number;
  name: string;
  category: Category;
  wealth: number;
  r: number;
  x: number;
  y: number;
  vx: number;
  vy: number;
  state: AgentState;
  targetTaskId: number | null;
  stateTimer: number;
  orbitAngle: number;
  orbitSpeed: number;
  opacity: number;
  trail: Array<{ x: number; y: number }>;
  rejectedTasks: number[];
  role: "worker" | "poster" | null;
  bidderIndex: number;
}

export interface TaskNode {
  id: number;
  name: string;
  category: Category;
  payoff: number;
  r: number;
  x: number;
  y: number;
  state: TaskState;
  stateTimer: number;
  bidders: number[];
  winnerId: number | null;
  posterId: number | null;
  pulseAge: number;
  // absorption animation
  absorbTarget: { x: number; y: number } | null;
  absorbProgress: number;
  absorbStartR: number;
}

export interface Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  life: number;
  maxLife: number;
  size: number;
  color: string;
}

export interface Ripple {
  x: number;
  y: number;
  r: number;
  maxR: number;
  life: number;
  maxLife: number;
  color: string;
}
```

**Step 2: Verify TypeScript compiles**

Run: `cd services/observatory/frontend && npx tsc --noEmit src/components/graph/types.ts`
Expected: No errors

**Step 3: Commit**

```bash
git add src/components/graph/types.ts
git commit -m "feat(graph): add type definitions and constants"
```

---

## Task 2: Camera Module

**Files:**
- Create: `src/components/graph/camera.ts`

**Step 1: Create the camera module**

```typescript
import { WORLD_SIZE } from "./types";

export interface Camera {
  x: number; // world center x
  y: number; // world center y
  zoom: number;
}

export function createCamera(): Camera {
  return {
    x: WORLD_SIZE / 2,
    y: WORLD_SIZE / 2,
    zoom: 1, // will be computed to fit viewport
  };
}

/** Compute zoom level to fit entire world in the given viewport */
export function fitToViewport(camera: Camera, viewportW: number, viewportH: number): void {
  const scaleX = viewportW / WORLD_SIZE;
  const scaleY = viewportH / WORLD_SIZE;
  camera.zoom = Math.min(scaleX, scaleY) * 0.92; // 8% padding
  camera.x = WORLD_SIZE / 2;
  camera.y = WORLD_SIZE / 2;
}

/** Convert world coordinates to screen coordinates */
export function worldToScreen(
  wx: number,
  wy: number,
  camera: Camera,
  canvasW: number,
  canvasH: number,
): { sx: number; sy: number } {
  return {
    sx: (wx - camera.x) * camera.zoom + canvasW / 2,
    sy: (wy - camera.y) * camera.zoom + canvasH / 2,
  };
}

/** Check if a circle at world position is visible on screen */
export function isVisible(
  wx: number,
  wy: number,
  r: number,
  camera: Camera,
  canvasW: number,
  canvasH: number,
): boolean {
  const { sx, sy } = worldToScreen(wx, wy, camera, canvasW, canvasH);
  const screenR = r * camera.zoom;
  const margin = 60;
  return (
    sx + screenR + margin > 0 &&
    sx - screenR - margin < canvasW &&
    sy + screenR + margin > 0 &&
    sy - screenR - margin < canvasH
  );
}
```

**Step 2: Verify TypeScript compiles**

Run: `cd services/observatory/frontend && npx tsc --noEmit src/components/graph/camera.ts`
Expected: No errors

**Step 3: Commit**

```bash
git add src/components/graph/camera.ts
git commit -m "feat(graph): add camera module with world-to-screen transform"
```

---

## Task 3: Agent State Machine

**Files:**
- Create: `src/components/graph/agents.ts`

**Step 1: Create the agent state machine module**

This file contains:
- `createAgent()` — spawn an agent at a random world position
- `updateAgent()` — tick the agent state machine one frame
- Agent radius formula: `r = 10 + sqrt(wealth / 1000) * 20`
- All state transitions from the spec: searching, approaching, inspecting, rejecting, orbiting, fleeing, in_progress

Key behaviors per state:
- **searching**: Random velocity impulses + `vx *= 0.97` damping. Soft boundary repulsion within 30px of world edge. Repulsion between searching agents (min separation `rA + rB + 70`). After `searchIdleTimer` (2-5s random), find nearest open/bidding task not in rejected list → `approaching`.
- **approaching**: Steer toward task inspect radius (`task.r + agent.r + 30`). Speed = `min(2.16, dist * 0.06 + 0.4)`. Direction arrow visible. Trail of last 8 positions. If task becomes full (4+ bidders) or leaves open/bidding → back to `searching`.
- **inspecting**: Damp velocity `vx *= 0.85`. Timer 0.6-1.4s. On expiry: 70% → `orbiting` (register as bidder), 30% → `rejecting` (add to rejected list).
- **rejecting**: Velocity away from task + random. `vx *= 0.88` decay. Duration 1.2-2.0s. Opacity 0.5. Then → `searching` with short idle timer.
- **orbiting**: `orbitAngle += orbitSpeed * dt`. `orbitR = task.r + agent.r + 40 + (bidderIndex * 22)`. Bid line drawn from agent to task center.
- **fleeing**: Velocity away from task. `vx *= 0.91` decay. Duration 1.5-2.3s. Opacity fades to 0.25. Then → `searching`.
- **in_progress**: Worker orbits at `task.r + agent.r + 36` clockwise. Poster orbits at `task.r + agent.r + 60` counter-clockwise.

The function signature for `updateAgent` takes the agent, all tasks, all agents (for repulsion), and dt. It mutates the agent in place.

**Step 2: Verify TypeScript compiles**

Run: `cd services/observatory/frontend && npx tsc --noEmit src/components/graph/agents.ts`
Expected: No errors

**Step 3: Commit**

```bash
git add src/components/graph/agents.ts
git commit -m "feat(graph): add agent state machine with all state transitions"
```

---

## Task 4: Task State Machine

**Files:**
- Create: `src/components/graph/tasks.ts`

**Step 1: Create the task state machine module**

This file contains:
- `createTask()` — spawn a task at a random world position with random payoff/category
- `updateTask()` — tick the task state machine one frame
- Task radius formula: `r = 18 + sqrt(payoff / 500) * 28`

State transitions:
- **open**: Pulse animation (`sin(pulseAge * 2.5)`). Timer 2-7s. If an agent registers as bidder → `bidding`. If timer expires with no bidders → respawn.
- **bidding**: Timer 3-6s. When timer expires → `awarded`.
- **awarded**: Pick random winner from `task.bidders`. All non-winners → trigger their `fleeing` state. Winner gets `payoff * 0.1` advance. Assign a random searching agent as poster. Timer 2-4s → `in_progress`. Return list of effects to spawn (ripple rings).
- **in_progress**: Work duration 5-10s. When timer expires → `complete`.
- **complete**: Winner gets `task.payoff`. Start absorption animation (task interpolates toward winner over 1.0s, shrinks). Return effects to spawn (ripples, coin particles). After absorption → respawn at new random position with new payoff/category.

The function returns an array of effects (particles, ripples) that the engine should spawn.

**Step 2: Verify TypeScript compiles**

Run: `cd services/observatory/frontend && npx tsc --noEmit src/components/graph/tasks.ts`
Expected: No errors

**Step 3: Commit**

```bash
git add src/components/graph/tasks.ts
git commit -m "feat(graph): add task state machine with lifecycle and respawning"
```

---

## Task 5: Effects Module

**Files:**
- Create: `src/components/graph/effects.ts`

**Step 1: Create the effects module**

This file manages transient visual effects:
- `updateParticle(p, dt)` — move particle, decay life. Returns false when dead.
- `updateRipple(r, dt)` — expand radius, decay life. Returns false when dead.
- `spawnCoinParticles(x, y)` — returns 6 gold (`#F59E0B`) square particles bursting outward from position
- `spawnRipples(x, y, color)` — returns 2-3 expanding rings at position with the given color

Particle physics:
- Position: `p.x += p.vx * dt`, `p.y += p.vy * dt`
- Gravity: `p.vy += 80 * dt` (coins arc downward)
- Life: `p.life -= dt`

Ripple physics:
- Radius: `r.r += (r.maxR / r.maxLife) * dt`
- Life: `r.life -= dt`

**Step 2: Verify TypeScript compiles**

Run: `cd services/observatory/frontend && npx tsc --noEmit src/components/graph/effects.ts`
Expected: No errors

**Step 3: Commit**

```bash
git add src/components/graph/effects.ts
git commit -m "feat(graph): add particle and ripple effect system"
```

---

## Task 6: Engine — Core Simulation Loop and Renderer

**Files:**
- Create: `src/components/graph/engine.ts`

**Step 1: Create the engine module**

This is the main file. It exports a class `GraphEngine` with:

```typescript
class GraphEngine {
  constructor(canvas: HTMLCanvasElement)
  start(): void      // begins rAF loop
  stop(): void       // cancels rAF loop
  resize(): void     // re-fits camera to canvas size
  getStateCounts(): Record<AgentState, number>  // for state ticker
}
```

**Initialization (`constructor`):**
- Store canvas ref, get 2D context
- Create camera, fit to viewport
- Spawn 120 agents via `createAgent()` with random positions, wealth, categories
- Spawn 80 tasks via `createTask()` with random positions, payoffs, categories
- Initialize empty particle and ripple arrays

**Main loop (`tick` called via rAF):**
1. Compute `dt = min((now - lastFrame) / 1000, 0.05)`
2. Update all agents via `updateAgent(agent, tasks, agents, dt)`
3. Update all tasks via `updateTask(task, agents, dt)` — collect returned effects
4. Update all particles and ripples — remove dead ones
5. Render everything to canvas

**Rendering order:**
1. Clear canvas with `BG_COLOR` (`#0A0A0F`)
2. Draw approach paths (dashed lines from approaching/inspecting agents to their target task)
3. Draw bid edges (solid lines from orbiting agents to task center)
4. Draw awarded edges (thicker solid line from winner to task)
5. Draw task nodes (circle with radial gradient + glow via `shadowBlur`)
6. Draw agent nodes (circle with radial gradient + glow, opacity per state)
7. Draw labels (name + wealth/payoff) when `zoom > 0.15`
8. Draw state badges when `zoom > 0.22`
9. Draw direction arrows for approaching/fleeing agents
10. Draw motion trails for moving agents
11. Draw ripple rings
12. Draw coin particles

**Node rendering detail:**
- For each node, set `ctx.shadowColor` to the category color, `ctx.shadowBlur = 12`
- Draw filled circle with radial gradient (category color center → darker edge)
- Draw 1px white stroke
- Reset shadowBlur to 0 before drawing labels

**Performance notes:**
- Cull nodes outside viewport via `isVisible()` before drawing
- Only compute agent repulsion for `searching` agents
- `shadowBlur` is the expensive part — if needed, batch glow rendering to offscreen canvas

**Step 2: Verify TypeScript compiles**

Run: `cd services/observatory/frontend && npx tsc --noEmit src/components/graph/engine.ts`
Expected: No errors

**Step 3: Commit**

```bash
git add src/components/graph/engine.ts
git commit -m "feat(graph): add main engine with simulation loop and canvas renderer"
```

---

## Task 7: React Wrapper Component

**Files:**
- Create: `src/components/graph/EconomyGraph.tsx`

**Step 1: Create the React wrapper**

```typescript
import { useEffect, useRef, useState } from "react";
import { GraphEngine } from "./engine";
import type { AgentState } from "./types";
import { STATE_LABELS } from "./types";

export default function EconomyGraph() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const engineRef = useRef<GraphEngine | null>(null);
  const [stateCounts, setStateCounts] = useState<Record<AgentState, number> | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const engine = new GraphEngine(canvas);
    engineRef.current = engine;
    engine.start();

    // Update state counts every 500ms for the ticker
    const interval = setInterval(() => {
      setStateCounts(engine.getStateCounts());
    }, 500);

    const handleResize = () => engine.resize();
    window.addEventListener("resize", handleResize);

    return () => {
      engine.stop();
      clearInterval(interval);
      window.removeEventListener("resize", handleResize);
      engineRef.current = null;
    };
  }, []);

  return (
    <>
      <canvas
        ref={canvasRef}
        className="absolute inset-0 w-full h-full"
        style={{ background: "#0A0A0F" }}
      />
      {stateCounts && (
        <div className="absolute bottom-0 left-0 right-0 z-10 flex justify-center gap-4 py-2 font-mono text-[9px] tracking-wide"
          style={{ background: "linear-gradient(transparent, rgba(10,10,15,0.8))" }}>
          {(Object.keys(STATE_LABELS) as AgentState[]).map((state) => (
            <span key={state} className="text-white/50">
              <span className="text-white/80 font-bold">{stateCounts[state] ?? 0}</span>
              {" "}{STATE_LABELS[state]}
            </span>
          ))}
        </div>
      )}
    </>
  );
}
```

**Step 2: Verify TypeScript compiles**

Run: `cd services/observatory/frontend && npx tsc --noEmit src/components/graph/EconomyGraph.tsx`
Expected: No errors

**Step 3: Commit**

```bash
git add src/components/graph/EconomyGraph.tsx
git commit -m "feat(graph): add React wrapper component with state ticker"
```

---

## Task 8: Integrate into Landing Page

**Files:**
- Modify: `src/pages/LandingPage.tsx`
- Modify: `src/components/landing/HeroSection.tsx`

**Step 1: Modify LandingPage.tsx**

Changes:
- Import `EconomyGraph`
- Wrap `Header` + `HeroSection` in a `relative h-screen` container with dark background
- `EconomyGraph` renders as `absolute inset-0` inside that container
- Header and HeroSection float on top with `relative z-10`
- Header text inverted to white-on-dark
- Rest of landing page continues below with existing light theme

The hero container is `h-screen overflow-hidden relative` with `bg-[#0A0A0F]`. The existing sections below use `bg-bg` (white) and scroll normally.

**Step 2: Modify HeroSection.tsx**

Changes:
- All text colors inverted: `text-text` → `text-white`, `text-text-mid` → `text-white/60`
- Add a subtle backdrop pill behind the text content: `backdrop-blur-sm bg-white/5 rounded-2xl px-8 py-10 max-w-[560px] mx-auto`
- Button styles updated for dark theme:
  - Primary: `bg-white text-[#0A0A0F] border-white`
  - Secondary: `bg-transparent text-white border-white/40`
- Tagline: `text-white/30`

**Step 3: Verify the app builds**

Run: `cd services/observatory/frontend && npx vite build`
Expected: Build succeeds with no errors

**Step 4: Run the dev server and visually verify**

Run: `cd services/observatory/frontend && npx vite --host`
Expected: Landing page shows dark hero with animated graph behind white text. Scrolling below reveals existing light-theme sections.

**Step 5: Commit**

```bash
git add src/pages/LandingPage.tsx src/components/landing/HeroSection.tsx
git commit -m "feat(graph): integrate economy graph as landing page hero background"
```

---

## Task 9: Polish and Performance Check

**Files:**
- Possibly modify: `src/components/graph/engine.ts` (performance tuning)
- Possibly modify: `src/index.css` (if any new keyframes needed)

**Step 1: Visual check at 200 nodes**

Run the dev server. Open Chrome DevTools → Performance tab → record 5 seconds. Verify:
- Frame rate stays above 55fps
- No layout thrashing or excessive GC

**Step 2: If shadowBlur is too expensive**

Implement offscreen glow canvas:
- Create a second offscreen canvas
- Render all node glows to it every 3 frames
- Composite it onto the main canvas with `globalCompositeOperation: 'lighter'`

**Step 3: Verify the full build**

Run: `cd services/observatory/frontend && npx vite build`
Expected: Build succeeds, no TypeScript errors, no warnings

**Step 4: Commit any performance fixes**

```bash
git add -A
git commit -m "perf(graph): optimize rendering for 200 nodes"
```

---

## Task 10: Final Integration Test

**Step 1: Clean build**

Run: `cd services/observatory/frontend && rm -rf dist && npx vite build`
Expected: Clean build succeeds

**Step 2: Verify all routes still work**

Start dev server and check:
- `/` — Landing page with dark hero + graph animation + text overlay + state ticker
- `/observatory` — Existing dashboard still works (unchanged)
- Scroll down on landing page — light theme sections appear correctly below hero

**Step 3: Commit and verify clean git state**

```bash
cd /path/to/worktree
git status
git log --oneline -10
```

Expected: All changes committed, no uncommitted files.
