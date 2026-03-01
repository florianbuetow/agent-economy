/** Semantic color classes — single source of truth for color meaning. */
export const colors = {
  money: "text-green",
  moneyBg: "bg-green-light",
  spent: "text-red",
  escrow: "text-amber",
  stars: "text-yellow",
  positive: "text-green",
  negative: "text-red",
  warning: "text-amber",
  live: "bg-green",
} as const;

/** Status colors for badges and timeline dots (fixed across themes). */
export const statusColors = {
  accepted: { bg: "#004085", border: "#004085", text: "#fff" },
  ruled: { bg: "#4a1580", border: "#4a1580", text: "#fff" },
  disputeBg: "#fdf3f3",
  rulingBorder: "#4a1580",
  rulingBg: "#e2d5f8",
  rulingBgAlpha: "#e2d5f81a",
} as const;

/** Standard tooltip background for Chart.js charts. */
export const tooltipBg = "#111111";

/** Read a CSS custom property value, with a fallback for SSR / missing vars. */
export function cssVar(name: string, fallback: string): string {
  return (
    getComputedStyle(document.documentElement)
      .getPropertyValue(name)
      .trim() || fallback
  );
}

/**
 * Returns a Tailwind text color class based on the sign of a value.
 * - "up-good": positive = green, negative = red (e.g., GDP growth)
 * - "up-bad": positive = red, negative = green (e.g., unemployment)
 */
export function trendColor(
  value: number,
  mode: "up-good" | "up-bad"
): string {
  if (value === 0) return "text-text";
  const positive = value > 0;
  if (mode === "up-good") return positive ? "text-green" : "text-red";
  return positive ? "text-red" : "text-green";
}

/**
 * Returns a Tailwind text color class based on threshold ranges.
 * - value >= good → green
 * - value >= warn → amber
 * - value < warn → red
 *
 * Set invert=true when lower is better (e.g., dispute rate).
 */
export function thresholdColor(
  value: number,
  good: number,
  warn: number,
  invert = false
): string {
  if (invert) {
    if (value <= warn) return "text-green";
    if (value <= good) return "text-amber";
    return "text-red";
  }
  if (value >= good) return "text-green";
  if (value >= warn) return "text-amber";
  return "text-red";
}
