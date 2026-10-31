"""MP-02 outbound DB Gateway request-signing contract."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from service_auth.gateway import (
    GatewayRequestSigner,
    GatewaySignerConfig,
    verify_gateway_request,
)
from service_auth.jws import InvalidPayloadError, TokenPolicy
from service_auth.signing import generate_keypair, load_public_key

from service_clients.gateway import GatewayClient


class _CapturingSigner:
    def __init__(self) -> None:
        self.signed: list[tuple[httpx.Request, str]] = []

    def sign_request(self, request: httpx.Request, *, action: str) -> httpx.Request:
        self.signed.append((request, action))
        request.headers["Authorization"] = "Bearer captured-signature"
        return request


@pytest.mark.unit
@pytest.mark.asyncio
async def test_write_signs_final_serialized_request_before_transport() -> None:
    signer = _CapturingSigner()
    captured: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(201, json={"agent_id": "a-1"})

    client = GatewayClient(
        base_url="http://gateway.test",
        timeout_seconds=5,
        signer=signer,
    )
    await client.connection.aclose()
    client._client = httpx.AsyncClient(  # pyright: ignore[reportPrivateUsage]
        base_url="http://gateway.test",
        transport=httpx.MockTransport(handler),
    )

    try:
        await client.register_agent(
            agent_id="a-1",
            name="worker",
            public_key="ed25519:key",
            registered_at="2026-10-15T12:00:00Z",
        )
    finally:
        await client.close()

    assert len(signer.signed) == 1
    signed_request, action = signer.signed[0]
    assert action == "gateway.identity.write"
    assert signed_request is captured[0]
    assert signed_request.content == captured[0].content
    assert captured[0].headers["Authorization"] == "Bearer captured-signature"
    assert captured[0].content


@pytest.mark.unit
@pytest.mark.asyncio
async def test_read_signs_final_encoded_query_before_transport() -> None:
    signer = _CapturingSigner()
    captured: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"feedback": []})

    client = GatewayClient(
        base_url="http://gateway.test",
        timeout_seconds=5,
        signer=signer,
    )
    await client.connection.aclose()
    client._client = httpx.AsyncClient(  # pyright: ignore[reportPrivateUsage]
        base_url="http://gateway.test",
        transport=httpx.MockTransport(handler),
    )

    try:
        result = await client.list_feedback(task_id="task /?&", agent_id="agent+1")
    finally:
        await client.close()

    assert result == []
    assert len(signer.signed) == 1
    signed_request, action = signer.signed[0]
    assert action == "gateway.reputation.read"
    assert signed_request is captured[0]
    assert signed_request.url.query == captured[0].url.query
    assert captured[0].headers["Authorization"] == "Bearer captured-signature"


@pytest.mark.unit
def test_constructor_requires_a_gateway_signer() -> None:
    with pytest.raises(TypeError):
        GatewayClient(  # type: ignore[call-arg]
            base_url="http://gateway.test",
            timeout_seconds=5,
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_real_signature_binds_gateway_client_final_body_and_query(tmp_path: Path) -> None:
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

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"feedback": []})

    client = GatewayClient(
        base_url="http://gateway.test",
        timeout_seconds=5,
        signer=signer,
    )
    await client.connection.aclose()
    client._client = httpx.AsyncClient(  # pyright: ignore[reportPrivateUsage]
        base_url="http://gateway.test",
        transport=httpx.MockTransport(handler),
    )
    try:
        await client.list_feedback(task_id="task /?&", agent_id="agent+1")
    finally:
        await client.close()

    request = captured[0]
    token = request.headers["Authorization"].removeprefix("Bearer ")
    policy = TokenPolicy(max_lifetime_seconds=30, clock_skew_seconds=0)
    public_key = load_public_key(tmp_path / "gateway-reputation.pub")
    verify_gateway_request(
        token,
        public_key,
        policy=policy,
        audience="db-gateway",
        method=request.method,
        path=request.url.path,
        body=request.content,
        query=request.url.query,
        allowed_actions="gateway.reputation.read",
    )
    with pytest.raises(InvalidPayloadError):
        verify_gateway_request(
            token,
            public_key,
            policy=policy,
            audience="db-gateway",
            method=request.method,
            path=request.url.path,
            body=b"tampered",
            query=request.url.query,
            allowed_actions="gateway.reputation.read",
        )
    with pytest.raises(InvalidPayloadError):
        verify_gateway_request(
            token,
            public_key,
            policy=policy,
            audience="db-gateway",
            method=request.method,
            path=request.url.path,
            body=request.content,
            query=b"task_id=other",
            allowed_actions="gateway.reputation.read",
        )
