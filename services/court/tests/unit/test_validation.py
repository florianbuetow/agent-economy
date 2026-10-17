"""Unit tests for router validation helpers."""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from service_auth.jws import (
    InvalidPayloadError,
    JwsProtectedHeader,
    VerifiedJws,
    create_jws,
    require_context,
)
from service_commons.exceptions import ServiceError

from court_service.routers.validation import (
    extract_jws_token,
    parse_json_body,
    require_non_empty_string,
)


def _verified(action: str) -> VerifiedJws:
    return VerifiedJws(
        header=JwsProtectedHeader(
            alg="EdDSA",
            typ="JWT",
            kid="a-platform",
            iat=1,
            exp=2,
            aud="court",
            resource="POST /disputes/file",
        ),
        payload={"action": action},
    )


@pytest.mark.unit
def test_parse_json_body_valid() -> None:
    """parse_json_body returns parsed object for valid JSON."""
    parsed = parse_json_body(b'{"token":"a.b.c"}')
    assert parsed == {"token": "a.b.c"}


@pytest.mark.unit
def test_parse_json_body_invalid_json() -> None:
    """parse_json_body raises invalid_json for malformed JSON."""
    with pytest.raises(ServiceError) as exc:
        parse_json_body(b"{not-json")
    assert exc.value.error == "invalid_json"


@pytest.mark.unit
def test_parse_json_body_not_object() -> None:
    """parse_json_body rejects non-object JSON values."""
    with pytest.raises(ServiceError) as exc:
        parse_json_body(b'"string"')
    assert exc.value.error == "invalid_json"


@pytest.mark.unit
def test_extract_jws_token_valid() -> None:
    """extract_jws_token returns valid compact JWS string."""
    token = extract_jws_token({"token": "a.b.c"}, "token")
    assert token == "a.b.c"


@pytest.mark.unit
def test_extract_jws_token_missing_field() -> None:
    """extract_jws_token raises invalid_jws when field missing."""
    with pytest.raises(ServiceError) as exc:
        extract_jws_token({}, "token")
    assert exc.value.error == "invalid_jws"


@pytest.mark.unit
def test_extract_jws_token_not_string() -> None:
    """extract_jws_token raises invalid_jws for non-string token."""
    with pytest.raises(ServiceError) as exc:
        extract_jws_token({"token": 123}, "token")
    assert exc.value.error == "invalid_jws"


@pytest.mark.unit
def test_require_action_correct() -> None:
    """Authenticated context accepts a matching action value."""
    require_context(
        _verified("file_dispute"),
        audience="court",
        resource="POST /disputes/file",
        allowed_action="file_dispute",
    )


@pytest.mark.unit
def test_require_action_wrong() -> None:
    """Authenticated context rejects non-matching action value."""
    with pytest.raises(InvalidPayloadError):
        require_context(
            _verified("submit_rebuttal"),
            audience="court",
            resource="POST /disputes/file",
            allowed_action="file_dispute",
        )


@pytest.mark.unit
def test_require_action_missing() -> None:
    """The strict token profile rejects a missing payload action."""
    with pytest.raises(InvalidPayloadError):
        create_jws(
            {"task_id": "t-1"},
            Ed25519PrivateKey.generate(),
            kid="a-platform",
            audience="court",
            resource="POST /disputes/file",
            token_ttl_seconds=60,
        )


@pytest.mark.unit
def test_require_non_empty_string_valid() -> None:
    """require_non_empty_string returns valid string field."""
    value = require_non_empty_string({"task_id": "task-1"}, "task_id")
    assert value == "task-1"


@pytest.mark.unit
def test_require_non_empty_string_missing() -> None:
    """require_non_empty_string rejects missing required field."""
    with pytest.raises(ServiceError) as exc:
        require_non_empty_string({}, "task_id")
    assert exc.value.error == "invalid_payload"


@pytest.mark.unit
def test_require_non_empty_string_empty() -> None:
    """require_non_empty_string rejects empty string field."""
    with pytest.raises(ServiceError) as exc:
        require_non_empty_string({"task_id": "   "}, "task_id")
    assert exc.value.error == "invalid_payload"
