"""MP-07 receiver-owned Task Board actor-authority configuration."""

from __future__ import annotations

import importlib
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError
from service_auth.signing import generate_keypair, load_public_key, public_key_to_b64

from db_gateway_service.app import create_app
from db_gateway_service.config import Settings, clear_settings_cache

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit


def _settings_payload(tmp_path: Path) -> dict[str, object]:
    generate_keypair("gateway-identity", tmp_path)
    generate_keypair("gateway-task-board", tmp_path)
    generate_keypair("platform", tmp_path)
    return {
        "service": {"name": "db-gateway-test", "version": "0.1.0"},
        "server": {"host": "127.0.0.1", "port": 18007, "log_level": "warning"},
        "telemetry": {
            "enabled": False,
            "otlp_endpoint": "http://127.0.0.1:4318",
            "sample_ratio": 1.0,
            "excluded_urls": "health",
        },
        "logging": {
            "level": "WARNING",
            "directory": str(tmp_path / "logs"),
            "format": "json",
        },
        "database": {
            "path": str(tmp_path / "economy.db"),
            "schema_path": str(
                Path(__file__).resolve().parents[4] / "docs/specifications/schema.sql"
            ),
            "busy_timeout_ms": 5000,
            "journal_mode": "wal",
        },
        "request": {"max_body_size": 1048576},
        "recovery": {
            "maintenance_lock_path": str(tmp_path / "maintenance.lock"),
            "backup_root": str(tmp_path / "backups"),
        },
        "auth": {
            "audience": "db-gateway",
            "max_lifetime_seconds": 30,
            "clock_skew_seconds": 0,
            "trusted_callers": {
                "gateway-identity": {
                    "public_key_path": "gateway-identity.pub",
                    "capabilities": [
                        "gateway.identity.read",
                        "gateway.identity.write",
                    ],
                },
                "gateway-task-board": {
                    "public_key_path": "gateway-task-board.pub",
                    "capabilities": ["gateway.board.write"],
                },
            },
        },
        "board_authority": {
            "platform_public_key_path": "platform.pub",
            "deadline_actor_id": "system-task-board-deadline",
        },
        "board_effect_recovery": {
            "current_task_board_caller_kid": "gateway-task-board",
            "max_attempts_per_delivery": 8,
            "initial_backoff_seconds": 2,
            "max_backoff_seconds": 60,
            "lease_duration_seconds": 45,
            "evidence_interval_seconds": 60,
            "gateway_timeout_seconds": 10,
            "bank_timeout_seconds": 10,
        },
    }


def _write_config(tmp_path: Path, payload: dict[str, object]) -> Path:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False))
    return config_path


@pytest.fixture
def configured_gateway(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[Path, dict[str, object]]]:
    payload = _settings_payload(tmp_path)
    config_path = _write_config(tmp_path, payload)
    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    clear_settings_cache()
    yield tmp_path, payload
    clear_settings_cache()


def test_board_authority_configuration_is_required(
    tmp_path: Path,
) -> None:
    payload = _settings_payload(tmp_path)
    del payload["board_authority"]

    with pytest.raises(ValidationError):
        Settings.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value", "remove"),
    [
        ("platform_public_key_path", None, True),
        ("deadline_actor_id", None, True),
        ("platform_public_key_path", "", False),
        ("platform_public_key_path", "   ", False),
        ("deadline_actor_id", "", False),
        ("deadline_actor_id", "   ", False),
        ("deadline_actor_id", " system-task-board-deadline", False),
    ],
)
def test_board_authority_rejects_blank_or_noncanonical_fields(
    field: str,
    value: str | None,
    remove: bool,
) -> None:
    config_module = importlib.import_module("db_gateway_service.config")
    config_type = getattr(config_module, "BoardAuthorityConfig", None)
    if config_type is None:
        pytest.fail("Gateway lacks required BoardAuthorityConfig")
    authority: dict[str, object] = {
        "platform_public_key_path": "platform.pub",
        "deadline_actor_id": "system-task-board-deadline",
    }
    if remove:
        del authority[field]
    else:
        assert value is not None
        authority[field] = value

    with pytest.raises(ValidationError):
        config_type.model_validate(authority)


def test_configured_platform_key_is_loaded_as_canonical_identity_public_key(
    configured_gateway: tuple[Path, dict[str, object]],
) -> None:
    tmp_path, _payload = configured_gateway

    app = create_app()

    authority_module = importlib.import_module("db_gateway_service.board_authority")
    authority_type = getattr(authority_module, "BoardAuthority", None)
    if authority_type is None:
        pytest.fail("Gateway lacks required BoardAuthority")
    expected = f"ed25519:{public_key_to_b64(load_public_key(tmp_path / 'platform.pub'))}"
    authority = app.state.board_authority
    assert authority == authority_type(
        platform_public_key=expected,
        deadline_actor_id="system-task-board-deadline",
    )
    with pytest.raises(FrozenInstanceError):
        authority.deadline_actor_id = "changed"


def test_missing_platform_public_key_fails_application_creation(
    configured_gateway: tuple[Path, dict[str, object]],
) -> None:
    tmp_path, _payload = configured_gateway
    (tmp_path / "platform.pub").unlink()

    with pytest.raises(FileNotFoundError):
        create_app()


def test_malformed_platform_public_key_fails_application_creation(
    configured_gateway: tuple[Path, dict[str, object]],
) -> None:
    tmp_path, _payload = configured_gateway
    (tmp_path / "platform.pub").write_text("not a PEM public key")

    with pytest.raises(ValueError):
        create_app()


def test_platform_key_must_not_reuse_a_trusted_gateway_service_key(
    configured_gateway: tuple[Path, dict[str, object]],
) -> None:
    tmp_path, payload = configured_gateway
    authority = payload["board_authority"]
    assert isinstance(authority, dict)
    authority["platform_public_key_path"] = "gateway-identity.pub"
    _write_config(tmp_path, payload)
    clear_settings_cache()

    with pytest.raises(ValueError, match="shares key material"):
        create_app()


def test_gateway_starts_before_platform_identity_registration(
    configured_gateway: tuple[Path, dict[str, object]],
) -> None:
    _tmp_path, _payload = configured_gateway
    app = create_app()

    with TestClient(app):
        assert app.state.board_authority.platform_public_key.startswith("ed25519:")
