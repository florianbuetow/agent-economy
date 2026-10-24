"""Live MP-07 worker rebuttal bridge uses Court's atomic Board projection."""

from __future__ import annotations

import importlib
import uuid
from typing import TYPE_CHECKING, Any, Protocol, cast
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from task_board_service.core.state import get_app_state

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from httpx import AsyncClient, Response

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

REBUTTAL = "The delivery follows the task specification."
_COURT_SUBMITTED_AT = "2026-10-17T09:00:25Z"


class _TaskSetup(Protocol):
    task_id: str
    poster_id: str
    worker_id: str


class _BidSetup(Protocol):
    amount: int


_ruling_helpers = importlib.import_module("tests.integration.test_mp07_native_ruling_handoff")
_effect_helpers = importlib.import_module("tests.integration.test_mp07_native_effect_recovery")
_replay_helpers = importlib.import_module("tests.integration.test_mp07_public_command_replay")
create_disputed_task = cast(
    "Callable[..., Awaitable[tuple[_TaskSetup, _BidSetup, dict[str, Any], str]]]",
    _ruling_helpers.__dict__["_create_disputed_task"],
)
_rows = cast(
    "Callable[..., tuple[tuple[object, ...], ...]]",
    _effect_helpers.__dict__["_rows"],
)
_post_signed = cast(
    "Callable[..., Awaitable[Response]]",
    _replay_helpers.__dict__["_post_signed"],
)


async def persist_rebuttal(
    gateway_client: AsyncClient,
    *,
    setup: _TaskSetup,
    claim_id: str,
    rebuttal: str,
) -> dict[str, Any]:
    rebuttal_id = f"reb-{uuid.uuid4()}"
    response = await gateway_client.post(
        "/court/rebuttals",
        json={
            "rebuttal_id": rebuttal_id,
            "claim_id": claim_id,
            "agent_id": setup.worker_id,
            "content": rebuttal,
            "submitted_at": _COURT_SUBMITTED_AT,
            "claim_status_update": "rebuttal",
            "constraints": {"status": "rebuttal_pending"},
            "event": {
                "event_source": "court",
                "event_type": "rebuttal.submitted",
                "timestamp": _COURT_SUBMITTED_AT,
                "task_id": setup.task_id,
                "agent_id": setup.worker_id,
                "summary": f"Submitted rebuttal {rebuttal_id}",
                "payload": "{}",
            },
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["rebuttal_id"] == rebuttal_id
    return {
        "dispute_id": claim_id,
        "status": "rebuttal",
        "rebuttal": rebuttal,
    }


def install_rebuttal_call(call: AsyncMock) -> None:
    state = get_app_state()
    assert state.platform_agent is not None
    state.platform_agent.submit_rebuttal = call


async def submit_public_rebuttal(
    client: AsyncClient,
    worker_key: Ed25519PrivateKey,
    setup: _TaskSetup,
    claim_id: str,
) -> Response:
    return await _post_signed(
        client,
        path=f"/tasks/{setup.task_id}/rebuttal",
        private_key=worker_key,
        signer_id=setup.worker_id,
        payload={
            "action": "submit_rebuttal",
            "task_id": setup.task_id,
            "dispute_id": claim_id,
            "worker_id": setup.worker_id,
            "rebuttal": REBUTTAL,
        },
    )


def get_event_cursor(db_path: str) -> int:
    rows = _rows(db_path, "SELECT COALESCE(MAX(event_id),0) FROM events")
    assert len(rows) == 1
    value = rows[0][0]
    assert isinstance(value, int)
    return value


def assert_committed_projection(
    db_path: str,
    *,
    setup: _TaskSetup,
    claim_id: str,
    event_cursor: int,
    generic_status_calls: int,
) -> None:
    observed = {
        "task": _rows(
            db_path,
            "SELECT t.status,t.dispute_id,t.rebuttal_submitted_at,c.lifecycle_version "
            "FROM board_tasks AS t JOIN board_task_contracts AS c ON c.task_id=t.task_id "
            "WHERE t.task_id=?",
            (setup.task_id,),
        ),
        "rebuttal": _rows(
            db_path,
            "SELECT r.claim_id,r.agent_id,r.content,r.submitted_at,c.status "
            "FROM court_rebuttals AS r JOIN court_claims AS c ON c.claim_id=r.claim_id "
            "WHERE r.claim_id=?",
            (claim_id,),
        ),
        "events": _rows(
            db_path,
            "SELECT event_source,event_type,timestamp,task_id,agent_id FROM events "
            "WHERE task_id=? AND event_id>? ORDER BY event_id",
            (setup.task_id, event_cursor),
        ),
        "generic_status_calls": generic_status_calls,
    }
    assert observed == {
        "task": (("disputed", claim_id, _COURT_SUBMITTED_AT, 3),),
        "rebuttal": ((claim_id, setup.worker_id, REBUTTAL, _COURT_SUBMITTED_AT, "rebuttal"),),
        "events": (
            (
                "court",
                "rebuttal.submitted",
                _COURT_SUBMITTED_AT,
                setup.task_id,
                setup.worker_id,
            ),
        ),
        "generic_status_calls": 0,
    }


async def test_public_rebuttal_uses_court_timestamp_without_generic_board_write(
    client: AsyncClient,
    gateway_client: AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    worker_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
) -> None:
    setup, _bid, _accepted, claim_id = await create_disputed_task(
        client,
        gateway_client,
        poster_keypair[0],
        worker_keypair[0],
        gateway_db_path,
    )
    event_cursor = get_event_cursor(gateway_db_path)

    async def _submit(dispute_id: str, rebuttal: str) -> dict[str, Any]:
        assert dispute_id == claim_id
        assert rebuttal == REBUTTAL
        return await persist_rebuttal(
            gateway_client,
            setup=setup,
            claim_id=claim_id,
            rebuttal=rebuttal,
        )

    court_call = AsyncMock(side_effect=_submit)
    install_rebuttal_call(court_call)
    state = get_app_state()
    assert state.store is not None
    with patch.object(state.store, "update_task", wraps=state.store.update_task) as generic_update:
        response = await submit_public_rebuttal(client, worker_keypair[0], setup, claim_id)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "dispute_id": claim_id,
        "status": "rebuttal",
        "rebuttal": REBUTTAL,
    }
    court_call.assert_awaited_once_with(claim_id, REBUTTAL)
    assert_committed_projection(
        gateway_db_path,
        setup=setup,
        claim_id=claim_id,
        event_cursor=event_cursor,
        generic_status_calls=generic_update.call_count,
    )


async def test_lost_court_ack_keeps_atomic_rebuttal_projection_and_returns_typed_502(
    client: AsyncClient,
    gateway_client: AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    worker_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
) -> None:
    setup, _bid, _accepted, claim_id = await create_disputed_task(
        client,
        gateway_client,
        poster_keypair[0],
        worker_keypair[0],
        gateway_db_path,
    )
    event_cursor = get_event_cursor(gateway_db_path)

    async def _persist_then_lose_ack(dispute_id: str, rebuttal: str) -> None:
        assert dispute_id == claim_id
        await persist_rebuttal(
            gateway_client,
            setup=setup,
            claim_id=claim_id,
            rebuttal=rebuttal,
        )
        raise httpx.ReadError(
            "Injected lost Court acknowledgment",
            request=httpx.Request("POST", f"http://court/disputes/{claim_id}/rebuttal"),
        )

    court_call = AsyncMock(side_effect=_persist_then_lose_ack)
    install_rebuttal_call(court_call)
    state = get_app_state()
    assert state.store is not None
    with patch.object(state.store, "update_task", wraps=state.store.update_task) as generic_update:
        response = await submit_public_rebuttal(client, worker_keypair[0], setup, claim_id)

    assert response.status_code == 502, response.text
    assert response.json() == {
        "error": "court_unavailable",
        "message": "Cannot connect to Court",
        "details": {},
    }
    court_call.assert_awaited_once_with(claim_id, REBUTTAL)
    assert_committed_projection(
        gateway_db_path,
        setup=setup,
        claim_id=claim_id,
        event_cursor=event_cursor,
        generic_status_calls=generic_update.call_count,
    )
