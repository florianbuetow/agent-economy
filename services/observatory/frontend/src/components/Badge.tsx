import type { CSSProperties, ReactNode } from "react";

interface BadgeProps {
  children: ReactNode;
  filled?: boolean;
  style?: CSSProperties;
}

export default function Badge({ children, filled = false, style }: BadgeProps) {
  return (
    <span
      className={`inline-block text-[8px] font-mono tracking-wide uppercase px-[5px] py-[2px] border border-border-strong ${
        filled ? "bg-border-strong text-bg" : "bg-bg text-text"
      }`}
      style={style}
    >
      {children}
    </span>
  );
}
