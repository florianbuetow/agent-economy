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

// --- Quarterly Report ---
export interface QuarterlyPeriod {
  start: string;
  end: string;
}

export interface QuarterlyGDP {
  total: number;
  previous_quarter: number;
  delta_pct: number;
  per_agent: number;
}

export interface QuarterlyTasks {
  posted: number;
  completed: number;
  disputed: number;
  completion_rate: number;
}

export interface QuarterlyLaborMarket {
  avg_bids_per_task: number;
  avg_time_to_acceptance_minutes: number;
  avg_reward: number;
}

export interface QuarterlySpecQuality {
  avg_score: number;
  previous_quarter_avg: number;
  delta_pct: number;
}

export interface QuarterlyAgents {
  new_registrations: number;
  total_at_quarter_end: number;
}

export interface NotableTask {
  task_id: string;
  title: string;
  reward?: number;
  bid_count?: number;
}

export interface NotableAgent {
  agent_id: string;
  name: string;
  earned?: number;
  spent?: number;
}

export interface QuarterlyNotable {
  highest_value_task: NotableTask | null;
  most_competitive_task: NotableTask | null;
  top_workers: NotableAgent[];
  top_posters: NotableAgent[];
}

export interface QuarterlyReportResponse {
  quarter: string;
  period: QuarterlyPeriod;
  gdp: QuarterlyGDP;
  tasks: QuarterlyTasks;
  labor_market: QuarterlyLaborMarket;
  spec_quality: QuarterlySpecQuality;
  agents: QuarterlyAgents;
  notable: QuarterlyNotable;
}
