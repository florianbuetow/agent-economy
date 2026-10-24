"""The process harness can stop an authenticated recovery finalize before mutation."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from service_auth.gateway import GatewayRequestSigner, GatewaySignerConfig
from service_auth.signing import generate_keypair

from tests.runtime.mp08_financial_effect_fault_server import (
    AuthenticationConfig,
    FaultGate,
)
from tests.runtime.test_mp08_authenticated_fault_proxy import _exercise

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_finalize_gate_preserves_exact_signed_request_and_blocks_before_forward(
    tmp_path: Path,
) -> None:
    generate_keypair("gateway-task-board", tmp_path)
    path = (
        "/board/task-effect-deliveries/op-11111111-1111-4111-8111-111111111111/finalize"
    )
    signer = GatewayRequestSigner(
        GatewaySignerConfig(
            kid="gateway-task-board",
            private_key_path=Path("gateway-task-board.key"),
            audience="db-gateway",
            token_ttl_seconds=30,
        ),
        config_dir=tmp_path,
    )
    request = signer.sign_request(
        httpx.Request("POST", f"http://proxy.test{path}", content=b"{}"),
        action="gateway.board.write",
    )
    gate = FaultGate(
        method="POST",
        path=path,
        request_sha256=None,
        occurrence=1,
        phase="before_forward",
        expected_status=200,
        expected_response={},
        artifact_path=tmp_path / "gate.json",
        authentication=AuthenticationConfig.from_mapping(
            {
                "mode": "gateway_request",
                "public_key_path": str(tmp_path / "gateway-task-board.pub"),
                "expected_kid": "gateway-task-board",
                "audience": "db-gateway",
                "action": "gateway.board.write",
                "max_lifetime_seconds": 30,
                "clock_skew_seconds": 0,
                "expected_payload": {},
            }
        ),
    )
    artifact, forwarded = await _exercise(gate, request)
    assert artifact["phase"] == "before_forward"
    assert artifact["response_headers_sha256"] is None
    assert artifact["worker_id"] is None
    assert len(forwarded) == 1
    assert forwarded[0].content == request.content
    assert forwarded[0].headers["Authorization"] == request.headers["Authorization"]
