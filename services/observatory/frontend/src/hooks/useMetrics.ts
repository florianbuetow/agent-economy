import { useEffect, useState } from "react";
import type { MetricsResponse, GDPHistoryResponse } from "../types";
import { fetchMetrics, fetchGDPHistory } from "../api/metrics";

export function useMetrics(pollInterval = 5000) {
  const [metrics, setMetrics] = useState<MetricsResponse | null>(null);
  const [gdpHistory, setGdpHistory] = useState<GDPHistoryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    async function poll() {
      try {
        const data = await fetchMetrics();
        if (active) {
          setMetrics(data);
          setError(null);
        }
      } catch (e) {
        if (active) setError(e instanceof Error ? e.message : "Unknown error");
      }
    }

    poll();
    const id = setInterval(poll, pollInterval);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, [pollInterval]);

  useEffect(() => {
    let active = true;
    fetchGDPHistory("7d", "1h")
      .then((data) => {
        if (active) setGdpHistory(data);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, []);

  return { metrics, gdpHistory, error };
}
