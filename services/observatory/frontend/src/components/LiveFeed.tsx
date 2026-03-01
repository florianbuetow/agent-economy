import { useState } from "react";
import { Link } from "react-router-dom";
import type { EventItem } from "../types";
import Badge from "./Badge";

interface LiveFeedProps {
  events: EventItem[];
  paused: boolean;
  onTogglePause: () => void;
}

const EVENT_TYPE_TO_BADGE: Record<string, string> = {
  // tasks
  "task.created": "TASK",
  "task.accepted": "CONTRACT",
  "task.submitted": "SUBMIT",
  "task.approved": "PAYOUT",
  "task.auto_approved": "PAYOUT",
  "task.disputed": "DISPUTE",
  "task.ruled": "RULING",
  "task.cancelled": "CANCEL",
  "task.expired": "CANCEL",
  // bids
  "bid.submitted": "BID",
  // bank
  "salary.paid": "SALARY",
  "escrow.locked": "ESCROW",
  "escrow.released": "PAYOUT",
  "escrow.split": "RULING",
  "account.created": "AGENT",
  // reputation
  "feedback.revealed": "REP",
  // court
  "claim.filed": "DISPUTE",
  "rebuttal.submitted": "DISPUTE",
  "ruling.delivered": "RULING",
  // identity
  "agent.registered": "AGENT",
  // assets
  "asset.uploaded": "SUBMIT",
};

const BADGE_COLORS: Record<string, string> = {
  TASK: "#4a6fa5",     // muted blue
  BID: "#a06080",      // dusty rose
  PAYOUT: "#4a8c5c",   // sage green
  SALARY: "#3a7a5a",   // darker green
  CONTRACT: "#6a5a8c", // muted violet
  SUBMIT: "#4a7a80",   // teal
  ESCROW: "#a07040",   // burnt sienna
  REP: "#8a7a3a",      // olive gold
  DISPUTE: "#8c4a4a",  // muted red
  RULING: "#6a4a80",   // plum
  CANCEL: "#7a7a7a",   // medium gray
  AGENT: "#6a6a6a",    // gray
};

const FILTER_TYPES = ["ALL", "TASK", "BID", "PAYOUT", "CONTRACT", "ESCROW", "REP"] as const;

function badgeStyle(badgeType: string): { filled: boolean; style?: React.CSSProperties } {
  const bg = BADGE_COLORS[badgeType];
  if (bg) {
    return { filled: true, style: { backgroundColor: bg, borderColor: bg, color: "#fff" } };
  }
  return { filled: true };
}

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

export default function LiveFeed({ events, paused, onTogglePause }: LiveFeedProps) {
  const [filter, setFilter] = useState<string>("ALL");

  const filtered = filter === "ALL"
    ? events
    : events.filter((ev) => {
        const badge = EVENT_TYPE_TO_BADGE[ev.event_type] ?? "";
        return badge === filter;
      });

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="p-3 border-b border-border">
        <div className="flex items-center justify-between mb-2">
          <div className="text-[9px] font-mono uppercase tracking-[1.5px] text-text-muted">
            Live Feed
          </div>
          <button
            onClick={onTogglePause}
            className="text-[9px] font-mono uppercase tracking-[1px] px-2 py-0.5 border border-border-strong bg-bg hover:bg-bg-off text-text cursor-pointer"
          >
            {paused ? "Resume" : "Pause"}
          </button>
        </div>
        <div className="flex gap-1 flex-wrap">
          {FILTER_TYPES.map((f) => {
            const bg = BADGE_COLORS[f];
            const isActive = filter === f;
            return (
              <button
                key={f}
                onClick={() => setFilter(f)}
                onMouseEnter={(e) => {
                  if (!isActive && bg) {
                    e.currentTarget.style.backgroundColor = bg;
                    e.currentTarget.style.borderColor = bg;
                    e.currentTarget.style.color = "#fff";
                  }
                }}
                onMouseLeave={(e) => {
                  if (!isActive) {
                    e.currentTarget.style.backgroundColor = "var(--color-bg)";
                    e.currentTarget.style.borderColor = "var(--color-border)";
                    e.currentTarget.style.color = "var(--color-text-muted)";
                  }
                }}
                className="text-[8px] font-mono uppercase tracking-[1px] px-1.5 py-0.5 border cursor-pointer transition-all duration-150"
                style={
                  isActive && bg
                    ? { backgroundColor: bg, borderColor: bg, color: "#fff", fontWeight: "bold" }
                    : isActive
                      ? { backgroundColor: "var(--color-border-strong)", borderColor: "var(--color-border-strong)", color: "var(--color-bg)", fontWeight: "bold" }
                      : { backgroundColor: "var(--color-bg)", borderColor: "var(--color-border)", color: "var(--color-text-muted)" }
                }
              >
                {f}
              </button>
            );
          })}
        </div>
      </div>

      {/* Event stream */}
      <div className="flex-1 overflow-y-auto">
        {filtered.length === 0 && (
          <div className="p-3 text-[10px] font-mono text-text-muted text-center">
            {paused ? "Feed paused" : "Waiting for events..."}
          </div>
        )}
        {filtered.map((ev, i) => {
          const badgeType = EVENT_TYPE_TO_BADGE[ev.event_type] ?? ev.event_type.toUpperCase();
          const bs = badgeStyle(badgeType);

          return (
            <div
              key={ev.event_id}
              className={`flex items-start gap-2 px-3 py-2 border-b border-border ${
                i === 0 ? "bg-bg-off" : "bg-bg"
              }`}
            >
              <Badge filled={bs.filled} style={bs.style}>
                {badgeType}
              </Badge>
              <div className="flex-1 min-w-0">
                <div className="text-[10px] font-mono text-text leading-tight">
                  {ev.summary}
                  {ev.task_id && (
                    <>
                      {" "}
                      <Link
                        to={`/observatory/tasks/${ev.task_id}`}
                        className="text-text-mid underline decoration-dotted underline-offset-2 hover:text-text"
                      >
                        {ev.task_id.slice(0, 8)}
                      </Link>
                    </>
                  )}
                </div>
              </div>
              <div className="text-[8px] font-mono text-text-faint whitespace-nowrap shrink-0">
                {timeAgo(ev.timestamp)}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
