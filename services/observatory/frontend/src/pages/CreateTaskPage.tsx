import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { postJSON } from "../api/client";

interface CreateTaskResponse {
  task_id: string;
}

export default function CreateTaskPage() {
  const navigate = useNavigate();
  const [title, setTitle] = useState("");
  const [spec, setSpec] = useState("");
  const [reward, setReward] = useState<number | "">(100);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
      navigate(`/observatory/tasks/${result.task_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create task");
      setSubmitting(false);
    }
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
