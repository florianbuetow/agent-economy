"""Public MP-04 opposing-settlement race through independent Gateway writers."""

from __future__ import annotations

import asyncio
import contextvars
import json
import sqlite3
from threading import Barrier
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

import httpx
import pytest
from db_gateway_service.core.state import AppState
from db_gateway_service.core.state import get_app_state as get_gateway_state
from db_gateway_service.routers import bank as gateway_bank_router
from db_gateway_service.services.db_reader import DbReader
from db_gateway_service.services.db_writer import DbWriter

from central_bank_service.core.state import get_app_state as get_bank_state
from tests.integration.conftest import (
    PLATFORM_AGENT_ID,
    make_jws_token,
    register_identity_agent,
)

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from fastapi import FastAPI

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_PAYER = "a-race-payer"
_WORKER = "a-race-worker"


class _BarrierWriter:
    """Pause the first settlement at the authenticated Gateway write boundary."""

    def __init__(self, writer: DbWriter, barrier: Barrier) -> None:
        self._writer = writer
        self._barrier = barrier
        self._release_waited = False
        self._split_waited = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._writer, name)

    def escrow_release(self, data: dict[str, Any]) -> dict[str, Any]:
        if not self._release_waited:
            self._release_waited = True
            self._barrier.wait(timeout=10)
        return self._writer.escrow_release(data)

    def escrow_split(self, data: dict[str, Any]) -> dict[str, Any]:
        if not self._split_waited:
            self._split_waited = True
            self._barrier.wait(timeout=10)
        return self._writer.escrow_split(data)


class _IndependentWriterTransport(httpx.AsyncBaseTransport):
    """Run each signed Gateway settlement on its assigned writer thread."""

    def __init__(
        self,
        app: FastAPI,
        selected_state: contextvars.ContextVar[AppState | None],
        states: dict[str, AppState],
    ) -> None:
        self._app = app
        self._selected_state = selected_state
        self._states = states

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        content = await request.aread()
        payload = json.loads(content)
        operation_id = str(payload["operation_id"])
        state = self._states[operation_id]
        token = self._selected_state.set(state)
        try:
            status_code, headers, response_content = await asyncio.to_thread(
                self._dispatch,
                request.method,
                str(request.url),
                list(request.headers.multi_items()),
                content,
            )
        finally:
            self._selected_state.reset(token)
        return httpx.Response(
            status_code=status_code,
            headers=headers,
            content=response_content,
            request=request,
        )

    def _dispatch(
        self,
        method: str,
        url: str,
        headers: list[tuple[str, str]],
        content: bytes,
    ) -> tuple[int, list[tuple[str, str]], bytes]:
        async def send() -> tuple[int, list[tuple[str, str]], bytes]:
            transport = httpx.ASGITransport(app=self._app)
            threaded_request = httpx.Request(
                method,
                url,
                headers=headers,
                content=content,
            )
            response = await transport.handle_async_request(threaded_request)
            response_content = await response.aread()
            result = (
                response.status_code,
                list(response.headers.multi_items()),
                response_content,
            )
            await response.aclose()
            await transport.aclose()
            return result

        return asyncio.run(send())


async def _create_account(
    client: httpx.AsyncClient,
    gateway_client: httpx.AsyncClient,
    platform_key: Ed25519PrivateKey,
    agent_id: str,
    balance: int,
) -> None:
    await register_identity_agent(gateway_client, agent_id)
    payload: dict[str, object] = {
        "action": "create_account",
        "agent_id": agent_id,
        "initial_balance": balance,
    }
    if balance > 0:
        payload["operation_id"] = f"op-{uuid4()}"
    token = make_jws_token(platform_key, PLATFORM_AGENT_ID, payload)
    response = await client.post("/accounts", json={"token": token})
    assert response.status_code == 201, response.text


async def _lock_escrow(
    client: httpx.AsyncClient,
    agent_key: Ed25519PrivateKey,
    task_id: str,
) -> str:
    token = make_jws_token(
        agent_key,
        _PAYER,
        {
            "action": "escrow_lock",
            "operation_id": f"op-{uuid4()}",
            "agent_id": _PAYER,
            "amount": 100,
            "task_id": task_id,
        },
    )
    response = await client.post("/escrow/lock", json={"token": token})
    assert response.status_code == 201, response.text
    return str(response.json()["receipt"]["escrow_id"])


def _settlement_request(
    platform_key: Ed25519PrivateKey,
    *,
    operation_id: str,
    escrow_id: str,
    split: bool,
) -> tuple[str, dict[str, str]]:
    if split:
        path = f"/escrow/{escrow_id}/split"
        payload = {
            "action": "escrow_split",
            "operation_id": operation_id,
            "escrow_id": escrow_id,
            "worker_account_id": _WORKER,
            "poster_account_id": _PAYER,
            "worker_pct": 40,
            "source_reference": "race:split",
        }
    else:
        path = f"/escrow/{escrow_id}/release"
        payload = {
            "action": "escrow_release",
            "operation_id": operation_id,
            "escrow_id": escrow_id,
            "recipient_account_id": _WORKER,
            "source_reference": "race:release",
        }
    token = make_jws_token(platform_key, PLATFORM_AGENT_ID, payload, resource=f"POST {path}")
    return path, {"token": token}


def _read_rows(
    database: str,
    query: str,
    parameters: tuple[object, ...] = (),
) -> list[sqlite3.Row]:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        return list(connection.execute(query, parameters).fetchall())
    finally:
        connection.close()


def _assert_persisted_winner(
    database: str,
    *,
    escrow_id: str,
    winner_id: str,
    release_id: str,
    winner_envelope: dict[str, Any],
    before_event_count: int,
    before_transaction_count: int,
) -> None:
    receipt = winner_envelope["receipt"]
    is_release = winner_id == release_id
    expected_kind = "escrow_release" if is_release else "escrow_split"
    expected_status = "released" if is_release else "split"
    expected_balances = {_PAYER: 0, _WORKER: 100} if is_release else {_PAYER: 60, _WORKER: 40}
    expected_transaction_delta = 1 if is_release else 2

    balances = _read_rows(
        database,
        "SELECT account_id, balance FROM bank_accounts WHERE account_id IN (?, ?)",
        (_PAYER, _WORKER),
    )
    assert {str(row["account_id"]): int(row["balance"]) for row in balances} == expected_balances
    escrow = _read_rows(
        database,
        "SELECT status, resolved_at FROM bank_escrow WHERE escrow_id = ?",
        (escrow_id,),
    )
    assert len(escrow) == 1
    assert (escrow[0]["status"], escrow[0]["resolved_at"]) == (
        expected_status,
        receipt["committed_at"],
    )
    assert winner_envelope["current_escrow"]["status"] == expected_status

    persisted_receipts = _read_rows(
        database,
        "SELECT operation_id, operation_kind, request_sha256, source_reference, committed_at, "
        "event_id, escrow_id, result_json FROM bank_operation_receipts "
        "WHERE escrow_id = ? AND operation_kind IN ('escrow_release', 'escrow_split')",
        (escrow_id,),
    )
    assert len(persisted_receipts) == 1
    assert dict(persisted_receipts[0]) == {
        "operation_id": receipt["operation_id"],
        "operation_kind": expected_kind,
        "request_sha256": receipt["request_sha256"],
        "source_reference": receipt["source_reference"],
        "committed_at": receipt["committed_at"],
        "event_id": receipt["event_id"],
        "escrow_id": receipt["escrow_id"],
        "result_json": json.dumps(receipt["result"], sort_keys=True, separators=(",", ":")),
    }
    persisted_legs = _read_rows(
        database,
        "SELECT role, account_id, direction, amount, transaction_id, balance_after "
        "FROM bank_operation_legs WHERE operation_id = ? ORDER BY leg_index",
        (winner_id,),
    )
    assert [dict(row) for row in persisted_legs] == receipt["legs"]

    transactions = _read_rows(
        database,
        "SELECT tx_id, event_id FROM bank_transactions "
        "WHERE reference = ? AND type = 'escrow_release' ORDER BY tx_id",
        (escrow_id,),
    )
    assert len(transactions) == expected_transaction_delta
    assert len({str(row["tx_id"]) for row in transactions}) == expected_transaction_delta
    assert {int(row["event_id"]) for row in transactions} == {receipt["event_id"]}
    assert len(_read_rows(database, "SELECT event_id FROM events")) == before_event_count + 1
    assert len(_read_rows(database, "SELECT tx_id FROM bank_transactions")) == (
        before_transaction_count + expected_transaction_delta
    )
    event = _read_rows(
        database,
        "SELECT event_id, event_type FROM events WHERE event_id = ?",
        (receipt["event_id"],),
    )
    assert [dict(row) for row in event] == [
        {
            "event_id": receipt["event_id"],
            "event_type": "escrow.released" if is_release else "escrow.split",
        }
    ]


async def test_opposing_public_settlements_have_one_immutable_native_winner(
    client: httpx.AsyncClient,
    gateway_client: httpx.AsyncClient,
    platform_keypair: tuple[Ed25519PrivateKey, str],
    agent_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
    _gateway: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    platform_key = platform_keypair[0]
    await _create_account(client, gateway_client, platform_key, _PAYER, 100)
    await _create_account(client, gateway_client, platform_key, _WORKER, 0)
    escrow_id = await _lock_escrow(client, agent_keypair[0], f"t-{uuid4()}")

    original_state = get_gateway_state()
    assert original_state.db_writer is not None
    barrier = Barrier(2)
    first_writer = cast("DbWriter", _BarrierWriter(original_state.db_writer, barrier))
    second_raw_writer = DbWriter(
        gateway_db_path,
        5000,
        "wal",
        None,
        current_task_board_caller_kid="gateway-task-board",
    )
    second_writer = cast("DbWriter", _BarrierWriter(second_raw_writer, barrier))
    release_id = f"op-{uuid4()}"
    split_id = f"op-{uuid4()}"
    selected_state: contextvars.ContextVar[AppState | None] = contextvars.ContextVar(
        "mp04_public_race_gateway_state",
        default=None,
    )
    first_state = AppState(
        db_writer=first_writer, db_reader=DbReader(original_state.db_writer.connection)
    )
    second_state = AppState(
        db_writer=second_writer, db_reader=DbReader(second_raw_writer.connection)
    )

    def selected_gateway_state() -> AppState:
        state = selected_state.get()
        return original_state if state is None else state

    monkeypatch.setattr(gateway_bank_router, "get_app_state", selected_gateway_state)
    ledger = cast("Any", get_bank_state().ledger)
    await ledger._gateway._client.aclose()
    ledger._gateway._client = httpx.AsyncClient(
        transport=_IndependentWriterTransport(
            _gateway,
            selected_state,
            {release_id: first_state, split_id: second_state},
        ),
        base_url="http://gateway-test",
    )

    release_request = _settlement_request(
        platform_key,
        operation_id=release_id,
        escrow_id=escrow_id,
        split=False,
    )
    split_request = _settlement_request(
        platform_key,
        operation_id=split_id,
        escrow_id=escrow_id,
        split=True,
    )
    before_events = _read_rows(gateway_db_path, "SELECT event_id FROM events")
    before_transactions = _read_rows(gateway_db_path, "SELECT tx_id FROM bank_transactions")

    try:
        release_response, split_response = await asyncio.gather(
            client.post(release_request[0], json=release_request[1]),
            client.post(split_request[0], json=split_request[1]),
        )

        responses = {
            release_id: (release_request, release_response),
            split_id: (split_request, split_response),
        }
        winners = [
            (operation_id, request, response)
            for operation_id, (request, response) in responses.items()
            if response.status_code == 200
        ]
        conflicts = [
            (operation_id, response)
            for operation_id, (_, response) in responses.items()
            if response.status_code == 409
        ]
        assert len(winners) == 1
        assert len(conflicts) == 1
        winner_id, winner_request, winner_response = winners[0]
        loser_id, conflict_response = conflicts[0]
        winner_envelope = winner_response.json()
        winner_receipt = winner_envelope["receipt"]
        assert winner_envelope["outcome"] == "native_receipt"
        assert winner_receipt["operation_id"] == winner_id
        assert conflict_response.json() == {
            "error": "financial_operation_already_committed",
            "message": "Financial effect already belongs to another operation identity",
            "details": {"winning_operation_id": winner_id},
        }
        assert loser_id != winner_id

        replay = await client.post(winner_request[0], json=winner_request[1])
        assert replay.status_code == 200, replay.text
        assert replay.json()["outcome"] == "native_receipt"
        assert replay.json()["receipt"] == winner_receipt
        assert replay.json()["current_escrow"] == winner_envelope["current_escrow"]

        _assert_persisted_winner(
            gateway_db_path,
            escrow_id=escrow_id,
            winner_id=winner_id,
            release_id=release_id,
            winner_envelope=winner_envelope,
            before_event_count=len(before_events),
            before_transaction_count=len(before_transactions),
        )
    finally:
        await ledger._gateway._client.aclose()
        second_raw_writer.close()
