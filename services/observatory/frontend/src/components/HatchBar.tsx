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
          backgroundImage:
            "repeating-linear-gradient(45deg, var(--color-green) 0, var(--color-green) 1px, transparent 0, transparent 50%)",
          backgroundSize: "6px 6px",
        }}
      />
    </div>
  );
}
