"""Unit tests for configuration loading."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from db_gateway_service.config import Settings


@pytest.mark.unit
class TestConfig:
    """Test configuration validation."""

    def test_valid_config_loads(self) -> None:
        """Settings can be constructed with valid data."""
        settings = Settings(
            service={"name": "db-gateway", "version": "0.1.0"},
            server={"host": "127.0.0.1", "port": 8006, "log_level": "info"},
            logging={"level": "INFO", "directory": "data/logs", "format": "json"},
            database={
                "path": "data/economy.db",
                "schema_path": "../../docs/specifications/schema.sql",
                "busy_timeout_ms": 5000,
                "journal_mode": "wal",
            },
            request={"max_body_size": 1048576},
            recovery={
                "maintenance_lock_path": "maintenance.lock",
                "backup_root": "backups",
            },
            board_authority={
                "platform_public_key_path": "platform.pub",
                "deadline_actor_id": "system-task-board-config-test",
            },
            auth={
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
                    }
                },
            },
        )
        assert settings.service.name == "db-gateway"
        assert settings.server.port == 8006
        assert settings.database.busy_timeout_ms == 5000

    def test_extra_fields_rejected(self) -> None:
        """Extra fields cause validation errors (ConfigDict extra='forbid')."""
        with pytest.raises(ValidationError):
            Settings(
                service={"name": "db-gateway", "version": "0.1.0", "extra": True},
                server={"host": "127.0.0.1", "port": 8006, "log_level": "info"},
                logging={"level": "INFO", "directory": "data/logs", "format": "json"},
                database={
                    "path": "data/economy.db",
                    "schema_path": "../../docs/specifications/schema.sql",
                    "busy_timeout_ms": 5000,
                    "journal_mode": "wal",
                },
                request={"max_body_size": 1048576},
            )

    def test_production_gateway_policy_matches_caller_token_lifetime(self) -> None:
        """Production callers issue 300-second tokens admitted with 30-second skew."""
        config_path = Path(__file__).resolve().parents[2] / "config.yaml"
        settings = Settings.model_validate(yaml.safe_load(config_path.read_text()))

        assert settings.auth.audience == "db-gateway"
        assert settings.auth.max_lifetime_seconds == 300
        assert settings.auth.clock_skew_seconds == 30

    def test_missing_required_field(self) -> None:
        """Missing required fields cause validation errors."""
        with pytest.raises(ValidationError):
            Settings(
                service={"name": "db-gateway", "version": "0.1.0"},
                server={"host": "127.0.0.1", "port": 8006, "log_level": "info"},
                logging={"level": "INFO", "directory": "data/logs", "format": "json"},
                # database section missing
                request={"max_body_size": 1048576},
            )
