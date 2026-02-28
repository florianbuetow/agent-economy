import { Outlet } from "react-router-dom";
import TopNav from "./components/TopNav";
import VitalsBar from "./components/VitalsBar";
import { useMetrics } from "./hooks/useMetrics";
import { useEventStream } from "./hooks/useEventStream";
import { useAgents } from "./hooks/useAgents";
import type { MetricsResponse, GDPHistoryResponse, EventItem, AgentListItem } from "./types";

export interface AppContext {
  metrics: MetricsResponse | null;
  gdpHistory: GDPHistoryResponse | null;
  events: EventItem[];
  paused: boolean;
  togglePause: () => void;
  connected: boolean;
  workers: AgentListItem[];
  posters: AgentListItem[];
}

export default function App() {
  const { metrics, gdpHistory } = useMetrics();
  const { events, connected, paused, togglePause } = useEventStream();
  const { workers, posters } = useAgents();

  const ctx: AppContext = {
    metrics,
    gdpHistory,
    events,
    paused,
    togglePause,
    connected,
    workers,
    posters,
  };

  return (
    <div className="font-mono text-text bg-bg h-screen flex flex-col">
      <TopNav />
      <VitalsBar metrics={metrics} connected={connected} />
      <Outlet context={ctx} />
    </div>
  );
}
