"""Unit tests for the Task Feeder autonomous acceptance loop (WP-15 / Q-16 / T-035).

Winner rule (Q-16): lowest ``amount`` wins; amount ties break toward the bidder
with the higher delivery-quality score read from the Reputation service's
`GET /agents/{id}/quality` (G12; the feeder no longer aggregates raw feedback);
an unrated bidder ranks 0. Any remaining tie breaks toward the
lexicographically smallest bid id.  A task is only eligible once the acceptance
window has opened (``acceptance_after_seconds``) or a bid quorum
(``min_bids_to_accept``) is met, and never with zero bids.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from service_auth.financial_identity import operation_id_for

from task_feeder.acceptance import (
    AcceptanceLoop,
    delivery_rank,
    select_winning_bid,
)
from task_feeder.config import AcceptanceConfig

from ._runtime_fixtures import (
    POSTER_ID,
    TASK_ID,
    make_agent,
    open_task,
    runtime_commands,
)

if TYPE_CHECKING:
    from pathlib import Path

    from base_agent.runtime_task_commands import RuntimeTaskCommands

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


def _quality(score: int | None) -> dict[str, Any]:
    """A Reputation quality report whose delivery dimension has ``score``."""
    rated = score is not None
    return {
        "agent_id": "w-x",
        "formula_version": "quality-v1",
        "specification_quality": {"rated": False, "score": None, "task_count": 0},
        "delivery_quality": {"rated": rated, "score": score, "task_count": 1 if rated else 0},
    }


def _task(
    bid_count: int,
    created_at: datetime = BASE,
    task_id: str = TASK_ID,
) -> dict[str, Any]:
    task = open_task(task_id=task_id)
    task.update(bid_count=bid_count, created_at=_iso(created_at))
    return task


def _make_agent(
    bids: list[dict[str, Any]] | None = None,
    quality_by_agent: dict[str, dict[str, Any]] | None = None,
) -> MagicMock:
    agent = make_agent()
    agent.get_task = AsyncMock(return_value=open_task())
    agent.list_bids = AsyncMock(return_value=bids if bids is not None else [])
    quality = quality_by_agent if quality_by_agent is not None else {}

    async def _get(method: str, url: str) -> dict[str, Any]:
        assert method == "GET"
        prefix = f"{agent.config.reputation_url}/agents/"
        assert url.startswith(prefix)
        assert url.endswith("/quality")
        return quality.get(url[len(prefix) : -len("/quality")], _quality(None))

    agent._request = AsyncMock(side_effect=_get)
    agent.get_agent_feedback = AsyncMock(side_effect=AssertionError("raw feedback read"))
    return agent


def _eligibility_loop(now: datetime) -> AcceptanceLoop:
    return AcceptanceLoop(
        agent=cast("Any", _make_agent()),
        config=_make_config(),
        now=lambda: now,
        commands=cast("RuntimeTaskCommands", object()),
    )


@pytest.mark.unit
class TestDeliveryRank:
    def test_rated_score_is_the_rank(self) -> None:
        assert delivery_rank(_quality(67)) == 67

    def test_unrated_ranks_zero(self) -> None:
        assert delivery_rank(_quality(None)) == 0

    @pytest.mark.parametrize(
        "report",
        [
            {},
            {**_quality(50), "formula_version": "quality-v2"},
            {**_quality(50), "delivery_quality": {"rated": True, "score": None}},
            {**_quality(None), "delivery_quality": {"rated": False, "score": 10}},
            {**_quality(50), "delivery_quality": {"rated": True, "score": 101}},
            {**_quality(50), "delivery_quality": {"rated": "yes", "score": 50}},
        ],
    )
    def test_malformed_report_is_not_unrated(self, report: dict[str, Any]) -> None:
        with pytest.raises(ValueError, match="quality"):
            delivery_rank(report)


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
        loop = _eligibility_loop(BASE)
        assert not loop.is_eligible(_iso(BASE), bid_count=1)

    def test_eligible_when_quorum_met(self) -> None:
        loop = _eligibility_loop(BASE)
        assert loop.is_eligible(_iso(BASE), bid_count=2)

    def test_eligible_when_time_threshold_passed(self) -> None:
        now = BASE + timedelta(seconds=40)
        loop = _eligibility_loop(now)
        assert loop.is_eligible(_iso(BASE), bid_count=1)

    def test_zero_bids_never_eligible(self) -> None:
        now = BASE + timedelta(seconds=1000)
        loop = _eligibility_loop(now)
        assert not loop.is_eligible(_iso(BASE), bid_count=0)


@pytest.mark.unit
class TestAcceptOne:
    @pytest.mark.asyncio
    async def test_accepts_lowest_bid_when_eligible(self, tmp_path: Path) -> None:
        agent = _make_agent(bids=[_bid("b-1", "w-a", 8), _bid("b-2", "w-b", 6)])
        with runtime_commands(tmp_path, agent) as (commands, _store):
            loop = AcceptanceLoop(
                agent=agent, config=_make_config(), now=lambda: BASE, commands=commands
            )
            result = await loop.accept_one(_task(bid_count=2))
        assert result == "accepted"
        agent.accept_bid.assert_awaited_once_with(
            TASK_ID,
            "b-2",
            operation_id=operation_id_for("task-accept", (TASK_ID, "7")),
            expected_lifecycle_version=7,
        )

    @pytest.mark.asyncio
    async def test_does_not_accept_before_eligibility(self, tmp_path: Path) -> None:
        agent = _make_agent(bids=[_bid("b-1", "w-a", 6)])
        with runtime_commands(tmp_path, agent) as (commands, store):
            loop = AcceptanceLoop(
                agent=agent, config=_make_config(), now=lambda: BASE, commands=commands
            )
            result = await loop.accept_one(_task(bid_count=1))
            assert store.load(TASK_ID, "acceptance") is None
        assert result == "not_eligible"
        agent.accept_bid.assert_not_called()

    @pytest.mark.asyncio
    async def test_zero_bids_left_to_expire(self, tmp_path: Path) -> None:
        now = BASE + timedelta(seconds=1000)
        agent = _make_agent()
        with runtime_commands(tmp_path, agent) as (commands, store):
            loop = AcceptanceLoop(
                agent=agent, config=_make_config(), now=lambda: now, commands=commands
            )
            result = await loop.accept_one(_task(bid_count=0))
            assert store.load(TASK_ID, "acceptance") is None
        assert result == "not_eligible"
        agent.accept_bid.assert_not_called()

    @pytest.mark.asyncio
    async def test_acceptance_happens_strictly_before_bidding_deadline(
        self, tmp_path: Path
    ) -> None:
        config = _make_config()
        # T-035 ordering invariant: the acceptance window opens before expiry.
        assert config.acceptance_after_seconds < config.bidding_deadline_seconds
        now = BASE + timedelta(seconds=config.acceptance_after_seconds)
        agent = _make_agent(bids=[_bid("b-1", "w-a", 6)])
        with runtime_commands(tmp_path, agent) as (commands, _store):
            loop = AcceptanceLoop(agent=agent, config=config, now=lambda: now, commands=commands)
            # bid_count 1 < quorum 2, so only the time threshold can make it eligible.
            result = await loop.accept_one(_task(bid_count=1))
        assert result == "accepted"
        agent.accept_bid.assert_awaited_once()
        elapsed = (now - BASE).total_seconds()
        assert elapsed < config.bidding_deadline_seconds

    @pytest.mark.asyncio
    async def test_generic_409_remains_pending_and_does_not_finalize(self, tmp_path: Path) -> None:
        agent = _make_agent(bids=[_bid("b-1", "w-a", 6)])
        with runtime_commands(tmp_path, agent, acknowledgment="generic_conflict") as (
            commands,
            store,
        ):
            loop = AcceptanceLoop(
                agent=agent,
                config=_make_config(min_bids_to_accept=1),
                now=lambda: BASE,
                commands=commands,
            )
            result = await loop.accept_one(_task(bid_count=1))
            saved = store.load(TASK_ID, "acceptance")
        assert result == "pending"
        assert saved is not None and saved.state == "pending"
        assert TASK_ID not in loop._finalized

    @pytest.mark.asyncio
    async def test_native_dependency_failure_is_journaled_pending_and_retried(
        self, tmp_path: Path
    ) -> None:
        agent = _make_agent(bids=[_bid("b-1", "w-a", 6)])
        with runtime_commands(tmp_path, agent, acknowledgment="dependency_failure") as (
            commands,
            store,
        ):
            loop = AcceptanceLoop(
                agent=agent,
                config=_make_config(min_bids_to_accept=1),
                now=lambda: BASE,
                commands=commands,
            )
            first = await loop.accept_one(_task(bid_count=1))
            second = await loop.accept_one(_task(bid_count=1))
            saved = store.load(TASK_ID, "acceptance")
        assert first == second == "pending"
        assert saved is not None and saved.state == "pending"
        assert agent.accept_bid.await_count == 2
        assert TASK_ID not in loop._finalized


@pytest.mark.unit
class TestProcessOpenTasks:
    @pytest.mark.asyncio
    async def test_polls_own_open_tasks_and_accepts_lowest(self, tmp_path: Path) -> None:
        agent = _make_agent(bids=[_bid("b-1", "w-a", 8), _bid("b-2", "w-b", 6)])
        agent.list_tasks = AsyncMock(return_value=[_task(bid_count=2)])
        with runtime_commands(tmp_path, agent) as (commands, _store):
            loop = AcceptanceLoop(
                agent=agent, config=_make_config(), now=lambda: BASE, commands=commands
            )
            await loop.process_open_tasks()
        agent.list_tasks.assert_awaited_once_with(status="open", poster_id=POSTER_ID)
        assert agent.accept_bid.await_args.args == (TASK_ID, "b-2")

    @pytest.mark.asyncio
    async def test_amount_tie_broken_by_reputation_via_loop(self, tmp_path: Path) -> None:
        agent = _make_agent(
            bids=[_bid("b-1", "w-a", 6), _bid("b-2", "w-b", 6)],
            quality_by_agent={"w-a": _quality(0), "w-b": _quality(100)},
        )
        agent.list_tasks = AsyncMock(return_value=[_task(bid_count=2)])
        with runtime_commands(tmp_path, agent) as (commands, _store):
            loop = AcceptanceLoop(
                agent=agent, config=_make_config(), now=lambda: BASE, commands=commands
            )
            await loop.process_open_tasks()
        assert agent.accept_bid.await_args.args == (TASK_ID, "b-2")
        # One service score per contender; no raw feedback is read or averaged.
        urls = sorted(call.args[1] for call in agent._request.await_args_list)
        assert urls == [
            f"{agent.config.reputation_url}/agents/w-a/quality",
            f"{agent.config.reputation_url}/agents/w-b/quality",
        ]
        agent.get_agent_feedback.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unrated_ties_rated_zero_and_falls_to_bid_id(self, tmp_path: Path) -> None:
        agent = _make_agent(
            bids=[_bid("b-2", "w-rated", 6), _bid("b-1", "w-unrated", 6)],
            quality_by_agent={"w-rated": _quality(0), "w-unrated": _quality(None)},
        )
        agent.list_tasks = AsyncMock(return_value=[_task(bid_count=2)])
        with runtime_commands(tmp_path, agent) as (commands, _store):
            loop = AcceptanceLoop(
                agent=agent, config=_make_config(), now=lambda: BASE, commands=commands
            )
            await loop.process_open_tasks()
        assert agent.accept_bid.await_args.args == (TASK_ID, "b-1")

    @pytest.mark.asyncio
    async def test_quality_lookup_failure_does_not_accept(self, tmp_path: Path) -> None:
        agent = _make_agent(bids=[_bid("b-1", "w-a", 6), _bid("b-2", "w-b", 6)])
        agent._request = AsyncMock(side_effect=httpx.ConnectError("reputation down"))
        agent.list_tasks = AsyncMock(return_value=[_task(bid_count=2)])
        with runtime_commands(tmp_path, agent) as (commands, _store):
            loop = AcceptanceLoop(
                agent=agent, config=_make_config(), now=lambda: BASE, commands=commands
            )
            await loop.process_open_tasks()
        agent.accept_bid.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_definitively_rejected_task_is_not_sent_again(self, tmp_path: Path) -> None:
        agent = _make_agent(bids=[_bid("b-1", "w-a", 6)])
        agent.list_tasks = AsyncMock(return_value=[_task(bid_count=1)])
        with runtime_commands(tmp_path, agent, acknowledgment="definitive_rejection") as (
            commands,
            store,
        ):
            loop = AcceptanceLoop(
                agent=agent,
                config=_make_config(min_bids_to_accept=1),
                now=lambda: BASE,
                commands=commands,
            )
            await loop.process_open_tasks()
            await loop.process_open_tasks()
            saved = store.load(TASK_ID, "acceptance")
        assert agent.accept_bid.await_count == 1
        assert saved is not None and saved.state == "definitive_rejection"
