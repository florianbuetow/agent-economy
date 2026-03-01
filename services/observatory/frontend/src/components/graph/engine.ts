// ─── Engine: Core Simulation Loop & Canvas Renderer ─────────────────────────
// Orchestrates the entire economy graph visualization. Owns all simulation
// state (agents, tasks, particles, ripples), runs the update loop at 60fps,
// and renders everything to a Canvas 2D context in back-to-front order.
// Uses the wireframe theme: monochrome palette, hatch-pattern fills, no glow.

import type { AgentNode, TaskNode, AgentState, Category, Particle, Ripple } from "./types";
import { BG_COLOR, HATCH_DEFS, W, STATE_LABELS, CATEGORIES } from "./types";
import { createCamera, fitToViewport, worldToScreen, isVisible } from "./camera";
import type { Camera } from "./camera";
import { createAgent, updateAgent } from "./agents";
import { createTask, updateTask } from "./tasks";
import { updateParticle, updateRipple } from "./effects";

// ─── Hatch pattern factory ──────────────────────────────────────────────────

/** Create a repeating Canvas pattern with hatched lines at the given angle/gap. */
function createHatchPattern(
  ctx: CanvasRenderingContext2D,
  angle: number,
  gap: number,
): CanvasPattern {
  const size = Math.max(gap, 2);
  const tile = document.createElement("canvas");
  tile.width = size;
  tile.height = size;
  const tc = tile.getContext("2d");
  if (!tc) throw new Error("Failed to get 2D context for hatch tile");

  // Background fill
  tc.fillStyle = W.bgNode;
  tc.fillRect(0, 0, size, size);

  // Hatch lines
  tc.strokeStyle = W.hatchStroke;
  tc.lineWidth = 1;

  if (angle === 0) {
    // Horizontal lines
    tc.beginPath();
    tc.moveTo(0, size / 2);
    tc.lineTo(size, size / 2);
    tc.stroke();
  } else if (angle === 90) {
    // Vertical lines
    tc.beginPath();
    tc.moveTo(size / 2, 0);
    tc.lineTo(size / 2, size);
    tc.stroke();
  } else if (angle === 45) {
    // Diagonal: bottom-left to top-right
    tc.beginPath();
    tc.moveTo(0, size);
    tc.lineTo(size, 0);
    tc.moveTo(-size, size);
    tc.lineTo(size, -size);
    tc.moveTo(0, 2 * size);
    tc.lineTo(2 * size, 0);
    tc.stroke();
  } else if (angle === -45) {
    // Diagonal: top-left to bottom-right
    tc.beginPath();
    tc.moveTo(0, 0);
    tc.lineTo(size, size);
    tc.moveTo(-size, 0);
    tc.lineTo(size, 2 * size);
    tc.moveTo(0, -size);
    tc.lineTo(2 * size, size);
    tc.stroke();
  }

  const pattern = ctx.createPattern(tile, "repeat");
  if (!pattern) throw new Error("Failed to create hatch CanvasPattern");
  return pattern;
}

// ─── Engine Class ───────────────────────────────────────────────────────────

export class GraphEngine {
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private camera: Camera;

  private agents: AgentNode[];
  private tasks: TaskNode[];
  private particles: Particle[];
  private ripples: Ripple[];

  private running: boolean;
  private rafId: number;
  private lastFrame: number;

  /** Pre-built hatch patterns per category (created once, reused every frame) */
  private hatchPatterns: Record<Category, CanvasPattern>;

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas;

    const ctx = canvas.getContext("2d");
    if (!ctx) {
      throw new Error("Failed to get 2D rendering context from canvas");
    }
    this.ctx = ctx;

    // Set canvas dimensions for sharp rendering on high-DPI displays
    const dpr = window.devicePixelRatio;
    canvas.width = canvas.clientWidth * dpr;
    canvas.height = canvas.clientHeight * dpr;
    ctx.scale(dpr, dpr);

    // Create camera and fit to viewport
    this.camera = createCamera();
    fitToViewport(this.camera, canvas.clientWidth, canvas.clientHeight);

    // Pre-create hatch patterns (one offscreen canvas per category)
    this.hatchPatterns = {} as Record<Category, CanvasPattern>;
    for (const cat of CATEGORIES) {
      const def = HATCH_DEFS[cat];
      this.hatchPatterns[cat] = createHatchPattern(ctx, def.angle, def.gap);
    }

    // Spawn entities
    this.agents = [];
    for (let i = 0; i < 120; i++) {
      this.agents.push(createAgent(i));
    }

    this.tasks = [];
    for (let i = 0; i < 80; i++) {
      this.tasks.push(createTask(i));
    }

    // Initialize empty effect arrays
    this.particles = [];
    this.ripples = [];

    // Timing
    this.running = false;
    this.rafId = 0;
    this.lastFrame = performance.now();
  }

  // ─── Public API ─────────────────────────────────────────────────────────

  /** Begin the requestAnimationFrame loop */
  start(): void {
    this.running = true;
    this.rafId = requestAnimationFrame(() => this.tick());
  }

  /** Cancel the animation loop */
  stop(): void {
    this.running = false;
    cancelAnimationFrame(this.rafId);
  }

  /** Re-fit camera to canvas size, update canvas dimensions */
  resize(): void {
    const dpr = window.devicePixelRatio;
    this.canvas.width = this.canvas.clientWidth * dpr;
    this.canvas.height = this.canvas.clientHeight * dpr;
    this.ctx.scale(dpr, dpr);
    fitToViewport(this.camera, this.canvas.clientWidth, this.canvas.clientHeight);
  }

  /** Count agents per state for the state ticker UI */
  getStateCounts(): Record<AgentState, number> {
    const counts: Record<AgentState, number> = {
      searching: 0,
      approaching: 0,
      inspecting: 0,
      rejecting: 0,
      orbiting: 0,
      fleeing: 0,
      in_progress: 0,
    };

    for (const agent of this.agents) {
      counts[agent.state]++;
    }

    return counts;
  }

  // ─── Main Loop ──────────────────────────────────────────────────────────

  private tick(): void {
    // 1. Compute delta time
    const now = performance.now();
    const dt = Math.min((now - this.lastFrame) / 1000, 0.05);
    this.lastFrame = now;

    // 2. Update simulation
    for (const agent of this.agents) {
      updateAgent(agent, this.tasks, this.agents, dt);
    }

    for (const task of this.tasks) {
      const fx = updateTask(task, this.agents, dt);
      if (fx.particles.length > 0) {
        this.particles.push(...fx.particles);
      }
      if (fx.ripples.length > 0) {
        this.ripples.push(...fx.ripples);
      }
    }

    // Update particles, remove dead ones
    this.particles = this.particles.filter((p) => updateParticle(p, dt));

    // Update ripples, remove dead ones
    this.ripples = this.ripples.filter((r) => updateRipple(r, dt));

    // 3. Render to canvas
    this.render();

    // 4. Schedule next frame
    if (this.running) {
      this.rafId = requestAnimationFrame(() => this.tick());
    }
  }

  // ─── Renderer ───────────────────────────────────────────────────────────

  private render(): void {
    const ctx = this.ctx;
    const camera = this.camera;
    const w = this.canvas.clientWidth;
    const h = this.canvas.clientHeight;

    // Clear canvas
    ctx.fillStyle = BG_COLOR;
    ctx.fillRect(0, 0, w, h);

    // (a) Approach paths — dashed lines for agents approaching or inspecting
    this.renderApproachPaths(ctx, camera, w, h);

    // (b) Bid edges — solid lines for orbiting agents
    this.renderBidEdges(ctx, camera, w, h);

    // (c) Awarded/work edges — for in_progress agents
    this.renderWorkEdges(ctx, camera, w, h);

    // (d) Task nodes
    this.renderTaskNodes(ctx, camera, w, h);

    // (e) Agent nodes
    this.renderAgentNodes(ctx, camera, w, h);

    // (f) Labels — only when zoomed in enough
    if (camera.zoom > 0.15) {
      this.renderLabels(ctx, camera, w, h);
    }

    // (g) State badges — only when zoomed in further
    if (camera.zoom > 0.22) {
      this.renderStateBadges(ctx, camera, w, h);
    }

    // (h) Direction arrows — for approaching/fleeing agents
    this.renderDirectionArrows(ctx, camera, w, h);

    // (i) Motion trails — for agents with trail data
    this.renderMotionTrails(ctx, camera, w, h);

    // (j) Ripple rings
    this.renderRipples(ctx, camera, w, h);

    // (k) Coin particles
    this.renderParticles(ctx, camera, w, h);
  }

  // ─── Render passes (back to front) ─────────────────────────────────────

  /** (a) Dashed approach paths for approaching/inspecting agents */
  private renderApproachPaths(
    ctx: CanvasRenderingContext2D,
    camera: Camera,
    w: number,
    h: number,
  ): void {
    ctx.strokeStyle = "rgba(51,51,51,0.12)";
    ctx.setLineDash([4 * camera.zoom, 6 * camera.zoom]);
    ctx.lineWidth = 1 * camera.zoom;

    for (const agent of this.agents) {
      if (
        (agent.state === "approaching" || agent.state === "inspecting") &&
        agent.targetTaskId !== null
      ) {
        const task = this.tasks.find((t) => t.id === agent.targetTaskId);
        if (!task) continue;

        const agentScreen = worldToScreen(agent.x, agent.y, camera, w, h);
        const taskScreen = worldToScreen(task.x, task.y, camera, w, h);

        ctx.beginPath();
        ctx.moveTo(agentScreen.sx, agentScreen.sy);
        ctx.lineTo(taskScreen.sx, taskScreen.sy);
        ctx.stroke();
      }
    }

    ctx.setLineDash([]); // reset
  }

  /** (b) Bid edges for orbiting agents */
  private renderBidEdges(
    ctx: CanvasRenderingContext2D,
    camera: Camera,
    w: number,
    h: number,
  ): void {
    for (const agent of this.agents) {
      if (agent.state !== "orbiting" || agent.targetTaskId === null) continue;

      const task = this.tasks.find((t) => t.id === agent.targetTaskId);
      if (!task) continue;

      const lw = (0.8 + agent.wealth / 5000) * camera.zoom;
      ctx.strokeStyle = "rgba(51,51,51,0.35)";
      ctx.lineWidth = lw;

      const agentScreen = worldToScreen(agent.x, agent.y, camera, w, h);
      const taskScreen = worldToScreen(task.x, task.y, camera, w, h);

      ctx.beginPath();
      ctx.moveTo(agentScreen.sx, agentScreen.sy);
      ctx.lineTo(taskScreen.sx, taskScreen.sy);
      ctx.stroke();
    }
  }

  /** (c) Awarded/work edges for in_progress agents */
  private renderWorkEdges(
    ctx: CanvasRenderingContext2D,
    camera: Camera,
    w: number,
    h: number,
  ): void {
    for (const agent of this.agents) {
      if (agent.state !== "in_progress" || agent.targetTaskId === null) continue;

      const task = this.tasks.find((t) => t.id === agent.targetTaskId);
      if (!task) continue;

      const agentScreen = worldToScreen(agent.x, agent.y, camera, w, h);
      const taskScreen = worldToScreen(task.x, task.y, camera, w, h);

      if (agent.role === "worker") {
        // Solid line for workers
        ctx.strokeStyle = "rgba(51,51,51,0.6)";
        ctx.lineWidth = 2 * camera.zoom;

        ctx.beginPath();
        ctx.moveTo(agentScreen.sx, agentScreen.sy);
        ctx.lineTo(taskScreen.sx, taskScreen.sy);
        ctx.stroke();
      } else if (agent.role === "poster") {
        // Dashed line for posters
        ctx.strokeStyle = "rgba(51,51,51,0.25)";
        ctx.setLineDash([4 * camera.zoom, 3 * camera.zoom]);
        ctx.lineWidth = 0.8 * camera.zoom;

        ctx.beginPath();
        ctx.moveTo(agentScreen.sx, agentScreen.sy);
        ctx.lineTo(taskScreen.sx, taskScreen.sy);
        ctx.stroke();

        ctx.setLineDash([]);
      }
    }
  }

  /** (d) Task nodes: hatch-pattern fill, white knockout center, #333 border */
  private renderTaskNodes(
    ctx: CanvasRenderingContext2D,
    camera: Camera,
    w: number,
    h: number,
  ): void {
    for (const task of this.tasks) {
      if (!isVisible(task.x, task.y, task.r, camera, w, h)) continue;

      const { sx, sy } = worldToScreen(task.x, task.y, camera, w, h);
      const sr = task.r * camera.zoom;

      // Hatch-pattern fill
      ctx.fillStyle = this.hatchPatterns[task.category];
      ctx.beginPath();
      ctx.arc(sx, sy, sr, 0, Math.PI * 2);
      ctx.fill();

      // White knockout center (62% of radius)
      ctx.fillStyle = W.bg;
      ctx.beginPath();
      ctx.arc(sx, sy, sr * 0.62, 0, Math.PI * 2);
      ctx.fill();

      // Border
      ctx.strokeStyle = W.borderStrong;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(sx, sy, sr, 0, Math.PI * 2);
      ctx.stroke();

      // Pulse ring for "open" tasks
      if (task.state === "open") {
        const pulseR = sr + (4 + Math.sin(task.pulseAge * 2.5) * 10) * camera.zoom;
        const pulseAlpha = 0.08 + Math.sin(task.pulseAge * 2.5) * 0.07;
        ctx.strokeStyle = `rgba(51,51,51,${pulseAlpha})`;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.arc(sx, sy, pulseR, 0, Math.PI * 2);
        ctx.stroke();
      }
    }
  }

  /** (e) Agent nodes: hatch-pattern fill, white knockout center, #333 border */
  private renderAgentNodes(
    ctx: CanvasRenderingContext2D,
    camera: Camera,
    w: number,
    h: number,
  ): void {
    for (const agent of this.agents) {
      if (!isVisible(agent.x, agent.y, agent.r, camera, w, h)) continue;

      ctx.globalAlpha = agent.opacity;
      const { sx, sy } = worldToScreen(agent.x, agent.y, camera, w, h);
      const sr = agent.r * camera.zoom;

      // Hatch-pattern fill
      ctx.fillStyle = this.hatchPatterns[agent.category];
      ctx.beginPath();
      ctx.arc(sx, sy, sr, 0, Math.PI * 2);
      ctx.fill();

      // White knockout center (62% of radius)
      ctx.fillStyle = W.bg;
      ctx.beginPath();
      ctx.arc(sx, sy, sr * 0.62, 0, Math.PI * 2);
      ctx.fill();

      // Border
      ctx.strokeStyle = W.borderStrong;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(sx, sy, sr, 0, Math.PI * 2);
      ctx.stroke();

      ctx.globalAlpha = 1;
    }
  }

  /** (f) Text labels for agents and tasks (visible at zoom > 0.15) */
  private renderLabels(
    ctx: CanvasRenderingContext2D,
    camera: Camera,
    w: number,
    h: number,
  ): void {
    ctx.textAlign = "center";

    // Agent labels — bold name
    ctx.font = `bold ${Math.max(8, 10 * camera.zoom)}px 'Courier New', monospace`;
    ctx.fillStyle = W.text;

    for (const agent of this.agents) {
      if (!isVisible(agent.x, agent.y, agent.r, camera, w, h)) continue;

      const { sx, sy } = worldToScreen(agent.x, agent.y, camera, w, h);
      const sr = agent.r * camera.zoom;
      ctx.fillText(agent.name, sx, sy - sr - 6 * camera.zoom);
    }

    // Task labels (name + payoff)
    for (const task of this.tasks) {
      if (!isVisible(task.x, task.y, task.r, camera, w, h)) continue;

      const { sx, sy } = worldToScreen(task.x, task.y, camera, w, h);
      const sr = task.r * camera.zoom;

      // Task name in bold
      ctx.font = `bold ${Math.max(8, 10 * camera.zoom)}px 'Courier New', monospace`;
      ctx.fillStyle = W.text;
      const displayName = task.name.length > 9 ? task.name.slice(0, 9) : task.name;
      ctx.fillText(displayName, sx, sy - sr - 12 * camera.zoom);

      // Payoff in muted
      ctx.font = `${Math.max(7, 9 * camera.zoom)}px 'Courier New', monospace`;
      ctx.fillStyle = W.textMuted;
      ctx.fillText(`${Math.round(task.payoff)}\u00A2`, sx, sy - sr - 3 * camera.zoom);
    }
  }

  /** (g) State badges below agent nodes (visible at zoom > 0.22) */
  private renderStateBadges(
    ctx: CanvasRenderingContext2D,
    camera: Camera,
    w: number,
    h: number,
  ): void {
    ctx.font = `${Math.max(6, 8 * camera.zoom)}px 'Courier New', monospace`;
    ctx.fillStyle = W.textMuted;
    ctx.textAlign = "center";

    for (const agent of this.agents) {
      if (!isVisible(agent.x, agent.y, agent.r, camera, w, h)) continue;

      const { sx, sy } = worldToScreen(agent.x, agent.y, camera, w, h);
      const sr = agent.r * camera.zoom;
      ctx.fillText(STATE_LABELS[agent.state], sx, sy + sr + 12 * camera.zoom);
    }
  }

  /** (h) Direction arrows for approaching/fleeing agents */
  private renderDirectionArrows(
    ctx: CanvasRenderingContext2D,
    camera: Camera,
    w: number,
    h: number,
  ): void {
    for (const agent of this.agents) {
      if (agent.state !== "approaching" && agent.state !== "fleeing") continue;
      if (!isVisible(agent.x, agent.y, agent.r, camera, w, h)) continue;

      const { sx, sy } = worldToScreen(agent.x, agent.y, camera, w, h);
      const sr = agent.r * camera.zoom;

      // Small filled triangle ahead of agent in direction of velocity
      const angle = Math.atan2(agent.vy, agent.vx);
      const arrowDist = sr + 6 * camera.zoom;
      const ax = sx + Math.cos(angle) * arrowDist;
      const ay = sy + Math.sin(angle) * arrowDist;
      const arrowSize = 4 * camera.zoom;

      ctx.fillStyle = W.borderStrong;
      ctx.beginPath();
      ctx.moveTo(
        ax + Math.cos(angle) * arrowSize,
        ay + Math.sin(angle) * arrowSize,
      );
      ctx.lineTo(
        ax + Math.cos(angle + 2.3) * arrowSize,
        ay + Math.sin(angle + 2.3) * arrowSize,
      );
      ctx.lineTo(
        ax + Math.cos(angle - 2.3) * arrowSize,
        ay + Math.sin(angle - 2.3) * arrowSize,
      );
      ctx.closePath();
      ctx.fill();
    }
  }

  /** (i) Motion trails for agents with trail data */
  private renderMotionTrails(
    ctx: CanvasRenderingContext2D,
    camera: Camera,
    w: number,
    h: number,
  ): void {
    for (const agent of this.agents) {
      if (agent.trail.length <= 1) continue;

      ctx.globalAlpha = agent.opacity * 0.2;
      ctx.strokeStyle = W.hatchStroke;
      ctx.lineWidth = agent.r * 0.4 * camera.zoom;
      ctx.lineCap = "round";
      ctx.beginPath();

      for (let i = 0; i < agent.trail.length; i++) {
        const { sx: tx, sy: ty } = worldToScreen(
          agent.trail[i].x,
          agent.trail[i].y,
          camera,
          w,
          h,
        );
        if (i === 0) {
          ctx.moveTo(tx, ty);
        } else {
          ctx.lineTo(tx, ty);
        }
      }

      ctx.stroke();
      ctx.globalAlpha = 1;
    }
  }

  /** (j) Ripple rings expanding outward */
  private renderRipples(
    ctx: CanvasRenderingContext2D,
    camera: Camera,
    w: number,
    h: number,
  ): void {
    for (const ripple of this.ripples) {
      const { sx, sy } = worldToScreen(ripple.x, ripple.y, camera, w, h);
      const sr = ripple.r * camera.zoom;
      const alpha = (ripple.life / ripple.maxLife) * 0.4;

      ctx.globalAlpha = alpha;
      ctx.strokeStyle = ripple.color;
      ctx.lineWidth = 1.5 * camera.zoom;
      ctx.beginPath();
      ctx.arc(sx, sy, sr, 0, Math.PI * 2);
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
  }

  /** (k) Coin particles (small squares) */
  private renderParticles(
    ctx: CanvasRenderingContext2D,
    camera: Camera,
    w: number,
    h: number,
  ): void {
    for (const p of this.particles) {
      const { sx, sy } = worldToScreen(p.x, p.y, camera, w, h);
      const alpha = p.life / p.maxLife;

      ctx.globalAlpha = alpha;
      ctx.fillStyle = p.color;
      const s = p.size * camera.zoom;
      ctx.fillRect(sx - s / 2, sy - s / 2, s, s);
      ctx.globalAlpha = 1;
    }
  }
}
