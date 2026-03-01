interface HatchBarProps {
  pct: number;
  height?: number;
}

export default function HatchBar({ pct, height = 14 }: HatchBarProps) {
  return (
    <div
      className="relative w-full border border-border bg-bg-off"
      style={{ height }}
    >
      <div
        className="absolute left-0 top-0 bottom-0 border-r border-border-strong"
        style={{
          width: `${pct}%`,
          backgroundColor: "var(--color-green-light)",
        }}
      />
    </div>
  );
}
