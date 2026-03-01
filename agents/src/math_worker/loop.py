"""Main agent loop and phase state machine for the Math Worker."""

from __future__ import annotations

import asyncio
import logging
from enum import Enum
from typing import Any

from math_worker.config import MathWorkerConfig
from math_worker.history import AgentHistory, TaskOutcome
from math_worker.llm_client import LLMClient
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

from base_agent.agent import BaseAgent

logger = logging.getLogger(__name__)


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
    ) -> None:
        self._agent = agent
        self._llm = llm
        self._config = config
        self._history = AgentHistory()
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
        """Execute one scan → bid → solve → submit cycle."""

        # --- SCANNING ---
        task_id = await self._phase_scanning()
        if task_id is None:
            logger.info("No suitable task found, sleeping %ds", self._config.scan_interval_seconds)
            await asyncio.sleep(self._config.scan_interval_seconds)
            return

        # --- BIDDING ---
        task = await self._agent.get_task(task_id)
        bid_result = await self._phase_bidding(task)
        if bid_result is None:
            return

        bid_id: str = bid_result["bid_id"]

        # --- WAITING FOR ACCEPTANCE ---
        accepted = await self._phase_waiting_for_acceptance(task_id, bid_id)
        if not accepted:
            self._history.record(
                task_id=task_id,
                title=task.get("title", ""),
                reward=task.get("reward", 0),
                bid_amount=bid_result.get("amount", 0),
                outcome=TaskOutcome.BID_TIMEOUT,
                solution=None,
                payout=0,
            )
            return

        # --- SOLVING ---
        task = await self._agent.get_task(task_id)
        solution = await self._phase_solving(task)
        if solution is None:
            self._history.record(
                task_id=task_id,
                title=task.get("title", ""),
                reward=task.get("reward", 0),
                bid_amount=bid_result.get("amount", 0),
                outcome=TaskOutcome.ERROR,
                solution=None,
                payout=0,
            )
            return

        # --- SUBMITTING ---
        await self._phase_submitting(task_id, solution)

        # --- WAITING FOR REVIEW ---
        review_outcome = await self._phase_waiting_for_review(task_id)

        if review_outcome == "APPROVED":
            self._history.record(
                task_id=task_id,
                title=task.get("title", ""),
                reward=task.get("reward", 0),
                bid_amount=bid_result.get("amount", 0),
                outcome=TaskOutcome.APPROVED,
                solution=solution,
                payout=task.get("reward", 0),
            )
        elif review_outcome == "DISPUTED":
            await self._phase_disputed(task, solution)
        else:
            # Timeout = auto-approve
            self._history.record(
                task_id=task_id,
                title=task.get("title", ""),
                reward=task.get("reward", 0),
                bid_amount=bid_result.get("amount", 0),
                outcome=TaskOutcome.APPROVED,
                solution=solution,
                payout=task.get("reward", 0),
            )

    # ------------------------------------------------------------------
    # Individual phases
    # ------------------------------------------------------------------

    async def _phase_scanning(self) -> str | None:
        """List open tasks, ask LLM to pick one.

        Returns:
            The chosen task_id, or None if no suitable task found.
        """
        logger.info("[SCANNING] Listing open tasks")
        tasks = await self._agent.list_tasks(status="BIDDING")

        if not tasks:
            return None

        # Filter by reward range
        eligible = [
            t
            for t in tasks
            if self._config.min_reward <= t.get("reward", 0) <= self._config.max_reward
        ]
        if not eligible:
            logger.info("No tasks in reward range [%d, %d]", self._config.min_reward, self._config.max_reward)
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

    async def _phase_bidding(self, task: dict[str, Any]) -> dict[str, Any] | None:
        """Ask LLM for bid amount, submit bid.

        Returns:
            The bid response dict, or None on failure.
        """
        task_id: str = task["task_id"]
        reward: int = task.get("reward", 0)
        logger.info("[BIDDING] Deciding bid for task %s (reward=%d)", task_id, reward)

        balance = await self._get_balance()
        prompt = build_bid_amount_prompt(task, balance)
        response = await self._llm.complete(BID_AMOUNT_SYSTEM, prompt)

        amount = parse_bid_amount(response.content, reward)
        if amount is None:
            # Fallback: bid the full reward
            logger.warning("Could not parse bid amount, using reward as bid: %d", reward)
            amount = reward

        logger.info("[BIDDING] Submitting bid: task=%s amount=%d", task_id, amount)
        result = await self._agent.submit_bid(task_id, amount)
        return result

    async def _phase_waiting_for_acceptance(
        self,
        task_id: str,
        bid_id: str,
    ) -> bool:
        """Poll until our bid is accepted or the bidding period expires.

        Returns:
            True if bid was accepted, False if timed out.
        """
        logger.info("[WAITING] Polling for bid acceptance: task=%s bid=%s", task_id, bid_id)

        for attempt in range(self._config.max_poll_attempts):
            task = await self._agent.get_task(task_id)
            status = task.get("status", "")

            if status in ("IN_PROGRESS", "EXECUTION"):
                # Our bid was accepted (task moved to execution)
                worker_id = task.get("worker_id")
                if worker_id == self._agent.agent_id:
                    logger.info("[WAITING] Bid accepted! task=%s", task_id)
                    return True
                # Someone else's bid was accepted
                logger.info("[WAITING] Another agent's bid accepted for task %s", task_id)
                return False

            if status in ("CANCELLED", "COMPLETED", "APPROVED", "FAILED"):
                logger.info("[WAITING] Task %s moved to terminal state: %s", task_id, status)
                return False

            if attempt < self._config.max_poll_attempts - 1:
                await asyncio.sleep(self._config.poll_interval_seconds)

        logger.info("[WAITING] Bid acceptance timed out for task %s", task_id)
        return False

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

    async def _phase_submitting(self, task_id: str, solution: str) -> None:
        """Upload the solution as an asset and submit for review."""
        logger.info("[SUBMITTING] Uploading solution for task %s", task_id)

        content = solution.encode("utf-8")
        filename = f"{task_id}_solution.txt"
        await self._agent.upload_asset(task_id, filename, content)

        logger.info("[SUBMITTING] Submitting deliverable for task %s", task_id)
        await self._agent.submit_deliverable(task_id)

    async def _phase_waiting_for_review(self, task_id: str) -> str:
        """Poll until the poster approves, disputes, or the review times out.

        Returns:
            One of: "APPROVED", "DISPUTED", "TIMEOUT".
        """
        logger.info("[REVIEW] Waiting for review of task %s", task_id)

        for attempt in range(self._config.max_poll_attempts):
            task = await self._agent.get_task(task_id)
            status = task.get("status", "")

            if status in ("APPROVED", "COMPLETED"):
                logger.info("[REVIEW] Task %s approved", task_id)
                return "APPROVED"

            if status in ("DISPUTED", "IN_DISPUTE"):
                logger.info("[REVIEW] Task %s disputed", task_id)
                return "DISPUTED"

            if attempt < self._config.max_poll_attempts - 1:
                await asyncio.sleep(self._config.poll_interval_seconds)

        logger.info("[REVIEW] Review timed out for task %s, assuming auto-approve", task_id)
        return "TIMEOUT"

    async def _phase_disputed(
        self,
        task: dict[str, Any],
        solution: str,
    ) -> None:
        """Generate a rebuttal and wait for the court ruling."""
        task_id = task.get("task_id", "unknown")
        logger.info("[DISPUTED] Generating rebuttal for task %s", task_id)

        # Get dispute reason from task metadata
        reason = task.get("dispute_reason", "No reason provided")

        prompt = build_rebuttal_prompt(task, solution, reason)
        response = await self._llm.complete(DISPUTE_REBUTTAL_SYSTEM, prompt)
        rebuttal = response.content

        logger.info("[DISPUTED] Filing rebuttal for task %s", task_id)
        # The court filing mechanism depends on the court service API.
        # For now, we log the rebuttal; the file_claim method will submit it.
        try:
            await self._agent.file_claim(task_id, rebuttal)
        except Exception:
            logger.exception("Failed to file claim for task %s", task_id)

        # Wait for ruling
        ruling_outcome = await self._phase_waiting_for_ruling(task_id)

        payout = ruling_outcome.get("payout", 0)
        won = payout > 0
        self._history.record(
            task_id=task_id,
            title=task.get("title", ""),
            reward=task.get("reward", 0),
            bid_amount=0,
            outcome=TaskOutcome.DISPUTED_WON if won else TaskOutcome.DISPUTED_LOST,
            solution=solution,
            payout=payout,
        )

    async def _phase_waiting_for_ruling(self, task_id: str) -> dict[str, Any]:
        """Poll until the court issues a ruling.

        Returns:
            A dict with at least a ``payout`` key.
        """
        logger.info("[RULING] Waiting for court ruling on task %s", task_id)

        for attempt in range(self._config.max_poll_attempts):
            task = await self._agent.get_task(task_id)
            status = task.get("status", "")

            if status in ("RULED", "COMPLETED", "APPROVED", "RESOLVED"):
                payout = task.get("worker_payout", task.get("reward", 0))
                logger.info("[RULING] Ruling received for %s: payout=%s", task_id, payout)
                return {"payout": payout, "status": status}

            if attempt < self._config.max_poll_attempts - 1:
                await asyncio.sleep(self._config.poll_interval_seconds)

        logger.warning("[RULING] Ruling timed out for task %s", task_id)
        return {"payout": 0, "status": "timeout"}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_balance(self) -> int:
        """Fetch the agent's current balance, defaulting to 0 on error."""
        try:
            result = await self._agent.get_balance()
            return int(result.get("balance", 0))
        except Exception:
            logger.warning("Could not fetch balance, using 0")
            return 0
