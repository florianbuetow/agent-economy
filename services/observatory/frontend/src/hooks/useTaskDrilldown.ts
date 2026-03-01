import { useState, useEffect } from "react";
import type { TaskDrilldownResponse } from "../types";
import { fetchTaskDrilldown } from "../api/tasks";

const ACTIVE_STATUSES = new Set(["open", "accepted", "submitted", "disputed"]);
const POLL_INTERVAL_MS = 5000;

interface UseTaskDrilldownResult {
  task: TaskDrilldownResponse | null;
  loading: boolean;
  error: boolean;
}

export function useTaskDrilldown(taskId: string): UseTaskDrilldownResult {
  const [task, setTask] = useState<TaskDrilldownResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(false);

    fetchTaskDrilldown(taskId)
      .then((data) => {
        if (active) setTask(data);
      })
      .catch(() => {
        if (active) setError(true);
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [taskId]);

  // Poll while task is in an active state
  useEffect(() => {
    if (!task || !ACTIVE_STATUSES.has(task.status)) return;

    const interval = setInterval(() => {
      fetchTaskDrilldown(taskId)
        .then((data) => setTask(data))
        .catch(() => {});
    }, POLL_INTERVAL_MS);

    return () => clearInterval(interval);
  }, [taskId, task?.status]);

  return { task, loading, error };
}
