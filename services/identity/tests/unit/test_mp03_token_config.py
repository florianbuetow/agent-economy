"""Strict Identity token-policy configuration tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from identity_service.config import Settings


def _config() -> dict[str, object]:
    return {
        "service": {"name": "identity", "version": "0.1.0"},
        "server": {"host": "127.0.0.1", "port": 8001, "log_level": "info"},
        "logging": {"level": "INFO", "directory": "data/logs"},
        "crypto": {
            "public_key_prefix": "ed25519:",
            "public_key_bytes": 32,
            "signature_bytes": 64,
        },
        "request": {"max_body_size": 1572864},
        "token_validation": {
            "max_lifetime_seconds": 300,
            "clock_skew_seconds": 30,
        },
        "registration": {"reserved_names": ["platform"], "trusted_key_files": []},
        "db_gateway": {"url": "http://localhost:8007", "timeout_seconds": 10},
    }


@pytest.mark.unit
def test_token_policy_configuration_is_required() -> None:
    config = _config()
    del config["token_validation"]
    with pytest.raises(ValidationError):
        Settings.model_validate(config)


@pytest.mark.unit
@pytest.mark.parametrize(
    "field,value",
    [
        ("max_lifetime_seconds", 0),
        ("max_lifetime_seconds", -1),
        ("max_lifetime_seconds", True),
        ("max_lifetime_seconds", 300.0),
        ("clock_skew_seconds", -1),
        ("clock_skew_seconds", True),
        ("clock_skew_seconds", 30.0),
    ],
)
def test_token_policy_configuration_rejects_invalid_values(field: str, value: object) -> None:
    config = _config()
    token_validation = config["token_validation"]
    assert isinstance(token_validation, dict)
    token_validation[field] = value
    with pytest.raises(ValidationError):
        Settings.model_validate(config)


@pytest.mark.unit
def test_registration_configuration_is_required() -> None:
    config = _config()
    del config["registration"]
    with pytest.raises(ValidationError):
        Settings.model_validate(config)
