"""T-122 live proofs for selected concurrent native Task Board transitions."""

from __future__ import annotations

import asyncio
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from threading import Barrier, BrokenBarrierError
from typing import TYPE_CHECKING, Any, Protocol, cast
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from db_gateway_service.task_time import parse_utc_text_to_epoch_us
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
    from collections.abc import Callable, Coroutine, Generator

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_DEADLINE_ACTOR = "system-task-board-deadline"
_DISPUTE_REASON = "The submitted result does not satisfy the specification."
_POSTING_CLOCK_US = parse_utc_text_to_epoch_us("2026-10-17T09:00:01Z")


class _GatewayEndpoint(Protocol):
    base_url: str


class _GatewayWriteAuth(httpx.Auth):
    """Sign one Gateway write domain with its owned test capability."""

    def __init__(self, signer: GatewayRequestSigner, action: str) -> None:
        self._signer = signer
        self._action = action

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        yield self._signer.sign_request(request, action=self._action)


class _ReadBarrierStore(TaskDbClient):
    """Real TaskDbClient that pauses one observed source-state read."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: int,
        signer: GatewayRequestSigner,
        task_id: str,
        barrier: Barrier,
        expected_status: str,
    ) -> None:
        super().__init__(base_url=base_url, timeout_seconds=timeout_seconds, signer=signer)
        self._task_id = task_id
        self._barrier = barrier
        self._expected_status = expected_status
        self._armed = True

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        task = super().get_task(task_id)
        if self._armed and task_id == self._task_id:
            self._armed = False
            if task is None or task["status"] != self._expected_status:
                raise AssertionError(
                    "MP-07 harness expected the armed read to observe "
                    f"{self._expected_status}, got {task!r}"
                )
            try:
                self._barrier.wait(timeout=10)
            except BrokenBarrierError as exc:
                raise AssertionError("MP-07 harness read barrier broke or timed out") from exc
        return task


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


async def _seed_funded_account(
    gateway_client: httpx.AsyncClient,
    *,
    payer_id: str,
    amount: int,
) -> None:
    """Register and fund the poster without locking before Board admission."""
    await register_identity_agent(gateway_client, payer_id)
    account_response = await gateway_client.post(
        "/bank/accounts",
        json={
            "operation_id": f"op-{uuid.uuid4()}",
            "account_id": payer_id,
            "created_at": "2026-10-17T09:00:00Z",
            "balance": amount,
            "event": {
                "event_source": "central-bank",
                "event_type": "account.created",
                "timestamp": "2026-10-17T09:00:00Z",
                "agent_id": payer_id,
                "summary": f"Created account for {payer_id}",
                "payload": "{}",
            },
            "initial_credit": {
                "tx_id": f"tx-{uuid.uuid4()}",
                "amount": amount,
                "reference": "initial_balance",
                "timestamp": "2026-10-17T09:00:00Z",
            },
        },
    )
    assert account_response.status_code == 201, account_response.text


async def _create_zero_balance_account(
    gateway_client: httpx.AsyncClient,
    *,
    account_id: str,
) -> None:
    response = await gateway_client.post(
        "/bank/accounts",
        json={
            "account_id": account_id,
            "balance": 0,
            "created_at": "2026-10-17T09:00:02Z",
            "event": {
                "event_source": "central-bank",
                "event_type": "account.created",
                "timestamp": "2026-10-17T09:00:02Z",
                "agent_id": account_id,
                "summary": f"Created account for {account_id}",
                "payload": "{}",
            },
        },
    )
    assert response.status_code == 201, response.text


async def _create_submitted_task(
    client: httpx.AsyncClient,
    gateway_client: httpx.AsyncClient,
    poster_key: Ed25519PrivateKey,
    worker_key: Ed25519PrivateKey,
) -> tuple[str, str, str]:
    """Use the normal signed Board API to establish a submitted task."""
    poster_id = f"a-mp07-poster-{uuid.uuid4()}"
    worker_id = f"a-mp07-worker-{uuid.uuid4()}"
    task_id = f"t-{uuid.uuid4()}"
    escrow_id = f"esc-{uuid.uuid4()}"

    await register_identity_agent(gateway_client, worker_id)
    await _create_zero_balance_account(gateway_client, account_id=worker_id)
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
        title="MP-07 approval versus dispute",
        spec="Reach submitted before the controlled conflicting review actions.",
        reward=100,
        bidding_deadline_seconds=3600,
        acceptance_window_seconds=3600,
        execution_deadline_seconds=86400,
        review_deadline_seconds=86400,
    )

    async def _persist_lock(_escrow_token: str) -> dict[str, Any]:
        return await persist_lock_after_admission(
            gateway_client,
            task_payload=task_payload,
            escrow_id=escrow_id,
            clock_us=lambda: _POSTING_CLOCK_US,
        )

    state.central_bank_client.escrow_lock = AsyncMock(side_effect=_persist_lock)
    create_response = await client.post(
        "/tasks",
        json={
            "task_token": make_jws_token(poster_key, poster_id, task_payload),
            "escrow_token": make_scoped_jws_token(
                poster_key,
                poster_id,
                native_escrow_payload(task_payload),
                audience="central-bank",
                resource="POST /escrow/lock",
            ),
        },
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
            "amount": 100,
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


async def _create_open_task_with_two_bids(
    client: httpx.AsyncClient,
    gateway_client: httpx.AsyncClient,
    poster_key: Ed25519PrivateKey,
    first_worker_key: Ed25519PrivateKey,
) -> tuple[str, str, tuple[str, str], tuple[str, str]]:
    """Use signed Board APIs to establish an open task with two valid bids."""
    poster_id = f"a-mp07-poster-{uuid.uuid4()}"
    task_id = f"t-{uuid.uuid4()}"
    escrow_id = f"esc-{uuid.uuid4()}"
    first_worker_id = f"a-mp07-worker-{uuid.uuid4()}"
    second_worker_id = f"a-mp07-worker-{uuid.uuid4()}"
    second_worker_key = Ed25519PrivateKey.generate()

    await register_identity_agent(gateway_client, first_worker_id)
    await register_identity_agent(gateway_client, second_worker_id)
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
        title="MP-07 open task transition race",
        spec="Reach open with two valid bids before the controlled race.",
        reward=100,
        bidding_deadline_seconds=86400,
        acceptance_window_seconds=86400,
        execution_deadline_seconds=86400,
        review_deadline_seconds=86400,
    )

    async def _persist_lock(_escrow_token: str) -> dict[str, Any]:
        return await persist_lock_after_admission(
            gateway_client,
            task_payload=task_payload,
            escrow_id=escrow_id,
            clock_us=lambda: _POSTING_CLOCK_US,
        )

    state.central_bank_client.escrow_lock = AsyncMock(side_effect=_persist_lock)
    create_response = await client.post(
        "/tasks",
        json={
            "task_token": make_jws_token(poster_key, poster_id, task_payload),
            "escrow_token": make_scoped_jws_token(
                poster_key,
                poster_id,
                native_escrow_payload(task_payload),
                audience="central-bank",
                resource="POST /escrow/lock",
            ),
        },
    )
    assert create_response.status_code == 201, create_response.text

    bids: list[tuple[str, str]] = []
    bidder_credentials = (
        (first_worker_id, first_worker_key, 90),
        (second_worker_id, second_worker_key, 80),
    )
    for worker_id, worker_key, amount in bidder_credentials:
        bid_token = make_jws_token(
            worker_key,
            worker_id,
            {
                "action": "submit_bid",
                "operation_id": f"op-{uuid.uuid4()}",
                "expected_lifecycle_version": 0,
                "task_id": task_id,
                "bidder_id": worker_id,
                "amount": amount,
            },
        )
        bid_response = await client.post(f"/tasks/{task_id}/bids", json={"token": bid_token})
        assert bid_response.status_code == 201, bid_response.text
        bids.append((str(bid_response.json()["bid_id"]), worker_id))

    return task_id, poster_id, bids[0], bids[1]


def _make_runtime(
    *,
    gateway: _GatewayEndpoint,
    gateway_db_path: str,
    task_id: str,
    barrier: Barrier,
    expected_status: str,
    asset_path: Path,
    cleanup: ExitStack,
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
    store = _ReadBarrierStore(
        base_url=gateway.base_url,
        timeout_seconds=10,
        signer=signer,
        task_id=task_id,
        barrier=barrier,
        expected_status=expected_status,
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
        store=store,
        escrow_coordinator=escrow,
        platform_agent=platform,
        deadline_actor_id=_DEADLINE_ACTOR,
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
    return _BoardRuntime(
        manager=manager,
        boundaries=_RecordedBoundaries(bank=bank, court=platform),
    )


def _gateway_write_client(
    gateway: _GatewayEndpoint,
    gateway_db_path: str,
    *,
    kid: str,
    action: str,
    cleanup: ExitStack,
) -> httpx.Client:
    key_dir = Path(gateway_db_path).parent
    signer = GatewayRequestSigner(
        GatewaySignerConfig(
            kid=kid,
            private_key_path=key_dir / f"{kid}.key",
            audience="db-gateway",
            token_ttl_seconds=300,
        ),
        config_dir=key_dir,
    )
    client = httpx.Client(
        base_url=gateway.base_url,
        auth=_GatewayWriteAuth(signer, action),
        timeout=10,
    )
    cleanup.callback(client.close)
    return client


def _install_persisting_release(
    runtime: _BoardRuntime,
    gateway_client: httpx.Client,
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
        response = gateway_client.post(
            "/bank/escrow/release",
            json={
                "operation_id": operation_id,
                "escrow_id": escrow_id,
                "recipient_account_id": recipient_id,
                "source_reference": source_reference,
                "tx_id": f"tx-{uuid.uuid4()}",
                "resolved_at": "2026-10-17T09:00:10Z",
                "event": {
                    "event_source": "central-bank",
                    "event_type": "escrow.released",
                    "timestamp": "2026-10-17T09:00:10Z",
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


def _install_persisting_claim(
    runtime: _BoardRuntime,
    gateway_client: httpx.Client,
    *,
    gateway_db_path: str,
    task_id: str,
    operation_id: str,
    claim_id: str,
    poster_id: str,
    worker_id: str,
    reason: str,
) -> AsyncMock:
    async def _file_claim(*args: object, **kwargs: object) -> dict[str, Any]:
        assert args == ()
        assert kwargs["task_id"] == task_id
        assert kwargs["claimant_id"] == poster_id
        assert kwargs["respondent_id"] == worker_id
        assert kwargs["claim"] == reason
        assert _read_rows(
            gateway_db_path,
            "SELECT command,source_status,source_lifecycle_version,operation_state,"
            "effect_kind,effect_state FROM board_task_operations WHERE operation_id=?",
            (operation_id,),
        ) == [
            {
                "command": "dispute_task",
                "source_status": "submitted",
                "source_lifecycle_version": 2,
                "operation_state": "claimed",
                "effect_kind": "court_claim",
                "effect_state": "pending",
            }
        ]
        response = gateway_client.post(
            "/court/claims",
            json={
                "claim_id": claim_id,
                "task_id": task_id,
                "claimant_id": poster_id,
                "respondent_id": worker_id,
                "reason": reason,
                "status": "rebuttal_pending",
                "rebuttal_deadline": "2026-10-17T10:00:20Z",
                "filed_at": "2026-10-17T09:00:20Z",
                "event": {
                    "event_source": "court",
                    "event_type": "claim.filed",
                    "timestamp": "2026-10-17T09:00:20Z",
                    "task_id": task_id,
                    "agent_id": poster_id,
                    "summary": f"Filed claim {claim_id}",
                    "payload": "{}",
                },
            },
        )
        assert response.status_code == 201, response.text
        return {
            "dispute_id": claim_id,
            "status": "rebuttal_pending",
            "rebuttal_deadline": "2026-10-17T10:00:20Z",
        }

    file_claim = AsyncMock(side_effect=_file_claim)
    runtime.boundaries.court.file_claim = file_claim
    return file_claim


def _invoke(
    operation: str,
    call: Callable[[], Coroutine[Any, Any, dict[str, Any]]],
) -> _Outcome:
    try:
        return _Outcome(operation=operation, result=asyncio.run(call()), error=None)
    except ServiceError as exc:
        return _Outcome(operation=operation, result=None, error=exc)


async def test_concurrent_approve_and_dispute_have_one_serial_winner(
    client: httpx.AsyncClient,
    gateway_client: httpx.AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    worker_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
    _gateway: _GatewayEndpoint,
    tmp_path: Path,
) -> None:
    task_id, poster_id, worker_id = await _create_submitted_task(
        client,
        gateway_client,
        poster_keypair[0],
        worker_keypair[0],
    )
    task_setup = _read_rows(
        gateway_db_path,
        "SELECT escrow_id FROM board_tasks WHERE task_id=?",
        (task_id,),
    )
    assert len(task_setup) == 1
    escrow_id = str(task_setup[0]["escrow_id"])
    approve_operation_id = f"op-{uuid.uuid4()}"
    dispute_operation_id = f"op-{uuid.uuid4()}"
    claim_id = f"clm-{uuid.uuid4()}"
    barrier = Barrier(2)
    with ExitStack() as cleanup:
        approve_runtime = _make_runtime(
            gateway=_gateway,
            gateway_db_path=gateway_db_path,
            task_id=task_id,
            barrier=barrier,
            expected_status="submitted",
            asset_path=tmp_path / "approve-assets",
            cleanup=cleanup,
        )
        dispute_runtime = _make_runtime(
            gateway=_gateway,
            gateway_db_path=gateway_db_path,
            task_id=task_id,
            barrier=barrier,
            expected_status="submitted",
            asset_path=tmp_path / "dispute-assets",
            cleanup=cleanup,
        )
        bank_gateway = _gateway_write_client(
            _gateway,
            gateway_db_path,
            kid="gateway-central-bank",
            action="gateway.bank.write",
            cleanup=cleanup,
        )
        court_gateway = _gateway_write_client(
            _gateway,
            gateway_db_path,
            kid="gateway-court",
            action="gateway.court.write",
            cleanup=cleanup,
        )
        _install_persisting_release(
            approve_runtime,
            bank_gateway,
            gateway_db_path=gateway_db_path,
            task_id=task_id,
            operation_id=approve_operation_id,
            command="approve_task",
            source_status="submitted",
            source_version=2,
            escrow_id=escrow_id,
            recipient_id=worker_id,
            source_reference=f"task:{task_id}:approved",
        )
        _install_persisting_claim(
            dispute_runtime,
            court_gateway,
            gateway_db_path=gateway_db_path,
            task_id=task_id,
            operation_id=dispute_operation_id,
            claim_id=claim_id,
            poster_id=poster_id,
            worker_id=worker_id,
            reason=_DISPUTE_REASON,
        )
        approve_token = make_jws_token(
            poster_keypair[0],
            poster_id,
            {
                "action": "approve_task",
                "task_id": task_id,
                "poster_id": poster_id,
                "operation_id": approve_operation_id,
                "expected_lifecycle_version": 2,
            },
        )
        dispute_token = make_jws_token(
            poster_keypair[0],
            poster_id,
            {
                "action": "dispute_task",
                "task_id": task_id,
                "poster_id": poster_id,
                "reason": _DISPUTE_REASON,
                "operation_id": dispute_operation_id,
                "expected_lifecycle_version": 2,
            },
        )

        with ThreadPoolExecutor(max_workers=2) as executor:
            approve_future = executor.submit(
                _invoke,
                "approve",
                lambda: approve_runtime.manager.approve_task(task_id, approve_token),
            )
            dispute_future = executor.submit(
                _invoke,
                "dispute",
                lambda: dispute_runtime.manager.dispute_task(
                    task_id,
                    dispute_token,
                    dispute_runtime.boundaries.court,
                ),
            )
            try:
                outcomes = [approve_future.result(timeout=15), dispute_future.result(timeout=15)]
            except Exception as exc:
                raise AssertionError("MP-07 contender thread failed or timed out") from exc

        task_rows = _read_rows(
            gateway_db_path,
            "SELECT status, approved_at, disputed_at, dispute_reason, dispute_id "
            "FROM board_tasks WHERE task_id = ?",
            (task_id,),
        )
        event_rows = _read_rows(
            gateway_db_path,
            "SELECT event_id, event_type FROM events WHERE task_id = ? "
            "AND event_type IN ('task.approved', 'task.disputed') ORDER BY event_id",
            (task_id,),
        )
        operation_rows = _read_rows(
            gateway_db_path,
            "SELECT operation_id,command,operation_state,effect_kind,effect_state,event_id "
            "FROM board_task_operations WHERE operation_id IN (?,?) ORDER BY operation_id",
            (approve_operation_id, dispute_operation_id),
        )
        receipt_rows = _read_rows(
            gateway_db_path,
            "SELECT r.operation_id,r.operation_kind,r.source_reference,l.account_id,l.amount "
            "FROM bank_operation_receipts AS r JOIN bank_operation_legs AS l "
            "ON l.operation_id=r.operation_id WHERE r.operation_id IN (?,?) "
            "ORDER BY r.operation_id,l.leg_index",
            (approve_operation_id, dispute_operation_id),
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
        successes = [outcome for outcome in outcomes if outcome.error is None]
        conflicts = [outcome for outcome in outcomes if outcome.error is not None]
        bank_release_count = sum(
            runtime.boundaries.bank.escrow_release.await_count
            for runtime in (approve_runtime, dispute_runtime)
        )
        court_claim_count = sum(
            runtime.boundaries.court.file_claim.await_count
            for runtime in (approve_runtime, dispute_runtime)
        )
        diagnostics = {
            "outcomes": [
                {
                    "operation": outcome.operation,
                    "result_status": outcome.result.get("status") if outcome.result else None,
                    "error": outcome.error.error if outcome.error else None,
                    "status_code": outcome.error.status_code if outcome.error else None,
                }
                for outcome in outcomes
            ],
            "task_rows": task_rows,
            "event_rows": event_rows,
            "bank_release_count": bank_release_count,
            "court_claim_count": court_claim_count,
            "operation_rows": operation_rows,
            "receipt_rows": receipt_rows,
            "claim_rows": claim_rows,
            "escrow_rows": escrow_rows,
        }

        unexpected_errors = [
            outcome
            for outcome in outcomes
            if outcome.error is not None and outcome.error.status_code != 409
        ]
        assert not unexpected_errors, {
            "message": "Unexpected non-conflict ServiceError; this is not the intended T-122 red",
            **diagnostics,
        }
        assert len(successes) == 1, diagnostics
        assert len(conflicts) == 1, diagnostics
        assert conflicts[0].error is not None
        assert conflicts[0].error.status_code == 409, diagnostics
        assert conflicts[0].error.error in {
            "transition_pending",
            "task_version_conflict",
        }, diagnostics
        assert len(task_rows) == 1, diagnostics

        winner = successes[0].operation
        expected_status = "approved" if winner == "approve" else "disputed"
        expected_event = "task.approved" if winner == "approve" else "task.disputed"
        assert successes[0].result is not None, diagnostics
        assert successes[0].result["status"] == expected_status, diagnostics
        assert successes[0].result["lifecycle_version"] == 3, diagnostics
        assert len(operation_rows) == 1, diagnostics
        assert operation_rows[0]["operation_id"] == (
            approve_operation_id if winner == "approve" else dispute_operation_id
        ), diagnostics
        assert operation_rows[0]["operation_state"] == "completed", diagnostics
        assert operation_rows[0]["event_id"] == event_rows[0]["event_id"], diagnostics
        task_row = task_rows[0]
        assert task_row["status"] == expected_status, diagnostics
        assert [row["event_type"] for row in event_rows] == [expected_event], diagnostics

        if winner == "approve":
            assert task_row["approved_at"] is not None, diagnostics
            assert task_row["disputed_at"] is None, diagnostics
            assert task_row["dispute_reason"] is None, diagnostics
            assert task_row["dispute_id"] is None, diagnostics
            assert bank_release_count == 1, diagnostics
            assert court_claim_count == 0, diagnostics
            assert operation_rows[0]["command"] == "approve_task", diagnostics
            assert operation_rows[0]["effect_kind"] == "bank_release", diagnostics
            assert operation_rows[0]["effect_state"] == "succeeded", diagnostics
            assert receipt_rows == [
                {
                    "operation_id": approve_operation_id,
                    "operation_kind": "escrow_release",
                    "source_reference": f"task:{task_id}:approved",
                    "account_id": worker_id,
                    "amount": 100,
                }
            ], diagnostics
            assert claim_rows == [], diagnostics
            assert escrow_rows[0]["status"] == "released", diagnostics
            assert escrow_rows[0]["resolved_at"] is not None, diagnostics
        else:
            assert task_row["approved_at"] is None, diagnostics
            assert task_row["disputed_at"] is not None, diagnostics
            assert task_row["dispute_reason"] is not None, diagnostics
            assert task_row["dispute_id"] is not None, diagnostics
            assert bank_release_count == 0, diagnostics
            assert court_claim_count == 1, diagnostics
            assert operation_rows[0]["command"] == "dispute_task", diagnostics
            assert operation_rows[0]["effect_kind"] == "court_claim", diagnostics
            assert operation_rows[0]["effect_state"] == "succeeded", diagnostics
            assert receipt_rows == [], diagnostics
            assert claim_rows == [
                {
                    "claim_id": claim_id,
                    "task_id": task_id,
                    "claimant_id": poster_id,
                    "respondent_id": worker_id,
                    "reason": _DISPUTE_REASON,
                }
            ], diagnostics
            assert escrow_rows == [{"status": "locked", "resolved_at": None}], diagnostics


async def test_competing_acceptances_have_one_serial_winner(
    client: httpx.AsyncClient,
    gateway_client: httpx.AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    worker_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
    _gateway: _GatewayEndpoint,
    tmp_path: Path,
) -> None:
    with ExitStack() as cleanup:
        task_id, poster_id, first_bid, second_bid = await _create_open_task_with_two_bids(
            client,
            gateway_client,
            poster_keypair[0],
            worker_keypair[0],
        )
        barrier = Barrier(2)
        first_runtime = _make_runtime(
            gateway=_gateway,
            gateway_db_path=gateway_db_path,
            task_id=task_id,
            barrier=barrier,
            expected_status="open",
            asset_path=tmp_path / "first-accept-assets",
            cleanup=cleanup,
        )
        second_runtime = _make_runtime(
            gateway=_gateway,
            gateway_db_path=gateway_db_path,
            task_id=task_id,
            barrier=barrier,
            expected_status="open",
            asset_path=tmp_path / "second-accept-assets",
            cleanup=cleanup,
        )
        bid_by_operation = {"accept-first": first_bid, "accept-second": second_bid}
        operation_id_by_operation = {
            "accept-first": f"op-{uuid.uuid4()}",
            "accept-second": f"op-{uuid.uuid4()}",
        }
        token_by_operation = {
            operation: make_jws_token(
                poster_keypair[0],
                poster_id,
                {
                    "action": "accept_bid",
                    "task_id": task_id,
                    "bid_id": bid_id,
                    "poster_id": poster_id,
                    "operation_id": operation_id_by_operation[operation],
                    "expected_lifecycle_version": 0,
                },
            )
            for operation, (bid_id, _worker_id) in bid_by_operation.items()
        }

        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(
                _invoke,
                "accept-first",
                lambda: first_runtime.manager.accept_bid(
                    task_id,
                    first_bid[0],
                    token_by_operation["accept-first"],
                ),
            )
            second_future = executor.submit(
                _invoke,
                "accept-second",
                lambda: second_runtime.manager.accept_bid(
                    task_id,
                    second_bid[0],
                    token_by_operation["accept-second"],
                ),
            )
            try:
                outcomes = [first_future.result(timeout=15), second_future.result(timeout=15)]
            except Exception as exc:
                raise AssertionError("MP-07 contender thread failed or timed out") from exc

        task_rows = _read_rows(
            gateway_db_path,
            "SELECT status, worker_id, accepted_bid_id, accepted_at, cancelled_at "
            "FROM board_tasks WHERE task_id = ?",
            (task_id,),
        )
        event_rows = _read_rows(
            gateway_db_path,
            "SELECT event_id, event_type FROM events WHERE task_id = ? "
            "AND event_type = 'task.accepted' ORDER BY event_id",
            (task_id,),
        )
        operation_rows = _read_rows(
            gateway_db_path,
            "SELECT operation_id,command,operation_state,effect_kind,effect_state,event_id "
            "FROM board_task_operations WHERE operation_id IN (?,?) ORDER BY operation_id",
            tuple(operation_id_by_operation.values()),
        )
        effect_rows = _read_rows(
            gateway_db_path,
            "SELECT operation_id FROM bank_operation_receipts WHERE operation_id IN (?,?)",
            tuple(operation_id_by_operation.values()),
        )
        claim_rows = _read_rows(
            gateway_db_path,
            "SELECT claim_id FROM court_claims WHERE task_id=?",
            (task_id,),
        )
        successes = [outcome for outcome in outcomes if outcome.error is None]
        conflicts = [outcome for outcome in outcomes if outcome.error is not None]
        bank_release_count = sum(
            runtime.boundaries.bank.escrow_release.await_count
            for runtime in (first_runtime, second_runtime)
        )
        court_claim_count = sum(
            runtime.boundaries.court.file_claim.await_count
            for runtime in (first_runtime, second_runtime)
        )
        diagnostics = {
            "outcomes": [
                {
                    "operation": outcome.operation,
                    "result_status": outcome.result.get("status") if outcome.result else None,
                    "error": outcome.error.error if outcome.error else None,
                    "status_code": outcome.error.status_code if outcome.error else None,
                }
                for outcome in outcomes
            ],
            "task_rows": task_rows,
            "event_rows": event_rows,
            "bank_release_count": bank_release_count,
            "court_claim_count": court_claim_count,
            "operation_rows": operation_rows,
            "effect_rows": effect_rows,
            "claim_rows": claim_rows,
        }

        unexpected_errors = [
            outcome
            for outcome in outcomes
            if outcome.error is not None and outcome.error.status_code != 409
        ]
        assert not unexpected_errors, {
            "message": "Unexpected non-conflict ServiceError; this is not the intended T-122 red",
            **diagnostics,
        }
        assert len(successes) == 1, diagnostics
        assert len(conflicts) == 1, diagnostics
        assert conflicts[0].error is not None
        assert conflicts[0].error.status_code == 409, diagnostics
        assert conflicts[0].error.error in {
            "transition_pending",
            "task_version_conflict",
        }, diagnostics
        assert len(task_rows) == 1, diagnostics

        winner = successes[0]
        winning_bid_id, winning_worker_id = bid_by_operation[winner.operation]
        assert winner.result is not None, diagnostics
        assert winner.result["status"] == "accepted", diagnostics
        assert winner.result["accepted_bid_id"] == winning_bid_id, diagnostics
        assert winner.result["worker_id"] == winning_worker_id, diagnostics
        assert winner.result["lifecycle_version"] == 1, diagnostics
        assert len(operation_rows) == 1, diagnostics
        assert operation_rows[0]["operation_id"] == operation_id_by_operation[winner.operation]
        assert operation_rows[0]["command"] == "accept_bid", diagnostics
        assert operation_rows[0]["operation_state"] == "completed", diagnostics
        assert operation_rows[0]["effect_kind"] == "none", diagnostics
        assert operation_rows[0]["effect_state"] == "none", diagnostics
        assert operation_rows[0]["event_id"] == event_rows[0]["event_id"], diagnostics
        task_row = task_rows[0]
        assert task_row["status"] == "accepted", diagnostics
        assert task_row["accepted_bid_id"] == winning_bid_id, diagnostics
        assert task_row["worker_id"] == winning_worker_id, diagnostics
        assert task_row["accepted_at"] is not None, diagnostics
        assert task_row["cancelled_at"] is None, diagnostics
        assert [row["event_type"] for row in event_rows] == ["task.accepted"], diagnostics
        assert bank_release_count == 0, diagnostics
        assert court_claim_count == 0, diagnostics
        assert effect_rows == [], diagnostics
        assert claim_rows == [], diagnostics


async def test_concurrent_acceptance_and_poster_cancel_have_one_serial_winner(
    client: httpx.AsyncClient,
    gateway_client: httpx.AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    worker_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
    _gateway: _GatewayEndpoint,
    tmp_path: Path,
) -> None:
    with ExitStack() as cleanup:
        task_id, poster_id, accepted_bid, _unused_bid = await _create_open_task_with_two_bids(
            client,
            gateway_client,
            poster_keypair[0],
            worker_keypair[0],
        )
        task_setup = _read_rows(
            gateway_db_path,
            "SELECT escrow_id FROM board_tasks WHERE task_id=?",
            (task_id,),
        )
        assert len(task_setup) == 1
        escrow_id = str(task_setup[0]["escrow_id"])
        accept_operation_id = f"op-{uuid.uuid4()}"
        cancel_operation_id = f"op-{uuid.uuid4()}"
        barrier = Barrier(2)
        accept_runtime = _make_runtime(
            gateway=_gateway,
            gateway_db_path=gateway_db_path,
            task_id=task_id,
            barrier=barrier,
            expected_status="open",
            asset_path=tmp_path / "accept-assets",
            cleanup=cleanup,
        )
        cancel_runtime = _make_runtime(
            gateway=_gateway,
            gateway_db_path=gateway_db_path,
            task_id=task_id,
            barrier=barrier,
            expected_status="open",
            asset_path=tmp_path / "cancel-assets",
            cleanup=cleanup,
        )
        bank_gateway = _gateway_write_client(
            _gateway,
            gateway_db_path,
            kid="gateway-central-bank",
            action="gateway.bank.write",
            cleanup=cleanup,
        )
        _install_persisting_release(
            cancel_runtime,
            bank_gateway,
            gateway_db_path=gateway_db_path,
            task_id=task_id,
            operation_id=cancel_operation_id,
            command="cancel_task",
            source_status="open",
            source_version=0,
            escrow_id=escrow_id,
            recipient_id=poster_id,
            source_reference=f"task:{task_id}:cancelled",
        )
        accept_token = make_jws_token(
            poster_keypair[0],
            poster_id,
            {
                "action": "accept_bid",
                "task_id": task_id,
                "bid_id": accepted_bid[0],
                "poster_id": poster_id,
                "operation_id": accept_operation_id,
                "expected_lifecycle_version": 0,
            },
        )
        cancel_token = make_jws_token(
            poster_keypair[0],
            poster_id,
            {
                "action": "cancel_task",
                "task_id": task_id,
                "poster_id": poster_id,
                "operation_id": cancel_operation_id,
                "expected_lifecycle_version": 0,
            },
        )

        with ThreadPoolExecutor(max_workers=2) as executor:
            accept_future = executor.submit(
                _invoke,
                "accept",
                lambda: accept_runtime.manager.accept_bid(
                    task_id,
                    accepted_bid[0],
                    accept_token,
                ),
            )
            cancel_future = executor.submit(
                _invoke,
                "cancel",
                lambda: cancel_runtime.manager.cancel_task(task_id, cancel_token),
            )
            try:
                outcomes = [accept_future.result(timeout=15), cancel_future.result(timeout=15)]
            except Exception as exc:
                raise AssertionError("MP-07 contender thread failed or timed out") from exc

        task_rows = _read_rows(
            gateway_db_path,
            "SELECT status, worker_id, accepted_bid_id, accepted_at, cancelled_at "
            "FROM board_tasks WHERE task_id = ?",
            (task_id,),
        )
        event_rows = _read_rows(
            gateway_db_path,
            "SELECT event_id, event_type FROM events WHERE task_id = ? "
            "AND event_type IN ('task.accepted', 'task.cancelled') ORDER BY event_id",
            (task_id,),
        )
        operation_rows = _read_rows(
            gateway_db_path,
            "SELECT operation_id,command,operation_state,effect_kind,effect_state,event_id "
            "FROM board_task_operations WHERE operation_id IN (?,?) ORDER BY operation_id",
            (accept_operation_id, cancel_operation_id),
        )
        receipt_rows = _read_rows(
            gateway_db_path,
            "SELECT r.operation_id,r.operation_kind,r.source_reference,l.account_id,l.amount "
            "FROM bank_operation_receipts AS r JOIN bank_operation_legs AS l "
            "ON l.operation_id=r.operation_id WHERE r.operation_id IN (?,?) "
            "ORDER BY r.operation_id,l.leg_index",
            (accept_operation_id, cancel_operation_id),
        )
        claim_rows = _read_rows(
            gateway_db_path,
            "SELECT claim_id FROM court_claims WHERE task_id=?",
            (task_id,),
        )
        escrow_rows = _read_rows(
            gateway_db_path,
            "SELECT status,resolved_at FROM bank_escrow WHERE escrow_id=?",
            (escrow_id,),
        )
        successes = [outcome for outcome in outcomes if outcome.error is None]
        conflicts = [outcome for outcome in outcomes if outcome.error is not None]
        accept_bank_release_count = accept_runtime.boundaries.bank.escrow_release.await_count
        cancel_bank_release_count = cancel_runtime.boundaries.bank.escrow_release.await_count
        court_claim_count = sum(
            runtime.boundaries.court.file_claim.await_count
            for runtime in (accept_runtime, cancel_runtime)
        )
        diagnostics = {
            "outcomes": [
                {
                    "operation": outcome.operation,
                    "result_status": outcome.result.get("status") if outcome.result else None,
                    "error": outcome.error.error if outcome.error else None,
                    "status_code": outcome.error.status_code if outcome.error else None,
                }
                for outcome in outcomes
            ],
            "task_rows": task_rows,
            "event_rows": event_rows,
            "accept_bank_release_count": accept_bank_release_count,
            "cancel_bank_release_count": cancel_bank_release_count,
            "court_claim_count": court_claim_count,
            "operation_rows": operation_rows,
            "receipt_rows": receipt_rows,
            "claim_rows": claim_rows,
            "escrow_rows": escrow_rows,
        }

        unexpected_errors = [
            outcome
            for outcome in outcomes
            if outcome.error is not None and outcome.error.status_code != 409
        ]
        assert not unexpected_errors, {
            "message": "Unexpected non-conflict ServiceError; this is not the intended T-122 red",
            **diagnostics,
        }
        assert len(successes) == 1, diagnostics
        assert len(conflicts) == 1, diagnostics
        assert conflicts[0].error is not None
        assert conflicts[0].error.status_code == 409, diagnostics
        assert conflicts[0].error.error in {
            "transition_pending",
            "task_version_conflict",
        }, diagnostics
        assert len(task_rows) == 1, diagnostics

        winner = successes[0].operation
        expected_status = "accepted" if winner == "accept" else "cancelled"
        expected_event = "task.accepted" if winner == "accept" else "task.cancelled"
        assert successes[0].result is not None, diagnostics
        assert successes[0].result["status"] == expected_status, diagnostics
        assert successes[0].result["lifecycle_version"] == 1, diagnostics
        assert len(operation_rows) == 1, diagnostics
        assert operation_rows[0]["operation_id"] == (
            accept_operation_id if winner == "accept" else cancel_operation_id
        ), diagnostics
        assert operation_rows[0]["operation_state"] == "completed", diagnostics
        assert operation_rows[0]["event_id"] == event_rows[0]["event_id"], diagnostics
        task_row = task_rows[0]
        assert task_row["status"] == expected_status, diagnostics
        assert [row["event_type"] for row in event_rows] == [expected_event], diagnostics
        assert accept_bank_release_count == 0, diagnostics
        assert court_claim_count == 0, diagnostics

        if winner == "accept":
            assert task_row["worker_id"] == accepted_bid[1], diagnostics
            assert task_row["accepted_bid_id"] == accepted_bid[0], diagnostics
            assert task_row["accepted_at"] is not None, diagnostics
            assert task_row["cancelled_at"] is None, diagnostics
            assert cancel_bank_release_count == 0, diagnostics
            assert operation_rows[0]["command"] == "accept_bid", diagnostics
            assert operation_rows[0]["effect_kind"] == "none", diagnostics
            assert operation_rows[0]["effect_state"] == "none", diagnostics
            assert receipt_rows == [], diagnostics
            assert escrow_rows == [{"status": "locked", "resolved_at": None}], diagnostics
        else:
            assert task_row["worker_id"] is None, diagnostics
            assert task_row["accepted_bid_id"] is None, diagnostics
            assert task_row["accepted_at"] is None, diagnostics
            assert task_row["cancelled_at"] is not None, diagnostics
            assert cancel_bank_release_count == 1, diagnostics
            assert operation_rows[0]["command"] == "cancel_task", diagnostics
            assert operation_rows[0]["effect_kind"] == "bank_release", diagnostics
            assert operation_rows[0]["effect_state"] == "succeeded", diagnostics
            assert receipt_rows == [
                {
                    "operation_id": cancel_operation_id,
                    "operation_kind": "escrow_release",
                    "source_reference": f"task:{task_id}:cancelled",
                    "account_id": poster_id,
                    "amount": 100,
                }
            ], diagnostics
            assert escrow_rows[0]["status"] == "released", diagnostics
            assert escrow_rows[0]["resolved_at"] is not None, diagnostics
        assert claim_rows == [], diagnostics
