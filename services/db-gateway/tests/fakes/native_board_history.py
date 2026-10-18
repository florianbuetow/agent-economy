"""HTTP-only builders for real MP-07 Board histories in Gateway tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from db_gateway_service.task_time import parse_utc_text_to_epoch_us
from tests.conftest import make_event

if TYPE_CHECKING:
    import httpx
    from fastapi.testclient import TestClient


FIXED_NOW_TEXT = "2099-01-01T12:00:00Z"
FIXED_NOW_US = parse_utc_text_to_epoch_us(FIXED_NOW_TEXT)
BIDDING_DEADLINE_TEXT = "2099-01-02T00:00:00Z"
BIDDING_CUTOFF_US = parse_utc_text_to_epoch_us(BIDDING_DEADLINE_TEXT)
ACCEPTANCE_WINDOW_SECONDS = 3600
ACCEPTANCE_CUTOFF_US = BIDDING_CUTOFF_US + ACCEPTANCE_WINDOW_SECONDS * 1_000_000


@dataclass
class BusinessClock:
    """Mutable fixed business clock; JWT auth continues using the real clock."""

    now_us: int = FIXED_NOW_US

    def __call__(self) -> int:
        return self.now_us


@dataclass(frozen=True)
class TaskGraph:
    """Registered, funded and escrow-backed published task identifiers."""

    task_id: str
    poster_id: str
    escrow_id: str


def register_agent(
    client: TestClient,
    *,
    agent_id: str | None = None,
    name: str = "Test",
    public_key: str | None = None,
) -> str:
    """Register one real Identity row through the signed Gateway route."""
    resolved_id = agent_id or f"a-{uuid4()}"
    response = client.post(
        "/identity/agents",
        json={
            "agent_id": resolved_id,
            "name": name,
            "public_key": public_key or f"ed25519:{uuid4()}",
            "registered_at": "2099-01-01T11:59:00Z",
            "event": make_event(
                source="identity",
                event_type="agent.registered",
                agent_id=resolved_id,
            ),
        },
    )
    assert response.status_code == 201, response.text
    return resolved_id


def create_account(client: TestClient, agent_id: str, *, balance: int) -> None:
    """Create one real Bank account, including receipt-backed initial credit."""
    body: dict[str, Any] = {
        "account_id": agent_id,
        "balance": balance,
        "created_at": "2099-01-01T11:59:01Z",
        "event": make_event(
            source="bank",
            event_type="account.created",
            agent_id=agent_id,
        ),
    }
    if balance:
        body.update(
            {
                "operation_id": f"op-{uuid4()}",
                "initial_credit": {
                    "tx_id": f"tx-{uuid4()}",
                    "amount": balance,
                    "reference": "initial_balance",
                    "timestamp": "2099-01-01T11:59:01Z",
                },
            }
        )
    response = client.post("/bank/accounts", json=body)
    assert response.status_code == 201, response.text


def register_worker(client: TestClient, *, name: str = "Worker") -> str:
    """Register a worker with the zero-balance Bank account settlement needs."""
    worker_id = register_agent(client, name=name)
    create_account(client, worker_id, balance=0)
    return worker_id


def lock_escrow(
    client: TestClient,
    poster_id: str,
    *,
    task_id: str | None = None,
    reward: int = 100,
) -> tuple[str, str]:
    """Lock the task reward through the real Bank route."""
    resolved_task_id = task_id or f"t-{uuid4()}"
    escrow_id = f"esc-{uuid4()}"
    response = client.post(
        "/bank/escrow/lock",
        json={
            "operation_id": f"op-{uuid4()}",
            "escrow_id": escrow_id,
            "payer_account_id": poster_id,
            "amount": reward,
            "task_id": resolved_task_id,
            "created_at": "2099-01-01T11:59:02Z",
            "tx_id": f"tx-{uuid4()}",
            "event": make_event(
                source="bank",
                event_type="escrow.locked",
                task_id=resolved_task_id,
            ),
        },
    )
    assert response.status_code == 201, response.text
    return escrow_id, resolved_task_id


def task_body(
    task_id: str,
    poster_id: str,
    escrow_id: str,
    *,
    reward: int = 100,
) -> dict[str, Any]:
    """Return one coherent timing-v2 publication body."""
    return {
        "task_id": task_id,
        "poster_id": poster_id,
        "title": "Test Task",
        "spec": "Build a login page",
        "reward": reward,
        "status": "open",
        "bidding_deadline_seconds": 43200,
        "acceptance_window_seconds": ACCEPTANCE_WINDOW_SECONDS,
        "deadline_seconds": 172800,
        "review_deadline_seconds": 43200,
        "bidding_deadline": BIDDING_DEADLINE_TEXT,
        "bidding_cutoff_us": BIDDING_CUTOFF_US,
        "acceptance_cutoff_us": ACCEPTANCE_CUTOFF_US,
        "escrow_id": escrow_id,
        "created_at": FIXED_NOW_TEXT,
        "event": make_event(source="board", event_type="task.created", task_id=task_id),
    }


def create_task(client: TestClient) -> TaskGraph:
    """Create a full registered/funded/locked/published task graph."""
    poster_id = register_agent(client, name="Poster")
    create_account(client, poster_id, balance=500)
    escrow_id, task_id = lock_escrow(client, poster_id)
    response = client.post(
        "/board/tasks",
        json=task_body(task_id, poster_id, escrow_id),
    )
    assert response.status_code == 201, response.text
    return TaskGraph(task_id, poster_id, escrow_id)


def operation_body(
    *,
    operation_id: str,
    task_id: str,
    command: str,
    expected_status: str,
    expected_lifecycle_version: int,
    actor_kind: str,
    actor_id: str,
    arguments: dict[str, object],
) -> dict[str, object]:
    """Compose the exact protocol-v1 task-operation envelope."""
    return {
        "protocol_version": 1,
        "operation_id": operation_id,
        "task_id": task_id,
        "command": command,
        "expected_status": expected_status,
        "expected_lifecycle_version": expected_lifecycle_version,
        "actor_kind": actor_kind,
        "actor_id": actor_id,
        "arguments": arguments,
    }


def submit_bid(
    client: TestClient,
    graph: TaskGraph,
    bidder_id: str,
    *,
    bid_id: str | None = None,
    amount: int = 90,
    operation_id: str | None = None,
) -> tuple[str, httpx.Response]:
    """Submit a bid through the native HTTP command endpoint."""
    resolved_bid_id = bid_id or f"bid-{uuid4()}"
    response = client.post(
        "/board/task-operations",
        json=operation_body(
            operation_id=operation_id or f"op-{uuid4()}",
            task_id=graph.task_id,
            command="submit_bid",
            expected_status="open",
            expected_lifecycle_version=0,
            actor_kind="agent",
            actor_id=bidder_id,
            arguments={
                "bid_id": resolved_bid_id,
                "bidder_id": bidder_id,
                "amount": amount,
            },
        ),
    )
    return resolved_bid_id, response


def accept_bid(
    client: TestClient,
    graph: TaskGraph,
    worker_id: str,
    bid_id: str,
    *,
    bid_amount: int = 90,
) -> httpx.Response:
    """Accept one stored bid through the pure native command."""
    return client.post(
        "/board/task-operations",
        json=operation_body(
            operation_id=f"op-{uuid4()}",
            task_id=graph.task_id,
            command="accept_bid",
            expected_status="open",
            expected_lifecycle_version=0,
            actor_kind="agent",
            actor_id=graph.poster_id,
            arguments={
                "bid_id": bid_id,
                "worker_id": worker_id,
                "escrow_id": graph.escrow_id,
                "reward": 100,
                "bid_amount": bid_amount,
            },
        ),
    )


def record_asset(client: TestClient, graph: TaskGraph, worker_id: str) -> str:
    """Persist one real worker asset required for submission."""
    asset_id = f"asset-{uuid4()}"
    response = client.post(
        "/board/assets",
        json={
            "asset_id": asset_id,
            "task_id": graph.task_id,
            "uploader_id": worker_id,
            "filename": "deliverable.txt",
            "content_type": "text/plain",
            "size_bytes": 7,
            "storage_path": f"assets/{graph.task_id}/deliverable.txt",
            "content_hash": "a" * 64,
            "uploaded_at": "2099-01-01T12:00:01Z",
            "event": make_event(
                source="board",
                event_type="asset.uploaded",
                task_id=graph.task_id,
            ),
        },
    )
    assert response.status_code == 201, response.text
    return asset_id


def prepare_submitted(client: TestClient, graph: TaskGraph) -> tuple[str, str]:
    """Build the real bid -> accept -> asset -> submission history."""
    worker_id = register_worker(client)
    bid_id, bid = submit_bid(client, graph, worker_id)
    assert bid.status_code == 200, bid.text
    accepted = accept_bid(client, graph, worker_id, bid_id)
    assert accepted.status_code == 200, accepted.text
    record_asset(client, graph, worker_id)
    submitted = client.post(
        "/board/task-operations",
        json=operation_body(
            operation_id=f"op-{uuid4()}",
            task_id=graph.task_id,
            command="submit_deliverable",
            expected_status="accepted",
            expected_lifecycle_version=1,
            actor_kind="agent",
            actor_id=worker_id,
            arguments={"worker_id": worker_id},
        ),
    )
    assert submitted.status_code == 200, submitted.text
    return worker_id, bid_id


def settle_release(
    client: TestClient,
    graph: TaskGraph,
    *,
    operation_id: str,
    recipient_id: str,
    source_reference: str,
) -> httpx.Response:
    """Persist a real Bank release receipt for a claimed Board operation."""
    return client.post(
        "/bank/escrow/release",
        json={
            "operation_id": operation_id,
            "escrow_id": graph.escrow_id,
            "recipient_account_id": recipient_id,
            "source_reference": source_reference,
            "tx_id": f"tx-{uuid4()}",
            "resolved_at": "2099-01-01T12:00:02Z",
            "event": make_event(
                source="central-bank",
                event_type="escrow.released",
                task_id=graph.task_id,
            ),
        },
    )


def settle_split(
    client: TestClient,
    graph: TaskGraph,
    *,
    operation_id: str,
    worker_id: str,
    worker_pct: int,
    source_reference: str,
) -> httpx.Response:
    """Persist a real Bank split receipt for a claimed ruling operation."""
    return client.post(
        "/bank/escrow/split",
        json={
            "operation_id": operation_id,
            "escrow_id": graph.escrow_id,
            "worker_account_id": worker_id,
            "poster_account_id": graph.poster_id,
            "worker_pct": worker_pct,
            "source_reference": source_reference,
            "worker_tx_id": f"tx-{uuid4()}",
            "poster_tx_id": f"tx-{uuid4()}",
            "resolved_at": "2099-01-01T12:00:03Z",
            "event": make_event(
                source="central-bank",
                event_type="escrow.split",
                task_id=graph.task_id,
            ),
        },
    )


def finalize(client: TestClient, operation_id: str) -> httpx.Response:
    """Finalize a claimed operation from Gateway-owned evidence only."""
    return client.post(f"/board/task-operations/{operation_id}/finalize", json={})


def file_claim(
    client: TestClient,
    graph: TaskGraph,
    *,
    worker_id: str,
    claim_id: str,
    reason: str,
) -> httpx.Response:
    """Persist a real Court claim matching a pending dispute operation."""
    return client.post(
        "/court/claims",
        json={
            "claim_id": claim_id,
            "task_id": graph.task_id,
            "claimant_id": graph.poster_id,
            "respondent_id": worker_id,
            "reason": reason,
            "status": "rebuttal_pending",
            "rebuttal_deadline": "2099-01-01T13:00:00Z",
            "filed_at": "2099-01-01T12:00:02Z",
            "event": make_event(
                source="court",
                event_type="claim.filed",
                task_id=graph.task_id,
            ),
        },
    )
