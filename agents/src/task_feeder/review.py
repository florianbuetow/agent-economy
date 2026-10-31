"""Review loop for auto-approving or disputing submitted feeder tasks."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from service_auth.financial_identity import operation_id_for

from base_agent.runtime_task_intent_factory import review_intent
from task_feeder.resilience import RECOVERABLE_DEPENDENCY_ERRORS
from task_feeder.workloads.classification import (
    is_classification_task,
    parse_task_spec,
    review_submission,
)

if TYPE_CHECKING:
    from base_agent.agent import BaseAgent
    from base_agent.runtime_intents import NewTaskIntent
    from base_agent.runtime_task_commands import RuntimeTaskCommands
    from task_feeder.reader import RawTask

logger = logging.getLogger(__name__)


def check_answer(submitted: str, solutions: Sequence[str]) -> bool:
    """Return True when submitted answer matches any expected solution."""
    normalized_submitted = submitted.strip().lower()
    if normalized_submitted == "":
        return False
    if not solutions:
        return False

    normalized_solutions = [solution.strip().lower() for solution in solutions]
    return normalized_submitted in normalized_solutions


class ReviewLoop:
    """Poll for submitted tasks and auto-review known feeder tasks."""

    def __init__(
        self,
        agent: BaseAgent,
        task_map: dict[str, RawTask],
        commands: RuntimeTaskCommands,
    ) -> None:
        self._agent = agent
        self._task_map = task_map
        self._commands = commands
        self._running = True

    async def review_one(self, task_id: str) -> str:
        """Review a single submitted task and approve or dispute it."""
        if task_id == "":
            raise ValueError("task_id must be a nonempty string")

        async def prepare() -> NewTaskIntent:
            if task_id not in self._task_map:
                raise LookupError(task_id)

            payload = await self._agent.get_task(task_id)
            raw_task = self._task_map[task_id]
            if is_classification_task(raw_task):
                action, reason = await self._classification_decision(task_id, payload, raw_task)
            else:
                action, reason = await self._answer_decision(task_id, payload, raw_task)
            source_version = payload.get("lifecycle_version")
            if type(source_version) is not int:
                raise ValueError("lifecycle_version must be an integer")
            operation_id = operation_id_for("task-review", (task_id, str(source_version)))
            actor_id = self._agent.agent_id
            if not isinstance(actor_id, str) or actor_id == "":
                raise ValueError("agent must be registered before reviewing a task")
            return review_intent(
                task=payload,
                actor_id=actor_id,
                action=action,
                reason=reason,
                operation_id=operation_id,
            )

        intent = await self._commands.execute(
            task_id=task_id,
            slot="review-decision",
            prepare=prepare,
        )
        if intent.state != "completed":
            return intent.state
        if intent.action == "approve_task":
            return "approved"
        return "disputed"

    async def _answer_decision(
        self, task_id: str, payload: Mapping[str, Any], raw_task: RawTask
    ) -> tuple[str, str | None]:
        """Single-answer workloads (math): compare the answer with the solutions."""
        submitted_answer = _payload_answer(payload)
        if submitted_answer == "":
            submitted_answer = await self._fetch_submitted_answer_from_assets(task_id)
        if check_answer(submitted_answer, raw_task.solutions):
            return "approve_task", None
        return (
            "dispute_task",
            f"Expected one of {list(raw_task.solutions)!r} but got {submitted_answer!r}",
        )

    async def _classification_decision(
        self, task_id: str, payload: Mapping[str, Any], raw_task: RawTask
    ) -> tuple[str, str | None]:
        """Multi-output workloads: read the worker's named outputs and apply the spec rule."""
        data = parse_task_spec(raw_task.spec)
        worker_id = payload.get("worker_id")
        latest: dict[str, str] = {}
        for asset in await self._agent.list_assets(task_id):
            projection = _asset_projection(asset)
            name = projection.get("filename")
            asset_id = projection.get("asset_id")
            uploader = projection.get("uploader_id")
            if uploader is not None and uploader != worker_id:
                continue
            if isinstance(name, str) and isinstance(asset_id, str) and asset_id != "":
                latest[name] = asset_id

        async def read(name: str) -> bytes | None:
            asset_id = latest.get(name)
            if asset_id is None:
                return None
            return _asset_content(await self._agent.download_asset(task_id, asset_id))

        verdict = review_submission(
            raw_task,
            labels_file=await read(data.labels_file),
            rationale_file=await read(data.rationale_file),
        )
        if verdict.approved:
            return "approve_task", None
        return "dispute_task", verdict.reason

    async def _fetch_submitted_answer_from_assets(self, task_id: str) -> str:
        """Read the submitted answer from the worker's uploaded asset.

        The real Task Board API has no ``submitted_answer``/``submission``/
        ``deliverable`` field on the task payload — ``MathWorkerLoop`` uploads
        its solution as a file asset (``{task_id}_solution.txt``, see
        ``math_worker/loop.py:_phase_submitting``). Falls back here only when
        the payload-based fields above are empty, so callers that already
        provide a task payload with those fields (e.g. existing unit tests)
        are unaffected.
        """
        assets = await self._agent.list_assets(task_id)
        if not assets:
            return ""

        latest = _asset_projection(assets[-1])
        asset_id = latest.get("asset_id")
        if not isinstance(asset_id, str) or asset_id == "":
            raise ValueError("asset_id must be a nonempty string")

        content = await self._agent.download_asset(task_id, asset_id)
        return _asset_content(content).decode("utf-8").strip()

    async def run(self, interval_seconds: int, retry_backoff_seconds: int) -> None:
        """Continuously review submitted tasks for this poster."""
        while self._running:
            try:
                await self._review_pass()
            except RECOVERABLE_DEPENDENCY_ERRORS:
                logger.exception(
                    "Review pass failed on a dependency error; retrying in %ss",
                    retry_backoff_seconds,
                )
                await asyncio.sleep(retry_backoff_seconds)
                continue
            await asyncio.sleep(interval_seconds)

    async def _review_pass(self) -> None:
        replayed = await self._commands.replay_unfinished()
        replayed_task_ids = {
            intent.task_id for intent in replayed if intent.slot == "review-decision"
        }
        tasks = await self._agent.list_tasks(
            status="submitted",
            poster_id=self._agent.agent_id,
        )
        for task in tasks:
            task_id_value = task.get("task_id")
            if not isinstance(task_id_value, str) or task_id_value == "":
                logger.error("Ignoring submitted-task projection with invalid task_id")
                continue
            task_id = task_id_value
            if task.get("status") != "submitted":
                continue
            if task_id in replayed_task_ids:
                continue
            if task_id not in self._task_map:
                continue
            try:
                await self.review_one(task_id)
            except Exception:
                logger.exception("Failed to review task_id=%s", task_id)

    def stop(self) -> None:
        """Signal the review loop to stop."""
        self._running = False


def _payload_answer(payload: Mapping[str, Any]) -> str:
    """Read only documented answer projections without coercing source values."""
    for key in ("submitted_answer", "submission", "deliverable"):
        value = payload.get(key)
        if value is None:
            continue
        if key == "submitted_answer":
            if not isinstance(value, str):
                raise ValueError("submitted_answer must be a string")
            if value != "":
                return value
            continue
        if not isinstance(value, Mapping):
            raise ValueError(f"{key} must be an object")
        answer = value.get("answer")
        if answer is None:
            continue
        if not isinstance(answer, str):
            raise ValueError(f"{key}.answer must be a string")
        if answer != "":
            return answer
    return ""


def _asset_projection(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("asset projection must be an object")
    return value


def _asset_content(value: object) -> bytes:
    if not isinstance(value, bytes):
        raise ValueError("asset content must be bytes")
    return value
