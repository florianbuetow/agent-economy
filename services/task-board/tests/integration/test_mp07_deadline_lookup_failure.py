"""MP-07 deadline bid lookup failures preserve readable source state."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from service_auth.financial_identity import operation_id_for
from service_commons.exceptions import ServiceError

from task_board_service.core.state import get_app_state
from task_board_service.services import deadline_evaluator as deadline_module
from task_board_service.task_operation_contract import invalid_gateway_response
from tests.integration import test_mp07_native_deadlines as deadline_support

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _support(name: str) -> Any:
    return getattr(deadline_support, name)


@dataclass
class _WarningLogger:
    warnings: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    def warning(self, message: str, *, extra: dict[str, object]) -> None:
        self.warnings.append((message, extra))


def _rows(
    db_path: str,
    query: str,
    params: tuple[object, ...] = (),
) -> list[dict[str, object]]:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(query, params).fetchall()]
    finally:
        connection.close()


def _task_from_response(response: httpx.Response, task_id: str, view: str) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    payload = response.json()
    if view == "detail":
        assert isinstance(payload, dict)
        return payload
    assert isinstance(payload, dict) and isinstance(payload.get("tasks"), list)
    task = next(item for item in payload["tasks"] if item["task_id"] == task_id)
    assert isinstance(task, dict)
    return task


@pytest.mark.parametrize("view", ["detail", "list"])
@pytest.mark.parametrize("failure", ["transport", "typed-503"])
async def test_retryable_bid_lookup_failure_returns_fresh_source_projection(
    client: httpx.AsyncClient,
    gateway_client: httpx.AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
    view: str,
    failure: str,
) -> None:
    install_clock = _support("_install_clock")
    create_task = _support("_create_task")
    install_bank = _support("_install_bank_probe")
    start = _support("_START")
    bidding_seconds = int(_support("_BIDDING_SECONDS"))
    clock = install_clock(monkeypatch)
    task = await create_task(client, gateway_client, poster_keypair[0])
    cutoff_us = clock.epoch_us() + bidding_seconds * 1_000_000
    operation_id = operation_id_for(
        "task-deadline",
        (task.task_id, "expire_bidding", "0", str(cutoff_us)),
    )
    probe = install_bank(
        gateway_client,
        gateway_db_path,
        source_status="open",
        operation_id=operation_id,
        fail=False,
    )
    state = get_app_state()
    assert state.store is not None
    original_lookup = state.store.get_bids_for_task

    def failed_lookup(_task_id: str) -> list[dict[str, Any]]:
        if failure == "transport":
            request = httpx.Request("GET", "http://gateway.test/board/tasks/bids")
            raise httpx.ReadError("injected bid lookup transport failure", request=request)
        raise ServiceError("gateway_busy", "injected bid lookup 503", 503, {})

    monkeypatch.setattr(state.store, "get_bids_for_task", failed_lookup)
    logger = _WarningLogger()

    def selected_logger(_name: str) -> _WarningLogger:
        return logger

    monkeypatch.setattr(deadline_module, "get_logger", selected_logger)
    clock.set(start + timedelta(seconds=bidding_seconds))
    path = f"/tasks/{task.task_id}" if view == "detail" else "/tasks?status=open"

    first = await client.get(path)
    projected = _task_from_response(first, task.task_id, view)

    assert projected["status"] == "open"
    assert projected["pending_transition"] is None
    assert len(logger.warnings) == 1
    assert probe.calls == []
    assert (
        _rows(
            gateway_db_path,
            "SELECT operation_id FROM board_task_operations WHERE task_id=?",
            (task.task_id,),
        )
        == []
    )
    assert (
        _rows(
            gateway_db_path,
            "SELECT event_id FROM events WHERE task_id=? AND event_type='task.expired'",
            (task.task_id,),
        )
        == []
    )

    monkeypatch.setattr(state.store, "get_bids_for_task", original_lookup)
    recovered = await client.get(f"/tasks/{task.task_id}")

    assert recovered.status_code == 200
    assert recovered.json()["status"] == "expired"
    assert probe.claim_seen == [True]
    assert len(probe.calls) == 1
    assert _rows(
        gateway_db_path,
        "SELECT operation_state,effect_state,command FROM board_task_operations "
        "WHERE operation_id=?",
        (operation_id,),
    ) == [
        {
            "operation_state": "completed",
            "effect_state": "succeeded",
            "command": "expire_bidding",
        }
    ]
    assert _rows(
        gateway_db_path,
        "SELECT operation_id FROM bank_operation_receipts WHERE operation_id=?",
        (operation_id,),
    ) == [{"operation_id": operation_id}]


async def test_malformed_bid_lookup_success_remains_visible_502(
    client: httpx.AsyncClient,
    gateway_client: httpx.AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_clock = _support("_install_clock")
    create_task = _support("_create_task")
    start = _support("_START")
    bidding_seconds = int(_support("_BIDDING_SECONDS"))
    clock = install_clock(monkeypatch)
    task = await create_task(client, gateway_client, poster_keypair[0])
    state = get_app_state()
    assert state.store is not None

    def malformed_lookup(_task_id: str) -> list[dict[str, Any]]:
        raise invalid_gateway_response()

    monkeypatch.setattr(state.store, "get_bids_for_task", malformed_lookup)
    logger = _WarningLogger()

    def selected_logger(_name: str) -> _WarningLogger:
        return logger

    monkeypatch.setattr(deadline_module, "get_logger", selected_logger)
    clock.set(start + timedelta(seconds=bidding_seconds))

    response = await client.get(f"/tasks/{task.task_id}")

    assert response.status_code == 502
    assert response.json()["error"] == "invalid_gateway_response"
    assert logger.warnings == []
    assert (
        _rows(
            gateway_db_path,
            "SELECT operation_id FROM board_task_operations WHERE task_id=?",
            (task.task_id,),
        )
        == []
    )
