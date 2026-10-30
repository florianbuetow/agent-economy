"""Task-page lane contracts: needs-action data, agent links, fixed errors, docs disabled."""

from __future__ import annotations

from typing import Any

import pytest

from ui_service.core.state import get_app_state

pytestmark = pytest.mark.integration

_OPERATOR = "a-operator"


def _task(task_id: str, poster_id: str, status: str, bid_count: int) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "poster_id": poster_id,
        "title": task_id,
        "reward": 10,
        "status": status,
        "bid_count": bid_count,
        "worker_id": None,
        "created_at": "2026-10-25T10:00:00Z",
        "bidding_deadline": "2026-10-25T11:00:00Z",
        "acceptance_deadline": "2026-10-25T11:30:00Z",
        "execution_deadline": "2026-10-25T12:00:00Z",
        "review_deadline": "2026-10-25T13:00:00Z",
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


class _NeedsActionReader:
    def __init__(self) -> None:
        self.tasks = [
            _task("t-bids-waiting", _OPERATOR, "open", 2),
            _task("t-no-bids", _OPERATOR, "open", 0),
            _task("t-review-waiting", _OPERATOR, "submitted", 1),
            _task("t-someone-elses", "a-other", "submitted", 1),
        ]

    async def list_tasks(
        self, status: str | None, poster_id: str | None, worker_id: str | None
    ) -> list[dict[str, Any]]:
        return [
            task
            for task in self.tasks
            if (status is None or task["status"] == status)
            and (poster_id is None or task["poster_id"] == poster_id)
            and (worker_id is None or task["worker_id"] == worker_id)
        ]

    async def get_task(self, task_id: str) -> dict[str, Any]:
        raise AssertionError(f"unexpected detail read for {task_id}")


async def test_operator_tasks_awaiting_acceptance_and_review_are_listable(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_app_state(), "task_board_reader", _NeedsActionReader())

    open_response = await client.get(f"/api/tasks?poster_id={_OPERATOR}&status=open&limit=100")
    submitted_response = await client.get(
        f"/api/tasks?poster_id={_OPERATOR}&status=submitted&limit=100"
    )

    assert open_response.status_code == 200
    assert submitted_response.status_code == 200
    open_tasks = open_response.json()["tasks"]
    awaiting_acceptance = [t["task_id"] for t in open_tasks if t["bid_count"] > 0]
    awaiting_review = [t["task_id"] for t in submitted_response.json()["tasks"]]
    assert awaiting_acceptance == ["t-bids-waiting"]
    assert awaiting_review == ["t-review-waiting"]


async def test_task_feedback_carries_agent_ids_for_links(client: Any) -> None:
    response = await client.get("/api/tasks/t-task1")

    assert response.status_code == 200
    feedback = response.json()["feedback"]
    assert feedback
    for item in feedback:
        assert item["from_agent_id"].startswith("a-")
        assert item["to_agent_id"].startswith("a-")


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"])
async def test_interactive_api_docs_are_not_served(client: Any, path: str) -> None:
    response = await client.get(path)

    assert response.status_code == 404


async def test_bid_list_failure_message_never_echoes_internal_details(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _BrokenAgent:
        agent_id = _OPERATOR

        async def list_bids(self, task_id: str) -> list[dict[str, object]]:
            del task_id
            raise RuntimeError("connect failed: http://task-board.internal:8003/tasks/secret")

    monkeypatch.setattr(get_app_state(), "user_agent", _BrokenAgent())

    response = await client.get("/api/proxy/tasks/t-task8/bids")

    assert response.status_code == 502
    body = response.json()
    assert body["error"] == "bid_listing_failed"
    assert body["message"] == "Task Board could not list the bids for this task."
    assert "task-board.internal" not in response.text
    assert "secret" not in response.text
