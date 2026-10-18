"""Temporary T-114 Identity ASGI token baseline and strict-contract reds."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from identity_service.app import create_app
from identity_service.config import clear_settings_cache
from identity_service.core.state import init_app_state, reset_app_state
from identity_service.services import agent_registry
from identity_service.services.agent_registry import AgentRegistry

FROZEN_NOW = datetime(2030, 1, 1, tzinfo=UTC)
NOW = int(FROZEN_NOW.timestamp())
AGENT_ID = "a-token-contract-agent"

pytestmark = pytest.mark.asyncio


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def make_raw_jws_fixture(
    header: object,
    payload: object,
    private_key: Ed25519PrivateKey,
) -> str:
    """Sign arbitrary JSON header/payload values without production token helpers."""
    header_segment = _b64url(json.dumps(header, sort_keys=True, separators=(",", ":")).encode())
    payload_segment = _b64url(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    signature_segment = _b64url(private_key.sign(signing_input))
    return f"{header_segment}.{payload_segment}.{signature_segment}"


class _FakeStore:
    def __init__(self, public_key: str) -> None:
        self._public_key = public_key

    async def get_by_id(self, agent_id: str) -> dict[str, str] | None:
        if agent_id != AGENT_ID:
            return None
        return {
            "agent_id": AGENT_ID,
            "name": "Token Contract Agent",
            "public_key": self._public_key,
            "registered_at": "2030-01-01T00:00:00Z",
        }

    async def close(self) -> None:
        return None


def _strict_header(**updates: object) -> dict[str, object]:
    header: dict[str, object] = {
        "alg": "EdDSA",
        "typ": "JWT",
        "kid": AGENT_ID,
        "iat": NOW,
        "exp": NOW + 300,
    }
    header.update(updates)
    return header


def _payload() -> dict[str, object]:
    return {
        "aud": "central-bank",
        "action": "escrow_release",
        "resource": "POST /escrow/esc-1/release",
        "escrow_id": "esc-1",
    }


@pytest_asyncio.fixture
async def identity_client(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[httpx.AsyncClient, Ed25519PrivateKey]:
    config_path = tmp_path / "identity.yaml"
    config_path.write_text(
        """
service:
  name: identity
  version: 0.1.0
server:
  host: 127.0.0.1
  port: 8001
  log_level: warning
logging:
  level: WARNING
  directory: data/logs
crypto:
  public_key_prefix: "ed25519:"
  public_key_bytes: 32
  signature_bytes: 64
request:
  max_body_size: 1572864
db_gateway:
  url: http://db-gateway.test
  timeout_seconds: 1
"""
    )
    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.setattr(agent_registry, "_clock", lambda: FROZEN_NOW)
    clear_settings_cache()
    reset_app_state()

    private_key = Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes_raw()
    public_key = f"ed25519:{base64.b64encode(public_bytes).decode()}"
    app = create_app()
    state = init_app_state()
    state.registry = AgentRegistry(
        store=_FakeStore(public_key),  # type: ignore[arg-type]
        public_key_prefix="ed25519:",
        public_key_bytes=32,
        signature_bytes=64,
    )
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://identity.test") as client:
        yield client, private_key

    reset_app_state()
    clear_settings_cache()


async def _verify_route(
    identity_client: tuple[httpx.AsyncClient, Ed25519PrivateKey],
    header: object,
    payload: object,
) -> httpx.Response:
    client, private_key = identity_client
    token = make_raw_jws_fixture(header, payload, private_key)
    return await client.post("/agents/verify-jws", json={"token": token})


@pytest.mark.parametrize("header", [None, [], "header", 7])
async def test_current_baseline_nonobject_header_returns_500(
    identity_client: tuple[httpx.AsyncClient, Ed25519PrivateKey],
    header: object,
) -> None:
    response = await _verify_route(identity_client, header, _payload())
    assert response.status_code == 500
    assert response.json()["error"] == "internal_error"


@pytest.mark.parametrize("payload", [None, [], "payload", 7])
async def test_current_baseline_nonobject_payload_returns_400(
    identity_client: tuple[httpx.AsyncClient, Ed25519PrivateKey],
    payload: object,
) -> None:
    response = await _verify_route(identity_client, _strict_header(), payload)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_jws"


@pytest.mark.parametrize(
    "header",
    [
        {"alg": "EdDSA", "kid": AGENT_ID, "iat": NOW, "exp": NOW + 300},
        _strict_header(typ="NOT-JWT"),
        {"alg": "EdDSA", "typ": "JWT", "kid": AGENT_ID, "exp": NOW + 300},
        _strict_header(iat=NOW + 301),
        _strict_header(iat=NOW + 300, exp=NOW + 200),
        {"alg": "EdDSA", "typ": "JWT", "kid": AGENT_ID, "iat": NOW},
        _strict_header(exp=NOW),
    ],
)
async def test_current_baseline_profile_and_temporal_gaps_are_accepted(
    identity_client: tuple[httpx.AsyncClient, Ed25519PrivateKey],
    header: dict[str, object],
) -> None:
    response = await _verify_route(identity_client, header, _payload())
    assert response.status_code == 200, response.text
    assert response.json()["valid"] is True


async def test_current_baseline_strict_shaped_control_is_accepted(
    identity_client: tuple[httpx.AsyncClient, Ed25519PrivateKey],
) -> None:
    response = await _verify_route(identity_client, _strict_header(), _payload())
    assert response.status_code == 200
    assert response.json()["valid"] is True


async def test_current_baseline_missing_expiry_is_accepted(
    identity_client: tuple[httpx.AsyncClient, Ed25519PrivateKey],
) -> None:
    header = _strict_header()
    del header["exp"]
    response = await _verify_route(identity_client, header, _payload())
    assert response.status_code == 200
    assert response.json()["valid"] is True


async def test_current_baseline_exact_expiry_is_accepted(
    identity_client: tuple[httpx.AsyncClient, Ed25519PrivateKey],
) -> None:
    response = await _verify_route(identity_client, _strict_header(exp=NOW), _payload())
    assert response.status_code == 200
    assert response.json()["valid"] is True


async def test_current_baseline_future_iat_is_accepted(
    identity_client: tuple[httpx.AsyncClient, Ed25519PrivateKey],
) -> None:
    response = await _verify_route(identity_client, _strict_header(iat=NOW + 301), _payload())
    assert response.status_code == 200
    assert response.json()["valid"] is True


async def test_current_baseline_exp_not_after_iat_is_accepted(
    identity_client: tuple[httpx.AsyncClient, Ed25519PrivateKey],
) -> None:
    header = _strict_header(iat=NOW + 300, exp=NOW + 200)
    response = await _verify_route(identity_client, header, _payload())
    assert response.status_code == 200
    assert response.json()["valid"] is True


@pytest.mark.parametrize(
    "header,payload",
    [
        (None, _payload()),
        ([], _payload()),
        ("header", _payload()),
        (7, _payload()),
        (_strict_header(), None),
        (_strict_header(), []),
        (_strict_header(), "payload"),
        (_strict_header(), 7),
        ({"typ": "JWT", "kid": AGENT_ID, "iat": NOW, "exp": NOW + 300}, _payload()),
        (_strict_header(alg="HS256"), _payload()),
        (_strict_header(alg=True), _payload()),
        (_strict_header(alg=1), _payload()),
        (_strict_header(alg=1.5), _payload()),
        ({"alg": "EdDSA", "kid": AGENT_ID, "iat": NOW, "exp": NOW + 300}, _payload()),
        (_strict_header(typ="NOT-JWT"), _payload()),
        (_strict_header(typ=True), _payload()),
        (_strict_header(typ=1), _payload()),
        (_strict_header(typ=1.5), _payload()),
        ({"alg": "EdDSA", "typ": "JWT", "iat": NOW, "exp": NOW + 300}, _payload()),
        (_strict_header(kid=""), _payload()),
        (_strict_header(kid=True), _payload()),
        (_strict_header(kid=1), _payload()),
        (_strict_header(kid=1.5), _payload()),
        ({"alg": "EdDSA", "typ": "JWT", "kid": AGENT_ID, "exp": NOW + 300}, _payload()),
        (_strict_header(iat=True), _payload()),
        (_strict_header(iat="now"), _payload()),
        (_strict_header(iat=1.5), _payload()),
        ({"alg": "EdDSA", "typ": "JWT", "kid": AGENT_ID, "iat": NOW}, _payload()),
        (_strict_header(exp=True), _payload()),
        (_strict_header(exp="later"), _payload()),
        (_strict_header(exp=float(NOW + 300)), _payload()),
        (_strict_header(iat=NOW + 300, exp=NOW + 200), _payload()),
    ],
)
async def test_required_contract_malformed_profile_returns_400(
    identity_client: tuple[httpx.AsyncClient, Ed25519PrivateKey],
    header: object,
    payload: object,
) -> None:
    response = await _verify_route(identity_client, header, payload)
    assert response.status_code == 400, response.text
    assert response.json()["error"] == "invalid_jws"


async def test_required_contract_exact_expiry_returns_401(
    identity_client: tuple[httpx.AsyncClient, Ed25519PrivateKey],
) -> None:
    response = await _verify_route(identity_client, _strict_header(exp=NOW), _payload())
    assert response.status_code == 401, response.text
    assert response.json()["error"] == "token_expired"


async def test_required_contract_future_iat_returns_401(
    identity_client: tuple[httpx.AsyncClient, Ed25519PrivateKey],
) -> None:
    response = await _verify_route(identity_client, _strict_header(iat=NOW + 301), _payload())
    assert response.status_code == 401, response.text
    assert response.json()["error"] == "token_not_yet_valid"
