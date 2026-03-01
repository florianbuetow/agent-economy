import { useState, useEffect } from "react";
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  ArcElement,
  RadialLinearScale,
  Filler,
  Tooltip,
} from "chart.js";
import { Line, Doughnut, Radar } from "react-chartjs-2";
import type { MetricsResponse, GDPHistoryResponse } from "../types";
import { fetchGDPHistory } from "../api/metrics";
import { cssVar, tooltipBg } from "../utils/colorUtils";

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  ArcElement,
  RadialLinearScale,
  Filler,
  Tooltip,
);

const TABS = ["GDP", "HEALTH", "TASKS"] as const;
type Tab = (typeof TABS)[number];

const GDP_WINDOWS = [
  { label: "1H", window: "1h", resolution: "1m" },
  { label: "24H", window: "24h", resolution: "5m" },
  { label: "7D", window: "7d", resolution: "1h" },
] as const;

// ---------------------------------------------------------------------------
// GDP Line Chart
// ---------------------------------------------------------------------------

function GDPLineChart({ gdpHistory }: { gdpHistory: GDPHistoryResponse | null }) {
  if (!gdpHistory || gdpHistory.data_points.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center text-[9px] font-mono text-text-faint border border-border border-dashed">
        No GDP data
      </div>
    );
  }

  const points = gdpHistory.data_points;
  const labels = points.map((d) => {
    const dt = new Date(d.timestamp);
    if (gdpHistory.resolution === "1m" || gdpHistory.resolution === "5m") {
      return dt.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" });
    }
    return dt.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  });

  const green = cssVar("--color-green", "#1a7a1a");
  const greenLight = cssVar("--color-green-light", "#e6f4e6");
  const textMuted = cssVar("--color-text-muted", "#888888");
  const border = cssVar("--color-border", "#cccccc");

  const chartData = {
    labels,
    datasets: [
      {
        data: points.map((d) => d.gdp),
        borderColor: green,
        backgroundColor: greenLight,
        borderWidth: 2,
        pointRadius: 0,
        pointHitRadius: 20,
        pointBackgroundColor: green,
        pointHoverRadius: 4,
        fill: true,
        tension: 0.3,
      },
    ],
  };

  const options = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      tooltip: {
        intersect: false,
        mode: "index" as const,
        backgroundColor: tooltipBg,
        titleFont: { family: "'Courier New', monospace", size: 10 },
        bodyFont: { family: "'Courier New', monospace", size: 11 },
        padding: 8,
        displayColors: false,
        callbacks: {
          title: (items: { dataIndex: number }[]) => {
            const i = items[0].dataIndex;
            return new Date(points[i].timestamp).toLocaleDateString("en-US", {
              month: "short",
              day: "numeric",
              hour: "2-digit",
              minute: "2-digit",
            });
          },
          label: (item: { parsed: { y: number } }) =>
            `${item.parsed.y.toLocaleString()} \u00a9 GDP`,
        },
      },
    },
    scales: {
      x: {
        display: true,
        ticks: {
          font: { family: "'Courier New', monospace", size: 8 },
          color: textMuted,
          maxRotation: 0,
          maxTicksLimit: 6,
        },
        grid: { display: false },
        border: { color: border },
      },
      y: {
        display: true,
        ticks: {
          font: { family: "'Courier New', monospace", size: 8 },
          color: textMuted,
          maxTicksLimit: 5,
          callback: (val: number | string) => {
            const v = Number(val);
            return v >= 1000 ? `${(v / 1000).toFixed(1)}k` : v;
          },
        },
        grid: { color: border, lineWidth: 0.5 },
        border: { display: false },
        beginAtZero: true,
      },
    },
  } as const;

  return (
    <div className="flex-1 min-h-0">
      <Line data={chartData} options={options as Parameters<typeof Line>[0]["options"]} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Economy Health Radar
// ---------------------------------------------------------------------------

function HealthRadar({ metrics }: { metrics: MetricsResponse }) {
  const green = cssVar("--color-green", "#1a7a1a");
  const greenLight = cssVar("--color-green-light", "#e6f4e6");
  const textMuted = cssVar("--color-text-muted", "#888888");
  const border = cssVar("--color-border", "#cccccc");

  const completionRate = metrics.tasks.completion_rate * 100;
  const avgBids = Math.min(metrics.labor_market.avg_bids_per_task / 10, 1) * 100;
  const gdpRate = Math.min(metrics.gdp.rate_per_hour / 100, 1) * 100;
  const postingRate = Math.min(metrics.labor_market.task_posting_rate / 20, 1) * 100;
  const employmentRate = (1 - metrics.labor_market.unemployment_rate) * 100;

  const chartData = {
    labels: ["Completion %", "Bid Activity", "GDP Rate", "Posting Rate", "Employment"],
    datasets: [
      {
        data: [completionRate, avgBids, gdpRate, postingRate, employmentRate],
        borderColor: green,
        backgroundColor: greenLight + "88",
        borderWidth: 2,
        pointRadius: 3,
        pointBackgroundColor: green,
        pointHoverRadius: 5,
      },
    ],
  };

  const options = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      tooltip: {
        intersect: false,
        backgroundColor: tooltipBg,
        titleFont: { family: "'Courier New', monospace", size: 10 },
        bodyFont: { family: "'Courier New', monospace", size: 11 },
        padding: 8,
        displayColors: false,
        callbacks: {
          label: (item: { parsed: { r: number }; label: string }) =>
            `${item.label}: ${item.parsed.r.toFixed(1)}`,
        },
      },
    },
    scales: {
      r: {
        beginAtZero: true,
        max: 100,
        ticks: {
          stepSize: 25,
          font: { family: "'Courier New', monospace", size: 7 },
          color: textMuted,
          backdropColor: "transparent",
        },
        pointLabels: {
          font: { family: "'Courier New', monospace", size: 8 },
          color: textMuted,
        },
        grid: { color: border, lineWidth: 0.5 },
        angleLines: { color: border, lineWidth: 0.5 },
      },
    },
  } as const;

  return (
    <div className="flex-1 min-h-0 flex items-center justify-center">
      <div className="w-full h-full max-w-[300px]">
        <Radar data={chartData} options={options as Parameters<typeof Radar>[0]["options"]} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Task Flow Doughnut
// ---------------------------------------------------------------------------

function TaskDoughnut({ metrics }: { metrics: MetricsResponse }) {
  const tasks = metrics.tasks;
  const total = tasks.open + tasks.in_execution + tasks.completed_all_time + tasks.disputed;

  const amber = cssVar("--color-amber", "#b8860b");
  const textColor = cssVar("--color-text", "#333333");
  const green = cssVar("--color-green", "#1a7a1a");
  const red = cssVar("--color-red", "#cc0000");
  const border = cssVar("--color-border", "#cccccc");

  const chartData = {
    labels: ["Open", "In Execution", "Completed", "Disputed"],
    datasets: [
      {
        data: [tasks.open, tasks.in_execution, tasks.completed_all_time, tasks.disputed],
        backgroundColor: [amber + "cc", textColor + "99", green + "cc", red + "cc"],
        borderColor: [amber, textColor, green, red],
        borderWidth: 1.5,
      },
    ],
  };

  const options = {
    responsive: true,
    maintainAspectRatio: false,
    cutout: "60%",
    plugins: {
      tooltip: {
        intersect: false,
        backgroundColor: tooltipBg,
        titleFont: { family: "'Courier New', monospace", size: 10 },
        bodyFont: { family: "'Courier New', monospace", size: 11 },
        padding: 8,
        displayColors: true,
        callbacks: {
          label: (item: { label: string; parsed: number }) => {
            const pct = total > 0 ? ((item.parsed / total) * 100).toFixed(1) : "0";
            return ` ${item.label}: ${item.parsed} (${pct}%)`;
          },
        },
      },
    },
  } as const;

  const textMuted = cssVar("--color-text-muted", "#888888");

  return (
    <div className="flex-1 min-h-0 flex flex-col items-center justify-center relative">
      <div className="w-full h-full max-w-[260px] relative">
        <Doughnut data={chartData} options={options as Parameters<typeof Doughnut>[0]["options"]} />
        <div
          className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none"
        >
          <span className="text-[18px] font-bold font-mono" style={{ color: textColor }}>
            {total}
          </span>
          <span className="text-[8px] font-mono uppercase tracking-[1px]" style={{ color: textMuted }}>
            tasks
          </span>
        </div>
      </div>
      <div className="flex gap-3 mt-2">
        {[
          { label: "Open", color: amber },
          { label: "Executing", color: textColor },
          { label: "Completed", color: green },
          { label: "Disputed", color: red },
        ].map((item) => (
          <div key={item.label} className="flex items-center gap-1">
            <div
              className="w-2 h-2 border"
              style={{ backgroundColor: item.color + "cc", borderColor: item.color }}
            />
            <span className="text-[7px] font-mono uppercase tracking-[0.5px]" style={{ color: border }}>
              {item.label}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main ChartPanel
// ---------------------------------------------------------------------------

interface ChartPanelProps {
  metrics: MetricsResponse | null;
  gdpHistory: GDPHistoryResponse | null;
}

export default function ChartPanel({ metrics, gdpHistory }: ChartPanelProps) {
  const [activeTab, setActiveTab] = useState<Tab>("GDP");
  const [gdpWindow, setGdpWindow] = useState<(typeof GDP_WINDOWS)[number]>(GDP_WINDOWS[2]);
  const [localGdpHistory, setLocalGdpHistory] = useState<GDPHistoryResponse | null>(gdpHistory);

  // When the parent gdpHistory changes (initial load), sync it
  useEffect(() => {
    if (gdpHistory && gdpWindow.window === "7d") {
      setLocalGdpHistory(gdpHistory);
    }
  }, [gdpHistory, gdpWindow.window]);

  // Fetch GDP data when window changes
  useEffect(() => {
    let active = true;
    fetchGDPHistory(gdpWindow.window, gdpWindow.resolution)
      .then((data) => {
        if (active) setLocalGdpHistory(data);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [gdpWindow]);

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Tab bar */}
      <div className="px-3 py-2 border-b border-border bg-bg-off shrink-0 flex items-center gap-2">
        <span className="text-[9px] font-mono uppercase tracking-[1.5px] text-text-muted">
          Charts
        </span>
        <div className="flex gap-1">
          {TABS.map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`text-[8px] font-mono tracking-[0.5px] px-2 py-0.5 border cursor-pointer transition-colors ${
                activeTab === tab
                  ? "border-border-strong bg-border-strong text-bg"
                  : "border-border bg-bg text-text-mid hover:bg-bg-off"
              }`}
            >
              {tab}
            </button>
          ))}
        </div>

        {/* GDP window sub-badges */}
        {activeTab === "GDP" && (
          <>
            <div className="w-px h-3 bg-border mx-1" />
            <div className="flex gap-1">
              {GDP_WINDOWS.map((w) => (
                <button
                  key={w.label}
                  onClick={() => setGdpWindow(w)}
                  className={`text-[7px] font-mono tracking-[0.5px] px-1.5 py-0.5 border cursor-pointer transition-colors ${
                    gdpWindow.label === w.label
                      ? "border-border bg-bg-dark text-text-mid"
                      : "border-border bg-bg text-text-faint hover:text-text-mid"
                  }`}
                >
                  {w.label}
                </button>
              ))}
            </div>
          </>
        )}
      </div>

      {/* Chart area */}
      <div className="flex-1 min-h-0 p-3 flex flex-col">
        {activeTab === "GDP" && <GDPLineChart gdpHistory={localGdpHistory} />}
        {activeTab === "HEALTH" && metrics && <HealthRadar metrics={metrics} />}
        {activeTab === "HEALTH" && !metrics && (
          <div className="flex-1 flex items-center justify-center text-[9px] font-mono text-text-faint">
            Loading metrics...
          </div>
        )}
        {activeTab === "TASKS" && metrics && <TaskDoughnut metrics={metrics} />}
        {activeTab === "TASKS" && !metrics && (
          <div className="flex-1 flex items-center justify-center text-[9px] font-mono text-text-faint">
            Loading metrics...
          </div>
        )}
      </div>
    </div>
  );
}
