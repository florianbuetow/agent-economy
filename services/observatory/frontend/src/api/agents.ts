import type {
  AgentListResponse,
  AgentProfileResponse,
  AgentFeedResponse,
  AgentEarningsResponse,
} from "../types";
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

export function fetchAgentProfile(
  agentId: string
): Promise<AgentProfileResponse> {
  return fetchJSON<AgentProfileResponse>(`/api/agents/${agentId}`);
}

export function fetchAgentFeed(
  agentId: string,
  params?: {
    limit?: number;
    before?: number;
    role?: string;
    type?: string;
    time?: string;
  }
): Promise<AgentFeedResponse> {
  const searchParams = new URLSearchParams();
  if (params?.limit) searchParams.set("limit", String(params.limit));
  if (params?.before) searchParams.set("before", String(params.before));
  if (params?.role) searchParams.set("role", params.role);
  if (params?.type) searchParams.set("type", params.type);
  if (params?.time) searchParams.set("time", params.time);
  const qs = searchParams.toString();
  return fetchJSON<AgentFeedResponse>(
    `/api/agents/${agentId}/feed${qs ? `?${qs}` : ""}`
  );
}

export function fetchAgentEarnings(
  agentId: string
): Promise<AgentEarningsResponse> {
  return fetchJSON<AgentEarningsResponse>(`/api/agents/${agentId}/earnings`);
}
