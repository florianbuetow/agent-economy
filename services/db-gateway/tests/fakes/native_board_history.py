"""HTTP-only builders for real MP-07 Board histories in Gateway tests."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from service_auth.financial_identity import operation_id_for

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


def publish_task(
    client: TestClient,
    *,
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
) -> httpx.Response:
    """Admit, fund, and reconcile one ordinary task through signed native routes."""

    operation_id = operation_id_for("gateway-native-http-fixture", (task_id, "publication"))
    lock_operation_id = operation_id_for("task-lock", (task_id, poster_id))
    intent = {
        "protocol_version": 1,
        "operation_id": operation_id,
        "task_id": task_id,
        "actor_kind": "agent",
        "actor_id": poster_id,
        "lock_operation_id": lock_operation_id,
        "title": title,
        "spec": spec,
        "reward": reward,
        "bidding_deadline_seconds": bidding_deadline_seconds,
        "acceptance_window_seconds": acceptance_window_seconds,
        "execution_deadline_seconds": execution_deadline_seconds,
        "review_deadline_seconds": review_deadline_seconds,
    }
    admitted = client.post("/board/task-publications", json=intent)
    assert admitted.status_code == 202, admitted.text
    locked = client.post(
        "/bank/escrow/lock",
        json={
            "operation_id": lock_operation_id,
            "escrow_id": escrow_id,
            "payer_account_id": poster_id,
            "amount": reward,
            "task_id": task_id,
            "created_at": lock_timestamp,
            "tx_id": f"tx-{uuid4()}",
            "event": {
                **make_event(
                    source="central-bank",
                    event_type="escrow.locked",
                    task_id=task_id,
                    agent_id=poster_id,
                ),
                "timestamp": lock_timestamp,
            },
        },
    )
    assert locked.status_code == 201, locked.text
    reconciled = client.post(f"/board/task-publications/{operation_id}/reconcile", json={})
    assert reconciled.status_code == 201, reconciled.text
    return reconciled


def create_task(client: TestClient) -> TaskGraph:
    """Create a full registered/funded/locked/published task graph."""
    poster_id = register_agent(client, name="Poster")
    create_account(client, poster_id, balance=500)
    task_id = f"t-{uuid4()}"
    escrow_id = f"esc-{uuid4()}"
    publish_task(
        client,
        task_id=task_id,
        poster_id=poster_id,
        escrow_id=escrow_id,
        title="Test Task",
        spec="Build a login page",
        reward=100,
        bidding_deadline_seconds=43200,
        acceptance_window_seconds=ACCEPTANCE_WINDOW_SECONDS,
        execution_deadline_seconds=172800,
        review_deadline_seconds=43200,
        lock_timestamp="2099-01-01T11:59:02Z",
    )
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
    protocol_version: int = 1,
) -> dict[str, object]:
    """Compose the exact task-operation envelope."""
    return {
        "protocol_version": protocol_version,
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
    amount: int = 100,
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
    bid_amount: int = 100,
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


_ARTIFACT_BODY = b"deliverable\n"
_ARTIFACT_POLICY: dict[str, object] = {
    "contract_version": 1,
    "max_file_bytes": 1024,
    "max_files_per_manifest": 4,
    "max_manifest_bytes": 4096,
    "reservation_ttl_seconds": 300,
    "reservation_renew_before_expiry_seconds": 60,
    "max_upload_duration_seconds": 900,
}


def record_artifact(client: TestClient, graph: TaskGraph, worker_id: str) -> tuple[str, str, str]:
    """Reserve and finalize one governed worker artifact through the public routes."""
    context = client.get(f"/board/tasks/{graph.task_id}/artifact-context")
    assert context.status_code == 200, context.text
    binding = context.json()
    assert binding["governed"] is True, binding
    manifest_id = binding["submission_manifest_id"]
    content_sha256 = hashlib.sha256(_ARTIFACT_BODY).hexdigest()
    reserve = client.post(
        "/board/artifact-uploads/reserve",
        json={
            "policy": _ARTIFACT_POLICY,
            "request": {
                "protocol_version": 1,
                "upload_operation_id": str(uuid4()),
                "resource": "POST /board/artifact-uploads/reserve",
                "task_id": graph.task_id,
                "manifest_id": manifest_id,
                "actor_id": worker_id,
                "actor_role": "worker",
                "display_filename": "deliverable.txt",
                "evidence_format": "text",
                "declared_size_bytes": len(_ARTIFACT_BODY),
                "content_sha256": content_sha256,
                "expected_lifecycle_version": 1,
                "accepted_binding_kind": binding["accepted_binding_kind"],
                "accepted_binding_sha256": binding["accepted_binding_sha256"],
            },
        },
    )
    assert reserve.status_code == 200, reserve.text
    reservation = reserve.json()
    attempt = reservation["attempt"]
    upload_operation_id = reservation["upload_operation_id"]
    finalized = client.post(
        f"/board/artifact-uploads/{upload_operation_id}/finalize",
        json={
            "policy": _ARTIFACT_POLICY,
            "owner_id": attempt["owner_id"],
            "generation": attempt["generation"],
            "attempt_nonce": attempt["attempt_nonce"],
            "observed_size_bytes": len(_ARTIFACT_BODY),
            "observed_sha256": content_sha256,
        },
    )
    assert finalized.status_code == 200, finalized.text
    return manifest_id, binding["accepted_binding_kind"], binding["accepted_binding_sha256"]


def prepare_submitted(client: TestClient, graph: TaskGraph) -> tuple[str, str]:
    """Build the real bid -> accept -> governed artifact -> submission history."""
    worker_id = register_worker(client)
    bid_id, bid = submit_bid(client, graph, worker_id)
    assert bid.status_code == 200, bid.text
    accepted = accept_bid(client, graph, worker_id, bid_id)
    assert accepted.status_code == 200, accepted.text
    manifest_id, binding_kind, binding_sha256 = record_artifact(client, graph, worker_id)
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
            arguments={
                "worker_id": worker_id,
                "manifest_id": manifest_id,
                "accepted_binding_kind": binding_kind,
                "accepted_binding_sha256": binding_sha256,
            },
            protocol_version=2,
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
