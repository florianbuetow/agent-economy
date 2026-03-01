"""Unit tests for router validation helpers."""

from __future__ import annotations

import pytest
from service_commons.exceptions import ServiceError

from court_service.routers.validation import (
    extract_jws_token,
    parse_json_body,
    require_action,
    require_non_empty_string,
    require_platform_signer,
)


@pytest.mark.unit
def test_parse_json_body_valid() -> None:
    """parse_json_body returns parsed object for valid JSON."""
    parsed = parse_json_body(b'{"token":"a.b.c"}')
    assert parsed == {"token": "a.b.c"}


@pytest.mark.unit
def test_parse_json_body_invalid_json() -> None:
    """parse_json_body raises INVALID_JSON for malformed JSON."""
    with pytest.raises(ServiceError) as exc:
        parse_json_body(b"{not-json")
    assert exc.value.error == "INVALID_JSON"


@pytest.mark.unit
def test_parse_json_body_not_object() -> None:
    """parse_json_body rejects non-object JSON values."""
    with pytest.raises(ServiceError) as exc:
        parse_json_body(b'"string"')
    assert exc.value.error == "INVALID_JSON"


@pytest.mark.unit
def test_extract_jws_token_valid() -> None:
    """extract_jws_token returns valid compact JWS string."""
    token = extract_jws_token({"token": "a.b.c"}, "token")
    assert token == "a.b.c"


@pytest.mark.unit
def test_extract_jws_token_missing_field() -> None:
    """extract_jws_token raises INVALID_JWS when field missing."""
    with pytest.raises(ServiceError) as exc:
        extract_jws_token({}, "token")
    assert exc.value.error == "INVALID_JWS"


@pytest.mark.unit
def test_extract_jws_token_not_string() -> None:
    """extract_jws_token raises INVALID_JWS for non-string token."""
    with pytest.raises(ServiceError) as exc:
        extract_jws_token({"token": 123}, "token")
    assert exc.value.error == "INVALID_JWS"


@pytest.mark.unit
def test_require_action_correct() -> None:
    """require_action accepts matching action value."""
    require_action({"action": "file_dispute"}, "file_dispute")


@pytest.mark.unit
def test_require_action_wrong() -> None:
    """require_action rejects non-matching action value."""
    with pytest.raises(ServiceError) as exc:
        require_action({"action": "submit_rebuttal"}, "file_dispute")
    assert exc.value.error == "INVALID_PAYLOAD"


@pytest.mark.unit
def test_require_action_missing() -> None:
    """require_action rejects missing action value."""
    with pytest.raises(ServiceError) as exc:
        require_action({}, "file_dispute")
    assert exc.value.error == "INVALID_PAYLOAD"


@pytest.mark.unit
def test_require_platform_signer_correct() -> None:
    """require_platform_signer accepts matching platform agent."""
    require_platform_signer({"agent_id": "agent-platform"}, "agent-platform")


@pytest.mark.unit
def test_require_platform_signer_wrong() -> None:
    """require_platform_signer rejects wrong platform agent."""
    with pytest.raises(ServiceError) as exc:
        require_platform_signer({"agent_id": "agent-rogue"}, "agent-platform")
    assert exc.value.error == "FORBIDDEN"


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
    assert exc.value.error == "INVALID_PAYLOAD"


@pytest.mark.unit
def test_require_non_empty_string_empty() -> None:
    """require_non_empty_string rejects empty string field."""
    with pytest.raises(ServiceError) as exc:
        require_non_empty_string({"task_id": "   "}, "task_id")
    assert exc.value.error == "INVALID_PAYLOAD"
