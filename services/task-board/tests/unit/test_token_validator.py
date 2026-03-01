"""Unit tests for TokenValidator."""

from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock

import pytest
from service_commons.exceptions import ServiceError

from task_board_service.services.token_validator import TokenValidator
from tests.helpers import generate_keypair, make_jws_token


@pytest.mark.unit
async def test_validate_jws_token_empty_token() -> None:
    """Empty token raises INVALID_JWS."""
    mock_identity = AsyncMock()
    validator = TokenValidator(identity_client=mock_identity)

    with pytest.raises(ServiceError) as exc_info:
        await validator.validate_jws_token("", "create_task")

    assert exc_info.value.error == "INVALID_JWS"


@pytest.mark.unit
async def test_validate_jws_token_wrong_format() -> None:
    """Non-three-part token raises INVALID_JWS."""
    mock_identity = AsyncMock()
    validator = TokenValidator(identity_client=mock_identity)

    with pytest.raises(ServiceError) as exc_info:
        await validator.validate_jws_token("only.two", "create_task")

    assert exc_info.value.error == "INVALID_JWS"


@pytest.mark.unit
async def test_validate_jws_token_identity_unavailable() -> None:
    """Connection errors from Identity are wrapped as IDENTITY_SERVICE_UNAVAILABLE."""
    mock_identity = AsyncMock()
    mock_identity.verify_jws = AsyncMock(side_effect=ConnectionError("unavailable"))
    validator = TokenValidator(identity_client=mock_identity)
    private_key, _public_key = generate_keypair()
    token = make_jws_token(private_key, "a-agent", {"action": "create_task"})

    with pytest.raises(ServiceError) as exc_info:
        await validator.validate_jws_token(token, "create_task")

    assert exc_info.value.error == "IDENTITY_SERVICE_UNAVAILABLE"
    assert exc_info.value.status_code == 502


@pytest.mark.unit
async def test_validate_jws_token_identity_service_error() -> None:
    """ServiceError from Identity is propagated unchanged."""
    expected = ServiceError("IDENTITY_SERVICE_UNAVAILABLE", "fail", 502, {})
    mock_identity = AsyncMock()
    mock_identity.verify_jws = AsyncMock(side_effect=expected)
    validator = TokenValidator(identity_client=mock_identity)
    private_key, _public_key = generate_keypair()
    token = make_jws_token(private_key, "a-agent", {"action": "create_task"})

    with pytest.raises(ServiceError) as exc_info:
        await validator.validate_jws_token(token, "create_task")

    assert exc_info.value is expected


@pytest.mark.unit
async def test_validate_jws_token_forbidden_tampered() -> None:
    """Payload tamper marker raises FORBIDDEN."""
    mock_identity = AsyncMock()
    mock_identity.verify_jws = AsyncMock(
        return_value={
            "agent_id": "a-agent",
            "payload": {"action": "create_task", "_tampered": True},
        }
    )
    validator = TokenValidator(identity_client=mock_identity)
    private_key, _public_key = generate_keypair()
    token = make_jws_token(private_key, "a-agent", {"action": "create_task"})

    with pytest.raises(ServiceError) as exc_info:
        await validator.validate_jws_token(token, "create_task")

    assert exc_info.value.error == "FORBIDDEN"
    assert exc_info.value.status_code == 403


@pytest.mark.unit
async def test_validate_jws_token_missing_action() -> None:
    """Missing action in payload raises INVALID_PAYLOAD."""
    mock_identity = AsyncMock()
    mock_identity.verify_jws = AsyncMock(return_value={"agent_id": "a-agent", "payload": {}})
    validator = TokenValidator(identity_client=mock_identity)
    private_key, _public_key = generate_keypair()
    token = make_jws_token(private_key, "a-agent", {"action": "create_task"})

    with pytest.raises(ServiceError) as exc_info:
        await validator.validate_jws_token(token, "create_task")

    assert exc_info.value.error == "INVALID_PAYLOAD"


@pytest.mark.unit
async def test_validate_jws_token_wrong_action() -> None:
    """Unexpected action raises INVALID_PAYLOAD."""
    mock_identity = AsyncMock()
    mock_identity.verify_jws = AsyncMock(
        return_value={"agent_id": "a-agent", "payload": {"action": "submit_bid"}}
    )
    validator = TokenValidator(identity_client=mock_identity)
    private_key, _public_key = generate_keypair()
    token = make_jws_token(private_key, "a-agent", {"action": "submit_bid"})

    with pytest.raises(ServiceError) as exc_info:
        await validator.validate_jws_token(token, "create_task")

    assert exc_info.value.error == "INVALID_PAYLOAD"


@pytest.mark.unit
async def test_validate_jws_token_valid_single_action() -> None:
    """Matching single action returns payload with signer id."""
    mock_identity = AsyncMock()
    mock_identity.verify_jws = AsyncMock(
        return_value={"agent_id": "a-agent", "payload": {"action": "create_task", "x": 1}}
    )
    validator = TokenValidator(identity_client=mock_identity)
    private_key, _public_key = generate_keypair()
    token = make_jws_token(private_key, "a-agent", {"action": "create_task"})

    result = await validator.validate_jws_token(token, "create_task")

    assert result["action"] == "create_task"
    assert result["_signer_id"] == "a-agent"


@pytest.mark.unit
async def test_validate_jws_token_valid_tuple_action() -> None:
    """Matching one action in tuple succeeds."""
    mock_identity = AsyncMock()
    mock_identity.verify_jws = AsyncMock(
        return_value={"agent_id": "a-agent", "payload": {"action": "file_dispute"}}
    )
    validator = TokenValidator(identity_client=mock_identity)
    private_key, _public_key = generate_keypair()
    token = make_jws_token(private_key, "a-agent", {"action": "file_dispute"})

    result = await validator.validate_jws_token(token, ("dispute_task", "file_dispute"))

    assert result["action"] == "file_dispute"
    assert result["_signer_id"] == "a-agent"


@pytest.mark.unit
def test_decode_escrow_token_payload_valid() -> None:
    """Valid escrow token payload is decoded."""
    mock_identity = AsyncMock()
    validator = TokenValidator(identity_client=mock_identity)
    private_key, _public_key = generate_keypair()
    token = make_jws_token(private_key, "a-agent", {"task_id": "t-1", "amount": 100})

    result = validator.decode_escrow_token_payload(token)

    assert result == {"amount": 100, "task_id": "t-1"}


@pytest.mark.unit
def test_decode_escrow_token_payload_wrong_format() -> None:
    """Token without three parts raises INVALID_JWS."""
    mock_identity = AsyncMock()
    validator = TokenValidator(identity_client=mock_identity)

    with pytest.raises(ServiceError) as exc_info:
        validator.decode_escrow_token_payload("only.two")

    assert exc_info.value.error == "INVALID_JWS"


@pytest.mark.unit
def test_decode_escrow_token_payload_invalid_base64() -> None:
    """Invalid payload base64 raises INVALID_JWS."""
    mock_identity = AsyncMock()
    validator = TokenValidator(identity_client=mock_identity)

    with pytest.raises(ServiceError) as exc_info:
        validator.decode_escrow_token_payload("a.%%%%.c")

    assert exc_info.value.error == "INVALID_JWS"


@pytest.mark.unit
def test_decode_escrow_token_payload_invalid_json() -> None:
    """Non-JSON payload bytes raise INVALID_JWS."""
    mock_identity = AsyncMock()
    validator = TokenValidator(identity_client=mock_identity)
    payload = base64.urlsafe_b64encode(b"not-json").rstrip(b"=").decode()

    with pytest.raises(ServiceError) as exc_info:
        validator.decode_escrow_token_payload(f"a.{payload}.c")

    assert exc_info.value.error == "INVALID_JWS"


@pytest.mark.unit
def test_decode_escrow_token_payload_not_object() -> None:
    """JSON payload that is not an object raises INVALID_JWS."""
    mock_identity = AsyncMock()
    validator = TokenValidator(identity_client=mock_identity)
    payload = base64.urlsafe_b64encode(json.dumps([1, 2, 3]).encode()).rstrip(b"=").decode()

    with pytest.raises(ServiceError) as exc_info:
        validator.decode_escrow_token_payload(f"a.{payload}.c")

    assert exc_info.value.error == "INVALID_JWS"
