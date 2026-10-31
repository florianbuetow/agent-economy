"""Task Board client contract for the native MP-08 delivery finalizer."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

import httpx
import pytest
from service_auth.gateway import (
    GatewayRequestSigner,
    GatewaySignerConfig,
    verify_gateway_request,
)
from service_auth.jws import TokenPolicy
from service_auth.signing import generate_keypair
from service_commons.exceptions import ServiceError

from task_board_service.services.task_db_client import TaskDbClient
from tests.fakes.board_mp08_recovery import EFFECT_OPERATION_ID, diagnostic_envelope

pytestmark = pytest.mark.integration

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


class _Finalize(Protocol):
    def __call__(self, effect_operation_id: str) -> dict[str, Any]: ...


def _client(tmp_path: Path) -> tuple[TaskDbClient, Ed25519PublicKey]:
    _private_key, public_key = generate_keypair("gateway-task-board", tmp_path)
    signer = GatewayRequestSigner(
        GatewaySignerConfig(
            kid="gateway-task-board",
            private_key_path=Path("gateway-task-board.key"),
            audience="db-gateway",
            token_ttl_seconds=300,
        ),
        config_dir=tmp_path,
    )
    return TaskDbClient(
        base_url="http://gateway.invalid", timeout_seconds=10, signer=signer
    ), public_key


def _finalizer(client: TaskDbClient) -> _Finalize:
    value = getattr(client, "finalize_task_effect_delivery", None)
    if not callable(value):
        pytest.fail("Missing native task-effect delivery finalizer client capability")
    return cast("_Finalize", value)


@pytest.mark.parametrize(
    ("status_code", "state"),
    [(202, "leased"), (200, "completed")],
)
def test_finalizer_uses_one_signed_post_to_new_route_without_read_or_old_finalizer(
    status_code: int,
    state: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, public_key = _client(tmp_path)
    requests: list[httpx.Request] = []
    body = diagnostic_envelope(state=state)

    def send(_client: httpx.Client, request: httpx.Request, **_kwargs: object) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status_code, json=body, request=request)

    monkeypatch.setattr(httpx.Client, "send", send)
    try:
        result = _finalizer(client)(EFFECT_OPERATION_ID)
    finally:
        client.close()

    assert result == body
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert request.url.path == (f"/board/task-effect-deliveries/{EFFECT_OPERATION_ID}/finalize")
    assert request.content == b"{}"
    assert "/board/task-operations/" not in request.url.path
    authorization = request.headers["Authorization"]
    assert authorization.startswith("Bearer ")
    verify_gateway_request(
        authorization.removeprefix("Bearer "),
        public_key,
        policy=TokenPolicy(max_lifetime_seconds=300, clock_skew_seconds=0),
        audience="db-gateway",
        method="POST",
        path=request.url.path,
        body=b"{}",
        query=b"",
        allowed_actions="gateway.board.write",
    )


def test_finalizer_rejects_invalid_identifier_before_http(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _public_key = _client(tmp_path)
    calls = 0

    def send(_client: httpx.Client, request: httpx.Request, **_kwargs: object) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise AssertionError(f"invalid identifier reached HTTP: {request.url}")

    monkeypatch.setattr(httpx.Client, "send", send)
    try:
        with pytest.raises(ServiceError):
            _finalizer(client)("not-an-operation")
    finally:
        client.close()
    assert calls == 0
