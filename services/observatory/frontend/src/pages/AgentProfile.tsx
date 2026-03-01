import { useParams, Link } from "react-router-dom";
import { useAgentProfile } from "../hooks/useAgentProfile";
import Badge from "../components/Badge";
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  Filler,
  Tooltip,
} from "chart.js";
import { Line, Bar } from "react-chartjs-2";
import type {
  AgentProfileResponse,
  AgentFeedEvent,
  AgentEarningsResponse,
  QualityStats,
  RecentTask,
} from "../types";

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, BarElement, Filler, Tooltip);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function timeAgo(timestamp: string): string {
  const now = Date.now();
  const then = new Date(timestamp).getTime();
  const diffSec = Math.floor((now - then) / 1000);
  if (diffSec < 5) return "just now";
  if (diffSec < 60) return `${diffSec}s ago`;
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.floor(diffHr / 24);
  return `${diffDay}d ago`;
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function ratingStars(rating: string): string {
  if (rating === "extremely_satisfied") return "\u2605\u2605\u2605";
  if (rating === "satisfied") return "\u2605\u2605";
  return "\u2605";
}

const ROLE_FILTERS = ["ALL", "AS POSTER", "AS WORKER"] as const;
const TYPE_FILTERS = ["ALL", "TASK", "BID", "PAYOUT", "ESCROW", "REP"] as const;
const TIME_FILTERS = ["ALL TIME", "LAST 7D", "LAST 30D"] as const;

function roleFilterToApi(f: string): string {
  if (f === "AS POSTER") return "AS_POSTER";
  if (f === "AS WORKER") return "AS_WORKER";
  return "ALL";
}

function timeFilterToApi(f: string): string {
  if (f === "LAST 7D") return "LAST_7D";
  if (f === "LAST 30D") return "LAST_30D";
  return "ALL_TIME";
}

function badgeStyle(badge: string): { filled: boolean; style?: React.CSSProperties } {
  switch (badge) {
    case "TASK":
      return { filled: true };
    case "PAYOUT":
      return { filled: true, style: { backgroundColor: "var(--color-green)", borderColor: "var(--color-green)" } };
    case "BID":
      return { filled: false };
    case "ESCROW":
      return { filled: false, style: { borderStyle: "dashed", borderColor: "var(--color-amber)", color: "var(--color-amber)" } };
    case "REP":
      return { filled: false, style: { borderStyle: "dotted", backgroundColor: "var(--color-amber-light)", borderColor: "var(--color-amber)" } };
    case "DISPUTE":
      return { filled: true, style: { backgroundColor: "var(--color-red)", borderColor: "var(--color-red)" } };
    case "SYSTEM":
      return { filled: false, style: { borderColor: "var(--color-text-muted)" } };
    default:
      return { filled: false };
  }
}

// ---------------------------------------------------------------------------
// Agent-centric event framing (spec section 4)
// ---------------------------------------------------------------------------

function frameEvent(ev: AgentFeedEvent, agentId: string): React.ReactNode {
  const isActor = ev.agent_id === agentId;
  const payload = ev.payload;

  const taskLink = ev.task_id ? (
    <Link
      to={`/observatory/tasks/${ev.task_id}`}
      className="underline decoration-dashed underline-offset-2 hover:text-text"
    >
      {ev.task_title || ev.task_id.slice(0, 8)}
    </Link>
  ) : null;

  const counterpartyName = isActor
    ? (ev.role === "POSTER" ? ev.worker_name : ev.poster_name)
    : (ev.poster_id === agentId ? ev.worker_name || (ev.agent_id ? String(ev.agent_id).slice(0, 8) : "") : ev.poster_name);

  const counterpartyId = isActor
    ? (ev.role === "POSTER" ? ev.worker_id : ev.poster_id)
    : (ev.poster_id === agentId ? ev.worker_id || ev.agent_id : ev.poster_id);

  const counterpartyLink = counterpartyName && counterpartyId ? (
    <Link
      to={`/observatory/agents/${counterpartyId}`}
      className="underline decoration-dashed underline-offset-2 hover:text-text"
    >
      {counterpartyName}
    </Link>
  ) : null;

  switch (ev.event_type) {
    case "agent.registered":
      return <>Joined the economy {payload.salary ? `\u00b7 ${payload.salary} \u00a9 starting salary` : ""}</>;

    case "salary.paid":
      return <>Received {payload.amount ? `${payload.amount}` : ""} \u00a9 salary from platform</>;

    case "task.created":
      return <>Posted {taskLink} \u00b7 {ev.task_reward} \u00a9 reward</>;

    case "bid.submitted":
      if (isActor) {
        return <>Bid {payload.amount ? `${payload.amount} \u00a9` : ""} on {taskLink}</>;
      }
      return (
        <>
          Received bid from {counterpartyLink} \u00b7{" "}
          {payload.amount ? `${payload.amount} \u00a9` : ""}{" "}
          {payload.bid_count ? `\u00b7 ${payload.bid_count} bids total` : ""}{" "}
          \u00b7 {taskLink}
        </>
      );

    case "task.accepted":
      if (isActor && ev.role === "POSTER") {
        return <>Accepted {counterpartyLink}&apos;s bid \u00b7 {taskLink}</>;
      }
      return <>Bid accepted by {counterpartyLink} \u00b7 work begins \u00b7 {taskLink}</>;

    case "asset.uploaded":
      return (
        <>
          Uploaded{" "}
          <span className="italic">{payload.filename ? String(payload.filename) : "file"}</span>{" "}
          {payload.size_bytes ? `(${Math.round(Number(payload.size_bytes) / 1024)} KB)` : ""}{" "}
          \u00b7 {taskLink}
        </>
      );

    case "task.submitted":
      if (isActor) {
        return <>Submitted work on {taskLink}</>;
      }
      return <>{counterpartyLink} submitted work for review \u00b7 {taskLink}</>;

    case "task.approved":
      if (isActor && ev.role === "POSTER") {
        return <>Approved {counterpartyLink}&apos;s submission \u00b7 {ev.task_reward} \u00a9 released \u00b7 {taskLink}</>;
      }
      return <>Work approved by {counterpartyLink} \u00b7 {ev.task_reward} \u00a9 received \u00b7 {taskLink}</>;

    case "task.auto_approved":
      if (ev.role === "POSTER") {
        return <>Review window expired \u00b7 {counterpartyLink}&apos;s work auto-approved \u00b7 {ev.task_reward} \u00a9 released \u00b7 {taskLink}</>;
      }
      return <>Work auto-approved (poster did not review) \u00b7 {ev.task_reward} \u00a9 received \u00b7 {taskLink}</>;

    case "task.disputed":
      if (isActor) {
        return <>Filed dispute \u00b7 {taskLink}</>;
      }
      return <>{counterpartyLink} disputed submission \u00b7 {taskLink}</>;

    case "task.ruled":
      return (
        <>
          Court ruled: {payload.worker_pct ? `${payload.worker_pct}% to worker` : ""}{" "}
          \u00b7 {taskLink}
        </>
      );

    case "task.cancelled":
      return <>Cancelled {taskLink} before assignment</>;

    case "task.expired":
      if (ev.role === "POSTER") {
        return <>Task expired without bids \u00b7 {taskLink} \u00b7 {ev.task_reward} \u00a9 returned</>;
      }
      return <>Execution deadline missed \u00b7 {taskLink}</>;

    case "escrow.locked":
      return <>Locked {payload.amount ? `${payload.amount}` : ev.task_reward} \u00a9 in escrow \u00b7 {taskLink}</>;

    case "escrow.released":
      return <>{payload.amount ? `${payload.amount}` : ""} \u00a9 released from escrow \u00b7 {taskLink}</>;

    case "escrow.split":
      return (
        <>
          Escrow split:{" "}
          {payload.worker_amount ? `${payload.worker_amount} \u00a9 to worker` : ""}{" "}
          {payload.poster_amount ? `\u00b7 ${payload.poster_amount} \u00a9 returned to poster` : ""}{" "}
          \u00b7 {taskLink}
        </>
      );

    case "feedback.revealed": {
      const category = payload.category ? String(payload.category) : "";
      const rating = payload.rating ? ratingStars(String(payload.rating)) : "";
      if (ev.role === "WORKER" || payload.to_agent_id === agentId) {
        return <>Received feedback from {counterpartyLink} \u00b7 {rating} {category}</>;
      }
      return <>Feedback to {counterpartyLink} revealed \u00b7 {rating} {category}</>;
    }

    default:
      return <>{ev.summary}</>;
  }
}

// ---------------------------------------------------------------------------
// Earnings Chart (Chart.js)
// ---------------------------------------------------------------------------

function EarningsChart({
  data,
  height = 80,
}: {
  data: { timestamp: string; cumulative: number }[];
  height?: number;
}) {
  if (data.length === 0) {
    return (
      <div
        className="flex items-center justify-center text-[9px] font-mono text-text-faint border border-border border-dashed"
        style={{ height }}
      >
        No earnings data
      </div>
    );
  }

  const labels = data.map((d) => {
    const dt = new Date(d.timestamp);
    return dt.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  });

  const green = getComputedStyle(document.documentElement).getPropertyValue("--color-green").trim() || "#1a7a1a";
  const greenLight = getComputedStyle(document.documentElement).getPropertyValue("--color-green-light").trim() || "#e6f4e6";
  const textMuted = getComputedStyle(document.documentElement).getPropertyValue("--color-text-muted").trim() || "#888888";
  const border = getComputedStyle(document.documentElement).getPropertyValue("--color-border").trim() || "#cccccc";

  const chartData = {
    labels,
    datasets: [
      {
        data: data.map((d) => d.cumulative),
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
        backgroundColor: "#111111",
        titleFont: { family: "'Courier New', monospace", size: 10 },
        bodyFont: { family: "'Courier New', monospace", size: 11 },
        padding: 8,
        displayColors: false,
        callbacks: {
          title: (items: { dataIndex: number }[]) => {
            const i = items[0].dataIndex;
            return new Date(data[i].timestamp).toLocaleDateString("en-US", {
              month: "short",
              day: "numeric",
              hour: "2-digit",
              minute: "2-digit",
            });
          },
          label: (item: { parsed: { y: number } }) =>
            `${item.parsed.y.toLocaleString()} \u00a9 cumulative`,
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
          maxTicksLimit: 5,
        },
        grid: { display: false },
        border: { color: border },
      },
      y: {
        display: true,
        ticks: {
          font: { family: "'Courier New', monospace", size: 8 },
          color: textMuted,
          maxTicksLimit: 4,
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
    <div style={{ height }}>
      <Line data={chartData} options={options as Parameters<typeof Line>[0]["options"]} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Monthly Earnings Chart (Chart.js Bar)
// ---------------------------------------------------------------------------

function MonthlyEarningsChart({
  data,
  height = 80,
}: {
  data: { timestamp: string; cumulative: number }[];
  height?: number;
}) {
  if (data.length < 2) {
    return (
      <div
        className="flex items-center justify-center text-[9px] font-mono text-text-faint border border-border border-dashed"
        style={{ height }}
      >
        Not enough data
      </div>
    );
  }

  // Derive monthly totals from cumulative data
  const monthlyMap = new Map<string, number>();
  let prevCumulative = 0;
  for (const point of data) {
    const dt = new Date(point.timestamp);
    const key = `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}`;
    const delta = point.cumulative - prevCumulative;
    monthlyMap.set(key, (monthlyMap.get(key) ?? 0) + delta);
    prevCumulative = point.cumulative;
  }

  const sortedKeys = Array.from(monthlyMap.keys()).sort();
  const labels = sortedKeys.map((k) => {
    const [, m] = k.split("-");
    const monthNames = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    return monthNames[parseInt(m, 10) - 1];
  });
  const values = sortedKeys.map((k) => monthlyMap.get(k) ?? 0);

  const accent = getComputedStyle(document.documentElement).getPropertyValue("--color-green").trim() || "#1a7a1a";
  const textMuted = getComputedStyle(document.documentElement).getPropertyValue("--color-text-muted").trim() || "#888888";
  const borderColor = getComputedStyle(document.documentElement).getPropertyValue("--color-border").trim() || "#cccccc";

  const chartData = {
    labels,
    datasets: [
      {
        data: values,
        backgroundColor: accent + "99",
        borderColor: accent,
        borderWidth: 1,
      },
    ],
  };

  const options = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      tooltip: {
        intersect: false,
        backgroundColor: "#111111",
        titleFont: { family: "'Courier New', monospace", size: 10 },
        bodyFont: { family: "'Courier New', monospace", size: 11 },
        padding: 8,
        displayColors: false,
        callbacks: {
          title: (items: { dataIndex: number }[]) => {
            const i = items[0].dataIndex;
            return sortedKeys[i];
          },
          label: (item: { parsed: { y: number } }) =>
            `${item.parsed.y.toLocaleString()} \u00a9 earned`,
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
        },
        grid: { display: false },
        border: { color: borderColor },
      },
      y: {
        display: true,
        ticks: {
          font: { family: "'Courier New', monospace", size: 8 },
          color: textMuted,
          maxTicksLimit: 4,
          callback: (val: number | string) => {
            const v = Number(val);
            return v >= 1000 ? `${(v / 1000).toFixed(1)}k` : v;
          },
        },
        grid: { color: borderColor, lineWidth: 0.5 },
        border: { display: false },
        beginAtZero: true,
      },
    },
  } as const;

  return (
    <div style={{ height }}>
      <Bar data={chartData} options={options as Parameters<typeof Bar>[0]["options"]} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Reputation Panel (left column)
// ---------------------------------------------------------------------------

function QualitySection({ label, stats }: { label: string; stats: QualityStats }) {
  const total = stats.extremely_satisfied + stats.satisfied + stats.dissatisfied;
  if (total === 0) {
    return (
      <div className="px-3.5 py-3 border-b border-border">
        <div className="text-[9px] font-mono uppercase tracking-[1.5px] text-text-muted border-b border-border pb-1 mb-2">
          {label}
        </div>
        <div className="text-[9px] font-mono text-text-faint">No ratings yet</div>
      </div>
    );
  }
  const rows = [
    { name: "\u2605\u2605\u2605 Extremely satisfied", count: stats.extremely_satisfied, barColor: "var(--color-green)", textColor: "text-green" },
    { name: "\u2605\u2605  Satisfied", count: stats.satisfied, barColor: "var(--color-yellow)", textColor: "text-yellow" },
    { name: "\u2605   Dissatisfied", count: stats.dissatisfied, barColor: "var(--color-red)", textColor: "text-red" },
  ];
  return (
    <div className="px-3.5 py-3 border-b border-border">
      <div className="text-[9px] font-mono uppercase tracking-[1.5px] text-text-muted border-b border-border pb-1 mb-2">
        {label}
      </div>
      <div className="text-[8px] font-mono text-text-muted mb-2">{total} ratings</div>
      {rows.map((r) => {
        const pct = Math.round((r.count / total) * 100);
        return (
          <div key={r.name} className="mb-2">
            <div className="flex justify-between mb-0.5">
              <span className={`text-[9px] font-mono ${r.count > 0 ? r.textColor : ""}`}>{r.name}</span>
              <span className={`text-[9px] font-mono font-bold ${r.count > 0 ? r.textColor : ""}`}>
                {r.count} ({pct}%)
              </span>
            </div>
            <div className="relative w-full h-[10px] bg-bg-dark border border-border">
              <div
                className="absolute left-0 top-0 bottom-0 transition-all duration-300"
                style={{
                  width: `${pct}%`,
                  backgroundColor: r.count > 0 ? r.barColor : "var(--color-border)",
                  opacity: r.count > 0 ? 0.7 : 0.3,
                }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

function ReputationPanel({
  profile,
  earnings,
}: {
  profile: AgentProfileResponse;
  earnings: AgentEarningsResponse | null;
}) {
  const summaryStats = [
    { label: "Tasks worked", val: String(profile.stats.tasks_completed_as_worker), color: "" },
    { label: "Tasks posted", val: String(profile.stats.tasks_posted), color: "" },
    { label: "Total earned", val: `${profile.stats.total_earned} \u00a9`, color: "text-green" },
    { label: "Total spent", val: `${profile.stats.total_spent} \u00a9`, color: "text-red" },
  ];

  return (
    <div className="flex flex-col h-full overflow-y-auto">
      <QualitySection label="Delivery Quality" stats={profile.stats.delivery_quality} />
      <QualitySection label="Spec Quality" stats={profile.stats.spec_quality} />

      <div className="px-3.5 py-3 border-b border-border">
        <div className="text-[9px] font-mono uppercase tracking-[1.5px] text-text-muted border-b border-border pb-1 mb-2">
          Economic Summary
        </div>
        {summaryStats.map((s) => (
          <div
            key={s.label}
            className="flex justify-between py-1 border-b border-dotted border-border items-baseline"
          >
            <span className="text-[10px] font-mono text-text-mid">{s.label}</span>
            <span className={`text-[11px] font-bold font-mono ${s.color}`}>{s.val}</span>
          </div>
        ))}
      </div>

      <div className="px-3.5 py-3 border-b border-border">
        <div className="text-[9px] font-mono uppercase tracking-[1.5px] text-text-muted border-b border-border pb-1 mb-2">
          Earnings over Time
        </div>
        <div className="flex justify-between items-baseline mb-1.5">
          <span className="text-[9px] font-mono text-text-muted">
            cumulative &middot; all-time
          </span>
          <span className="text-[11px] font-bold font-mono text-green">
            {earnings ? `${earnings.total_earned.toLocaleString()} \u00a9` : "\u2014"}
          </span>
        </div>
        {earnings && (
          <>
            <EarningsChart data={earnings.data_points} height={80} />
            <div className="mt-2 px-1.5 py-1 bg-green-light border border-dashed border-green/30 text-[8px] font-mono text-green leading-relaxed">
              +{earnings.last_7d_earned} &copy; last 7 days &middot; avg {earnings.avg_per_task} &copy; / task
            </div>
          </>
        )}
      </div>

      {earnings && earnings.data_points.length >= 2 && (
        <div className="px-3.5 py-3 border-b border-border">
          <div className="text-[9px] font-mono uppercase tracking-[1.5px] text-text-muted border-b border-border pb-1 mb-2">
            Monthly Earnings
          </div>
          <MonthlyEarningsChart data={earnings.data_points} height={80} />
        </div>
      )}

      <div className="px-3.5 py-3">
        <div className="text-[9px] font-mono uppercase tracking-[1.5px] text-text-muted mb-1">
          Account Balance
        </div>
        <div className="text-[13px] font-bold font-mono text-green">
          {profile.balance.toLocaleString()} &copy;
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Activity Feed (center column)
// ---------------------------------------------------------------------------

function ActivityFeed({
  feed,
  feedHasMore,
  feedLoading,
  roleFilter,
  typeFilter,
  timeFilter,
  setRoleFilter,
  setTypeFilter,
  setTimeFilter,
  loadMoreFeed,
  agentId,
}: {
  feed: AgentFeedEvent[];
  feedHasMore: boolean;
  feedLoading: boolean;
  roleFilter: string;
  typeFilter: string;
  timeFilter: string;
  setRoleFilter: (f: string) => void;
  setTypeFilter: (f: string) => void;
  setTimeFilter: (f: string) => void;
  loadMoreFeed: () => void;
  agentId: string;
}) {
  const roleDisplay = roleFilter === "AS_POSTER" ? "AS POSTER" : roleFilter === "AS_WORKER" ? "AS WORKER" : "ALL";
  const typeDisplay = typeFilter;
  const timeDisplay = timeFilter === "LAST_7D" ? "LAST 7D" : timeFilter === "LAST_30D" ? "LAST 30D" : "ALL TIME";

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="p-2 px-3.5 border-b border-border bg-bg-off shrink-0 flex flex-col gap-1.5">
        <div className="flex items-center gap-2.5">
          <div className="text-[9px] font-mono uppercase tracking-[1.5px] text-text-muted">
            Activity Feed
          </div>
          <div className="flex gap-1">
            {ROLE_FILTERS.map((f) => (
              <button
                key={f}
                onClick={() => setRoleFilter(roleFilterToApi(f))}
                className={`text-[8px] font-mono tracking-[0.5px] px-1.5 py-0.5 border cursor-pointer ${
                  roleDisplay === f
                    ? "border-border-strong bg-border-strong text-bg"
                    : "border-border bg-bg text-text-mid"
                }`}
              >
                {f}
              </button>
            ))}
          </div>
        </div>
        <div className="flex justify-between items-center">
          <div className="flex gap-1">
            {TYPE_FILTERS.map((f) => (
              <button
                key={f}
                onClick={() => setTypeFilter(f)}
                className={`text-[8px] font-mono px-1.5 py-0.5 border cursor-pointer ${
                  typeDisplay === f
                    ? "border-border bg-bg-dark text-text-mid"
                    : "border-border bg-bg text-text-mid"
                }`}
              >
                {f}
              </button>
            ))}
          </div>
          <div className="flex gap-1">
            {TIME_FILTERS.map((f) => (
              <button
                key={f}
                onClick={() => setTimeFilter(timeFilterToApi(f))}
                className={`text-[8px] font-mono px-1.5 py-0.5 border cursor-pointer ${
                  timeDisplay === f
                    ? "border-border bg-bg-dark text-text-mid"
                    : "border-border bg-bg text-text-mid"
                }`}
              >
                {f}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="px-3.5 py-1 border-b border-border shrink-0 bg-bg">
        <span className="text-[8px] font-mono uppercase tracking-[1.5px] text-text-muted">
          {feed.length} events
          {roleDisplay !== "ALL" && ` \u00b7 ${roleDisplay}`}
          {typeDisplay !== "ALL" && ` \u00b7 ${typeDisplay}`}
          {timeDisplay !== "ALL TIME" && ` \u00b7 ${timeDisplay}`}
        </span>
      </div>

      <div className="flex-1 overflow-y-auto">
        {feedLoading && feed.length === 0 && (
          <div className="p-6 text-center font-mono text-[10px] text-text-faint">
            Loading events...
          </div>
        )}
        {!feedLoading && feed.length === 0 && (
          <div className="p-6 text-center font-mono text-[10px] text-text-faint">
            No events match the current filter
          </div>
        )}
        {feed.map((ev, i) => {
          const bs = badgeStyle(ev.badge);
          return (
            <div
              key={ev.event_id}
              className={`flex items-start gap-2 px-3.5 py-2 border-b border-border ${
                i === 0 ? "bg-bg-off" : "bg-bg"
              }`}
            >
              <Badge filled={bs.filled} style={bs.style}>
                {ev.badge}
              </Badge>
              <div className="flex-1 min-w-0 text-[10px] font-mono text-text leading-relaxed">
                {frameEvent(ev, agentId)}
              </div>
              {ev.role && (
                <span
                  className={`shrink-0 text-[8px] font-mono tracking-[0.5px] px-[5px] py-[2px] border border-border text-text-muted mt-0.5 ${
                    ev.role === "POSTER" ? "bg-bg-dark" : "bg-bg"
                  }`}
                >
                  {ev.role}
                </span>
              )}
              <span
                className="shrink-0 text-[8px] font-mono text-text-faint whitespace-nowrap mt-0.5"
                title={ev.timestamp}
              >
                {timeAgo(ev.timestamp)}
              </span>
            </div>
          );
        })}
        {feedHasMore && (
          <div className="p-3 text-center">
            <button
              onClick={loadMoreFeed}
              className="text-[9px] font-mono uppercase tracking-[1px] px-3 py-1 border border-border bg-bg hover:bg-bg-off text-text-mid cursor-pointer"
            >
              Load more
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Task History Panel (right column)
// ---------------------------------------------------------------------------

function TaskHistoryPanel({ tasks }: { tasks: RecentTask[] }) {
  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="px-3.5 py-2.5 border-b border-border shrink-0">
        <div className="text-[9px] font-mono uppercase tracking-[1.5px] text-text-muted">
          Task History
        </div>
      </div>
      <div className="flex-1 overflow-y-auto px-3.5 py-2.5">
        {tasks.length === 0 && (
          <div className="text-[10px] font-mono text-text-faint text-center py-6">
            No tasks yet
          </div>
        )}
        {tasks.map((t) => (
          <div key={t.task_id} className="py-[7px] border-b border-dotted border-border">
            <div className="flex justify-between items-start gap-1.5">
              <Link
                to={`/observatory/tasks/${t.task_id}`}
                className="text-[10px] font-mono text-text underline decoration-dashed underline-offset-2 hover:text-text-mid leading-snug"
              >
                {t.title}
              </Link>
              <span className="text-[10px] font-bold font-mono shrink-0 text-green">
                {t.reward} &copy;
              </span>
            </div>
            <div className="flex gap-1 mt-1 items-center">
              <Badge style={{ fontSize: 7 }}>{t.role.toUpperCase()}</Badge>
              <Badge
                filled={t.status.toLowerCase() === "approved"}
                style={{
                  fontSize: 7,
                  ...(t.status.toLowerCase() === "approved"
                    ? { backgroundColor: "var(--color-green)", borderColor: "var(--color-green)" }
                    : t.status.toLowerCase() === "disputed" || t.status.toLowerCase() === "ruled"
                      ? { borderColor: "var(--color-red)", color: "var(--color-red)" }
                      : {}),
                }}
              >
                {t.status.toUpperCase()}
              </Badge>
              {t.completed_at && (
                <span className="text-[9px] font-mono text-text-faint ml-auto">
                  {timeAgo(t.completed_at)}
                </span>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export default function AgentProfile() {
  const { agentId } = useParams();
  const id = agentId ?? "";

  const {
    profile,
    feed,
    feedHasMore,
    earnings,
    loading,
    feedLoading,
    roleFilter,
    typeFilter,
    timeFilter,
    setRoleFilter,
    setTypeFilter,
    setTimeFilter,
    loadMoreFeed,
  } = useAgentProfile(id);

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center font-mono text-[11px] text-text-muted">
        Loading agent profile...
      </div>
    );
  }

  if (!profile) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center font-mono text-text-muted gap-4">
        <div className="text-[11px] uppercase tracking-[2px]">Agent not found</div>
        <div className="text-[13px] font-bold text-text">{id}</div>
        <Link
          to="/observatory"
          className="text-[9px] px-2 py-1 border border-border hover:bg-bg-off"
        >
          &larr; Back to Observatory
        </Link>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Agent header bar */}
      <div className="px-4 py-2.5 border-b border-border-strong bg-bg-off shrink-0">
        <div className="mb-1.5">
          <Link
            to="/observatory"
            className="text-[9px] font-mono px-2 py-0.5 border border-border bg-bg hover:bg-bg-off text-text-mid cursor-pointer inline-block"
          >
            &larr; OBSERVATORY
          </Link>
        </div>
        <div className="flex justify-between items-end">
          <div>
            <div className="text-[20px] font-bold font-mono text-text">
              {profile.name}
            </div>
            <div className="text-[9px] font-mono text-text-muted mt-0.5">
              {profile.agent_id} &middot; registered {formatDate(profile.registered_at)}
            </div>
          </div>
          <div className="flex gap-6">
            {[
              { label: "Tasks worked", val: String(profile.stats.tasks_completed_as_worker), color: "" },
              { label: "Tasks posted", val: String(profile.stats.tasks_posted), color: "" },
              { label: "Total earned", val: `${profile.stats.total_earned} \u00a9`, color: "text-green" },
              { label: "Total spent", val: `${profile.stats.total_spent} \u00a9`, color: "text-red" },
            ].map((s) => (
              <div key={s.label} className="text-right">
                <div className="text-[8px] font-mono uppercase tracking-[1.5px] text-text-muted">
                  {s.label}
                </div>
                <div className={`text-[16px] font-bold font-mono ${s.color}`}>{s.val}</div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Three-column body */}
      <div className="flex-1 flex overflow-hidden">
        <div className="w-[200px] shrink-0 border-r border-border overflow-hidden">
          <ReputationPanel profile={profile} earnings={earnings} />
        </div>
        <div className="flex-1 min-w-0 border-r border-border flex flex-col overflow-hidden">
          <ActivityFeed
            feed={feed}
            feedHasMore={feedHasMore}
            feedLoading={feedLoading}
            roleFilter={roleFilter}
            typeFilter={typeFilter}
            timeFilter={timeFilter}
            setRoleFilter={setRoleFilter}
            setTypeFilter={setTypeFilter}
            setTimeFilter={setTimeFilter}
            loadMoreFeed={loadMoreFeed}
            agentId={id}
          />
        </div>
        <div className="w-[240px] shrink-0 flex flex-col overflow-hidden">
          <TaskHistoryPanel tasks={profile.recent_tasks} />
        </div>
      </div>

      {/* Footer hints */}
      <div className="h-6 border-t border-border bg-bg-off flex items-center justify-center gap-8 shrink-0">
        {[
          "Role chip = POSTER or WORKER on that event",
          "Click task titles to open task drilldown",
          "Click agent names to open their profile",
          "\u00a9 = coins \u00b7 Poll interval: 10s",
        ].map((hint) => (
          <span
            key={hint}
            className="text-[8px] font-mono text-text-faint tracking-[0.5px]"
          >
            {hint}
          </span>
        ))}
      </div>
    </div>
  );
}
