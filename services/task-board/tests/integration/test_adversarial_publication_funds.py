"""Distinct concurrent publications competing for one poster balance."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock

import pytest
from service_auth.financial_identity import operation_id_for
from service_commons.exceptions import ServiceError

from task_board_service.core.state import get_app_state
from tests.integration.test_mp05_posting_recovery import (
    _assert_saved_before_bank,
    _new_posting,
    _Posting,
    _rows,
    _tokens,
)

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from httpx import AsyncClient

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_CHANGED_TERMS: tuple[tuple[str, object], ...] = (
    ("title", "Changed title"),
    ("spec", "Changed specification"),
    ("reward", 99),
    ("bidding_deadline_seconds", 3_599),
    ("acceptance_window_seconds", 1_799),
    ("execution_deadline_seconds", 7_199),
    ("review_deadline_seconds", 899),
)


def _retarget(first: _Posting, *, reward: int) -> _Posting:
    task_id = f"t-{uuid.uuid4()}"
    intent = replace(
        first.intent,
        operation_id=f"op-{uuid.uuid4()}",
        task_id=task_id,
        lock_operation_id=operation_id_for("task-lock", (task_id, first.intent.actor_id)),
        reward=reward,
        title=f"Concurrent publication {task_id}",
    )
    task_token, escrow_token = _tokens(first.private_key, intent)
    return _Posting(
        intent,
        first.private_key,
        task_token,
        escrow_token,
        f"esc-{uuid.uuid4()}",
    )


async def _gateway_lock(gateway_client: AsyncClient, posting: _Posting) -> dict[str, Any]:
    timestamp = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    response = await gateway_client.post(
        "/bank/escrow/lock",
        json={
            "operation_id": posting.intent.lock_operation_id,
            "escrow_id": posting.escrow_id,
            "payer_account_id": posting.intent.actor_id,
            "amount": posting.intent.reward,
            "task_id": posting.intent.task_id,
            "created_at": timestamp,
            "tx_id": f"tx-{uuid.uuid4()}",
            "event": {
                "event_source": "central-bank",
                "event_type": "escrow.locked",
                "timestamp": timestamp,
                "task_id": posting.intent.task_id,
                "agent_id": posting.intent.actor_id,
                "summary": f"Locked escrow for {posting.intent.task_id}",
                "payload": "{}",
            },
        },
    )
    if response.status_code != 201:
        body = cast("dict[str, Any]", response.json())
        raise ServiceError(
            str(body["error"]),
            str(body["message"]),
            response.status_code,
            cast("dict[str, Any]", body["details"]),
        )
    return cast("dict[str, Any]", response.json())


async def test_distinct_publications_cannot_create_a_phantom_task_when_funds_run_out(
    client: AsyncClient,
    gateway_client: AsyncClient,
    gateway_db_path: str,
    poster_keypair: tuple[Ed25519PrivateKey, str],
) -> None:
    """PUB-03: one of two reward-300 publications wins a balance-500 race."""
    seeded = await _new_posting(gateway_client, poster_keypair[0])
    first = _retarget(seeded, reward=300)
    second = _retarget(seeded, reward=300)
    postings = {item.escrow_token: item for item in (first, second)}
    both_at_bank = asyncio.Event()
    arrivals = 0

    async def concurrent_lock(escrow_token: str) -> dict[str, Any]:
        nonlocal arrivals
        posting = postings[escrow_token]
        await _assert_saved_before_bank(gateway_client, gateway_db_path, posting)
        arrivals += 1
        if arrivals == 2:
            both_at_bank.set()
        await asyncio.wait_for(both_at_bank.wait(), timeout=2)
        return await _gateway_lock(gateway_client, posting)

    state = get_app_state()
    assert state.central_bank_client is not None
    state.central_bank_client.escrow_lock = AsyncMock(side_effect=concurrent_lock)

    responses = await asyncio.gather(
        client.post("/tasks", json=first.request),
        client.post("/tasks", json=second.request),
    )

    assert arrivals == 2
    assert sorted(response.status_code for response in responses) == [201, 402]
    assert (
        next(response.json() for response in responses if response.status_code == 402)["error"]
        == "insufficient_funds"
    )
    tasks = _rows(
        gateway_db_path,
        "SELECT task_id,status,reward FROM board_tasks WHERE poster_id=?",
        (seeded.intent.actor_id,),
    )
    assert len(tasks) == 1
    assert tasks[0]["task_id"] in {first.intent.task_id, second.intent.task_id}
    assert tasks[0]["status"] == "open"
    assert tasks[0]["reward"] == 300
    assert _rows(
        gateway_db_path,
        "SELECT balance FROM bank_accounts WHERE account_id=?",
        (seeded.intent.actor_id,),
    ) == [{"balance": 200}]
    escrows = _rows(
        gateway_db_path,
        "SELECT task_id,amount,status FROM bank_escrow WHERE payer_account_id=?",
        (seeded.intent.actor_id,),
    )
    assert len(escrows) == 1
    assert escrows[0] == {"task_id": tasks[0]["task_id"], "amount": 300, "status": "locked"}
    loser = second if tasks[0]["task_id"] == first.intent.task_id else first
    loser_publication = _rows(
        gateway_db_path,
        "SELECT state,result_json FROM board_task_publications WHERE operation_id=?",
        (loser.intent.operation_id,),
    )
    assert loser_publication == [{"state": "awaiting_lock", "result_json": None}]


@pytest.mark.parametrize(("field", "changed_value"), _CHANGED_TERMS)
async def test_uncertain_publication_rejects_changed_semantic_terms(
    field: str,
    changed_value: object,
    client: AsyncClient,
    gateway_client: AsyncClient,
    gateway_db_path: str,
    poster_keypair: tuple[Ed25519PrivateKey, str],
) -> None:
    """PUB-02: one admitted operation cannot authorize changed task terms."""
    posting = await _new_posting(gateway_client, poster_keypair[0])
    state = get_app_state()
    assert state.central_bank_client is not None

    async def ambiguous_without_receipt(escrow_token: str) -> dict[str, Any]:
        assert escrow_token == posting.escrow_token
        await _assert_saved_before_bank(gateway_client, gateway_db_path, posting)
        raise ServiceError("central_bank_unavailable", "Injected unknown outcome", 502, {})

    state.central_bank_client.escrow_lock = AsyncMock(side_effect=ambiguous_without_receipt)
    pending = await client.post("/tasks", json=posting.request)
    assert pending.status_code == 202
    assert pending.json()["publication_state"] == "awaiting_lock"

    changed_intent = replace(posting.intent, **{field: changed_value})
    changed_task_token, _unchanged_escrow = _tokens(posting.private_key, changed_intent)
    conflict = await client.post(
        "/tasks",
        json={"task_token": changed_task_token, "escrow_token": posting.escrow_token},
    )

    expected_error = (
        (400, "token_mismatch")
        if field == "reward"
        else (
            409,
            "operation_id_conflict",
        )
    )
    assert (conflict.status_code, conflict.json()["error"]) == expected_error
    assert _rows(
        gateway_db_path,
        "SELECT state,request_sha256 FROM board_task_publications WHERE operation_id=?",
        (posting.intent.operation_id,),
    ) == [{"state": "awaiting_lock", "request_sha256": posting.intent.request_sha256}]
    assert (
        _rows(
            gateway_db_path,
            "SELECT operation_id FROM bank_operation_receipts WHERE operation_id=?",
            (posting.intent.lock_operation_id,),
        )
        == []
    )
