"""Shared native Task Board posting setup for integration tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from db_gateway_service.task_time import epoch_us_to_utc_text
from service_auth.financial_identity import operation_id_for
from service_auth.task_postings import TaskPostingIntent

from task_board_service.core.state import get_app_state

if TYPE_CHECKING:
    from collections.abc import Callable

    import httpx


async def persist_lock_after_admission(
    gateway_client: httpx.AsyncClient,
    *,
    task_payload: dict[str, Any],
    escrow_id: str,
    clock_us: Callable[[], int],
) -> dict[str, Any]:
    """Persist the real Bank lock only after Gateway proves Board admission."""
    intent = TaskPostingIntent.from_json(
        {
            "protocol_version": task_payload["protocol_version"],
            "operation_id": task_payload["operation_id"],
            "task_id": task_payload["task_id"],
            "actor_kind": "agent",
            "actor_id": task_payload["poster_id"],
            "lock_operation_id": task_payload["lock_operation_id"],
            "title": task_payload["title"],
            "spec": task_payload["spec"],
            "reward": task_payload["reward"],
            "bidding_deadline_seconds": task_payload["bidding_deadline_seconds"],
            "acceptance_window_seconds": task_payload["acceptance_window_seconds"],
            "execution_deadline_seconds": task_payload["execution_deadline_seconds"],
            "review_deadline_seconds": task_payload["review_deadline_seconds"],
        }
    )
    state = get_app_state()
    assert state.store is not None
    publication = state.store.get_task_publication(intent.operation_id, intent.actor_id)
    assert publication.status_code == 200
    assert publication.body["publication_state"] == "awaiting_lock"
    assert publication.body["operation_id"] == intent.operation_id
    assert publication.body["task_id"] == intent.task_id
    assert publication.body["lock_operation_id"] == intent.lock_operation_id
    assert publication.body["request_sha256"] == intent.request_sha256

    timestamp = epoch_us_to_utc_text(clock_us())
    response = await gateway_client.post(
        "/bank/escrow/lock",
        json={
            "operation_id": intent.lock_operation_id,
            "escrow_id": escrow_id,
            "payer_account_id": intent.actor_id,
            "amount": intent.reward,
            "task_id": intent.task_id,
            "created_at": timestamp,
            "tx_id": f"tx-{uuid4()}",
            "event": {
                "event_source": "central-bank",
                "event_type": "escrow.locked",
                "timestamp": timestamp,
                "task_id": intent.task_id,
                "agent_id": intent.actor_id,
                "summary": f"Locked escrow for {intent.task_id}",
                "payload": "{}",
            },
        },
    )
    assert response.status_code == 201, response.text
    receipt = cast("dict[str, Any]", response.json())
    assert receipt["outcome"] == "native_receipt"
    assert receipt["receipt"]["operation_id"] == intent.lock_operation_id
    assert receipt["receipt"]["operation_kind"] == "escrow_lock"
    assert receipt["current_escrow"]["task_id"] == intent.task_id
    assert receipt["current_escrow"]["payer_account_id"] == intent.actor_id
    assert receipt["current_escrow"]["amount"] == intent.reward
    assert receipt["current_escrow"]["created_at"] == timestamp
    return receipt


def native_task_payload(
    *,
    task_id: str,
    poster_id: str,
    title: str,
    spec: str,
    reward: int,
    bidding_deadline_seconds: int,
    acceptance_window_seconds: int,
    execution_deadline_seconds: int,
    review_deadline_seconds: int,
) -> dict[str, Any]:
    """Build a complete signed task command and its deterministic lock identity."""
    return {
        "action": "create_task",
        "protocol_version": 1,
        "operation_id": f"op-{uuid4()}",
        "task_id": task_id,
        "poster_id": poster_id,
        "lock_operation_id": operation_id_for("task-lock", (task_id, poster_id)),
        "title": title,
        "spec": spec,
        "reward": reward,
        "bidding_deadline_seconds": bidding_deadline_seconds,
        "acceptance_window_seconds": acceptance_window_seconds,
        "execution_deadline_seconds": execution_deadline_seconds,
        "review_deadline_seconds": review_deadline_seconds,
    }


def native_escrow_payload(task_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": "escrow_lock",
        "operation_id": task_payload["lock_operation_id"],
        "task_id": task_payload["task_id"],
        "agent_id": task_payload["poster_id"],
        "amount": task_payload["reward"],
    }
