import { useEffect, useRef, useState, useCallback } from "react";
import { GraphEngine } from "./engine";
import type { HitInfo } from "./engine";
import type { AgentState } from "./types";
import { STATE_LABELS, AGENT_STATE_TINTS } from "./types";

interface Tooltip {
  x: number;
  y: number;
  info: HitInfo;
}

export default function EconomyGraph() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const engineRef = useRef<GraphEngine | null>(null);
  const [stateCounts, setStateCounts] = useState<Record<
    AgentState,
    number
  > | null>(null);
  const [tooltip, setTooltip] = useState<Tooltip | null>(null);

  // Track drag state in refs (no re-renders needed)
  const isDragging = useRef(false);
  const lastMouse = useRef({ x: 0, y: 0 });

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

    // Native wheel listener (non-passive) so preventDefault works without warnings
    const handleNativeWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
      engine.zoomAt(x, y, factor);
    };
    canvas.addEventListener("wheel", handleNativeWheel, { passive: false });

    return () => {
      engine.stop();
      clearInterval(interval);
      window.removeEventListener("resize", handleResize);
      canvas.removeEventListener("wheel", handleNativeWheel);
      engineRef.current = null;
    };
  }, []);

  const getCanvasCoords = useCallback(
    (e: React.MouseEvent) => {
      const canvas = canvasRef.current;
      if (!canvas) return { x: 0, y: 0 };
      const rect = canvas.getBoundingClientRect();
      return { x: e.clientX - rect.left, y: e.clientY - rect.top };
    },
    [],
  );

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      const engine = engineRef.current;
      if (!engine) return;

      const { x, y } = getCanvasCoords(e);

      // Pan while dragging
      if (isDragging.current) {
        const dx = x - lastMouse.current.x;
        const dy = y - lastMouse.current.y;
        engine.panBy(dx, dy);
        lastMouse.current = { x, y };
        setTooltip(null);
        return;
      }

      lastMouse.current = { x, y };

      // Hit-test for tooltip
      const hit = engine.hitTest(x, y);
      if (hit) {
        setTooltip({ x: e.clientX, y: e.clientY, info: hit });
      } else {
        setTooltip(null);
      }
    },
    [getCanvasCoords],
  );

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      if (e.button !== 0) return;
      isDragging.current = true;
      const { x, y } = getCanvasCoords(e);
      lastMouse.current = { x, y };
      setTooltip(null);
    },
    [getCanvasCoords],
  );

  const handleMouseUp = useCallback(() => {
    isDragging.current = false;
  }, []);

  const handleMouseLeave = useCallback(() => {
    isDragging.current = false;
    setTooltip(null);
  }, []);

  return (
    <>
      <canvas
        ref={canvasRef}
        className="absolute inset-0 w-full h-full"
        style={{ background: "#fafafa", cursor: isDragging.current ? "grabbing" : "grab" }}
        onMouseMove={handleMouseMove}
        onMouseDown={handleMouseDown}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseLeave}
      />
      {tooltip && (
        <div
          className="fixed z-50 pointer-events-none font-mono border border-[#cccccc] bg-white/95 backdrop-blur-sm px-3 py-2 text-[10px] leading-[1.6]"
          style={{
            left: tooltip.x + 14,
            top: tooltip.y + 14,
          }}
        >
          <div className="text-[#111111] font-bold tracking-wide">
            {tooltip.info.kind === "agent" ? "AGENT" : "TASK"}{" "}
            <span className="font-normal text-[#888888]">{tooltip.info.category}</span>
          </div>
          <div className="text-[#333333]">{tooltip.info.name}</div>
          <div className="text-[#888888]">
            {tooltip.info.state} &middot; {tooltip.info.detailLabel}: {tooltip.info.detail}
          </div>
        </div>
      )}
      {stateCounts && (
        <div
          className="absolute bottom-0 left-0 right-0 z-10 flex justify-center gap-4 py-2 font-mono text-[9px] tracking-wide"
          style={{
            background: "linear-gradient(transparent, rgba(250,250,250,0.85))",
          }}
        >
          {(Object.keys(STATE_LABELS) as AgentState[]).map((state) => (
            <span key={state} className="text-[#888888] flex items-center gap-1.5">
              <span
                className="inline-block w-2 h-2 rounded-full border border-[#cccccc]"
                style={{ backgroundColor: AGENT_STATE_TINTS[state] }}
              />
              <span className="text-[#333333] font-bold">
                {stateCounts[state] ?? 0}
              </span>
              {STATE_LABELS[state]}
            </span>
          ))}
        </div>
      )}
    </>
  );
}
