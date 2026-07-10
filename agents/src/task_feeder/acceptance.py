"""Autonomous bid-acceptance loop for the Task Feeder (WP-15 / Q-16 / T-035).

The feeder polls its own ``open`` tasks and, once a task is eligible, accepts the
winning bid so the economy can leave ``open`` without a human or the scripted
demo in the loop.

Eligibility (Q-16): a task is eligible once its acceptance window has opened
(``now - created_at >= acceptance_after_seconds``) **or** a bid quorum is met
(``bid_count >= min_bids_to_accept``); a task with zero bids is never eligible
and is left to expire.  ``acceptance_after_seconds`` is validated to be strictly
less than the bidding deadline (T-035), so the window always opens before expiry.

Winner rule (Q-16): the lowest ``amount`` wins — accepting the first bid to
arrive is forbidden because it destroys the undercutting the thesis rests on.
Ties on amount break toward the bidder with the higher delivery-quality
reputation, derived feeder-side from the Reputation service's raw feedback
(Q-12 keeps aggregation on the consumer side).  Any remaining tie (e.g. two
unproven bidders) breaks deterministically toward the lexicographically smallest
bid id.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from collections.abc import Callable

    from base_agent.agent import BaseAgent
    from task_feeder.config import AcceptanceConfig

logger = logging.getLogger(__name__)

_HTTP_CONFLICT = 409

# Delivery-quality ratings are ordinal (dissatisfied < satisfied <
# extremely_satisfied); mapped to a numeric scale and averaged to score an agent.
_DELIVERY_QUALITY_CATEGORY = "delivery_quality"
_DELIVERY_RATING_SCORES: dict[str, float] = {
    "dissatisfied": 0.0,
    "satisfied": 1.0,
    "extremely_satisfied": 2.0,
}
# A bidder with no delivery-quality feedback has no track record and enters the
# tie-break at the floor of the scale; equal scores fall through to the
# deterministic bid-id fallback.
_NO_FEEDBACK_SCORE = 0.0


def delivery_quality_score(feedback: list[dict[str, Any]]) -> float | None:
    """Average delivery-quality rating from an agent's raw feedback records.

    Returns ``None`` when the agent has no ``delivery_quality`` feedback.
    """
    scores = [
        _DELIVERY_RATING_SCORES[str(record["rating"])]
        for record in feedback
        if record.get("category") == _DELIVERY_QUALITY_CATEGORY
        and str(record.get("rating")) in _DELIVERY_RATING_SCORES
    ]
    if not scores:
        return None
    return sum(scores) / len(scores)


def select_winning_bid(
    bids: list[dict[str, Any]],
    delivery_scores: dict[str, float],
) -> dict[str, Any]:
    """Pick the winning bid per the Q-16 rule.

    Sort key (ascending ``min``): lowest ``amount`` first; then higher
    delivery-quality reputation (negated so higher wins); then lexicographically
    smallest ``bid_id`` as the deterministic final tie-break.
    """
    if not bids:
        msg = "cannot select a winner from an empty bid list"
        raise ValueError(msg)
    return min(
        bids,
        key=lambda bid: (
            int(bid["amount"]),
            -delivery_scores.get(str(bid["bidder_id"]), _NO_FEEDBACK_SCORE),
            str(bid["bid_id"]),
        ),
    )


def _parse_created_at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class AcceptanceLoop:
    """Poll the feeder's own open tasks and accept the winning bid when eligible."""

    def __init__(
        self,
        agent: BaseAgent,
        config: AcceptanceConfig,
        now: Callable[[], datetime],
    ) -> None:
        self._agent = agent
        self._config = config
        self._now = now
        self._running = True
        self._finalized: set[str] = set()

    def is_eligible(self, created_at: str, bid_count: int) -> bool:
        """Return True when the task may be accepted now (Q-16 eligibility).

        ``bid_count`` is the authoritative count from the bid list, not the task
        summary's ``bid_count`` field (which the gateway-backed board leaves at 0).
        """
        if bid_count <= 0:
            return False
        if bid_count >= self._config.min_bids_to_accept:
            return True
        age_seconds = (self._now() - _parse_created_at(created_at)).total_seconds()
        return age_seconds >= self._config.acceptance_after_seconds

    async def _delivery_scores_for(self, bidder_ids: set[str]) -> dict[str, float]:
        scores: dict[str, float] = {}
        for bidder_id in bidder_ids:
            feedback = await self._agent.get_agent_feedback(bidder_id)
            score = delivery_quality_score(feedback)
            if score is not None:
                scores[bidder_id] = score
        return scores

    async def accept_one(self, task: dict[str, Any]) -> str:
        """Evaluate one open task and, if eligible, accept its winning bid."""
        task_id = str(task["task_id"])
        # The bid list is the authoritative bid count; the task summary's
        # bid_count is unreliable against the gateway-backed board.
        bids = await self._agent.list_bids(task_id)
        if not self.is_eligible(str(task["created_at"]), len(bids)):
            # Not enough bids yet, or the acceptance window has not opened, or the
            # task has zero bids and is left to expire.
            return "not_eligible"

        # Reputation only matters as a tie-break among the lowest-amount bids, so
        # only fetch it when there is an amount tie to break.
        lowest_amount = min(int(bid["amount"]) for bid in bids)
        contenders = {str(bid["bidder_id"]) for bid in bids if int(bid["amount"]) == lowest_amount}
        delivery_scores = await self._delivery_scores_for(contenders) if len(contenders) > 1 else {}
        winner = select_winning_bid(bids, delivery_scores)
        winner_bid_id = str(winner["bid_id"])

        try:
            await self._agent.accept_bid(task_id, winner_bid_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == _HTTP_CONFLICT:
                logger.warning(
                    "Task %s no longer open (409) — likely expired first; marking done-with",
                    task_id,
                )
                self._finalized.add(task_id)
                return "conflict"
            raise

        self._finalized.add(task_id)
        logger.info(
            "Accepted bid %s on task %s (amount=%s)",
            winner_bid_id,
            task_id,
            winner["amount"],
        )
        return "accepted"

    async def process_open_tasks(self) -> None:
        """Run one polling pass over the feeder's own open tasks."""
        tasks = await self._agent.list_tasks(
            status="open",
            poster_id=self._agent.agent_id,
        )
        for task in tasks:
            task_id = str(task.get("task_id", ""))
            if task_id in self._finalized:
                continue
            if str(task.get("status", "")).lower() != "open":
                continue
            try:
                await self.accept_one(task)
            except Exception:
                logger.exception("Failed to process task_id=%s for acceptance", task_id)

    async def run(self) -> None:
        """Continuously accept eligible bids on this poster's open tasks."""
        logger.info(
            "Acceptance loop starting (poll=%ss, after=%ss, quorum=%s)",
            self._config.acceptance_poll_interval_seconds,
            self._config.acceptance_after_seconds,
            self._config.min_bids_to_accept,
        )
        while self._running:
            await self.process_open_tasks()
            await asyncio.sleep(self._config.acceptance_poll_interval_seconds)

    def stop(self) -> None:
        """Signal the acceptance loop to stop after the current cycle."""
        self._running = False
