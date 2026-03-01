import { useState, useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { postJSON } from "../api/client";
import { fetchTaskDrilldown } from "../api/tasks";

interface CreateTaskResponse {
  task_id: string;
}

const POLL_INTERVAL_MS = 2000;

export default function CreateTaskPage() {
  const navigate = useNavigate();
  const [title, setTitle] = useState("");
  const [spec, setSpec] = useState("");
  const [reward, setReward] = useState<number | "">(100);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [waitingForTaskId, setWaitingForTaskId] = useState<string | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Poll the Observatory DB until the task appears, then redirect
  useEffect(() => {
    if (!waitingForTaskId) return;

    function poll() {
      fetchTaskDrilldown(waitingForTaskId!)
        .then(() => {
          navigate(`/observatory/tasks/${waitingForTaskId}`);
        })
        .catch(() => {
          // Not in DB yet — keep polling
        });
    }

    // First attempt immediately
    poll();
    intervalRef.current = setInterval(poll, POLL_INTERVAL_MS);

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [waitingForTaskId, navigate]);

  const canSubmit = title.trim() !== "" && spec.trim() !== "" && reward !== "" && reward > 0;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const result = await postJSON<CreateTaskResponse>("/api/demo/tasks", {
        title: title.trim(),
        spec: spec.trim(),
        reward: Number(reward),
      });
      setWaitingForTaskId(result.task_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create task");
      setSubmitting(false);
    }
  }

  if (waitingForTaskId) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-4">
        <div className="flex gap-1">
          {[0, 1, 2].map((i) => (
            <div
              key={i}
              className="w-2 h-2 bg-text-muted rounded-full animate-bounce"
              style={{ animationDelay: `${i * 150}ms` }}
            />
          ))}
        </div>
        <div className="font-mono text-[11px] text-text-muted uppercase tracking-[2px]">
          Task posted — waiting for sync
        </div>
        <div className="font-mono text-[10px] text-text-faint">
          {waitingForTaskId}
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-xl mx-auto">
        <h1 className="font-mono text-xs font-bold uppercase tracking-[2px] text-text mb-6">
          Create Task
        </h1>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block font-mono text-[10px] uppercase tracking-[1px] text-text-muted mb-1">
              Title
            </label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="e.g. Write a sorting algorithm"
              className="w-full bg-bg border border-border px-3 py-2 font-mono text-xs text-text placeholder:text-text-muted focus:outline-none focus:border-text"
            />
          </div>
          <div>
            <label className="block font-mono text-[10px] uppercase tracking-[1px] text-text-muted mb-1">
              Specification
            </label>
            <textarea
              value={spec}
              onChange={(e) => setSpec(e.target.value)}
              placeholder="Describe what the agent should deliver..."
              rows={8}
              className="w-full bg-bg border border-border px-3 py-2 font-mono text-xs text-text placeholder:text-text-muted focus:outline-none focus:border-text resize-y"
            />
          </div>
          <div>
            <label className="block font-mono text-[10px] uppercase tracking-[1px] text-text-muted mb-1">
              Reward (coins)
            </label>
            <input
              type="number"
              min={1}
              value={reward}
              onChange={(e) => setReward(e.target.value === "" ? "" : Number(e.target.value))}
              className="w-32 bg-bg border border-border px-3 py-2 font-mono text-xs text-text focus:outline-none focus:border-text"
            />
          </div>
          {error && (
            <div className="border border-red-400 bg-red-950/20 px-3 py-2 font-mono text-[10px] text-red-400">
              {error}
            </div>
          )}
          <button
            type="submit"
            disabled={!canSubmit || submitting}
            className="px-4 py-2 font-mono text-[10px] uppercase tracking-[1px] border border-text bg-text text-bg hover:bg-transparent hover:text-text transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {submitting ? "Creating..." : "Create Task"}
          </button>
        </form>
      </div>
    </div>
  );
}
