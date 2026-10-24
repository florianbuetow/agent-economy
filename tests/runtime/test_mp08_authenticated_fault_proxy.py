"""Authenticated dynamic-request controls for the MP-08 fault proxy."""

from __future__ import annotations

import asyncio
import hashlib
import json
from argparse import Namespace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from collections.abc import Mapping

import httpx
import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from service_auth.gateway import GatewayRequestSigner, GatewaySignerConfig
from service_auth.jws import JwsAdmissionError, create_jws
from service_auth.signing import generate_keypair

from tests.runtime.mp08_financial_effect_fault_server import (
    AuthenticationConfig,
    FaultGate,
    _serve,
    canonical_headers_sha256,
)

pytestmark = pytest.mark.integration

PATH = "/board/task-effect-deliveries/lease"
AUDIENCE = "db-gateway"
ACTION = "gateway.board.write"
WORKER_ID = "recovery-12345678-1234-4abc-8def-123456789abc"
BODY = json.dumps(
    {"protocol_version": 1, "worker_id": WORKER_ID},
    sort_keys=True,
    separators=(",", ":"),
).encode()


def _authentication(
    public_key_path: Path,
    *,
    mode: str,
    expected_kid: str,
    audience: str = AUDIENCE,
    action: str = ACTION,
    expected_payload: Mapping[str, object] | None = None,
) -> AuthenticationConfig:
    return AuthenticationConfig.from_mapping(
        {
            "mode": mode,
            "public_key_path": str(public_key_path),
            "expected_kid": expected_kid,
            "audience": audience,
            "action": action,
            "max_lifetime_seconds": 30,
            "clock_skew_seconds": 0,
            "expected_payload": expected_payload
            if expected_payload is not None
            else {"protocol_version": 1},
        }
    )


def _gate(tmp_path: Path, authentication: AuthenticationConfig) -> FaultGate:
    return FaultGate(
        method="POST",
        path=PATH,
        request_sha256=None,
        occurrence=1,
        phase="before_forward",
        expected_status=200,
        expected_response={},
        artifact_path=tmp_path / "gate.json",
        authentication=authentication,
    )


def _gateway_request(tmp_path: Path, *, body: bytes = BODY) -> httpx.Request:
    signer = GatewayRequestSigner(
        GatewaySignerConfig(
            kid="gateway-task-board",
            private_key_path=Path("gateway-task-board.key"),
            audience=AUDIENCE,
            token_ttl_seconds=30,
        ),
        config_dir=tmp_path,
    )
    return signer.sign_request(
        httpx.Request("POST", f"http://proxy.test{PATH}", content=body),
        action=ACTION,
    )


def _bank_request(
    private_key: Ed25519PrivateKey,
    *,
    kid: str = "platform-task-board",
    audience: str = "central-bank",
    resource: str = "POST /escrow/release",
    action: str = "escrow_release",
    payload: Mapping[str, object] | None = None,
    token_ttl_seconds: int = 30,
) -> httpx.Request:
    business = payload or {"escrow_id": "escrow-1", "recipient_account_id": "agent-1"}
    token = create_jws(
        {"action": action, **business},
        private_key,
        kid=kid,
        audience=audience,
        resource=resource,
        token_ttl_seconds=token_ttl_seconds,
    )
    return httpx.Request(
        "POST",
        "http://proxy.test/escrow/release",
        content=json.dumps(
            {"token": token}, sort_keys=True, separators=(",", ":")
        ).encode(),
    )


async def _exercise(
    gate: FaultGate, request: httpx.Request
) -> tuple[dict[str, Any], list[httpx.Request]]:
    calls = 0
    recorded: list[httpx.Request] = []

    def upstream(upstream_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        recorded.append(upstream_request)
        return httpx.Response(200, json={})

    from tests.runtime.mp08_financial_effect_fault_server import create_proxy

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(upstream)
    ) as upstream_client:
        app = create_proxy(upstream_client, "http://gateway.test", gate)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
        ) as client:
            pending = asyncio.create_task(client.send(request))
            reached = asyncio.create_task(gate.reached.wait())
            try:
                async with asyncio.timeout(2):
                    done, _pending = await asyncio.wait(
                        {pending, reached}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if pending in done and not gate.reached.is_set():
                        await pending
                    await reached
                artifact = json.loads(gate.artifact_path.read_text())
                gate.release.set()
                assert (await pending).status_code == 200
                assert calls == len(recorded)
                return artifact, recorded
            finally:
                gate.release.set()
                if not pending.done():
                    pending.cancel()
                if not reached.done():
                    reached.cancel()
                await asyncio.gather(pending, reached, return_exceptions=True)


@pytest.mark.asyncio
async def test_gateway_lease_authenticates_dynamic_jws_and_records_safe_evidence(
    tmp_path: Path,
) -> None:
    generate_keypair("gateway-task-board", tmp_path)
    request = _gateway_request(tmp_path)
    gate = _gate(
        tmp_path,
        _authentication(
            tmp_path / "gateway-task-board.pub",
            mode="gateway_lease",
            expected_kid="gateway-task-board",
        ),
    )

    artifact, recorded = await _exercise(gate, request)

    assert len(recorded) == 1
    assert artifact["request_sha256"] == hashlib.sha256(BODY).hexdigest()
    assert artifact["request_headers_sha256"] == canonical_headers_sha256(
        recorded[0].headers.raw
    )
    assert artifact["response_headers_sha256"] is None
    assert artifact["worker_id"] == WORKER_ID
    assert "authorization" not in json.dumps(artifact).lower()
    assert request.headers["Authorization"] not in json.dumps(artifact)


@pytest.mark.asyncio
async def test_bank_token_authenticates_exact_business_payload(tmp_path: Path) -> None:
    private_key, _public_key = generate_keypair("platform-task-board", tmp_path)
    expected = {"escrow_id": "escrow-1", "recipient_account_id": "agent-1"}
    request = _bank_request(private_key, payload=expected)
    gate = FaultGate(
        method="POST",
        path="/escrow/release",
        request_sha256=None,
        occurrence=1,
        phase="before_forward",
        expected_status=200,
        expected_response={},
        artifact_path=tmp_path / "gate.json",
        authentication=_authentication(
            tmp_path / "platform-task-board.pub",
            mode="bank_token",
            expected_kid="platform-task-board",
            audience="central-bank",
            action="escrow_release",
            expected_payload=expected,
        ),
    )

    artifact, recorded = await _exercise(gate, request)

    assert len(recorded) == 1
    assert artifact["worker_id"] is None
    assert artifact["request_headers_sha256"] == canonical_headers_sha256(
        recorded[0].headers.raw
    )


@pytest.mark.parametrize(
    "change",
    ["signature", "kid", "audience", "resource", "action", "payload", "extra"],
)
@pytest.mark.asyncio
async def test_bank_token_rejects_untrusted_or_inexact_claims(
    tmp_path: Path, change: str
) -> None:
    private_key, _public_key = generate_keypair("platform-task-board", tmp_path)
    rogue_private, _rogue_public = generate_keypair("rogue", tmp_path)
    expected = {"escrow_id": "escrow-1", "recipient_account_id": "agent-1"}
    payload: dict[str, object] = dict(expected)
    if change == "payload":
        payload["escrow_id"] = "escrow-other"
    if change == "extra":
        payload["unexpected"] = True
    request = _bank_request(
        rogue_private if change == "signature" else private_key,
        kid="wrong-kid" if change == "kid" else "platform-task-board",
        audience="other" if change == "audience" else "central-bank",
        resource="POST /other" if change == "resource" else "POST /escrow/release",
        action="other" if change == "action" else "escrow_release",
        payload=payload,
    )
    gate = FaultGate(
        method="POST",
        path="/escrow/release",
        request_sha256=None,
        occurrence=1,
        phase="before_forward",
        expected_status=200,
        expected_response={},
        artifact_path=tmp_path / "gate.json",
        authentication=_authentication(
            tmp_path / "platform-task-board.pub",
            mode="bank_token",
            expected_kid="platform-task-board",
            audience="central-bank",
            action="escrow_release",
            expected_payload=expected,
        ),
    )

    with pytest.raises((InvalidSignature, JwsAdmissionError, ValueError)):
        await _exercise(gate, request)
    assert not gate.reached.is_set()
    assert not gate.artifact_path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("freshness", ["expired", "future", "overlong"])
async def test_bank_token_rejects_invalid_freshness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, freshness: str
) -> None:
    private_key, _public_key = generate_keypair("platform-task-board", tmp_path)
    import service_auth.jws as jws

    now = datetime.now(UTC)
    signed_at = (
        now + timedelta(minutes=5)
        if freshness == "future"
        else now - timedelta(minutes=5)
    )
    if freshness == "overlong":
        signed_at = now
    monkeypatch.setattr(jws, "_clock", lambda: signed_at)
    request = _bank_request(
        private_key,
        token_ttl_seconds=31 if freshness == "overlong" else 30,
    )
    monkeypatch.setattr(jws, "_clock", lambda: datetime.now(UTC))
    expected = {"escrow_id": "escrow-1", "recipient_account_id": "agent-1"}
    gate = FaultGate(
        method="POST",
        path="/escrow/release",
        request_sha256=None,
        occurrence=1,
        phase="before_forward",
        expected_status=200,
        expected_response={},
        artifact_path=tmp_path / "gate.json",
        authentication=_authentication(
            tmp_path / "platform-task-board.pub",
            mode="bank_token",
            expected_kid="platform-task-board",
            audience="central-bank",
            action="escrow_release",
            expected_payload=expected,
        ),
    )

    with pytest.raises(JwsAdmissionError):
        await _exercise(gate, request)
    assert not gate.reached.is_set()


@pytest.mark.parametrize("change", ["hash", "uuid", "type", "extra", "duplicate"])
@pytest.mark.asyncio
async def test_gateway_lease_rejects_unbound_or_noncanonical_body(
    tmp_path: Path, change: str
) -> None:
    generate_keypair("gateway-task-board", tmp_path)
    if change == "uuid":
        body = b'{"protocol_version":1,"worker_id":"recovery-not-a-uuid"}'
    elif change == "type":
        body = b'{"protocol_version":true,"worker_id":"' + WORKER_ID.encode() + b'"}'
    elif change == "extra":
        body = (
            b'{"protocol_version":1,"worker_id":"'
            + WORKER_ID.encode()
            + b'","extra":true}'
        )
    elif change == "duplicate":
        body = (
            b'{"protocol_version":1,"protocol_version":1,"worker_id":"'
            + WORKER_ID.encode()
            + b'"}'
        )
    else:
        body = BODY
    request = _gateway_request(tmp_path, body=BODY if change == "hash" else body)
    if change == "hash":
        request = httpx.Request(
            "POST",
            request.url,
            content=b'{"protocol_version":1,"worker_id":"recovery-not-a-uuid"}',
            headers=request.headers,
        )
    gate = _gate(
        tmp_path,
        _authentication(
            tmp_path / "gateway-task-board.pub",
            mode="gateway_lease",
            expected_kid="gateway-task-board",
        ),
    )

    with pytest.raises((JwsAdmissionError, ValueError)):
        await _exercise(gate, request)
    assert not gate.reached.is_set()
    assert not gate.artifact_path.exists()


def test_authentication_configuration_is_exact_and_required_for_dynamic_hash(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError):
        AuthenticationConfig.from_mapping(
            {
                "mode": "gateway_lease",
                "public_key_path": str(tmp_path / "key.pub"),
                "expected_kid": "gateway-task-board",
                "audience": AUDIENCE,
                "action": ACTION,
                "max_lifetime_seconds": 30,
                "clock_skew_seconds": 0,
                "expected_payload": {"protocol_version": 1},
                "extra": True,
            }
        )
    with pytest.raises(ValueError, match="authentication"):
        _gate(tmp_path, None)  # type: ignore[arg-type]


def test_header_hash_preserves_order_duplicates_and_value_bytes() -> None:
    first = canonical_headers_sha256([(b"X-Test", b"one"), (b"x-test", b"two")])
    reordered = canonical_headers_sha256([(b"x-test", b"two"), (b"X-Test", b"one")])
    changed = canonical_headers_sha256([(b"X-Test", b"one"), (b"x-test", b"tw\xf6")])

    assert len(first) == 64
    assert first != reordered
    assert first != changed


@pytest.mark.asyncio
async def test_cli_authentication_is_top_level_and_release_signal_must_be_fresh(
    tmp_path: Path,
) -> None:
    generate_keypair("gateway-task-board", tmp_path)
    release_path = tmp_path / "release"
    release_path.touch()
    config_path = tmp_path / "proxy.json"
    config_path.write_text(
        json.dumps(
            {
                "upstream_url": "http://gateway.test",
                "timeout_seconds": 5,
                "release_poll_seconds": 0.01,
                "release_path": str(release_path),
                "authentication": {
                    "mode": "gateway_lease",
                    "public_key_path": str(tmp_path / "gateway-task-board.pub"),
                    "expected_kid": "gateway-task-board",
                    "audience": AUDIENCE,
                    "action": ACTION,
                    "max_lifetime_seconds": 30,
                    "clock_skew_seconds": 0,
                    "expected_payload": {"protocol_version": 1},
                },
                "gate": {
                    "method": "POST",
                    "path": PATH,
                    "request_sha256": None,
                    "occurrence": 1,
                    "phase": "before_forward",
                    "expected_status": 200,
                    "expected_response": {},
                    "artifact_path": str(tmp_path / "gate.json"),
                },
            },
            sort_keys=True,
        )
    )

    with pytest.raises(ValueError, match="stale release"):
        await _serve(Namespace(config=config_path, fd=-1))


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["timeout_seconds", "release_poll_seconds"])
async def test_cli_rejects_nonfinite_timing(tmp_path: Path, field: str) -> None:
    config: dict[str, object] = {
        "upstream_url": "http://gateway.test",
        "timeout_seconds": 5,
        "release_poll_seconds": 0.01,
        "release_path": str(tmp_path / "release"),
        "gate": {
            "method": "POST",
            "path": PATH,
            "request_sha256": hashlib.sha256(BODY).hexdigest(),
            "occurrence": 1,
            "phase": "before_forward",
            "expected_status": 200,
            "expected_response": {},
            "artifact_path": str(tmp_path / "gate.json"),
        },
    }
    config[field] = float("nan")
    config_path = tmp_path / "proxy.json"
    config_path.write_text(json.dumps(config, sort_keys=True))

    with pytest.raises(ValueError, match="positive and finite"):
        await _serve(Namespace(config=config_path, fd=-1))
