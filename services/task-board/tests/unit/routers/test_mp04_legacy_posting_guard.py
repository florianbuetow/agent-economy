"""Historical escrow observations cannot fund newly posted tasks."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

import pytest
from service_auth.financial_identity import operation_id_for
from service_auth.financial_responses import parse_financial_envelope

from task_board_service.core.state import get_app_state
from tests.unit.routers.conftest import create_task, make_task_id

if TYPE_CHECKING:
    from httpx import AsyncClient

pytestmark = pytest.mark.unit


def _hash(value: object) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _legacy_lock_observation(
    *,
    operation_id: str,
    task_id: str,
    payer_id: str,
    amount: int,
    current_status: str,
) -> dict[str, object]:
    request = {
        "request_version": 1,
        "operation_kind": "escrow_lock",
        "payer_account_id": payer_id,
        "task_id": task_id,
        "amount": amount,
    }
    transaction = {
        "tx_id": "tx-historical-task-lock",
        "account_id": payer_id,
        "type": "escrow_lock",
        "amount": amount,
        "balance_after": 900,
        "reference": task_id,
        "timestamp": "",
        "event_id": None,
    }
    escrow_lock = {
        "escrow_id": "esc-historical-task-lock",
        "payer_account_id": payer_id,
        "amount": amount,
        "task_id": task_id,
        "created_at": "",
        "event_id": None,
    }
    resolved_at = None if current_status == "locked" else "2026-10-17T12:00:00Z"
    envelope: dict[str, object] = {
        "outcome": "legacy_observation",
        "attempt": {
            "operation_id": operation_id,
            "operation_kind": "escrow_lock",
            "request_sha256": _hash(request),
        },
        "observation": {
            "operation_id": None,
            "observed_kind": "observed_escrow_lock",
            "transaction": transaction,
            "observed_fact_sha256": _hash(transaction),
            "escrow_lock": escrow_lock,
            "observed_status": "locked",
            "lock_fact_sha256": _hash(escrow_lock),
            "resolution_fact": None,
            "resolution_fact_sha256": None,
        },
        "current_escrow": {
            "escrow_id": "esc-historical-task-lock",
            "payer_account_id": payer_id,
            "task_id": task_id,
            "amount": amount,
            "status": current_status,
            "created_at": "",
            "resolved_at": resolved_at,
        },
    }
    validated = parse_financial_envelope(
        envelope,
        expected_operation_id=operation_id,
        expected_operation_kind="escrow_lock",
        expected_business_fields={
            "payer_account_id": payer_id,
            "task_id": task_id,
            "amount": amount,
        },
    )
    assert validated == envelope
    return envelope


@pytest.mark.parametrize("current_status", ["locked", "released"])
async def test_posting_rejects_historical_lock_without_task_or_settlement_side_effects(
    client: AsyncClient,
    alice_keypair: Any,
    alice_agent_id: str,
    current_status: str,
) -> None:
    task_id = make_task_id()
    reward = 100
    operation_id = operation_id_for("task-lock", (task_id, alice_agent_id))
    legacy = _legacy_lock_observation(
        operation_id=operation_id,
        task_id=task_id,
        payer_id=alice_agent_id,
        amount=reward,
        current_status=current_status,
    )
    state = get_app_state()
    state.central_bank_client.escrow_lock.side_effect = None
    state.central_bank_client.escrow_lock.return_value = legacy

    response = await create_task(
        client,
        alice_keypair,
        alice_agent_id,
        task_id=task_id,
        reward=reward,
    )

    assert response.status_code == 502
    assert response.json()["error"] == "central_bank_unavailable"
    assert state.store.get_task(task_id) is None
    state.central_bank_client.escrow_lock.assert_awaited_once()
    state.central_bank_client.escrow_release.assert_not_awaited()
    state.central_bank_client.escrow_split.assert_not_awaited()
