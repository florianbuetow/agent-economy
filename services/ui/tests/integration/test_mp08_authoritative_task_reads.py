"""UI task routes use one coherent Task Board snapshot as public authority."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from ui_service.core.state import get_app_state

pytestmark = pytest.mark.integration

_TASK_ID = "t-task1"
_OPERATION_ID = "op-00000000-0000-4000-8000-000000000208"
_LOCKED_EFFECT: dict[str, object] = {
    "schema_version": 1,
    "settlement_state": "locked",
    "receipt_state": "absent",
    "outcome": None,
    "operation_id": None,
    "receipt_reference": None,
    "business_projection_pending": False,
    "pending_target_status": None,
}
_SPLIT_EFFECT: dict[str, object] = {
    "schema_version": 1,
    "settlement_state": "complete",
    "receipt_state": "confirmed",
    "outcome": "split",
    "operation_id": _OPERATION_ID,
    "receipt_reference": _OPERATION_ID,
    "business_projection_pending": False,
    "pending_target_status": None,
}


def _summary(task_id: str, *, created_at: str, status: str = "open") -> dict[str, Any]:
    return {
        "task_id": task_id,
        "poster_id": "a-alice",
        "title": f"Authoritative {task_id}",
        "reward": 91,
        "status": status,
        "bid_count": 2,
        "worker_id": None,
        "created_at": created_at,
        "bidding_deadline": "2026-10-22T12:00:00Z",
        "acceptance_deadline": "2026-10-22T13:00:00Z",
        "execution_deadline": None,
        "review_deadline": None,
        "financial_effect": _LOCKED_EFFECT,
    }


def _detail(
    *,
    status: str,
    accepted_bid_id: str | None,
    worker_pct: int | None = None,
    task_id: str = _TASK_ID,
) -> dict[str, Any]:
    return {
        **_summary(task_id, created_at="2026-10-22T10:00:00Z", status=status),
        "poster_id": "a-dave" if task_id == "t-task5" else "a-alice",
        "worker_id": "a-carol" if task_id == "t-task5" else "a-bob" if accepted_bid_id else None,
        "spec": "Task Board owns this snapshot.",
        "lifecycle_version": 17,
        "accepted_bid_id": accepted_bid_id,
        "accepted_at": "2026-10-22T10:30:00Z" if accepted_bid_id is not None else None,
        "submitted_at": "2026-10-22T11:00:00Z" if status == "ruled" else None,
        "approved_at": None,
        "ruling_id": "rul-authoritative" if worker_pct is not None else None,
        "worker_pct": worker_pct,
        "ruling_summary": "Authoritative Board ruling" if worker_pct is not None else None,
        "ruled_at": "2026-10-22T11:30:00Z" if worker_pct is not None else None,
        "financial_effect": _SPLIT_EFFECT if worker_pct is not None else _LOCKED_EFFECT,
    }


class _Reader:
    def __init__(
        self,
        *,
        listed: object = None,
        detail: dict[str, Any] | BaseException | None = None,
    ) -> None:
        self.listed = listed if listed is not None else []
        self.detail = detail
        self.list_calls: list[str | None] = []
        self.detail_calls: list[str] = []

    async def list_tasks(
        self,
        status: str | None = None,
        poster_id: str | None = None,
        worker_id: str | None = None,
    ) -> Any:
        assert poster_id is None
        assert worker_id is None
        self.list_calls.append(status)
        return self.listed

    async def get_task(self, task_id: str) -> dict[str, Any]:
        self.detail_calls.append(task_id)
        if isinstance(self.detail, BaseException):
            raise self.detail
        assert self.detail is not None
        return self.detail


def _http_error(status_code: int, *, valid_not_found: bool = False) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", f"http://task-board/tasks/{_TASK_ID}")
    body = (
        {"error": "task_not_found", "message": "Task not found", "details": {}}
        if valid_not_found
        else {"unexpected": "body"}
    )
    response = httpx.Response(status_code, request=request, json=body)
    return httpx.HTTPStatusError("upstream failure", request=request, response=response)


async def test_list_uses_one_authoritative_call_then_slices_in_upstream_order(client: Any) -> None:
    upstream = [
        _summary("t-newest", created_at="2026-10-22T12:00:00Z"),
        _summary("t-middle", created_at="2026-10-22T11:00:00Z"),
        _summary("t-oldest", created_at="2026-10-22T10:00:00Z"),
    ]
    reader = _Reader(listed=upstream)
    get_app_state().task_board_reader = reader

    response = await client.get("/api/tasks?status=open&limit=1&offset=1")

    assert response.status_code == 200, response.text
    assert reader.list_calls == ["open"]
    assert [task["task_id"] for task in response.json()["tasks"]] == ["t-middle"]
    assert response.json()["total_count"] == 3
    assert response.json()["tasks"][0]["financial_effect"] == _LOCKED_EFFECT


async def test_detail_uses_upstream_status_to_keep_open_bids_sealed(client: Any) -> None:
    reader = _Reader(detail=_detail(status="open", accepted_bid_id=None))
    get_app_state().task_board_reader = reader

    response = await client.get(f"/api/tasks/{_TASK_ID}")

    assert response.status_code == 200, response.text
    assert reader.detail_calls == [_TASK_ID]
    assert response.json()["status"] == "open"
    assert response.json()["bids"] == []


async def test_detail_uses_board_ruling_fields_instead_of_stale_local_ruling(client: Any) -> None:
    task_id = "t-task5"
    reader = _Reader(
        detail=_detail(
            status="ruled",
            accepted_bid_id="bid-7",
            worker_pct=41,
            task_id=task_id,
        )
    )
    get_app_state().task_board_reader = reader

    response = await client.get(f"/api/tasks/{task_id}")

    assert response.status_code == 200, response.text
    assert response.json()["dispute"]["ruling"] == {
        "ruling_id": "rul-authoritative",
        "worker_pct": 41,
        "summary": "Authoritative Board ruling",
        "ruled_at": "2026-10-22T11:30:00Z",
    }
    assert response.json()["financial_effect"] == _SPLIT_EFFECT


@pytest.mark.parametrize(
    ("detail", "expected_status", "expected_error"),
    [
        (_http_error(404, valid_not_found=True), 404, "task_not_found"),
        (_http_error(503), 502, "task_board_unavailable"),
        (
            {**_detail(status="open", accepted_bid_id=None), "financial_effect": {}},
            502,
            "task_board_unavailable",
        ),
    ],
)
async def test_detail_maps_upstream_absence_and_failure_without_sql_fallback(
    client: Any,
    detail: dict[str, Any] | BaseException,
    expected_status: int,
    expected_error: str,
) -> None:
    get_app_state().task_board_reader = _Reader(detail=detail)

    response = await client.get(f"/api/tasks/{_TASK_ID}")

    assert response.status_code == expected_status, response.text
    assert response.json()["error"] == expected_error


@pytest.mark.parametrize(
    "listed",
    [
        {},
        [
            _summary("t-duplicate", created_at="2026-10-22T12:00:00Z"),
            _summary("t-duplicate", created_at="2026-10-22T11:00:00Z"),
        ],
        [_summary("t-wrong-status", created_at="2026-10-22T12:00:00Z", status="accepted")],
        [_summary("t-unknown-status", created_at="2026-10-22T12:00:00Z", status="mystery")],
        [{**_summary("t-negative-reward", created_at="2026-10-22T12:00:00Z"), "reward": -1}],
        [{**_summary("t-negative-count", created_at="2026-10-22T12:00:00Z"), "bid_count": -1}],
    ],
)
async def test_list_rejects_malformed_or_incoherent_upstream_snapshot(
    client: Any,
    listed: object,
) -> None:
    get_app_state().task_board_reader = _Reader(listed=listed)

    response = await client.get("/api/tasks?status=open")

    assert response.status_code == 502
    assert response.json()["error"] == "task_board_unavailable"


@pytest.mark.parametrize(
    "detail",
    [
        _http_error(404),
        _detail(status="mystery", accepted_bid_id="bid-1"),
        {**_detail(status="open", accepted_bid_id=None), "reward": -1},
        {**_detail(status="open", accepted_bid_id=None), "bid_count": -1},
        _detail(status="ruled", accepted_bid_id="bid-1", worker_pct=101),
    ],
)
async def test_detail_rejects_malformed_status_money_ruling_and_not_found(
    client: Any,
    detail: dict[str, Any] | BaseException,
) -> None:
    get_app_state().task_board_reader = _Reader(detail=detail)

    response = await client.get(f"/api/tasks/{_TASK_ID}")

    assert response.status_code == 502
    assert response.json()["error"] == "task_board_unavailable"


async def test_task_reads_require_an_initialized_board_reader(client: Any) -> None:
    get_app_state().task_board_reader = None

    response = await client.get("/api/tasks")

    assert response.status_code == 503
    assert response.json()["error"] == "task_board_reader_unavailable"
