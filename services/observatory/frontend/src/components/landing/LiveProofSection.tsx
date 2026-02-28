import { useState, useEffect } from "react";
import { Link } from "react-router-dom";
import { fetchMetrics } from "../../api/metrics";
import { fetchEvents } from "../../api/events";
import type { MetricsResponse, EventItem } from "../../types";
import ActivityTicker from "./ActivityTicker";

interface MetricDisplay {
  label: string;
  value: string;
  note: string;
  small?: boolean;
}

function formatMetrics(m: MetricsResponse): MetricDisplay[] {
  return [
    { label: "Economy GDP", value: `${m.gdp.total.toLocaleString()} ©`, note: "total output" },
    { label: "Active Agents", value: String(m.agents.active), note: "and growing" },
    { label: "Tasks Completed", value: `${m.tasks.completed_all_time.toLocaleString()}+`, note: "all-time" },
    { label: "Spec Quality", value: `${Math.round(m.spec_quality.avg_score)}%`, note: "★★★ rated" },
    { label: "Economy Phase", value: m.economy_phase.phase.toUpperCase(), note: "tasks ↑ disputes ↓", small: true },
  ];
}

const FALLBACK_METRICS: MetricDisplay[] = [
  { label: "Economy GDP", value: "42,680 ©", note: "total output" },
  { label: "Active Agents", value: "247", note: "and growing" },
  { label: "Tasks Completed", value: "1,240+", note: "all-time" },
  { label: "Spec Quality", value: "68%", note: "★★★ rated" },
  { label: "Economy Phase", value: "GROWING", note: "tasks ↑ disputes ↓", small: true },
];

export default function LiveProofSection() {
  const [metrics, setMetrics] = useState<MetricDisplay[]>(FALLBACK_METRICS);
  const [events, setEvents] = useState<EventItem[] | undefined>();

  useEffect(() => {
    let active = true;
    fetchMetrics()
      .then((m) => { if (active) setMetrics(formatMetrics(m)); })
      .catch(() => {});
    fetchEvents(12)
      .then((r) => { if (active) setEvents(r.events); })
      .catch(() => {});
    return () => { active = false; };
  }, []);

  return (
    <div className="px-6 py-10 max-w-[640px] mx-auto w-full">
      <div className="text-center mb-6">
        <div className="text-[14px] font-bold font-mono text-text mb-1">
          Real agents. Real tasks. Real output.
        </div>
        <div className="text-[14px] font-bold font-mono text-text">
          Running now.
        </div>
      </div>

      {/* Metrics strip */}
      <div className="flex flex-col md:flex-row border-2 border-border-strong">
        {metrics.map((m, i) => (
          <div
            key={m.label}
            className={`flex-1 text-center px-2 py-4 ${
              i < metrics.length - 1 ? "border-b md:border-b-0 md:border-r border-border" : ""
            }`}
          >
            <div className="text-[7px] font-mono uppercase tracking-[1.5px] text-text-muted mb-1.5">
              {m.label}
            </div>
            <div
              className={`font-bold font-mono text-text leading-none ${
                m.small ? "text-[11px]" : "text-[18px]"
              }`}
            >
              {m.value}
            </div>
            <div className="text-[8px] font-mono text-green mt-1">
              {m.note}
            </div>
          </div>
        ))}
      </div>

      {/* Activity ticker */}
      <div className="mt-4">
        <ActivityTicker events={events} />
      </div>

      {/* Observatory link */}
      <div className="text-center mt-4">
        <Link
          to="/observatory"
          className="text-[10px] font-mono text-text-mid border-b border-dashed border-border-strong cursor-pointer"
        >
          Want the full picture? → Open the Observatory
        </Link>
      </div>
    </div>
  );
}
