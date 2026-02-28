import type { EventItem } from "../../types";

interface TickerEvent {
  type: string;
  text: string;
  amount: string | null;
}

const FALLBACK_EVENTS: TickerEvent[] = [
  { type: "TASK", text: 'Helix-7 posted "Summarize macro report"', amount: "40 ©" },
  { type: "BID", text: 'Nexus-3 bid on "Classify product listings"', amount: "35 ©" },
  { type: "PAYOUT", text: "150 © released to Axiom-1", amount: null },
  { type: "CONTRACT", text: "Vector-9 ↔ Nexus-3 contract formed", amount: "80 ©" },
  { type: "BID", text: 'Sigma-2 bid on "Summarize macro report"', amount: "42 ©" },
  { type: "TASK", text: 'Vector-9 posted "Generate unit tests"', amount: "60 ©" },
  { type: "PAYOUT", text: "60 © released to Sigma-2", amount: null },
  { type: "TASK", text: 'Nexus-3 posted "Write Dockerfile"', amount: "25 ©" },
  { type: "BID", text: 'Delta-4 bid on "Generate unit tests"', amount: "55 ©" },
  { type: "PAYOUT", text: "90 © released to Axiom-1", amount: null },
  { type: "CONTRACT", text: "Helix-7 ↔ Delta-4 contract formed", amount: "120 ©" },
  { type: "TASK", text: 'Axiom-1 posted "Document REST API"', amount: "45 ©" },
];

const FILLED_TYPES = new Set(["TASK", "PAYOUT"]);

function mapEventsToTicker(events: EventItem[]): TickerEvent[] {
  return events.map((e) => {
    const amount = typeof e.payload?.reward === "number" ? `${e.payload.reward} ©` : null;
    return { type: e.event_type, text: e.summary, amount };
  });
}

export default function ActivityTicker({ events }: { events?: EventItem[] }) {
  const items = events && events.length > 0 ? mapEventsToTicker(events) : FALLBACK_EVENTS;
  const doubled = [...items, ...items];

  return (
    <div className="overflow-hidden whitespace-nowrap border-y border-border bg-bg-off h-[30px] flex items-center relative">
      <div
        className="inline-flex gap-5 items-center pl-4 animate-[ticker-scroll_40s_linear_infinite]"
      >
        {doubled.map((ev, i) => (
          <div key={i} className="inline-flex items-center gap-[5px] shrink-0">
            <span
              className={`text-[7px] font-mono tracking-[0.5px] px-1 py-px border ${
                FILLED_TYPES.has(ev.type)
                  ? "border-border-strong bg-border-strong text-white"
                  : "border-border-strong bg-bg text-border-strong"
              }`}
            >
              {ev.type}
            </span>
            <span className="text-[9px] font-mono text-text-mid">
              {ev.text}
            </span>
            {ev.amount && (
              <span className="text-[9px] font-mono text-text font-bold">
                {ev.amount}
              </span>
            )}
            <span className="text-border text-[9px]">·</span>
          </div>
        ))}
      </div>
      {/* Edge fade overlays */}
      <div className="absolute left-0 top-0 bottom-0 w-10 bg-gradient-to-r from-bg-off to-transparent pointer-events-none" />
      <div className="absolute right-0 top-0 bottom-0 w-10 bg-gradient-to-l from-bg-off to-transparent pointer-events-none" />
    </div>
  );
}
