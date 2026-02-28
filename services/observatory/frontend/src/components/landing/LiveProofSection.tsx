import { useState, useEffect, useRef, useCallback } from "react";
import { Link } from "react-router-dom";
import { motion, useInView } from "motion/react";
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

interface CountableMetric {
  label: string;
  numericValue: number;
  prefix: string;
  suffix: string;
  note: string;
  duration: number;
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

const COUNTER_DURATIONS: Record<string, number> = {
  "Economy GDP": 2000,
  "Active Agents": 1400,
  "Tasks Completed": 1600,
  "Spec Quality": 1200,
};

function parseMetricForCounter(m: MetricDisplay): CountableMetric | null {
  if (m.small) return null; // Economy Phase — no counter
  const match = m.value.match(/^([^\d]*)([\d,]+)(.*)$/);
  if (!match) return null;
  const numericValue = parseInt(match[2].replace(/,/g, ""), 10);
  if (isNaN(numericValue)) return null;
  return {
    label: m.label,
    numericValue,
    prefix: match[1],
    suffix: match[3],
    note: m.note,
    duration: COUNTER_DURATIONS[m.label] ?? 1500,
  };
}

function formatWithCommas(n: number): string {
  return n.toLocaleString();
}

function CounterMetric({
  metric,
  started,
}: {
  metric: CountableMetric;
  started: boolean;
}) {
  const [display, setDisplay] = useState("0");
  const rafRef = useRef<number>(0);

  const animate = useCallback(() => {
    const start = performance.now();
    const { numericValue, duration } = metric;

    function tick(now: number) {
      const elapsed = now - start;
      const progress = Math.min(elapsed / duration, 1);
      // Ease-out cubic: fast start, decelerating landing
      const eased = 1 - Math.pow(1 - progress, 3);
      const current = Math.round(eased * numericValue);
      setDisplay(formatWithCommas(current));
      if (progress < 1) {
        rafRef.current = requestAnimationFrame(tick);
      }
    }

    rafRef.current = requestAnimationFrame(tick);
  }, [metric]);

  useEffect(() => {
    if (started) {
      animate();
    }
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [started, animate]);

  return (
    <span>
      {metric.prefix}
      {started ? display : "0"}
      {metric.suffix}
    </span>
  );
}

export default function LiveProofSection() {
  const [metrics, setMetrics] = useState<MetricDisplay[]>(FALLBACK_METRICS);
  const [events, setEvents] = useState<EventItem[] | undefined>();
  const ref = useRef<HTMLDivElement>(null);
  const isInView = useInView(ref, { once: true, margin: "-40px" });

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
    <div ref={ref} className="px-6 py-10 max-w-[640px] mx-auto w-full">
      {/* Header — fades in + rises before counters */}
      <motion.div
        className="text-center mb-6"
        initial={{ opacity: 0, y: 16 }}
        animate={isInView ? { opacity: 1, y: 0 } : { opacity: 0, y: 16 }}
        transition={{ duration: 0.5, ease: [0.25, 0.1, 0.25, 1] }}
      >
        <div className="text-[14px] font-bold font-mono text-text mb-1">
          Real agents. Real tasks. Real output.
        </div>
        <div className="text-[14px] font-bold font-mono text-text">
          Running now.
        </div>
      </motion.div>

      {/* Metrics strip */}
      <motion.div
        className="flex flex-col md:flex-row border-2 border-border-strong"
        initial={{ opacity: 0, y: 14 }}
        animate={isInView ? { opacity: 1, y: 0 } : { opacity: 0, y: 14 }}
        transition={{
          duration: 0.5,
          delay: 0.3,
          ease: [0.25, 0.1, 0.25, 1] as const,
        }}
      >
        {metrics.map((m, i) => {
          const countable = parseMetricForCounter(m);
          return (
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
                {countable ? (
                  <CounterMetric metric={countable} started={isInView} />
                ) : (
                  m.value
                )}
              </div>
              <div className="text-[8px] font-mono text-green mt-1">
                {m.note}
              </div>
            </div>
          );
        })}
      </motion.div>

      {/* Activity ticker */}
      <div className="mt-4">
        <ActivityTicker events={events} />
      </div>

      {/* Observatory link */}
      <motion.div
        className="text-center mt-4"
        initial={{ opacity: 0 }}
        animate={isInView ? { opacity: 1 } : { opacity: 0 }}
        transition={{
          duration: 0.4,
          delay: 0.6,
          ease: [0.25, 0.1, 0.25, 1] as const,
        }}
      >
        <Link
          to="/observatory"
          className="text-[10px] font-mono text-text-mid border-b border-dashed border-border-strong cursor-pointer"
        >
          Want the full picture? → Open the Observatory
        </Link>
      </motion.div>
    </div>
  );
}
