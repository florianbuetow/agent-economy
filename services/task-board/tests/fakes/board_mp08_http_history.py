"""HTTP-only real Gateway histories for Task Board MP-08 integration tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from db_gateway_service.task_time import epoch_us_to_utc_text, utc_now_us
from service_auth.financial_identity import operation_id_for
from service_auth.gateway import GatewayRequestSigner, GatewaySignerConfig
from service_auth.signing import load_public_key, public_key_to_b64

from task_board_service.services.task_db_client import TaskDbClient

if TYPE_CHECKING:
    import httpx

Command = Literal["approve_task", "auto_approve", "cancel_task", "expire_bidding", "record_ruling"]


@dataclass(frozen=True, slots=True)
class RecoveryHistory:
    task_id: str
    poster_id: str
    worker_id: str | None
    escrow_id: str
    operation_id: str
    command: Command
    effect_kind: Literal["bank_release", "bank_split"]
    source_reference: str
    worker_pct: int | None


def make_task_store(gateway_base_url: str, gateway_db_path: str) -> TaskDbClient:
    """Build the production signed TaskDbClient for one disposable live Gateway."""
    key_dir = Path(gateway_db_path).parent
    signer = GatewayRequestSigner(
        GatewaySignerConfig(
            kid="gateway-task-board",
            private_key_path=key_dir / "gateway-task-board.key",
            audience="db-gateway",
            token_ttl_seconds=300,
        ),
        config_dir=key_dir,
    )
    return TaskDbClient(base_url=gateway_base_url, timeout_seconds=10, signer=signer)


def _event(
    event_type: str,
    *,
    source: str,
    task_id: str | None = None,
    agent_id: str | None = None,
) -> dict[str, object]:
    return {
        "event_source": source,
        "event_type": event_type,
        "timestamp": "2099-01-01T12:00:00Z",
        "task_id": task_id,
        "agent_id": agent_id,
        "summary": event_type,
        "payload": "{}",
    }


async def _register(
    client: httpx.AsyncClient,
    agent_id: str,
    *,
    public_key: str | None = None,
) -> None:
    response = await client.post(
        "/identity/agents",
        json={
            "agent_id": agent_id,
            "name": agent_id,
            "public_key": public_key or f"ed25519:{uuid4()}",
            "registered_at": "2099-01-01T11:59:00Z",
            "event": _event("agent.registered", source="identity", agent_id=agent_id),
        },
    )
    assert response.status_code == 201, response.text


async def register_platform_identity(
    client: httpx.AsyncClient,
    public_key_path: Path,
) -> str:
    """Register the receiver-configured platform key for real ruling history."""
    agent_id = f"a-platform-{uuid4()}"
    public_key = f"ed25519:{public_key_to_b64(load_public_key(public_key_path))}"
    await _register(client, agent_id, public_key=public_key)
    return agent_id


async def _account(client: httpx.AsyncClient, agent_id: str, balance: int) -> None:
    body: dict[str, object] = {
        "account_id": agent_id,
        "balance": balance,
        "created_at": "2099-01-01T11:59:01Z",
        "event": _event("account.created", source="central-bank", agent_id=agent_id),
    }
    if balance:
        body.update(
            operation_id=f"op-{uuid4()}",
            initial_credit={
                "tx_id": f"tx-{uuid4()}",
                "amount": balance,
                "reference": "initial_balance",
                "timestamp": "2099-01-01T11:59:01Z",
            },
        )
    response = await client.post("/bank/accounts", json=body)
    assert response.status_code == 201, response.text


async def _publish(
    client: httpx.AsyncClient,
    *,
    task_id: str,
    poster_id: str,
    escrow_id: str,
    bidding_deadline_seconds: int,
    review_deadline_seconds: int,
) -> None:
    publication_id = operation_id_for("gateway-native-http-fixture", (task_id, "publication"))
    lock_id = operation_id_for("task-lock", (task_id, poster_id))
    admitted = await client.post(
        "/board/task-publications",
        json={
            "protocol_version": 1,
            "operation_id": publication_id,
            "task_id": task_id,
            "actor_kind": "agent",
            "actor_id": poster_id,
            "lock_operation_id": lock_id,
            "title": "MP-08 recovery history",
            "spec": "Recover this exact immutable financial effect.",
            "reward": 100,
            "bidding_deadline_seconds": bidding_deadline_seconds,
            "acceptance_window_seconds": 3_600,
            "execution_deadline_seconds": 172_800,
            "review_deadline_seconds": review_deadline_seconds,
        },
    )
    assert admitted.status_code == 202, admitted.text
    lock_timestamp = epoch_us_to_utc_text(utc_now_us())
    locked = await client.post(
        "/bank/escrow/lock",
        json={
            "operation_id": lock_id,
            "escrow_id": escrow_id,
            "payer_account_id": poster_id,
            "amount": 100,
            "task_id": task_id,
            "created_at": lock_timestamp,
            "tx_id": f"tx-{uuid4()}",
            "event": {
                **_event(
                    "escrow.locked",
                    source="central-bank",
                    task_id=task_id,
                    agent_id=poster_id,
                ),
                "timestamp": lock_timestamp,
            },
        },
    )
    assert locked.status_code == 201, locked.text
    published = await client.post(f"/board/task-publications/{publication_id}/reconcile", json={})
    assert published.status_code == 201, published.text


def _operation(
    *,
    operation_id: str,
    task_id: str,
    command: str,
    status: str,
    version: int,
    actor_kind: str,
    actor_id: str,
    arguments: dict[str, object],
) -> dict[str, object]:
    return {
        "protocol_version": 1,
        "operation_id": operation_id,
        "task_id": task_id,
        "command": command,
        "expected_status": status,
        "expected_lifecycle_version": version,
        "actor_kind": actor_kind,
        "actor_id": actor_id,
        "arguments": arguments,
    }


async def _submitted(
    client: httpx.AsyncClient,
    *,
    task_id: str,
    poster_id: str,
    worker_id: str,
    escrow_id: str,
) -> None:
    bid_id = f"bid-{uuid4()}"
    bid = await client.post(
        "/board/task-operations",
        json=_operation(
            operation_id=f"op-{uuid4()}",
            task_id=task_id,
            command="submit_bid",
            status="open",
            version=0,
            actor_kind="agent",
            actor_id=worker_id,
            arguments={"bid_id": bid_id, "bidder_id": worker_id, "amount": 90},
        ),
    )
    assert bid.status_code == 200, bid.text
    accepted = await client.post(
        "/board/task-operations",
        json=_operation(
            operation_id=f"op-{uuid4()}",
            task_id=task_id,
            command="accept_bid",
            status="open",
            version=0,
            actor_kind="agent",
            actor_id=poster_id,
            arguments={
                "bid_id": bid_id,
                "worker_id": worker_id,
                "escrow_id": escrow_id,
                "reward": 100,
                "bid_amount": 90,
            },
        ),
    )
    assert accepted.status_code == 200, accepted.text
    asset = await client.post(
        "/board/assets",
        json={
            "admission": {
                "expected_status": "accepted",
                "expected_lifecycle_version": 1,
                "max_files_per_task": 10,
            },
            "asset_id": f"asset-{uuid4()}",
            "task_id": task_id,
            "uploader_id": worker_id,
            "filename": "result.txt",
            "content_type": "text/plain",
            "size_bytes": 4,
            "storage_path": f"assets/{task_id}/result.txt",
            "content_hash": "a" * 64,
            "uploaded_at": "2099-01-01T12:00:01Z",
            "event": _event(
                "asset.uploaded", source="task-board", task_id=task_id, agent_id=worker_id
            ),
        },
    )
    assert asset.status_code == 201, asset.text
    submitted = await client.post(
        "/board/task-operations",
        json=_operation(
            operation_id=f"op-{uuid4()}",
            task_id=task_id,
            command="submit_deliverable",
            status="accepted",
            version=1,
            actor_kind="agent",
            actor_id=worker_id,
            arguments={"worker_id": worker_id},
        ),
    )
    assert submitted.status_code == 200, submitted.text


async def prepare_recovery_history(
    client: httpx.AsyncClient,
    command: Command,
    *,
    platform_actor_id: str | None = None,
) -> RecoveryHistory:
    """Create a real source history and leave one native financial effect claimed."""
    task_id = f"t-{uuid4()}"
    poster_id = f"a-poster-{uuid4()}"
    worker_id = f"a-worker-{uuid4()}"
    escrow_id = f"esc-{uuid4()}"
    await _register(client, poster_id)
    await _register(client, worker_id)
    await _account(client, poster_id, 100)
    await _account(client, worker_id, 0)
    await _publish(
        client,
        task_id=task_id,
        poster_id=poster_id,
        escrow_id=escrow_id,
        bidding_deadline_seconds=1 if command == "expire_bidding" else 43_200,
        review_deadline_seconds=1 if command == "auto_approve" else 43_200,
    )

    if command in {"approve_task", "auto_approve", "record_ruling"}:
        await _submitted(
            client,
            task_id=task_id,
            poster_id=poster_id,
            worker_id=worker_id,
            escrow_id=escrow_id,
        )
    if command == "record_ruling":
        claim_id = f"clm-{uuid4()}"
        dispute_id = f"op-{uuid4()}"
        disputed = await client.post(
            "/board/task-operations",
            json=_operation(
                operation_id=dispute_id,
                task_id=task_id,
                command="dispute_task",
                status="submitted",
                version=2,
                actor_kind="agent",
                actor_id=poster_id,
                arguments={
                    "poster_id": poster_id,
                    "worker_id": worker_id,
                    "escrow_id": escrow_id,
                    "reward": 100,
                    "reason": "Exact MP-08 ruling history",
                },
            ),
        )
        assert disputed.status_code == 202, disputed.text
        claim = await client.post(
            "/court/claims",
            json={
                "claim_id": claim_id,
                "task_id": task_id,
                "claimant_id": poster_id,
                "respondent_id": worker_id,
                "reason": "Exact MP-08 ruling history",
                "status": "rebuttal_pending",
                "rebuttal_deadline": "2099-01-01T13:00:00Z",
                "filed_at": "2099-01-01T12:00:02Z",
                "event": _event("claim.filed", source="court", task_id=task_id, agent_id=poster_id),
            },
        )
        assert claim.status_code == 201, claim.text
        finalized = await client.post(f"/board/task-operations/{dispute_id}/finalize", json={})
        assert finalized.status_code == 200, finalized.text
        source_reference = f"task:{task_id}:ruling:{claim_id}"
        arguments: dict[str, object] = {
            "poster_id": poster_id,
            "worker_id": worker_id,
            "escrow_id": escrow_id,
            "reward": 100,
            "ruling_id": claim_id,
            "worker_pct": 60,
            "summary": "Split by ruling",
            "source_reference": source_reference,
        }
        assert platform_actor_id is not None
        status, version, actor_kind, actor_id = (
            "disputed",
            3,
            "platform",
            platform_actor_id,
        )
        effect_kind: Literal["bank_release", "bank_split"] = "bank_split"
        worker_pct: int | None = 60
    else:
        task_response = await client.get(f"/board/tasks/{task_id}")
        assert task_response.status_code == 200, task_response.text
        task = task_response.json()
        target = (
            "approved"
            if command in {"approve_task", "auto_approve"}
            else ("cancelled" if command == "cancel_task" else "expired")
        )
        source_reference = f"task:{task_id}:{target}"
        arguments: dict[str, object] = {
            "poster_id": poster_id,
            "escrow_id": escrow_id,
            "reward": 100,
            "source_reference": source_reference,
        }
        if command in {"approve_task", "auto_approve"}:
            arguments["worker_id"] = worker_id
        if command == "auto_approve":
            arguments["cutoff_us"] = task["review_cutoff_us"]
            delay = max(0.0, (int(task["review_cutoff_us"]) - utc_now_us()) / 1_000_000)
            await asyncio.sleep(delay + 0.02)
        elif command in {"cancel_task", "expire_bidding"}:
            arguments["cutoff_us"] = task["bidding_cutoff_us"]
            if command == "expire_bidding":
                delay = max(0.0, (int(task["bidding_cutoff_us"]) - utc_now_us()) / 1_000_000)
                await asyncio.sleep(delay + 0.02)
        status = "submitted" if command in {"approve_task", "auto_approve"} else "open"
        version = 2 if status == "submitted" else 0
        actor_kind = "agent" if command in {"approve_task", "cancel_task"} else "system"
        actor_id = poster_id if actor_kind == "agent" else "system-task-board-deadline"
        effect_kind = "bank_release"
        worker_pct = None
    operation_id = f"op-{uuid4()}"
    claimed = await client.post(
        "/board/task-operations",
        json=_operation(
            operation_id=operation_id,
            task_id=task_id,
            command=command,
            status=status,
            version=version,
            actor_kind=actor_kind,
            actor_id=actor_id,
            arguments=arguments,
        ),
    )
    assert claimed.status_code == 202, claimed.text
    return RecoveryHistory(
        task_id=task_id,
        poster_id=poster_id,
        worker_id=worker_id
        if command in {"approve_task", "auto_approve", "record_ruling"}
        else None,
        escrow_id=escrow_id,
        operation_id=operation_id,
        command=command,
        effect_kind=effect_kind,
        source_reference=source_reference,
        worker_pct=worker_pct,
    )


async def persist_effect(client: httpx.AsyncClient, history: RecoveryHistory) -> None:
    """Persist the exact Bank receipt requested by one recovery lease."""
    if history.effect_kind == "bank_release":
        recipient = history.worker_id or history.poster_id
        response = await client.post(
            "/bank/escrow/release",
            json={
                "operation_id": history.operation_id,
                "escrow_id": history.escrow_id,
                "recipient_account_id": recipient,
                "source_reference": history.source_reference,
                "tx_id": f"tx-{uuid4()}",
                "resolved_at": "2099-01-01T12:00:03Z",
                "event": _event(
                    "escrow.released",
                    source="central-bank",
                    task_id=history.task_id,
                    agent_id=recipient,
                ),
            },
        )
    else:
        assert history.worker_id is not None and history.worker_pct is not None
        response = await client.post(
            "/bank/escrow/split",
            json={
                "operation_id": history.operation_id,
                "escrow_id": history.escrow_id,
                "worker_account_id": history.worker_id,
                "poster_account_id": history.poster_id,
                "worker_pct": history.worker_pct,
                "source_reference": history.source_reference,
                "worker_tx_id": f"tx-{uuid4()}",
                "poster_tx_id": f"tx-{uuid4()}",
                "resolved_at": "2099-01-01T12:00:03Z",
                "event": _event("escrow.split", source="central-bank", task_id=history.task_id),
            },
        )
    assert response.status_code == 200, response.text
