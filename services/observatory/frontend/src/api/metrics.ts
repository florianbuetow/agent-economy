import type { MetricsResponse, GDPHistoryResponse } from "../types";
import { fetchJSON } from "./client";

export function fetchMetrics(): Promise<MetricsResponse> {
  return fetchJSON<MetricsResponse>("/api/metrics");
}

export function fetchGDPHistory(
  window: string = "7d",
  resolution: string = "1h"
): Promise<GDPHistoryResponse> {
  return fetchJSON<GDPHistoryResponse>(
    `/api/metrics/gdp/history?window=${window}&resolution=${resolution}`
  );
}
