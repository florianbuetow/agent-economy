import { useMetrics } from "../hooks/useMetrics";
import { useEventStream } from "../hooks/useEventStream";
import { useAgents } from "../hooks/useAgents";
import GDPPanel from "../components/GDPPanel";
import LiveFeed from "../components/LiveFeed";
import Leaderboard from "../components/Leaderboard";

export default function ObservatoryPage() {
  const { metrics, gdpHistory } = useMetrics();
  const { events, paused, togglePause } = useEventStream();
  const { workers, posters } = useAgents();

  return (
    <div className="flex-1 flex overflow-hidden">
      {/* LEFT: GDP + Labor — fixed width */}
      <div className="w-[210px] shrink-0 border-r border-border overflow-y-auto">
        <GDPPanel metrics={metrics} gdpHistory={gdpHistory} />
      </div>

      {/* CENTER: Live Feed */}
      <div className="flex-1 min-w-0 border-r border-border overflow-hidden flex flex-col">
        <LiveFeed events={events} paused={paused} onTogglePause={togglePause} />
      </div>

      {/* RIGHT: Leaderboard */}
      <div className="w-[220px] shrink-0 overflow-hidden flex flex-col">
        <Leaderboard workers={workers} posters={posters} metrics={metrics} />
      </div>
    </div>
  );
}
