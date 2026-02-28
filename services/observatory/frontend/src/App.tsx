import { Outlet } from "react-router-dom";
import TopNav from "./components/TopNav";
import VitalsBar from "./components/VitalsBar";
import { useMetrics } from "./hooks/useMetrics";
import { useEventStream } from "./hooks/useEventStream";

export default function App() {
  const { metrics } = useMetrics();
  const { connected } = useEventStream();

  return (
    <div className="font-mono text-text bg-bg h-screen flex flex-col">
      <TopNav />
      <VitalsBar metrics={metrics} connected={connected} />
      <Outlet />
    </div>
  );
}
