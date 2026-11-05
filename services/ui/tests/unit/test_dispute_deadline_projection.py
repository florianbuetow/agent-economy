"""Projection coverage for the authoritative worker rebuttal deadline."""

from __future__ import annotations

from typing import Any

import aiosqlite
import pytest

from ui_service.schemas import TaskDrilldownResponse
from ui_service.services import tasks as tasks_service

pytestmark = pytest.mark.unit

DEADLINE = "2026-11-05T21:45:04.113060+00:00"


class _TaskReader:
    async def get_task(self, task_id: str) -> dict[str, Any]:
        return {
            "task_id": task_id,
            "poster_id": "a-poster",
            "worker_id": "a-worker",
            "title": "Invert a tree",
            "spec": "Provide Python example code.",
            "reward": 100,
            "status": "disputed",
            "lifecycle_version": 3,
            "bid_count": 1,
            "accepted_bid_id": "bid-1",
            "accepted_bid_amount": 1,
            "created_at": "2026-11-04T21:31:33Z",
            "accepted_at": "2026-11-04T21:33:58Z",
            "submitted_at": "2026-11-04T21:34:11Z",
            "approved_at": None,
            "bidding_deadline": "2026-11-04T21:36:31Z",
            "acceptance_deadline": "2026-11-04T21:41:31Z",
            "execution_deadline": "2026-11-04T21:38:58Z",
            "review_deadline": "2026-11-04T21:39:11Z",
            "rebuttal_deadline": DEADLINE,
            "ruling_id": None,
            "ruled_at": None,
            "worker_pct": None,
            "ruling_summary": None,
            "financial_effect": {
                "schema_version": 1,
                "settlement_state": "locked",
                "receipt_state": "absent",
                "outcome": None,
                "operation_id": None,
                "receipt_reference": None,
                "business_projection_pending": False,
                "pending_target_status": None,
            },
        }


async def _poster_name(_db: aiosqlite.Connection, _agent_id: str) -> str:
    return "Poster"


async def _worker(_db: aiosqlite.Connection, _agent_id: str | None) -> dict[str, str]:
    return {"agent_id": "a-worker", "name": "Worker"}


async def _empty(*_args: object, **_kwargs: object) -> list[object]:
    return []


async def test_drilldown_projects_authoritative_rebuttal_deadline(monkeypatch) -> None:
    """The browser receives the exact Task Board worker-response deadline."""
    async with aiosqlite.connect(":memory:") as db:
        await db.execute(
            "CREATE TABLE court_claims (claim_id TEXT, task_id TEXT, reason TEXT, filed_at TEXT)"
        )
        await db.execute(
            "CREATE TABLE court_rebuttals (claim_id TEXT, content TEXT, submitted_at TEXT)"
        )
        await db.execute(
            "INSERT INTO court_claims VALUES (?, ?, ?, ?)",
            ("disp-1", "t-job", "Wrong output.", "2026-11-04T21:45:04Z"),
        )
        await db.commit()

        monkeypatch.setattr(tasks_service, "_resolve_agent_name", _poster_name)
        monkeypatch.setattr(tasks_service, "_load_worker", _worker)
        monkeypatch.setattr(tasks_service, "_load_bids", _empty)
        monkeypatch.setattr(tasks_service, "_load_assets", _empty)
        monkeypatch.setattr(tasks_service, "_load_feedback", _empty)

        projection = await tasks_service.get_task_drilldown(db, _TaskReader(), "t-job")

    response = TaskDrilldownResponse.model_validate(projection)
    assert response.dispute is not None
    assert response.dispute.rebuttal_deadline == DEADLINE
