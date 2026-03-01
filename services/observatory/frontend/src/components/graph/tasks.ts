// ─── Task State Machine ─────────────────────────────────────────────────────
// Core task lifecycle: open -> bidding -> awarded -> in_progress -> complete -> respawn.
// The key visual moment is "complete" where the task flies into the winning agent
// and the agent visibly grows from the wealth gain.

import type { TaskNode, AgentNode, Particle, Ripple, Category } from "./types";
import { CATEGORIES, CATEGORY_COLORS, TASK_NAMES, WORLD_SIZE } from "./types";
import { computeAgentRadius } from "./agents";
import { spawnCoinParticles, spawnRipples } from "./effects";

// ─── Helpers ────────────────────────────────────────────────────────────────

function smoothstep(t: number): number {
  const x = Math.max(0, Math.min(1, t));
  return x * x * (3 - 2 * x);
}

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

function randomRange(min: number, max: number): number {
  return min + Math.random() * (max - min);
}

function pickRandom<T>(arr: readonly T[]): T {
  return arr[Math.floor(Math.random() * arr.length)];
}

// ─── Task Radius ────────────────────────────────────────────────────────────

/** Compute visual radius from task payoff */
export function computeTaskRadius(payoff: number): number {
  return 18 + Math.sqrt(payoff / 500) * 28;
}

// ─── Task Factory ───────────────────────────────────────────────────────────

/** Spawn a task at a random world position with random properties */
export function createTask(id: number): TaskNode {
  const category: Category = pickRandom(CATEGORIES);
  const payoff = randomRange(200, 2000);

  return {
    id,
    name: pickRandom(TASK_NAMES),
    category,
    payoff,
    r: computeTaskRadius(payoff),
    x: Math.random() * WORLD_SIZE,
    y: Math.random() * WORLD_SIZE,
    state: "open",
    stateTimer: randomRange(2, 7),
    bidders: [],
    winnerId: null,
    posterId: null,
    pulseAge: 0,
    absorbStart: null,
    absorbTarget: null,
    absorbProgress: 0,
    absorbStartR: 0,
  };
}

// ─── Respawn ────────────────────────────────────────────────────────────────

/** Reset a completed task to a new random position, category, payoff, name, and "open" state. */
export function respawnTask(task: TaskNode): void {
  const category: Category = pickRandom(CATEGORIES);
  const payoff = randomRange(200, 2000);

  task.name = pickRandom(TASK_NAMES);
  task.category = category;
  task.payoff = payoff;
  task.r = computeTaskRadius(payoff);
  task.x = Math.random() * WORLD_SIZE;
  task.y = Math.random() * WORLD_SIZE;
  task.state = "open";
  task.stateTimer = randomRange(2, 7);
  task.bidders = [];
  task.winnerId = null;
  task.posterId = null;
  task.pulseAge = 0;
  task.absorbStart = null;
  task.absorbTarget = null;
  task.absorbProgress = 0;
  task.absorbStartR = 0;
}

// ─── State Machine ──────────────────────────────────────────────────────────

/** Tick the task state machine. Mutates task (and agents) in place. Returns effects to spawn. */
export function updateTask(
  task: TaskNode,
  agents: AgentNode[],
  dt: number,
): { particles: Particle[]; ripples: Ripple[] } {
  let particles: Particle[] = [];
  let ripples: Ripple[] = [];

  switch (task.state) {
    case "open":
      updateOpen(task, dt);
      break;
    case "bidding": {
      const effects = updateBidding(task, agents, dt);
      ripples = effects.ripples;
      break;
    }
    case "awarded":
      updateAwarded(task, dt);
      break;
    case "in_progress": {
      const effects = updateInProgress(task, agents, dt);
      particles = effects.particles;
      ripples = effects.ripples;
      break;
    }
    case "complete":
      updateComplete(task, agents, dt);
      break;
  }

  return { particles, ripples };
}

// ─── open ───────────────────────────────────────────────────────────────────

function updateOpen(task: TaskNode, dt: number): void {
  // Increment pulse age for visual pulsing
  task.pulseAge += dt;

  // Decrement state timer
  task.stateTimer -= dt;

  // Check if any agent has registered as a bidder
  if (task.bidders.length > 0) {
    task.state = "bidding";
    task.stateTimer = randomRange(3, 6);
    return;
  }

  // If stateTimer expires with no bidders, respawn the task
  if (task.stateTimer <= 0) {
    respawnTask(task);
  }
}

// ─── bidding ────────────────────────────────────────────────────────────────

function updateBidding(
  task: TaskNode,
  agents: AgentNode[],
  dt: number,
): { ripples: Ripple[] } {
  task.stateTimer -= dt;

  if (task.stateTimer <= 0) {
    // Transition to "awarded"
    task.state = "awarded";

    // Pick random winner from bidders
    task.winnerId = task.bidders[Math.floor(Math.random() * task.bidders.length)];

    // Handle losing bidders: set them to "fleeing"
    for (const bidderId of task.bidders) {
      if (bidderId === task.winnerId) continue;

      const loser = agents.find((a) => a.id === bidderId);
      if (!loser) continue;

      const dx = loser.x - task.x;
      const dy = loser.y - task.y;
      const dist = Math.sqrt(dx * dx + dy * dy);

      if (dist > 0.01) {
        loser.vx = (dx / dist) * 4 + (Math.random() - 0.5);
        loser.vy = (dy / dist) * 4 + (Math.random() - 0.5);
      } else {
        loser.vx = (Math.random() - 0.5) * 4;
        loser.vy = (Math.random() - 0.5) * 4;
      }

      loser.state = "fleeing";
      loser.stateTimer = 1.5 + Math.random() * 0.8;
      // opacity stays at current value
    }

    // Handle winner agent
    const winner = agents.find((a) => a.id === task.winnerId);
    if (winner) {
      winner.state = "in_progress";
      winner.role = "worker";
      winner.wealth += task.payoff * 0.1;
      winner.r = computeAgentRadius(winner.wealth);
      winner.targetTaskId = task.id;
    }

    // Assign a poster: find a random agent in "searching" state (not in bidders)
    const searchingAgents = agents.filter(
      (a) => a.state === "searching" && !task.bidders.includes(a.id),
    );
    if (searchingAgents.length > 0) {
      const poster = pickRandom(searchingAgents);
      poster.state = "in_progress";
      poster.role = "poster";
      poster.targetTaskId = task.id;
      task.posterId = poster.id;
    }

    // Set task timer for awarded phase
    task.stateTimer = randomRange(2, 4);

    // Spawn ripple effects at task position with category color
    return { ripples: spawnRipples(task.x, task.y, CATEGORY_COLORS[task.category]) };
  }

  return { ripples: [] };
}

// ─── awarded ────────────────────────────────────────────────────────────────

function updateAwarded(task: TaskNode, dt: number): void {
  task.stateTimer -= dt;

  if (task.stateTimer <= 0) {
    task.state = "in_progress";
    task.stateTimer = randomRange(5, 10);
  }
}

// ─── in_progress ────────────────────────────────────────────────────────────

function updateInProgress(
  task: TaskNode,
  agents: AgentNode[],
  dt: number,
): { particles: Particle[]; ripples: Ripple[] } {
  task.stateTimer -= dt;

  if (task.stateTimer <= 0) {
    // Transition to "complete"
    task.state = "complete";

    // Find the winner agent
    const winner = agents.find((a) => a.id === task.winnerId);

    if (winner) {
      // The money shot: agent grows
      winner.wealth += task.payoff;
      winner.r = computeAgentRadius(winner.wealth);

      // Start absorption animation: task flies into winner
      task.absorbStart = { x: task.x, y: task.y };
      task.absorbTarget = { x: winner.x, y: winner.y };
      task.absorbProgress = 0;
      task.absorbStartR = task.r;
    }

    // Set timer for absorption duration
    task.stateTimer = 1.0;

    // Release poster agent back to "searching"
    if (task.posterId !== null) {
      const poster = agents.find((a) => a.id === task.posterId);
      if (poster) {
        poster.state = "searching";
        poster.stateTimer = 0.5;
        poster.targetTaskId = null;
        poster.role = null;
      }
    }

    // Spawn effects at winner position
    if (winner) {
      return {
        particles: spawnCoinParticles(winner.x, winner.y),
        ripples: spawnRipples(winner.x, winner.y, CATEGORY_COLORS[task.category]),
      };
    }

    return { particles: [], ripples: [] };
  }

  return { particles: [], ripples: [] };
}

// ─── complete ───────────────────────────────────────────────────────────────

function updateComplete(task: TaskNode, agents: AgentNode[], dt: number): void {
  // Advance absorption progress (1.0s duration)
  task.absorbProgress += dt / 1.0;

  // Interpolate task position toward target using smoothstep
  if (task.absorbTarget && task.absorbStart) {
    const t = smoothstep(task.absorbProgress);
    task.x = lerp(task.absorbStart.x, task.absorbTarget.x, t);
    task.y = lerp(task.absorbStart.y, task.absorbTarget.y, t);
    // Shrink radius
    task.r = task.absorbStartR * (1 - task.absorbProgress);
  }

  // When absorption completes, respawn and release winner
  if (task.absorbProgress >= 1.0) {
    // Release winner to "searching"
    if (task.winnerId !== null) {
      const winner = agents.find((a) => a.id === task.winnerId);
      if (winner) {
        winner.state = "searching";
        winner.stateTimer = 0.5;
        winner.targetTaskId = null;
        winner.role = null;
      }
    }

    // Respawn the task
    respawnTask(task);
  }
}
