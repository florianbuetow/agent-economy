import { fetchJSON } from "./client";
import type { TaskDrilldownResponse } from "../types";

export function fetchTaskDrilldown(
  taskId: string,
): Promise<TaskDrilldownResponse> {
  return fetchJSON<TaskDrilldownResponse>(`/api/tasks/${taskId}`);
}
