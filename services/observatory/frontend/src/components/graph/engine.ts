// ─── Engine: Core Simulation Loop & Canvas Renderer ─────────────────────────
// Orchestrates the entire economy graph visualization. Owns all simulation
// state (agents, tasks, particles, ripples), runs the update loop at 60fps,
// and renders everything to a Canvas 2D context in back-to-front order.

import type { AgentNode, TaskNode, AgentState, Particle, Ripple } from "./types.ts";
import { BG_COLOR, CATEGORY_COLORS, STATE_LABELS } from "./types.ts";
import { createCamera, fitToViewport, worldToScreen, isVisible } from "./camera.ts";
import type { Camera } from "./camera.ts";
import { createAgent, updateAgent } from "./agents.ts";
import { createTask, updateTask } from "./tasks.ts";
import { updateParticle, updateRipple } from "./effects.ts";

// ─── Helpers ────────────────────────────────────────────────────────────────

/** Convert hex color to rgba with alpha */
function adjustAlpha(hex: string, alpha: number): string {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${alpha})`;
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
    ctx.strokeStyle = "rgba(255,255,255,0.08)";
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
      ctx.strokeStyle = "rgba(255,255,255,0.4)";
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
        ctx.strokeStyle = "rgba(255,255,255,0.7)";
        ctx.lineWidth = 2 * camera.zoom;

        ctx.beginPath();
        ctx.moveTo(agentScreen.sx, agentScreen.sy);
        ctx.lineTo(taskScreen.sx, taskScreen.sy);
        ctx.stroke();
      } else if (agent.role === "poster") {
        // Dashed line for posters
        ctx.strokeStyle = "rgba(255,255,255,0.3)";
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

  /** (d) Task nodes with glow, gradient fill, outline, and pulse ring */
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

      // Glow
      ctx.shadowColor = CATEGORY_COLORS[task.category];
      ctx.shadowBlur = 12 * camera.zoom;

      // Radial gradient fill
      const grad = ctx.createRadialGradient(sx, sy, 0, sx, sy, sr);
      const color = CATEGORY_COLORS[task.category];
      grad.addColorStop(0, color);
      grad.addColorStop(1, adjustAlpha(color, 0.6));
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.arc(sx, sy, sr, 0, Math.PI * 2);
      ctx.fill();

      // White outline
      ctx.shadowBlur = 0;
      ctx.strokeStyle = "rgba(255,255,255,0.8)";
      ctx.lineWidth = 1;
      ctx.stroke();

      // Pulse ring for "open" tasks
      if (task.state === "open") {
        const pulseR = sr + (4 + Math.sin(task.pulseAge * 2.5) * 10) * camera.zoom;
        const pulseAlpha = 0.1 + Math.sin(task.pulseAge * 2.5) * 0.09;
        ctx.strokeStyle = `rgba(255,255,255,${pulseAlpha})`;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.arc(sx, sy, pulseR, 0, Math.PI * 2);
        ctx.stroke();
      }
    }
  }

  /** (e) Agent nodes with glow, gradient fill, and outline */
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

      // Glow
      ctx.shadowColor = CATEGORY_COLORS[agent.category];
      ctx.shadowBlur = 10 * camera.zoom;

      // Radial gradient
      const grad = ctx.createRadialGradient(sx, sy, 0, sx, sy, sr);
      grad.addColorStop(0, CATEGORY_COLORS[agent.category]);
      grad.addColorStop(0.7, adjustAlpha(CATEGORY_COLORS[agent.category], 0.7));
      grad.addColorStop(1, adjustAlpha(CATEGORY_COLORS[agent.category], 0.4));
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.arc(sx, sy, sr, 0, Math.PI * 2);
      ctx.fill();

      // White outline
      ctx.shadowBlur = 0;
      ctx.strokeStyle = "rgba(255,255,255,0.6)";
      ctx.lineWidth = 1;
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
    ctx.font = `bold ${Math.max(8, 10 * camera.zoom)}px 'Courier New', monospace`;
    ctx.fillStyle = "rgba(255,255,255,0.9)";
    ctx.textAlign = "center";

    // Agent labels
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
      ctx.fillText(
        `${task.name} ($${Math.round(task.payoff)})`,
        sx,
        sy - sr - 6 * camera.zoom,
      );
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
    ctx.fillStyle = "rgba(255,255,255,0.5)";
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

      ctx.fillStyle = "rgba(255,255,255,0.6)";
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

      ctx.globalAlpha = agent.opacity * 0.3;
      ctx.strokeStyle = CATEGORY_COLORS[agent.category];
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
      const alpha = (ripple.life / ripple.maxLife) * 0.5;

      ctx.globalAlpha = alpha;
      ctx.strokeStyle = ripple.color;
      ctx.lineWidth = 2 * camera.zoom;
      ctx.beginPath();
      ctx.arc(sx, sy, sr, 0, Math.PI * 2);
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
  }

  /** (k) Coin particles (gold squares) */
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
