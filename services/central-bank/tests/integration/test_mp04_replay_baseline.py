"""MP-04 red baseline: committed Bank commands must have durable replay receipts.

These tests drive the public Central Bank HTTP API through its production
``LedgerDbClient`` into the real in-process DB Gateway and SQLite database.  A
transport wrapper discards the first successful HTTP response only after the
ASGI application has finished, making the ambiguous-outcome boundary
deterministic while retaining the original reply for comparison.

This bounded T-116 slice covers settlement response loss, same-ID semantic
conflicts, settled-lock receipt replay, zero split legs, and public release
reconciliation. Separate later slices cover issuance/credit receipt breadth,
different-ID natural-key conflicts, legacy observations, opposing concurrency,
and transaction rollback before those behaviors are implemented.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import httpx
import pytest

from tests.integration.conftest import PLATFORM_AGENT_ID, make_jws_token, register_identity_agent

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from fastapi import FastAPI
    from httpx import AsyncClient, Request, Response

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_SOURCE_REFERENCE = "t116-settlement-intent"


class _DropSuccessfulResponseTransport(httpx.AsyncBaseTransport):
    """Run one request to completion, retain its reply, then simulate response loss."""

    def __init__(self, app: FastAPI) -> None:
        self._inner = httpx.ASGITransport(app=app)
        self.retained_response: Response | None = None

    async def handle_async_request(self, request: Request) -> Response:
        response = await self._inner.handle_async_request(request)
        content = await response.aread()
        self.retained_response = httpx.Response(
            status_code=response.status_code,
            headers=response.headers,
            content=content,
            request=request,
            extensions=response.extensions,
        )
        await response.aclose()
        if 200 <= response.status_code < 300:
            raise httpx.ReadError(
                "injected response loss after committed Bank reply", request=request
            )
        return self.retained_response

    async def aclose(self) -> None:
        await self._inner.aclose()


@dataclass(frozen=True)
class _PersistedSettlement:
    escrow_status: str
    balances: tuple[int, int | None]
    transaction_ids: tuple[str, ...]
    event_ids: tuple[int, ...]


def _read_rows(
    db_path: str,
    query: str,
    params: tuple[object, ...],
) -> list[sqlite3.Row]:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        return list(connection.execute(query, params).fetchall())
    finally:
        connection.close()


def _settlement_state(
    db_path: str,
    escrow_id: str,
    *,
    poster_id: str,
    worker_id: str,
) -> _PersistedSettlement:
    escrow_rows = _read_rows(
        db_path,
        "SELECT status FROM bank_escrow WHERE escrow_id = ?",
        (escrow_id,),
    )
    assert len(escrow_rows) == 1
    balance_rows = _read_rows(
        db_path,
        "SELECT account_id, balance FROM bank_accounts WHERE account_id IN (?, ?) "
        "ORDER BY account_id",
        (poster_id, worker_id),
    )
    balances_by_account = {str(row["account_id"]): int(row["balance"]) for row in balance_rows}
    transaction_rows = _read_rows(
        db_path,
        "SELECT tx_id, event_id FROM bank_transactions "
        "WHERE reference = ? AND type = 'escrow_release' ORDER BY tx_id",
        (escrow_id,),
    )
    event_ids = tuple(sorted({int(row["event_id"]) for row in transaction_rows}))
    return _PersistedSettlement(
        escrow_status=str(escrow_rows[0]["status"]),
        balances=(balances_by_account[poster_id], balances_by_account.get(worker_id)),
        transaction_ids=tuple(str(row["tx_id"]) for row in transaction_rows),
        event_ids=event_ids,
    )


def _financial_snapshot(db_path: str) -> dict[str, tuple[tuple[object, ...], ...]]:
    """Snapshot every financial side-effect surface, including v2 receipts when present."""
    connection = sqlite3.connect(db_path)
    try:
        existing_tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        queries = {
            "bank_accounts": "SELECT * FROM bank_accounts",
            "bank_transactions": "SELECT * FROM bank_transactions",
            "bank_escrow": "SELECT * FROM bank_escrow",
            "events": "SELECT * FROM events",
            "bank_operation_receipts": "SELECT * FROM bank_operation_receipts",
            "bank_operation_legs": "SELECT * FROM bank_operation_legs",
        }
        return {
            table: tuple(tuple(row) for row in connection.execute(query))
            for table, query in queries.items()
            if table in existing_tables
        }
    finally:
        connection.close()


async def _create_account(
    client: AsyncClient,
    gateway_client: AsyncClient,
    platform_key: Ed25519PrivateKey,
    agent_id: str,
    initial_balance: int,
    operation_id: str | None,
) -> None:
    await register_identity_agent(gateway_client, agent_id)
    payload: dict[str, Any] = {
        "action": "create_account",
        "agent_id": agent_id,
        "initial_balance": initial_balance,
    }
    if operation_id is not None:
        payload["operation_id"] = operation_id
    token = make_jws_token(
        platform_key,
        PLATFORM_AGENT_ID,
        payload,
    )
    response = await client.post("/accounts", json={"token": token})
    assert response.status_code == 201, response.text


async def _lock(
    client: AsyncClient,
    agent_key: Ed25519PrivateKey,
    *,
    operation_id: str,
    task_id: str,
    amount: int,
    payer_id: str,
) -> tuple[dict[str, Any], dict[str, str]]:
    token = make_jws_token(
        agent_key,
        payer_id,
        {
            "action": "escrow_lock",
            "operation_id": operation_id,
            "agent_id": payer_id,
            "amount": amount,
            "task_id": task_id,
        },
    )
    request_body = {"token": token}
    response = await client.post("/escrow/lock", json=request_body)
    assert response.status_code == 201, response.text
    return response.json(), request_body


def _settlement_request(
    platform_key: Ed25519PrivateKey,
    *,
    operation_id: str,
    escrow_id: str,
    worker_pct: int | None,
) -> tuple[str, dict[str, str]]:
    if worker_pct is None:
        path = f"/escrow/{escrow_id}/release"
        payload = {
            "action": "escrow_release",
            "operation_id": operation_id,
            "escrow_id": escrow_id,
            "recipient_account_id": "a-worker",
            "source_reference": _SOURCE_REFERENCE,
        }
    else:
        path = f"/escrow/{escrow_id}/split"
        payload = {
            "action": "escrow_split",
            "operation_id": operation_id,
            "escrow_id": escrow_id,
            "worker_account_id": "a-worker",
            "worker_pct": worker_pct,
            "poster_account_id": "a-poster",
            "source_reference": _SOURCE_REFERENCE,
        }
    token = make_jws_token(platform_key, PLATFORM_AGENT_ID, payload)
    return path, {"token": token}


def _observed_escrow_for_setup(result: dict[str, Any]) -> dict[str, Any]:
    """Read the escrow projection while the red baseline still returns its legacy shape.

    Assertions for the required native envelope remain strict below. This setup-only
    adapter lets the current implementation reach the post-commit replay boundary;
    once implemented, it reads the contract's ``current_escrow`` projection.
    """
    current_escrow = result.get("current_escrow")
    if isinstance(current_escrow, dict):
        return current_escrow
    return result


def _assert_current_escrow(
    envelope: dict[str, Any],
    *,
    escrow_id: str,
    task_id: str,
    expected_status: str,
) -> None:
    current_escrow = envelope["current_escrow"]
    assert current_escrow["escrow_id"] == escrow_id
    assert current_escrow["payer_account_id"] == "a-poster"
    assert current_escrow["task_id"] == task_id
    assert current_escrow["amount"] == 100
    assert current_escrow["status"] == expected_status
    assert isinstance(current_escrow["created_at"], str)
    assert isinstance(current_escrow["resolved_at"], str)


def _assert_settlement_legs(
    receipt: dict[str, Any],
    *,
    worker_pct: int | None,
    committed: _PersistedSettlement,
) -> None:
    expected_logical_leg_count = 1 if worker_pct is None else 2
    assert len(receipt["legs"]) == expected_logical_leg_count
    positive_transaction_ids = {
        str(leg["transaction_id"]) for leg in receipt["legs"] if leg["transaction_id"] is not None
    }
    assert positive_transaction_ids == set(committed.transaction_ids)
    if worker_pct is None:
        release_leg = receipt["legs"][0]
        assert release_leg["role"] == "recipient"
        assert release_leg["account_id"] == "a-worker"
        assert release_leg["direction"] == "credit"
        assert release_leg["amount"] == 100
        assert isinstance(release_leg["transaction_id"], str)
        assert release_leg["balance_after"] == 100
        return

    legs_by_role = {str(leg["role"]): leg for leg in receipt["legs"]}
    assert set(legs_by_role) == {"worker", "poster"}
    expected_amounts = {"worker": worker_pct, "poster": 100 - worker_pct}
    for role, expected_amount in expected_amounts.items():
        leg = legs_by_role[role]
        assert leg["account_id"] == f"a-{role}"
        assert leg["direction"] == "credit"
        assert leg["amount"] == expected_amount
        if expected_amount == 0:
            assert leg["transaction_id"] is None
            assert leg["balance_after"] is None
        else:
            assert isinstance(leg["transaction_id"], str)
            assert isinstance(leg["balance_after"], int)


def _assert_native_settlement_replay(
    retained_envelope: dict[str, Any],
    replay_envelope: dict[str, Any],
    *,
    operation_id: str,
    escrow_id: str,
    task_id: str,
    expected_status: str,
    worker_pct: int | None,
    committed: _PersistedSettlement,
) -> None:
    assert retained_envelope["outcome"] == "native_receipt"
    assert replay_envelope["outcome"] == "native_receipt"
    assert replay_envelope["receipt"] == retained_envelope["receipt"]
    receipt = retained_envelope["receipt"]
    expected_kind = "escrow_release" if worker_pct is None else "escrow_split"
    assert receipt["operation_id"] == operation_id
    assert receipt["operation_kind"] == expected_kind
    assert receipt["receipt_version"] == 1
    assert receipt["source_reference"] == _SOURCE_REFERENCE
    assert receipt["escrow_id"] == escrow_id
    _assert_settlement_legs(receipt, worker_pct=worker_pct, committed=committed)
    for envelope in (retained_envelope, replay_envelope):
        _assert_current_escrow(
            envelope,
            escrow_id=escrow_id,
            task_id=task_id,
            expected_status=expected_status,
        )


@pytest.mark.parametrize(
    (
        "worker_pct",
        "expected_status",
        "expected_balances",
        "expected_transaction_count",
    ),
    [
        pytest.param(None, "released", (0, 100), 1, id="release"),
        pytest.param(0, "split", (100, None), 1, id="split-0-percent"),
        pytest.param(50, "split", (50, 50), 2, id="split-partial"),
        pytest.param(100, "split", (0, 100), 1, id="split-100-percent"),
    ],
)
async def test_committed_settlement_replay_returns_original_receipt_without_new_legs(
    app: FastAPI,
    client: AsyncClient,
    gateway_client: AsyncClient,
    platform_keypair: tuple[Ed25519PrivateKey, str],
    agent_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
    worker_pct: int | None,
    expected_status: str,
    expected_balances: tuple[int, int | None],
    expected_transaction_count: int,
) -> None:
    """A retry after response loss converges on the committed result for all splits."""
    platform_key = platform_keypair[0]
    await _create_account(
        client,
        gateway_client,
        platform_key,
        "a-poster",
        100,
        f"op-{uuid4()}",
    )
    if worker_pct == 0:
        await register_identity_agent(gateway_client, "a-worker")
    else:
        await _create_account(client, gateway_client, platform_key, "a-worker", 0, None)
    task_id = f"t-{uuid4()}"
    lock_result, _ = await _lock(
        client,
        agent_keypair[0],
        operation_id=f"op-{uuid4()}",
        task_id=task_id,
        amount=100,
        payer_id="a-poster",
    )
    escrow_id = str(_observed_escrow_for_setup(lock_result)["escrow_id"])
    operation_id = f"op-{uuid4()}"
    path, request_body = _settlement_request(
        platform_key,
        operation_id=operation_id,
        escrow_id=escrow_id,
        worker_pct=worker_pct,
    )

    dropping_transport = _DropSuccessfulResponseTransport(app)
    async with httpx.AsyncClient(
        transport=dropping_transport,
        base_url="http://test",
    ) as lossy_client:
        with pytest.raises(httpx.ReadError, match="injected response loss"):
            await lossy_client.post(path, json=request_body)

    retained = dropping_transport.retained_response
    assert retained is not None
    assert retained.status_code == 200
    committed = _settlement_state(
        gateway_db_path,
        escrow_id,
        poster_id="a-poster",
        worker_id="a-worker",
    )
    assert committed.escrow_status == expected_status
    assert committed.balances == expected_balances
    assert len(committed.transaction_ids) == expected_transaction_count
    assert len(committed.event_ids) == 1
    if worker_pct == 0:
        assert (
            _read_rows(
                gateway_db_path,
                "SELECT account_id FROM bank_accounts WHERE account_id = ?",
                ("a-worker",),
            )
            == []
        )

    replay = await client.post(path, json=request_body)
    replayed = _settlement_state(
        gateway_db_path,
        escrow_id,
        poster_id="a-poster",
        worker_id="a-worker",
    )
    retained_envelope = retained.json()
    replay_envelope = replay.json()
    assert replay.status_code == 200
    _assert_native_settlement_replay(
        retained_envelope,
        replay_envelope,
        operation_id=operation_id,
        escrow_id=escrow_id,
        task_id=task_id,
        expected_status=expected_status,
        worker_pct=worker_pct,
        committed=committed,
    )
    assert replayed == committed


async def test_operation_id_reuse_with_changed_credit_reference_conflicts_without_write(
    client: AsyncClient,
    gateway_client: AsyncClient,
    platform_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
) -> None:
    """One operation identity cannot authorize a second semantic credit command."""
    platform_key = platform_keypair[0]
    await _create_account(client, gateway_client, platform_key, "a-worker", 0, None)
    operation_id = f"op-{uuid4()}"

    def request_body(reference: str, amount: int) -> dict[str, str]:
        token = make_jws_token(
            platform_key,
            PLATFORM_AGENT_ID,
            {
                "action": "credit",
                "operation_id": operation_id,
                "account_id": "a-worker",
                "amount": amount,
                "reference": reference,
            },
        )
        return {"token": token}

    first = await client.post(
        "/accounts/a-worker/credit",
        json=request_body("issuance-one", 10),
    )
    assert first.status_code == 200, first.text
    before_conflict = _financial_snapshot(gateway_db_path)
    conflict = await client.post(
        "/accounts/a-worker/credit",
        json=request_body("issuance-two", 20),
    )
    after_conflict = _financial_snapshot(gateway_db_path)
    conflict_body = conflict.json()
    details = conflict_body.get("details")
    winning_operation_id = (
        details.get("winning_operation_id") if isinstance(details, dict) else None
    )
    assert (
        conflict.status_code,
        conflict_body.get("error"),
        winning_operation_id,
        after_conflict == before_conflict,
    ) == (409, "operation_id_conflict", operation_id, True)


async def test_operation_id_reuse_with_changed_lock_target_conflicts_without_write(
    client: AsyncClient,
    gateway_client: AsyncClient,
    platform_keypair: tuple[Ed25519PrivateKey, str],
    agent_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
) -> None:
    """One operation identity cannot debit the payer for a different task."""
    await _create_account(
        client,
        gateway_client,
        platform_keypair[0],
        "a-poster",
        100,
        f"op-{uuid4()}",
    )
    operation_id = f"op-{uuid4()}"
    first, _ = await _lock(
        client,
        agent_keypair[0],
        operation_id=operation_id,
        task_id="t-first",
        amount=20,
        payer_id="a-poster",
    )
    assert _observed_escrow_for_setup(first)["status"] == "locked"
    before_conflict = _financial_snapshot(gateway_db_path)

    token = make_jws_token(
        agent_keypair[0],
        "a-poster",
        {
            "action": "escrow_lock",
            "operation_id": operation_id,
            "agent_id": "a-poster",
            "amount": 20,
            "task_id": "t-second",
        },
    )
    conflict = await client.post("/escrow/lock", json={"token": token})
    after_conflict = _financial_snapshot(gateway_db_path)
    conflict_body = conflict.json()
    details = conflict_body.get("details")
    winning_operation_id = (
        details.get("winning_operation_id") if isinstance(details, dict) else None
    )
    assert (
        conflict.status_code,
        conflict_body.get("error"),
        winning_operation_id,
        after_conflict == before_conflict,
    ) == (409, "operation_id_conflict", operation_id, True)


async def test_exact_lock_replay_after_settlement_preserves_receipt_and_refreshes_projection(
    client: AsyncClient,
    gateway_client: AsyncClient,
    platform_keypair: tuple[Ed25519PrivateKey, str],
    agent_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
) -> None:
    """Lock replay keeps its receipt while returning the current released projection."""
    platform_key = platform_keypair[0]
    await _create_account(
        client,
        gateway_client,
        platform_key,
        "a-poster",
        100,
        f"op-{uuid4()}",
    )
    await _create_account(client, gateway_client, platform_key, "a-worker", 0, None)
    operation_id = f"op-{uuid4()}"
    original, request_body = await _lock(
        client,
        agent_keypair[0],
        operation_id=operation_id,
        task_id=f"t-{uuid4()}",
        amount=100,
        payer_id="a-poster",
    )
    escrow_id = str(_observed_escrow_for_setup(original)["escrow_id"])
    release_path, release_body = _settlement_request(
        platform_key,
        operation_id=f"op-{uuid4()}",
        escrow_id=escrow_id,
        worker_pct=None,
    )
    release = await client.post(release_path, json=release_body)
    assert release.status_code == 200, release.text
    committed = _settlement_state(
        gateway_db_path,
        escrow_id,
        poster_id="a-poster",
        worker_id="a-worker",
    )

    replay = await client.post("/escrow/lock", json=request_body)
    replayed = _settlement_state(
        gateway_db_path,
        escrow_id,
        poster_id="a-poster",
        worker_id="a-worker",
    )
    replay_result = replay.json()
    assert replay.status_code == 201
    assert original.get("outcome") == "native_receipt"
    assert replay_result.get("outcome") == "native_receipt"
    assert replay_result["receipt"] == original["receipt"]
    assert original["receipt"]["result"]["escrow_status_at_commit"] == "locked"
    assert original["current_escrow"]["status"] == "locked"
    assert replay_result["current_escrow"]["status"] == "released"
    assert replayed == committed


def _assert_release_envelope(
    envelope: dict[str, Any],
    *,
    operation_id: str,
    escrow_id: str,
    task_id: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    assert envelope["outcome"] == "native_receipt"
    receipt = envelope["receipt"]
    assert receipt["receipt_version"] == 1
    assert receipt["operation_id"] == operation_id
    assert receipt["operation_kind"] == "escrow_release"
    canonical_request = {
        "escrow_id": escrow_id,
        "operation_kind": "escrow_release",
        "recipient_account_id": "a-worker",
        "request_version": 1,
        "source_reference": _SOURCE_REFERENCE,
    }
    canonical_bytes = json.dumps(
        canonical_request,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    assert receipt["request_sha256"] == hashlib.sha256(canonical_bytes).hexdigest()
    assert receipt["source_reference"] == _SOURCE_REFERENCE
    assert isinstance(receipt["committed_at"], str) and receipt["committed_at"]
    assert isinstance(receipt["event_id"], int)
    assert receipt["escrow_id"] == escrow_id
    assert isinstance(receipt["result"], dict)
    assert len(receipt["legs"]) == 1
    leg = receipt["legs"][0]
    assert leg["role"] == "recipient"
    assert leg["account_id"] == "a-worker"
    assert leg["direction"] == "credit"
    assert leg["amount"] == 100
    assert isinstance(leg["transaction_id"], str)
    assert leg["balance_after"] == 100
    _assert_current_escrow(
        envelope,
        escrow_id=escrow_id,
        task_id=task_id,
        expected_status="released",
    )
    return receipt, leg, envelope["current_escrow"]


def _assert_release_persistence(
    db_path: str,
    *,
    receipt: dict[str, Any],
    leg: dict[str, Any],
    current_escrow: dict[str, Any],
) -> None:
    transaction_rows = _read_rows(
        db_path,
        "SELECT tx_id, account_id, type, amount, balance_after, reference, event_id "
        "FROM bank_transactions WHERE tx_id = ?",
        (leg["transaction_id"],),
    )
    assert len(transaction_rows) == 1
    assert dict(transaction_rows[0]) == {
        "tx_id": leg["transaction_id"],
        "account_id": "a-worker",
        "type": "escrow_release",
        "amount": 100,
        "balance_after": 100,
        "reference": receipt["escrow_id"],
        "event_id": receipt["event_id"],
    }
    event_rows = _read_rows(
        db_path,
        "SELECT event_id, event_source, event_type FROM events WHERE event_id = ?",
        (receipt["event_id"],),
    )
    assert len(event_rows) == 1
    assert dict(event_rows[0]) == {
        "event_id": receipt["event_id"],
        "event_source": "central-bank",
        "event_type": "escrow.released",
    }
    escrow_rows = _read_rows(
        db_path,
        "SELECT escrow_id, payer_account_id, amount, task_id, status, created_at, resolved_at "
        "FROM bank_escrow WHERE escrow_id = ?",
        (receipt["escrow_id"],),
    )
    assert len(escrow_rows) == 1
    assert dict(escrow_rows[0]) == current_escrow


async def test_successful_release_exposes_reconcilable_public_receipt(
    client: AsyncClient,
    gateway_client: AsyncClient,
    platform_keypair: tuple[Ed25519PrivateKey, str],
    agent_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
) -> None:
    """The public native envelope identifies its immutable receipt and ledger leg."""
    platform_key = platform_keypair[0]
    await _create_account(
        client,
        gateway_client,
        platform_key,
        "a-poster",
        100,
        f"op-{uuid4()}",
    )
    await _create_account(client, gateway_client, platform_key, "a-worker", 0, None)
    task_id = f"t-{uuid4()}"
    lock_result, _ = await _lock(
        client,
        agent_keypair[0],
        operation_id=f"op-{uuid4()}",
        task_id=task_id,
        amount=100,
        payer_id="a-poster",
    )
    escrow_id = str(_observed_escrow_for_setup(lock_result)["escrow_id"])
    operation_id = f"op-{uuid4()}"
    path, request_body = _settlement_request(
        platform_key,
        operation_id=operation_id,
        escrow_id=escrow_id,
        worker_pct=None,
    )

    response = await client.post(path, json=request_body)
    assert response.status_code == 200, response.text
    envelope = response.json()
    receipt, leg, current_escrow = _assert_release_envelope(
        envelope,
        operation_id=operation_id,
        escrow_id=escrow_id,
        task_id=task_id,
    )
    _assert_release_persistence(
        gateway_db_path,
        receipt=receipt,
        leg=leg,
        current_escrow=current_escrow,
    )


async def test_zero_account_rejects_operation_identity_without_ledger_write(
    client: AsyncClient,
    gateway_client: AsyncClient,
    platform_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
) -> None:
    await register_identity_agent(gateway_client, "a-zero")
    before = _financial_snapshot(gateway_db_path)
    token = make_jws_token(
        platform_keypair[0],
        PLATFORM_AGENT_ID,
        {
            "action": "create_account",
            "agent_id": "a-zero",
            "initial_balance": 0,
            "operation_id": f"op-{uuid4()}",
        },
    )
    response = await client.post("/accounts", json={"token": token})
    assert (response.status_code, response.json()["error"]) == (400, "invalid_payload")
    assert _financial_snapshot(gateway_db_path) == before


@pytest.mark.parametrize("bad_operation_id", ["not-an-operation", "op-NOT-CANONICAL"])
async def test_public_credit_rejects_malformed_operation_identity(
    client: AsyncClient,
    gateway_client: AsyncClient,
    platform_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
    bad_operation_id: str,
) -> None:
    await _create_account(client, gateway_client, platform_keypair[0], "a-worker", 0, None)
    before = _financial_snapshot(gateway_db_path)
    token = make_jws_token(
        platform_keypair[0],
        PLATFORM_AGENT_ID,
        {
            "action": "credit",
            "operation_id": bad_operation_id,
            "account_id": "a-worker",
            "amount": 10,
            "reference": "bad-operation",
        },
    )
    response = await client.post("/accounts/a-worker/credit", json={"token": token})
    assert (response.status_code, response.json()["error"]) == (400, "invalid_operation_id")
    assert _financial_snapshot(gateway_db_path) == before


async def test_public_credit_preserves_invalid_utf8_financial_request(
    client: AsyncClient,
    gateway_client: AsyncClient,
    platform_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
) -> None:
    await _create_account(client, gateway_client, platform_keypair[0], "a-worker", 0, None)
    before = _financial_snapshot(gateway_db_path)
    token = make_jws_token(
        platform_keypair[0],
        PLATFORM_AGENT_ID,
        {
            "action": "credit",
            "operation_id": f"op-{uuid4()}",
            "account_id": "a-worker",
            "amount": 10,
            "reference": "bad-\ud800-reference",
        },
    )
    response = await client.post("/accounts/a-worker/credit", json={"token": token})
    assert (response.status_code, response.json()["error"]) == (
        400,
        "invalid_financial_request",
    )
    assert _financial_snapshot(gateway_db_path) == before


async def test_agent_lock_rejects_malformed_operation_identity(
    client: AsyncClient,
    gateway_client: AsyncClient,
    platform_keypair: tuple[Ed25519PrivateKey, str],
    agent_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
) -> None:
    await _create_account(
        client,
        gateway_client,
        platform_keypair[0],
        "a-poster",
        20,
        f"op-{uuid4()}",
    )
    before = _financial_snapshot(gateway_db_path)
    token = make_jws_token(
        agent_keypair[0],
        "a-poster",
        {
            "action": "escrow_lock",
            "operation_id": "malformed",
            "agent_id": "a-poster",
            "amount": 10,
            "task_id": "t-malformed-operation",
        },
    )
    response = await client.post("/escrow/lock", json={"token": token})
    assert (response.status_code, response.json()["error"]) == (400, "invalid_operation_id")
    assert _financial_snapshot(gateway_db_path) == before
