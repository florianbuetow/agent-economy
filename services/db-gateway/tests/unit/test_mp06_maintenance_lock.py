"""MP-06 maintenance exclusion for the Gateway lifecycle."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi import FastAPI
from pydantic import ValidationError
from service_commons.maintenance_lock import (
    MaintenanceLockError,
    MaintenanceLockMode,
    hold_maintenance_lock,
)

from db_gateway_service import config as config_module
from db_gateway_service.config import Settings
from db_gateway_service.core import lifespan as lifespan_module
from db_gateway_service.core.lifespan import lifespan
from db_gateway_service.schema import SchemaState, load_schema_sql


def _settings(lock_path: Path) -> SimpleNamespace:
    (lock_path.parent / "schema.sql").write_text(load_schema_sql(SchemaState.CURRENT_V1))
    return SimpleNamespace(
        service=SimpleNamespace(name="db-gateway-test", version="0.1.0"),
        server=SimpleNamespace(port=18007),
        logging=SimpleNamespace(level="WARNING", directory=str(lock_path.parent / "logs")),
        database=SimpleNamespace(
            path=str(lock_path.parent / "database" / "economy.db"),
            schema_path=str(lock_path.parent / "schema.sql"),
            busy_timeout_ms=5000,
            journal_mode="wal",
        ),
        recovery=SimpleNamespace(maintenance_lock_path=lock_path, backup_root=Path("backups")),
        board_effect_recovery=SimpleNamespace(current_task_board_caller_kid="gateway-task-board"),
    )


class _Writer:
    def __init__(self, **_kwargs: object) -> None:
        self.connection = object()
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.mark.unit
async def test_gateway_holds_shared_maintenance_lock_before_database_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "maintenance.lock"
    monkeypatch.setattr(lifespan_module, "get_settings", lambda: _settings(lock_path))
    monkeypatch.setattr(lifespan_module, "setup_logging", lambda *_args: None)
    monkeypatch.setattr(lifespan_module, "DbWriter", _Writer)
    monkeypatch.setattr(lifespan_module, "DbReader", lambda **_kwargs: object())

    async with lifespan(FastAPI()):
        with pytest.raises(MaintenanceLockError, match="already held") as exc_info:
            with hold_maintenance_lock(
                lock_path,
                mode=MaintenanceLockMode.EXCLUSIVE,
                blocking=False,
            ):
                pytest.fail("exclusive maintenance entered while Gateway was active")

    assert exc_info.value.reason == "busy"


@pytest.mark.unit
async def test_gateway_releases_maintenance_lock_after_startup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "maintenance.lock"
    monkeypatch.setattr(lifespan_module, "get_settings", lambda: _settings(lock_path))
    monkeypatch.setattr(lifespan_module, "setup_logging", lambda *_args: None)
    writer = _Writer()
    monkeypatch.setattr(lifespan_module, "DbWriter", lambda **_kwargs: writer)

    def _fail_reader(**_kwargs: object) -> object:
        with (
            pytest.raises(MaintenanceLockError) as exc_info,
            hold_maintenance_lock(
                lock_path,
                mode=MaintenanceLockMode.EXCLUSIVE,
                blocking=False,
            ),
        ):
            pytest.fail("exclusive maintenance entered during Gateway startup")
        assert exc_info.value.reason == "busy"
        raise RuntimeError("synthetic reader failure")

    monkeypatch.setattr(lifespan_module, "DbReader", _fail_reader)

    with pytest.raises(RuntimeError, match="synthetic reader failure"):
        async with lifespan(FastAPI()):
            pytest.fail("Gateway unexpectedly started")

    assert writer.closed
    with hold_maintenance_lock(
        lock_path,
        mode=MaintenanceLockMode.EXCLUSIVE,
        blocking=False,
    ):
        pass


@pytest.mark.unit
def test_gateway_recovery_configuration_is_required() -> None:
    config_path = Path(__file__).resolve().parents[2] / "config.yaml"
    config = yaml.safe_load(config_path.read_text())
    del config["recovery"]

    with pytest.raises(ValidationError, match="recovery"):
        Settings.model_validate(config)


@pytest.mark.unit
def test_gateway_backup_root_is_required() -> None:
    config_path = Path(__file__).resolve().parents[2] / "config.yaml"
    config = yaml.safe_load(config_path.read_text())
    del config["recovery"]["backup_root"]

    with pytest.raises(ValidationError, match="backup_root"):
        Settings.model_validate(config)


@pytest.mark.unit
def test_gateway_recovery_path_keeps_symlink_ancestor_lexical(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_parent = tmp_path / "actual"
    actual_parent.mkdir()
    lexical_parent = tmp_path / "configured"
    lexical_parent.symlink_to(actual_parent, target_is_directory=True)
    monkeypatch.setattr(config_module, "get_config_path", lambda: lexical_parent / "config.yaml")

    resolved = config_module.resolve_recovery_path(Path("maintenance.lock"))

    assert resolved == lexical_parent / "maintenance.lock"
    assert resolved != actual_parent / "maintenance.lock"
