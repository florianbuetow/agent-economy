import { useEffect, useState } from "react";
import type { AgentListItem } from "../types";
import { fetchAgents } from "../api/agents";

export function useAgents() {
  const [workers, setWorkers] = useState<AgentListItem[]>([]);
  const [posters, setPosters] = useState<AgentListItem[]>([]);

  useEffect(() => {
    fetchAgents("total_earned", "desc", 10).then((res) =>
      setWorkers(res.agents)
    ).catch((e) => { console.warn("Top workers fetch failed:", e); });
    fetchAgents("spec_quality", "desc", 10).then((res) =>
      setPosters(res.agents)
    ).catch((e) => { console.warn("Top posters fetch failed:", e); });
  }, []);

  return { workers, posters };
}
