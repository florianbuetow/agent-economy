import type { MetricsResponse, GDPHistoryResponse } from "../types";
import Sparkline from "./Sparkline";
import HatchBar from "./HatchBar";
import Badge from "./Badge";
import { colors, trendColor, thresholdColor } from "../utils/colorUtils";

interface GDPPanelProps {
  metrics: MetricsResponse | null;
  gdpHistory: GDPHistoryResponse | null;
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[9px] font-mono uppercase tracking-[1.5px] text-text-muted border-b border-border pb-1 mb-3">
      {children}
    </div>
  );
}

function KVRow({
  label,
  value,
  colorClass,
}: {
  label: string;
  value: string;
  colorClass?: string;
}) {
  return (
    <div className="flex justify-between items-baseline py-0.5">
      <span className="text-[10px] font-mono text-text-muted">{label}</span>
      <span className={`text-[11px] font-mono font-bold ${colorClass ?? "text-text"}`}>
        {value}
      </span>
    </div>
  );
}

function normalizePoints(values: number[]): number[] {
  if (values.length < 2) return values;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  return values.map((v) => (v - min) / range);
}

function phaseColor(phase: string): { bg: string; border: string; text: string } {
  switch (phase.toLowerCase()) {
    case "growing":
      return { bg: "var(--color-green)", border: "var(--color-green)", text: "#fff" };
    case "contracting":
      return { bg: "var(--color-red)", border: "var(--color-red)", text: "#fff" };
    default:
      return { bg: "var(--color-amber)", border: "var(--color-amber)", text: "#fff" };
  }
}

export default function GDPPanel({ metrics, gdpHistory }: GDPPanelProps) {
  if (!metrics) {
    return (
      <div className="p-3 text-[10px] font-mono text-text-muted">
        Loading economy data...
      </div>
    );
  }

  const gdpValues = gdpHistory?.data_points.map((d) => d.gdp) ?? [];
  const gdpPoints = normalizePoints(gdpValues);

  const agentsActive = metrics.agents.active || 1;
  const perAgentValues = gdpValues.map((v) => v / agentsActive);
  const perAgentPoints = normalizePoints(perAgentValues);

  const rd = metrics.labor_market.reward_distribution;
  const rdTotal = rd["0_to_10"] + rd["11_to_50"] + rd["51_to_100"] + rd.over_100 || 1;
  const rdBuckets = [
    { label: "0-10 ¢", count: rd["0_to_10"], pct: (rd["0_to_10"] / rdTotal) * 100 },
    { label: "11-50 ¢", count: rd["11_to_50"], pct: (rd["11_to_50"] / rdTotal) * 100 },
    { label: "51-100 ¢", count: rd["51_to_100"], pct: (rd["51_to_100"] / rdTotal) * 100 },
    { label: "100+ ¢", count: rd.over_100, pct: (rd.over_100 / rdTotal) * 100 },
  ];

  const gdpTrend = trendColor(metrics.gdp.rate_per_hour, "up-good");
  const completionPct = metrics.tasks.completion_rate * 100;
  const disputePct = metrics.economy_phase.dispute_rate * 100;
  const pc = phaseColor(metrics.economy_phase.phase);

  return (
    <div className="h-full overflow-y-auto">
      {/* Economy Output */}
      <div className="p-3 border-b border-border">
        <SectionLabel>Economy Output</SectionLabel>
        <div className="flex items-end justify-between mb-2">
          <div>
            <div className={`text-[22px] font-bold font-mono leading-none ${gdpTrend}`}>
              {metrics.gdp.total.toLocaleString()}
            </div>
            <div className="text-[10px] font-mono text-text-muted mt-0.5">
              ¢ total GDP
            </div>
          </div>
          <Sparkline points={gdpPoints} fill />
        </div>
        <div className="text-[10px] font-mono text-text-mid">
          <span className={gdpTrend}>{metrics.gdp.rate_per_hour.toFixed(1)} ¢/hr</span>
          {" "}&middot;{" "}
          {metrics.gdp.last_24h.toLocaleString()} last 24h &middot;{" "}
          {metrics.gdp.last_7d.toLocaleString()} last 7d
        </div>
      </div>

      {/* GDP per Agent */}
      <div className="p-3 border-b border-border">
        <SectionLabel>GDP / Agent</SectionLabel>
        <div className="flex items-end justify-between mb-2">
          <div className={`text-[18px] font-bold font-mono leading-none ${gdpTrend}`}>
            {metrics.gdp.per_agent.toFixed(1)}
          </div>
          <Sparkline points={perAgentPoints} />
        </div>
        <div className="text-[10px] font-mono text-text-muted p-2 border border-border bg-bg-off">
          {metrics.agents.active} active of {metrics.agents.total_registered} registered
          &middot; {metrics.agents.with_completed_tasks} with completions
        </div>
      </div>

      {/* Economy Phase */}
      <div className="p-3 border-b border-border">
        <SectionLabel>Economy Phase</SectionLabel>
        <div className="flex items-center gap-2">
          <Badge
            filled
            style={{
              backgroundColor: pc.bg,
              borderColor: pc.border,
              color: pc.text,
            }}
          >
            {metrics.economy_phase.phase.toUpperCase()}
          </Badge>
          <span className="text-[10px] font-mono text-text-muted">
            creation trend: {metrics.economy_phase.task_creation_trend}
          </span>
        </div>
        <div className={`text-[10px] font-mono mt-1 ${thresholdColor(disputePct, 5, 2, true)}`}>
          dispute rate: {disputePct.toFixed(1)}%
        </div>
      </div>

      {/* Labor Market */}
      <div className="p-3 border-b border-border">
        <SectionLabel>Labor Market</SectionLabel>
        <KVRow
          label="Avg bids / task"
          value={metrics.labor_market.avg_bids_per_task.toFixed(1)}
        />
        <KVRow
          label="Acceptance latency"
          value={`${metrics.labor_market.acceptance_latency_minutes.toFixed(0)} min`}
        />
        <KVRow
          label="Completion rate"
          value={`${completionPct.toFixed(1)}%`}
          colorClass={thresholdColor(completionPct, 80, 50)}
        />
        <KVRow
          label="Avg reward"
          value={`${metrics.labor_market.avg_reward.toFixed(1)} ¢`}
          colorClass={colors.money}
        />
        <KVRow
          label="Posting rate"
          value={`${metrics.labor_market.task_posting_rate.toFixed(1)}/hr`}
        />
      </div>

      {/* Reward Distribution */}
      <div className="p-3">
        <SectionLabel>Reward Distribution</SectionLabel>
        <div className="space-y-2">
          {rdBuckets.map((b) => (
            <div key={b.label}>
              <div className="flex justify-between text-[9px] font-mono mb-0.5">
                <span className="text-text-muted">{b.label}</span>
                <span className="text-text">
                  {b.count} ({b.pct.toFixed(0)}%)
                </span>
              </div>
              <HatchBar pct={b.pct} />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
