"""Unit tests for shared router validation helpers."""

from __future__ import annotations

import pytest
from service_commons.exceptions import ServiceError

from task_board_service.routers.validation import (
    extract_bearer_token,
    extract_token,
    parse_json_body,
)


@pytest.mark.unit
def test_parse_json_body_valid_object() -> None:
    """Returns parsed dict for valid JSON object."""
    result = parse_json_body(b'{"token":"abc"}')
    assert result == {"token": "abc"}


@pytest.mark.unit
def test_parse_json_body_invalid_json() -> None:
    """Raises INVALID_JSON when body is malformed JSON."""
    with pytest.raises(ServiceError) as exc_info:
        parse_json_body(b"{")
    assert exc_info.value.error == "INVALID_JSON"
    assert exc_info.value.status_code == 400


@pytest.mark.unit
def test_parse_json_body_non_object() -> None:
    """Raises INVALID_JSON when JSON is not an object."""
    with pytest.raises(ServiceError) as exc_info:
        parse_json_body(b'["not", "object"]')
    assert exc_info.value.error == "INVALID_JSON"
    assert exc_info.value.status_code == 400


@pytest.mark.unit
def test_parse_json_body_empty() -> None:
    """Raises INVALID_JSON for empty request body."""
    with pytest.raises(ServiceError) as exc_info:
        parse_json_body(b"")
    assert exc_info.value.error == "INVALID_JSON"
    assert exc_info.value.status_code == 400


@pytest.mark.unit
def test_extract_token_valid() -> None:
    """Returns token string when field is valid."""
    result = extract_token({"token": "abc"}, "token")
    assert result == "abc"


@pytest.mark.unit
def test_extract_token_missing_field() -> None:
    """Raises INVALID_JWS when required field is missing."""
    with pytest.raises(ServiceError) as exc_info:
        extract_token({}, "token")
    assert exc_info.value.error == "INVALID_JWS"
    assert exc_info.value.status_code == 400


@pytest.mark.unit
def test_extract_token_null_value() -> None:
    """Raises INVALID_JWS when field is null."""
    with pytest.raises(ServiceError) as exc_info:
        extract_token({"token": None}, "token")
    assert exc_info.value.error == "INVALID_JWS"
    assert exc_info.value.status_code == 400


@pytest.mark.unit
def test_extract_token_not_string() -> None:
    """Raises INVALID_JWS when field value is not a string."""
    with pytest.raises(ServiceError) as exc_info:
        extract_token({"token": 123}, "token")
    assert exc_info.value.error == "INVALID_JWS"
    assert exc_info.value.status_code == 400


@pytest.mark.unit
def test_extract_token_empty_string() -> None:
    """Raises INVALID_JWS when field value is empty."""
    with pytest.raises(ServiceError) as exc_info:
        extract_token({"token": ""}, "token")
    assert exc_info.value.error == "INVALID_JWS"
    assert exc_info.value.status_code == 400


@pytest.mark.unit
def test_extract_bearer_token_required_valid() -> None:
    """Returns token from valid Bearer header when required."""
    result = extract_bearer_token("Bearer abc", required=True)
    assert result == "abc"


@pytest.mark.unit
def test_extract_bearer_token_required_missing() -> None:
    """Raises INVALID_JWS when Authorization header is missing and required."""
    with pytest.raises(ServiceError) as exc_info:
        extract_bearer_token(None, required=True)
    assert exc_info.value.error == "INVALID_JWS"
    assert exc_info.value.status_code == 400


@pytest.mark.unit
def test_extract_bearer_token_required_wrong_scheme() -> None:
    """Raises INVALID_JWS for non-Bearer scheme when required."""
    with pytest.raises(ServiceError) as exc_info:
        extract_bearer_token("Basic abc", required=True)
    assert exc_info.value.error == "INVALID_JWS"
    assert exc_info.value.status_code == 400


@pytest.mark.unit
def test_extract_bearer_token_required_empty_token() -> None:
    """Raises INVALID_JWS for empty Bearer token when required."""
    with pytest.raises(ServiceError) as exc_info:
        extract_bearer_token("Bearer ", required=True)
    assert exc_info.value.error == "INVALID_JWS"
    assert exc_info.value.status_code == 400


@pytest.mark.unit
def test_extract_bearer_token_optional_valid() -> None:
    """Returns token from valid optional Bearer header."""
    result = extract_bearer_token("Bearer abc", required=False)
    assert result == "abc"


@pytest.mark.unit
def test_extract_bearer_token_optional_missing() -> None:
    """Returns None when optional Authorization header is absent."""
    result = extract_bearer_token(None, required=False)
    assert result is None


@pytest.mark.unit
def test_extract_bearer_token_optional_wrong_scheme() -> None:
    """Raises INVALID_JWS for wrong scheme even when optional."""
    with pytest.raises(ServiceError) as exc_info:
        extract_bearer_token("Basic abc", required=False)
    assert exc_info.value.error == "INVALID_JWS"
    assert exc_info.value.status_code == 400
