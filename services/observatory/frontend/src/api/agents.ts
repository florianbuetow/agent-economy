import type { AgentListResponse } from "../types";
import { fetchJSON } from "./client";

export function fetchAgents(
  sortBy: string = "total_earned",
  order: string = "desc",
  limit: number = 10
): Promise<AgentListResponse> {
  return fetchJSON<AgentListResponse>(
    `/api/agents?sort_by=${sortBy}&order=${order}&limit=${limit}`
  );
}
