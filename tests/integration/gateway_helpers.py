"""Helpers for in-process DB Gateway test clients."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import httpx


def _write_test_config(db_path: str, schema_path: str) -> str:
    config_content = f"""
service:
  name: "db-gateway-test"
  version: "0.1.0"

server:
  host: "127.0.0.1"
  port: 18007
  log_level: "warning"

logging:
  level: "WARNING"
  directory: "data/logs"
  format: "json"

database:
  path: "{db_path}"
  schema_path: "{schema_path}"
  busy_timeout_ms: 5000
  journal_mode: "wal"

request:
  max_body_size: 1048576
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as file_handle:
        file_handle.write(config_content)
        return file_handle.name


def create_gateway_client(db_path: str) -> httpx.AsyncClient:
    """Create an async client that talks to an in-process DB Gateway app."""
    schema_path = str(Path(__file__).parent.parent.parent / "docs" / "specifications" / "schema.sql")
    config_path = _write_test_config(db_path, schema_path)

    with patch.dict(os.environ, {"CONFIG_PATH": config_path}):
        from db_gateway_service.config import clear_settings_cache, get_settings
        from db_gateway_service.core.state import get_app_state, init_app_state, reset_app_state
        from db_gateway_service.services.db_reader import DbReader
        from db_gateway_service.services.db_writer import DbWriter

        clear_settings_cache()
        settings = get_settings()

        try:
            existing_state = get_app_state()
            if existing_state.db_writer is not None:
                existing_state.db_writer.close()
        except RuntimeError:
            pass

        reset_app_state()
        state = init_app_state()
        schema_sql = Path(settings.database.schema_path).read_text()
        state.db_writer = DbWriter(
            db_path=settings.database.path,
            busy_timeout_ms=settings.database.busy_timeout_ms,
            journal_mode=settings.database.journal_mode,
            schema_sql=schema_sql,
        )
        state.db_reader = DbReader(db=state.db_writer._db)

        from db_gateway_service.app import create_app

        app = create_app()

    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://gateway-test")
    setattr(client, "_test_config_path", config_path)
    return client
