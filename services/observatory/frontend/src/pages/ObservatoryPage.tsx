import { useOutletContext } from "react-router-dom";
import type { AppContext } from "../App";
import GDPPanel from "../components/GDPPanel";
import ChartPanel from "../components/ChartPanel";
import LiveFeed from "../components/LiveFeed";
import Leaderboard from "../components/Leaderboard";

export default function ObservatoryPage() {
  const { metrics, gdpHistory, events, paused, togglePause, workers, posters } =
    useOutletContext<AppContext>();

  return (
    <div className="flex-1 flex overflow-hidden">
      <div className="w-[210px] shrink-0 border-r border-border overflow-y-auto">
        <GDPPanel metrics={metrics} gdpHistory={gdpHistory} />
      </div>
      <div className="flex-1 min-w-0 border-r border-border overflow-hidden flex flex-col">
        <ChartPanel gdpHistory={gdpHistory} />
      </div>
      <div className="flex-1 min-w-0 border-r border-border overflow-hidden flex flex-col">
        <LiveFeed events={events} paused={paused} onTogglePause={togglePause} />
      </div>
      <div className="w-[220px] shrink-0 overflow-hidden flex flex-col">
        <Leaderboard workers={workers} posters={posters} metrics={metrics} />
      </div>
    </div>
  );
}
