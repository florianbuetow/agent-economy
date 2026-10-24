"""Unit tests for TokenValidator."""

from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock

import pytest
from cryptography.exceptions import InvalidSignature
from service_auth.jws import JwsProtectedHeader, VerifiedJws
from service_commons.exceptions import ServiceError

from task_board_service.services.token_validator import TokenValidator
from tests.helpers import generate_keypair, make_jws_token


def _platform_mock(
    *,
    return_value: object | None = None,
    side_effect: object | None = None,
) -> MagicMock:
    """Build a platform agent mock with validate_certificate configured."""
    platform = MagicMock()
    if side_effect is not None:
        platform.validate_certificate = MagicMock(side_effect=side_effect)
    elif return_value is not None:
        platform.validate_certificate = MagicMock(return_value=_verified(return_value))
    else:
        platform.validate_certificate = MagicMock(return_value=_verified({"action": "create_task"}))
    return platform


def _verified(payload: object) -> VerifiedJws:
    assert isinstance(payload, dict)
    return VerifiedJws(
        header=JwsProtectedHeader(
            alg="EdDSA",
            typ="JWT",
            kid="a-agent",
            iat=1,
            exp=2,
            aud="task-board",
            resource="POST /tasks",
        ),
        payload=payload,
    )


class _Identity:
    def __init__(self, platform: MagicMock) -> None:
        self._platform = platform

    async def verify_jws(self, token: str) -> VerifiedJws:
        return self._platform.validate_certificate(token)


def _validator(platform: MagicMock) -> TokenValidator:
    return TokenValidator(platform_agent=platform, identity_client=_Identity(platform))  # type: ignore[arg-type]


@pytest.mark.unit
async def test_validate_jws_token_empty_token() -> None:
    """Empty token raises invalid_jws."""
    mock_platform = _platform_mock()
    validator = _validator(mock_platform)

    with pytest.raises(ServiceError) as exc_info:
        await validator.validate_jws_token("", "create_task", expected_resource="POST /tasks")

    assert exc_info.value.error == "invalid_jws"


@pytest.mark.unit
async def test_validate_jws_token_wrong_format() -> None:
    """Non-three-part token raises invalid_jws."""
    mock_platform = _platform_mock()
    validator = _validator(mock_platform)

    with pytest.raises(ServiceError) as exc_info:
        await validator.validate_jws_token(
            "only.two",
            "create_task",
            expected_resource="POST /tasks",
        )

    assert exc_info.value.error == "invalid_jws"


@pytest.mark.unit
async def test_validate_jws_token_identity_unavailable() -> None:
    """Identity transport errors preserve identity_service_unavailable."""
    mock_platform = _platform_mock(
        side_effect=ServiceError("identity_service_unavailable", "unavailable", 502, {})
    )
    validator = _validator(mock_platform)
    private_key, _public_key = generate_keypair()
    token = make_jws_token(private_key, "a-agent", {"action": "create_task"})

    with pytest.raises(ServiceError) as exc_info:
        await validator.validate_jws_token(token, "create_task", expected_resource="POST /tasks")

    assert exc_info.value.error == "identity_service_unavailable"
    assert exc_info.value.status_code == 502


@pytest.mark.unit
async def test_validate_jws_token_identity_service_error() -> None:
    """ServiceError from platform verification is wrapped as unavailable."""
    expected = ServiceError("identity_service_unavailable", "fail", 502, {})
    mock_platform = _platform_mock(side_effect=expected)
    validator = _validator(mock_platform)
    private_key, _public_key = generate_keypair()
    token = make_jws_token(private_key, "a-agent", {"action": "create_task"})

    with pytest.raises(ServiceError) as exc_info:
        await validator.validate_jws_token(token, "create_task", expected_resource="POST /tasks")

    assert exc_info.value.error == "identity_service_unavailable"
    assert exc_info.value.status_code == 502


@pytest.mark.unit
async def test_validate_jws_token_forbidden_tampered() -> None:
    """Payload tamper marker raises forbidden."""
    mock_platform = _platform_mock(side_effect=InvalidSignature())
    validator = _validator(mock_platform)
    private_key, _public_key = generate_keypair()
    token = make_jws_token(private_key, "a-agent", {"action": "create_task"})

    with pytest.raises(ServiceError) as exc_info:
        await validator.validate_jws_token(token, "create_task", expected_resource="POST /tasks")

    assert exc_info.value.error == "forbidden"
    assert exc_info.value.status_code == 403


@pytest.mark.unit
async def test_validate_jws_token_missing_action() -> None:
    """Missing action in payload raises invalid_payload."""
    mock_platform = _platform_mock(
        side_effect=ServiceError("invalid_payload", "missing action", 400, {})
    )
    validator = _validator(mock_platform)
    private_key, _public_key = generate_keypair()
    token = make_jws_token(private_key, "a-agent", {"action": "create_task"})

    with pytest.raises(ServiceError) as exc_info:
        await validator.validate_jws_token(token, "create_task", expected_resource="POST /tasks")

    assert exc_info.value.error == "invalid_payload"


@pytest.mark.unit
async def test_validate_jws_token_wrong_action() -> None:
    """Unexpected action raises invalid_payload."""
    mock_platform = _platform_mock(return_value={"action": "submit_bid"})
    validator = _validator(mock_platform)
    private_key, _public_key = generate_keypair()
    token = make_jws_token(
        private_key,
        "a-agent",
        {"action": "submit_bid"},
        resource="POST /tasks",
    )

    with pytest.raises(ServiceError) as exc_info:
        await validator.validate_jws_token(token, "create_task", expected_resource="POST /tasks")

    assert exc_info.value.error == "invalid_payload"


@pytest.mark.unit
async def test_validate_jws_token_valid_single_action() -> None:
    """Matching single action returns payload with signer id."""
    mock_platform = _platform_mock(return_value={"action": "create_task", "x": 1})
    validator = _validator(mock_platform)
    private_key, _public_key = generate_keypair()
    token = make_jws_token(private_key, "a-agent", {"action": "create_task"})

    result = await validator.validate_jws_token(
        token,
        "create_task",
        expected_resource="POST /tasks",
    )

    assert result["action"] == "create_task"
    assert result["_signer_id"] == "a-agent"


@pytest.mark.unit
async def test_validate_jws_token_valid_tuple_action() -> None:
    """Matching one action in tuple succeeds."""
    mock_platform = _platform_mock(return_value={"action": "file_dispute"})
    validator = _validator(mock_platform)
    private_key, _public_key = generate_keypair()
    token = make_jws_token(
        private_key,
        "a-agent",
        {"action": "file_dispute"},
        resource="POST /tasks",
    )

    result = await validator.validate_jws_token(
        token,
        ("dispute_task", "file_dispute"),
        expected_resource="POST /tasks",
    )

    assert result["action"] == "file_dispute"
    assert result["_signer_id"] == "a-agent"


@pytest.mark.unit
def test_decode_escrow_token_payload_valid() -> None:
    """Valid escrow token payload is decoded."""
    mock_platform = _platform_mock()
    validator = _validator(mock_platform)
    private_key, _public_key = generate_keypair()
    token = make_jws_token(
        private_key,
        "a-agent",
        {"action": "escrow_lock", "task_id": "t-1", "amount": 100},
        audience="central-bank",
        resource="POST /escrow/lock",
    )

    result = validator.decode_escrow_token_payload(token)

    assert result == {"action": "escrow_lock", "amount": 100, "task_id": "t-1"}


@pytest.mark.unit
def test_decode_escrow_token_payload_wrong_format() -> None:
    """Token without three parts raises invalid_jws."""
    mock_platform = _platform_mock()
    validator = _validator(mock_platform)

    with pytest.raises(ServiceError) as exc_info:
        validator.decode_escrow_token_payload("only.two")

    assert exc_info.value.error == "invalid_jws"


@pytest.mark.unit
def test_decode_escrow_token_payload_invalid_base64() -> None:
    """Invalid payload base64 raises invalid_jws."""
    mock_platform = _platform_mock()
    validator = _validator(mock_platform)

    with pytest.raises(ServiceError) as exc_info:
        validator.decode_escrow_token_payload("a.%%%%.c")

    assert exc_info.value.error == "invalid_jws"


@pytest.mark.unit
def test_decode_escrow_token_payload_invalid_json() -> None:
    """Non-JSON payload bytes raise invalid_jws."""
    mock_platform = _platform_mock()
    validator = _validator(mock_platform)
    payload = base64.urlsafe_b64encode(b"not-json").rstrip(b"=").decode()

    with pytest.raises(ServiceError) as exc_info:
        validator.decode_escrow_token_payload(f"a.{payload}.c")

    assert exc_info.value.error == "invalid_jws"


@pytest.mark.unit
def test_decode_escrow_token_payload_not_object() -> None:
    """JSON payload that is not an object raises invalid_jws."""
    mock_platform = _platform_mock()
    validator = _validator(mock_platform)
    payload = base64.urlsafe_b64encode(json.dumps([1, 2, 3]).encode()).rstrip(b"=").decode()

    with pytest.raises(ServiceError) as exc_info:
        validator.decode_escrow_token_payload(f"a.{payload}.c")

    assert exc_info.value.error == "invalid_jws"


@pytest.mark.unit
async def test_validate_jws_token_invalid_signature() -> None:
    """Invalid signatures from platform verification raise forbidden."""
    mock_platform = _platform_mock(side_effect=InvalidSignature())
    validator = _validator(mock_platform)
    private_key, _public_key = generate_keypair()
    token = make_jws_token(private_key, "a-agent", {"action": "create_task"})

    with pytest.raises(ServiceError) as exc_info:
        await validator.validate_jws_token(token, "create_task", expected_resource="POST /tasks")

    assert exc_info.value.error == "forbidden"
    assert exc_info.value.status_code == 403
