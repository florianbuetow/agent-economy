"""Strict TaskDbClient boundary for read-only legacy posting diagnostics."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any, cast

import httpx
import pytest
from service_commons.exceptions import ServiceError

from task_board_service.posting_recovery_contract import validate_legacy_posting_envelope
from tests.unit.test_mp05_posting_recovery_client import (
    OBSERVATION_ID,
    _assert_write_auth,
    _graph,
    legacy_refund_envelope,
    pending_refund_envelope,
    signer,
)

if TYPE_CHECKING:
    from service_auth.gateway import GatewayRequestSigner

    from task_board_service.services.task_db_client import TaskDbClient

pytestmark = pytest.mark.unit
__all__ = ["signer"]

_CAPABILITY_GAP = "Missing TaskDbClient legacy posting observation diagnostic GET"


def _blocked_envelope() -> dict[str, object]:
    envelope = pending_refund_envelope()
    envelope.update(
        {
            "observation_state": "blocked_integrity",
            "refund_receipt_event_id": None,
            "refunded_at": None,
            "result": None,
            "error": {
                "error": "posting_refund_integrity_blocked",
                "message": "Posting refund evidence requires reconciliation",
                "details": {"reason": "receipt_invalid"},
            },
        }
    )
    return envelope


def _get(client: TaskDbClient, observation_id: str) -> dict[str, Any]:
    method = getattr(client, "get_legacy_posting_observation", None)
    if not callable(method):
        pytest.fail(_CAPABILITY_GAP)
    return cast("dict[str, Any]", method(observation_id))


def test_fixture_states_match_the_existing_legacy_envelope_contract() -> None:
    for envelope in (
        pending_refund_envelope(),
        legacy_refund_envelope(),
        _blocked_envelope(),
    ):
        assert (
            validate_legacy_posting_envelope(
                envelope,
                expected_observation_id=OBSERVATION_ID,
            )
            == envelope
        )


@pytest.mark.parametrize(
    "envelope",
    [pending_refund_envelope(), legacy_refund_envelope(), _blocked_envelope()],
    ids=["pending", "refunded", "blocked"],
)
def test_get_strictly_parses_every_existing_legacy_envelope_state(
    signer: GatewayRequestSigner,
    envelope: dict[str, object],
) -> None:
    graph = _graph(signer, [httpx.Response(200, json=envelope)])
    try:
        assert _get(graph.client, OBSERVATION_ID) == envelope
    finally:
        graph.client.close()


def test_get_uses_exact_path_and_existing_board_write_authority(
    signer: GatewayRequestSigner,
) -> None:
    envelope = pending_refund_envelope()
    graph = _graph(signer, [httpx.Response(200, json=envelope)])
    try:
        assert _get(graph.client, OBSERVATION_ID) == envelope
    finally:
        graph.client.close()
    assert len(graph.requests) == 1
    request = graph.requests[0]
    assert request.method == "GET"
    assert request.url.path == f"/board/legacy-posting-observations/{OBSERVATION_ID}"
    assert request.url.query == b""
    _assert_write_auth(
        request,
        f"GET /board/legacy-posting-observations/{OBSERVATION_ID}",
    )


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (202, pending_refund_envelope()),
        (200, {**pending_refund_envelope(), "extra": True}),
        (
            200,
            {**pending_refund_envelope(), "observation_id": "obs-other"},
        ),
    ],
    ids=["non-200-success", "extra-field", "wrong-observation"],
)
def test_get_rejects_nonexact_successes(
    signer: GatewayRequestSigner,
    status: int,
    body: dict[str, object],
) -> None:
    graph = _graph(signer, [httpx.Response(status, json=body)])
    try:
        with pytest.raises(ServiceError) as raised:
            _get(graph.client, OBSERVATION_ID)
    finally:
        graph.client.close()
    assert (raised.value.status_code, raised.value.error) == (
        502,
        "invalid_gateway_response",
    )


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (
            403,
            {
                "error": "operation_caller_mismatch",
                "message": "Posting observation belongs to another verified Gateway caller",
                "details": {"observation_id": OBSERVATION_ID},
            },
        ),
        (
            404,
            {
                "error": "posting_observation_not_found",
                "message": "Posting observation not found",
                "details": {},
            },
        ),
    ],
    ids=["foreign", "missing"],
)
def test_get_preserves_exact_gateway_visibility_errors(
    signer: GatewayRequestSigner,
    status: int,
    error: dict[str, object],
) -> None:
    graph = _graph(signer, [httpx.Response(status, json=copy.deepcopy(error))])
    try:
        with pytest.raises(ServiceError) as raised:
            _get(graph.client, OBSERVATION_ID)
    finally:
        graph.client.close()
    assert raised.value.status_code == status
    assert raised.value.error == error["error"]
    assert raised.value.message == error["message"]
    assert raised.value.details == error["details"]
