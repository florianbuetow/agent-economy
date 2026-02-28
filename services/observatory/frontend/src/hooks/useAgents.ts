import { useEffect, useState } from "react";
import type { AgentListItem } from "../types";
import { fetchAgents } from "../api/agents";

export function useAgents() {
  const [workers, setWorkers] = useState<AgentListItem[]>([]);
  const [posters, setPosters] = useState<AgentListItem[]>([]);

  useEffect(() => {
    fetchAgents("total_earned", "desc", 10).then((res) =>
      setWorkers(res.agents)
    ).catch(() => {});
    fetchAgents("total_spent", "desc", 10).then((res) =>
      setPosters(res.agents)
    ).catch(() => {});
  }, []);

  return { workers, posters };
}
