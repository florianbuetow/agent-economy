"""Regression tests for authoritative settlement-event task binding."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal
from uuid import uuid4

import pytest

from tests.conftest import make_event

if TYPE_CHECKING:
    import sqlite3

    from fastapi.testclient import TestClient
    from httpx import Response

    from db_gateway_service.services.db_writer import DbWriter

SettlementKind = Literal["release", "split"]


def _register_account(client: TestClient, balance: int) -> str:
    account_id = f"a-{uuid4()}"
    registered = client.post(
        "/identity/agents",
        json={
            "agent_id": account_id,
            "name": "Settlement task binding",
            "public_key": f"ed25519:{uuid4()}",
            "registered_at": "2026-10-22T10:00:00Z",
            "event": make_event(),
        },
    )
    assert registered.status_code == 201, registered.text

    account: dict[str, object] = {
        "account_id": account_id,
        "balance": balance,
        "created_at": "2026-10-22T10:01:00Z",
        "event": make_event(source="bank", event_type="account.created"),
    }
    if balance > 0:
        account["operation_id"] = f"op-{uuid4()}"
        account["initial_credit"] = {
            "tx_id": f"tx-{uuid4()}",
            "amount": balance,
            "reference": "initial_balance",
            "timestamp": "2026-10-22T10:01:00Z",
        }
    created = client.post("/bank/accounts", json=account)
    assert created.status_code == 201, created.text
    return account_id


def _setup_locked_escrow(
    client: TestClient,
) -> tuple[str, str, str, str]:
    payer_id = _register_account(client, 100)
    recipient_id = _register_account(client, 0)
    escrow_id = f"esc-{uuid4()}"
    task_id = f"t-{uuid4()}"
    locked = client.post(
        "/bank/escrow/lock",
        json={
            "operation_id": f"op-{uuid4()}",
            "escrow_id": escrow_id,
            "payer_account_id": payer_id,
            "amount": 60,
            "task_id": task_id,
            "created_at": "2026-10-22T10:02:00Z",
            "tx_id": f"tx-{uuid4()}",
            "event": make_event(
                source="bank",
                event_type="escrow.locked",
                task_id=task_id,
            ),
        },
    )
    assert locked.status_code == 201, locked.text
    return payer_id, recipient_id, escrow_id, task_id


def _settlement_payload(
    kind: SettlementKind,
    *,
    payer_id: str,
    recipient_id: str,
    escrow_id: str,
    operation_id: str,
    event: dict[str, object],
) -> dict[str, object]:
    common: dict[str, object] = {
        "operation_id": operation_id,
        "escrow_id": escrow_id,
        "source_reference": f"task-settlement:{escrow_id}",
        "resolved_at": "2026-10-22T10:03:00Z",
        "event": event,
    }
    if kind == "release":
        return {
            **common,
            "recipient_account_id": recipient_id,
            "tx_id": f"tx-{uuid4()}",
        }
    return {
        **common,
        "worker_account_id": recipient_id,
        "poster_account_id": payer_id,
        "worker_pct": 75,
        "worker_tx_id": f"tx-{uuid4()}",
        "poster_tx_id": f"tx-{uuid4()}",
    }


def _post_settlement(
    client: TestClient,
    kind: SettlementKind,
    payload: dict[str, object],
) -> Response:
    return client.post(f"/bank/escrow/{kind}", json=payload)


def _event_task_id(connection: sqlite3.Connection, event_id: int) -> object:
    row = connection.execute(
        "SELECT task_id FROM events WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    assert row is not None
    return row[0]


def _financial_snapshot(connection: sqlite3.Connection) -> tuple[object, ...]:
    balances = tuple(connection.execute("SELECT account_id, balance FROM bank_accounts ORDER BY 1"))
    escrows = tuple(
        connection.execute("SELECT escrow_id, status, resolved_at FROM bank_escrow ORDER BY 1")
    )
    counts = tuple(
        connection.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM events), "
            "(SELECT COUNT(*) FROM bank_transactions), "
            "(SELECT COUNT(*) FROM bank_operation_receipts), "
            "(SELECT COUNT(*) FROM bank_operation_legs)"
        ).fetchone()
        or ()
    )
    return balances, escrows, counts


@pytest.mark.unit
@pytest.mark.parametrize("kind", ["release", "split"])
@pytest.mark.parametrize("task_id_shape", ["missing", "null"])
def test_settlement_event_uses_authoritative_escrow_task_id(
    app_with_writer: TestClient,
    db_writer: DbWriter,
    kind: SettlementKind,
    task_id_shape: str,
) -> None:
    payer_id, recipient_id, escrow_id, task_id = _setup_locked_escrow(app_with_writer)
    event: dict[str, object] = make_event(
        source="bank",
        event_type=f"escrow.{kind}",
    )
    if task_id_shape == "missing":
        del event["task_id"]
    payload = _settlement_payload(
        kind,
        payer_id=payer_id,
        recipient_id=recipient_id,
        escrow_id=escrow_id,
        operation_id=f"op-{uuid4()}",
        event=event,
    )

    response = _post_settlement(app_with_writer, kind, payload)

    assert response.status_code == 200, response.text
    assert (
        _event_task_id(
            db_writer.connection,
            response.json()["receipt"]["event_id"],
        )
        == task_id
    )


@pytest.mark.unit
@pytest.mark.parametrize("kind", ["release", "split"])
def test_settlement_rejects_conflicting_event_task_id_atomically(
    app_with_writer: TestClient,
    db_writer: DbWriter,
    kind: SettlementKind,
) -> None:
    payer_id, recipient_id, escrow_id, _task_id = _setup_locked_escrow(app_with_writer)
    before = _financial_snapshot(db_writer.connection)
    payload = _settlement_payload(
        kind,
        payer_id=payer_id,
        recipient_id=recipient_id,
        escrow_id=escrow_id,
        operation_id=f"op-{uuid4()}",
        event=make_event(
            source="bank",
            event_type=f"escrow.{kind}",
            task_id=f"t-{uuid4()}",
        ),
    )

    response = _post_settlement(app_with_writer, kind, payload)

    assert response.status_code == 400, response.text
    assert response.json()["error"] == "payload_mismatch"
    assert _financial_snapshot(db_writer.connection) == before


@pytest.mark.unit
@pytest.mark.parametrize("kind", ["release", "split"])
def test_settlement_accepts_exact_event_task_id(
    app_with_writer: TestClient,
    db_writer: DbWriter,
    kind: SettlementKind,
) -> None:
    payer_id, recipient_id, escrow_id, task_id = _setup_locked_escrow(app_with_writer)
    payload = _settlement_payload(
        kind,
        payer_id=payer_id,
        recipient_id=recipient_id,
        escrow_id=escrow_id,
        operation_id=f"op-{uuid4()}",
        event=make_event(
            source="bank",
            event_type=f"escrow.{kind}",
            task_id=task_id,
        ),
    )

    response = _post_settlement(app_with_writer, kind, payload)

    assert response.status_code == 200, response.text
    assert (
        _event_task_id(
            db_writer.connection,
            response.json()["receipt"]["event_id"],
        )
        == task_id
    )


@pytest.mark.unit
@pytest.mark.parametrize("kind", ["release", "split"])
def test_settlement_replay_returns_immutable_original_receipt(
    app_with_writer: TestClient,
    db_writer: DbWriter,
    kind: SettlementKind,
) -> None:
    payer_id, recipient_id, escrow_id, task_id = _setup_locked_escrow(app_with_writer)
    operation_id = f"op-{uuid4()}"
    payload = _settlement_payload(
        kind,
        payer_id=payer_id,
        recipient_id=recipient_id,
        escrow_id=escrow_id,
        operation_id=operation_id,
        event=make_event(source="bank", event_type=f"escrow.{kind}"),
    )
    original = _post_settlement(app_with_writer, kind, payload)
    assert original.status_code == 200, original.text
    after_original = _financial_snapshot(db_writer.connection)
    replay_payload = dict(payload)
    replay_payload["event"] = make_event(
        source="bank",
        event_type=f"escrow.{kind}",
        task_id=f"t-{uuid4()}",
        summary="Replay transport metadata must not replace the committed event",
    )

    replay = _post_settlement(app_with_writer, kind, replay_payload)

    assert replay.status_code == 200, replay.text
    assert replay.json() == original.json()
    assert _financial_snapshot(db_writer.connection) == after_original
    assert (
        _event_task_id(
            db_writer.connection,
            original.json()["receipt"]["event_id"],
        )
        == task_id
    )
