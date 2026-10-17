"""MP-02 signed Court persistence transport."""

from pathlib import Path

import httpx
import pytest
from service_auth.gateway import (
    GatewayRequestSigner,
    GatewaySignerConfig,
    verify_gateway_request,
)
from service_auth.jws import TokenPolicy
from service_auth.signing import generate_keypair, load_public_key

from court_service.services.dispute_db_client import DisputeDbClient


@pytest.mark.unit
def test_escrow_lookup_uses_only_the_narrow_board_task_capability(tmp_path: Path) -> None:
    generate_keypair("gateway-court", tmp_path)
    signer = GatewayRequestSigner(
        GatewaySignerConfig(
            kid="gateway-court",
            private_key_path=Path("gateway-court.key"),
            audience="db-gateway",
            token_ttl_seconds=30,
        ),
        config_dir=tmp_path,
    )
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"escrow_id": "esc-1"})

    client = DisputeDbClient("http://gateway.test", 5, signer)
    client._client.close()
    client._client = httpx.Client(
        base_url="http://gateway.test",
        transport=httpx.MockTransport(handler),
    )
    try:
        assert client._get_escrow_id("t-1") == "esc-1"
    finally:
        client.close()

    request = captured[0]
    verify_gateway_request(
        request.headers["Authorization"].removeprefix("Bearer "),
        load_public_key(tmp_path / "gateway-court.pub"),
        policy=TokenPolicy(max_lifetime_seconds=30, clock_skew_seconds=0),
        audience="db-gateway",
        method="GET",
        path="/board/tasks/t-1",
        body=request.content,
        query=request.url.query,
        allowed_actions="gateway.board.task.read",
    )
