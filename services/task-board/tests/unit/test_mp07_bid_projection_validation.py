"""Malformed authoritative bids cannot become accepted economic terms."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import pytest
from service_auth.gateway import GatewayRequestSigner, GatewaySignerConfig
from service_auth.signing import generate_keypair
from service_commons.exceptions import ServiceError

from tests.unit.test_mp07_task_operation_client import _recording_client

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


@pytest.fixture
def signer(tmp_path: Path) -> GatewayRequestSigner:
    generate_keypair("gateway-bid-reader", tmp_path)
    return GatewayRequestSigner(
        GatewaySignerConfig(
            kid="gateway-bid-reader",
            private_key_path=tmp_path / "gateway-bid-reader.key",
            audience="db-gateway",
            token_ttl_seconds=30,
        ),
        config_dir=tmp_path,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("amount", True),
        ("amount", "90"),
        ("amount", 1.5),
        ("amount", None),
        ("bidder_id", None),
        ("submitted_at", None),
        ("bid_id", "bid-other"),
        ("task_id", "t-other"),
        ("proposal", None),
    ],
)
def test_malformed_bid_is_rejected_before_normalization(
    signer: GatewayRequestSigner,
    field: str,
    value: object,
) -> None:
    body: dict[str, Any] = {
        "bid_id": "bid-one",
        "task_id": "t-one",
        "bidder_id": "a-worker",
        "amount": 90,
        "proposal": "90",
        "submitted_at": "2099-01-01T00:00:00Z",
    }
    body[field] = value
    graph = _recording_client(signer, [httpx.Response(200, json=body)])
    try:
        with pytest.raises(ServiceError) as raised:
            graph.client.get_bid("bid-one", "t-one")
    finally:
        graph.client.close()
    assert raised.value.status_code == 502
    assert raised.value.error == "invalid_gateway_response"


def test_valid_bid_and_missing_bid_remain_distinct(signer: GatewayRequestSigner) -> None:
    body = {
        "bid_id": "bid-one",
        "task_id": "t-one",
        "bidder_id": "a-worker",
        "amount": 90,
        "proposal": "90",
        "submitted_at": "2099-01-01T00:00:00Z",
    }
    graph = _recording_client(signer, [httpx.Response(200, json=body), httpx.Response(404)])
    try:
        assert graph.client.get_bid("bid-one", "t-one") == {
            key: value for key, value in body.items() if key != "proposal"
        }
        assert graph.client.get_bid("bid-missing", "t-one") is None
    finally:
        graph.client.close()
