import { useEffect, useState } from "react";
import type { QuarterlyReportResponse } from "../types";
import { fetchQuarterlyReport } from "../api/quarterly";

interface UseQuarterlyReportResult {
  report: QuarterlyReportResponse | null;
  loading: boolean;
  error: string | null;
}

function currentQuarterLabel(): string {
  const now = new Date();
  const q = Math.ceil((now.getMonth() + 1) / 3);
  return `${now.getFullYear()}-Q${q}`;
}

export function useQuarterlyReport(quarter: string): UseQuarterlyReportResult {
  const [report, setReport] = useState<QuarterlyReportResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);

    fetchQuarterlyReport(quarter)
      .then((data) => {
        if (active) {
          setReport(data);
          setLoading(false);
        }
      })
      .catch((e) => {
        if (active) {
          setError(e instanceof Error ? e.message : "Unknown error");
          setReport(null);
          setLoading(false);
        }
      });

    return () => {
      active = false;
    };
  }, [quarter]);

  return { report, loading, error };
}

export { currentQuarterLabel };
