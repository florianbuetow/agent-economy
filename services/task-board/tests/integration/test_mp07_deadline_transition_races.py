"""T-122 proofs for task actions racing their authoritative deadlines.

The Task Board and DB Gateway paths are real. Deadline winners persist Bank
receipts, ledger legs and escrow settlement through the authenticated Gateway.
Losing task actions create no operation, settlement or Court claim.
"""

from __future__ import annotations

import asyncio
import importlib
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from threading import Event, Lock
from typing import TYPE_CHECKING, Any, Protocol, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from service_auth.financial_identity import operation_id_for
from service_auth.gateway import GatewayRequestSigner, GatewaySignerConfig
from service_commons.exceptions import ServiceError

from task_board_service.core.state import get_app_state
from task_board_service.services.asset_manager import AssetManager
from task_board_service.services.deadline_evaluator import DeadlineEvaluator
from task_board_service.services.escrow_coordinator import EscrowCoordinator
from task_board_service.services.task_db_client import TaskDbClient
from task_board_service.services.task_manager import TaskManager
from task_board_service.services.token_validator import TokenValidator
from tests.helpers import make_jws_token as make_scoped_jws_token
from tests.helpers import verify_compact_jws
from tests.integration.conftest import make_jws_token, register_identity_agent
from tests.integration.mp05_posting_setup import (
    native_escrow_payload,
    native_task_payload,
    persist_lock_after_admission,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    import httpx
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_START = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
_DEADLINE_SECONDS = 10
_WAIT_SECONDS = 10
_FUTURE_SECONDS = 15
_DISPUTE_REASON = "The submitted result does not satisfy the specification."

_atomic_helpers = importlib.import_module("tests.integration.test_mp07_atomic_task_transitions")
_gateway_write_client = cast(
    "Callable[..., httpx.Client]",
    _atomic_helpers.__dict__["_gateway_write_client"],
)
_install_persisting_claim = cast(
    "Callable[..., AsyncMock]",
    _atomic_helpers.__dict__["_install_persisting_claim"],
)


class _GatewayEndpoint(Protocol):
    base_url: str


class _MutableDomainClock:
    """One synchronized domain clock shared by every task transition actor."""

    def __init__(self, initial: datetime) -> None:
        self._value = initial
        self._lock = Lock()

    def now(self, timezone: tzinfo | None) -> datetime:
        with self._lock:
            value = self._value
        if timezone is None:
            return value.replace(tzinfo=None)
        return value.astimezone(timezone)

    def set(self, value: datetime) -> None:
        with self._lock:
            if value < self._value:
                raise AssertionError("MP-07 domain clock cannot move backwards")
            self._value = value

    def epoch_us(self) -> int:
        with self._lock:
            value = self._value
        epoch = datetime(1970, 1, 1, tzinfo=UTC)
        delta = value - epoch
        return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds


class _ClockDateTime:
    """datetime seam whose parsing stays real and whose current time is injected."""

    clock: _MutableDomainClock

    @classmethod
    def now(cls, timezone: tzinfo | None = None) -> datetime:
        return cls.clock.now(timezone)

    @classmethod
    def fromisoformat(cls, value: str) -> datetime:
        return datetime.fromisoformat(value)

    @classmethod
    def fromtimestamp(cls, timestamp: float, timezone: tzinfo | None = None) -> datetime:
        return datetime.fromtimestamp(timestamp, timezone)


class _PreClaimGate:
    """READY/RELEASE gate at the contender's actual native-claim boundary."""

    def __init__(self, *, task_id: str, expected_status: str, expected_command: str) -> None:
        self._task_id = task_id
        self._expected_status = expected_status
        self._expected_command = expected_command
        self._ready = Event()
        self._release = Event()
        self._armed = True

    def pause(self, task: dict[str, Any], command: dict[str, Any]) -> None:
        if (
            not self._armed
            or task.get("task_id") != self._task_id
            or command.get("task_id") != self._task_id
            or command.get("command") != self._expected_command
        ):
            return
        self._armed = False
        if task.get("status") != self._expected_status:
            raise AssertionError(
                "MP-07 harness expected the native claim boundary to observe "
                f"{self._expected_status}, got {task!r}"
            )
        if command.get("expected_status") != self._expected_status:
            raise AssertionError(f"MP-07 harness received the wrong claim: {command!r}")
        self._ready.set()
        if not self._release.wait(timeout=_WAIT_SECONDS):
            raise AssertionError("MP-07 harness RELEASE gate timed out")

    def wait_until_ready(self) -> None:
        if not self._ready.wait(timeout=_WAIT_SECONDS):
            raise AssertionError("MP-07 harness READY gate timed out")

    def release(self) -> None:
        self._release.set()


class _ClaimGateStore(TaskDbClient):
    """Pause one command immediately before its real Gateway claim."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: int,
        signer: GatewayRequestSigner,
        gate: _PreClaimGate,
    ) -> None:
        super().__init__(base_url=base_url, timeout_seconds=timeout_seconds, signer=signer)
        self._gate = gate

    def claim_task_operation(self, payload: dict[str, Any]) -> dict[str, Any]:
        task_id = payload.get("task_id")
        if isinstance(task_id, str):
            task = super().get_task(task_id)
            if task is not None:
                self._gate.pause(task, payload)
        return super().claim_task_operation(payload)


@dataclass(frozen=True)
class _RecordedBoundaries:
    bank: AsyncMock
    court: MagicMock


@dataclass(frozen=True)
class _BoardRuntime:
    manager: TaskManager
    boundaries: _RecordedBoundaries


@dataclass(frozen=True)
class _Outcome:
    operation: str
    result: dict[str, Any] | None
    error: ServiceError | None


def _install_domain_clock(monkeypatch: pytest.MonkeyPatch) -> _MutableDomainClock:
    clock = _MutableDomainClock(_START)
    _ClockDateTime.clock = clock
    for module_name in (
        "task_board_service.services.asset_manager",
        "task_board_service.services.deadline_evaluator",
        "task_board_service.services.task_db_client",
        "task_board_service.services.task_helpers",
    ):
        monkeypatch.setattr(f"{module_name}.datetime", _ClockDateTime)
    monkeypatch.setattr("db_gateway_service.task_time.utc_now_us", clock.epoch_us)
    return clock


def _read_rows(
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


def _epoch_us(value: datetime) -> int:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = value - epoch
    return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds


def _deadline_operation_id(task_id: str, command: str, version: int, cutoff_us: int) -> str:
    return operation_id_for("task-deadline", (task_id, command, str(version), str(cutoff_us)))


def _utc_text(value: datetime) -> str:
    return value.isoformat(timespec="auto").replace("+00:00", "Z")


def _install_clocked_release(
    runtime: _BoardRuntime,
    gateway_client: Any,
    *,
    gateway_db_path: str,
    task_id: str,
    operation_id: str,
    command: str,
    source_status: str,
    source_version: int,
    escrow_id: str,
    recipient_id: str,
    source_reference: str,
) -> AsyncMock:
    async def _release(*args: object, **kwargs: object) -> dict[str, Any]:
        assert args == ()
        assert kwargs == {
            "escrow_id": escrow_id,
            "recipient_account_id": recipient_id,
            "operation_id": operation_id,
            "source_reference": source_reference,
        }
        assert _read_rows(
            gateway_db_path,
            "SELECT command,source_status,source_lifecycle_version,operation_state,"
            "effect_state,effect_operation_id FROM board_task_operations WHERE operation_id=?",
            (operation_id,),
        ) == [
            {
                "command": command,
                "source_status": source_status,
                "source_lifecycle_version": source_version,
                "operation_state": "claimed",
                "effect_state": "pending",
                "effect_operation_id": operation_id,
            }
        ]
        resolved_at = _utc_text(_ClockDateTime.clock.now(UTC))
        response = gateway_client.post(
            "/bank/escrow/release",
            json={
                "operation_id": operation_id,
                "escrow_id": escrow_id,
                "recipient_account_id": recipient_id,
                "source_reference": source_reference,
                "tx_id": f"tx-{uuid.uuid4()}",
                "resolved_at": resolved_at,
                "event": {
                    "event_source": "central-bank",
                    "event_type": "escrow.released",
                    "timestamp": resolved_at,
                    "task_id": task_id,
                    "agent_id": recipient_id,
                    "summary": f"Released escrow for {task_id}",
                    "payload": "{}",
                },
            },
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["outcome"] == "native_receipt"
        return cast("dict[str, Any]", result)

    release = AsyncMock(side_effect=_release)
    runtime.boundaries.bank.escrow_release = release
    return release


async def _seed_funded_account(
    gateway_client: httpx.AsyncClient,
    *,
    payer_id: str,
    amount: int,
) -> None:
    await register_identity_agent(gateway_client, payer_id)
    account_response = await gateway_client.post(
        "/bank/accounts",
        json={
            "operation_id": f"op-{uuid.uuid4()}",
            "account_id": payer_id,
            "created_at": "2026-10-17T11:59:58Z",
            "balance": amount,
            "event": {
                "event_source": "central-bank",
                "event_type": "account.created",
                "timestamp": "2026-10-17T11:59:58Z",
                "agent_id": payer_id,
                "summary": f"Created account for {payer_id}",
                "payload": "{}",
            },
            "initial_credit": {
                "tx_id": f"tx-{uuid.uuid4()}",
                "amount": amount,
                "reference": "initial_balance",
                "timestamp": "2026-10-17T11:59:58Z",
            },
        },
    )
    assert account_response.status_code == 201, account_response.text


async def _create_task_ready_for_deadline_race(
    client: httpx.AsyncClient,
    gateway_client: httpx.AsyncClient,
    poster_key: Ed25519PrivateKey,
    worker_key: Ed25519PrivateKey,
    *,
    submit: bool,
) -> tuple[str, str, str]:
    """Use public signed APIs to reach accepted-with-asset or submitted."""
    poster_id = f"a-mp07-deadline-poster-{uuid.uuid4()}"
    worker_id = f"a-mp07-deadline-worker-{uuid.uuid4()}"
    task_id = f"t-{uuid.uuid4()}"
    escrow_id = f"esc-{uuid.uuid4()}"

    await register_identity_agent(gateway_client, worker_id)
    worker_account = await gateway_client.post(
        "/bank/accounts",
        json={
            "account_id": worker_id,
            "balance": 0,
            "created_at": "2026-10-17T11:59:59Z",
            "event": {
                "event_source": "central-bank",
                "event_type": "account.created",
                "timestamp": "2026-10-17T11:59:59Z",
                "agent_id": worker_id,
                "summary": f"Created account for {worker_id}",
                "payload": "{}",
            },
        },
    )
    assert worker_account.status_code == 201, worker_account.text
    await _seed_funded_account(
        gateway_client,
        payer_id=poster_id,
        amount=100,
    )
    state = get_app_state()
    assert state.central_bank_client is not None
    task_payload = native_task_payload(
        task_id=task_id,
        poster_id=poster_id,
        title="MP-07 deadline transition race",
        spec="Reach the source state before an exact-cutoff transition race.",
        reward=100,
        bidding_deadline_seconds=3600,
        acceptance_window_seconds=3600,
        execution_deadline_seconds=_DEADLINE_SECONDS,
        review_deadline_seconds=_DEADLINE_SECONDS,
    )

    async def _persist_lock(_escrow_token: str) -> dict[str, Any]:
        return await persist_lock_after_admission(
            gateway_client,
            task_payload=task_payload,
            escrow_id=escrow_id,
            clock_us=_ClockDateTime.clock.epoch_us,
        )

    state.central_bank_client.escrow_lock = AsyncMock(side_effect=_persist_lock)
    task_token = make_jws_token(poster_key, poster_id, task_payload)
    escrow_token = make_scoped_jws_token(
        poster_key,
        poster_id,
        native_escrow_payload(task_payload),
        audience="central-bank",
        resource="POST /escrow/lock",
    )
    create_response = await client.post(
        "/tasks",
        json={"task_token": task_token, "escrow_token": escrow_token},
    )
    assert create_response.status_code == 201, create_response.text

    bid_token = make_jws_token(
        worker_key,
        worker_id,
        {
            "action": "submit_bid",
            "operation_id": f"op-{uuid.uuid4()}",
            "expected_lifecycle_version": 0,
            "task_id": task_id,
            "bidder_id": worker_id,
            "amount": 90,
        },
    )
    bid_response = await client.post(f"/tasks/{task_id}/bids", json={"token": bid_token})
    assert bid_response.status_code == 201, bid_response.text
    bid_id = str(bid_response.json()["bid_id"])

    accept_token = make_jws_token(
        poster_key,
        poster_id,
        {
            "action": "accept_bid",
            "operation_id": f"op-{uuid.uuid4()}",
            "expected_lifecycle_version": 0,
            "task_id": task_id,
            "bid_id": bid_id,
            "poster_id": poster_id,
        },
    )
    accept_response = await client.post(
        f"/tasks/{task_id}/bids/{bid_id}/accept",
        json={"token": accept_token},
    )
    assert accept_response.status_code == 200, accept_response.text
    assert accept_response.json()["status"] == "accepted"

    upload_token = make_jws_token(
        worker_key,
        worker_id,
        {"action": "upload_asset", "task_id": task_id},
    )
    upload_response = await client.post(
        f"/tasks/{task_id}/assets",
        headers={"Authorization": f"Bearer {upload_token}"},
        files={"file": ("result.txt", b"completed", "text/plain")},
    )
    assert upload_response.status_code == 201, upload_response.text

    if submit:
        submit_token = make_jws_token(
            worker_key,
            worker_id,
            {
                "action": "submit_deliverable",
                "operation_id": f"op-{uuid.uuid4()}",
                "expected_lifecycle_version": 1,
                "task_id": task_id,
                "worker_id": worker_id,
            },
        )
        submit_response = await client.post(
            f"/tasks/{task_id}/submit",
            json={"token": submit_token},
        )
        assert submit_response.status_code == 200, submit_response.text
        assert submit_response.json()["status"] == "submitted"

    return task_id, poster_id, worker_id


def _make_runtime(
    *,
    gateway: _GatewayEndpoint,
    gateway_db_path: str,
    asset_path: Path,
    cleanup: ExitStack,
    gate: _PreClaimGate | None,
) -> _BoardRuntime:
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
    if gate is None:
        store: TaskDbClient = TaskDbClient(
            base_url=gateway.base_url,
            timeout_seconds=10,
            signer=signer,
        )
    else:
        store = _ClaimGateStore(
            base_url=gateway.base_url,
            timeout_seconds=10,
            signer=signer,
            gate=gate,
        )
    cleanup.callback(store.close)

    platform = MagicMock()
    platform.agent_id = "a-platform-test-id"
    platform.validate_certificate = MagicMock(side_effect=verify_compact_jws)
    platform.file_claim = AsyncMock(
        return_value={"dispute_id": f"disp-{uuid.uuid4()}", "status": "rebuttal_pending"}
    )
    identity = AsyncMock()
    identity.verify_jws = AsyncMock(side_effect=verify_compact_jws)
    token_validator = TokenValidator(platform_agent=platform, identity_client=identity)

    bank = AsyncMock()
    bank.escrow_release = AsyncMock(return_value={"status": "recorded-boundary-only"})
    bank.escrow_split = AsyncMock(return_value={"status": "recorded-boundary-only"})
    escrow = EscrowCoordinator(central_bank_client=bank)
    deadline = DeadlineEvaluator(
        store,
        escrow,
        platform_agent=platform,
        deadline_actor_id="system-task-board-deadline",
    )
    assets = AssetManager(
        store=store,
        token_validator=token_validator,
        deadline_evaluator=deadline,
        asset_storage_path=str(asset_path),
        max_file_size=10_485_760,
        max_files_per_task=20,
    )
    manager = TaskManager(
        store=store,
        central_bank_client=bank,
        escrow_coordinator=escrow,
        token_validator=token_validator,
        deadline_evaluator=deadline,
        asset_manager=assets,
        platform_signer=platform,
        platform_agent_id="a-platform-test-id",
    )
    return _BoardRuntime(manager=manager, boundaries=_RecordedBoundaries(bank, platform))


def _invoke(
    operation: str,
    call: Callable[[], Coroutine[Any, Any, dict[str, Any]]],
) -> _Outcome:
    try:
        return _Outcome(operation=operation, result=asyncio.run(call()), error=None)
    except ServiceError as exc:
        return _Outcome(operation=operation, result=None, error=exc)


def _evaluate_deadline(runtime: _BoardRuntime, task_id: str) -> dict[str, Any]:
    task = runtime.manager.store.get_task(task_id)
    if task is None:
        raise AssertionError(f"MP-07 harness could not reload task {task_id}")
    return asyncio.run(runtime.manager.deadline_evaluator.evaluate_deadline(task))


def _diagnostics(
    outcome: _Outcome,
    task_rows: list[dict[str, object]],
    events: list[str],
) -> dict[str, Any]:
    return {
        "outcome": {
            "operation": outcome.operation,
            "result_status": outcome.result.get("status") if outcome.result else None,
            "error": outcome.error.error if outcome.error else None,
            "status_code": outcome.error.status_code if outcome.error else None,
        },
        "task_rows": task_rows,
        "semantic_events": events,
    }


@pytest.mark.integration
async def test_stale_validated_submit_cannot_overwrite_execution_expiry(
    client: httpx.AsyncClient,
    gateway_client: httpx.AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    worker_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
    _gateway: _GatewayEndpoint,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _install_domain_clock(monkeypatch)
    task_id, poster_id, worker_id = await _create_task_ready_for_deadline_race(
        client,
        gateway_client,
        poster_keypair[0],
        worker_keypair[0],
        submit=False,
    )
    contract_rows = _read_rows(
        gateway_db_path,
        "SELECT t.escrow_id,c.execution_cutoff_us,c.lifecycle_version "
        "FROM board_tasks AS t JOIN board_task_contracts AS c ON c.task_id=t.task_id "
        "WHERE t.task_id=?",
        (task_id,),
    )
    assert len(contract_rows) == 1
    expected_cutoff_us = _epoch_us(_START + timedelta(seconds=_DEADLINE_SECONDS))
    assert contract_rows == [
        {
            "escrow_id": contract_rows[0]["escrow_id"],
            "execution_cutoff_us": expected_cutoff_us,
            "lifecycle_version": 1,
        }
    ]
    escrow_id = str(contract_rows[0]["escrow_id"])
    submit_operation_id = f"op-{uuid.uuid4()}"
    expiry_operation_id = _deadline_operation_id(
        task_id,
        "expire_execution",
        1,
        expected_cutoff_us,
    )
    clock.set(_START + timedelta(seconds=_DEADLINE_SECONDS, microseconds=-1))
    gate = _PreClaimGate(
        task_id=task_id,
        expected_status="accepted",
        expected_command="submit_deliverable",
    )

    with ExitStack() as cleanup:
        cleanup.callback(gate.release)
        contender = _make_runtime(
            gateway=_gateway,
            gateway_db_path=gateway_db_path,
            asset_path=tmp_path / "submit-contender-assets",
            cleanup=cleanup,
            gate=gate,
        )
        deadline = _make_runtime(
            gateway=_gateway,
            gateway_db_path=gateway_db_path,
            asset_path=tmp_path / "execution-expiry-assets",
            cleanup=cleanup,
            gate=None,
        )
        bank_gateway = _gateway_write_client(
            _gateway,
            gateway_db_path,
            kid="gateway-central-bank",
            action="gateway.bank.write",
            cleanup=cleanup,
        )
        _install_clocked_release(
            deadline,
            bank_gateway,
            gateway_db_path=gateway_db_path,
            task_id=task_id,
            operation_id=expiry_operation_id,
            command="expire_execution",
            source_status="accepted",
            source_version=1,
            escrow_id=escrow_id,
            recipient_id=poster_id,
            source_reference=f"task:{task_id}:expired",
        )
        submit_token = make_jws_token(
            worker_keypair[0],
            worker_id,
            {
                "action": "submit_deliverable",
                "operation_id": submit_operation_id,
                "expected_lifecycle_version": 1,
                "task_id": task_id,
                "worker_id": worker_id,
            },
        )
        executor = cleanup.enter_context(ThreadPoolExecutor(max_workers=2))
        submit_future = executor.submit(
            _invoke,
            "submit",
            lambda: contender.manager.submit_deliverable(task_id, submit_token),
        )
        try:
            await asyncio.to_thread(gate.wait_until_ready)
            clock.set(_START + timedelta(seconds=_DEADLINE_SECONDS))
            expiry_future = executor.submit(_evaluate_deadline, deadline, task_id)
            expiry_result = expiry_future.result(timeout=_FUTURE_SECONDS)
            if expiry_result.get("status") != "expired":
                raise AssertionError(
                    "MP-07 harness execution-expiry actor did not commit the required winner: "
                    f"{expiry_result!r}"
                )
        finally:
            gate.release()

        try:
            outcome = submit_future.result(timeout=_FUTURE_SECONDS)
        except Exception as exc:
            raise AssertionError(
                "MP-07 submit contender failed or timed out outside the proof"
            ) from exc

        task_rows = _read_rows(
            gateway_db_path,
            "SELECT t.status,t.submitted_at,t.expired_at,t.approved_at,t.disputed_at,"
            "t.dispute_id,t.escrow_pending,c.lifecycle_version,c.execution_cutoff_us "
            "FROM board_tasks AS t JOIN board_task_contracts AS c ON c.task_id=t.task_id "
            "WHERE t.task_id=?",
            (task_id,),
        )
        event_rows = _read_rows(
            gateway_db_path,
            "SELECT event_id,event_type,timestamp FROM events WHERE task_id = ? "
            "AND event_type IN ('task.submitted', 'task.expired') ORDER BY event_id",
            (task_id,),
        )
        events = [str(row["event_type"]) for row in event_rows]
        operation_rows = _read_rows(
            gateway_db_path,
            "SELECT operation_id,command,operation_state,effect_kind,effect_state,event_id "
            "FROM board_task_operations WHERE operation_id IN (?,?) ORDER BY operation_id",
            (submit_operation_id, expiry_operation_id),
        )
        receipt_rows = _read_rows(
            gateway_db_path,
            "SELECT r.operation_id,r.operation_kind,r.source_reference,l.account_id,l.amount "
            "FROM bank_operation_receipts AS r JOIN bank_operation_legs AS l "
            "ON l.operation_id=r.operation_id WHERE r.operation_id IN (?,?)",
            (submit_operation_id, expiry_operation_id),
        )
        escrow_rows = _read_rows(
            gateway_db_path,
            "SELECT status,resolved_at FROM bank_escrow WHERE escrow_id=?",
            (escrow_id,),
        )
        diagnostics = _diagnostics(outcome, task_rows, events)
        diagnostics.update(
            {
                "expiry_bank_calls": deadline.boundaries.bank.escrow_release.await_count,
                "submit_bank_calls": contender.boundaries.bank.escrow_release.await_count,
                "submit_court_calls": contender.boundaries.court.file_claim.await_count,
                "operation_rows": operation_rows,
                "receipt_rows": receipt_rows,
                "escrow_rows": escrow_rows,
            }
        )

        if outcome.error is not None and outcome.error.status_code != 409:
            raise AssertionError(
                {
                    "message": "Unexpected non-409 submit error; not the intended T-122 red",
                    **diagnostics,
                }
            )
        assert outcome.error is not None and outcome.error.status_code == 409, diagnostics
        assert outcome.error.error == "task_version_conflict", diagnostics
        assert len(task_rows) == 1, diagnostics
        task_row = task_rows[0]
        assert task_row["status"] == "expired", diagnostics
        assert task_row["expired_at"] is not None, diagnostics
        assert task_row["submitted_at"] is None, diagnostics
        assert task_row["approved_at"] is None, diagnostics
        assert task_row["disputed_at"] is None, diagnostics
        assert task_row["dispute_id"] is None, diagnostics
        assert task_row["lifecycle_version"] == 2, diagnostics
        assert task_row["execution_cutoff_us"] == expected_cutoff_us, diagnostics
        assert task_row["expired_at"] == "2026-10-17T12:00:10Z", diagnostics
        assert events == ["task.expired"], diagnostics
        assert event_rows[0]["timestamp"] == "2026-10-17T12:00:10Z", diagnostics
        assert deadline.boundaries.bank.escrow_release.await_count == 1, diagnostics
        assert contender.boundaries.bank.escrow_release.await_count == 0, diagnostics
        assert contender.boundaries.court.file_claim.await_count == 0, diagnostics
        assert operation_rows == [
            {
                "operation_id": expiry_operation_id,
                "command": "expire_execution",
                "operation_state": "completed",
                "effect_kind": "bank_release",
                "effect_state": "succeeded",
                "event_id": event_rows[0]["event_id"],
            }
        ], diagnostics
        assert receipt_rows == [
            {
                "operation_id": expiry_operation_id,
                "operation_kind": "escrow_release",
                "source_reference": f"task:{task_id}:expired",
                "account_id": poster_id,
                "amount": 100,
            }
        ], diagnostics
        assert escrow_rows[0]["status"] == "released", diagnostics
        assert escrow_rows[0]["resolved_at"] == "2026-10-17T12:00:10Z", diagnostics


@pytest.mark.integration
@pytest.mark.parametrize("action", ["approve", "dispute"])
async def test_stale_review_action_cannot_beat_autoapproval_at_cutoff(
    action: str,
    client: httpx.AsyncClient,
    gateway_client: httpx.AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    worker_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
    _gateway: _GatewayEndpoint,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _install_domain_clock(monkeypatch)
    task_id, poster_id, worker_id = await _create_task_ready_for_deadline_race(
        client,
        gateway_client,
        poster_keypair[0],
        worker_keypair[0],
        submit=True,
    )
    contract_rows = _read_rows(
        gateway_db_path,
        "SELECT t.escrow_id,c.review_cutoff_us,c.lifecycle_version "
        "FROM board_tasks AS t JOIN board_task_contracts AS c ON c.task_id=t.task_id "
        "WHERE t.task_id=?",
        (task_id,),
    )
    assert len(contract_rows) == 1
    expected_cutoff_us = _epoch_us(_START + timedelta(seconds=_DEADLINE_SECONDS))
    assert contract_rows == [
        {
            "escrow_id": contract_rows[0]["escrow_id"],
            "review_cutoff_us": expected_cutoff_us,
            "lifecycle_version": 2,
        }
    ]
    escrow_id = str(contract_rows[0]["escrow_id"])
    contender_operation_id = f"op-{uuid.uuid4()}"
    automatic_operation_id = _deadline_operation_id(
        task_id,
        "auto_approve",
        2,
        expected_cutoff_us,
    )
    claim_id = f"clm-{uuid.uuid4()}"
    clock.set(_START + timedelta(seconds=_DEADLINE_SECONDS, microseconds=-1))
    gate = _PreClaimGate(
        task_id=task_id,
        expected_status="submitted",
        expected_command=f"{action}_task",
    )

    with ExitStack() as cleanup:
        cleanup.callback(gate.release)
        contender = _make_runtime(
            gateway=_gateway,
            gateway_db_path=gateway_db_path,
            asset_path=tmp_path / f"{action}-contender-assets",
            cleanup=cleanup,
            gate=gate,
        )
        deadline = _make_runtime(
            gateway=_gateway,
            gateway_db_path=gateway_db_path,
            asset_path=tmp_path / "autoapproval-assets",
            cleanup=cleanup,
            gate=None,
        )
        automatic_bank_gateway = _gateway_write_client(
            _gateway,
            gateway_db_path,
            kid="gateway-central-bank",
            action="gateway.bank.write",
            cleanup=cleanup,
        )
        _install_clocked_release(
            deadline,
            automatic_bank_gateway,
            gateway_db_path=gateway_db_path,
            task_id=task_id,
            operation_id=automatic_operation_id,
            command="auto_approve",
            source_status="submitted",
            source_version=2,
            escrow_id=escrow_id,
            recipient_id=worker_id,
            source_reference=f"task:{task_id}:approved",
        )
        if action == "approve":
            manual_bank_gateway = _gateway_write_client(
                _gateway,
                gateway_db_path,
                kid="gateway-central-bank",
                action="gateway.bank.write",
                cleanup=cleanup,
            )
            _install_clocked_release(
                contender,
                manual_bank_gateway,
                gateway_db_path=gateway_db_path,
                task_id=task_id,
                operation_id=contender_operation_id,
                command="approve_task",
                source_status="submitted",
                source_version=2,
                escrow_id=escrow_id,
                recipient_id=worker_id,
                source_reference=f"task:{task_id}:approved",
            )
        else:
            court_gateway = _gateway_write_client(
                _gateway,
                gateway_db_path,
                kid="gateway-court",
                action="gateway.court.write",
                cleanup=cleanup,
            )
            _install_persisting_claim(
                contender,
                court_gateway,
                gateway_db_path=gateway_db_path,
                task_id=task_id,
                operation_id=contender_operation_id,
                claim_id=claim_id,
                poster_id=poster_id,
                worker_id=worker_id,
                reason=_DISPUTE_REASON,
            )
        payload: dict[str, Any] = {
            "action": f"{action}_task",
            "operation_id": contender_operation_id,
            "expected_lifecycle_version": 2,
            "task_id": task_id,
            "poster_id": poster_id,
        }
        if action == "dispute":
            payload["reason"] = _DISPUTE_REASON
        token = make_jws_token(poster_keypair[0], poster_id, payload)

        async def call() -> dict[str, Any]:
            if action == "approve":
                return await contender.manager.approve_task(task_id, token)
            return await contender.manager.dispute_task(
                task_id,
                token,
                contender.boundaries.court,
            )

        executor = cleanup.enter_context(ThreadPoolExecutor(max_workers=2))
        contender_future = executor.submit(_invoke, action, call)
        try:
            await asyncio.to_thread(gate.wait_until_ready)
            clock.set(_START + timedelta(seconds=_DEADLINE_SECONDS))
            auto_future = executor.submit(_evaluate_deadline, deadline, task_id)
            auto_result = auto_future.result(timeout=_FUTURE_SECONDS)
            if auto_result.get("status") != "approved":
                raise AssertionError(
                    "MP-07 harness autoapproval actor did not commit the required winner: "
                    f"{auto_result!r}"
                )
        finally:
            gate.release()

        try:
            outcome = contender_future.result(timeout=_FUTURE_SECONDS)
        except Exception as exc:
            raise AssertionError(
                f"MP-07 {action} contender failed or timed out outside the proof"
            ) from exc

        task_rows = _read_rows(
            gateway_db_path,
            "SELECT t.status,t.approved_at,t.expired_at,t.disputed_at,t.dispute_reason,"
            "t.dispute_id,t.escrow_pending,c.lifecycle_version,c.review_cutoff_us "
            "FROM board_tasks AS t JOIN board_task_contracts AS c ON c.task_id=t.task_id "
            "WHERE t.task_id=?",
            (task_id,),
        )
        event_rows = _read_rows(
            gateway_db_path,
            "SELECT event_id,event_type,timestamp FROM events WHERE task_id = ? "
            "AND event_type IN ('task.approved', 'task.auto_approved', 'task.disputed') "
            "ORDER BY event_id",
            (task_id,),
        )
        events = [str(row["event_type"]) for row in event_rows]
        operation_rows = _read_rows(
            gateway_db_path,
            "SELECT operation_id,command,operation_state,effect_kind,effect_state,event_id "
            "FROM board_task_operations WHERE operation_id IN (?,?) ORDER BY operation_id",
            (contender_operation_id, automatic_operation_id),
        )
        receipt_rows = _read_rows(
            gateway_db_path,
            "SELECT r.operation_id,r.operation_kind,r.source_reference,l.account_id,l.amount "
            "FROM bank_operation_receipts AS r JOIN bank_operation_legs AS l "
            "ON l.operation_id=r.operation_id WHERE r.operation_id IN (?,?)",
            (contender_operation_id, automatic_operation_id),
        )
        claim_rows = _read_rows(
            gateway_db_path,
            "SELECT claim_id,task_id,claimant_id,respondent_id,reason FROM court_claims "
            "WHERE task_id=?",
            (task_id,),
        )
        escrow_rows = _read_rows(
            gateway_db_path,
            "SELECT status,resolved_at FROM bank_escrow WHERE escrow_id=?",
            (escrow_id,),
        )
        diagnostics = _diagnostics(outcome, task_rows, events)
        diagnostics.update(
            {
                "autoapproval_bank_calls": deadline.boundaries.bank.escrow_release.await_count,
                "manual_bank_calls": contender.boundaries.bank.escrow_release.await_count,
                "manual_court_calls": contender.boundaries.court.file_claim.await_count,
                "operation_rows": operation_rows,
                "receipt_rows": receipt_rows,
                "claim_rows": claim_rows,
                "escrow_rows": escrow_rows,
            }
        )

        if outcome.error is not None and outcome.error.status_code != 409:
            raise AssertionError(
                {
                    "message": f"Unexpected non-409 {action} error; not the intended T-122 red",
                    **diagnostics,
                }
            )
        assert outcome.error is not None and outcome.error.status_code == 409, diagnostics
        assert outcome.error.error == "task_version_conflict", diagnostics
        assert len(task_rows) == 1, diagnostics
        task_row = task_rows[0]
        assert task_row["status"] == "approved", diagnostics
        assert task_row["approved_at"] is not None, diagnostics
        assert task_row["expired_at"] is None, diagnostics
        assert task_row["disputed_at"] is None, diagnostics
        assert task_row["dispute_reason"] is None, diagnostics
        assert task_row["dispute_id"] is None, diagnostics
        assert task_row["lifecycle_version"] == 3, diagnostics
        assert task_row["review_cutoff_us"] == expected_cutoff_us, diagnostics
        assert task_row["approved_at"] == "2026-10-17T12:00:10Z", diagnostics
        assert events == ["task.auto_approved"], diagnostics
        assert event_rows[0]["timestamp"] == "2026-10-17T12:00:10Z", diagnostics
        assert deadline.boundaries.bank.escrow_release.await_count == 1, diagnostics
        assert contender.boundaries.bank.escrow_release.await_count == 0, diagnostics
        assert contender.boundaries.court.file_claim.await_count == 0, diagnostics
        assert operation_rows == [
            {
                "operation_id": automatic_operation_id,
                "command": "auto_approve",
                "operation_state": "completed",
                "effect_kind": "bank_release",
                "effect_state": "succeeded",
                "event_id": event_rows[0]["event_id"],
            }
        ], diagnostics
        assert receipt_rows == [
            {
                "operation_id": automatic_operation_id,
                "operation_kind": "escrow_release",
                "source_reference": f"task:{task_id}:approved",
                "account_id": worker_id,
                "amount": 100,
            }
        ], diagnostics
        assert claim_rows == [], diagnostics
        assert escrow_rows[0]["status"] == "released", diagnostics
        assert escrow_rows[0]["resolved_at"] == "2026-10-17T12:00:10Z", diagnostics
