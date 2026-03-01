import { useEffect, useRef, useState } from "react";
import { GraphEngine } from "./engine";
import type { AgentState } from "./types";
import { STATE_LABELS } from "./types";

export default function EconomyGraph() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const engineRef = useRef<GraphEngine | null>(null);
  const [stateCounts, setStateCounts] = useState<Record<
    AgentState,
    number
  > | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const engine = new GraphEngine(canvas);
    engineRef.current = engine;
    engine.start();

    // Update state counts every 500ms for the ticker
    const interval = setInterval(() => {
      setStateCounts(engine.getStateCounts());
    }, 500);

    const handleResize = () => engine.resize();
    window.addEventListener("resize", handleResize);

    return () => {
      engine.stop();
      clearInterval(interval);
      window.removeEventListener("resize", handleResize);
      engineRef.current = null;
    };
  }, []);

  return (
    <>
      <canvas
        ref={canvasRef}
        className="absolute inset-0 w-full h-full"
        style={{ background: "#fafafa" }}
      />
      {stateCounts && (
        <div
          className="absolute bottom-0 left-0 right-0 z-10 flex justify-center gap-4 py-2 font-mono text-[9px] tracking-wide"
          style={{
            background: "linear-gradient(transparent, rgba(250,250,250,0.85))",
          }}
        >
          {(Object.keys(STATE_LABELS) as AgentState[]).map((state) => (
            <span key={state} className="text-[#888888]">
              <span className="text-[#333333] font-bold">
                {stateCounts[state] ?? 0}
              </span>{" "}
              {STATE_LABELS[state]}
            </span>
          ))}
        </div>
      )}
    </>
  );
}
