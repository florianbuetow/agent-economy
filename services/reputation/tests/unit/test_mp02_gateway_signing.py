"""MP-02 signed Reputation persistence transport."""

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

from reputation_service.services.feedback_db_client import FeedbackDbClient


@pytest.mark.unit
def test_feedback_reads_are_signed_over_the_final_query(tmp_path: Path) -> None:
    generate_keypair("gateway-reputation", tmp_path)
    signer = GatewayRequestSigner(
        GatewaySignerConfig(
            kid="gateway-reputation",
            private_key_path=Path("gateway-reputation.key"),
            audience="db-gateway",
            token_ttl_seconds=30,
        ),
        config_dir=tmp_path,
    )
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"feedback": []})

    client = FeedbackDbClient("http://gateway.test", 5, signer)
    client._client.close()
    client._client = httpx.Client(
        base_url="http://gateway.test",
        transport=httpx.MockTransport(handler),
    )
    try:
        assert client.get_by_task("task /?&") == []
    finally:
        client.close()

    request = captured[0]
    verify_gateway_request(
        request.headers["Authorization"].removeprefix("Bearer "),
        load_public_key(tmp_path / "gateway-reputation.pub"),
        policy=TokenPolicy(max_lifetime_seconds=30, clock_skew_seconds=0),
        audience="db-gateway",
        method="GET",
        path="/reputation/feedback",
        body=request.content,
        query=request.url.query,
        allowed_actions="gateway.reputation.read",
    )
