"""Temporary T-114 shared-token baseline and strict-contract reds."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from service_auth import signing
from service_auth.config import AgentConfig
from service_auth.platform import PlatformAgent
from service_auth.signing import TokenExpiredError, verify_jws

FROZEN_NOW = datetime(2030, 1, 1, tzinfo=UTC)
NOW = int(FROZEN_NOW.timestamp())
REGISTERED_KID = "a-platform-registered"


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def make_raw_jws_fixture(
    header: object,
    payload: object,
    private_key: Ed25519PrivateKey,
) -> str:
    """Sign arbitrary JSON header/payload values without production token helpers."""
    header_bytes = json.dumps(header, sort_keys=True, separators=(",", ":")).encode()
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    header_segment = _b64url(header_bytes)
    payload_segment = _b64url(payload_bytes)
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    signature_segment = _b64url(private_key.sign(signing_input))
    return f"{header_segment}.{payload_segment}.{signature_segment}"


@pytest.fixture
def private_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.fixture(autouse=True)
def _frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(signing, "_clock", lambda: FROZEN_NOW)


def _strict_header(**updates: object) -> dict[str, object]:
    header: dict[str, object] = {
        "alg": "EdDSA",
        "typ": "JWT",
        "kid": REGISTERED_KID,
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


def _verify(header: object, payload: object, private_key: Ed25519PrivateKey) -> Any:
    token = make_raw_jws_fixture(header, payload, private_key)
    return verify_jws(token, private_key.public_key())


@pytest.mark.parametrize("header", [None, [], "header", 7])
def test_current_baseline_nonobject_header_crashes_after_authentication(
    header: object,
    private_key: Ed25519PrivateKey,
) -> None:
    with pytest.raises(AttributeError):
        _verify(header, _payload(), private_key)


@pytest.mark.parametrize("payload", [None, [], "payload", 7])
def test_current_baseline_nonobject_payload_is_returned(
    payload: object,
    private_key: Ed25519PrivateKey,
) -> None:
    assert _verify(_strict_header(), payload, private_key) == payload


@pytest.mark.parametrize(
    "header",
    [
        {"typ": "JWT", "kid": REGISTERED_KID, "iat": NOW, "exp": NOW + 300},
        _strict_header(alg="HS256"),
        _strict_header(alg=True),
        _strict_header(alg=1),
        _strict_header(alg=1.5),
        {"alg": "EdDSA", "kid": REGISTERED_KID, "iat": NOW, "exp": NOW + 300},
        _strict_header(typ="NOT-JWT"),
        _strict_header(typ=True),
        _strict_header(typ=1),
        _strict_header(typ=1.5),
        {"alg": "EdDSA", "typ": "JWT", "iat": NOW, "exp": NOW + 300},
        _strict_header(kid=""),
        _strict_header(kid=True),
        _strict_header(kid=1),
        _strict_header(kid=1.5),
        {"alg": "EdDSA", "typ": "JWT", "kid": REGISTERED_KID, "exp": NOW + 300},
        _strict_header(iat=True),
        _strict_header(iat="now"),
        _strict_header(iat=1.5),
        {"alg": "EdDSA", "typ": "JWT", "kid": REGISTERED_KID, "iat": NOW},
        _strict_header(exp="later"),
        _strict_header(exp=float(NOW + 300)),
    ],
)
def test_current_baseline_invalid_or_missing_profile_fields_are_accepted(
    header: dict[str, object],
    private_key: Ed25519PrivateKey,
) -> None:
    assert _verify(header, _payload(), private_key) == _payload()


def test_current_baseline_boolean_exp_is_treated_as_expired_integer(
    private_key: Ed25519PrivateKey,
) -> None:
    with pytest.raises(TokenExpiredError):
        _verify(_strict_header(exp=True), _payload(), private_key)


def test_current_baseline_missing_expiry_is_accepted(
    private_key: Ed25519PrivateKey,
) -> None:
    header = _strict_header()
    del header["exp"]
    assert _verify(header, _payload(), private_key) == _payload()


def test_current_baseline_exact_expiry_is_accepted(
    private_key: Ed25519PrivateKey,
) -> None:
    assert _verify(_strict_header(exp=NOW), _payload(), private_key) == _payload()


def test_current_baseline_future_iat_is_accepted(
    private_key: Ed25519PrivateKey,
) -> None:
    assert _verify(_strict_header(iat=NOW + 301), _payload(), private_key) == _payload()


def test_current_baseline_exp_not_after_iat_is_accepted(
    private_key: Ed25519PrivateKey,
) -> None:
    header = _strict_header(iat=NOW + 300, exp=NOW + 200)
    assert _verify(header, _payload(), private_key) == _payload()


@pytest.mark.parametrize(
    "header",
    [
        _strict_header(exp=NOW),
        _strict_header(iat=NOW + 301),
        _strict_header(iat=NOW + 300, exp=NOW + 200),
    ],
)
def test_current_baseline_temporal_boundaries_are_accepted(
    header: dict[str, object],
    private_key: Ed25519PrivateKey,
) -> None:
    assert _verify(header, _payload(), private_key) == _payload()


@pytest.mark.asyncio
async def test_current_baseline_wrong_local_platform_kid_is_accepted(
    private_key: Ed25519PrivateKey,
) -> None:
    agent = PlatformAgent(
        AgentConfig(
            name="Platform",
            private_key=private_key,
            public_key=private_key.public_key(),
            identity_url="http://identity.test",
            bank_url="http://bank.test",
            task_board_url="http://task-board.test",
            reputation_url="http://reputation.test",
            court_url="http://court.test",
            token_ttl_seconds=300,
        )
    )
    agent.agent_id = REGISTERED_KID
    token = make_raw_jws_fixture(
        _strict_header(kid="a-different-platform"),
        _payload(),
        private_key,
    )
    try:
        assert agent.validate_certificate(token) == _payload()
    finally:
        await agent.close()


def test_current_baseline_strict_shaped_control_is_accepted(
    private_key: Ed25519PrivateKey,
) -> None:
    assert _verify(_strict_header(), _payload(), private_key) == _payload()


@pytest.mark.parametrize("header", [None, [], "header", 7])
def test_required_contract_nonobject_header_is_rejected_as_invalid_jws(
    header: object,
    private_key: Ed25519PrivateKey,
) -> None:
    with pytest.raises(ValueError):
        _verify(header, _payload(), private_key)


@pytest.mark.parametrize("payload", [None, [], "payload", 7])
def test_required_contract_nonobject_payload_is_rejected_as_invalid_jws(
    payload: object,
    private_key: Ed25519PrivateKey,
) -> None:
    with pytest.raises(ValueError):
        _verify(_strict_header(), payload, private_key)


@pytest.mark.parametrize(
    "header",
    [
        {"typ": "JWT", "kid": REGISTERED_KID, "iat": NOW, "exp": NOW + 300},
        _strict_header(alg="HS256"),
        _strict_header(alg=True),
        _strict_header(alg=1),
        _strict_header(alg=1.5),
        {"alg": "EdDSA", "kid": REGISTERED_KID, "iat": NOW, "exp": NOW + 300},
        _strict_header(typ="NOT-JWT"),
        _strict_header(typ=True),
        _strict_header(typ=1),
        _strict_header(typ=1.5),
        {"alg": "EdDSA", "typ": "JWT", "iat": NOW, "exp": NOW + 300},
        _strict_header(kid=""),
        _strict_header(kid=True),
        _strict_header(kid=1),
        _strict_header(kid=1.5),
        {"alg": "EdDSA", "typ": "JWT", "kid": REGISTERED_KID, "exp": NOW + 300},
        _strict_header(iat=True),
        _strict_header(iat="now"),
        _strict_header(iat=1.5),
        {"alg": "EdDSA", "typ": "JWT", "kid": REGISTERED_KID, "iat": NOW},
        _strict_header(exp=True),
        _strict_header(exp="later"),
        _strict_header(exp=float(NOW + 300)),
    ],
)
def test_required_contract_invalid_or_missing_profile_field_is_rejected(
    header: dict[str, object],
    private_key: Ed25519PrivateKey,
) -> None:
    with pytest.raises(ValueError):
        _verify(header, _payload(), private_key)


def test_required_contract_exact_expiry_is_rejected(
    private_key: Ed25519PrivateKey,
) -> None:
    with pytest.raises(TokenExpiredError):
        _verify(_strict_header(exp=NOW), _payload(), private_key)


@pytest.mark.parametrize(
    "header",
    [
        _strict_header(iat=NOW + 301),
        _strict_header(iat=NOW + 300, exp=NOW + 200),
    ],
)
def test_required_contract_invalid_temporal_relation_is_rejected(
    header: dict[str, object],
    private_key: Ed25519PrivateKey,
) -> None:
    with pytest.raises(ValueError):
        _verify(header, _payload(), private_key)


@pytest.mark.asyncio
async def test_required_contract_wrong_local_platform_kid_is_rejected(
    private_key: Ed25519PrivateKey,
) -> None:
    agent = PlatformAgent(
        AgentConfig(
            name="Platform",
            private_key=private_key,
            public_key=private_key.public_key(),
            identity_url="http://identity.test",
            bank_url="http://bank.test",
            task_board_url="http://task-board.test",
            reputation_url="http://reputation.test",
            court_url="http://court.test",
            token_ttl_seconds=300,
        )
    )
    agent.agent_id = REGISTERED_KID
    token = make_raw_jws_fixture(
        _strict_header(kid="a-different-platform"),
        _payload(),
        private_key,
    )
    try:
        with pytest.raises(ValueError):
            agent.validate_certificate(token)
    finally:
        await agent.close()
