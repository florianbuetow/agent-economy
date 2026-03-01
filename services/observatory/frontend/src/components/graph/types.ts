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

// ─── Hatch pattern definitions (wireframe theme) ────────────────────────────
export const HATCH_DEFS: Record<Category, { angle: number; gap: number }> = {
  data: { angle: 45, gap: 4 },
  writing: { angle: -45, gap: 4 },
  code: { angle: 90, gap: 3 },
  research: { angle: 0, gap: 4 },
  design: { angle: 45, gap: 8 },
  general: { angle: 0, gap: 8 },
};

// ─── State tint colors (pale pastels for the knockout center) ───────────────
export const TASK_STATE_TINTS: Record<TaskState, string> = {
  open: "#fff3b0",       // warm yellow — available
  bidding: "#ffd9a0",    // soft orange — attracting bids
  awarded: "#b8d4ff",    // sky blue — decided
  in_progress: "#b0e8b0", // mint green — work underway
  complete: "#d4b8ff",   // soft violet — absorbing
};

export const AGENT_STATE_TINTS: Record<AgentState, string> = {
  searching: "#e0e0e0",   // light gray — drifting
  approaching: "#b0daff", // soft blue — heading toward task
  inspecting: "#ffe8a0",  // warm gold — evaluating
  orbiting: "#ffc8a0",   // soft peach — bidding
  rejecting: "#f5b8b8",  // soft rose — walking away
  fleeing: "#f5b8b8",    // soft rose — lost bid
  in_progress: "#a8e0a8", // soft green — working
};

// ─── Wireframe theme palette ────────────────────────────────────────────────
export const W = {
  bg: "#ffffff",
  bgCanvas: "#fafafa",
  bgNode: "#f0f0f0",
  border: "#cccccc",
  borderStrong: "#333333",
  hatchStroke: "#aaaaaa",
  text: "#111111",
  textMid: "#444444",
  textMuted: "#888888",
  textFaint: "#bbbbbb",
} as const;

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
export const BG_COLOR = "#fafafa";

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
  absorbStart: { x: number; y: number } | null;
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
