"""Economy report: journal collection, statistics and rendering."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import pytest

from classification_worker.adaptation import PricingRule
from economy_report.report import (
    WorkerJournal,
    collect_workers,
    fetch_quality,
    render,
    summarize,
    wilson_interval,
)
from tests.unit.math_worker.runtime_fixtures import WORKER_ID, runtime_worker

if TYPE_CHECKING:
    from pathlib import Path

RULE = PricingRule(
    initial_bid_pct=80,
    min_bid_pct=50,
    max_bid_pct=100,
    raise_step_pct=5,
    lower_step_pct=10,
    min_samples=3,
    min_approval_pct=50,
)


def _job(seq: int, outcome: str | None, task_type: str, bid: int, payout: int) -> dict[str, Any]:
    return {
        "outcome": outcome,
        "task_type": task_type,
        "outcome_seq": seq,
        "bid_amount": bid,
        "payout": payout,
    }


def _journal() -> WorkerJournal:
    return WorkerJournal(
        handle="classbot_keyword",
        profile="classbot_keyword",
        agent_id="a-1",
        jobs=[
            _job(1, "approved", "sentiment", 40, 40),
            _job(2, "approved", "sentiment", 42, 42),
            _job(3, "disputed_lost", "ticket_routing", 30, 0),
            _job(4, "bid_timeout", "ticket_routing", 30, 0),
            _job(5, "disputed_won", "ticket_routing", 30, 15),
            _job(0, None, "sentiment", 44, 0),
        ],
    )


@pytest.mark.unit
class TestWilsonInterval:
    def test_known_value(self) -> None:
        low, high = wilson_interval(8, 10, z=1.96)
        assert low == pytest.approx(0.4902, abs=1e-3)
        assert high == pytest.approx(0.9433, abs=1e-3)

    def test_no_samples_is_fully_uncertain(self) -> None:
        assert wilson_interval(0, 0, z=1.96) == (0.0, 1.0)

    def test_all_successes_upper_bound_is_one(self) -> None:
        low, high = wilson_interval(3, 3, z=1.96)
        assert high == pytest.approx(1.0)
        assert low < 0.5


@pytest.mark.unit
class TestSummarize:
    def test_counts_rates_cost_and_adapted_prices(self) -> None:
        summary = summarize(_journal(), rule=RULE, z=1.96)
        assert summary.bids == 6
        assert summary.selected == 4
        assert summary.in_flight == 1
        assert summary.lost_auctions == 1
        assert summary.decided == 4
        assert summary.successes == 3
        assert summary.approval_rate == pytest.approx(0.75)
        assert summary.cost_paid == 97
        assert summary.interval == wilson_interval(3, 4, z=1.96)
        sentiment = summary.by_type["sentiment"]
        assert (sentiment.decided, sentiment.successes, sentiment.bid_pct) == (2, 2, 90)
        routing = summary.by_type["ticket_routing"]
        assert (routing.decided, routing.successes, routing.bid_pct) == (2, 1, 75)
        assert routing.bidding


@pytest.mark.unit
@pytest.mark.asyncio
async def test_collects_only_configured_roles_from_real_journals(tmp_path: Path) -> None:
    root = tmp_path / "rt"
    async with runtime_worker(root) as (_agent, _store, commands):
        commands.jobs.save(
            "t-10000000-0000-4000-8000-000000000101", _job(1, "approved", "sentiment", 40, 40)
        )
    found = collect_workers(root, roles=["math-worker"])
    assert len(found) == 1
    assert found[0].agent_id == WORKER_ID
    assert found[0].jobs[0]["outcome"] == "approved"
    assert collect_workers(root, roles=["classification-worker"]) == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_quality_fetch_reports_body_or_unavailability() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/agents/a-1/quality":
            return httpx.Response(200, json={"agent_id": "a-1", "delivery_quality": 0.8})
        return httpx.Response(404, json={"error": "AGENT_NOT_FOUND"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ok = await fetch_quality(client, "http://rep", "a-1")
        missing = await fetch_quality(client, "http://rep", "a-2")
    assert ok == {"agent_id": "a-1", "delivery_quality": 0.8}
    assert missing == "unavailable (HTTP 404)"


@pytest.mark.unit
def test_render_shows_samples_interval_cost_and_quality() -> None:
    summary = summarize(_journal(), rule=RULE, z=1.96)
    quality = {"delivery_quality": {"rated": True, "score": 80, "task_count": 4}}
    text = render([summary], {"a-1": quality}, z=1.96)
    assert "classbot_keyword" in text
    assert "3/4" in text
    assert "cost paid 97" in text
    low, high = summary.interval
    assert f"[{low:.2f}, {high:.2f}]" in text
    assert "delivery_quality 80/100 (n=4)" in text
    assert "ticket_routing" in text
