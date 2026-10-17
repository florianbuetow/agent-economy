"""MP-06 maintenance exclusion for the UI lifecycle."""

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

from ui_service import config as config_module
from ui_service.config import Settings
from ui_service.core import lifespan as lifespan_module
from ui_service.core.lifespan import lifespan


class _Database:
    row_factory = None

    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _UserAgent:
    agent_id = "a-ui-test"
    name = "operator"

    def __init__(self) -> None:
        self.closed = False

    async def register(self) -> None:
        return None

    async def close(self) -> None:
        self.closed = True


class _Factory:
    def __init__(self, **_kwargs: object) -> None:
        pass

    def user_agent(self, _handle: str) -> _UserAgent:
        return _UserAgent()


def _settings(lock_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        service=SimpleNamespace(name="ui-test", version="0.1.0"),
        server=SimpleNamespace(port=18008),
        logging=SimpleNamespace(level="WARNING", directory=str(lock_path.parent / "logs")),
        database=SimpleNamespace(path=str(lock_path.parent / "economy.db")),
        frontend=SimpleNamespace(web_root=str(lock_path.parent / "web")),
        user_agent=SimpleNamespace(
            agent_config_path=str(lock_path.parent / "agents.yaml"),
            handle="operator",
        ),
        recovery=SimpleNamespace(maintenance_lock_path=lock_path),
    )


def _patch_dependencies(monkeypatch: pytest.MonkeyPatch, lock_path: Path) -> None:
    monkeypatch.setattr(lifespan_module, "get_settings", lambda: _settings(lock_path))
    monkeypatch.setattr(
        lifespan_module,
        "get_config_path",
        lambda: lock_path.parent / "config.yaml",
        raising=False,
    )
    monkeypatch.setattr(lifespan_module, "setup_logging", lambda *_args: None)
    monkeypatch.setattr(lifespan_module, "AgentFactory", _Factory)

    async def _connect(*_args: object, **_kwargs: object) -> _Database:
        return _Database()

    monkeypatch.setattr(lifespan_module.aiosqlite, "connect", _connect)


@pytest.mark.unit
async def test_ui_holds_shared_maintenance_lock_while_active(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "maintenance.lock"
    _patch_dependencies(monkeypatch, lock_path)

    async with lifespan(FastAPI()):
        with pytest.raises(MaintenanceLockError) as exc_info:
            with hold_maintenance_lock(
                lock_path,
                mode=MaintenanceLockMode.EXCLUSIVE,
                blocking=False,
            ):
                pytest.fail("exclusive maintenance entered while UI was active")

    assert exc_info.value.reason == "busy"


@pytest.mark.unit
async def test_ui_releases_maintenance_lock_after_startup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "maintenance.lock"
    _patch_dependencies(monkeypatch, lock_path)
    database = _Database()
    user_agent = _UserAgent()

    class _TrackedFactory(_Factory):
        def user_agent(self, _handle: str) -> _UserAgent:
            return user_agent

    async def _connect(*_args: object, **_kwargs: object) -> _Database:
        return database

    class _FailingLogger:
        def info(self, message: str, **_kwargs: object) -> None:
            if message != "Service starting":
                return
            with (
                pytest.raises(MaintenanceLockError) as exc_info,
                hold_maintenance_lock(
                    lock_path,
                    mode=MaintenanceLockMode.EXCLUSIVE,
                    blocking=False,
                ),
            ):
                pytest.fail("exclusive maintenance entered during UI startup")
            assert exc_info.value.reason == "busy"
            raise RuntimeError("synthetic startup logging failure")

        def error(self, _message: str, **_kwargs: object) -> None:
            return None

    monkeypatch.setattr(lifespan_module, "AgentFactory", _TrackedFactory)
    monkeypatch.setattr(lifespan_module.aiosqlite, "connect", _connect)
    monkeypatch.setattr(lifespan_module, "get_logger", lambda _name: _FailingLogger())

    with pytest.raises(RuntimeError, match="synthetic startup logging failure"):
        async with lifespan(FastAPI()):
            pytest.fail("UI unexpectedly started")

    assert database.closed
    assert user_agent.closed
    with hold_maintenance_lock(
        lock_path,
        mode=MaintenanceLockMode.EXCLUSIVE,
        blocking=False,
    ):
        pass


@pytest.mark.unit
def test_ui_recovery_configuration_is_required() -> None:
    config_path = Path(__file__).resolve().parents[2] / "config.yaml"
    config = yaml.safe_load(config_path.read_text())
    del config["recovery"]

    with pytest.raises(ValidationError, match="recovery"):
        Settings.model_validate(config)


@pytest.mark.unit
def test_ui_recovery_path_keeps_symlink_ancestor_lexical(
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
