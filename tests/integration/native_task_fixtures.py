"""Native Task Board fixture transitions through the private DB Gateway API."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from uuid import uuid4

from service_auth.financial_identity import operation_id_for
from service_auth.task_postings import TaskPostingIntent

from db_gateway_service import task_time
from db_gateway_service.task_time import (
    epoch_us_to_utc_text,
    parse_utc_text_to_epoch_us,
)

if TYPE_CHECKING:
    import httpx
    import pytest


def freeze_task_clock(monkeypatch: pytest.MonkeyPatch, timestamp: str) -> None:
    """Use one deterministic instant for native task claims and finalization."""
    now_us = parse_utc_text_to_epoch_us(timestamp)
    monkeypatch.setattr(task_time, "utc_now_us", lambda: now_us)


async def publish_open_task(
    client: httpx.AsyncClient,
    *,
    task_id: str,
    poster_id: str,
    title: str,
    spec: str,
    reward: int,
    escrow_id: str,
    bidding_deadline_seconds: int,
    acceptance_window_seconds: int,
    execution_deadline_seconds: int,
    review_deadline_seconds: int,
) -> httpx.Response:
    """Admit, fund, and reconcile an ordinary task through native Gateway routes."""
    intent = TaskPostingIntent.from_json(
        {
            "protocol_version": 1,
            "operation_id": operation_id_for(
                "cross-service-task-publication",
                (task_id, poster_id),
            ),
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
    )
    admitted = await client.post("/board/task-publications", json=intent.as_json())
    assert admitted.status_code == 202, admitted.text
    admission = admitted.json()
    assert admission["publication_state"] == "awaiting_lock"
    assert admission["operation_id"] == intent.operation_id
    assert admission["task_id"] == intent.task_id
    assert admission["lock_operation_id"] == intent.lock_operation_id
    lock_timestamp = epoch_us_to_utc_text(
        parse_utc_text_to_epoch_us(admission["admitted_at"])
    )

    lock_response = await client.post(
        "/bank/escrow/lock",
        json={
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
                "agent_id": poster_id,
                "task_id": task_id,
                "summary": f"Escrow locked for {task_id}",
                "payload": json.dumps({"escrow_id": escrow_id, "amount": reward}),
            },
        },
    )
    assert lock_response.status_code == 201, lock_response.text
    lock_envelope = lock_response.json()
    assert lock_envelope["outcome"] == "native_receipt"
    assert lock_envelope["receipt"]["operation_id"] == intent.lock_operation_id
    assert lock_envelope["receipt"]["operation_kind"] == "escrow_lock"
    assert lock_envelope["receipt"]["source_reference"] == task_id
    assert lock_envelope["current_escrow"]["escrow_id"] == escrow_id
    assert lock_envelope["current_escrow"]["task_id"] == task_id
    assert lock_envelope["current_escrow"]["payer_account_id"] == poster_id
    assert lock_envelope["current_escrow"]["amount"] == reward
    assert lock_envelope["current_escrow"]["status"] == "locked"
    assert lock_envelope["current_escrow"]["created_at"] == lock_timestamp

    reconciled = await client.post(
        f"/board/task-publications/{intent.operation_id}/reconcile",
        json={},
    )
    assert reconciled.status_code == 201, reconciled.text
    envelope = reconciled.json()
    assert envelope["publication_state"] == "published"
    assert envelope["operation_id"] == intent.operation_id
    assert envelope["task_id"] == task_id
    assert envelope["lock_operation_id"] == intent.lock_operation_id
    assert envelope["escrow_id"] == escrow_id
    result = envelope["result"]
    assert result["task_id"] == task_id
    assert result["poster_id"] == poster_id
    assert result["title"] == title
    assert result["spec"] == spec
    assert result["reward"] == reward
    assert result["status"] == "open"
    assert isinstance(result["event_id"], int)
    return reconciled


async def claim_task_operation(
    client: httpx.AsyncClient,
    *,
    operation_id: str,
    task_id: str,
    command: str,
    expected_status: str,
    expected_lifecycle_version: int,
    actor_kind: str,
    actor_id: str,
    arguments: dict[str, str | int],
) -> httpx.Response:
    """Submit one explicit native command and return its actual response."""
    return await client.post(
        "/board/task-operations",
        json={
            "protocol_version": 1,
            "operation_id": operation_id,
            "task_id": task_id,
            "command": command,
            "expected_status": expected_status,
            "expected_lifecycle_version": expected_lifecycle_version,
            "actor_kind": actor_kind,
            "actor_id": actor_id,
            "arguments": arguments,
        },
    )


async def submit_native_bid(
    client: httpx.AsyncClient,
    *,
    operation_id: str,
    task_id: str,
    bid_id: str,
    bidder_id: str,
    amount: int,
) -> httpx.Response:
    return await claim_task_operation(
        client,
        operation_id=operation_id,
        task_id=task_id,
        command="submit_bid",
        expected_status="open",
        expected_lifecycle_version=0,
        actor_kind="agent",
        actor_id=bidder_id,
        arguments={"bid_id": bid_id, "bidder_id": bidder_id, "amount": amount},
    )


async def accept_native_bid(
    client: httpx.AsyncClient,
    *,
    operation_id: str,
    task_id: str,
    bid_id: str,
    poster_id: str,
    worker_id: str,
    escrow_id: str,
    reward: int,
    bid_amount: int,
) -> httpx.Response:
    return await claim_task_operation(
        client,
        operation_id=operation_id,
        task_id=task_id,
        command="accept_bid",
        expected_status="open",
        expected_lifecycle_version=0,
        actor_kind="agent",
        actor_id=poster_id,
        arguments={
            "bid_id": bid_id,
            "worker_id": worker_id,
            "escrow_id": escrow_id,
            "reward": reward,
            "bid_amount": bid_amount,
        },
    )


async def advance_task_to_submitted(
    client: httpx.AsyncClient,
    *,
    task_id: str,
    poster_id: str,
    worker_id: str,
    escrow_id: str,
    reward: int,
    operation_suffix: str,
    timestamp: str,
) -> None:
    """Drive an open task to submitted through persisted native commands."""
    bid_id = f"bid-{operation_suffix}"
    bid = await submit_native_bid(
        client,
        operation_id=f"op-00000000-0000-4000-8000-{operation_suffix}01",
        task_id=task_id,
        bid_id=bid_id,
        bidder_id=worker_id,
        amount=reward,
    )
    assert bid.status_code == 200, bid.text
    accepted = await accept_native_bid(
        client,
        operation_id=f"op-00000000-0000-4000-8000-{operation_suffix}02",
        task_id=task_id,
        bid_id=bid_id,
        poster_id=poster_id,
        worker_id=worker_id,
        escrow_id=escrow_id,
        reward=reward,
        bid_amount=reward,
    )
    assert accepted.status_code == 200, accepted.text
    asset = await client.post(
        "/board/assets",
        json={
            "asset_id": f"asset-{operation_suffix}",
            "task_id": task_id,
            "uploader_id": worker_id,
            "filename": "fixture.txt",
            "content_type": "text/plain",
            "size_bytes": 7,
            "storage_path": f"/data/assets/asset-{operation_suffix}",
            "content_hash": f"sha256:{operation_suffix}",
            "uploaded_at": timestamp,
            "constraints": {"status": "accepted"},
            "event": {
                "event_source": "board",
                "event_type": "asset.uploaded",
                "timestamp": timestamp,
                "agent_id": worker_id,
                "task_id": task_id,
                "summary": "Fixture asset uploaded",
                "payload": "{}",
            },
        },
    )
    assert asset.status_code == 201, asset.text
    submitted = await claim_task_operation(
        client,
        operation_id=f"op-00000000-0000-4000-8000-{operation_suffix}03",
        task_id=task_id,
        command="submit_deliverable",
        expected_status="accepted",
        expected_lifecycle_version=1,
        actor_kind="agent",
        actor_id=worker_id,
        arguments={"worker_id": worker_id},
    )
    assert submitted.status_code == 200, submitted.text


async def approve_with_bank_receipt(
    client: httpx.AsyncClient,
    *,
    task_id: str,
    poster_id: str,
    worker_id: str,
    escrow_id: str,
    reward: int,
    operation_id: str,
    timestamp: str,
) -> tuple[httpx.Response, httpx.Response, httpx.Response]:
    """Claim approval, persist its real Bank receipt, and finalize from evidence."""
    source_reference = f"task:{task_id}:approved"
    claim = await claim_task_operation(
        client,
        operation_id=operation_id,
        task_id=task_id,
        command="approve_task",
        expected_status="submitted",
        expected_lifecycle_version=2,
        actor_kind="agent",
        actor_id=poster_id,
        arguments={
            "poster_id": poster_id,
            "worker_id": worker_id,
            "escrow_id": escrow_id,
            "reward": reward,
            "source_reference": source_reference,
        },
    )
    receipt = await client.post(
        "/bank/escrow/release",
        json={
            "operation_id": operation_id,
            "escrow_id": escrow_id,
            "recipient_account_id": worker_id,
            "source_reference": source_reference,
            "tx_id": f"tx-{operation_id[3:]}",
            "resolved_at": timestamp,
            "event": {
                "event_source": "central-bank",
                "event_type": "escrow.released",
                "timestamp": timestamp,
                "agent_id": worker_id,
                "task_id": task_id,
                "summary": "Escrow released after approval",
                "payload": json.dumps({"escrow_id": escrow_id, "amount": reward}),
            },
        },
    )
    finalized = await client.post(
        f"/board/task-operations/{operation_id}/finalize", json={}
    )
    return claim, receipt, finalized


async def claim_dispute(
    client: httpx.AsyncClient,
    *,
    task_id: str,
    poster_id: str,
    worker_id: str,
    escrow_id: str,
    reward: int,
    reason: str,
    operation_id: str,
) -> httpx.Response:
    return await claim_task_operation(
        client,
        operation_id=operation_id,
        task_id=task_id,
        command="dispute_task",
        expected_status="submitted",
        expected_lifecycle_version=2,
        actor_kind="agent",
        actor_id=poster_id,
        arguments={
            "poster_id": poster_id,
            "worker_id": worker_id,
            "escrow_id": escrow_id,
            "reward": reward,
            "reason": reason,
        },
    )


async def finalize_task_operation(
    client: httpx.AsyncClient, operation_id: str
) -> httpx.Response:
    return await client.post(f"/board/task-operations/{operation_id}/finalize", json={})
