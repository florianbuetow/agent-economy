"""Classification worker loop: the math worker's durable job lifecycle with a new workload.

``MathWorkerLoop`` owns the restart-safe lifecycle (bid intent, acceptance
wait, submission intent, review wait, dispute rebuttal, ruling). This loop
replaces only the workload-specific decisions:

* scanning: classification tasks only, skipping task types the adapted
  policy abstains from;
* bidding: the price comes from the outcome-adapted policy, not an LLM;
* solving: the configured strategy (LLM or keyword table);
* uploading: two named outputs, each journaled so a restart never duplicates;
* finishing: the outcome is journaled with its task type and order, which is
  the evidence the adaptation folds over.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from base_agent.runtime_task_intent_factory import bid_intent
from classification_worker.adaptation import TypeState, adapt, outcome_records
from classification_worker.solvers import Answer, build_outputs
from math_worker.config import MathWorkerConfig
from math_worker.loop import MathWorkerLoop
from task_feeder.workloads.classification import (
    ClassificationSpecError,
    ClassificationTaskData,
    parse_task_spec,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from base_agent.agent import BaseAgent
    from base_agent.runtime_intents import FrozenTaskIntent, NewTaskIntent
    from base_agent.runtime_task_commands import RuntimeTaskCommands
    from classification_worker.adaptation import PricingRule
    from classification_worker.solvers import Solver
    from math_worker.history import TaskOutcome
    from math_worker.llm_client import LLMClient, LLMResponse

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LoopSettings:
    """Lifecycle timing, reward range and pricing rule for one worker profile."""

    handle: str
    scan_interval_seconds: int
    poll_interval_seconds: int
    max_poll_attempts: int
    error_backoff_seconds: int
    min_reward: int
    max_reward: int
    pricing: PricingRule


class _NoCompletion:
    """Guard: every LLM call site of the base loop is overridden here."""

    async def complete(self, system_prompt: str, user_prompt: str) -> LLMResponse:
        del system_prompt, user_prompt
        raise RuntimeError("classification worker must not use the math LLM path")

    async def close(self) -> None:
        return None


def task_data(task: Mapping[str, Any]) -> ClassificationTaskData | None:
    """Parse a public task's classification data, or None for other workloads."""
    spec = task.get("spec")
    if not isinstance(spec, str):
        return None
    try:
        return parse_task_spec(spec)
    except ClassificationSpecError:
        return None


class ClassificationWorkerLoop(MathWorkerLoop):
    """Bid, solve and deliver text-classification tasks with outcome-based pricing."""

    def __init__(
        self,
        agent: BaseAgent,
        solver: Solver,
        settings: LoopSettings,
        commands: RuntimeTaskCommands,
    ) -> None:
        super().__init__(
            agent=agent,
            llm=cast("LLMClient", _NoCompletion()),
            config=MathWorkerConfig(
                handle=settings.handle,
                scan_interval_seconds=settings.scan_interval_seconds,
                poll_interval_seconds=settings.poll_interval_seconds,
                max_poll_attempts=settings.max_poll_attempts,
                error_backoff_seconds=settings.error_backoff_seconds,
                min_reward=settings.min_reward,
                max_reward=settings.max_reward,
            ),
            commands=commands,
        )
        self._solver = solver
        self._rule = settings.pricing

    def policy_state(self, task_type: str) -> TypeState:
        """Current adapted state for a task type, folded from the durable journal."""
        return adapt(self._rule, outcome_records(self._commands.jobs.all()), task_type)

    # ------------------------------------------------------------------
    # Workload-specific phases
    # ------------------------------------------------------------------

    async def _phase_scanning(self) -> str | None:
        known = {task_id for task_id, _job in self._commands.jobs.all()}
        candidates: list[tuple[int, str]] = []
        for task in await self._agent.list_tasks(status="open"):
            task_id = task.get("task_id")
            reward = task.get("reward")
            if not isinstance(task_id, str) or task_id in known or type(reward) is not int:
                continue
            if not self._config.min_reward <= reward <= self._config.max_reward:
                continue
            data = task_data(task)
            if data is None:
                continue
            state = self.policy_state(data.task_type)
            if not state.bidding:
                logger.info(
                    "[SCANNING] Abstaining from %s task %s: %d/%d decided outcomes succeeded",
                    data.task_type,
                    task_id,
                    state.successes,
                    state.decided,
                )
                continue
            candidates.append((reward, task_id))
        if not candidates:
            return None
        return max(candidates)[1]

    async def _phase_bidding(self, task_id: str) -> FrozenTaskIntent:
        async def prepare() -> NewTaskIntent:
            task = await self._agent.get_task(task_id)
            reward = self._require_task_reward(task)
            data = task_data(task)
            if data is None:
                raise ClassificationSpecError(f"task {task_id} is not a classification task")
            state = self.policy_state(data.task_type)
            amount = state.bid_amount(reward=reward)
            logger.info(
                "[BIDDING] task=%s type=%s reward=%d bid_pct=%d amount=%d",
                task_id,
                data.task_type,
                reward,
                state.bid_pct,
                amount,
            )
            return bid_intent(
                task=task,
                actor_id=self._require_agent_id(),
                amount=amount,
                operation_id=f"op-{uuid.uuid4()}",
            )

        return await self._commands.execute(task_id=task_id, slot="bid", prepare=prepare)

    async def _phase_solving(self, task: dict[str, Any]) -> str | None:
        data = task_data(task)
        if data is None:
            logger.warning("[SOLVING] Task %s has no classification data", task.get("task_id"))
            return None
        answer = await self._solver.solve(data)
        return answer.to_json() if answer is not None else None

    async def _upload_once(self, task_id: str, solution: str, job: dict[str, Any]) -> None:
        """Upload each named output at most once, journaling before and after each upload."""
        data = task_data(await self._agent.get_task(task_id))
        if data is None:
            raise ClassificationSpecError(f"task {task_id} is not a classification task")
        uploads_value = job.get("uploads")
        uploads: dict[str, Any] = dict(uploads_value) if isinstance(uploads_value, dict) else {}
        for filename, content in build_outputs(data, Answer.from_json(solution)):
            digest = hashlib.sha256(content).hexdigest()
            record = uploads.get(filename)
            if isinstance(record, dict) and record.get("content_sha256") == digest:
                if isinstance(record.get("asset_id"), str):
                    continue
                adopted = await self._find_uploaded_asset(task_id, filename, digest)
                if adopted is not None:
                    uploads[filename] = {"content_sha256": digest, "asset_id": adopted}
                    job["uploads"] = uploads
                    self._save_job(task_id, job)
                    continue
            uploads[filename] = {"content_sha256": digest, "asset_id": None}
            job["uploads"] = uploads
            self._save_job(task_id, job)
            logger.info("[SUBMITTING] Uploading %s for task %s", filename, task_id)
            result = await self._agent.upload_asset(task_id, filename, content)
            asset_id = result.get("asset_id")
            if not isinstance(asset_id, str) or not asset_id:
                raise ValueError("Asset upload response has no asset_id")
            uploads[filename] = {"content_sha256": digest, "asset_id": asset_id}
            job["uploads"] = uploads
            self._save_job(task_id, job)

    async def _compose_rebuttal(self, task: dict[str, Any], solution: str, reason: str) -> str:
        """Deterministic rebuttal that argues from the published acceptance rule."""
        data = task_data(task)
        if data is None or not solution:
            return f"The delivery followed the specification. Poster's reason: {reason}"
        answer = Answer.from_json(solution)
        assigned = ", ".join(f"{item_id}={answer.labels[item_id]}" for item_id in data.input_ids)
        return (
            f"The specification requires {data.labels_file} covering every input id with an "
            f"allowed label and at least {data.min_correct} of {len(data.inputs)} labels "
            f"matching the reference. We delivered {data.labels_file} ({assigned}) using only "
            f"the allowed labels ({', '.join(data.labels)}) and a nonempty "
            f"{data.rationale_file}. The label definitions in the specification are the only "
            "published criteria; where an input fits more than one definition the "
            f"specification is ambiguous. Poster's reason: {reason}"
        )

    def _finish(
        self,
        task: Mapping[str, Any],
        job: dict[str, Any],
        outcome: TaskOutcome,
        payout: int,
    ) -> None:
        if job.get("outcome") is not None:
            return
        data = task_data(task)
        seqs = [
            existing.get("outcome_seq")
            for _task_id, existing in self._commands.jobs.all()
            if type(existing.get("outcome_seq")) is int
        ]
        job["task_type"] = data.task_type if data is not None else "unknown"
        job["outcome_seq"] = 1 + max((int(str(seq)) for seq in seqs), default=0)
        super()._finish(task, job, outcome, payout)
