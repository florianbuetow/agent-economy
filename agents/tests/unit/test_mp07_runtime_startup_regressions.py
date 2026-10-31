"""T-181 startup admission, replay diagnostics, and sibling-drain regressions."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from service_commons.maintenance_lock import (
    MaintenanceLockMode,
    hold_existing_maintenance_lock,
)

from base_agent.runtime_config import RuntimeStateConfig
from base_agent.runtime_intents import (
    FileTaskIntentStore,
    RuntimeIntentError,
    RuntimeOwner,
)
from base_agent.runtime_task_commands import RuntimeTaskCommands, runtime_owner_from_agent
from math_worker import __main__ as math_worker_main
from task_feeder import __main__ as task_feeder_main
from tests.unit.test_mp07_runtime_startup import (
    _acceptance_config,
    _assert_lease_busy,
    _assert_lease_released,
    _FakeAgent,
    _FakeLLM,
    _feeder_config,
    _feeder_lock_path,
    _no_mutation_calls,
    _patch_signal_handlers,
    _public_key_b64,
    _worker_bundle,
    _worker_config,
    _WorkerStartupBundle,
    _write_config,
)
from tests.unit.test_runtime_task_commands import (
    _WORKER_ID,
    _new_intent,
    _operation_id,
    _store_bid_state,
    _task_id,
)

if TYPE_CHECKING:
    from pathlib import Path

    from task_feeder.config import AcceptanceConfig, TaskFeederConfig

pytestmark = pytest.mark.unit


def _owner_for_worker(bundle: _WorkerStartupBundle) -> RuntimeOwner:
    return runtime_owner_from_agent(
        runtime_config=bundle.runtime_config,
        agent=bundle.agent,
        role="math-worker",
        profile=bundle.profile_name,
        handle=bundle.config.handle,
    )


def _seed_admission_failure(
    *,
    runtime_config: RuntimeStateConfig,
    owner: RuntimeOwner,
    failure: str,
) -> Path:
    with FileTaskIntentStore(
        root_directory=runtime_config.directory,
        owner=owner,
        diagnostic_limit=runtime_config.diagnostic_limit,
        max_record_bytes=runtime_config.max_record_bytes,
    ) as store:
        owner_path = store.owner_path
        lock_path = store.lock_path
        if failure == "journal":
            corrupt = store.identity_directory / "intents" / "corrupt.json"
            corrupt.write_text("{")
            corrupt.chmod(0o600)
    if failure == "owner":
        owner_path.write_text("{")
        owner_path.chmod(0o600)
    return lock_path


def _patch_worker_main(
    monkeypatch: pytest.MonkeyPatch,
    *,
    config_path: Path,
    bundle: _WorkerStartupBundle,
    loop_type: type[object],
) -> None:
    class Factory:
        def __init__(self, *, config_path: Path) -> None:
            assert config_path == selected_config

        def create_math_worker(self, profile_name: str) -> _WorkerStartupBundle:
            assert profile_name == bundle.profile_name
            return bundle

    selected_config = config_path
    monkeypatch.setattr(
        math_worker_main,
        "resolve_config_path",
        Mock(return_value=config_path),
    )
    monkeypatch.setattr(math_worker_main, "WorkerFactory", Factory)
    monkeypatch.setattr(math_worker_main, "MathWorkerLoop", loop_type)


def _patch_feeder_main(
    monkeypatch: pytest.MonkeyPatch,
    *,
    config_path: Path,
    agent: _FakeAgent,
    runtime_config: RuntimeStateConfig,
    feeder_config: TaskFeederConfig,
    acceptance_config: AcceptanceConfig,
    feeder_loop: type[object],
    acceptance_loop: type[object],
    review_loop: type[object],
) -> None:
    monkeypatch.setattr(
        task_feeder_main,
        "resolve_config_path",
        Mock(return_value=config_path),
    )
    monkeypatch.setattr(
        task_feeder_main,
        "load_task_feeder_settings",
        Mock(return_value=feeder_config),
    )
    monkeypatch.setattr(
        task_feeder_main,
        "load_acceptance_settings",
        Mock(return_value=acceptance_config),
    )
    monkeypatch.setattr(task_feeder_main, "load_agent_config", Mock(return_value=object()))
    monkeypatch.setattr(
        task_feeder_main,
        "load_runtime_state_config",
        Mock(return_value=runtime_config),
    )
    monkeypatch.setattr(task_feeder_main, "BaseAgent", Mock(return_value=agent))
    monkeypatch.setattr(task_feeder_main, "TaskFeederLoop", feeder_loop)
    monkeypatch.setattr(task_feeder_main, "AcceptanceLoop", acceptance_loop)
    monkeypatch.setattr(task_feeder_main, "ReviewLoop", review_loop)


def _expected_reason(failure: str) -> str:
    return "lease_busy" if failure == "busy" else "corrupt_record"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["owner", "journal", "busy"])
async def test_worker_runtime_admission_precedes_bank_account_and_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    _patch_signal_handlers(monkeypatch)
    config_path, keys_dir = _write_config(tmp_path)
    events: list[str] = []
    bundle = _worker_bundle(tmp_path, keys_dir, events)
    bundle.agent.agent_id = "a-startup-worker"
    owner = _owner_for_worker(bundle)
    lock_path = _seed_admission_failure(
        runtime_config=bundle.runtime_config,
        owner=owner,
        failure=failure,
    )
    bundle.agent.agent_id = None

    class Loop:
        def __init__(self, **_kwargs: object) -> None:
            events.append("loop-construct")

        async def run(self) -> None:
            events.append("loop-run")

        def stop(self) -> None:
            pass

    _patch_worker_main(
        monkeypatch,
        config_path=config_path,
        bundle=bundle,
        loop_type=Loop,
    )

    if failure == "busy":
        metadata = lock_path.lstat()
        context = hold_existing_maintenance_lock(
            lock_path,
            mode=MaintenanceLockMode.EXCLUSIVE,
            blocking=False,
            expected_device=metadata.st_dev,
            expected_inode=metadata.st_ino,
        )
    else:
        context = _null_context()

    with context, pytest.raises(RuntimeIntentError) as raised:
        await math_worker_main._main_factory(bundle.profile_name)

    assert raised.value.reason == _expected_reason(failure)
    assert events[0] == "register"
    assert "account" not in events
    assert "loop-construct" not in events and "loop-run" not in events
    _no_mutation_calls(bundle.agent)
    assert bundle.agent.closed and bundle.llm.closed
    _assert_lease_released(lock_path)


class _null_context:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_args: object) -> None:
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["owner", "journal", "busy"])
async def test_feeder_runtime_admission_precedes_bank_account_and_loops(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
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
    runtime_config = RuntimeStateConfig(
        environment_id="startup-test",
        directory=tmp_path / "runtime-state",
        diagnostic_limit=8,
        max_record_bytes=65_536,
    )
    agent.agent_id = "a-startup-feeder"
    owner = runtime_owner_from_agent(
        runtime_config=runtime_config,
        agent=agent,
        role="task-feeder",
        profile="feeder",
        handle="feeder",
    )
    lock_path = _seed_admission_failure(
        runtime_config=runtime_config,
        owner=owner,
        failure=failure,
    )
    agent.agent_id = None

    class NeverLoop:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            events.append("loop-construct")

        async def run(self, *_args: object) -> None:
            events.append("loop-run")

        def stop(self) -> None:
            pass

    _patch_feeder_main(
        monkeypatch,
        config_path=config_path,
        agent=agent,
        runtime_config=runtime_config,
        feeder_config=_feeder_config(tmp_path),
        acceptance_config=_acceptance_config(),
        feeder_loop=NeverLoop,
        acceptance_loop=NeverLoop,
        review_loop=NeverLoop,
    )

    if failure == "busy":
        metadata = lock_path.lstat()
        context = hold_existing_maintenance_lock(
            lock_path,
            mode=MaintenanceLockMode.EXCLUSIVE,
            blocking=False,
            expected_device=metadata.st_dev,
            expected_inode=metadata.st_ino,
        )
    else:
        context = _null_context()

    with context, pytest.raises(RuntimeIntentError) as raised:
        await task_feeder_main._main()

    assert raised.value.reason == _expected_reason(failure)
    assert events[0] == "register"
    assert "account" not in events
    assert "loop-construct" not in events and "loop-run" not in events
    _no_mutation_calls(agent)
    assert agent.closed
    _assert_lease_released(lock_path)


def _seed_replay_states(
    *,
    bundle: _WorkerStartupBundle,
) -> tuple[str, ...]:
    bundle.agent.agent_id = _WORKER_ID
    owner = _owner_for_worker(bundle)
    intents = (
        _new_intent(task_id=_task_id(501), operation_id=_operation_id(501)),
        _new_intent(task_id=_task_id(502), operation_id=_operation_id(502)),
        _new_intent(task_id=_task_id(503), operation_id=_operation_id(503)),
    )
    with FileTaskIntentStore(
        root_directory=bundle.runtime_config.directory,
        owner=owner,
        diagnostic_limit=bundle.runtime_config.diagnostic_limit,
        max_record_bytes=bundle.runtime_config.max_record_bytes,
    ) as store:
        store.create(intents[0])
        _store_bid_state(store, intents[1], "blocked")
        _store_bid_state(store, intents[2], "attention_required")
    bundle.agent.agent_id = None
    return tuple(intent.operation_id for intent in intents)


@pytest.mark.asyncio
async def test_worker_logs_bounded_replay_state_and_operation_summary_before_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _patch_signal_handlers(monkeypatch)
    config_path, keys_dir = _write_config(tmp_path)
    events: list[str] = []
    base_bundle = _worker_bundle(tmp_path, keys_dir, events)
    runtime_config = RuntimeStateConfig(
        environment_id=base_bundle.runtime_config.environment_id,
        directory=base_bundle.runtime_config.directory,
        diagnostic_limit=2,
        max_record_bytes=base_bundle.runtime_config.max_record_bytes,
    )
    agent = _FakeAgent(
        handle="worker-handle",
        public_key_b64=base_bundle.agent.get_public_key_b64(),
        registered_agent_id=_WORKER_ID,
        events=events,
    )
    bundle = _WorkerStartupBundle(
        agent=agent,
        llm=_FakeLLM(events),
        config=_worker_config(),
        profile_name="actual-profile",
        runtime_config=runtime_config,
    )
    operation_ids = _seed_replay_states(bundle=bundle)
    request = httpx.Request("POST", "https://task-board.startup.example/v1/tasks/bid")
    agent.submit_bid.side_effect = httpx.ReadTimeout("response unavailable", request=request)

    class Loop:
        def __init__(self, **_kwargs: object) -> None:
            events.append("loop-construct")

        async def run(self) -> None:
            events.append("loop-run")

        def stop(self) -> None:
            pass

    _patch_worker_main(
        monkeypatch,
        config_path=config_path,
        bundle=bundle,
        loop_type=Loop,
    )
    caplog.set_level(logging.INFO, logger="math_worker")

    await math_worker_main._main_factory(bundle.profile_name)

    messages = "\n".join(
        record.getMessage() for record in caplog.records if record.name == "math_worker"
    )
    assert "runtime replay" in messages.lower()
    for state in ("pending", "blocked", "attention_required"):
        assert f"{state}=1" in messages
    assert "total=3" in messages and "logged=2" in messages and "omitted=1" in messages
    assert sum(operation_id in messages for operation_id in operation_ids) == 2
    assert "test-key" not in messages and "response unavailable" not in messages
    assert "loop-run" in events


@pytest.mark.asyncio
async def test_feeder_sibling_failure_drains_under_lease_before_close_and_release(
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
    runtime_config = RuntimeStateConfig(
        environment_id="startup-test",
        directory=tmp_path / "runtime-state",
        diagnostic_limit=8,
        max_record_bytes=65_536,
    )
    sibling_started = {"acceptance": asyncio.Event(), "review": asyncio.Event()}

    class FeederLoop:
        def __init__(
            self,
            agent: _FakeAgent,
            config: TaskFeederConfig,
            posting: object,
        ) -> None:
            assert agent is agent_under_test
            assert config == _feeder_config(tmp_path)
            assert posting is not None
            self.task_map: dict[str, object] = {}

        def rebuild_task_map(self) -> dict[str, object]:
            return self.task_map

        async def run(self) -> None:
            await sibling_started["acceptance"].wait()
            await sibling_started["review"].wait()
            events.append("feed-fail")
            raise RuntimeError("injected feeder failure")

        def stop(self) -> None:
            pass

    class AcceptanceLoop:
        def __init__(self, *, commands: RuntimeTaskCommands, **_kwargs: object) -> None:
            self.commands = commands

        async def run(self, _backoff: int) -> None:
            sibling_started["acceptance"].set()
            try:
                await asyncio.Event().wait()
            finally:
                _assert_lease_busy(_feeder_lock_path(runtime_config, agent))
                events.append("acceptance-finally")

        def stop(self) -> None:
            pass

    class ReviewLoop:
        def __init__(
            self, *_args: object, commands: RuntimeTaskCommands, **_kwargs: object
        ) -> None:
            self.commands = commands

        async def run(self, _interval: int, _backoff: int) -> None:
            sibling_started["review"].set()
            try:
                await asyncio.Event().wait()
            finally:
                _assert_lease_busy(_feeder_lock_path(runtime_config, agent))
                events.append("review-finally")

        def stop(self) -> None:
            pass

    async def close_agent() -> None:
        assert "acceptance-finally" in events and "review-finally" in events
        _assert_lease_busy(_feeder_lock_path(runtime_config, agent))
        events.append("agent-close")
        agent.closed = True

    agent_under_test = agent
    agent.close = AsyncMock(side_effect=close_agent)
    _patch_feeder_main(
        monkeypatch,
        config_path=config_path,
        agent=agent,
        runtime_config=runtime_config,
        feeder_config=_feeder_config(tmp_path),
        acceptance_config=_acceptance_config(),
        feeder_loop=FeederLoop,
        acceptance_loop=AcceptanceLoop,
        review_loop=ReviewLoop,
    )

    with pytest.raises(ExceptionGroup) as raised:
        await task_feeder_main._main()

    assert len(raised.value.exceptions) == 1
    assert isinstance(raised.value.exceptions[0], RuntimeError)
    assert str(raised.value.exceptions[0]) == "injected feeder failure"
    assert events.index("feed-fail") < events.index("acceptance-finally")
    assert events.index("feed-fail") < events.index("review-finally")
    assert events.index("acceptance-finally") < events.index("agent-close")
    assert events.index("review-finally") < events.index("agent-close")
    _assert_lease_released(_feeder_lock_path(runtime_config, agent))
    _no_mutation_calls(agent)
