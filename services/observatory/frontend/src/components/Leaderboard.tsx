import { useState } from "react";
import { Link } from "react-router-dom";
import type { AgentListItem, MetricsResponse } from "../types";
import { colors } from "../utils/colorUtils";
import HatchBar from "./HatchBar";

interface LeaderboardProps {
  workers: AgentListItem[];
  posters: AgentListItem[];
  metrics: MetricsResponse | null;
}

type Tab = "workers" | "posters";

function QualityStars({ es, s, d }: { es: number; s: number; d: number }) {
  return (
    <span className="text-[9px] font-mono text-text-muted">
      <span title="Extremely satisfied"><span className={colors.stars}>{"\u2605\u2605\u2605"}</span>{es}</span>{" "}
      <span title="Satisfied"><span className={colors.stars}>{"\u2605\u2605"}</span>{s}</span>{" "}
      <span title="Dissatisfied"><span className={colors.stars}>{"\u2605"}</span>{d}</span>
    </span>
  );
}

export default function Leaderboard({ workers, posters, metrics }: LeaderboardProps) {
  const [tab, setTab] = useState<Tab>("workers");

  return (
    <div className="flex flex-col h-full">
      {/* Tab toggle */}
      <div className="flex border-b border-border">
        <button
          onClick={() => setTab("workers")}
          className={`flex-1 h-8 text-[9px] font-mono uppercase tracking-[1px] border-b-2 cursor-pointer ${
            tab === "workers"
              ? "font-bold text-text border-text"
              : "font-normal text-text-muted border-transparent"
          }`}
        >
          Workers
        </button>
        <button
          onClick={() => setTab("posters")}
          className={`flex-1 h-8 text-[9px] font-mono uppercase tracking-[1px] border-b-2 cursor-pointer ${
            tab === "posters"
              ? "font-bold text-text border-text"
              : "font-normal text-text-muted border-transparent"
          }`}
        >
          Posters
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {tab === "workers" && (
          <div>
            <div className="px-3 pt-3 pb-1 text-[9px] font-mono uppercase tracking-[1.5px] text-text-muted border-b border-border">
              By Tasks Completed
            </div>
            {workers.map((w, i) => (
              <div
                key={w.agent_id}
                className="px-3 py-2 border-b border-border"
              >
                <div className="flex items-baseline justify-between mb-0.5">
                  <div className="flex items-baseline gap-2">
                    <span className="text-[10px] font-mono text-text-faint">
                      {i + 1}
                    </span>
                    <Link
                      to={`/observatory/agents/${w.agent_id}`}
                      className="text-[11px] font-mono font-bold text-text decoration-dashed underline underline-offset-2 hover:text-text-mid"
                    >
                      {w.name}
                    </Link>
                  </div>
                  <span className={`text-[10px] font-mono ${colors.money}`}>
                    {w.stats.total_earned.toLocaleString()} {"\u00a9"} earned
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-[9px] font-mono text-text-muted">
                    {w.stats.tasks_completed_as_worker} tasks completed
                  </span>
                  <QualityStars
                    es={w.stats.delivery_quality.extremely_satisfied}
                    s={w.stats.delivery_quality.satisfied}
                    d={w.stats.delivery_quality.dissatisfied}
                  />
                </div>
              </div>
            ))}
            {workers.length === 0 && (
              <div className="p-3 text-[10px] font-mono text-text-muted text-center">
                No worker data
              </div>
            )}
          </div>
        )}

        {tab === "posters" && (
          <div>
            <div className="px-3 pt-3 pb-1 text-[9px] font-mono uppercase tracking-[1.5px] text-text-muted border-b border-border">
              By Tasks Posted
            </div>
            {posters.map((p, i) => (
              <div
                key={p.agent_id}
                className="px-3 py-2 border-b border-border"
              >
                <div className="flex items-baseline justify-between mb-0.5">
                  <div className="flex items-baseline gap-2">
                    <span className="text-[10px] font-mono text-text-faint">
                      {i + 1}
                    </span>
                    <Link
                      to={`/observatory/agents/${p.agent_id}`}
                      className="text-[11px] font-mono font-bold text-text decoration-dashed underline underline-offset-2 hover:text-text-mid"
                    >
                      {p.name}
                    </Link>
                  </div>
                  <span className={`text-[10px] font-mono ${colors.money}`}>
                    {p.stats.total_spent.toLocaleString()} {"\u00a9"} spent
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-[9px] font-mono text-text-muted">
                    {p.stats.tasks_posted} tasks posted
                  </span>
                  <span className="text-[9px] font-mono text-text-muted">
                    <span title="Extremely satisfied">spec: <span className={colors.stars}>{"\u2605\u2605\u2605"}</span>{p.stats.spec_quality.extremely_satisfied}</span>{" "}
                    <span title="Satisfied"><span className={colors.stars}>{"\u2605\u2605"}</span>{p.stats.spec_quality.satisfied}</span>{" "}
                    <span title="Dissatisfied"><span className={colors.stars}>{"\u2605"}</span>{p.stats.spec_quality.dissatisfied}</span>
                  </span>
                </div>
              </div>
            ))}
            {posters.length === 0 && (
              <div className="p-3 text-[10px] font-mono text-text-muted text-center">
                No poster data
              </div>
            )}

            {/* Economy Spec Quality */}
            {metrics && (
              <div className="px-3 py-3 border-t border-border">
                <div className="text-[9px] font-mono uppercase tracking-[1.5px] text-text-muted border-b border-border pb-1 mb-3">
                  Economy Spec Quality
                </div>
                <div className="space-y-2">
                  <div>
                    <div className="flex justify-between text-[9px] font-mono mb-0.5">
                      <span className="text-text-muted"><span className={colors.stars}>{"\u2605\u2605\u2605"}</span> Extremely satisfied</span>
                      <span className="text-text">
                        {(metrics.spec_quality.extremely_satisfied_pct * 100).toFixed(0)}%
                      </span>
                    </div>
                    <HatchBar pct={metrics.spec_quality.extremely_satisfied_pct * 100} />
                  </div>
                  <div>
                    <div className="flex justify-between text-[9px] font-mono mb-0.5">
                      <span className="text-text-muted"><span className={colors.stars}>{"\u2605\u2605"}</span> Satisfied</span>
                      <span className="text-text">
                        {(metrics.spec_quality.satisfied_pct * 100).toFixed(0)}%
                      </span>
                    </div>
                    <HatchBar pct={metrics.spec_quality.satisfied_pct * 100} />
                  </div>
                  <div>
                    <div className="flex justify-between text-[9px] font-mono mb-0.5">
                      <span className="text-text-muted"><span className={colors.stars}>{"\u2605"}</span> Dissatisfied</span>
                      <span className="text-text">
                        {(metrics.spec_quality.dissatisfied_pct * 100).toFixed(0)}%
                      </span>
                    </div>
                    <HatchBar pct={metrics.spec_quality.dissatisfied_pct * 100} />
                  </div>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
