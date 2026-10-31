"""Live MP-07 Bank-effect recovery and pending-projection proofs."""

from __future__ import annotations

import importlib
import uuid
from typing import TYPE_CHECKING, Any, Protocol, cast
from unittest.mock import AsyncMock

import pytest
from service_commons.exceptions import ServiceError

from task_board_service.core.state import get_app_state

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from httpx import AsyncClient, Response

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class _TaskSetup(Protocol):
    task_id: str
    poster_id: str
    worker_id: str


class _BidSetup(Protocol):
    amount: int


_lifecycle_helpers = importlib.import_module("tests.integration.test_mp07_native_public_lifecycle")
_replay_helpers = importlib.import_module("tests.integration.test_mp07_public_command_replay")
_create_accepted_task = cast(
    "Callable[..., Awaitable[tuple[_TaskSetup, _BidSetup, dict[str, Any]]]]",
    _lifecycle_helpers.__dict__["_create_accepted_task"],
)
_create_open_task = cast(
    "Callable[[AsyncClient, AsyncClient, Ed25519PrivateKey], Awaitable[_TaskSetup]]",
    _replay_helpers.__dict__["_create_open_task"],
)
_insert_owned_asset = cast(
    "Callable[[_TaskSetup], None]",
    _lifecycle_helpers.__dict__["_insert_owned_asset"],
)
_native_submit = cast(
    "Callable[[_TaskSetup, str], dict[str, Any]]",
    _lifecycle_helpers.__dict__["_native_submit"],
)
_create_zero_balance_account = cast(
    "Callable[..., Awaitable[None]]",
    _lifecycle_helpers.__dict__["_create_zero_balance_account"],
)
_install_persisting_release = cast(
    "Callable[..., AsyncMock]",
    _lifecycle_helpers.__dict__["_install_persisting_release"],
)
_rows = cast(
    "Callable[..., tuple[tuple[object, ...], ...]]",
    _lifecycle_helpers.__dict__["_rows"],
)
_operation_rows = cast(
    "Callable[[str, str], tuple[tuple[object, ...], ...]]",
    _lifecycle_helpers.__dict__["_operation_rows"],
)
_lifecycle_events = cast(
    "Callable[[str, str, str], tuple[tuple[object, ...], ...]]",
    _lifecycle_helpers.__dict__["_lifecycle_events"],
)
_post_signed = cast(
    "Callable[..., Awaitable[Response]]",
    _replay_helpers.__dict__["_post_signed"],
)


async def _create_submitted_task(
    client: AsyncClient,
    gateway_client: AsyncClient,
    poster_key: Ed25519PrivateKey,
    worker_key: Ed25519PrivateKey,
) -> tuple[_TaskSetup, _BidSetup, dict[str, Any]]:
    setup, bid, accepted = await _create_accepted_task(
        client,
        gateway_client,
        poster_key,
        worker_key,
    )
    _insert_owned_asset(setup)
    submitted = _native_submit(setup, f"op-{uuid.uuid4()}")
    assert submitted["operation_state"] == "completed"
    assert submitted["result"]["status"] == "submitted"
    await _create_zero_balance_account(gateway_client, account_id=setup.worker_id)
    return setup, bid, accepted


def _approval_payload(setup: _TaskSetup, operation_id: str) -> dict[str, Any]:
    return {
        "action": "approve_task",
        "operation_id": operation_id,
        "expected_lifecycle_version": 2,
        "task_id": setup.task_id,
        "poster_id": setup.poster_id,
    }


async def _approve(
    client: AsyncClient,
    poster_key: Ed25519PrivateKey,
    setup: _TaskSetup,
    operation_id: str,
) -> Response:
    return await _post_signed(
        client,
        path=f"/tasks/{setup.task_id}/approve",
        private_key=poster_key,
        signer_id=setup.poster_id,
        payload=_approval_payload(setup, operation_id),
    )


def _claim_before_bank(
    db_path: str,
    *,
    task_id: str,
    operation_id: str,
) -> tuple[tuple[object, ...], ...]:
    rows = _rows(
        db_path,
        "SELECT o.command, o.source_status, o.source_lifecycle_version, o.operation_state, "
        "o.effect_state, o.event_id, o.result_json, t.status, c.lifecycle_version "
        "FROM board_task_operations AS o JOIN board_tasks AS t ON t.task_id=o.task_id "
        "JOIN board_task_contracts AS c ON c.task_id=t.task_id "
        "WHERE o.operation_id=? AND o.task_id=?",
        (operation_id, task_id),
    )
    assert rows == (
        ("approve_task", "submitted", 2, "claimed", "pending", None, None, "submitted", 2),
    )
    return rows


def _assert_pending_money_state(
    db_path: str,
    *,
    task_id: str,
    operation_id: str,
    worker_id: str,
) -> None:
    assert _claim_before_bank(db_path, task_id=task_id, operation_id=operation_id)
    assert _rows(
        db_path,
        "SELECT status, resolved_at FROM bank_escrow WHERE task_id=?",
        (task_id,),
    ) == (("locked", None),)
    assert _rows(
        db_path,
        "SELECT balance FROM bank_accounts WHERE account_id=?",
        (worker_id,),
    ) == ((0,),)
    assert (
        _rows(
            db_path,
            "SELECT operation_id FROM bank_operation_receipts WHERE operation_id=?",
            (operation_id,),
        )
        == ()
    )
    assert _lifecycle_events(db_path, task_id, "task.approved") == ()


def _all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(*(map(_all_keys, value.values())))
    if isinstance(value, list):
        return set().union(*(map(_all_keys, value)))
    return set()


def _install_bank_release(release: AsyncMock) -> None:
    state = get_app_state()
    assert state.central_bank_client is not None
    state.central_bank_client.escrow_release = release


async def test_missing_receipt_stays_pending_and_exact_retry_completes(
    client: AsyncClient,
    gateway_client: AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    worker_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
) -> None:
    setup, _bid, accepted = await _create_submitted_task(
        client,
        gateway_client,
        poster_keypair[0],
        worker_keypair[0],
    )
    operation_id = f"op-{uuid.uuid4()}"

    async def _success_without_receipt(*args: object, **kwargs: object) -> dict[str, str]:
        assert args == ()
        assert kwargs["operation_id"] == operation_id
        _claim_before_bank(
            gateway_db_path,
            task_id=setup.task_id,
            operation_id=operation_id,
        )
        return {"status": "released"}

    missing_receipt = AsyncMock(side_effect=_success_without_receipt)
    _install_bank_release(missing_receipt)
    pending_response = await _approve(client, poster_keypair[0], setup, operation_id)
    pending = pending_response.json()

    assert pending_response.status_code == 202, pending_response.text
    assert pending["status"] == "submitted"
    assert pending["lifecycle_version"] == 2
    assert pending["operation"]["operation_id"] == operation_id
    assert pending["operation"]["operation_state"] == "claimed"
    assert pending["operation"]["effect_state"] == "pending"
    assert pending["operation"]["event_id"] is None
    assert pending["operation"]["result"] is None
    assert missing_receipt.await_count == 1
    _assert_pending_money_state(
        gateway_db_path,
        task_id=setup.task_id,
        operation_id=operation_id,
        worker_id=setup.worker_id,
    )

    detail_response = await client.get(f"/tasks/{setup.task_id}")
    list_response = await client.get("/tasks", params={"status": "submitted"})
    assert detail_response.status_code == list_response.status_code == 200
    detail = detail_response.json()
    listed = list_response.json()["tasks"]
    assert len(listed) == 1
    expected_projection = {
        "operation_id": operation_id,
        "command": "approve_task",
        "target_status": "approved",
        "target_lifecycle_version": 3,
        "operation_state": "claimed",
        "effect_state": "pending",
    }
    assert detail["pending_transition"] == expected_projection
    assert listed[0]["pending_transition"] == expected_projection
    private_keys = {"effect_intent", "caller_kid", "request_sha256", "canonical_request_json"}
    assert not private_keys.intersection(_all_keys(detail))
    assert not private_keys.intersection(_all_keys(listed[0]))

    persisted_release = _install_persisting_release(
        gateway_client,
        db_path=gateway_db_path,
        task_id=setup.task_id,
        command="approve_task",
        operation_id=operation_id,
        source_status="submitted",
        source_version=2,
        escrow_id=str(accepted["escrow_id"]),
        recipient_id=setup.worker_id,
        source_reference=f"task:{setup.task_id}:approved",
    )
    completed_response = await _approve(client, poster_keypair[0], setup, operation_id)
    completed = completed_response.json()

    assert completed_response.status_code == 200, completed_response.text
    assert completed["status"] == "approved"
    assert completed["lifecycle_version"] == 3
    assert completed["operation"]["operation_id"] == operation_id
    assert completed["operation"]["operation_state"] == "completed"
    assert completed["operation"]["event_id"] is not None
    assert completed["operation"]["result"]["status"] == "approved"
    assert persisted_release.await_count == 1
    assert len(_lifecycle_events(gateway_db_path, setup.task_id, "task.approved")) == 1


async def test_persisted_receipt_survives_lost_bank_acknowledgement(
    client: AsyncClient,
    gateway_client: AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    worker_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
) -> None:
    setup, _bid, accepted = await _create_submitted_task(
        client,
        gateway_client,
        poster_keypair[0],
        worker_keypair[0],
    )
    operation_id = f"op-{uuid.uuid4()}"
    persisted_release = _install_persisting_release(
        gateway_client,
        db_path=gateway_db_path,
        task_id=setup.task_id,
        command="approve_task",
        operation_id=operation_id,
        source_status="submitted",
        source_version=2,
        escrow_id=str(accepted["escrow_id"]),
        recipient_id=setup.worker_id,
        source_reference=f"task:{setup.task_id}:approved",
    )

    async def _lose_ack(*args: object, **kwargs: object) -> None:
        await persisted_release(*args, **kwargs)
        raise ServiceError(
            "central_bank_unavailable",
            "Injected lost Bank acknowledgment",
            502,
            {"phase": "after_commit"},
        )

    lost_ack = AsyncMock(side_effect=_lose_ack)
    _install_bank_release(lost_ack)
    completed_response = await _approve(client, poster_keypair[0], setup, operation_id)
    completed = completed_response.json()
    operations_before_replay = _operation_rows(gateway_db_path, setup.task_id)
    events_before_replay = _lifecycle_events(gateway_db_path, setup.task_id, "task.approved")
    replay = await _approve(client, poster_keypair[0], setup, operation_id)

    assert completed_response.status_code == replay.status_code == 200
    assert replay.json() == completed
    assert completed["status"] == "approved"
    assert completed["operation"]["operation_id"] == operation_id
    assert completed["operation"]["effect_state"] == "succeeded"
    assert lost_ack.await_count == 1
    assert persisted_release.await_count == 1
    assert _operation_rows(gateway_db_path, setup.task_id) == operations_before_replay
    assert (
        _lifecycle_events(gateway_db_path, setup.task_id, "task.approved") == events_before_replay
    )
    assert len(events_before_replay) == 1
    assert _rows(
        gateway_db_path,
        "SELECT balance FROM bank_accounts WHERE account_id=?",
        (setup.worker_id,),
    ) == ((100,),)


async def test_precommit_bank_failure_keeps_claim_for_same_operation_retry(
    client: AsyncClient,
    gateway_client: AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    worker_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
) -> None:
    setup, _bid, accepted = await _create_submitted_task(
        client,
        gateway_client,
        poster_keypair[0],
        worker_keypair[0],
    )
    operation_id = f"op-{uuid.uuid4()}"
    failure = AsyncMock(
        side_effect=ServiceError(
            "central_bank_unavailable",
            "Injected pre-commit Bank failure",
            502,
            {"phase": "before_commit"},
        )
    )
    _install_bank_release(failure)
    failed_response = await _approve(client, poster_keypair[0], setup, operation_id)

    assert failed_response.status_code == 502, failed_response.text
    assert failed_response.json() == {
        "error": "central_bank_unavailable",
        "message": "Injected pre-commit Bank failure",
        "details": {"phase": "before_commit"},
    }
    assert failure.await_count == 1
    _assert_pending_money_state(
        gateway_db_path,
        task_id=setup.task_id,
        operation_id=operation_id,
        worker_id=setup.worker_id,
    )

    persisted_release = _install_persisting_release(
        gateway_client,
        db_path=gateway_db_path,
        task_id=setup.task_id,
        command="approve_task",
        operation_id=operation_id,
        source_status="submitted",
        source_version=2,
        escrow_id=str(accepted["escrow_id"]),
        recipient_id=setup.worker_id,
        source_reference=f"task:{setup.task_id}:approved",
    )
    completed_response = await _approve(client, poster_keypair[0], setup, operation_id)

    assert completed_response.status_code == 200, completed_response.text
    assert completed_response.json()["status"] == "approved"
    assert completed_response.json()["operation"]["operation_id"] == operation_id
    assert persisted_release.await_count == 1
    assert len(_lifecycle_events(gateway_db_path, setup.task_id, "task.approved")) == 1


async def test_open_task_approval_returns_domain_conflict_before_bank_or_claim(
    client: AsyncClient,
    gateway_client: AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
) -> None:
    setup = await _create_open_task(client, gateway_client, poster_keypair[0])
    operation_id = f"op-{uuid.uuid4()}"
    state = get_app_state()
    assert state.central_bank_client is not None
    release = AsyncMock(return_value={"status": "released"})
    state.central_bank_client.escrow_release = release
    response = await _post_signed(
        client,
        path=f"/tasks/{setup.task_id}/approve",
        private_key=poster_keypair[0],
        signer_id=setup.poster_id,
        payload={
            "action": "approve_task",
            "operation_id": operation_id,
            "expected_lifecycle_version": 0,
            "task_id": setup.task_id,
            "poster_id": setup.poster_id,
        },
    )

    assert release.await_count == 0
    assert (
        _rows(
            gateway_db_path,
            "SELECT operation_id FROM board_task_operations WHERE operation_id=?",
            (operation_id,),
        )
        == ()
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"] == "invalid_status"
