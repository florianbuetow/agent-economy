"""Strict TaskDbClient boundary tests for MP-05 private posting recovery."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
import pytest
from service_auth.financial_identity import operation_id_for
from service_auth.gateway import GatewayRequestSigner, GatewaySignerConfig
from service_auth.jws import parse_jws
from service_auth.signing import generate_keypair
from service_commons.exceptions import ServiceError

from task_board_service.services.task_db_client import TaskDbClient

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

pytestmark = pytest.mark.unit

_OBSERVATION_ID = "obs-00000000-0000-4000-8000-000000000001"
_OTHER_OBSERVATION_ID = "obs-00000000-0000-4000-8000-000000000002"
_OPERATION_ID = "op-00000000-0000-4000-8000-000000000003"
_TASK_ID = "t-00000000-0000-4000-8000-000000000004"
_ESCROW_ID = "esc-mp05-client"
_POSTER_ID = "a-mp05-poster"
_AMOUNT = 100
_TIMESTAMP = "2026-10-18T12:34:56.123456Z"

OBSERVATION_ID = _OBSERVATION_ID


@dataclass(frozen=True)
class _ClientGraph:
    client: TaskDbClient
    requests: list[httpx.Request]


class _MockTaskDbClient(TaskDbClient):
    def __init__(
        self,
        signer: GatewayRequestSigner,
        handler: Callable[[httpx.Request], httpx.Response],
    ) -> None:
        super().__init__("http://gateway.test", 5, signer)
        self._client.close()
        self._client = httpx.Client(
            base_url="http://gateway.test",
            transport=httpx.MockTransport(handler),
        )


@pytest.fixture
def signer(tmp_path: Path) -> GatewayRequestSigner:
    generate_keypair("gateway-mp05-recovery-client", tmp_path)
    return GatewayRequestSigner(
        GatewaySignerConfig(
            kid="gateway-mp05-recovery-client",
            private_key_path=tmp_path / "gateway-mp05-recovery-client.key",
            audience="db-gateway",
            token_ttl_seconds=30,
        ),
        config_dir=tmp_path,
    )


def _graph(
    signer: GatewayRequestSigner,
    responses: list[httpx.Response],
) -> _ClientGraph:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert responses, "unexpected recovery request"
        return responses.pop(0)

    return _ClientGraph(_MockTaskDbClient(signer, handler), requests)


def _canonical_hash(value: dict[str, object]) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _refund_request(*, source_reference: str | None = None) -> dict[str, object]:
    return {
        "request_version": 1,
        "operation_kind": "escrow_release",
        "escrow_id": _ESCROW_ID,
        "recipient_account_id": _POSTER_ID,
        "source_reference": source_reference or f"task:{_TASK_ID}:posting_refund:{_ESCROW_ID}",
    }


def _pending_envelope() -> dict[str, object]:
    request = _refund_request()
    return {
        "protocol_version": 1,
        "observation_id": _OBSERVATION_ID,
        "task_id": _TASK_ID,
        "escrow_id": _ESCROW_ID,
        "poster_id": _POSTER_ID,
        "amount": _AMOUNT,
        "observed_fact_sha256": "a" * 64,
        "observation_state": "refund_pending",
        "refund_operation_id": operation_id_for("task-posting-refund", (_ESCROW_ID,)),
        "refund_request": request,
        "refund_request_sha256": _canonical_hash(request),
        "refund_receipt_event_id": None,
        "refunded_at": None,
        "result": None,
        "error": None,
    }


def _native_refunded_envelope() -> dict[str, object]:
    envelope = _pending_envelope()
    request = _refund_request(source_reference=f"task:{_TASK_ID}:posting_compensation")
    envelope.update(
        {
            "observation_state": "refunded",
            "refund_operation_id": _OPERATION_ID,
            "refund_request": request,
            "refund_request_sha256": _canonical_hash(request),
            "refund_receipt_event_id": 41,
            "refunded_at": _TIMESTAMP,
            "result": {"escrow_status_at_commit": "released"},
        }
    )
    return envelope


def _legacy_refunded_envelope() -> dict[str, object]:
    envelope = _pending_envelope()
    envelope.update(
        {
            "observation_state": "refunded",
            "refund_operation_id": None,
            "refund_request": None,
            "refund_request_sha256": None,
            "refund_receipt_event_id": None,
            "refunded_at": _TIMESTAMP,
            "result": {
                "refund_origin": "existing_refund",
                "settlement_provenance_kind": "legacy_observation",
                "escrow_status_at_cutover": "released",
            },
        }
    )
    return envelope


# Shared fixtures for the additional recovery-boundary regression modules.
recovery_client_graph = _graph
canonical_refund_hash = _canonical_hash
pending_refund_envelope = _pending_envelope
legacy_refund_envelope = _legacy_refunded_envelope


def _assert_write_auth(request: httpx.Request, resource: str) -> None:
    token = request.headers["Authorization"].removeprefix("Bearer ")
    parsed = parse_jws(token)
    assert parsed.header.aud == "db-gateway"
    assert parsed.header.resource == resource
    assert parsed.payload == {
        "action": "gateway.board.write",
        "body_sha256": hashlib.sha256(request.content).hexdigest(),
        "query_sha256": hashlib.sha256(request.url.query).hexdigest(),
    }


def test_recovery_page_uses_write_authority_and_preserves_exact_cursor_page(
    signer: GatewayRequestSigner,
) -> None:
    page = {
        "items": [
            {
                "cursor": f"legacy:{_OBSERVATION_ID}",
                "kind": "legacy",
                "identity": _OBSERVATION_ID,
            },
            {
                "cursor": f"native:{_OPERATION_ID}",
                "kind": "native",
                "identity": _OPERATION_ID,
            },
        ],
        "next_after": f"native:{_OPERATION_ID}",
    }
    graph = _graph(signer, [httpx.Response(200, json=page)])
    try:
        assert graph.client.list_posting_recovery("", 2) == page
    finally:
        graph.client.close()
    assert len(graph.requests) == 1
    request = graph.requests[0]
    assert request.method == "GET"
    assert request.url.path == "/board/posting-recovery"
    assert dict(request.url.params) == {"after": "", "limit": "2"}
    _assert_write_auth(request, "GET /board/posting-recovery")


@pytest.mark.parametrize(
    ("after", "limit"),
    [
        ("plain", 1),
        ("native:not-an-operation", 1),
        ("legacy:not-an-observation", 1),
        ("", True),
        ("", 0),
        ("", 1001),
    ],
)
def test_invalid_recovery_page_request_is_rejected_before_transport(
    signer: GatewayRequestSigner,
    after: str,
    limit: int,
) -> None:
    graph = _graph(signer, [])
    try:
        with pytest.raises(ServiceError) as raised:
            graph.client.list_posting_recovery(after, limit)
    finally:
        graph.client.close()
    assert raised.value.error == "invalid_payload"
    assert raised.value.status_code == 400
    assert graph.requests == []


@pytest.mark.parametrize(
    "page",
    [
        {"items": [], "next_after": "native:" + _OPERATION_ID},
        {
            "items": [
                {
                    "cursor": f"native:{_OPERATION_ID}",
                    "kind": "legacy",
                    "identity": _OPERATION_ID,
                }
            ],
            "next_after": None,
        },
        {
            "items": [
                {
                    "cursor": f"native:{_OPERATION_ID}",
                    "kind": "native",
                    "identity": _OPERATION_ID,
                    "extra": True,
                }
            ],
            "next_after": None,
        },
        {
            "items": [
                {
                    "cursor": f"native:{_OPERATION_ID}",
                    "kind": "native",
                    "identity": _OPERATION_ID,
                },
                {
                    "cursor": f"legacy:{_OBSERVATION_ID}",
                    "kind": "legacy",
                    "identity": _OBSERVATION_ID,
                },
            ],
            "next_after": None,
        },
        {"items": [], "next_after": None, "extra": []},
    ],
    ids=["cursor-without-items", "kind-mismatch", "extra-item-field", "unsorted", "extra-page"],
)
def test_malformed_recovery_page_success_fails_closed(
    signer: GatewayRequestSigner,
    page: dict[str, object],
) -> None:
    graph = _graph(signer, [httpx.Response(200, json=page)])
    try:
        with pytest.raises(ServiceError) as raised:
            graph.client.list_posting_recovery("", 2)
    finally:
        graph.client.close()
    assert raised.value.error == "invalid_gateway_response"
    assert raised.value.status_code == 502


@pytest.mark.parametrize(
    ("status", "envelope"),
    [
        (202, _pending_envelope()),
        (200, _native_refunded_envelope()),
        (200, _legacy_refunded_envelope()),
    ],
    ids=["pending", "native-refunded", "legacy-refunded"],
)
def test_legacy_reconciliation_validates_state_and_preserves_historical_identity(
    signer: GatewayRequestSigner,
    status: int,
    envelope: dict[str, object],
) -> None:
    graph = _graph(signer, [httpx.Response(status, json=envelope)])
    try:
        assert graph.client.reconcile_legacy_posting_observation(_OBSERVATION_ID) == envelope
    finally:
        graph.client.close()
    request = graph.requests[0]
    assert request.method == "POST"
    assert request.url.path == f"/board/legacy-posting-observations/{_OBSERVATION_ID}/reconcile"
    assert json.loads(request.content) == {}
    _assert_write_auth(
        request,
        f"POST /board/legacy-posting-observations/{_OBSERVATION_ID}/reconcile",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("protocol_version", True),
        ("observation_id", _OTHER_OBSERVATION_ID),
        ("amount", True),
        ("observed_fact_sha256", "A" * 64),
        ("refund_operation_id", _OPERATION_ID),
        ("refund_request_sha256", "b" * 64),
        ("refund_receipt_event_id", 1),
        ("result", {"escrow_status_at_commit": "released"}),
    ],
)
def test_malformed_pending_legacy_success_fails_closed(
    signer: GatewayRequestSigner,
    field: str,
    value: object,
) -> None:
    envelope = _pending_envelope()
    envelope[field] = value
    graph = _graph(signer, [httpx.Response(202, json=envelope)])
    try:
        with pytest.raises(ServiceError) as raised:
            graph.client.reconcile_legacy_posting_observation(_OBSERVATION_ID)
    finally:
        graph.client.close()
    assert raised.value.error == "invalid_gateway_response"
    assert raised.value.status_code == 502


def test_exact_blocked_legacy_wrapper_becomes_service_error(
    signer: GatewayRequestSigner,
) -> None:
    envelope = _pending_envelope()
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
    wrapper = {
        "error": "posting_refund_integrity_blocked",
        "message": "Posting refund evidence requires reconciliation",
        "details": {"observation": envelope},
    }
    graph = _graph(signer, [httpx.Response(409, json=wrapper)])
    try:
        with pytest.raises(ServiceError) as raised:
            graph.client.reconcile_legacy_posting_observation(_OBSERVATION_ID)
    finally:
        graph.client.close()
    assert raised.value.status_code == 409
    assert raised.value.error == "posting_refund_integrity_blocked"
    assert raised.value.message == "Posting refund evidence requires reconciliation"
    assert raised.value.details == {"observation": envelope}


def test_blocked_new_refund_rejects_changed_operation_and_source(
    signer: GatewayRequestSigner,
) -> None:
    envelope = _pending_envelope()
    request = _refund_request(source_reference=f"task:{_TASK_ID}:posting_compensation")
    envelope.update(
        {
            "observation_state": "blocked_integrity",
            "refund_operation_id": _OPERATION_ID,
            "refund_request": request,
            "refund_request_sha256": _canonical_hash(request),
            "error": {
                "error": "posting_refund_integrity_blocked",
                "message": "Posting refund evidence requires reconciliation",
                "details": {"reason": "receipt_invalid"},
            },
        }
    )
    wrapper = {
        "error": "posting_refund_integrity_blocked",
        "message": "Posting refund evidence requires reconciliation",
        "details": {"observation": envelope},
    }
    graph = _graph(signer, [httpx.Response(409, json=wrapper)])
    try:
        with pytest.raises(ServiceError) as raised:
            graph.client.reconcile_legacy_posting_observation(_OBSERVATION_ID)
    finally:
        graph.client.close()
    assert raised.value.status_code == 502
    assert raised.value.error == "invalid_gateway_response"


def test_nonblocked_legacy_conflict_is_preserved(signer: GatewayRequestSigner) -> None:
    error = {
        "error": "posting_observation_conflict",
        "message": "Posting observation conflicts with another operation",
        "details": {"winning_observation_id": _OTHER_OBSERVATION_ID},
    }
    graph = _graph(signer, [httpx.Response(409, json=error)])
    try:
        with pytest.raises(ServiceError) as raised:
            graph.client.reconcile_legacy_posting_observation(_OBSERVATION_ID)
    finally:
        graph.client.close()
    assert raised.value.status_code == 409
    assert raised.value.error == error["error"]
    assert raised.value.message == error["message"]
    assert raised.value.details == error["details"]


def test_ordinary_legacy_gateway_error_is_preserved(signer: GatewayRequestSigner) -> None:
    error = {
        "error": "posting_observation_not_found",
        "message": "Posting observation not found",
        "details": {},
    }
    graph = _graph(signer, [httpx.Response(404, json=error)])
    try:
        with pytest.raises(ServiceError) as raised:
            graph.client.reconcile_legacy_posting_observation(_OBSERVATION_ID)
    finally:
        graph.client.close()
    assert raised.value.status_code == 404
    assert raised.value.error == error["error"]
    assert raised.value.message == error["message"]
    assert raised.value.details == {}
