import type { QuarterlyReportResponse } from "../types";
import { fetchJSON } from "./client";

export function fetchQuarterlyReport(
  quarter: string,
): Promise<QuarterlyReportResponse> {
  return fetchJSON<QuarterlyReportResponse>(
    `/api/quarterly-report?quarter=${encodeURIComponent(quarter)}`,
  );
}
