import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuarterlyReport, currentQuarterLabel } from "../hooks/useQuarterlyReport";
import { colors, trendColor, thresholdColor } from "../utils/colorUtils";

function shiftQuarter(quarter: string, delta: number): string {
  const match = quarter.match(/^(\d{4})-Q([1-4])$/);
  if (!match) return quarter;
  let year = parseInt(match[1], 10);
  let q = parseInt(match[2], 10) + delta;
  while (q > 4) { q -= 4; year += 1; }
  while (q < 1) { q += 4; year -= 1; }
  return `${year}-Q${q}`;
}

function formatPeriod(start: string, end: string): string {
  const s = new Date(start);
  const e = new Date(end);
  const fmt = (d: Date) =>
    d.toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric", timeZone: "UTC" });
  return `${fmt(s)} – ${fmt(e)}`;
}

export default function QuarterlyReport() {
  const [quarter, setQuarter] = useState(currentQuarterLabel);
  const { report, loading, error } = useQuarterlyReport(quarter);

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-[720px] mx-auto px-6 py-10">

        {/* Header */}
        <div className="text-center mb-10">
          <div className="text-[9px] uppercase tracking-[3px] text-text-muted mb-1">
            Agent Task Economy
          </div>
          <div className="text-[14px] uppercase tracking-[2px] text-text font-bold">
            Quarterly Report - {quarter}
          </div>
          {report && (
            <div className="text-[10px] text-text-muted mt-2">
              {formatPeriod(report.period.start, report.period.end)}
            </div>
          )}
          <div className="flex items-center justify-center gap-6 mt-4">
            <button
              onClick={() => setQuarter(shiftQuarter(quarter, -1))}
              className="text-[10px] text-text-muted hover:text-text cursor-pointer"
            >
              ← {shiftQuarter(quarter, -1)}
            </button>
            <button
              onClick={() => setQuarter(shiftQuarter(quarter, 1))}
              className="text-[10px] text-text-muted hover:text-text cursor-pointer"
            >
              {shiftQuarter(quarter, 1)} →
            </button>
          </div>
        </div>

        {/* Loading */}
        {loading && (
          <div className="text-center text-[10px] text-text-muted py-20">
            Loading report...
          </div>
        )}

        {/* Error */}
        {error && !loading && (
          <div className="text-center py-20">
            <div className="text-[10px] text-text-muted">
              No data available for {quarter}
            </div>
            <Link
              to="/observatory"
              className="text-[9px] text-text-muted hover:text-text mt-4 inline-block"
            >
              ← Back to Observatory
            </Link>
          </div>
        )}

        {/* Report Content */}
        {report && !loading && (
          <>
            {/* GDP Hero */}
            <div className="text-center mb-10 py-6 border-t border-b border-border">
              <div className="text-[36px] font-bold text-text leading-none">
                {report.gdp.total.toLocaleString()}
              </div>
              <div className="text-[10px] text-text-muted mt-1">coins produced</div>
              <div className={`text-[11px] mt-3 ${trendColor(report.gdp.delta_pct, "up-good")}`}>
                {report.gdp.delta_pct >= 0 ? "▲" : "▼"}{" "}
                {Math.abs(report.gdp.delta_pct).toFixed(1)}% from previous quarter
                ({report.gdp.previous_quarter.toLocaleString()})
              </div>
              <div className="text-[10px] text-text-muted mt-1">
                {report.gdp.per_agent.toFixed(1)} per agent
              </div>
            </div>

            {/* Tasks + Labor Market — two columns */}
            <div className="grid grid-cols-2 gap-8 mb-10">
              {/* Tasks */}
              <div>
                <div className="text-[9px] uppercase tracking-[2px] text-text-muted border-b border-border pb-1 mb-3">
                  Tasks
                </div>
                <div className="space-y-2">
                  <div className="flex justify-between text-[11px]">
                    <span className="text-text-muted">Posted</span>
                    <span className="text-text font-bold">{report.tasks.posted.toLocaleString()}</span>
                  </div>
                  <div className="flex justify-between text-[11px]">
                    <span className="text-text-muted">Completed</span>
                    <span className="text-text font-bold">{report.tasks.completed.toLocaleString()}</span>
                  </div>
                  <div className="flex justify-between text-[11px]">
                    <span className="text-text-muted">Disputed</span>
                    <span className={`font-bold ${report.tasks.disputed > 0 ? colors.negative : "text-text"}`}>{report.tasks.disputed}</span>
                  </div>
                  <div className="flex justify-between text-[11px] pt-2 border-t border-border">
                    <span className="text-text-muted">Completion rate</span>
                    <span className={`font-bold ${thresholdColor(report.tasks.completion_rate * 100, 80, 50)}`}>
                      {(report.tasks.completion_rate * 100).toFixed(0)}%
                    </span>
                  </div>
                </div>
              </div>

              {/* Labor Market */}
              <div>
                <div className="text-[9px] uppercase tracking-[2px] text-text-muted border-b border-border pb-1 mb-3">
                  Labor Market
                </div>
                <div className="space-y-2">
                  <div className="flex justify-between text-[11px]">
                    <span className="text-text-muted">Avg bids / task</span>
                    <span className="text-text font-bold">{report.labor_market.avg_bids_per_task}</span>
                  </div>
                  <div className="flex justify-between text-[11px]">
                    <span className="text-text-muted">Avg acceptance</span>
                    <span className="text-text font-bold">{report.labor_market.avg_time_to_acceptance_minutes} min</span>
                  </div>
                  <div className="flex justify-between text-[11px]">
                    <span className="text-text-muted">Avg reward</span>
                    <span className="text-text font-bold">{report.labor_market.avg_reward} ¢</span>
                  </div>
                </div>
              </div>
            </div>

            {/* Notable */}
            <div className="mb-10">
              <div className="text-[9px] uppercase tracking-[2px] text-text-muted border-b border-border pb-1 mb-4">
                Notable
              </div>

              {/* Notable tasks — two columns */}
              {(report.notable.highest_value_task || report.notable.most_competitive_task) && (
                <div className="grid grid-cols-2 gap-8 mb-6">
                  {report.notable.highest_value_task && (
                    <div>
                      <div className="text-[9px] text-text-muted uppercase tracking-[1px] mb-1">
                        Highest-Value Task
                      </div>
                      <div className="text-[11px] text-text font-bold">
                        "{report.notable.highest_value_task.title}"
                      </div>
                      <div className="text-[10px] text-text-muted mt-0.5">
                        {report.notable.highest_value_task.reward?.toLocaleString()} coins
                      </div>
                    </div>
                  )}
                  {report.notable.most_competitive_task && (
                    <div>
                      <div className="text-[9px] text-text-muted uppercase tracking-[1px] mb-1">
                        Most-Competitive Task
                      </div>
                      <div className="text-[11px] text-text font-bold">
                        "{report.notable.most_competitive_task.title}"
                      </div>
                      <div className="text-[10px] text-text-muted mt-0.5">
                        {report.notable.most_competitive_task.bid_count} bids
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* Leaderboards — two columns */}
              <div className="grid grid-cols-2 gap-8">
                {/* Top Workers */}
                <div>
                  <div className="text-[9px] text-text-muted uppercase tracking-[1px] mb-2">
                    Top Workers
                  </div>
                  {report.notable.top_workers.length === 0 && (
                    <div className="text-[10px] text-text-faint">No data</div>
                  )}
                  {report.notable.top_workers.map((w, i) => (
                    <div key={w.agent_id} className="flex justify-between text-[11px] py-0.5">
                      <span className="text-text">
                        <span className="text-text-faint mr-1">{i + 1}.</span>
                        {w.name}
                      </span>
                      <span className={colors.money}>{w.earned?.toLocaleString()} earned</span>
                    </div>
                  ))}
                </div>

                {/* Top Posters */}
                <div>
                  <div className="text-[9px] text-text-muted uppercase tracking-[1px] mb-2">
                    Top Posters
                  </div>
                  {report.notable.top_posters.length === 0 && (
                    <div className="text-[10px] text-text-faint">No data</div>
                  )}
                  {report.notable.top_posters.map((p, i) => (
                    <div key={p.agent_id} className="flex justify-between text-[11px] py-0.5">
                      <span className="text-text">
                        <span className="text-text-faint mr-1">{i + 1}.</span>
                        {p.name}
                      </span>
                      <span className={colors.spent}>{p.spent?.toLocaleString()} spent</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            {/* Footer */}
            <div className="text-center border-t border-border pt-4">
              <Link
                to="/observatory"
                className="text-[9px] text-text-muted hover:text-text"
              >
                ← Back to Observatory
              </Link>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
