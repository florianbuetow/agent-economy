"""Unit tests for the Task Feeder autonomous acceptance loop (WP-15 / Q-16 / T-035).

Winner rule (Q-16): lowest ``amount`` wins; amount ties break toward the bidder
with the higher delivery-quality reputation (derived feeder-side from raw
Reputation feedback per Q-12); any remaining tie breaks toward the
lexicographically smallest bid id.  A task is only eligible once the acceptance
window has opened (``acceptance_after_seconds``) or a bid quorum
(``min_bids_to_accept``) is met, and never with zero bids.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from task_feeder.acceptance import (
    AcceptanceLoop,
    delivery_quality_score,
    select_winning_bid,
)
from task_feeder.config import AcceptanceConfig

BASE = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _make_config(**overrides: int) -> AcceptanceConfig:
    values: dict[str, int] = {
        "acceptance_after_seconds": 30,
        "acceptance_poll_interval_seconds": 5,
        "min_bids_to_accept": 2,
        "bidding_deadline_seconds": 120,
    }
    values.update(overrides)
    return AcceptanceConfig(**values)


def _bid(bid_id: str, bidder_id: str, amount: int) -> dict[str, Any]:
    return {
        "bid_id": bid_id,
        "bidder_id": bidder_id,
        "amount": amount,
        "submitted_at": _iso(BASE),
    }


def _feedback(category: str, rating: str, to_agent_id: str = "w-a") -> dict[str, Any]:
    return {
        "feedback_id": "f-1",
        "task_id": "t-1",
        "from_agent_id": "poster",
        "to_agent_id": to_agent_id,
        "category": category,
        "rating": rating,
        "comment": None,
        "submitted_at": _iso(BASE),
        "visible": True,
    }


def _task(bid_count: int, created_at: datetime = BASE, task_id: str = "t-1") -> dict[str, Any]:
    return {
        "task_id": task_id,
        "status": "open",
        "bid_count": bid_count,
        "created_at": _iso(created_at),
    }


def _make_agent(
    bids: list[dict[str, Any]] | None = None,
    feedback_by_agent: dict[str, list[dict[str, Any]]] | None = None,
) -> MagicMock:
    agent = MagicMock()
    agent.agent_id = "feeder-1"
    agent.list_tasks = AsyncMock(return_value=[])
    agent.list_bids = AsyncMock(return_value=bids if bids is not None else [])
    agent.accept_bid = AsyncMock(return_value={"status": "accepted"})
    feedback = feedback_by_agent if feedback_by_agent is not None else {}

    async def _get_feedback(agent_id: str) -> list[dict[str, Any]]:
        return feedback.get(agent_id, [])

    agent.get_agent_feedback = AsyncMock(side_effect=_get_feedback)
    return agent


def _http_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://localhost:8003/accept")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


@pytest.mark.unit
class TestDeliveryQualityScore:
    def test_averages_delivery_quality_only(self) -> None:
        feedback = [
            _feedback("delivery_quality", "extremely_satisfied"),
            _feedback("delivery_quality", "satisfied"),
            _feedback("spec_quality", "dissatisfied"),
        ]
        # delivery ratings map to 2.0 and 1.0 -> average 1.5; spec_quality ignored.
        assert delivery_quality_score(feedback) == 1.5

    def test_none_when_no_delivery_feedback(self) -> None:
        assert delivery_quality_score([_feedback("spec_quality", "satisfied")]) is None

    def test_none_when_empty(self) -> None:
        assert delivery_quality_score([]) is None


@pytest.mark.unit
class TestSelectWinningBid:
    def test_lowest_amount_wins(self) -> None:
        bids = [_bid("b-1", "w-a", 8), _bid("b-2", "w-b", 6), _bid("b-3", "w-c", 10)]
        winner = select_winning_bid(bids, {})
        assert winner["bid_id"] == "b-2"

    def test_tie_broken_by_delivery_quality(self) -> None:
        bids = [_bid("b-1", "w-a", 6), _bid("b-2", "w-b", 6)]
        winner = select_winning_bid(bids, {"w-a": 0.0, "w-b": 2.0})
        assert winner["bid_id"] == "b-2"

    def test_tie_broken_by_bid_id_when_no_reputation(self) -> None:
        bids = [_bid("b-2", "w-b", 6), _bid("b-1", "w-a", 6)]
        winner = select_winning_bid(bids, {})
        assert winner["bid_id"] == "b-1"

    def test_reputation_does_not_override_lower_amount(self) -> None:
        bids = [_bid("b-1", "w-a", 8), _bid("b-2", "w-b", 6)]
        winner = select_winning_bid(bids, {"w-a": 2.0, "w-b": 0.0})
        assert winner["bid_id"] == "b-2"

    def test_empty_bids_raises(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            select_winning_bid([], {})


@pytest.mark.unit
class TestEligibility:
    def test_not_eligible_before_threshold_and_quorum(self) -> None:
        loop = AcceptanceLoop(agent=_make_agent(), config=_make_config(), now=lambda: BASE)
        assert not loop.is_eligible(_iso(BASE), bid_count=1)

    def test_eligible_when_quorum_met(self) -> None:
        loop = AcceptanceLoop(agent=_make_agent(), config=_make_config(), now=lambda: BASE)
        assert loop.is_eligible(_iso(BASE), bid_count=2)

    def test_eligible_when_time_threshold_passed(self) -> None:
        now = BASE + timedelta(seconds=40)
        loop = AcceptanceLoop(agent=_make_agent(), config=_make_config(), now=lambda: now)
        assert loop.is_eligible(_iso(BASE), bid_count=1)

    def test_zero_bids_never_eligible(self) -> None:
        now = BASE + timedelta(seconds=1000)
        loop = AcceptanceLoop(agent=_make_agent(), config=_make_config(), now=lambda: now)
        assert not loop.is_eligible(_iso(BASE), bid_count=0)


@pytest.mark.unit
class TestAcceptOne:
    @pytest.mark.asyncio
    async def test_accepts_lowest_bid_when_eligible(self) -> None:
        agent = _make_agent(bids=[_bid("b-1", "w-a", 8), _bid("b-2", "w-b", 6)])
        loop = AcceptanceLoop(agent=agent, config=_make_config(), now=lambda: BASE)
        result = await loop.accept_one(_task(bid_count=2))
        assert result == "accepted"
        agent.accept_bid.assert_awaited_once_with("t-1", "b-2")

    @pytest.mark.asyncio
    async def test_does_not_accept_before_eligibility(self) -> None:
        agent = _make_agent(bids=[_bid("b-1", "w-a", 6)])
        loop = AcceptanceLoop(agent=agent, config=_make_config(), now=lambda: BASE)
        result = await loop.accept_one(_task(bid_count=1))
        assert result == "not_eligible"
        agent.accept_bid.assert_not_called()

    @pytest.mark.asyncio
    async def test_zero_bids_left_to_expire(self) -> None:
        now = BASE + timedelta(seconds=1000)
        agent = _make_agent()
        loop = AcceptanceLoop(agent=agent, config=_make_config(), now=lambda: now)
        result = await loop.accept_one(_task(bid_count=0))
        assert result == "not_eligible"
        agent.accept_bid.assert_not_called()

    @pytest.mark.asyncio
    async def test_acceptance_happens_strictly_before_bidding_deadline(self) -> None:
        config = _make_config()
        # T-035 ordering invariant: the acceptance window opens before expiry.
        assert config.acceptance_after_seconds < config.bidding_deadline_seconds
        now = BASE + timedelta(seconds=config.acceptance_after_seconds)
        agent = _make_agent(bids=[_bid("b-1", "w-a", 6)])
        loop = AcceptanceLoop(agent=agent, config=config, now=lambda: now)
        # bid_count 1 < quorum 2, so only the time threshold can make it eligible.
        result = await loop.accept_one(_task(bid_count=1))
        assert result == "accepted"
        agent.accept_bid.assert_awaited_once()
        elapsed = (now - BASE).total_seconds()
        assert elapsed < config.bidding_deadline_seconds

    @pytest.mark.asyncio
    async def test_409_marks_done_with_and_does_not_crash(self) -> None:
        agent = _make_agent(bids=[_bid("b-1", "w-a", 6)])
        agent.accept_bid = AsyncMock(side_effect=_http_error(409))
        loop = AcceptanceLoop(
            agent=agent, config=_make_config(min_bids_to_accept=1), now=lambda: BASE
        )
        result = await loop.accept_one(_task(bid_count=1))
        assert result == "conflict"

    @pytest.mark.asyncio
    async def test_non_conflict_http_error_propagates(self) -> None:
        agent = _make_agent(bids=[_bid("b-1", "w-a", 6)])
        agent.accept_bid = AsyncMock(side_effect=_http_error(500))
        loop = AcceptanceLoop(
            agent=agent, config=_make_config(min_bids_to_accept=1), now=lambda: BASE
        )
        with pytest.raises(httpx.HTTPStatusError):
            await loop.accept_one(_task(bid_count=1))


@pytest.mark.unit
class TestProcessOpenTasks:
    @pytest.mark.asyncio
    async def test_polls_own_open_tasks_and_accepts_lowest(self) -> None:
        agent = _make_agent(bids=[_bid("b-1", "w-a", 8), _bid("b-2", "w-b", 6)])
        agent.list_tasks = AsyncMock(return_value=[_task(bid_count=2)])
        loop = AcceptanceLoop(agent=agent, config=_make_config(), now=lambda: BASE)
        await loop.process_open_tasks()
        agent.list_tasks.assert_awaited_once_with(status="open", poster_id="feeder-1")
        agent.accept_bid.assert_awaited_once_with("t-1", "b-2")

    @pytest.mark.asyncio
    async def test_amount_tie_broken_by_reputation_via_loop(self) -> None:
        agent = _make_agent(
            bids=[_bid("b-1", "w-a", 6), _bid("b-2", "w-b", 6)],
            feedback_by_agent={
                "w-a": [_feedback("delivery_quality", "dissatisfied", to_agent_id="w-a")],
                "w-b": [_feedback("delivery_quality", "extremely_satisfied", to_agent_id="w-b")],
            },
        )
        agent.list_tasks = AsyncMock(return_value=[_task(bid_count=2)])
        loop = AcceptanceLoop(agent=agent, config=_make_config(), now=lambda: BASE)
        await loop.process_open_tasks()
        agent.accept_bid.assert_awaited_once_with("t-1", "b-2")

    @pytest.mark.asyncio
    async def test_finalized_task_not_retried_after_conflict(self) -> None:
        agent = _make_agent(bids=[_bid("b-1", "w-a", 6)])
        agent.accept_bid = AsyncMock(side_effect=_http_error(409))
        agent.list_tasks = AsyncMock(return_value=[_task(bid_count=1)])
        loop = AcceptanceLoop(
            agent=agent, config=_make_config(min_bids_to_accept=1), now=lambda: BASE
        )
        await loop.process_open_tasks()
        await loop.process_open_tasks()
        assert agent.accept_bid.await_count == 1
