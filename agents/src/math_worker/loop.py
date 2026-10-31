"""Main agent loop and phase state machine for the Math Worker."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from collections.abc import Mapping
from enum import Enum
from typing import TYPE_CHECKING, Any

from service_auth.financial_responses import SQLITE_MAX_INTEGER

from base_agent.runtime_intents import RuntimeIntentError
from base_agent.runtime_task_intent_factory import bid_intent, submission_intent
from math_worker.history import AgentHistory, TaskOutcome
from math_worker.parser import parse_bid_amount, parse_solution, parse_task_selection
from math_worker.prompts import (
    BID_AMOUNT_SYSTEM,
    DISPUTE_REBUTTAL_SYSTEM,
    SOLVE_PROBLEM_SYSTEM,
    TASK_SELECTION_SYSTEM,
    build_bid_amount_prompt,
    build_rebuttal_prompt,
    build_solve_prompt,
    build_task_selection_prompt,
)

if TYPE_CHECKING:
    from base_agent.agent import BaseAgent
    from base_agent.runtime_intents import FrozenTaskIntent, NewTaskIntent
    from base_agent.runtime_task_commands import RuntimeTaskCommands
    from math_worker.config import MathWorkerConfig
    from math_worker.llm_client import LLMClient

logger = logging.getLogger(__name__)

_ASSIGNED_STATUSES = ("accepted", "submitted", "disputed")
_CLOSED_STATUSES = frozenset({"cancelled", "expired"})


def _status(task: Mapping[str, Any]) -> str:
    return str(task.get("status", "")).lower()


def _new_job(phase: str) -> dict[str, Any]:
    return {
        "phase": phase,
        "bid_amount": None,
        "solution": None,
        "last_error": None,
        "upload": None,
        "rebuttal": None,
        "outcome": None,
        "payout": None,
    }


def _settlement_amount(task: Mapping[str, Any]) -> int:
    """The amount the escrow settles on: the accepted price when the Board exposes it."""
    for key in ("accepted_price", "accepted_reward", "reward"):
        value = task.get(key)
        if type(value) is int and value > 0:
            return value
    return 0


def _ruling_payout(task: Mapping[str, Any]) -> int:
    """Worker share of a ruling, matching the Bank's ``amount * worker_pct // 100`` split."""
    worker_pct = task.get("worker_pct")
    if type(worker_pct) is not int or not 0 <= worker_pct <= 100:
        logger.warning("Ruled task %s has no valid worker_pct", task.get("task_id"))
        return 0
    return _settlement_amount(task) * worker_pct // 100


class Phase(Enum):
    """Agent lifecycle phases."""

    SCANNING = "scanning"
    BIDDING = "bidding"
    WAITING_FOR_ACCEPTANCE = "waiting_for_acceptance"
    SOLVING = "solving"
    SUBMITTING = "submitting"
    WAITING_FOR_REVIEW = "waiting_for_review"
    DISPUTED = "disputed"
    WAITING_FOR_RULING = "waiting_for_ruling"


class MathWorkerLoop:
    """Deterministic outer loop that calls the LLM at decision points.

    The loop drives a ``BaseAgent`` through the task lifecycle and asks the
    LLM (via ``LLMClient``) for task selection, bid amounts, solutions, and
    dispute rebuttals.
    """

    def __init__(
        self,
        agent: BaseAgent,
        llm: LLMClient,
        config: MathWorkerConfig,
        commands: RuntimeTaskCommands,
    ) -> None:
        self._agent = agent
        self._llm = llm
        self._config = config
        self._commands = commands
        self._history = AgentHistory()
        self._history_restored = False
        self._running = True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Run the agent loop until stopped."""
        logger.info("Math worker loop starting (handle=%s)", self._config.handle)

        while self._running:
            try:
                await self._cycle()
            except asyncio.CancelledError:
                logger.info("Loop cancelled, shutting down")
                self._running = False
            except Exception:
                logger.exception("Unhandled error in agent cycle")
                await asyncio.sleep(self._config.error_backoff_seconds)

        logger.info(
            "Math worker loop stopped. tasks=%d earnings=%d",
            self._history.tasks_completed,
            self._history.total_earnings,
        )

    def stop(self) -> None:
        """Signal the loop to stop after the current cycle."""
        self._running = False

    # ------------------------------------------------------------------
    # One full cycle
    # ------------------------------------------------------------------

    async def _cycle(self) -> None:
        """Resume every assigned job; only when none is in flight, scan and bid."""

        await self._commands.replay_unfinished()
        self._restore_history()

        if await self._resume_jobs():
            return

        # --- SCANNING ---
        task_id = await self._phase_scanning()
        if task_id is None:
            logger.info("No suitable task found, sleeping %ds", self._config.scan_interval_seconds)
            await asyncio.sleep(self._config.scan_interval_seconds)
            return

        # --- BIDDING ---
        bid_intent_result = await self._phase_bidding(task_id)
        if bid_intent_result.state != "completed":
            return

        bid_result = self._completed_body(bid_intent_result)
        bid_id = bid_result["bid_id"]
        if not isinstance(bid_id, str):
            raise ValueError("Completed bid intent has no validated bid ID")
        amount = bid_result.get("amount")
        self._save_job(task_id, {**_new_job("bid"), "bid_amount": amount})

        await self._advance_job(task_id)

    # ------------------------------------------------------------------
    # Job resumption
    # ------------------------------------------------------------------

    async def _resume_jobs(self) -> bool:
        """Advance assigned and journaled unfinished jobs.

        Returns:
            True when at least one job is in flight, so no new bid is placed.
        """
        me = self._require_agent_id()
        task_ids: set[str] = set()
        for status in _ASSIGNED_STATUSES:
            for task in await self._agent.list_tasks(status=status, worker_id=me):
                task_id = task.get("task_id")
                if task.get("worker_id") == me and isinstance(task_id, str):
                    task_ids.add(task_id)
        for task_id, job in self._commands.jobs.all():
            if job.get("outcome") is None:
                task_ids.add(task_id)
        for task_id in sorted(task_ids):
            try:
                await self._advance_job(task_id)
            except Exception:
                # One broken job must not starve the others; it is retried next cycle.
                logger.exception("[JOB] Could not advance task %s; retrying next cycle", task_id)
        return bool(task_ids)

    async def _advance_job(self, task_id: str) -> None:
        """Drive one job from its current public status as far as it can go now."""
        task = await self._agent.get_task(task_id)
        job = self._load_job(task_id)
        if job is None:
            job = {**_new_job(_status(task)), "bid_amount": task.get("accepted_bid_amount")}
            self._save_job(task_id, job)
        if job.get("outcome") is not None:
            return

        if _status(task) == "open":
            # --- WAITING FOR ACCEPTANCE (until the task leaves ``open``) ---
            left_open = await self._phase_waiting_for_acceptance(task_id)
            if left_open is None:
                return
            task = left_open

        status = _status(task)
        if task.get("worker_id") != self._agent.agent_id:
            logger.info("[WAITING] Task %s left bidding without us (%s)", task_id, status)
            self._finish(task, job, TaskOutcome.BID_TIMEOUT, 0)
            return

        if status == "accepted":
            if not await self._deliver(task, job):
                return
            status = "submitted"

        if status == "submitted":
            # --- WAITING FOR REVIEW ---
            if await self._phase_waiting_for_review(task_id) == "timeout":
                logger.info("[REVIEW] Task %s still under review; resuming next cycle", task_id)
                return
            task = await self._agent.get_task(task_id)

        await self._conclude(task, job)

    async def _conclude(self, task: dict[str, Any], job: dict[str, Any]) -> None:
        """Handle a reviewed job: record approval, contest a dispute, or record a ruling."""
        task_id = str(task["task_id"])
        status = _status(task)
        if status == "approved":
            self._finish(task, job, TaskOutcome.APPROVED, _settlement_amount(task))
        elif status == "disputed":
            await self._phase_disputed(task, job)
        elif status == "ruled":
            self._finish_ruled(task, job)
        elif status in _CLOSED_STATUSES:
            logger.warning("[JOB] Task %s closed as %s without payout", task_id, status)
            self._finish(task, job, TaskOutcome.TIMEOUT, 0)

    async def _deliver(self, task: dict[str, Any], job: dict[str, Any]) -> bool:
        """Solve (or reuse the persisted solution), upload once, and submit once."""
        task_id = str(task["task_id"])
        solution = job.get("solution")
        if not isinstance(solution, str):
            # --- SOLVING ---
            solution = await self._phase_solving(task)
            if solution is None:
                job["last_error"] = "solution could not be parsed; retrying next cycle"
                self._save_job(task_id, job)
                return False
            job.update(solution=solution, last_error=None, phase="solved")
            self._save_job(task_id, job)

        # --- SUBMITTING ---
        submission = await self._phase_submitting(task_id, solution, job)
        if submission.state != "completed":
            return False
        job["phase"] = "submitted"
        self._save_job(task_id, job)
        return True

    # ------------------------------------------------------------------
    # Individual phases
    # ------------------------------------------------------------------

    async def _phase_scanning(self) -> str | None:
        """List open tasks, ask LLM to pick one.

        Returns:
            The chosen task_id, or None if no suitable task found.
        """
        logger.info("[SCANNING] Listing open tasks")
        tasks = await self._agent.list_tasks(status="open")

        if not tasks:
            return None

        # Filter by reward range
        eligible = []
        for task in tasks:
            reward = task.get("reward")
            if (
                type(reward) is int
                and 1 <= reward <= SQLITE_MAX_INTEGER
                and self._config.min_reward <= reward <= self._config.max_reward
            ):
                eligible.append(task)
        if not eligible:
            logger.info(
                "No tasks in reward range [%d, %d]",
                self._config.min_reward,
                self._config.max_reward,
            )
            return None

        balance = await self._get_balance()
        prompt = build_task_selection_prompt(eligible, balance)
        response = await self._llm.complete(TASK_SELECTION_SYSTEM, prompt)

        valid_ids = [t["task_id"] for t in eligible if "task_id" in t]
        chosen = parse_task_selection(response.content, valid_ids)

        if chosen is not None:
            logger.info("[SCANNING] LLM selected task: %s", chosen)
        else:
            logger.info("[SCANNING] LLM declined all tasks")

        return chosen

    async def _phase_bidding(self, task_id: str) -> FrozenTaskIntent:
        """Ask LLM for bid amount, submit bid.

        Returns:
            The durable bid intent after one transport attempt.
        """

        async def prepare() -> NewTaskIntent:
            task = await self._agent.get_task(task_id)
            reward = self._require_task_reward(task)
            logger.info("[BIDDING] Deciding bid for task %s (reward=%d)", task_id, reward)

            balance = await self._get_balance()
            prompt = build_bid_amount_prompt(task, balance)
            response = await self._llm.complete(BID_AMOUNT_SYSTEM, prompt)

            amount = parse_bid_amount(response.content, reward)
            if amount is None:
                logger.warning("Could not parse bid amount, using reward as bid: %d", reward)
                amount = reward

            logger.info("[BIDDING] Submitting bid: task=%s amount=%d", task_id, amount)
            return bid_intent(
                task=task,
                actor_id=self._require_agent_id(),
                amount=amount,
                operation_id=f"op-{uuid.uuid4()}",
            )

        return await self._commands.execute(task_id=task_id, slot="bid", prepare=prepare)

    async def _phase_waiting_for_acceptance(self, task_id: str) -> dict[str, Any] | None:
        """Poll up to ``max_poll_attempts`` times for the task to leave ``open``.

        A binding bid is never abandoned: when the budget runs out the job
        stays journaled and the next cycle polls again.

        Returns:
            The first task snapshot whose status is not ``open``, or None.
        """
        logger.info("[WAITING] Polling for bid acceptance: task=%s", task_id)

        for attempt in range(self._config.max_poll_attempts):
            task = await self._agent.get_task(task_id)
            if _status(task) != "open":
                logger.info("[WAITING] Task %s left bidding: %s", task_id, _status(task))
                return task
            if attempt < self._config.max_poll_attempts - 1:
                await asyncio.sleep(self._config.poll_interval_seconds)

        logger.info("[WAITING] Task %s still open; waiting again next cycle", task_id)
        return None

    async def _phase_solving(self, task: dict[str, Any]) -> str | None:
        """Ask LLM to solve the math problem.

        Returns:
            The parsed answer string, or None on failure.
        """
        task_id = task.get("task_id", "unknown")
        logger.info("[SOLVING] Asking LLM to solve task %s", task_id)

        prompt = build_solve_prompt(task)
        response = await self._llm.complete(SOLVE_PROBLEM_SYSTEM, prompt)
        answer = parse_solution(response.content)

        if answer is not None:
            logger.info("[SOLVING] LLM answer for %s: %s", task_id, answer)
        else:
            logger.warning("[SOLVING] Could not parse solution for task %s", task_id)

        return answer

    async def _phase_submitting(
        self, task_id: str, solution: str, job: dict[str, Any]
    ) -> FrozenTaskIntent:
        """Upload the solution at most once and durably submit it for review."""

        async def prepare() -> NewTaskIntent:
            await self._upload_once(task_id, solution, job)
            task = await self._agent.get_task(task_id)
            logger.info("[SUBMITTING] Submitting deliverable for task %s", task_id)
            return submission_intent(
                task=task,
                actor_id=self._require_agent_id(),
                operation_id=f"op-{uuid.uuid4()}",
            )

        return await self._commands.execute(task_id=task_id, slot="submission", prepare=prepare)

    async def _upload_once(self, task_id: str, solution: str, job: dict[str, Any]) -> None:
        """Journal the upload so a crash before submission never duplicates the asset."""
        content = solution.encode("utf-8")
        digest = hashlib.sha256(content).hexdigest()
        filename = f"{task_id}_solution.txt"
        upload = job.get("upload")
        if isinstance(upload, dict) and upload.get("content_sha256") == digest:
            if isinstance(upload.get("asset_id"), str):
                return
            adopted = await self._find_uploaded_asset(task_id, filename, digest)
            if adopted is not None:
                logger.info("[SUBMITTING] Adopting earlier upload %s for %s", adopted, task_id)
                job["upload"] = {**upload, "asset_id": adopted}
                self._save_job(task_id, job)
                return

        job["upload"] = {"filename": filename, "content_sha256": digest, "asset_id": None}
        self._save_job(task_id, job)
        logger.info("[SUBMITTING] Uploading solution for task %s", task_id)
        result = await self._agent.upload_asset(task_id, filename, content)
        asset_id = result.get("asset_id")
        if not isinstance(asset_id, str) or not asset_id:
            raise ValueError("Asset upload response has no asset_id")
        job["upload"] = {"filename": filename, "content_sha256": digest, "asset_id": asset_id}
        self._save_job(task_id, job)

    async def _find_uploaded_asset(self, task_id: str, filename: str, digest: str) -> str | None:
        for asset in await self._agent.list_assets(task_id):
            if (
                asset.get("uploader_id") == self._agent.agent_id
                and asset.get("filename") == filename
                and asset.get("content_hash") == digest
                and isinstance(asset.get("asset_id"), str)
            ):
                return str(asset["asset_id"])
        return None

    async def _phase_waiting_for_review(self, task_id: str) -> str:
        """Poll until the poster approves, disputes, or the poll budget runs out.

        Returns:
            One of: "approved", "disputed", "timeout".
        """
        logger.info("[REVIEW] Waiting for review of task %s", task_id)

        for attempt in range(self._config.max_poll_attempts):
            task = await self._agent.get_task(task_id)
            status = str(task.get("status", "")).lower()

            if status == "approved":
                logger.info("[REVIEW] Task %s approved", task_id)
                return "approved"

            if status == "disputed":
                logger.info("[REVIEW] Task %s disputed", task_id)
                return "disputed"

            if attempt < self._config.max_poll_attempts - 1:
                await asyncio.sleep(self._config.poll_interval_seconds)

        logger.info("[REVIEW] Review poll budget exhausted for task %s", task_id)
        return "timeout"

    async def _phase_disputed(self, task: dict[str, Any], job: dict[str, Any]) -> None:
        """File exactly one rebuttal, then wait for the court ruling."""
        task_id = str(task["task_id"])
        if job.get("phase") != "disputed":
            job["phase"] = "disputed"
            self._save_job(task_id, job)

        if not await self._file_rebuttal_once(task, job):
            return

        ruled = await self._phase_waiting_for_ruling(task_id)
        if ruled is None:
            return
        self._finish_ruled(ruled, job)

    async def _file_rebuttal_once(self, task: dict[str, Any], job: dict[str, Any]) -> bool:
        """Return True once the rebuttal is filed, skipped, or already on record."""
        task_id = str(task["task_id"])
        rebuttal = job.get("rebuttal")
        if isinstance(rebuttal, dict) and rebuttal.get("state") == "filed":
            return True
        if task.get("rebuttal_submitted_at") is not None:
            job["rebuttal"] = {"state": "filed", "dispute_id": task.get("dispute_id")}
            self._save_job(task_id, job)
            return True

        dispute_id = await self._resolve_dispute_id(task_id, task)
        if dispute_id is None:
            logger.warning("No Court dispute found for task %s; skipping rebuttal", task_id)
            return True

        if not isinstance(rebuttal, dict) or rebuttal.get("dispute_id") != dispute_id:
            logger.info("[DISPUTED] Generating rebuttal for task %s", task_id)
            solution = job.get("solution")
            reason = task.get("dispute_reason") or "No reason provided"
            text = await self._compose_rebuttal(
                task, solution if isinstance(solution, str) else "", str(reason)
            )
            rebuttal = {"state": "prepared", "dispute_id": dispute_id, "text": text}
            job["rebuttal"] = rebuttal
            self._save_job(task_id, job)

        logger.info("[DISPUTED] Submitting rebuttal for task %s", task_id)
        try:
            await self._agent.submit_worker_rebuttal(task_id, dispute_id, str(rebuttal["text"]))
        except Exception:
            logger.exception("Failed to submit rebuttal for task %s; retrying next cycle", task_id)
            return False
        job["rebuttal"] = {**rebuttal, "state": "filed"}
        self._save_job(task_id, job)
        return True

    async def _compose_rebuttal(self, task: dict[str, Any], solution: str, reason: str) -> str:
        """Write the rebuttal text for a disputed delivery (LLM-generated for math)."""
        prompt = build_rebuttal_prompt(task, solution, reason)
        response = await self._llm.complete(DISPUTE_REBUTTAL_SYSTEM, prompt)
        return response.content

    async def _phase_waiting_for_ruling(self, task_id: str) -> dict[str, Any] | None:
        """Poll until the court ruling is recorded on the task.

        Returns:
            The ruled task snapshot, or None when the poll budget runs out.
        """
        logger.info("[RULING] Waiting for court ruling on task %s", task_id)

        for attempt in range(self._config.max_poll_attempts):
            task = await self._agent.get_task(task_id)
            if _status(task) == "ruled":
                logger.info(
                    "[RULING] Ruling received for %s: payout=%d",
                    task_id,
                    _ruling_payout(task),
                )
                return task

            if attempt < self._config.max_poll_attempts - 1:
                await asyncio.sleep(self._config.poll_interval_seconds)

        logger.info("[RULING] No ruling yet for task %s; resuming next cycle", task_id)
        return None

    # ------------------------------------------------------------------
    # Job journal and history
    # ------------------------------------------------------------------

    def _load_job(self, task_id: str) -> dict[str, Any] | None:
        job = self._commands.jobs.load(task_id)
        return job if isinstance(job, dict) else None

    def _save_job(self, task_id: str, job: Mapping[str, Any]) -> None:
        self._commands.jobs.save(task_id, job)

    def _restore_history(self) -> None:
        """Rebuild the outcome history from finished journaled jobs once per process."""
        if self._history_restored:
            return
        self._history_restored = True
        for task_id, job in self._commands.jobs.all():
            outcome = job.get("outcome")
            if outcome is None:
                continue
            self._history.record(
                task_id=task_id,
                title=str(job.get("title", "")),
                reward=int(job.get("reward", 0)),
                bid_amount=int(job.get("bid_amount") or 0),
                outcome=TaskOutcome(outcome),
                solution=job.get("solution"),
                payout=int(job.get("payout", 0)),
            )

    def _finish_ruled(self, task: dict[str, Any], job: dict[str, Any]) -> None:
        payout = _ruling_payout(task)
        outcome = TaskOutcome.DISPUTED_WON if payout > 0 else TaskOutcome.DISPUTED_LOST
        self._finish(task, job, outcome, payout)

    def _finish(
        self,
        task: Mapping[str, Any],
        job: dict[str, Any],
        outcome: TaskOutcome,
        payout: int,
    ) -> None:
        """Persist the terminal outcome first, then record it exactly once."""
        if job.get("outcome") is not None:
            return
        task_id = str(task["task_id"])
        bid_amount = job.get("bid_amount")
        if type(bid_amount) is not int:
            bid_amount = task.get("accepted_bid_amount")
        bid_amount = bid_amount if type(bid_amount) is int else 0
        title = str(task.get("title", ""))
        reward = task.get("reward")
        reward = reward if type(reward) is int else 0
        job.update(
            phase="finished",
            outcome=outcome.value,
            payout=payout,
            bid_amount=bid_amount,
            title=title,
            reward=reward,
        )
        self._save_job(task_id, job)
        self._history.record(
            task_id=task_id,
            title=title,
            reward=reward,
            bid_amount=bid_amount,
            outcome=outcome,
            solution=job.get("solution"),
            payout=payout,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _require_agent_id(self) -> str:
        agent_id = self._agent.agent_id
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise ValueError("Math worker must be registered before task mutation")
        return agent_id

    @staticmethod
    def _require_task_reward(task: Mapping[str, Any]) -> int:
        reward = task.get("reward")
        if type(reward) is not int or not 1 <= reward <= SQLITE_MAX_INTEGER:
            raise RuntimeIntentError(
                "invalid_input",
                "task reward must be a positive SQLite-safe integer",
            )
        return reward

    @staticmethod
    def _completed_body(intent: FrozenTaskIntent) -> Mapping[str, Any]:
        result = intent.result
        if result is None:
            raise ValueError("Completed task intent has no normalized result")
        body = result.get("body")
        if not isinstance(body, Mapping):
            raise ValueError("Completed task intent has no normalized response body")
        return body

    async def _resolve_dispute_id(self, task_id: str, task: dict[str, Any]) -> str | None:
        """Resolve the Court dispute id for a disputed task."""
        task_dispute_id = task.get("dispute_id")
        if isinstance(task_dispute_id, str) and task_dispute_id:
            return task_dispute_id

        disputes = await self._agent.list_disputes(task_id=task_id)
        if not disputes:
            return None
        dispute_id = disputes[0].get("dispute_id")
        if isinstance(dispute_id, str) and dispute_id:
            return dispute_id
        return None

    async def _get_balance(self) -> int:
        """Fetch the agent's current balance, defaulting to 0 on error."""
        try:
            result = await self._agent.get_balance()
            return int(result.get("balance", 0))
        except Exception:
            logger.warning("Could not fetch balance, using 0")
            return 0
