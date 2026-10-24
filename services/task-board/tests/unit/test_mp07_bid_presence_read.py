"""MP-07 bid-presence reads fail closed on malformed Gateway success."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from service_auth.gateway import GatewayRequestSigner, GatewaySignerConfig
from service_auth.signing import generate_keypair
from service_commons.exceptions import ServiceError

from task_board_service.services.task_db_client import TaskDbClient

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

pytestmark = pytest.mark.unit

_TASK_ID = "t-bid-presence"


@dataclass(frozen=True)
class _ClientGraph:
    client: TaskDbClient
    requests: list[httpx.Request]


class _MockTransportTaskDbClient(TaskDbClient):
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


def _recording_client(
    signer: GatewayRequestSigner,
    responses: list[httpx.Response],
) -> _ClientGraph:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert responses, "Mock transport received an unexpected request"
        return responses.pop(0)

    return _ClientGraph(_MockTransportTaskDbClient(signer, handler), requests)


@pytest.fixture
def signer(tmp_path: Path) -> GatewayRequestSigner:
    generate_keypair("gateway-bid-presence-reader", tmp_path)
    return GatewayRequestSigner(
        GatewaySignerConfig(
            kid="gateway-bid-presence-reader",
            private_key_path=tmp_path / "gateway-bid-presence-reader.key",
            audience="db-gateway",
            token_ttl_seconds=30,
        ),
        config_dir=tmp_path,
    )


def _bid(suffix: str, *, task_id: str = _TASK_ID) -> dict[str, object]:
    return {
        "bid_id": f"bid-{suffix}",
        "task_id": task_id,
        "bidder_id": f"a-worker-{suffix}",
        "proposal": f"Proposal {suffix}",
        "amount": 90,
        "submitted_at": "2099-01-01T00:00:00Z",
    }


def test_valid_empty_bid_list_remains_distinct_from_invalid_success(
    signer: GatewayRequestSigner,
) -> None:
    graph = _recording_client(signer, [httpx.Response(200, json={"bids": []})])
    try:
        assert graph.client.get_bids_for_task(_TASK_ID) == []
    finally:
        graph.client.close()


def test_valid_bid_rows_are_strictly_normalized_without_proposal(
    signer: GatewayRequestSigner,
) -> None:
    rows = [_bid("one"), _bid("two")]
    graph = _recording_client(signer, [httpx.Response(200, json={"bids": rows})])
    try:
        result = graph.client.get_bids_for_task(_TASK_ID)
    finally:
        graph.client.close()
    assert result == [
        {key: value for key, value in row.items() if key != "proposal"} for row in rows
    ]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json=[]),
        httpx.Response(200, json={}),
        httpx.Response(200, json={"bids": None}),
        httpx.Response(200, json={"bids": {}}),
        httpx.Response(200, json={"bids": ["not-an-object"]}),
        httpx.Response(200, content=b"not-json"),
    ],
    ids=["non-object", "missing-bids", "null-bids", "object-bids", "scalar-row", "non-json"],
)
def test_malformed_bid_list_success_is_not_treated_as_empty(
    signer: GatewayRequestSigner,
    response: httpx.Response,
) -> None:
    graph = _recording_client(signer, [response])
    try:
        with pytest.raises(ServiceError) as raised:
            graph.client.get_bids_for_task(_TASK_ID)
    finally:
        graph.client.close()
    assert raised.value.status_code == 502
    assert raised.value.error == "invalid_gateway_response"


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        ("amount", True),
        ("amount", "90"),
        ("bidder_id", None),
        ("submitted_at", None),
        ("proposal", None),
        ("extra", "unexpected"),
    ],
)
def test_malformed_bid_row_is_rejected_without_coercion_or_filtering(
    signer: GatewayRequestSigner,
    mutation: str,
    value: object,
) -> None:
    row: dict[str, Any] = _bid("one")
    row[mutation] = value
    graph = _recording_client(signer, [httpx.Response(200, json={"bids": [row]})])
    try:
        with pytest.raises(ServiceError) as raised:
            graph.client.get_bids_for_task(_TASK_ID)
    finally:
        graph.client.close()
    assert raised.value.status_code == 502
    assert raised.value.error == "invalid_gateway_response"


def test_bid_from_another_task_is_rejected(signer: GatewayRequestSigner) -> None:
    graph = _recording_client(
        signer,
        [httpx.Response(200, json={"bids": [_bid("one", task_id="t-other")]})],
    )
    try:
        with pytest.raises(ServiceError) as raised:
            graph.client.get_bids_for_task(_TASK_ID)
    finally:
        graph.client.close()
    assert raised.value.status_code == 502
    assert raised.value.error == "invalid_gateway_response"


def test_typed_gateway_error_is_preserved(signer: GatewayRequestSigner) -> None:
    response = httpx.Response(
        503,
        json={
            "error": "gateway_busy",
            "message": "Gateway is temporarily busy",
            "details": {"retryable": True},
        },
    )
    graph = _recording_client(signer, [response])
    try:
        with pytest.raises(ServiceError) as raised:
            graph.client.get_bids_for_task(_TASK_ID)
    finally:
        graph.client.close()
    assert raised.value.status_code == 503
    assert raised.value.error == "gateway_busy"
    assert raised.value.message == "Gateway is temporarily busy"
    assert raised.value.details == {"retryable": True}
