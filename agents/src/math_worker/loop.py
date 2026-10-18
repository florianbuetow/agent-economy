"""Main agent loop and phase state machine for the Math Worker."""

from __future__ import annotations

import asyncio
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

        await self._commands.replay_unfinished()

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
        task = await self._agent.get_task(task_id)

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
        submission_result = await self._phase_submitting(task_id, solution)
        if submission_result.state != "completed":
            return

        # --- WAITING FOR REVIEW ---
        review_outcome = await self._phase_waiting_for_review(task_id)

        if review_outcome == "approved":
            self._history.record(
                task_id=task_id,
                title=task.get("title", ""),
                reward=task.get("reward", 0),
                bid_amount=bid_result.get("amount", 0),
                outcome=TaskOutcome.APPROVED,
                solution=solution,
                payout=task.get("reward", 0),
            )
        elif review_outcome == "disputed":
            disputed_task = await self._agent.get_task(task_id)
            await self._phase_disputed(disputed_task, solution)
        else:
            logger.warning(
                "[REVIEW] Review poll exhausted for task %s without a terminal "
                "status; recording TIMEOUT with zero payout",
                task_id,
            )
            self._history.record(
                task_id=task_id,
                title=task.get("title", ""),
                reward=task.get("reward", 0),
                bid_amount=bid_result.get("amount", 0),
                outcome=TaskOutcome.TIMEOUT,
                solution=solution,
                payout=0,
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
            status = str(task.get("status", "")).lower()

            if status == "accepted":
                # Our bid was accepted (task moved to execution)
                worker_id = task.get("worker_id")
                if worker_id == self._agent.agent_id:
                    logger.info("[WAITING] Bid accepted! task=%s", task_id)
                    return True
                # Someone else's bid was accepted
                logger.info("[WAITING] Another agent's bid accepted for task %s", task_id)
                return False

            if status in ("cancelled", "approved", "disputed", "ruled", "expired"):
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

    async def _phase_submitting(self, task_id: str, solution: str) -> FrozenTaskIntent:
        """Upload a new solution and durably submit it for review."""

        async def prepare() -> NewTaskIntent:
            logger.info("[SUBMITTING] Uploading solution for task %s", task_id)
            content = solution.encode("utf-8")
            filename = f"{task_id}_solution.txt"
            await self._agent.upload_asset(task_id, filename, content)

            task = await self._agent.get_task(task_id)
            logger.info("[SUBMITTING] Submitting deliverable for task %s", task_id)
            return submission_intent(
                task=task,
                actor_id=self._require_agent_id(),
                operation_id=f"op-{uuid.uuid4()}",
            )

        return await self._commands.execute(task_id=task_id, slot="submission", prepare=prepare)

    async def _phase_waiting_for_review(self, task_id: str) -> str:
        """Poll until the poster approves, disputes, or the review times out.

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

        logger.info("[REVIEW] Review timed out for task %s", task_id)
        return "timeout"

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

        logger.info("[DISPUTED] Submitting rebuttal for task %s", task_id)
        try:
            dispute_id = await self._resolve_dispute_id(task_id, task)
            if dispute_id is None:
                logger.warning("No Court dispute found for task %s; skipping rebuttal", task_id)
            else:
                await self._agent.submit_worker_rebuttal(task_id, dispute_id, rebuttal)
        except Exception:
            logger.exception("Failed to submit rebuttal for task %s", task_id)

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
            status = str(task.get("status", "")).lower()

            if status == "ruled":
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
