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
