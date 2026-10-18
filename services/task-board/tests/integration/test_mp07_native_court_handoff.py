"""Live MP-07 public dispute handoff and durable Court-evidence proofs."""

from __future__ import annotations

import importlib
import uuid
from typing import TYPE_CHECKING, Any, Protocol, cast
from unittest.mock import AsyncMock

import httpx
import pytest

from task_board_service.core.state import get_app_state
from tests.integration.conftest import register_identity_agent

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from httpx import AsyncClient, Response

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_REASON = "The submitted deliverable does not satisfy the frozen specification."


class _TaskSetup(Protocol):
    task_id: str
    poster_id: str
    worker_id: str


class _BidSetup(Protocol):
    amount: int


_effect_helpers = importlib.import_module("tests.integration.test_mp07_native_effect_recovery")
_replay_helpers = importlib.import_module("tests.integration.test_mp07_public_command_replay")
_create_submitted_task = cast(
    "Callable[..., Awaitable[tuple[_TaskSetup, _BidSetup, dict[str, Any]]]]",
    _effect_helpers.__dict__["_create_submitted_task"],
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


def _dispute_payload(setup: _TaskSetup, operation_id: str) -> dict[str, Any]:
    return {
        "action": "dispute_task",
        "operation_id": operation_id,
        "expected_lifecycle_version": 2,
        "task_id": setup.task_id,
        "poster_id": setup.poster_id,
        "reason": _REASON,
    }


async def _dispute(
    client: AsyncClient,
    poster_key: Ed25519PrivateKey,
    setup: _TaskSetup,
    operation_id: str,
) -> Response:
    return await _post_signed(
        client,
        path=f"/tasks/{setup.task_id}/dispute",
        private_key=poster_key,
        signer_id=setup.poster_id,
        payload=_dispute_payload(setup, operation_id),
    )


def _claim_before_court(
    db_path: str,
    *,
    task_id: str,
    operation_id: str,
) -> None:
    assert _rows(
        db_path,
        "SELECT o.command, o.source_status, o.source_lifecycle_version, o.operation_state, "
        "o.effect_kind, o.effect_state, o.effect_operation_id, o.event_id, o.result_json, "
        "t.status, c.lifecycle_version FROM board_task_operations AS o "
        "JOIN board_tasks AS t ON t.task_id=o.task_id "
        "JOIN board_task_contracts AS c ON c.task_id=t.task_id "
        "WHERE o.operation_id=? AND o.task_id=?",
        (operation_id, task_id),
    ) == (
        (
            "dispute_task",
            "submitted",
            2,
            "claimed",
            "court_claim",
            "pending",
            None,
            None,
            None,
            "submitted",
            2,
        ),
    )


async def _persist_claim(
    gateway_client: AsyncClient,
    *,
    setup: _TaskSetup,
    db_path: str,
    operation_id: str,
    claim_id: str,
    respondent_id: str | None = None,
    reason: str = _REASON,
) -> dict[str, Any]:
    _claim_before_court(
        db_path,
        task_id=setup.task_id,
        operation_id=operation_id,
    )
    filed_at = "2026-10-17T09:00:20Z"
    rebuttal_deadline = "2026-10-17T10:00:20Z"
    response = await gateway_client.post(
        "/court/claims",
        json={
            "claim_id": claim_id,
            "task_id": setup.task_id,
            "claimant_id": setup.poster_id,
            "respondent_id": setup.worker_id if respondent_id is None else respondent_id,
            "reason": reason,
            "status": "rebuttal_pending",
            "rebuttal_deadline": rebuttal_deadline,
            "filed_at": filed_at,
            "event": {
                "event_source": "court",
                "event_type": "claim.filed",
                "timestamp": filed_at,
                "task_id": setup.task_id,
                "agent_id": setup.poster_id,
                "summary": f"Filed claim {claim_id}",
                "payload": "{}",
            },
        },
    )
    assert response.status_code == 201, response.text
    persisted = response.json()
    assert persisted["claim_id"] == claim_id
    return {
        "dispute_id": claim_id,
        "status": "rebuttal_pending",
        "rebuttal_deadline": rebuttal_deadline,
    }


def _install_court_call(file_claim: AsyncMock) -> None:
    state = get_app_state()
    assert state.platform_agent is not None
    state.platform_agent.file_claim = file_claim


def _assert_exact_call(
    args: tuple[object, ...],
    kwargs: dict[str, object],
    *,
    setup: _TaskSetup,
    escrow_id: str,
) -> None:
    assert args == ()
    assert kwargs == {
        "task_id": setup.task_id,
        "claimant_id": setup.poster_id,
        "respondent_id": setup.worker_id,
        "claim": _REASON,
        "escrow_id": escrow_id,
    }


async def test_persisted_claim_finalizes_with_actual_claim_identity_and_deadline(
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
    claim_id = f"clm-{uuid.uuid4()}"

    async def _file(*args: object, **kwargs: object) -> dict[str, Any]:
        _assert_exact_call(args, kwargs, setup=setup, escrow_id=str(accepted["escrow_id"]))
        return await _persist_claim(
            gateway_client,
            setup=setup,
            db_path=gateway_db_path,
            operation_id=operation_id,
            claim_id=claim_id,
        )

    file_claim = AsyncMock(side_effect=_file)
    _install_court_call(file_claim)
    response = await _dispute(client, poster_keypair[0], setup, operation_id)
    result = response.json()

    assert response.status_code == 200, response.text
    assert result["status"] == "disputed"
    assert result["lifecycle_version"] == 3
    assert result["dispute_id"] == claim_id
    assert result["rebuttal_deadline"] == "2026-10-17T10:00:20Z"
    operation = result["operation"]
    assert operation["operation_id"] == operation_id
    assert operation["operation_state"] == "completed"
    assert operation["effect_kind"] == "court_claim"
    assert operation["effect_state"] == "succeeded"
    assert operation["effect_result_reference"] == claim_id
    assert operation["result"]["dispute_id"] == claim_id
    assert operation["result"]["reason"] == _REASON
    assert file_claim.await_count == 1
    assert _rows(
        gateway_db_path,
        "SELECT claim_id, task_id, claimant_id, respondent_id, reason, rebuttal_deadline "
        "FROM court_claims WHERE task_id=?",
        (setup.task_id,),
    ) == (
        (
            claim_id,
            setup.task_id,
            setup.poster_id,
            setup.worker_id,
            _REASON,
            "2026-10-17T10:00:20Z",
        ),
    )
    assert len(_lifecycle_events(gateway_db_path, setup.task_id, "task.disputed")) == 1


async def test_persisted_claim_survives_lost_court_ack_and_exact_replay(
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
    claim_id = f"clm-{uuid.uuid4()}"

    async def _lose_ack(*args: object, **kwargs: object) -> None:
        _assert_exact_call(args, kwargs, setup=setup, escrow_id=str(accepted["escrow_id"]))
        await _persist_claim(
            gateway_client,
            setup=setup,
            db_path=gateway_db_path,
            operation_id=operation_id,
            claim_id=claim_id,
        )
        raise httpx.ReadError(
            "Injected lost Court acknowledgment",
            request=httpx.Request("POST", "http://court/claims"),
        )

    lost_ack = AsyncMock(side_effect=_lose_ack)
    _install_court_call(lost_ack)
    completed_response = await _dispute(client, poster_keypair[0], setup, operation_id)
    completed = completed_response.json()
    operations_before = _operation_rows(gateway_db_path, setup.task_id)
    events_before = _lifecycle_events(gateway_db_path, setup.task_id, "task.disputed")
    replay = await _dispute(client, poster_keypair[0], setup, operation_id)

    assert completed_response.status_code == replay.status_code == 200
    assert replay.json() == completed
    assert completed["status"] == "disputed"
    assert completed["dispute_id"] == claim_id
    assert completed["operation"]["effect_result_reference"] == claim_id
    assert lost_ack.await_count == 1
    assert _operation_rows(gateway_db_path, setup.task_id) == operations_before
    assert _lifecycle_events(gateway_db_path, setup.task_id, "task.disputed") == events_before
    assert len(events_before) == 1


async def test_success_body_without_persisted_claim_stays_pending(
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
    fabricated_claim_id = f"clm-{uuid.uuid4()}"

    async def _success_without_claim(*args: object, **kwargs: object) -> dict[str, str]:
        _assert_exact_call(args, kwargs, setup=setup, escrow_id=str(accepted["escrow_id"]))
        _claim_before_court(
            gateway_db_path,
            task_id=setup.task_id,
            operation_id=operation_id,
        )
        return {
            "dispute_id": fabricated_claim_id,
            "status": "rebuttal_pending",
            "rebuttal_deadline": "2026-10-17T10:00:20Z",
        }

    file_claim = AsyncMock(side_effect=_success_without_claim)
    _install_court_call(file_claim)
    response = await _dispute(client, poster_keypair[0], setup, operation_id)
    result = response.json()

    assert response.status_code == 202, response.text
    assert result["status"] == "submitted"
    assert result["lifecycle_version"] == 2
    assert result["dispute_id"] is None
    assert result["operation"]["operation_id"] == operation_id
    assert result["operation"]["operation_state"] == "claimed"
    assert result["operation"]["effect_state"] == "pending"
    assert result["operation"]["effect_result_reference"] is None
    assert result["operation"]["event_id"] is None
    assert result["operation"]["result"] is None
    assert file_claim.await_count == 1
    assert (
        _rows(
            gateway_db_path,
            "SELECT claim_id FROM court_claims WHERE task_id=?",
            (setup.task_id,),
        )
        == ()
    )
    assert _lifecycle_events(gateway_db_path, setup.task_id, "task.disputed") == ()


async def test_mismatched_persisted_claim_blocks_without_fabricated_success(
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
    foreign_respondent = f"a-foreign-{uuid.uuid4()}"
    await register_identity_agent(gateway_client, foreign_respondent)
    operation_id = f"op-{uuid.uuid4()}"
    claim_id = f"clm-{uuid.uuid4()}"

    async def _file_foreign(*args: object, **kwargs: object) -> dict[str, Any]:
        _assert_exact_call(args, kwargs, setup=setup, escrow_id=str(accepted["escrow_id"]))
        return await _persist_claim(
            gateway_client,
            setup=setup,
            db_path=gateway_db_path,
            operation_id=operation_id,
            claim_id=claim_id,
            respondent_id=foreign_respondent,
        )

    file_claim = AsyncMock(side_effect=_file_foreign)
    _install_court_call(file_claim)
    response = await _dispute(client, poster_keypair[0], setup, operation_id)
    conflict = response.json()
    replay = await _dispute(client, poster_keypair[0], setup, operation_id)

    assert response.status_code == 409, response.text
    assert replay.status_code == 409, replay.text
    assert replay.json() == conflict
    assert conflict["error"] == "effect_evidence_conflict"
    assert conflict["details"] == {"operation_id": operation_id}
    assert file_claim.await_count == 1
    assert _rows(
        gateway_db_path,
        "SELECT operation_state, effect_kind, effect_state, event_id, result_json "
        "FROM board_task_operations WHERE operation_id=?",
        (operation_id,),
    ) == (("blocked", "court_claim", "conflict", None, None),)
    assert _rows(
        gateway_db_path,
        "SELECT t.status, c.lifecycle_version, t.dispute_id FROM board_tasks AS t "
        "JOIN board_task_contracts AS c ON c.task_id=t.task_id WHERE t.task_id=?",
        (setup.task_id,),
    ) == (("submitted", 2, None),)
    assert _lifecycle_events(gateway_db_path, setup.task_id, "task.disputed") == ()
