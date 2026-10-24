"""Authenticated HTTP boundary for read-only legacy posting diagnostics."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from service_commons.exceptions import ServiceError

from db_gateway_service.core.state import get_app_state
from tests.unit.conftest import gateway_test_signers
from tests.unit.test_mp05_legacy_posting_recovery import (
    _PendingTemplate,
    _persist_conflicting_refund,
    _reconcile,
    legacy_gateway,
    pending_template,
)
from tests.unit.test_mp05_publication_contract import PublicationGateway, _signed_request

if TYPE_CHECKING:
    import sqlite3

    from db_gateway_service.services.db_writer import DbWriter

pytestmark = pytest.mark.unit
__all__ = ["gateway_test_signers", "legacy_gateway", "pending_template"]

_LEGACY_PATH = "/board/legacy-posting-observations"


def _writer_and_snapshot() -> tuple[DbWriter, tuple[tuple[str, ...], int]]:
    writer = get_app_state().db_writer
    assert writer is not None
    connection = writer.connection
    return writer, (tuple(connection.iterdump()), connection.total_changes)


def _assert_unchanged(connection: sqlite3.Connection, before: tuple[tuple[str, ...], int]) -> None:
    assert (tuple(connection.iterdump()), connection.total_changes) == before


@pytest.mark.parametrize("state", ["refund_pending", "refunded", "blocked_integrity"])
def test_owner_get_returns_200_exact_envelope_without_effects(
    legacy_gateway: tuple[PublicationGateway, _PendingTemplate],
    state: str,
) -> None:
    gateway, template = legacy_gateway
    observation_id = (
        template.refunded_observation_id
        if state == "refunded"
        else template.pending_observation_ids[0]
    )
    writer, _ = _writer_and_snapshot()
    if state == "blocked_integrity":
        _persist_conflicting_refund(writer, observation_id, "event")
        with pytest.raises(ServiceError) as blocked:
            _reconcile(writer, observation_id, template.caller_kid)
        expected = blocked.value.details["observation"]
    else:
        expected = _reconcile(writer, observation_id, template.caller_kid)
    assert expected["observation_state"] == state
    connection = writer.connection
    before = (tuple(connection.iterdump()), connection.total_changes)

    response = _signed_request(
        gateway,
        "board",
        "GET",
        f"{_LEGACY_PATH}/{observation_id}",
        action="gateway.board.write",
    )

    assert response.status_code == 200, response.text
    assert response.json() == expected
    _assert_unchanged(connection, before)


@pytest.mark.parametrize(
    ("caller", "action", "observation", "expected_status", "expected_error"),
    [
        pytest.param(
            "identity",
            "gateway.board.write",
            "owned",
            403,
            "forbidden",
            id="wrong-caller-capability",
        ),
        pytest.param(
            "board",
            "gateway.board.read",
            "owned",
            400,
            "invalid_payload",
            id="wrong-route-action",
        ),
        pytest.param(
            "board_foreign",
            "gateway.board.write",
            "owned",
            403,
            "operation_caller_mismatch",
            id="foreign-writer",
        ),
        pytest.param(
            "board",
            "gateway.board.write",
            "missing",
            404,
            "posting_observation_not_found",
            id="missing",
        ),
    ],
)
def test_get_rejects_unauthorized_or_invisible_observations_without_effects(
    legacy_gateway: tuple[PublicationGateway, _PendingTemplate],
    caller: str,
    action: str,
    observation: str,
    expected_status: int,
    expected_error: str,
) -> None:
    gateway, template = legacy_gateway
    observation_id = (
        template.pending_observation_ids[0] if observation == "owned" else f"obs-{uuid4()}"
    )
    writer, before = _writer_and_snapshot()
    response = _signed_request(
        gateway,
        caller,
        "GET",
        f"{_LEGACY_PATH}/{observation_id}",
        action=action,
    )
    assert response.status_code == expected_status, response.text
    assert response.json()["error"] == expected_error
    _assert_unchanged(writer.connection, before)


def test_get_rejects_unsigned_request_without_effects(
    legacy_gateway: tuple[PublicationGateway, _PendingTemplate],
) -> None:
    gateway, template = legacy_gateway
    writer, before = _writer_and_snapshot()
    response = gateway.client.get(
        f"{_LEGACY_PATH}/{template.pending_observation_ids[0]}",
        auth=None,
    )
    assert response.status_code == 401, response.text
    assert response.json()["error"] == "invalid_authorization"
    _assert_unchanged(writer.connection, before)
