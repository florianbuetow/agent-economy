interface SparklineProps {
  points: number[];
  width?: number;
  height?: number;
  fill?: boolean;
}

export default function Sparkline({
  points,
  width = 120,
  height = 28,
  fill = false,
}: SparklineProps) {
  if (points.length < 2) return null;

  const pts = points
    .map(
      (y, i) =>
        `${(i / (points.length - 1)) * width},${height - y * height}`
    )
    .join(" ");

  return (
    <svg width={width} height={height} className="block">
      {fill && (
        <polygon
          points={`0,${height} ${pts} ${width},${height}`}
          fill="#eeeeee"
          stroke="none"
        />
      )}
      <polyline
        points={pts}
        fill="none"
        stroke="#111111"
        strokeWidth={1.5}
        strokeLinejoin="round"
      />
    </svg>
  );
}
