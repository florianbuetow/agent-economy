"""Regression for classification discovery through Task Board summaries."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from classification_worker.adaptation import PricingRule
from classification_worker.loop import ClassificationWorkerLoop, LoopSettings
from classification_worker.solvers import KeywordSolver
from task_feeder.workloads.classification import ClassificationTask, render_spec

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

TASK_ID = "t-00000000-0000-4000-8000-000000000157"
SPEC = render_spec(
    ClassificationTask(
        title="Sentiment",
        task_type="sentiment",
        level=1,
        instructions="Label sentiment.",
        labels=("positive", "negative"),
        inputs=(("s1", "love it"),),
        expected={"s1": "positive"},
        min_correct=1,
        rationale_max_chars=100,
    )
)


@pytest.mark.unit
async def test_scanner_fetches_task_detail_before_parsing_classification_spec() -> None:
    summary = {
        "task_id": TASK_ID,
        "poster_id": "a-poster",
        "title": "Sentiment",
        "reward": 50,
        "status": "open",
        "bid_count": 0,
        "worker_id": None,
        "created_at": "2026-11-04T00:00:00Z",
        "bidding_deadline": "2026-11-04T00:02:00Z",
        "acceptance_deadline": "2026-11-04T00:04:00Z",
        "execution_deadline": None,
        "review_deadline": None,
    }
    assert "spec" not in summary
    detail = {**summary, "spec": SPEC}
    agent = SimpleNamespace(
        list_tasks=AsyncMock(return_value=[summary]),
        get_task=AsyncMock(return_value=detail),
    )
    jobs = SimpleNamespace(all=lambda: [])
    commands = SimpleNamespace(jobs=jobs)
    loop = ClassificationWorkerLoop(
        cast("Any", agent),
        KeywordSolver({"positive": ["love"], "negative": ["terrible"]}),
        LoopSettings(
            handle="classbot_keyword",
            scan_interval_seconds=1,
            poll_interval_seconds=1,
            max_poll_attempts=2,
            error_backoff_seconds=1,
            min_reward=1,
            max_reward=1000,
            pricing=PricingRule(
                initial_bid_pct=70,
                min_bid_pct=40,
                max_bid_pct=95,
                raise_step_pct=5,
                lower_step_pct=10,
                min_samples=3,
                min_approval_pct=50,
            ),
        ),
        cast("Any", commands),
    )

    selected = await loop._phase_scanning()

    assert selected == TASK_ID
    agent.list_tasks.assert_awaited_once_with(status="open")
    agent.get_task.assert_awaited_once_with(TASK_ID)
