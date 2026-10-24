"""MP-07 native deadline transitions use authoritative bids and receipts."""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock

import pytest
from service_auth.financial_identity import operation_id_for
from service_commons.exceptions import ServiceError

from task_board_service.core.state import get_app_state
from tests.helpers import make_jws_token as make_scoped_jws_token
from tests.integration.conftest import make_jws_token, register_identity_agent
from tests.integration.mp05_posting_setup import (
    native_escrow_payload,
    native_task_payload,
    persist_lock_after_admission,
)

if TYPE_CHECKING:
    import httpx
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_START = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
_BIDDING_SECONDS = 10
_WINDOW_SECONDS = 20
_EXECUTION_SECONDS = 60
_REVIEW_SECONDS = 60
_REWARD = 100
_BID_AMOUNT = 90


class _MutableClock:
    def __init__(self, value: datetime) -> None:
        self._value = value

    def now(self, timezone: tzinfo | None) -> datetime:
        if timezone is None:
            return self._value.replace(tzinfo=None)
        return self._value.astimezone(timezone)

    def epoch_us(self) -> int:
        delta = self._value - datetime(1970, 1, 1, tzinfo=UTC)
        return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds

    def set(self, value: datetime) -> None:
        if value < self._value:
            raise AssertionError("Deadline clock cannot move backwards")
        self._value = value


class _ClockDateTime:
    clock: _MutableClock

    @classmethod
    def now(cls, timezone: tzinfo | None = None) -> datetime:
        return cls.clock.now(timezone)

    @classmethod
    def fromisoformat(cls, value: str) -> datetime:
        return datetime.fromisoformat(value)

    @classmethod
    def fromtimestamp(cls, timestamp: float, timezone: tzinfo | None = None) -> datetime:
        return datetime.fromtimestamp(timestamp, timezone)


@dataclass(frozen=True)
class _Task:
    task_id: str
    poster_id: str
    escrow_id: str


@dataclass(frozen=True)
class _Bid:
    task: _Task
    worker_id: str
    bid_id: str


@dataclass
class _BankProbe:
    gateway_client: httpx.AsyncClient
    db_path: str
    source_status: str
    expected_operation_id: str
    fail: bool
    calls: list[tuple[object, ...]] = field(default_factory=list)
    claim_seen: list[bool] = field(default_factory=list)

    async def release(
        self,
        escrow_id: str,
        recipient_account_id: str,
        operation_id: str,
        source_reference: str,
    ) -> dict[str, Any]:
        self.calls.append((escrow_id, recipient_account_id, operation_id, source_reference))
        claim = _rows(
            self.db_path,
            "SELECT o.operation_state,o.effect_state,t.status FROM board_task_operations o "
            "JOIN board_tasks t ON t.task_id=o.task_id WHERE o.operation_id=?",
            (self.expected_operation_id,),
        )
        self.claim_seen.append(
            claim
            == [
                {
                    "operation_state": "claimed",
                    "effect_state": "pending",
                    "status": self.source_status,
                }
            ]
        )
        if self.fail:
            raise ServiceError("central_bank_unavailable", "injected Bank failure", 502, {})
        escrow = _rows(
            self.db_path, "SELECT task_id FROM bank_escrow WHERE escrow_id=?", (escrow_id,)
        )
        assert len(escrow) == 1
        response = await self.gateway_client.post(
            "/bank/escrow/release",
            json={
                "operation_id": operation_id,
                "escrow_id": escrow_id,
                "recipient_account_id": recipient_account_id,
                "source_reference": source_reference,
                "resolved_at": _iso(_ClockDateTime.clock.now(UTC)),
                "tx_id": f"tx-{uuid.uuid4()}",
                "event": _event("central-bank", "escrow.released", str(escrow[0]["task_id"])),
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert isinstance(body, dict)
        return body


def _install_clock(monkeypatch: pytest.MonkeyPatch) -> _MutableClock:
    clock = _MutableClock(_START)
    _ClockDateTime.clock = clock
    for module in (
        "task_board_service.services.asset_manager",
        "task_board_service.services.deadline_evaluator",
        "task_board_service.services.task_db_client",
        "task_board_service.services.task_helpers",
    ):
        monkeypatch.setattr(f"{module}.datetime", _ClockDateTime)
    monkeypatch.setattr("db_gateway_service.task_time.utc_now_us", clock.epoch_us)
    return clock


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _event(source: str, event_type: str, task_id: str | None) -> dict[str, object]:
    return {
        "event_source": source,
        "event_type": event_type,
        "timestamp": _iso(_ClockDateTime.clock.now(UTC)),
        "task_id": task_id,
        "summary": event_type,
        "payload": "{}",
    }


def _rows(
    db_path: str,
    query: str,
    params: tuple[object, ...] = (),
) -> list[dict[str, object]]:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(query, params).fetchall()]
    finally:
        connection.close()


async def _seed_funded_account(
    gateway_client: httpx.AsyncClient,
    *,
    poster_id: str,
) -> None:
    await register_identity_agent(gateway_client, poster_id)
    account = await gateway_client.post(
        "/bank/accounts",
        json={
            "operation_id": f"op-{uuid.uuid4()}",
            "account_id": poster_id,
            "balance": _REWARD,
            "created_at": _iso(_START - timedelta(seconds=2)),
            "initial_credit": {
                "tx_id": f"tx-{uuid.uuid4()}",
                "amount": _REWARD,
                "reference": "initial_balance",
                "timestamp": _iso(_START - timedelta(seconds=2)),
            },
            "event": _event("central-bank", "account.created", None),
        },
    )
    assert account.status_code == 201, account.text


async def _create_task(
    client: httpx.AsyncClient,
    gateway_client: httpx.AsyncClient,
    poster_key: Ed25519PrivateKey,
) -> _Task:
    task = _Task(
        task_id=f"t-{uuid.uuid4()}",
        poster_id=f"a-deadline-poster-{uuid.uuid4()}",
        escrow_id=f"esc-{uuid.uuid4()}",
    )
    await _seed_funded_account(
        gateway_client,
        poster_id=task.poster_id,
    )
    state = get_app_state()
    assert state.central_bank_client is not None
    bank = cast("AsyncMock", state.central_bank_client)
    task_payload = native_task_payload(
        task_id=task.task_id,
        poster_id=task.poster_id,
        title="Native deadline task",
        spec="Use authoritative bid existence and native deadline claims.",
        reward=_REWARD,
        bidding_deadline_seconds=_BIDDING_SECONDS,
        acceptance_window_seconds=_WINDOW_SECONDS,
        execution_deadline_seconds=_EXECUTION_SECONDS,
        review_deadline_seconds=_REVIEW_SECONDS,
    )

    async def _persist_lock(_escrow_token: str) -> dict[str, Any]:
        return await persist_lock_after_admission(
            gateway_client,
            task_payload=task_payload,
            escrow_id=task.escrow_id,
            clock_us=_ClockDateTime.clock.epoch_us,
        )

    bank.escrow_lock = AsyncMock(side_effect=_persist_lock)
    response = await client.post(
        "/tasks",
        json={
            "task_token": make_jws_token(
                poster_key,
                task.poster_id,
                task_payload,
            ),
            "escrow_token": make_scoped_jws_token(
                poster_key,
                task.poster_id,
                native_escrow_payload(task_payload),
                audience="central-bank",
                resource="POST /escrow/lock",
            ),
        },
    )
    assert response.status_code == 201, response.text
    return task


async def _submit_bid(
    client: httpx.AsyncClient,
    gateway_client: httpx.AsyncClient,
    worker_key: Ed25519PrivateKey,
    task: _Task,
) -> _Bid:
    worker_id = f"a-deadline-worker-{uuid.uuid4()}"
    await register_identity_agent(gateway_client, worker_id)
    account = await gateway_client.post(
        "/bank/accounts",
        json={
            "account_id": worker_id,
            "balance": 0,
            "created_at": _iso(_START - timedelta(seconds=1)),
            "event": _event("central-bank", "account.created", None),
        },
    )
    assert account.status_code == 201, account.text
    response = await client.post(
        f"/tasks/{task.task_id}/bids",
        json={
            "token": make_jws_token(
                worker_key,
                worker_id,
                {
                    "action": "submit_bid",
                    "operation_id": f"op-{uuid.uuid4()}",
                    "expected_lifecycle_version": 0,
                    "task_id": task.task_id,
                    "bidder_id": worker_id,
                    "amount": _BID_AMOUNT,
                },
            )
        },
    )
    assert response.status_code == 201, response.text
    return _Bid(task, worker_id, str(response.json()["bid_id"]))


async def _accept_bid(
    client: httpx.AsyncClient,
    poster_key: Ed25519PrivateKey,
    bid: _Bid,
) -> None:
    response = await client.post(
        f"/tasks/{bid.task.task_id}/bids/{bid.bid_id}/accept",
        json={
            "token": make_jws_token(
                poster_key,
                bid.task.poster_id,
                {
                    "action": "accept_bid",
                    "operation_id": f"op-{uuid.uuid4()}",
                    "expected_lifecycle_version": 0,
                    "task_id": bid.task.task_id,
                    "bid_id": bid.bid_id,
                    "poster_id": bid.task.poster_id,
                },
            )
        },
    )
    assert response.status_code == 200, response.text


def _deadline_id(task_id: str, command: str, version: int, cutoff_us: int) -> str:
    return operation_id_for("task-deadline", (task_id, command, str(version), str(cutoff_us)))


def _install_bank_probe(
    gateway_client: httpx.AsyncClient,
    db_path: str,
    *,
    source_status: str,
    operation_id: str,
    fail: bool,
) -> _BankProbe:
    probe = _BankProbe(gateway_client, db_path, source_status, operation_id, fail)
    state = get_app_state()
    assert state.central_bank_client is not None
    cast("AsyncMock", state.central_bank_client).escrow_release = AsyncMock(
        side_effect=probe.release
    )
    return probe


def _assert_native_completion(
    db_path: str,
    *,
    task_id: str,
    operation_id: str,
    command: str,
    event_type: str,
    recipient_id: str,
) -> None:
    assert _rows(
        db_path,
        "SELECT operation_state,effect_state,command FROM board_task_operations "
        "WHERE operation_id=?",
        (operation_id,),
    ) == [{"operation_state": "completed", "effect_state": "succeeded", "command": command}]
    assert _rows(
        db_path,
        "SELECT l.account_id,l.amount FROM bank_operation_legs l "
        "WHERE l.operation_id=? AND l.role='recipient'",
        (operation_id,),
    ) == [{"account_id": recipient_id, "amount": _REWARD}]
    assert _rows(
        db_path,
        "SELECT event_type FROM events WHERE task_id=? AND event_type=?",
        (task_id, event_type),
    ) == [{"event_type": event_type}]


async def test_bid_at_bidding_cutoff_stays_open_until_acceptance_cutoff(
    client: httpx.AsyncClient,
    gateway_client: httpx.AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    worker_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _install_clock(monkeypatch)
    task = await _create_task(client, gateway_client, poster_keypair[0])
    await _submit_bid(client, gateway_client, worker_keypair[0], task)
    connection = sqlite3.connect(gateway_db_path)
    connection.execute("UPDATE board_tasks SET bid_count=0 WHERE task_id=?", (task.task_id,))
    connection.commit()
    connection.close()
    clock.set(_START + timedelta(seconds=_BIDDING_SECONDS))

    fetched = await client.get(f"/tasks/{task.task_id}")
    listed = await client.get("/tasks?status=open")

    assert fetched.status_code == listed.status_code == 200
    assert fetched.json()["status"] == "open"
    assert task.task_id in {row["task_id"] for row in listed.json()["tasks"]}
    assert (
        _rows(
            gateway_db_path,
            "SELECT command FROM board_task_operations WHERE task_id=? "
            "AND command IN ('expire_bidding','expire_acceptance')",
            (task.task_id,),
        )
        == []
    )


@pytest.mark.parametrize(
    ("with_bid", "command", "seconds"),
    [
        (False, "expire_bidding", _BIDDING_SECONDS),
        (True, "expire_acceptance", _BIDDING_SECONDS + _WINDOW_SECONDS),
    ],
)
async def test_open_deadline_uses_authoritative_bid_existence_and_native_refund(
    client: httpx.AsyncClient,
    gateway_client: httpx.AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    worker_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
    with_bid: bool,
    command: str,
    seconds: int,
) -> None:
    clock = _install_clock(monkeypatch)
    task = await _create_task(client, gateway_client, poster_keypair[0])
    if with_bid:
        await _submit_bid(client, gateway_client, worker_keypair[0], task)
        cached_count = 0
    else:
        cached_count = 7
    connection = sqlite3.connect(gateway_db_path)
    connection.execute(
        "UPDATE board_tasks SET bid_count=? WHERE task_id=?", (cached_count, task.task_id)
    )
    connection.commit()
    connection.close()
    cutoff_us = int(clock.epoch_us() + seconds * 1_000_000)
    operation_id = _deadline_id(task.task_id, command, 0, cutoff_us)
    probe = _install_bank_probe(
        gateway_client,
        gateway_db_path,
        source_status="open",
        operation_id=operation_id,
        fail=False,
    )
    clock.set(_START + timedelta(seconds=seconds))

    first = await client.get(f"/tasks/{task.task_id}")
    second = await client.get(f"/tasks/{task.task_id}")
    listed = await client.get("/tasks?status=expired")

    assert first.status_code == second.status_code == listed.status_code == 200
    assert first.json()["status"] == second.json()["status"] == "expired"
    assert task.task_id in {row["task_id"] for row in listed.json()["tasks"]}
    assert probe.claim_seen == [True]
    assert len(probe.calls) == 1
    _assert_native_completion(
        gateway_db_path,
        task_id=task.task_id,
        operation_id=operation_id,
        command=command,
        event_type="task.expired",
        recipient_id=task.poster_id,
    )


async def test_execution_expiry_refunds_fixed_reward_through_native_claim(
    client: httpx.AsyncClient,
    gateway_client: httpx.AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    worker_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _install_clock(monkeypatch)
    task = await _create_task(client, gateway_client, poster_keypair[0])
    bid = await _submit_bid(client, gateway_client, worker_keypair[0], task)
    await _accept_bid(client, poster_keypair[0], bid)
    cutoff_us = clock.epoch_us() + _EXECUTION_SECONDS * 1_000_000
    operation_id = _deadline_id(task.task_id, "expire_execution", 1, cutoff_us)
    probe = _install_bank_probe(
        gateway_client,
        gateway_db_path,
        source_status="accepted",
        operation_id=operation_id,
        fail=False,
    )
    clock.set(_START + timedelta(seconds=_EXECUTION_SECONDS))

    response = await client.get(f"/tasks/{task.task_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "expired"
    assert probe.claim_seen == [True]
    _assert_native_completion(
        gateway_db_path,
        task_id=task.task_id,
        operation_id=operation_id,
        command="expire_execution",
        event_type="task.expired",
        recipient_id=task.poster_id,
    )


async def _submit_natively(
    gateway_client: httpx.AsyncClient,
    bid: _Bid,
) -> None:
    asset = await gateway_client.post(
        "/board/assets",
        json={
            "admission": {
                "expected_status": "accepted",
                "expected_lifecycle_version": 1,
                "max_files_per_task": 10,
            },
            "asset_id": f"asset-{uuid.uuid4()}",
            "task_id": bid.task.task_id,
            "uploader_id": bid.worker_id,
            "filename": "deadline.txt",
            "content_type": "text/plain",
            "size_bytes": 1,
            "storage_path": f"{bid.task.task_id}/deadline.txt",
            "uploaded_at": _iso(_START),
            "event": _event("board", "asset.uploaded", bid.task.task_id),
        },
    )
    assert asset.status_code == 201, asset.text
    state = get_app_state()
    assert state.store is not None
    envelope = state.store.claim_task_operation(
        {
            "protocol_version": 1,
            "operation_id": f"op-{uuid.uuid4()}",
            "task_id": bid.task.task_id,
            "command": "submit_deliverable",
            "expected_status": "accepted",
            "expected_lifecycle_version": 1,
            "actor_kind": "agent",
            "actor_id": bid.worker_id,
            "arguments": {"worker_id": bid.worker_id},
        }
    )
    assert envelope["operation_state"] == "completed"


async def test_review_cutoff_autoapproves_fixed_reward_through_native_claim(
    client: httpx.AsyncClient,
    gateway_client: httpx.AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    worker_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _install_clock(monkeypatch)
    task = await _create_task(client, gateway_client, poster_keypair[0])
    bid = await _submit_bid(client, gateway_client, worker_keypair[0], task)
    await _accept_bid(client, poster_keypair[0], bid)
    await _submit_natively(gateway_client, bid)
    cutoff_us = clock.epoch_us() + _REVIEW_SECONDS * 1_000_000
    operation_id = _deadline_id(task.task_id, "auto_approve", 2, cutoff_us)
    probe = _install_bank_probe(
        gateway_client,
        gateway_db_path,
        source_status="submitted",
        operation_id=operation_id,
        fail=False,
    )
    clock.set(_START + timedelta(seconds=_REVIEW_SECONDS))

    response = await client.get(f"/tasks/{task.task_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert probe.claim_seen == [True]
    _assert_native_completion(
        gateway_db_path,
        task_id=task.task_id,
        operation_id=operation_id,
        command="auto_approve",
        event_type="task.auto_approved",
        recipient_id=bid.worker_id,
    )


async def test_bank_failure_keeps_source_state_and_reduced_pending_projection(
    client: httpx.AsyncClient,
    gateway_client: httpx.AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _install_clock(monkeypatch)
    task = await _create_task(client, gateway_client, poster_keypair[0])
    cutoff_us = clock.epoch_us() + _BIDDING_SECONDS * 1_000_000
    operation_id = _deadline_id(task.task_id, "expire_bidding", 0, cutoff_us)
    probe = _install_bank_probe(
        gateway_client,
        gateway_db_path,
        source_status="open",
        operation_id=operation_id,
        fail=True,
    )
    clock.set(_START + timedelta(seconds=_BIDDING_SECONDS))

    response = await client.get(f"/tasks/{task.task_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "open"
    assert body["pending_transition"] == {
        "operation_id": operation_id,
        "command": "expire_bidding",
        "target_status": "expired",
        "target_lifecycle_version": 1,
        "operation_state": "claimed",
        "effect_state": "pending",
    }
    assert set(body["pending_transition"]) == {
        "operation_id",
        "command",
        "target_status",
        "target_lifecycle_version",
        "operation_state",
        "effect_state",
    }
    assert probe.claim_seen == [True]
    assert _rows(
        gateway_db_path,
        "SELECT operation_state,effect_state FROM board_task_operations WHERE operation_id=?",
        (operation_id,),
    ) == [{"operation_state": "claimed", "effect_state": "pending"}]
    assert (
        _rows(
            gateway_db_path,
            "SELECT operation_id FROM bank_operation_receipts WHERE operation_id=?",
            (operation_id,),
        )
        == []
    )
