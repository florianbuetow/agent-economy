import type { MetricsResponse } from "../types";
import { colors, thresholdColor } from "../utils/colorUtils";

interface VitalsBarProps {
  metrics: MetricsResponse | null;
  connected: boolean;
}

interface Vital {
  label: string;
  value: string;
  delta?: string;
  up?: boolean;
  colorClass?: string;
  deltaColorClass?: string;
}

function formatVitals(m: MetricsResponse): Vital[] {
  const unemploymentPct = m.labor_market.unemployment_rate * 100;

  return [
    { label: "Active Agents", value: String(m.agents.active) },
    { label: "Open Tasks", value: String(m.tasks.open) },
    { label: "Completed (24h)", value: String(m.tasks.completed_24h) },
    {
      label: "GDP (Total)",
      value: m.gdp.total.toLocaleString(),
      delta: `${m.gdp.rate_per_hour.toFixed(1)}/hr`,
      up: true,
      deltaColorClass: m.gdp.rate_per_hour > 0 ? colors.positive : colors.negative,
    },
    { label: "GDP / Agent", value: m.gdp.per_agent.toFixed(1) },
    {
      label: "Unemployment",
      value: `${unemploymentPct.toFixed(1)}%`,
      colorClass: thresholdColor(unemploymentPct, 20, 10, true),
    },
    {
      label: "Escrow Locked",
      value: `${m.escrow.total_locked.toLocaleString()} ¢`,
      colorClass: colors.escrow,
    },
  ];
}

export default function VitalsBar({ metrics, connected }: VitalsBarProps) {
  const vitals = metrics ? formatVitals(metrics) : [];

  return (
    <div className="flex items-center border-b border-border-strong bg-bg-off px-4 h-[38px] shrink-0">
      {vitals.map((v, i) => (
        <div
          key={v.label}
          className={`flex items-center gap-3 pr-4 mr-4 whitespace-nowrap ${
            i < vitals.length - 1 ? "border-r border-border" : ""
          }`}
        >
          <div>
            <div className="text-[8px] font-mono uppercase tracking-[1.5px] text-text-muted">
              {v.label}
            </div>
            <div className="flex items-baseline gap-1">
              <span className={`text-[13px] font-bold font-mono ${v.colorClass ?? "text-text"}`}>
                {v.value}
              </span>
              {v.delta && (
                <span className={`text-[10px] font-mono ${v.deltaColorClass ?? "text-text-mid"}`}>
                  {v.up ? "↑" : "↓"}{v.delta}
                </span>
              )}
            </div>
          </div>
        </div>
      ))}
      <div className="ml-auto flex items-center gap-1.5">
        <div
          className={`w-1.5 h-1.5 rounded-full ${
            connected ? colors.live : "bg-text-muted"
          }`}
          style={{ animation: connected ? "pulse-dot 2s infinite" : "none" }}
        />
        <span className="text-[8px] font-mono uppercase tracking-[1.5px] text-text-muted">
          {connected ? "LIVE" : "OFFLINE"}
        </span>
      </div>
    </div>
  );
}
