"""MP-07 Court acknowledgment decoding cannot override durable claim evidence."""

from __future__ import annotations

import importlib
import json
import uuid
from typing import TYPE_CHECKING, Any, Protocol, cast
from unittest.mock import AsyncMock

import pytest

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


_court_helpers = importlib.import_module("tests.integration.test_mp07_native_court_handoff")
_effect_helpers = importlib.import_module("tests.integration.test_mp07_native_effect_recovery")
_create_submitted_task = cast(
    "Callable[..., Awaitable[tuple[_TaskSetup, _BidSetup, dict[str, Any]]]]",
    _effect_helpers.__dict__["_create_submitted_task"],
)
_persist_claim = cast(
    "Callable[..., Awaitable[dict[str, Any]]]",
    _court_helpers.__dict__["_persist_claim"],
)
_claim_before_court = cast(
    "Callable[..., None]",
    _court_helpers.__dict__["_claim_before_court"],
)
_install_court_call = cast(
    "Callable[[AsyncMock], None]",
    _court_helpers.__dict__["_install_court_call"],
)
_dispute = cast(
    "Callable[..., Awaitable[Response]]",
    _court_helpers.__dict__["_dispute"],
)
_rows = cast(
    "Callable[..., tuple[tuple[object, ...], ...]]",
    _effect_helpers.__dict__["_rows"],
)
_lifecycle_events = cast(
    "Callable[[str, str, str], tuple[tuple[object, ...], ...]]",
    _effect_helpers.__dict__["_lifecycle_events"],
)


def _decode_failure() -> json.JSONDecodeError:
    return json.JSONDecodeError("Injected malformed Court acknowledgment", "not-json", 0)


async def test_committed_claim_completes_when_court_ack_json_is_malformed(
    client: AsyncClient,
    gateway_client: AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    worker_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
) -> None:
    setup, _bid, _accepted = await _create_submitted_task(
        client,
        gateway_client,
        poster_keypair[0],
        worker_keypair[0],
    )
    operation_id = f"op-{uuid.uuid4()}"
    claim_id = f"clm-{uuid.uuid4()}"

    async def _persist_then_fail_decode(**_kwargs: object) -> None:
        await _persist_claim(
            gateway_client,
            setup=setup,
            db_path=gateway_db_path,
            operation_id=operation_id,
            claim_id=claim_id,
        )
        raise _decode_failure()

    file_claim = AsyncMock(side_effect=_persist_then_fail_decode)
    _install_court_call(file_claim)
    response = await _dispute(client, poster_keypair[0], setup, operation_id)
    result = response.json()

    assert response.status_code == 200, response.text
    assert result["status"] == "disputed"
    assert result["lifecycle_version"] == 3
    assert result["dispute_id"] == claim_id
    assert result["operation"]["operation_id"] == operation_id
    assert result["operation"]["operation_state"] == "completed"
    assert result["operation"]["effect_result_reference"] == claim_id
    assert file_claim.await_count == 1
    assert _rows(
        gateway_db_path,
        "SELECT claim_id, task_id FROM court_claims WHERE task_id=?",
        (setup.task_id,),
    ) == ((claim_id, setup.task_id),)
    assert len(_lifecycle_events(gateway_db_path, setup.task_id, "task.disputed")) == 1


async def test_malformed_ack_without_claim_returns_typed_failure_and_keeps_claim(
    client: AsyncClient,
    gateway_client: AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    worker_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
) -> None:
    setup, _bid, _accepted = await _create_submitted_task(
        client,
        gateway_client,
        poster_keypair[0],
        worker_keypair[0],
    )
    operation_id = f"op-{uuid.uuid4()}"

    async def _fail_decode_without_claim(**_kwargs: object) -> None:
        _claim_before_court(
            gateway_db_path,
            task_id=setup.task_id,
            operation_id=operation_id,
        )
        raise _decode_failure()

    file_claim = AsyncMock(side_effect=_fail_decode_without_claim)
    _install_court_call(file_claim)
    response = await _dispute(client, poster_keypair[0], setup, operation_id)

    assert response.status_code == 502, response.text
    assert response.json() == {
        "error": "court_unavailable",
        "message": "Court claim delivery failed",
        "details": {},
    }
    assert file_claim.await_count == 1
    assert _rows(
        gateway_db_path,
        "SELECT o.operation_state, o.effect_kind, o.effect_state, o.event_id, o.result_json, "
        "t.status, c.lifecycle_version FROM board_task_operations AS o "
        "JOIN board_tasks AS t ON t.task_id=o.task_id "
        "JOIN board_task_contracts AS c ON c.task_id=t.task_id WHERE o.operation_id=?",
        (operation_id,),
    ) == (("claimed", "court_claim", "pending", None, None, "submitted", 2),)
    assert (
        _rows(
            gateway_db_path,
            "SELECT claim_id FROM court_claims WHERE task_id=?",
            (setup.task_id,),
        )
        == ()
    )
    assert _lifecycle_events(gateway_db_path, setup.task_id, "task.disputed") == ()
