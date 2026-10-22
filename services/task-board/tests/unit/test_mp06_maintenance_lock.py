"""MP-06 maintenance exclusion for the Task Board lifecycle."""

from __future__ import annotations

import asyncio
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

from task_board_service import config as config_module
from task_board_service.config import Settings
from task_board_service.core import lifespan as lifespan_module
from task_board_service.core.lifespan import lifespan


class _Closable:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _Store:
    def close(self) -> None:
        return None


class _PlatformAgent(_Closable):
    agent_id = "a-platform-test"

    async def register(self) -> None:
        return None


class _Factory:
    def __init__(self, **_kwargs: object) -> None:
        pass

    def platform_agent(self) -> _PlatformAgent:
        return _PlatformAgent()


class _DeadlineEvaluator:
    def __init__(self, **_kwargs: object) -> None:
        pass

    async def run_periodic_sweep(self, _interval: int) -> None:
        await asyncio.Event().wait()


class _TaskManager:
    def __init__(self, **_kwargs: object) -> None:
        pass

    def close(self) -> None:
        return None


def _settings(lock_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        service=SimpleNamespace(name="task-board-test", version="0.1.0"),
        server=SimpleNamespace(port=18003),
        logging=SimpleNamespace(level="WARNING", directory=str(lock_path.parent / "logs")),
        database=SimpleNamespace(path=str(lock_path.parent / "task-board.db")),
        identity=SimpleNamespace(
            base_url="http://127.0.0.1:1",
            get_agent_path="/agents",
            verify_jws_path="/agents/verify-jws",
            timeout_seconds=1,
        ),
        central_bank=SimpleNamespace(
            base_url="http://127.0.0.1:1",
            escrow_lock_path="/escrow/lock",
            escrow_release_path="/escrow/release",
            escrow_split_path="/escrow/split",
            timeout_seconds=1,
        ),
        platform=SimpleNamespace(agent_config_path=str(lock_path.parent / "agents.yaml")),
        assets=SimpleNamespace(
            storage_path=str(lock_path.parent / "assets"),
            max_file_size=1024,
            max_files_per_task=1,
        ),
        limits=None,
        db_gateway=SimpleNamespace(
            url="http://127.0.0.1:1",
            timeout_seconds=1,
            signing=object(),
        ),
        deadline_evaluation=SimpleNamespace(
            evaluation_interval_seconds=60, actor_id="system-maintenance-test-deadline"
        ),
        recovery=SimpleNamespace(maintenance_lock_path=lock_path),
        posting_recovery=SimpleNamespace(
            interval_seconds=2,
            batch_size=100,
            diagnostic_limit=100,
        ),
    )


def _patch_dependencies(monkeypatch: pytest.MonkeyPatch, lock_path: Path) -> None:
    monkeypatch.setattr(lifespan_module, "get_settings", lambda: _settings(lock_path))
    monkeypatch.setattr(
        lifespan_module, "get_config_path", lambda: lock_path.parent / "config.yaml"
    )
    monkeypatch.setattr(lifespan_module, "setup_logging", lambda *_args: None)
    monkeypatch.setattr(lifespan_module, "GatewayRequestSigner", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(lifespan_module, "AgentFactory", _Factory)
    monkeypatch.setattr(lifespan_module, "IdentityClient", lambda **_kwargs: _Closable())
    monkeypatch.setattr(lifespan_module, "BankClient", lambda **_kwargs: _Closable())
    monkeypatch.setattr(lifespan_module, "TaskDbClient", lambda **_kwargs: _Store())
    monkeypatch.setattr(lifespan_module, "EscrowCoordinator", lambda **_kwargs: object())
    monkeypatch.setattr(lifespan_module, "TokenValidator", lambda **_kwargs: object())
    monkeypatch.setattr(lifespan_module, "DeadlineEvaluator", _DeadlineEvaluator)
    monkeypatch.setattr(lifespan_module, "AssetManager", lambda **_kwargs: object())
    monkeypatch.setattr(lifespan_module, "TaskManager", _TaskManager)


@pytest.mark.unit
async def test_task_board_holds_shared_maintenance_lock_while_active(
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
                pytest.fail("exclusive maintenance entered while Task Board was active")

    assert exc_info.value.reason == "busy"


@pytest.mark.unit
async def test_task_board_releases_maintenance_lock_after_startup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "maintenance.lock"
    _patch_dependencies(monkeypatch, lock_path)

    class _FailingPlatformAgent(_PlatformAgent):
        async def register(self) -> None:
            with (
                pytest.raises(MaintenanceLockError) as exc_info,
                hold_maintenance_lock(
                    lock_path,
                    mode=MaintenanceLockMode.EXCLUSIVE,
                    blocking=False,
                ),
            ):
                pytest.fail("exclusive maintenance entered during Task Board startup")
            assert exc_info.value.reason == "busy"
            raise RuntimeError("synthetic registration failure")

    failing_agent = _FailingPlatformAgent()

    class _FailingFactory(_Factory):
        def platform_agent(self) -> _FailingPlatformAgent:
            return failing_agent

    monkeypatch.setattr(lifespan_module, "AgentFactory", _FailingFactory)

    with pytest.raises(RuntimeError, match="synthetic registration failure"):
        async with lifespan(FastAPI()):
            pytest.fail("Task Board unexpectedly started")

    assert failing_agent.closed
    with hold_maintenance_lock(
        lock_path,
        mode=MaintenanceLockMode.EXCLUSIVE,
        blocking=False,
    ):
        pass


@pytest.mark.unit
def test_task_board_recovery_configuration_is_required() -> None:
    config_path = Path(__file__).resolve().parents[2] / "config.yaml"
    config = yaml.safe_load(config_path.read_text())
    del config["recovery"]

    with pytest.raises(ValidationError, match="recovery"):
        Settings.model_validate(config)


@pytest.mark.unit
def test_task_board_recovery_path_keeps_symlink_ancestor_lexical(
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
