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
