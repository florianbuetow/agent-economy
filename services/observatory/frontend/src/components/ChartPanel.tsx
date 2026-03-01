import { useState, useEffect } from "react";
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Filler,
  Tooltip,
} from "chart.js";
import { Line } from "react-chartjs-2";
import type { GDPHistoryResponse } from "../types";
import { fetchGDPHistory } from "../api/metrics";
import { cssVar, tooltipBg } from "../utils/colorUtils";

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Filler,
  Tooltip,
);

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
// Main ChartPanel
// ---------------------------------------------------------------------------

interface ChartPanelProps {
  gdpHistory: GDPHistoryResponse | null;
}

export default function ChartPanel({ gdpHistory }: ChartPanelProps) {
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
      {/* Header bar */}
      <div className="px-3 py-2 border-b border-border bg-bg-off shrink-0 flex items-center gap-2">
        <span className="text-[9px] font-mono uppercase tracking-[1.5px] text-text-muted">
          GDP
        </span>
        <div className="flex gap-1">
          {GDP_WINDOWS.map((w) => (
            <button
              key={w.label}
              onClick={() => setGdpWindow(w)}
              className={`text-[8px] font-mono tracking-[0.5px] px-2 py-0.5 border cursor-pointer transition-colors ${
                gdpWindow.label === w.label
                  ? "border-border-strong bg-border-strong text-bg"
                  : "border-border bg-bg text-text-mid hover:bg-bg-off"
              }`}
            >
              {w.label}
            </button>
          ))}
        </div>
      </div>

      {/* Chart area */}
      <div className="flex-1 min-h-0 p-3 flex flex-col">
        <GDPLineChart gdpHistory={localGdpHistory} />
      </div>
    </div>
  );
}
