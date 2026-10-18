"""Live MP-07 public ruling handoff and financial-receipt proofs."""

from __future__ import annotations

import importlib
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast
from unittest.mock import AsyncMock

import pytest
from service_auth.signing import load_public_key, public_key_to_b64

from task_board_service.core.state import get_app_state
from tests.integration.conftest import PLATFORM_AGENT_ID

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from httpx import AsyncClient, Response

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_SUMMARY = "Court resolved the frozen claim evidence."


class _TaskSetup(Protocol):
    task_id: str
    poster_id: str
    worker_id: str


class _BidSetup(Protocol):
    amount: int


_court_helpers = importlib.import_module("tests.integration.test_mp07_native_court_handoff")
_effect_helpers = importlib.import_module("tests.integration.test_mp07_native_effect_recovery")
_lifecycle_helpers = importlib.import_module("tests.integration.test_mp07_native_public_lifecycle")
_replay_helpers = importlib.import_module("tests.integration.test_mp07_public_command_replay")
_create_submitted_task = cast(
    "Callable[..., Awaitable[tuple[_TaskSetup, _BidSetup, dict[str, Any]]]]",
    _effect_helpers.__dict__["_create_submitted_task"],
)
_persist_claim = cast(
    "Callable[..., Awaitable[dict[str, Any]]]",
    _court_helpers.__dict__["_persist_claim"],
)
_install_court_call = cast(
    "Callable[[AsyncMock], None]",
    _court_helpers.__dict__["_install_court_call"],
)
_dispute = cast(
    "Callable[..., Awaitable[Response]]",
    _court_helpers.__dict__["_dispute"],
)
_install_persisting_release = cast(
    "Callable[..., AsyncMock]",
    _lifecycle_helpers.__dict__["_install_persisting_release"],
)
_rows = cast(
    "Callable[..., tuple[tuple[object, ...], ...]]",
    _effect_helpers.__dict__["_rows"],
)
_operation_rows = cast(
    "Callable[[str, str], tuple[tuple[object, ...], ...]]",
    _effect_helpers.__dict__["_operation_rows"],
)
_lifecycle_events = cast(
    "Callable[[str, str, str], tuple[tuple[object, ...], ...]]",
    _effect_helpers.__dict__["_lifecycle_events"],
)
_post_signed = cast(
    "Callable[..., Awaitable[Response]]",
    _replay_helpers.__dict__["_post_signed"],
)


async def _create_disputed_task(
    client: AsyncClient,
    gateway_client: AsyncClient,
    poster_key: Ed25519PrivateKey,
    worker_key: Ed25519PrivateKey,
    db_path: str,
) -> tuple[_TaskSetup, _BidSetup, dict[str, Any], str]:
    setup, bid, accepted = await _create_submitted_task(
        client,
        gateway_client,
        poster_key,
        worker_key,
    )
    operation_id = f"op-{uuid.uuid4()}"
    claim_id = f"clm-{uuid.uuid4()}"

    async def _file_claim(**_kwargs: object) -> dict[str, Any]:
        return await _persist_claim(
            gateway_client,
            setup=setup,
            db_path=db_path,
            operation_id=operation_id,
            claim_id=claim_id,
        )

    file_claim = AsyncMock(side_effect=_file_claim)
    _install_court_call(file_claim)
    disputed_response = await _dispute(client, poster_key, setup, operation_id)
    assert disputed_response.status_code == 200, disputed_response.text
    disputed = disputed_response.json()
    assert disputed["status"] == "disputed"
    assert disputed["lifecycle_version"] == 3
    assert disputed["dispute_id"] == claim_id
    assert file_claim.await_count == 1
    return setup, bid, accepted, claim_id


async def _register_configured_platform(
    gateway_client: AsyncClient,
    *,
    db_path: str,
) -> None:
    public_path = Path(db_path).parent / "agent-keys" / "platform.pub"
    formatted_public_key = f"ed25519:{public_key_to_b64(load_public_key(public_path))}"
    response = await gateway_client.post(
        "/identity/agents",
        json={
            "agent_id": PLATFORM_AGENT_ID,
            "name": "Configured economic platform",
            "public_key": formatted_public_key,
            "registered_at": "2026-10-17T09:00:30Z",
            "event": {
                "event_source": "identity",
                "event_type": "agent.registered",
                "timestamp": "2026-10-17T09:00:30Z",
                "agent_id": PLATFORM_AGENT_ID,
                "summary": "Registered configured economic platform",
                "payload": "{}",
            },
        },
    )
    assert response.status_code == 201, response.text
    assert _rows(
        db_path,
        "SELECT agent_id, public_key FROM identity_agents WHERE agent_id=?",
        (PLATFORM_AGENT_ID,),
    ) == ((PLATFORM_AGENT_ID, formatted_public_key),)


def _ruling_payload(
    setup: _TaskSetup,
    *,
    operation_id: str,
    ruling_id: str,
    worker_pct: int,
    summary: str = _SUMMARY,
) -> dict[str, Any]:
    return {
        "action": "record_ruling",
        "operation_id": operation_id,
        "expected_lifecycle_version": 3,
        "task_id": setup.task_id,
        "ruling_id": ruling_id,
        "worker_pct": worker_pct,
        "ruling_summary": summary,
    }


async def _record_ruling(
    client: AsyncClient,
    platform_key: Ed25519PrivateKey,
    setup: _TaskSetup,
    payload: dict[str, Any],
) -> Response:
    return await _post_signed(
        client,
        path=f"/tasks/{setup.task_id}/ruling",
        private_key=platform_key,
        signer_id=PLATFORM_AGENT_ID,
        payload=payload,
    )


def _claim_before_money(
    db_path: str,
    *,
    task_id: str,
    operation_id: str,
    effect_kind: str,
) -> None:
    assert _rows(
        db_path,
        "SELECT o.command, o.actor_kind, o.actor_id, o.source_status, "
        "o.source_lifecycle_version, o.operation_state, o.effect_kind, o.effect_state, "
        "o.effect_operation_id, o.event_id, o.result_json, t.status, c.lifecycle_version "
        "FROM board_task_operations AS o JOIN board_tasks AS t ON t.task_id=o.task_id "
        "JOIN board_task_contracts AS c ON c.task_id=t.task_id "
        "WHERE o.operation_id=? AND o.task_id=?",
        (operation_id, task_id),
    ) == (
        (
            "record_ruling",
            "platform",
            PLATFORM_AGENT_ID,
            "disputed",
            3,
            "claimed",
            effect_kind,
            "pending",
            operation_id,
            None,
            None,
            "disputed",
            3,
        ),
    )
    assert (
        _rows(
            db_path,
            "SELECT ruling_id FROM court_rulings WHERE task_id=?",
            (task_id,),
        )
        == ()
    )


def _install_persisting_split(
    gateway_client: AsyncClient,
    *,
    db_path: str,
    setup: _TaskSetup,
    operation_id: str,
    escrow_id: str,
    claim_id: str,
) -> AsyncMock:
    source_reference = f"task:{setup.task_id}:ruling:{claim_id}"

    async def _split(*args: object, **kwargs: object) -> dict[str, Any]:
        assert args == ()
        assert kwargs == {
            "escrow_id": escrow_id,
            "worker_account_id": setup.worker_id,
            "poster_account_id": setup.poster_id,
            "worker_pct": 50,
            "operation_id": operation_id,
            "source_reference": source_reference,
        }
        _claim_before_money(
            db_path,
            task_id=setup.task_id,
            operation_id=operation_id,
            effect_kind="bank_split",
        )
        response = await gateway_client.post(
            "/bank/escrow/split",
            json={
                "operation_id": operation_id,
                "escrow_id": escrow_id,
                "worker_account_id": setup.worker_id,
                "poster_account_id": setup.poster_id,
                "worker_pct": 50,
                "source_reference": source_reference,
                "worker_tx_id": f"tx-{uuid.uuid4()}",
                "poster_tx_id": f"tx-{uuid.uuid4()}",
                "resolved_at": "2026-10-17T09:00:40Z",
                "event": {
                    "event_source": "central-bank",
                    "event_type": "escrow.split",
                    "timestamp": "2026-10-17T09:00:40Z",
                    "task_id": setup.task_id,
                    "summary": f"Split escrow for {setup.task_id}",
                    "payload": "{}",
                },
            },
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["outcome"] == "native_receipt"
        return result

    split = AsyncMock(side_effect=_split)
    state = get_app_state()
    assert state.central_bank_client is not None
    state.central_bank_client.escrow_split = split
    return split


def _install_ruling_money(
    gateway_client: AsyncClient,
    *,
    db_path: str,
    setup: _TaskSetup,
    operation_id: str,
    escrow_id: str,
    claim_id: str,
    worker_pct: int,
) -> AsyncMock:
    if worker_pct == 50:
        return _install_persisting_split(
            gateway_client,
            db_path=db_path,
            setup=setup,
            operation_id=operation_id,
            escrow_id=escrow_id,
            claim_id=claim_id,
        )
    recipient = setup.poster_id if worker_pct == 0 else setup.worker_id
    release = _install_persisting_release(
        gateway_client,
        db_path=db_path,
        task_id=setup.task_id,
        command="record_ruling",
        operation_id=operation_id,
        source_status="disputed",
        source_version=3,
        escrow_id=escrow_id,
        recipient_id=recipient,
        source_reference=f"task:{setup.task_id}:ruling:{claim_id}",
    )
    return release


def _expected_legs(
    setup: _TaskSetup,
    operation_id: str,
    worker_pct: int,
) -> tuple[tuple[object, ...], ...]:
    if worker_pct == 0:
        return ((operation_id, "recipient", setup.poster_id, 100),)
    if worker_pct == 100:
        return ((operation_id, "recipient", setup.worker_id, 100),)
    return (
        (operation_id, "poster", setup.poster_id, 50),
        (operation_id, "worker", setup.worker_id, 50),
    )


@pytest.mark.parametrize(
    ("worker_pct", "operation_kind", "escrow_status"),
    (
        (0, "escrow_release", "released"),
        (50, "escrow_split", "split"),
        (100, "escrow_release", "released"),
    ),
)
async def test_public_ruling_uses_native_claim_and_actual_receipt(
    worker_pct: int,
    operation_kind: str,
    escrow_status: str,
    client: AsyncClient,
    gateway_client: AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    worker_keypair: tuple[Ed25519PrivateKey, str],
    platform_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
) -> None:
    setup, bid, accepted, claim_id = await _create_disputed_task(
        client,
        gateway_client,
        poster_keypair[0],
        worker_keypair[0],
        gateway_db_path,
    )
    await _register_configured_platform(gateway_client, db_path=gateway_db_path)
    operation_id = f"op-{uuid.uuid4()}"
    money = _install_ruling_money(
        gateway_client,
        db_path=gateway_db_path,
        setup=setup,
        operation_id=operation_id,
        escrow_id=str(accepted["escrow_id"]),
        claim_id=claim_id,
        worker_pct=worker_pct,
    )
    payload = _ruling_payload(
        setup,
        operation_id=operation_id,
        ruling_id=claim_id,
        worker_pct=worker_pct,
    )
    response = await _record_ruling(client, platform_keypair[0], setup, payload)
    result = response.json()
    operations_before_replay = _operation_rows(gateway_db_path, setup.task_id)
    events_before_replay = _lifecycle_events(gateway_db_path, setup.task_id, "task.ruled")
    replay = await _record_ruling(client, platform_keypair[0], setup, payload)

    assert response.status_code == replay.status_code == 200
    assert replay.json() == result
    assert result["status"] == "ruled"
    assert result["lifecycle_version"] == 4
    assert result["ruling_id"] == claim_id
    assert result["worker_pct"] == worker_pct
    assert result["ruling_summary"] == _SUMMARY
    operation = result["operation"]
    assert operation["operation_id"] == operation_id
    assert operation["operation_state"] == "completed"
    assert operation["effect_state"] == "succeeded"
    assert operation["effect_result_reference"] == operation_id
    assert operation["result"]["reward"] == accepted["reward"] == 100
    assert bid.amount == 90
    assert money.await_count == 1
    assert _operation_rows(gateway_db_path, setup.task_id) == operations_before_replay
    assert _lifecycle_events(gateway_db_path, setup.task_id, "task.ruled") == events_before_replay
    assert len(events_before_replay) == 1
    assert _rows(
        gateway_db_path,
        "SELECT operation_kind, source_reference FROM bank_operation_receipts WHERE operation_id=?",
        (operation_id,),
    ) == ((operation_kind, f"task:{setup.task_id}:ruling:{claim_id}"),)
    assert _rows(
        gateway_db_path,
        "SELECT operation_id, role, account_id, amount FROM bank_operation_legs "
        "WHERE operation_id=? ORDER BY role",
        (operation_id,),
    ) == _expected_legs(setup, operation_id, worker_pct)
    assert _rows(
        gateway_db_path,
        "SELECT status FROM bank_escrow WHERE escrow_id=?",
        (accepted["escrow_id"],),
    ) == ((escrow_status,),)
    assert (
        _rows(
            gateway_db_path,
            "SELECT ruling_id FROM court_rulings WHERE task_id=?",
            (setup.task_id,),
        )
        == ()
    )


async def test_changed_ruling_terms_reuse_conflicts_before_bank(
    client: AsyncClient,
    gateway_client: AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    worker_keypair: tuple[Ed25519PrivateKey, str],
    platform_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
) -> None:
    setup, _bid, accepted, claim_id = await _create_disputed_task(
        client,
        gateway_client,
        poster_keypair[0],
        worker_keypair[0],
        gateway_db_path,
    )
    await _register_configured_platform(gateway_client, db_path=gateway_db_path)
    operation_id = f"op-{uuid.uuid4()}"
    money = _install_ruling_money(
        gateway_client,
        db_path=gateway_db_path,
        setup=setup,
        operation_id=operation_id,
        escrow_id=str(accepted["escrow_id"]),
        claim_id=claim_id,
        worker_pct=50,
    )
    original = _ruling_payload(
        setup,
        operation_id=operation_id,
        ruling_id=claim_id,
        worker_pct=50,
    )
    completed = await _record_ruling(client, platform_keypair[0], setup, original)
    assert completed.status_code == 200, completed.text

    changed_pct = dict(original, worker_pct=40)
    changed_summary = dict(original, ruling_summary="Different summary")
    pct_response = await _record_ruling(client, platform_keypair[0], setup, changed_pct)
    summary_response = await _record_ruling(client, platform_keypair[0], setup, changed_summary)

    assert pct_response.status_code == summary_response.status_code == 409
    assert pct_response.json()["error"] == "operation_id_conflict"
    assert summary_response.json()["error"] == "operation_id_conflict"
    assert money.await_count == 1
    assert len(_lifecycle_events(gateway_db_path, setup.task_id, "task.ruled")) == 1


async def test_foreign_ruling_identity_rejects_before_bank_or_claim(
    client: AsyncClient,
    gateway_client: AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    worker_keypair: tuple[Ed25519PrivateKey, str],
    platform_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
) -> None:
    setup, _bid, _accepted, _claim_id = await _create_disputed_task(
        client,
        gateway_client,
        poster_keypair[0],
        worker_keypair[0],
        gateway_db_path,
    )
    await _register_configured_platform(gateway_client, db_path=gateway_db_path)
    operation_id = f"op-{uuid.uuid4()}"
    state = get_app_state()
    assert state.central_bank_client is not None
    release = AsyncMock(return_value={"status": "released"})
    split = AsyncMock(return_value={"status": "split"})
    state.central_bank_client.escrow_release = release
    state.central_bank_client.escrow_split = split
    response = await _record_ruling(
        client,
        platform_keypair[0],
        setup,
        _ruling_payload(
            setup,
            operation_id=operation_id,
            ruling_id=f"clm-foreign-{uuid.uuid4()}",
            worker_pct=100,
        ),
    )

    assert release.await_count == split.await_count == 0
    assert (
        _rows(
            gateway_db_path,
            "SELECT operation_id FROM board_task_operations WHERE operation_id=?",
            (operation_id,),
        )
        == ()
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"] == "payload_mismatch"
