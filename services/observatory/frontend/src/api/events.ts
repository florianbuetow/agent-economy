import type { EventsResponse } from "../types";
import { fetchJSON } from "./client";

export function fetchEvents(limit: number = 20): Promise<EventsResponse> {
  return fetchJSON<EventsResponse>(`/api/events?limit=${limit}`);
}
