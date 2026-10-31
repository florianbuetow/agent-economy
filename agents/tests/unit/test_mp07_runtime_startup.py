"""T-181 startup composition for durable worker and feeder runtimes."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, Mock

import pytest
import yaml
from service_auth import public_key_to_b64
from service_auth.signing import generate_keypair, load_public_key
from service_commons.maintenance_lock import (
    MaintenanceLockError,
    MaintenanceLockMode,
    hold_existing_maintenance_lock,
)

from base_agent.runtime_config import RuntimeStateConfig, load_runtime_state_config
from base_agent.runtime_intents import FileTaskIntentStore, RuntimeIntentError
from base_agent.runtime_task_commands import RuntimeTaskCommands, runtime_owner_from_agent
from base_agent.worker_factory import MathWorkerBundle, WorkerFactory
from math_worker import __main__ as math_worker_main
from math_worker.config import MathWorkerConfig
from task_feeder import __main__ as task_feeder_main
from task_feeder.config import AcceptanceConfig, TaskFeederConfig

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit

_URLS = {
    "identity_url": "https://identity.startup.example/v1",
    "bank_url": "https://bank.startup.example/v1",
    "task_board_url": "https://task-board.startup.example/v1",
    "reputation_url": "https://reputation.startup.example/v1",
    "court_url": "https://court.startup.example/v1",
}


@dataclass(frozen=True, slots=True)
class _WorkerStartupBundle:
    agent: _FakeAgent
    llm: _FakeLLM
    config: MathWorkerConfig
    profile_name: str
    runtime_config: RuntimeStateConfig


class _FakeAgent:
    def __init__(
        self,
        *,
        handle: str,
        public_key_b64: str,
        registered_agent_id: str,
        events: list[str],
    ) -> None:
        self.name = handle
        self.agent_id: str | None = None
        self.config = SimpleNamespace(**_URLS)
        self._public_key_b64 = public_key_b64
        self._registered_agent_id = registered_agent_id
        self._events = events
        self.closed = False
        self.submit_bid = AsyncMock()
        self.accept_bid = AsyncMock()
        self.submit_deliverable = AsyncMock()
        self.approve_task = AsyncMock()
        self.dispute_task = AsyncMock()
        self.list_tasks = AsyncMock()

    def get_public_key_b64(self) -> str:
        return self._public_key_b64

    async def register(self) -> dict[str, object]:
        self._events.append("register")
        self.agent_id = self._registered_agent_id
        return {"agent_id": self.agent_id}

    async def create_account(self) -> dict[str, object]:
        assert self.agent_id == self._registered_agent_id
        self._events.append("account")
        return {"account_id": self.agent_id}

    async def close(self) -> None:
        self._events.append("agent-close")
        self.closed = True


class _FakeLLM:
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self.closed = False

    async def complete(self, _system: str, _prompt: str) -> object:
        self._events.append("llm")
        return SimpleNamespace(content="unused")

    async def close(self) -> None:
        self._events.append("llm-close")
        self.closed = True


def _write_config(tmp_path: Path) -> tuple[Path, Path]:
    keys_dir = tmp_path / "keys"
    for handle in ("worker-handle", "feeder"):
        generate_keypair(handle, keys_dir)
    roster_path = tmp_path / "roster.yaml"
    roster_path.write_text(
        yaml.safe_dump(
            {
                "agents": {
                    "worker-handle": {"name": "Worker", "type": "worker"},
                    "feeder": {"name": "Feeder", "type": "feeder"},
                }
            }
        )
    )
    config = {
        "platform": dict(_URLS),
        "data": {"keys_dir": str(keys_dir), "roster_path": str(roster_path)},
        "signing": {
            "token_ttl_seconds": 300,
            "max_lifetime_seconds": 300,
            "clock_skew_seconds": 30,
        },
        "runtime_state": {
            "environment_id": "startup-test",
            "directory": str(tmp_path / "runtime-state"),
            "diagnostic_limit": 8,
            "max_record_bytes": 65_536,
        },
        "workers": {
            "actual-profile": {
                "handle": "worker-handle",
                "type": "math_worker",
                "llm": {
                    "base_url": "https://llm.startup.example/v1",
                    "api_key": "test-key",
                    "model_id": "test-model",
                    "temperature": 0.5,
                    "max_tokens": 512,
                },
                "behavior": {
                    "scan_interval_seconds": 1,
                    "poll_interval_seconds": 1,
                    "max_poll_attempts": 1,
                    "error_backoff_seconds": 1,
                    "min_reward": 1,
                    "max_reward": 100,
                },
            }
        },
        "task_feeder": {
            "handle": "feeder",
            "tasks_file": str(tmp_path / "tasks.jsonl"),
            "source_max_bytes": 8_388_608,
            "feed_interval_seconds": 1,
            "max_open_tasks": 1,
            "bidding_deadline_seconds": 30,
            "acceptance_window_seconds": 20,
            "execution_deadline_seconds": 30,
            "review_deadline_seconds": 30,
            "review_interval_seconds": 1,
            "retry_backoff_seconds": 1,
            "auto_approve_on_error": False,
            "base_reward": 10,
            "reward_per_level": 5,
            "shuffle": False,
            "acceptance_after_seconds": 10,
            "acceptance_poll_interval_seconds": 1,
            "min_bids_to_accept": 1,
        },
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    (tmp_path / "tasks.jsonl").write_text("")
    return config_path, keys_dir


def _public_key_b64(keys_dir: Path, handle: str) -> str:
    return public_key_to_b64(load_public_key(keys_dir / f"{handle}.pub"))


def _runtime_config(tmp_path: Path) -> RuntimeStateConfig:
    return RuntimeStateConfig(
        environment_id="startup-test",
        directory=tmp_path / "runtime-state",
        diagnostic_limit=8,
        max_record_bytes=65_536,
    )


def _worker_config() -> MathWorkerConfig:
    return MathWorkerConfig(
        handle="worker-handle",
        scan_interval_seconds=1,
        poll_interval_seconds=1,
        max_poll_attempts=1,
        error_backoff_seconds=1,
        min_reward=1,
        max_reward=100,
    )


def _feeder_config(tmp_path: Path) -> TaskFeederConfig:
    return TaskFeederConfig(
        handle="feeder",
        tasks_file=str(tmp_path / "tasks.jsonl"),
        source_max_bytes=8_388_608,
        feed_interval_seconds=1,
        max_open_tasks=1,
        bidding_deadline_seconds=30,
        acceptance_window_seconds=20,
        execution_deadline_seconds=30,
        review_deadline_seconds=30,
        review_interval_seconds=1,
        retry_backoff_seconds=1,
        auto_approve_on_error=False,
        base_reward=10,
        reward_per_level=5,
        shuffle=False,
    )


def _acceptance_config() -> AcceptanceConfig:
    return AcceptanceConfig(
        acceptance_after_seconds=10,
        acceptance_poll_interval_seconds=1,
        min_bids_to_accept=1,
        bidding_deadline_seconds=30,
    )


def _patch_signal_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    loop_type = type(asyncio.get_running_loop())

    def ignore_signal(*_args: object, **_kwargs: object) -> None:
        pass

    monkeypatch.setattr(loop_type, "add_signal_handler", ignore_signal)


def _assert_lease_busy(lock_path: Path) -> None:
    metadata = lock_path.lstat()
    with (
        pytest.raises(MaintenanceLockError) as raised,
        hold_existing_maintenance_lock(
            lock_path,
            mode=MaintenanceLockMode.EXCLUSIVE,
            blocking=False,
            expected_device=metadata.st_dev,
            expected_inode=metadata.st_ino,
        ),
    ):
        pass
    assert raised.value.reason == "busy"


def _assert_lease_released(lock_path: Path) -> None:
    metadata = lock_path.lstat()
    with hold_existing_maintenance_lock(
        lock_path,
        mode=MaintenanceLockMode.EXCLUSIVE,
        blocking=False,
        expected_device=metadata.st_dev,
        expected_inode=metadata.st_ino,
    ):
        pass


def _no_mutation_calls(agent: _FakeAgent) -> None:
    for method in (
        agent.submit_bid,
        agent.accept_bid,
        agent.submit_deliverable,
        agent.approve_task,
        agent.dispute_task,
    ):
        method.assert_not_awaited()


@pytest.mark.asyncio
async def test_registered_runtime_fixture_replays_empty_store_under_real_lease(
    tmp_path: Path,
) -> None:
    _config_path, keys_dir = _write_config(tmp_path)
    events: list[str] = []
    bundle = _worker_bundle(tmp_path, keys_dir, events)
    await bundle.agent.register()
    await bundle.agent.create_account()
    owner = runtime_owner_from_agent(
        runtime_config=bundle.runtime_config,
        agent=bundle.agent,
        role="math-worker",
        profile=bundle.profile_name,
        handle=bundle.config.handle,
    )
    with FileTaskIntentStore(
        root_directory=bundle.runtime_config.directory,
        owner=owner,
        diagnostic_limit=bundle.runtime_config.diagnostic_limit,
        max_record_bytes=bundle.runtime_config.max_record_bytes,
    ) as store:
        commands = RuntimeTaskCommands(bundle.agent, store)
        assert await commands.replay_unfinished() == ()
        _assert_lease_busy(store.lock_path)
        lock_path = store.lock_path
    _assert_lease_released(lock_path)
    _no_mutation_calls(bundle.agent)


def test_worker_bundle_contract_returns_inputs_without_prebuilt_loop() -> None:
    fields = getattr(MathWorkerBundle, "__dataclass_fields__", {})
    assert set(fields) == {"agent", "llm", "config", "profile_name", "runtime_config"}
    assert "loop" not in fields


def test_worker_factory_uses_one_config_for_profile_agent_and_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path, keys_dir = _write_config(tmp_path)
    fake_llm = object()
    llm_constructor = Mock(return_value=fake_llm)
    premature_loop = Mock(side_effect=AssertionError("loop constructed before registration"))
    runtime_loader = Mock(wraps=load_runtime_state_config)
    monkeypatch.setattr("base_agent.worker_factory.LLMClient", llm_constructor)
    monkeypatch.setattr("base_agent.worker_factory.MathWorkerLoop", premature_loop, raising=False)
    monkeypatch.setattr(
        "base_agent.worker_factory.load_runtime_state_config",
        runtime_loader,
        raising=False,
    )

    bundle = WorkerFactory(config_path=config_path, keys_dir=keys_dir).create_math_worker(
        "actual-profile"
    )

    assert bundle.profile_name == "actual-profile"
    assert bundle.config == _worker_config()
    assert bundle.runtime_config == _runtime_config(tmp_path)
    assert bundle.llm is fake_llm
    runtime_loader.assert_called_once_with(config_path)
    premature_loop.assert_not_called()


def _worker_bundle(tmp_path: Path, keys_dir: Path, events: list[str]) -> _WorkerStartupBundle:
    agent = _FakeAgent(
        handle="worker-handle",
        public_key_b64=_public_key_b64(keys_dir, "worker-handle"),
        registered_agent_id="a-startup-worker",
        events=events,
    )
    return _WorkerStartupBundle(
        agent=agent,
        llm=_FakeLLM(events),
        config=_worker_config(),
        profile_name="actual-profile",
        runtime_config=_runtime_config(tmp_path),
    )


@pytest.mark.asyncio
async def test_worker_startup_replays_under_lease_before_loop_and_releases_after(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_signal_handlers(monkeypatch)
    config_path, keys_dir = _write_config(tmp_path)
    events: list[str] = []
    bundle = _worker_bundle(tmp_path, keys_dir, events)
    resolver = Mock(return_value=config_path)

    class Factory:
        def __init__(self, *, config_path: Path) -> None:
            events.append("factory")
            assert config_path == config_path_value

        def create_math_worker(self, profile_name: str) -> _WorkerStartupBundle:
            assert profile_name == "actual-profile"
            return bundle

    config_path_value = config_path
    lock_path = (
        bundle.runtime_config.directory
        / "identities"
        / runtime_owner_from_agent(
            runtime_config=bundle.runtime_config,
            agent=SimpleNamespace(
                agent_id="a-startup-worker",
                get_public_key_b64=bundle.agent.get_public_key_b64,
                config=bundle.agent.config,
            ),
            role="math-worker",
            profile="actual-profile",
            handle="worker-handle",
        ).key_fingerprint
        / "runtime.lock"
    )

    class Loop:
        def __init__(
            self,
            *,
            agent: _FakeAgent,
            llm: _FakeLLM,
            config: MathWorkerConfig,
            commands: RuntimeTaskCommands,
        ) -> None:
            assert agent.agent_id == "a-startup-worker"
            assert config == bundle.config and llm is bundle.llm
            self.commands = commands
            events.append("loop-construct")

        async def run(self) -> None:
            _assert_lease_busy(lock_path)
            events.append("discovery")
            await bundle.llm.complete("system", "prompt")

        def stop(self) -> None:
            events.append("loop-stop")

    original_replay = RuntimeTaskCommands.replay_unfinished

    async def replay(commands: RuntimeTaskCommands) -> tuple[object, ...]:
        events.append("replay")
        return await original_replay(commands)

    monkeypatch.setattr(math_worker_main, "resolve_config_path", resolver, raising=False)
    monkeypatch.setattr(math_worker_main, "WorkerFactory", Factory)
    monkeypatch.setattr(math_worker_main, "MathWorkerLoop", Loop, raising=False)
    monkeypatch.setattr(
        math_worker_main,
        "FileTaskIntentStore",
        FileTaskIntentStore,
        raising=False,
    )
    monkeypatch.setattr(
        math_worker_main,
        "RuntimeTaskCommands",
        RuntimeTaskCommands,
        raising=False,
    )
    monkeypatch.setattr(
        math_worker_main,
        "runtime_owner_from_agent",
        runtime_owner_from_agent,
        raising=False,
    )
    monkeypatch.setattr(RuntimeTaskCommands, "replay_unfinished", replay)

    await math_worker_main._main_factory("actual-profile")

    resolver.assert_called_once_with(
        env_var_name="AGENT_CONFIG_PATH",
        default_filename="config.yaml",
    )
    assert events.index("register") < events.index("account") < events.index("replay")
    assert events.index("replay") < events.index("loop-construct") < events.index("discovery")
    assert events.index("discovery") < events.index("llm")
    assert bundle.llm.closed and bundle.agent.closed
    assert events[-2:] == ["llm-close", "agent-close"]
    _assert_lease_released(lock_path)
    _no_mutation_calls(bundle.agent)


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["owner", "journal"])
async def test_worker_corrupt_runtime_fails_before_replay_mutation_or_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    _patch_signal_handlers(monkeypatch)
    config_path, keys_dir = _write_config(tmp_path)
    events: list[str] = []
    bundle = _worker_bundle(tmp_path, keys_dir, events)
    bundle.agent.agent_id = "a-startup-worker"
    owner = runtime_owner_from_agent(
        runtime_config=bundle.runtime_config,
        agent=bundle.agent,
        role="math-worker",
        profile="actual-profile",
        handle="worker-handle",
    )
    with FileTaskIntentStore(
        root_directory=bundle.runtime_config.directory,
        owner=owner,
        diagnostic_limit=8,
        max_record_bytes=65_536,
    ) as store:
        if corruption == "journal":
            corrupt_path = store.identity_directory / "intents" / "corrupt.json"
            corrupt_path.write_text("{")
            corrupt_path.chmod(0o600)
        owner_path = store.owner_path
        lock_path = store.lock_path
    if corruption == "owner":
        owner_path.write_text("{")
        owner_path.chmod(0o600)
    bundle.agent.agent_id = None

    class Factory:
        def __init__(self, *, config_path: Path) -> None:
            assert config_path == config_path_value

        def create_math_worker(self, _profile_name: str) -> _WorkerStartupBundle:
            return bundle

    class Loop:
        def __init__(self, **_kwargs: object) -> None:
            events.append("loop-construct")

        async def run(self) -> None:
            events.append("loop-run")

        def stop(self) -> None:
            pass

    config_path_value = config_path
    monkeypatch.setattr(
        math_worker_main,
        "resolve_config_path",
        Mock(return_value=config_path),
        raising=False,
    )
    monkeypatch.setattr(math_worker_main, "WorkerFactory", Factory)
    monkeypatch.setattr(math_worker_main, "MathWorkerLoop", Loop, raising=False)
    monkeypatch.setattr(math_worker_main, "FileTaskIntentStore", FileTaskIntentStore, raising=False)
    monkeypatch.setattr(math_worker_main, "RuntimeTaskCommands", RuntimeTaskCommands, raising=False)
    monkeypatch.setattr(
        math_worker_main,
        "runtime_owner_from_agent",
        runtime_owner_from_agent,
        raising=False,
    )

    with pytest.raises(RuntimeIntentError) as raised:
        await math_worker_main._main_factory("actual-profile")
    assert raised.value.reason == "corrupt_record"
    assert "loop-construct" not in events and "loop-run" not in events
    _no_mutation_calls(bundle.agent)
    assert bundle.agent.closed and bundle.llm.closed
    _assert_lease_released(lock_path)


@pytest.mark.asyncio
async def test_feeder_resolves_one_config_shares_commands_and_holds_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_signal_handlers(monkeypatch)
    config_path, keys_dir = _write_config(tmp_path)
    events: list[str] = []
    agent = _FakeAgent(
        handle="feeder",
        public_key_b64=_public_key_b64(keys_dir, "feeder"),
        registered_agent_id="a-startup-feeder",
        events=events,
    )
    runtime_config = _runtime_config(tmp_path)
    feeder_config = _feeder_config(tmp_path)
    acceptance_config = _acceptance_config()
    resolver = Mock(return_value=config_path)
    feeder_loader = Mock(return_value=feeder_config)
    acceptance_loader = Mock(return_value=acceptance_config)
    agent_loader = Mock(return_value=object())
    runtime_loader = Mock(return_value=runtime_config)
    command_ids: list[int] = []

    class FeederLoop:
        def __init__(
            self,
            agent: _FakeAgent,
            config: TaskFeederConfig,
            posting: object,
        ) -> None:
            assert agent.agent_id == "a-startup-feeder" and config is feeder_config
            assert posting is not None
            self.task_map: dict[str, object] = {}

        def rebuild_task_map(self) -> dict[str, object]:
            return self.task_map

        async def run(self) -> None:
            events.append("feed-run")

        def stop(self) -> None:
            pass

    class AcceptanceLoop:
        def __init__(
            self,
            *,
            agent: _FakeAgent,
            config: AcceptanceConfig,
            now: object,
            commands: RuntimeTaskCommands,
        ) -> None:
            assert agent.agent_id == "a-startup-feeder"
            assert config is acceptance_config and callable(now)
            self.commands = commands
            command_ids.append(id(commands))

        async def run(self, backoff: int) -> None:
            assert backoff == 1
            lock_path = _feeder_lock_path(runtime_config, agent)
            _assert_lease_busy(lock_path)
            events.append("acceptance-run")

        def stop(self) -> None:
            pass

    class ReviewLoop:
        def __init__(
            self,
            agent: _FakeAgent,
            task_map: dict[str, object],
            *,
            commands: RuntimeTaskCommands,
        ) -> None:
            assert agent.agent_id == "a-startup-feeder" and task_map == {}
            self.commands = commands
            command_ids.append(id(commands))

        async def run(self, interval: int, backoff: int) -> None:
            assert interval == 1 and backoff == 1
            events.append("review-run")

        def stop(self) -> None:
            pass

    original_replay = RuntimeTaskCommands.replay_unfinished

    async def replay(commands: RuntimeTaskCommands) -> tuple[object, ...]:
        events.append("replay")
        return await original_replay(commands)

    monkeypatch.setattr(task_feeder_main, "resolve_config_path", resolver, raising=False)
    monkeypatch.setattr(task_feeder_main, "load_task_feeder_settings", feeder_loader)
    monkeypatch.setattr(task_feeder_main, "load_acceptance_settings", acceptance_loader)
    monkeypatch.setattr(task_feeder_main, "load_agent_config", agent_loader)
    monkeypatch.setattr(
        task_feeder_main, "load_runtime_state_config", runtime_loader, raising=False
    )
    monkeypatch.setattr(task_feeder_main, "BaseAgent", Mock(return_value=agent))
    monkeypatch.setattr(task_feeder_main, "TaskFeederLoop", FeederLoop)
    monkeypatch.setattr(task_feeder_main, "AcceptanceLoop", AcceptanceLoop)
    monkeypatch.setattr(task_feeder_main, "ReviewLoop", ReviewLoop)
    monkeypatch.setattr(task_feeder_main, "FileTaskIntentStore", FileTaskIntentStore, raising=False)
    monkeypatch.setattr(task_feeder_main, "RuntimeTaskCommands", RuntimeTaskCommands, raising=False)
    monkeypatch.setattr(
        task_feeder_main,
        "runtime_owner_from_agent",
        runtime_owner_from_agent,
        raising=False,
    )
    monkeypatch.setattr(RuntimeTaskCommands, "replay_unfinished", replay)

    await task_feeder_main._main()

    resolver.assert_called_once_with(
        env_var_name="AGENT_CONFIG_PATH",
        default_filename="config.yaml",
    )
    feeder_loader.assert_called_once_with(config_path)
    acceptance_loader.assert_called_once_with(config_path)
    agent_loader.assert_called_once_with("feeder", config_path)
    runtime_loader.assert_called_once_with(config_path)
    assert len(command_ids) == 2 and len(set(command_ids)) == 1
    assert events.index("replay") < events.index("feed-run")
    assert events.index("replay") < events.index("acceptance-run")
    assert events.index("replay") < events.index("review-run")
    assert agent.closed
    _assert_lease_released(_feeder_lock_path(runtime_config, agent))
    _no_mutation_calls(agent)


def _feeder_lock_path(runtime_config: RuntimeStateConfig, agent: _FakeAgent) -> Path:
    owner = runtime_owner_from_agent(
        runtime_config=runtime_config,
        agent=agent,
        role="task-feeder",
        profile="feeder",
        handle="feeder",
    )
    return runtime_config.directory / "identities" / owner.key_fingerprint / "runtime.lock"


def test_main_functions_remain_async_and_explicitly_profiled() -> None:
    assert inspect.iscoroutinefunction(math_worker_main._main_factory)
    assert tuple(inspect.signature(math_worker_main._main_factory).parameters) == ("worker_name",)
    assert inspect.iscoroutinefunction(task_feeder_main._main)
