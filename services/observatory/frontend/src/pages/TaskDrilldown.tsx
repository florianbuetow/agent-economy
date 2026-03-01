import { useState } from "react";
import { useParams, Link } from "react-router-dom";
import { useTaskDrilldown } from "../hooks/useTaskDrilldown";
import Badge from "../components/Badge";
import { colors, statusColors } from "../utils/colorUtils";
import type {
  TaskDrilldownResponse,
  BidItem,
  AssetItem,
  TaskFeedbackDetail,
} from "../types";

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

function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function formatDelta(fromIso: string, toIso: string): string {
  const diffMs = new Date(toIso).getTime() - new Date(fromIso).getTime();
  const totalMin = Math.floor(Math.abs(diffMs) / 60000);
  if (totalMin < 60) return `+${totalMin}m`;
  const hrs = Math.floor(totalMin / 60);
  const mins = totalMin % 60;
  return mins > 0 ? `+${hrs}h ${mins}m` : `+${hrs}h`;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function mimeToLabel(mime: string): string {
  const map: Record<string, string> = {
    "application/zip": "ZIP archive",
    "application/pdf": "PDF document",
    "application/json": "JSON",
    "text/plain": "Plain text",
    "text/html": "HTML",
    "text/css": "CSS",
    "text/csv": "CSV",
    "image/png": "PNG image",
    "image/jpeg": "JPEG image",
  };
  if (map[mime]) return map[mime];
  if (mime.startsWith("text/x-python") || mime.includes("python"))
    return "Python source";
  if (mime.startsWith("text/")) return "Text file";
  if (mime.startsWith("application/")) return mime.replace("application/", "");
  return mime;
}

// ---------------------------------------------------------------------------
// Status badge styling
// ---------------------------------------------------------------------------

function statusBadgeProps(status: string): {
  filled: boolean;
  style?: React.CSSProperties;
} {
  switch (status) {
    case "open":
      return {
        filled: false,
        style: {
          borderColor: "var(--color-green)",
          color: "var(--color-green)",
          backgroundColor: "var(--color-green-light)",
        },
      };
    case "accepted":
      return {
        filled: true,
        style: {
          backgroundColor: statusColors.accepted.bg,
          borderColor: statusColors.accepted.border,
        },
      };
    case "submitted":
      return {
        filled: false,
        style: {
          borderColor: "var(--color-amber)",
          color: "var(--color-amber)",
          backgroundColor: "var(--color-amber-light)",
        },
      };
    case "approved":
      return {
        filled: true,
        style: {
          backgroundColor: "var(--color-green)",
          borderColor: "var(--color-green)",
        },
      };
    case "disputed":
      return {
        filled: true,
        style: {
          backgroundColor: "var(--color-red)",
          borderColor: "var(--color-red)",
        },
      };
    case "ruled":
      return {
        filled: true,
        style: {
          backgroundColor: statusColors.ruled.bg,
          borderColor: statusColors.ruled.border,
        },
      };
    case "cancelled":
    case "expired":
      return {
        filled: false,
        style: {
          borderColor: "var(--color-border)",
          color: "var(--color-text-muted)",
        },
      };
    default:
      return { filled: false };
  }
}

// ---------------------------------------------------------------------------
// Section label (consistent with observatory)
// ---------------------------------------------------------------------------

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[9px] font-mono uppercase tracking-[1.5px] text-text-muted border-b border-border pb-1 mb-3">
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Lifecycle Timeline
// ---------------------------------------------------------------------------

interface TimelineNode {
  label: string;
  timestamp: string | null;
  note?: string;
  done: boolean;
  color?: string; // CSS color for the dot
}

function buildTimeline(task: TaskDrilldownResponse): TimelineNode[] {
  const ts = task.timestamps;
  const nodes: TimelineNode[] = [];

  // POSTED
  nodes.push({
    label: "POSTED",
    timestamp: ts.created_at,
    note: task.poster.name,
    done: true,
    color: "var(--color-green)",
  });

  // BIDDING
  const bidCount = task.bids.length;
  nodes.push({
    label: `BIDDING (${bidCount} bid${bidCount !== 1 ? "s" : ""})`,
    timestamp: task.deadlines.bidding_deadline,
    note:
      bidCount > 0
        ? `${bidCount} agent${bidCount !== 1 ? "s" : ""} competed`
        : "no bids",
    done: true,
    color: "var(--color-amber)",
  });

  // ACCEPTED
  const acceptedBid = task.bids.find((b) => b.accepted);
  if (ts.accepted_at) {
    nodes.push({
      label: "ACCEPTED",
      timestamp: ts.accepted_at,
      note: acceptedBid
        ? `${acceptedBid.bidder.name}`
        : task.worker?.name ?? undefined,
      done: true,
      color: statusColors.accepted.bg,
    });
  } else if (
    task.status !== "open" &&
    task.status !== "cancelled" &&
    task.status !== "expired"
  ) {
    nodes.push({ label: "ACCEPTED", timestamp: null, done: false });
  }

  // SUBMITTED
  if (ts.submitted_at) {
    nodes.push({
      label: "SUBMITTED",
      timestamp: ts.submitted_at,
      note: `${task.assets.length} asset${task.assets.length !== 1 ? "s" : ""}`,
      done: true,
      color: "var(--color-amber)",
    });
  } else if (
    task.status === "accepted" ||
    task.status === "open"
  ) {
    // Show upcoming
    nodes.push({ label: "SUBMITTED", timestamp: null, done: false });
  }

  // Terminal states
  if (ts.approved_at) {
    nodes.push({
      label: "APPROVED",
      timestamp: ts.approved_at,
      note: "poster approved",
      done: true,
      color: "var(--color-green)",
    });
  } else if (task.dispute) {
    // DISPUTED
    nodes.push({
      label: "DISPUTED",
      timestamp: task.dispute.filed_at,
      note: "poster rejected",
      done: true,
      color: "var(--color-red)",
    });

    if (task.dispute.rebuttal) {
      nodes.push({
        label: "REBUTTAL",
        timestamp: task.dispute.rebuttal.submitted_at,
        note: task.worker?.name
          ? `${task.worker.name} responded`
          : "worker responded",
        done: true,
        color: "var(--color-red)",
      });
    }

    if (task.dispute.ruling) {
      const workerPct = task.dispute.ruling.worker_pct;
      nodes.push({
        label: "RULED",
        timestamp: task.dispute.ruling.ruled_at,
        note: `Worker ${workerPct}% - Poster ${100 - workerPct}%`,
        done: true,
        color: statusColors.ruled.bg,
      });
    } else {
      nodes.push({ label: "RULED", timestamp: null, done: false });
    }
  } else if (
    task.status === "submitted" ||
    task.status === "accepted"
  ) {
    // Show upcoming terminal nodes
    nodes.push({ label: "APPROVED / DISPUTED", timestamp: null, done: false });
  }

  return nodes;
}

function LifecycleTimeline({ task }: { task: TaskDrilldownResponse }) {
  const nodes = buildTimeline(task);

  return (
    <div className="px-3.5 py-3 border-b border-border">
      <SectionLabel>Lifecycle Timeline</SectionLabel>
      <div className="pl-1">
        {nodes.map((node, i) => (
          <div key={i} className="flex gap-0">
            <div className="flex flex-col items-center mr-2.5">
              <div
                className="w-2 h-2 rounded-full mt-0.5 shrink-0 border"
                style={
                  node.done
                    ? {
                        backgroundColor: node.color ?? "var(--color-border-strong)",
                        borderColor: node.color ?? "var(--color-border-strong)",
                      }
                    : {
                        backgroundColor: "var(--color-bg-dark)",
                        borderColor: "var(--color-border)",
                      }
                }
              />
              {i < nodes.length - 1 && (
                <div
                  className={`w-px flex-1 min-h-[18px] ${
                    node.done ? "bg-border-strong" : "bg-border"
                  }`}
                />
              )}
            </div>
            <div className="pb-3">
              <div className="flex gap-2.5 items-baseline">
                <span
                  className="text-[10px] font-mono font-bold"
                  style={{
                    color: node.done
                      ? node.color ?? "var(--color-text)"
                      : "var(--color-text-faint)",
                  }}
                >
                  {node.label}
                </span>
                {node.timestamp && i > 0 && nodes[i - 1].timestamp && (
                  <span className="text-[8px] font-mono uppercase tracking-[1.5px] text-text-faint">
                    {formatDelta(nodes[i - 1].timestamp!, node.timestamp)}
                  </span>
                )}
              </div>
              <div className="flex gap-2.5 mt-0.5">
                <span className="text-[9px] font-mono text-text-muted">
                  {node.timestamp ? formatTimestamp(node.timestamp) : "pending"}
                </span>
                {node.note && (
                  <span className="text-[9px] font-mono text-text-muted">
                    - {node.note}
                  </span>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Bid Panel
// ---------------------------------------------------------------------------

function BidPanel({ bids }: { bids: BidItem[] }) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const acceptedBid = bids.find((b) => b.accepted);

  return (
    <div className="px-3.5 py-3 border-b border-border">
      <SectionLabel>Bid Panel</SectionLabel>

      <div className="text-[10px] font-mono text-text-muted mb-2.5">
        {bids.length === 0 ? (
          "0 bids received during the bidding window"
        ) : (
          <>
            {bids.length} agent{bids.length !== 1 ? "s" : ""} competed
            {acceptedBid && (
              <>
                {" "}
                -{" "}
                <Link
                  to={`/observatory/agents/${acceptedBid.bidder.agent_id}`}
                  className="underline decoration-dashed underline-offset-2 hover:text-text"
                >
                  {acceptedBid.bidder.name}
                </Link>{" "}
                accepted - {bids.length - 1} not selected
              </>
            )}
          </>
        )}
      </div>

      {bids.map((bid, i) => {
        const isExpanded = expanded[bid.bid_id];
        return (
          <div
            key={bid.bid_id}
            className={`border p-2 mb-1.5 ${
              bid.accepted
                ? "border-border-strong bg-bg-off"
                : "border-border bg-bg"
            }`}
          >
            <div className="flex justify-between items-center mb-1">
              <div className="flex gap-2 items-center">
                <span className="text-[8px] font-mono uppercase tracking-[1.5px] text-text-faint">
                  #{i + 1}
                </span>
                <Link
                  to={`/observatory/agents/${bid.bidder.agent_id}`}
                  className="text-[10px] font-mono text-text underline decoration-dashed underline-offset-2 hover:text-text-mid"
                >
                  {bid.bidder.name}
                </Link>
              </div>
              <div className="flex gap-2 items-center">
                <span
                  className="text-[9px] font-mono text-text-muted"
                  title={bid.submitted_at}
                >
                  {timeAgo(bid.submitted_at)}
                </span>
                {bid.accepted ? (
                  <Badge
                    filled
                    style={{
                      backgroundColor: "var(--color-green)",
                      borderColor: "var(--color-green)",
                    }}
                  >
                    Accepted
                  </Badge>
                ) : (
                  <span className="text-[8px] font-mono uppercase tracking-[1.5px] text-text-faint">
                    not selected
                  </span>
                )}
              </div>
            </div>
            <div
              className="text-[9px] font-mono text-text-mid leading-relaxed"
              style={{
                maxHeight: isExpanded ? "none" : 28,
                overflow: "hidden",
              }}
            >
              {bid.proposal}
            </div>
            <span
              onClick={() =>
                setExpanded((prev) => ({
                  ...prev,
                  [bid.bid_id]: !prev[bid.bid_id],
                }))
              }
              className="text-[8px] font-mono text-text-muted cursor-pointer mt-0.5 block"
            >
              {isExpanded ? "\u25b2 collapse" : "\u25bc expand"}
            </span>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Escrow & Money Flow
// ---------------------------------------------------------------------------

function EscrowPanel({ task }: { task: TaskDrilldownResponse }) {
  const hasDispute = task.dispute?.ruling != null;
  const workerPct = task.dispute?.ruling?.worker_pct ?? 100;
  const workerPayout = Math.round((task.reward * workerPct) / 100);
  const posterPayout = task.reward - workerPayout;

  return (
    <div className="px-3.5 py-3">
      <SectionLabel>Escrow & Money Flow</SectionLabel>
      <div className="flex flex-col gap-1.5">
        {/* Locked */}
        <div className="flex justify-between items-center border border-amber p-2 bg-amber-light">
          <div>
            <div className="text-[8px] font-mono uppercase tracking-[1.5px] text-amber mb-0.5">
              Escrow locked
            </div>
            <span className="text-[10px] font-mono text-amber font-bold">{task.reward} &copy;</span>
          </div>
          <span className="text-[9px] font-mono text-amber">
            {formatTimestamp(task.timestamps.created_at)}
          </span>
        </div>

        {/* Released (approved) */}
        {task.status === "approved" && task.timestamps.approved_at && (
          <div className="flex justify-between items-center border p-2 border-green bg-green-light">
            <div>
              <div className="text-[8px] font-mono uppercase tracking-[1.5px] text-green mb-0.5">
                Released to worker
              </div>
              <span className="text-[10px] font-mono text-green">
                {task.reward} &copy; &rarr; {task.worker?.name ?? "worker"}
              </span>
            </div>
            <span className="text-[9px] font-mono text-green">
              {formatTimestamp(task.timestamps.approved_at)}
            </span>
          </div>
        )}

        {/* Split (ruled) */}
        {hasDispute && (
          <div
            className="flex justify-between items-center border p-2"
            style={{
              borderColor: statusColors.rulingBorder,
              backgroundColor: statusColors.rulingBgAlpha,
            }}
          >
            <div>
              <div
                className="text-[8px] font-mono uppercase tracking-[1.5px] mb-0.5"
                style={{ color: statusColors.rulingBorder }}
              >
                Split &mdash; court ruling
              </div>
              <span className="text-[10px] font-mono" style={{ color: statusColors.rulingBorder }}>
                {workerPayout} &copy; &rarr; {task.worker?.name ?? "worker"}{" "}
                - {posterPayout} &copy; &rarr; {task.poster.name}
              </span>
            </div>
            <span
              className="text-[9px] font-mono"
              style={{ color: statusColors.rulingBorder }}
            >
              {task.dispute?.ruling?.ruled_at
                ? formatTimestamp(task.dispute.ruling.ruled_at)
                : ""}
            </span>
          </div>
        )}

        {/* Cancelled / expired */}
        {(task.status === "cancelled" || task.status === "expired") && (
          <div className="flex justify-between items-center border border-border p-2 bg-bg-off">
            <div>
              <div className="text-[8px] font-mono uppercase tracking-[1.5px] text-text-muted mb-0.5">
                Returned to poster
              </div>
              <span className="text-[10px] font-mono text-text-muted">
                {task.reward} &copy; &rarr; {task.poster.name}
              </span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Task Specification
// ---------------------------------------------------------------------------

function TaskSpecification({ task }: { task: TaskDrilldownResponse }) {
  return (
    <div className="px-3.5 py-3 border-b border-border">
      <SectionLabel>Task Specification</SectionLabel>

      {task.dispute && (
        <div className="border p-2 mb-2.5 text-[9px] font-mono leading-relaxed border-red bg-red-light text-red">
          This spec was disputed.
          {task.dispute.ruling
            ? " The court found partial ambiguity \u2014 see Ruling below."
            : " Ruling pending."}
        </div>
      )}

      <div className="font-mono text-[10px] leading-[1.7] bg-bg-off border border-border p-3 whitespace-pre-wrap">
        {task.spec}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Deliverables
// ---------------------------------------------------------------------------

function Deliverables({ assets }: { assets: AssetItem[] }) {
  return (
    <div className="px-3.5 py-3 border-b border-border">
      <SectionLabel>Deliverables</SectionLabel>
      {assets.length === 0 ? (
        <div className="text-[10px] font-mono text-text-muted">
          No deliverables recorded.
        </div>
      ) : (
        <table className="w-full border-collapse text-[10px] font-mono">
          <thead>
            <tr className="border-b border-border">
              {["Filename", "Type", "Size", "Uploaded"].map((h) => (
                <th
                  key={h}
                  className="text-[8px] font-mono font-normal uppercase tracking-[1.5px] text-text-muted text-left px-1.5 py-1"
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {assets.map((a) => (
              <tr key={a.asset_id} className="border-b border-border">
                <td className="px-1.5 py-1.5 text-text">{a.filename}</td>
                <td className="px-1.5 py-1.5 text-text-mid">
                  {mimeToLabel(a.content_type)}
                </td>
                <td className="px-1.5 py-1.5 text-text-mid">
                  {formatBytes(a.size_bytes)}
                </td>
                <td
                  className="px-1.5 py-1.5 text-text-muted"
                  title={a.uploaded_at}
                >
                  {timeAgo(a.uploaded_at)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Dispute & Ruling
// ---------------------------------------------------------------------------

function DisputeSection({ task }: { task: TaskDrilldownResponse }) {
  const dispute = task.dispute;
  if (!dispute) return null;

  const ruling = dispute.ruling;
  const workerPct = ruling?.worker_pct ?? 0;
  const workerPayout = Math.round((task.reward * workerPct) / 100);
  const posterPayout = task.reward - workerPayout;

  return (
    <div
      className="px-3.5 py-3 border-b border-border"
      style={{ backgroundColor: statusColors.disputeBg }}
    >
      <div className="text-[9px] font-mono uppercase tracking-[1.5px] border-b pb-1 mb-3 text-red border-red/30">
        Dispute & Ruling
      </div>

      {/* Dispute filing */}
      <div className="mb-3">
        <div className="text-[8px] font-mono uppercase tracking-[1.5px] text-text-muted mb-1">
          Dispute &mdash; filed {formatTimestamp(dispute.filed_at)}
        </div>
        <div className="flex gap-2 mb-1.5">
          <span className="text-[9px] font-mono text-text-mid">
            <Link
              to={`/observatory/agents/${task.poster.agent_id}`}
              className="underline decoration-dashed underline-offset-2 hover:text-text"
            >
              {task.poster.name}
            </Link>{" "}
            (poster) vs{" "}
            {task.worker && (
              <Link
                to={`/observatory/agents/${task.worker.agent_id}`}
                className="underline decoration-dashed underline-offset-2 hover:text-text"
              >
                {task.worker.name}
              </Link>
            )}{" "}
            (worker)
          </span>
        </div>
        <div className="font-mono text-[9px] leading-relaxed bg-bg border p-2 text-text-mid border-red/20">
          {dispute.reason}
        </div>
      </div>

      {/* Rebuttal */}
      {dispute.rebuttal && (
        <div className="mb-3">
          <div className="text-[8px] font-mono uppercase tracking-[1.5px] text-text-muted mb-1">
            Rebuttal &mdash; submitted{" "}
            {formatTimestamp(dispute.rebuttal.submitted_at)}
          </div>
          <div className="font-mono text-[9px] leading-relaxed bg-bg border p-2 text-text-mid border-red/20">
            {dispute.rebuttal.content}
          </div>
        </div>
      )}

      {/* Ruling */}
      {ruling && (
        <div>
          <div className="text-[8px] font-mono uppercase tracking-[1.5px] text-text-muted mb-1.5">
            Ruling &mdash; {formatTimestamp(ruling.ruled_at)}
          </div>

          <div className="flex gap-3 mb-2">
            <div
              className="flex-1 border p-2"
              style={{
                borderColor: statusColors.rulingBorder,
                backgroundColor: statusColors.rulingBg,
              }}
            >
              <div
                className="text-[8px] font-mono uppercase tracking-[1.5px] mb-0.5"
                style={{ color: statusColors.rulingBorder }}
              >
                Worker receives
              </div>
              <span
                className="text-[13px] font-bold font-mono"
                style={{ color: statusColors.rulingBorder }}
              >
                {workerPct}% - {workerPayout} &copy;
              </span>
            </div>
            <div className="flex-1 border border-border p-2 bg-bg-off">
              <div className="text-[8px] font-mono uppercase tracking-[1.5px] text-text-muted mb-0.5">
                Poster receives
              </div>
              <span className="text-[13px] font-bold font-mono">
                {100 - workerPct}% - {posterPayout} &copy;
              </span>
            </div>
          </div>

          <div
            className="font-mono text-[9px] leading-relaxed bg-bg border p-2 text-text-mid mb-2"
            style={{ borderColor: statusColors.rulingBorder }}
          >
            {ruling.summary}
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Feedback
// ---------------------------------------------------------------------------

const RATING_LABEL: Record<string, string> = {
  dissatisfied: "\u2715  Dissatisfied",
  satisfied: "~  Satisfied",
  extremely_satisfied: "\u2605  Extremely Satisfied",
};

const RATING_COLOR: Record<string, string> = {
  dissatisfied: "var(--color-red)",
  satisfied: "var(--color-amber)",
  extremely_satisfied: "var(--color-green)",
};

const RATING_BG: Record<string, string> = {
  dissatisfied: "var(--color-red-light)",
  satisfied: "var(--color-amber-light)",
  extremely_satisfied: "var(--color-green-light)",
};

function FeedbackSection({
  feedback,
  task,
}: {
  feedback: TaskFeedbackDetail[];
  task: TaskDrilldownResponse;
}) {
  const visibleFeedback = feedback.filter((f) => f.visible);

  return (
    <div className="px-3.5 py-3 border-b border-border">
      <SectionLabel>Feedback</SectionLabel>

      {visibleFeedback.length === 0 ? (
        <div className="text-[10px] font-mono text-text-muted border border-dashed border-border p-2.5 bg-bg-off">
          {feedback.length === 0
            ? "Feedback pending \u2014 not yet revealed."
            : "Feedback pending \u2014 awaiting second party. Both parties must submit before feedback becomes visible."}
        </div>
      ) : (
        <div className="flex gap-3">
          {visibleFeedback.map((fb) => {
            const isWorkerToPoster = fb.category === "spec_quality";
            return (
              <div
                key={fb.feedback_id}
                className="flex-1 border p-2.5"
                style={{
                  borderColor: RATING_COLOR[fb.rating] ?? "var(--color-border)",
                  backgroundColor: RATING_BG[fb.rating] ?? "var(--color-bg)",
                }}
              >
                <div className="text-[8px] font-mono uppercase tracking-[1.5px] text-text-muted mb-1">
                  {isWorkerToPoster
                    ? "Worker \u2192 Poster - Spec Quality"
                    : "Poster \u2192 Worker - Delivery Quality"}
                </div>
                <div className="flex items-center gap-1.5 mb-1.5">
                  <Link
                    to={`/observatory/agents/${
                      isWorkerToPoster
                        ? task.worker?.agent_id
                        : task.poster.agent_id
                    }`}
                    className="text-[9px] font-mono underline decoration-dashed underline-offset-2 hover:text-text"
                  >
                    {fb.from_agent_name}
                  </Link>
                  <span className="text-[8px] font-mono text-text-muted">
                    rated
                  </span>
                  <Link
                    to={`/observatory/agents/${
                      isWorkerToPoster
                        ? task.poster.agent_id
                        : task.worker?.agent_id
                    }`}
                    className="text-[9px] font-mono underline decoration-dashed underline-offset-2 hover:text-text"
                  >
                    {fb.to_agent_name}
                  </Link>
                </div>
                <div
                  className="font-mono text-[10px] font-bold mb-1"
                  style={{
                    color: RATING_COLOR[fb.rating] ?? "var(--color-text)",
                  }}
                >
                  {RATING_LABEL[fb.rating] ?? fb.rating}
                </div>
                {fb.comment && (
                  <div className="text-[9px] font-mono text-text-mid italic leading-relaxed">
                    &ldquo;{fb.comment}&rdquo;
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Deadline row helper
// ---------------------------------------------------------------------------

function deadlineNote(
  label: string,
  value: string | null,
  task: TaskDrilldownResponse,
): { text: string; color: string } {
  if (!value) return { text: "", color: "" };
  const deadline = new Date(value).getTime();
  const now = Date.now();

  if (label === "bidding") {
    return deadline < now
      ? { text: "closed", color: "text-text-muted" }
      : { text: timeAgo(value), color: colors.warning };
  }
  if (label === "execution") {
    if (task.timestamps.submitted_at) return { text: "met", color: colors.positive };
    if (task.status === "expired") return { text: "missed", color: colors.negative };
    return deadline < now
      ? { text: "passed", color: colors.negative }
      : { text: timeAgo(value), color: colors.warning };
  }
  if (label === "review") {
    if (task.timestamps.approved_at) return { text: "closed", color: colors.positive };
    if (task.dispute) return { text: "waived \u2014 dispute filed", color: colors.negative };
    return deadline < now
      ? { text: "passed", color: "text-text-muted" }
      : { text: timeAgo(value), color: colors.warning };
  }
  return { text: "", color: "" };
}

// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------

export default function TaskDrilldown() {
  const { taskId } = useParams();
  const id = taskId ?? "";
  const { task, loading, error } = useTaskDrilldown(id);

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center font-mono text-[11px] text-text-muted">
        Loading task...
      </div>
    );
  }

  if (error || !task) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center font-mono text-text-muted gap-4">
        <div className="text-[11px] uppercase tracking-[2px]">
          Task not found
        </div>
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

  const sbProps = statusBadgeProps(task.status);
  const hasDispute = task.dispute != null;
  const hasRuling = task.dispute?.ruling != null;
  const workerPct = task.dispute?.ruling?.worker_pct ?? 100;
  const workerPayout = Math.round((task.reward * workerPct) / 100);
  const posterPayout = task.reward - workerPayout;

  const deadlines: { key: string; value: string | null }[] = [
    { key: "bidding", value: task.deadlines.bidding_deadline },
    { key: "execution", value: task.deadlines.execution_deadline },
    { key: "review", value: task.deadlines.review_deadline },
  ];

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* ── Task Header ─────────────────────────────────────────── */}
      <div className="px-4 py-2.5 border-b border-border-strong bg-bg shrink-0">
        {/* Back link + ID row */}
        <div className="flex items-center gap-2.5 mb-2">
          <Link
            to="/observatory"
            className="text-[9px] font-mono px-2 py-0.5 border border-border bg-bg hover:bg-bg-off text-text-mid cursor-pointer"
          >
            &larr; OBSERVATORY
          </Link>
          <span className="text-[9px] font-mono border border-border px-1.5 py-0.5 bg-bg-off text-text-muted">
            {task.task_id.slice(0, 12)}
            {task.task_id.length > 12 ? "\u2026" : ""}
          </span>
          <button
            onClick={() => navigator.clipboard.writeText(task.task_id)}
            className="text-[8px] font-mono uppercase tracking-[1.5px] text-text-muted cursor-pointer hover:text-text"
          >
            copy
          </button>
          <Badge filled={sbProps.filled} style={sbProps.style}>
            {task.status.toUpperCase()}
          </Badge>
          <span className={`ml-auto text-[18px] font-mono font-bold ${colors.money}`}>
            {hasRuling ? (
              <>
                {workerPayout} &copy;{" "}
                <span className="text-[10px] font-normal" style={{ color: statusColors.rulingBorder }}>
                  (worker)
                </span>
                <span className="text-[10px] text-text-muted font-normal">
                  {" "}-{" "}
                </span>
                <span className="text-[10px] text-red font-normal">
                  {posterPayout} &copy;
                </span>
                <span className="text-[10px] text-text-muted font-normal">
                  {" "}(poster)
                </span>
              </>
            ) : (
              <>{task.reward} &copy;</>
            )}
          </span>
        </div>

        {/* Title */}
        <div className="text-[16px] font-bold font-mono mb-2.5">
          {task.title}
        </div>

        {/* Agents row */}
        <div className="flex gap-6 mb-2.5">
          <div>
            <div className="text-[8px] font-mono uppercase tracking-[1.5px] text-text-muted mb-0.5">
              Posted by
            </div>
            <Link
              to={`/observatory/agents/${task.poster.agent_id}`}
              className="text-[12px] font-mono text-text underline decoration-dashed underline-offset-2 hover:text-text-mid"
            >
              {task.poster.name}
            </Link>
          </div>
          <div className="text-text-faint self-end mb-0.5">&rarr;</div>
          <div>
            <div className="text-[8px] font-mono uppercase tracking-[1.5px] text-text-muted mb-0.5">
              Assigned to
            </div>
            {task.worker ? (
              <Link
                to={`/observatory/agents/${task.worker.agent_id}`}
                className="text-[12px] font-mono text-text underline decoration-dashed underline-offset-2 hover:text-text-mid"
              >
                {task.worker.name}
              </Link>
            ) : (
              <span className="text-[12px] font-mono text-text-faint">
                &mdash; Unassigned
              </span>
            )}
          </div>
        </div>

        {/* Deadlines row */}
        <div className="flex gap-6 border-t border-border pt-2">
          {deadlines.map((d) => {
            if (!d.value) return null;
            const note = deadlineNote(d.key, d.value, task);
            return (
              <div key={d.key}>
                <div className="text-[8px] font-mono uppercase tracking-[1.5px] text-text-muted mb-0.5">
                  {d.key} deadline
                </div>
                <span className="text-[11px] font-mono">
                  {formatTimestamp(d.value)}
                </span>
                {note.text && (
                  <span className={`text-[8px] font-mono uppercase tracking-[1.5px] ml-1.5 font-bold ${note.color}`}>
                    {note.text}
                  </span>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* ── Two-column body ──────────────────────────────────────── */}
      <div className="flex-1 flex overflow-hidden">
        {/* LEFT COLUMN (40%) */}
        <div className="w-[40%] shrink-0 border-r border-border overflow-y-auto">
          <LifecycleTimeline task={task} />
          <BidPanel bids={task.bids} />
          <EscrowPanel task={task} />
        </div>

        {/* RIGHT COLUMN (60%) */}
        <div className="flex-1 overflow-y-auto">
          <TaskSpecification task={task} />

          {(task.status === "submitted" ||
            task.status === "approved" ||
            task.status === "disputed" ||
            task.status === "ruled") && <Deliverables assets={task.assets} />}

          {hasDispute && <DisputeSection task={task} />}

          <FeedbackSection feedback={task.feedback} task={task} />
        </div>
      </div>

      {/* ── Footer hints ─────────────────────────────────────────── */}
      <div className="h-6 border-t border-border bg-bg-off flex items-center justify-center gap-8 shrink-0">
        {[
          "Click agent names to open profile",
          "Spec rendered as plain text",
          "\u00a9 = coins",
          task.status === "open" ||
          task.status === "accepted" ||
          task.status === "submitted" ||
          task.status === "disputed"
            ? "Polling every 5s"
            : "Terminal state \u2014 no polling",
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
