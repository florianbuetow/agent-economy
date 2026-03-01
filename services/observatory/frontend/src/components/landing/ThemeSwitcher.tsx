import { useRef } from "react";
import { motion, useInView } from "motion/react";
import { THEMES, useTheme } from "../../theme";

const THEME_KEYS = ["newsprint", "ft", "gs"] as const;

export default function ThemeSwitcher() {
  const ref = useRef<HTMLDivElement>(null);
  const isInView = useInView(ref, { once: true, margin: "-40px" });
  const { current, setTheme } = useTheme();

  return (
    <div ref={ref} className="px-6 py-10 max-w-[640px] mx-auto w-full">
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={isInView ? { opacity: 1, y: 0 } : { opacity: 0, y: 16 }}
        transition={{ duration: 0.5, ease: [0.25, 0.1, 0.25, 1] as const }}
      >
        <div className="text-center mb-6 text-[9px] font-mono uppercase tracking-[2.5px] text-text-muted">
          THEME
        </div>

        <div className="flex flex-col md:flex-row gap-3">
          {THEME_KEYS.map((key) => {
            const theme = THEMES[key];
            const active = current === key;
            return (
              <button
                key={key}
                onClick={() => setTheme(key)}
                className={`flex-1 text-left px-4 py-4 border cursor-pointer transition-[border-color] duration-150 ${
                  active
                    ? "border-border-strong"
                    : "border-border hover:border-text-muted"
                }`}
              >
                <div className="text-[11px] font-mono font-bold text-text mb-1">
                  {theme.name}
                </div>
                <div className="text-[9px] font-mono text-text-muted leading-[1.6] mb-3">
                  {theme.description}
                </div>
                <div className="flex gap-1">
                  {[
                    theme.colors.bg,
                    theme.colors.bgOff,
                    theme.colors.borderStrong,
                    theme.colors.text,
                    theme.colors.green,
                  ].map((color, i) => (
                    <div
                      key={i}
                      className="w-4 h-4 border border-border"
                      style={{ backgroundColor: color }}
                    />
                  ))}
                </div>
                <div className={`text-[8px] font-mono uppercase tracking-[1.5px] text-green mt-2.5 ${active ? "" : "invisible"}`}>
                  ACTIVE
                </div>
              </button>
            );
          })}
        </div>
      </motion.div>
    </div>
  );
}
