// ─── Agent State Machine ────────────────────────────────────────────────────
// Core narrative behavior of the economy graph. Each agent moves through
// states autonomously: searching -> approaching -> inspecting -> orbiting/rejecting
// -> fleeing/in_progress -> back to searching.

import type { AgentNode, TaskNode, Category } from "./types";
import { CATEGORIES, AGENT_PREFIXES, WORLD_SIZE } from "./types";

// ─── Helpers ────────────────────────────────────────────────────────────────

/** Compute visual radius from agent wealth */
export function computeAgentRadius(wealth: number): number {
  return 10 + Math.sqrt(wealth / 1000) * 20;
}

function randomRange(min: number, max: number): number {
  return min + Math.random() * (max - min);
}

function pickRandom<T>(arr: readonly T[]): T {
  return arr[Math.floor(Math.random() * arr.length)];
}

// ─── Agent Factory ──────────────────────────────────────────────────────────

/** Spawn an agent at a random world position with random properties */
export function createAgent(id: number): AgentNode {
  const wealth = randomRange(500, 5000);
  const category: Category = pickRandom(CATEGORIES);
  const prefix = pickRandom(AGENT_PREFIXES);

  return {
    id,
    name: `${prefix}-${id}`,
    category,
    wealth,
    r: computeAgentRadius(wealth),
    x: Math.random() * WORLD_SIZE,
    y: Math.random() * WORLD_SIZE,
    vx: 0,
    vy: 0,
    state: "searching",
    targetTaskId: null,
    stateTimer: randomRange(2, 5),
    orbitAngle: 0,
    orbitSpeed: 0,
    opacity: 1,
    trail: [],
    rejectedTasks: [],
    role: null,
    bidderIndex: 0,
  };
}

// ─── State Machine ──────────────────────────────────────────────────────────

/** Tick the agent state machine. Mutates agent in place for performance. */
export function updateAgent(
  agent: AgentNode,
  tasks: TaskNode[],
  agents: AgentNode[],
  dt: number,
): void {

  switch (agent.state) {
    case "searching":
      updateSearching(agent, tasks, agents, dt);
      break;
    case "approaching":
      updateApproaching(agent, tasks, dt);
      break;
    case "inspecting":
      updateInspecting(agent, tasks, dt);
      break;
    case "rejecting":
      updateRejecting(agent, dt);
      break;
    case "orbiting":
      updateOrbiting(agent, tasks, dt);
      break;
    case "fleeing":
      updateFleeing(agent, dt);
      break;
    case "in_progress":
      updateInProgress(agent, tasks, dt);
      break;
  }
}

// ─── searching ──────────────────────────────────────────────────────────────

function updateSearching(
  agent: AgentNode,
  tasks: TaskNode[],
  agents: AgentNode[],
  dt: number,
): void {
  // Random velocity impulses: ~1/20 chance per frame per agent
  if (Math.random() < 0.05) {
    agent.vx += (Math.random() - 0.5) * 0.3;
    agent.vy += (Math.random() - 0.5) * 0.3;
  }

  // Damping
  agent.vx *= 0.97;
  agent.vy *= 0.97;

  // Max speed: clamp to 0.5 world units/frame
  const speed = Math.sqrt(agent.vx * agent.vx + agent.vy * agent.vy);
  if (speed > 0.5) {
    agent.vx = (agent.vx / speed) * 0.5;
    agent.vy = (agent.vy / speed) * 0.5;
  }

  // Soft boundary repulsion: if within 100px of world edge, push inward
  if (agent.x < 100) agent.vx += (100 - agent.x) * 0.01;
  if (agent.x > WORLD_SIZE - 100) agent.vx -= (agent.x - (WORLD_SIZE - 100)) * 0.01;
  if (agent.y < 100) agent.vy += (100 - agent.y) * 0.01;
  if (agent.y > WORLD_SIZE - 100) agent.vy -= (agent.y - (WORLD_SIZE - 100)) * 0.01;

  // Repulsion between searching agents
  for (const other of agents) {
    if (other.id === agent.id || other.state !== "searching") continue;
    const dx = agent.x - other.x;
    const dy = agent.y - other.y;
    const dist = Math.sqrt(dx * dx + dy * dy);
    const minDist = agent.r + other.r + 70;
    if (dist < minDist && dist > 0.01) {
      const force = (minDist - dist) * 0.005;
      agent.vx += (dx / dist) * force;
      agent.vy += (dy / dist) * force;
    }
  }

  // Decrement state timer
  agent.stateTimer -= dt;
  if (agent.stateTimer <= 0) {
    // Find nearest eligible task
    let nearestTask: TaskNode | null = null;
    let nearestDist = Infinity;

    for (const task of tasks) {
      if (task.state !== "open" && task.state !== "bidding") continue;
      if (task.bidders.length >= 4) continue;
      if (agent.rejectedTasks.includes(task.id)) continue;

      const dx = task.x - agent.x;
      const dy = task.y - agent.y;
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist < nearestDist) {
        nearestDist = dist;
        nearestTask = task;
      }
    }

    if (nearestTask) {
      // Found a task -- approach it
      agent.targetTaskId = nearestTask.id;
      agent.state = "approaching";
    } else {
      // No task found -- reset timer and keep searching
      agent.stateTimer = randomRange(2, 5);
      // Clear rejected list if it's getting long
      if (agent.rejectedTasks.length >= 5) {
        agent.rejectedTasks.length = 0;
      }
    }
  }

  // Update position
  agent.x += agent.vx;
  agent.y += agent.vy;
}

// ─── approaching ────────────────────────────────────────────────────────────

function updateApproaching(
  agent: AgentNode,
  tasks: TaskNode[],
  _dt: number,
): void {
  // Find target task
  const task = tasks.find((t) => t.id === agent.targetTaskId);

  // Bail if task gone or no longer eligible
  if (
    !task ||
    (task.state !== "open" && task.state !== "bidding") ||
    task.bidders.length >= 4
  ) {
    agent.state = "searching";
    agent.stateTimer = randomRange(0.5, 1);
    agent.targetTaskId = null;
    return;
  }

  const inspectRadius = task.r + agent.r + 30;

  // Compute direction to task center
  const dx = task.x - agent.x;
  const dy = task.y - agent.y;
  const dist = Math.sqrt(dx * dx + dy * dy);

  if (dist > 0.01) {
    // Target point: offset from task center by inspect radius in direction of approach
    // We steer toward the task center -- when we're close enough we transition
    const speed = Math.min(2.16, dist * 0.06 + 0.4);
    agent.vx = (dx / dist) * speed;
    agent.vy = (dy / dist) * speed;
  }

  // Trail: push current position, keep only last 8
  agent.trail.push({ x: agent.x, y: agent.y });
  if (agent.trail.length > 8) {
    agent.trail.shift();
  }

  // Update position
  agent.x += agent.vx;
  agent.y += agent.vy;

  // Check arrival: when distance to task center < inspect radius + 5
  const newDx = task.x - agent.x;
  const newDy = task.y - agent.y;
  const newDist = Math.sqrt(newDx * newDx + newDy * newDy);

  if (newDist < inspectRadius + 5) {
    agent.state = "inspecting";
    agent.stateTimer = randomRange(0.6, 1.4);
  }

}

// ─── inspecting ─────────────────────────────────────────────────────────────

function updateInspecting(
  agent: AgentNode,
  tasks: TaskNode[],
  dt: number,
): void {
  // Damp velocity
  agent.vx *= 0.85;
  agent.vy *= 0.85;

  // Update position with damped velocity
  agent.x += agent.vx;
  agent.y += agent.vy;

  // Decrement state timer
  agent.stateTimer -= dt;

  if (agent.stateTimer <= 0) {
    const task = tasks.find((t) => t.id === agent.targetTaskId);

    if (!task) {
      agent.state = "searching";
      agent.stateTimer = randomRange(0.5, 1);
      agent.targetTaskId = null;
      return;
    }

    if (Math.random() < 0.7) {
      // 70% chance: transition to orbiting (bid)
      task.bidders.push(agent.id);
      agent.bidderIndex = task.bidders.length - 1;
      agent.orbitAngle = Math.random() * Math.PI * 2;
      agent.orbitSpeed = 0.25 + Math.random() * 0.35;
      agent.state = "orbiting";
    } else {
      // 30% chance: transition to rejecting
      agent.rejectedTasks.push(task.id);
      if (agent.rejectedTasks.length > 5) {
        agent.rejectedTasks.shift();
      }

      // Velocity away from task
      const dx = agent.x - task.x;
      const dy = agent.y - task.y;
      const d = Math.sqrt(dx * dx + dy * dy);
      if (d > 0.01) {
        agent.vx = (dx / d) * 2 + (Math.random() - 0.5);
        agent.vy = (dy / d) * 2 + (Math.random() - 0.5);
      } else {
        agent.vx = (Math.random() - 0.5) * 2;
        agent.vy = (Math.random() - 0.5) * 2;
      }

      agent.stateTimer = randomRange(1.2, 2.0);
      agent.opacity = 0.5;
      agent.state = "rejecting";
    }
  }
}

// ─── rejecting ──────────────────────────────────────────────────────────────

function updateRejecting(agent: AgentNode, dt: number): void {
  // Decay velocity
  agent.vx *= 0.88;
  agent.vy *= 0.88;

  // Update position
  agent.x += agent.vx;
  agent.y += agent.vy;

  // Decrement state timer
  agent.stateTimer -= dt;

  if (agent.stateTimer <= 0) {
    // Transition back to searching
    agent.state = "searching";
    agent.stateTimer = randomRange(0.3, 0.8);
    agent.opacity = 1;
    agent.targetTaskId = null;
  }

  // Hard clamp to world bounds
  agent.x = Math.max(0, Math.min(WORLD_SIZE, agent.x));
  agent.y = Math.max(0, Math.min(WORLD_SIZE, agent.y));
}

// ─── orbiting ───────────────────────────────────────────────────────────────

function updateOrbiting(agent: AgentNode, tasks: TaskNode[], dt: number): void {
  // Find target task
  const task = tasks.find((t) => t.id === agent.targetTaskId);

  if (!task) {
    agent.state = "searching";
    agent.stateTimer = randomRange(0.5, 1);
    agent.targetTaskId = null;
    return;
  }

  // Advance orbit angle
  agent.orbitAngle += agent.orbitSpeed * dt;

  // Compute orbit radius with spacing per bidder
  const orbitR = task.r + agent.r + 40 + agent.bidderIndex * 22;

  // Set position on orbit
  agent.x = task.x + orbitR * Math.cos(agent.orbitAngle);
  agent.y = task.y + orbitR * Math.sin(agent.orbitAngle);

  // Clear trail while orbiting
  agent.trail.length = 0;
}

// ─── fleeing ────────────────────────────────────────────────────────────────

function updateFleeing(agent: AgentNode, dt: number): void {
  // Decay velocity
  agent.vx *= 0.91;
  agent.vy *= 0.91;

  // Update position
  agent.x += agent.vx;
  agent.y += agent.vy;

  // Fade opacity: decrease toward 0.25 over the flee duration
  if (agent.opacity > 0.25) {
    agent.opacity -= dt * 0.5;
    if (agent.opacity < 0.25) agent.opacity = 0.25;
  }

  // Trail: push position, keep last 8
  agent.trail.push({ x: agent.x, y: agent.y });
  if (agent.trail.length > 8) {
    agent.trail.shift();
  }

  // Decrement state timer
  agent.stateTimer -= dt;

  if (agent.stateTimer <= 0) {
    // Transition back to searching
    agent.state = "searching";
    agent.stateTimer = 0.2;
    agent.opacity = 1;
    agent.targetTaskId = null;
    agent.role = null;
  }

  // Hard clamp to world bounds
  agent.x = Math.max(0, Math.min(WORLD_SIZE, agent.x));
  agent.y = Math.max(0, Math.min(WORLD_SIZE, agent.y));
}

// ─── in_progress ────────────────────────────────────────────────────────────

function updateInProgress(
  agent: AgentNode,
  tasks: TaskNode[],
  dt: number,
): void {
  // Find target task
  const task = tasks.find((t) => t.id === agent.targetTaskId);

  if (!task) {
    agent.state = "searching";
    agent.stateTimer = randomRange(0.3, 0.8);
    agent.targetTaskId = null;
    agent.role = null;
    return;
  }

  if (agent.role === "worker") {
    // Worker: orbit at task.r + agent.r + 36, clockwise
    agent.orbitAngle += 0.30 * dt;
    const orbitR = task.r + agent.r + 36;
    agent.x = task.x + orbitR * Math.cos(agent.orbitAngle);
    agent.y = task.y + orbitR * Math.sin(agent.orbitAngle);
  } else if (agent.role === "poster") {
    // Poster: orbit at task.r + agent.r + 60, counter-clockwise
    agent.orbitAngle -= 0.20 * dt;
    const orbitR = task.r + agent.r + 60;
    agent.x = task.x + orbitR * Math.cos(agent.orbitAngle);
    agent.y = task.y + orbitR * Math.sin(agent.orbitAngle);
  }
}
