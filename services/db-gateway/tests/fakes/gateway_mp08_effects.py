"""Public-route builders for MP-08 Gateway delivery acceptance tests."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from service_auth.signing import load_public_key, public_key_to_b64

from tests.fakes.native_board_history import (
    create_task,
    file_claim,
    finalize,
    prepare_submitted,
    register_agent,
)

if TYPE_CHECKING:
    import httpx
    from fastapi.testclient import TestClient


@dataclass(frozen=True, slots=True)
class PendingEffect:
    task_id: str
    operation_id: str
    poster_id: str
    worker_id: str
    escrow_id: str
    source_reference: str


def prepare_native_pending(client: TestClient) -> PendingEffect:
    """Claim one real approval release and leave its Bank effect pending."""
    graph = create_task(client)
    worker_id, _bid_id = prepare_submitted(client, graph)
    operation_id = f"op-{uuid4()}"
    response = client.post(
        "/board/task-operations",
        json={
            "protocol_version": 1,
            "operation_id": operation_id,
            "task_id": graph.task_id,
            "command": "approve_task",
            "expected_status": "submitted",
            "expected_lifecycle_version": 2,
            "actor_kind": "agent",
            "actor_id": graph.poster_id,
            "arguments": {
                "poster_id": graph.poster_id,
                "worker_id": worker_id,
                "escrow_id": graph.escrow_id,
                "reward": 100,
                "source_reference": f"task:{graph.task_id}:approved",
            },
        },
    )
    assert response.status_code == 202, response.text
    assert response.json()["operation_id"] == operation_id
    return PendingEffect(
        graph.task_id,
        operation_id,
        graph.poster_id,
        worker_id,
        graph.escrow_id,
        f"task:{graph.task_id}:approved",
    )


def prepare_native_refund_pending(client: TestClient) -> PendingEffect:
    """Claim one real open-task cancellation refund and leave it pending."""
    graph = create_task(client)
    task = client.get(f"/board/tasks/{graph.task_id}")
    assert task.status_code == 200, task.text
    cutoff_us = task.json()["bidding_cutoff_us"]
    assert type(cutoff_us) is int
    operation_id = f"op-{uuid4()}"
    response = client.post(
        "/board/task-operations",
        json={
            "protocol_version": 1,
            "operation_id": operation_id,
            "task_id": graph.task_id,
            "command": "cancel_task",
            "expected_status": "open",
            "expected_lifecycle_version": 0,
            "actor_kind": "agent",
            "actor_id": graph.poster_id,
            "arguments": {
                "poster_id": graph.poster_id,
                "escrow_id": graph.escrow_id,
                "reward": 100,
                "source_reference": f"task:{graph.task_id}:cancelled",
                "cutoff_us": cutoff_us,
            },
        },
    )
    assert response.status_code == 202, response.text
    return PendingEffect(
        graph.task_id,
        operation_id,
        graph.poster_id,
        graph.poster_id,
        graph.escrow_id,
        f"task:{graph.task_id}:cancelled",
    )


def prepare_native_split_pending(client: TestClient) -> PendingEffect:
    """Claim one real disputed-task split ruling and leave settlement pending."""
    graph = create_task(client)
    worker_id, _bid_id = prepare_submitted(client, graph)
    reason = "MP-08 split projection dispute"
    dispute_operation_id = f"op-{uuid4()}"
    disputed = client.post(
        "/board/task-operations",
        json={
            "protocol_version": 1,
            "operation_id": dispute_operation_id,
            "task_id": graph.task_id,
            "command": "dispute_task",
            "expected_status": "submitted",
            "expected_lifecycle_version": 2,
            "actor_kind": "agent",
            "actor_id": graph.poster_id,
            "arguments": {
                "poster_id": graph.poster_id,
                "worker_id": worker_id,
                "escrow_id": graph.escrow_id,
                "reward": 100,
                "reason": reason,
            },
        },
    )
    assert disputed.status_code == 202, disputed.text
    claim_id = f"clm-{uuid4()}"
    claim = file_claim(
        client,
        graph,
        worker_id=worker_id,
        claim_id=claim_id,
        reason=reason,
    )
    assert claim.status_code == 201, claim.text
    completed_dispute = finalize(client, dispute_operation_id)
    assert completed_dispute.status_code == 200, completed_dispute.text

    config_path = Path(os.environ["CONFIG_PATH"])
    platform_key = load_public_key(config_path.parent / "platform.pub")
    platform_id = register_agent(
        client,
        name="MP-08 platform",
        public_key=f"ed25519:{public_key_to_b64(platform_key)}",
    )
    operation_id = f"op-{uuid4()}"
    ruling = client.post(
        "/board/task-operations",
        json={
            "protocol_version": 1,
            "operation_id": operation_id,
            "task_id": graph.task_id,
            "command": "record_ruling",
            "expected_status": "disputed",
            "expected_lifecycle_version": 3,
            "actor_kind": "platform",
            "actor_id": platform_id,
            "arguments": {
                "poster_id": graph.poster_id,
                "worker_id": worker_id,
                "escrow_id": graph.escrow_id,
                "reward": 100,
                "ruling_id": claim_id,
                "worker_pct": 50,
                "summary": "Split the escrow equally.",
                "source_reference": f"task:{graph.task_id}:ruling:{claim_id}",
            },
        },
    )
    assert ruling.status_code == 202, ruling.text
    return PendingEffect(
        graph.task_id,
        operation_id,
        graph.poster_id,
        worker_id,
        graph.escrow_id,
        f"task:{graph.task_id}:ruling:{claim_id}",
    )


def lease(client: TestClient, worker_id: str) -> httpx.Response:
    response = client.post(
        "/board/task-effect-deliveries/lease",
        json={"protocol_version": 1, "worker_id": worker_id},
    )
    assert response.status_code == 200, (
        "Missing protected MP-08 delivery lease capability: " + response.text
    )
    return response


def delivery(client: TestClient, operation_id: str) -> dict[str, object]:
    response = client.get(f"/board/task-effect-deliveries/{operation_id}")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["protocol_version"] == 1
    return payload["delivery"]
