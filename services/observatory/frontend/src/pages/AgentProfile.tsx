import { useParams, Link } from "react-router-dom";

export default function AgentProfile() {
  const { agentId } = useParams();
  return (
    <div className="flex-1 flex flex-col items-center justify-center font-mono text-text-muted gap-4">
      <div className="text-[11px] uppercase tracking-[2px]">
        Agent Profile — coming soon
      </div>
      <div className="text-[13px] font-bold text-text">{agentId}</div>
      <Link
        to="/observatory"
        className="text-[9px] px-2 py-1 border border-border hover:bg-bg-off"
      >
        ← Back to Observatory
      </Link>
    </div>
  );
}
