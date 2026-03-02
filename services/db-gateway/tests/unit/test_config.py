"""Unit tests for configuration loading."""

from __future__ import annotations

import pytest
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
