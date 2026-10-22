"""Shared current-schema task seeding through the real native publication writer."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from service_auth.financial_identity import operation_id_for
from service_auth.task_postings import TaskPostingIntent

from db_gateway_service.services.task_publication_writer import TaskPublicationWriter
from db_gateway_service.task_publication_requests import parse_task_publication_request

if TYPE_CHECKING:
    from db_gateway_service.services.db_writer import DbWriter


def native_publication_payload(
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
) -> dict[str, object]:
    """Return one complete valid native payload for admission and mutation tests."""

    return TaskPostingIntent.from_json(
        {
            "protocol_version": 1,
            "operation_id": operation_id_for("gateway-native-fixture", (task_id, "publication")),
            "task_id": task_id,
            "actor_kind": "agent",
            "actor_id": poster_id,
            "lock_operation_id": operation_id_for("task-lock", (task_id, poster_id)),
            "title": title,
            "spec": spec,
            "reward": reward,
            "bidding_deadline_seconds": bidding_deadline_seconds,
            "acceptance_window_seconds": acceptance_window_seconds,
            "execution_deadline_seconds": execution_deadline_seconds,
            "review_deadline_seconds": review_deadline_seconds,
        }
    ).as_json()


def publish_native_task(
    writer: DbWriter,
    *,
    caller_kid: str,
    clock_us: int,
    task_id: str,
    poster_id: str,
    escrow_id: str,
    title: str,
    spec: str,
    reward: int,
    bidding_deadline_seconds: int,
    acceptance_window_seconds: int,
    execution_deadline_seconds: int,
    review_deadline_seconds: int,
    lock_timestamp: str,
) -> TaskPostingIntent:
    """Admit, fund, and reconcile one fresh native task with real persisted proof."""

    payload = native_publication_payload(
        task_id=task_id,
        poster_id=poster_id,
        title=title,
        spec=spec,
        reward=reward,
        bidding_deadline_seconds=bidding_deadline_seconds,
        acceptance_window_seconds=acceptance_window_seconds,
        execution_deadline_seconds=execution_deadline_seconds,
        review_deadline_seconds=review_deadline_seconds,
    )
    intent = TaskPostingIntent.from_json(payload)
    publications = TaskPublicationWriter(writer.connection, clock_us=lambda: clock_us)
    admitted = publications.admit(parse_task_publication_request(payload), caller_kid)
    assert admitted["publication_state"] == "awaiting_lock"
    writer.escrow_lock(
        {
            "operation_id": intent.lock_operation_id,
            "escrow_id": escrow_id,
            "payer_account_id": poster_id,
            "amount": reward,
            "task_id": task_id,
            "created_at": lock_timestamp,
            "tx_id": f"tx-{uuid4()}",
            "event": {
                "event_source": "central-bank",
                "event_type": "escrow.locked",
                "timestamp": lock_timestamp,
                "task_id": task_id,
                "agent_id": poster_id,
                "summary": "escrow.locked",
                "payload": "{}",
            },
        }
    )
    published = publications.reconcile(intent.operation_id, caller_kid)
    assert published["publication_state"] == "published"
    assert published["task_id"] == task_id
    assert published["escrow_id"] == escrow_id
    return intent


__all__ = ["native_publication_payload", "publish_native_task"]
