"""MP-07 worker integration with caller-owned native Task Board intents."""

from __future__ import annotations

import base64
import hashlib
import inspect
import json
import os
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from service_auth import TaskBoardCommandResponse

from base_agent.agent import BaseAgent
from base_agent.config import AgentConfig, TokenPolicy
from base_agent.runtime_config import RuntimeStateConfig
from base_agent.runtime_intent_models import canonical_sha256
from base_agent.runtime_intents import (
    FileTaskIntentStore,
    FrozenTaskIntent,
    NewTaskIntent,
    RuntimeIntentError,
    RuntimeOwner,
)
from base_agent.runtime_task_commands import RuntimeTaskCommands, runtime_owner_from_agent
from math_worker.config import MathWorkerConfig
from math_worker.llm_client import LLMResponse
from math_worker.loop import MathWorkerLoop

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator
    from pathlib import Path

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

_TASK_ID = "t-20000000-0000-4000-8000-000000000001"
_BID_OPERATION_ID = "op-20000000-0000-4000-8000-000000000001"
_SUBMIT_OPERATION_ID = "op-20000000-0000-4000-8000-000000000002"
_WORKER_ID = "a-worker-native"
_POSTER_ID = "a-poster-native"
_ESCROW_ID = "esc-native-worker"
_URLS = {
    "identity_url": "https://identity.worker.example/v1",
    "bank_url": "https://bank.worker.example/v1",
    "task_board_url": "https://task-board.worker.example/v1",
    "reputation_url": "https://reputation.worker.example/v1",
    "court_url": "https://court.worker.example/v1",
}


def _agent_config() -> AgentConfig:
    private_key = Ed25519PrivateKey.from_private_bytes(bytes([7]) * 32)
    return AgentConfig(
        name="Native Math Worker",
        private_key=private_key,
        public_key=private_key.public_key(),
        identity_url=_URLS["identity_url"],
        bank_url=_URLS["bank_url"],
        task_board_url=_URLS["task_board_url"],
        reputation_url=_URLS["reputation_url"],
        court_url=_URLS["court_url"],
        token_ttl_seconds=300,
        token_policy=TokenPolicy(max_lifetime_seconds=300, clock_skew_seconds=30),
    )


@pytest.fixture()
async def worker_agent() -> AsyncIterator[BaseAgent]:
    agent = BaseAgent(_agent_config())
    agent.agent_id = _WORKER_ID
    mocked = cast("Any", agent)
    mocked._request = AsyncMock(side_effect=AssertionError("private decoded request used"))
    mocked._request_raw = AsyncMock(side_effect=AssertionError("private raw request used"))
    mocked.get_task = AsyncMock()
    mocked.list_tasks = AsyncMock(return_value=[])
    mocked.get_balance = AsyncMock(return_value={"balance": 500})
    mocked.submit_bid = AsyncMock()
    mocked.upload_asset = AsyncMock()
    mocked.submit_deliverable = AsyncMock()
    try:
        yield agent
    finally:
        await agent.close()


def _config() -> MathWorkerConfig:
    return MathWorkerConfig(
        handle="native-math-worker",
        scan_interval_seconds=1,
        poll_interval_seconds=0,
        max_poll_attempts=1,
        error_backoff_seconds=1,
        min_reward=1,
        max_reward=1_000,
    )


def _llm(content: str = "90") -> MagicMock:
    client = MagicMock()
    client.complete = AsyncMock(
        return_value=LLMResponse(
            content=content,
            finish_reason="stop",
            prompt_tokens=10,
            completion_tokens=2,
        )
    )
    return client


def _runtime_config(root: Path) -> RuntimeStateConfig:
    return RuntimeStateConfig(
        environment_id="worker-intent-tests",
        directory=root,
        diagnostic_limit=8,
        max_record_bytes=65_536,
    )


def _owner(root: Path, agent: BaseAgent) -> RuntimeOwner:
    return runtime_owner_from_agent(
        runtime_config=_runtime_config(root),
        agent=agent,
        role="math-worker",
        profile="mathbot_native",
        handle="native-math-worker",
    )


def _store(root: Path, agent: BaseAgent) -> FileTaskIntentStore:
    runtime_config = _runtime_config(root)
    return FileTaskIntentStore(
        root_directory=runtime_config.directory,
        owner=_owner(root, agent),
        diagnostic_limit=runtime_config.diagnostic_limit,
        max_record_bytes=runtime_config.max_record_bytes,
    )


@contextmanager
def _open_commands(
    root: Path,
    agent: BaseAgent,
) -> Iterator[tuple[FileTaskIntentStore, RuntimeTaskCommands]]:
    with _store(root, agent) as store:
        yield store, RuntimeTaskCommands(agent, store)


def _loop(
    agent: BaseAgent,
    llm: MagicMock,
    commands: RuntimeTaskCommands,
) -> MathWorkerLoop:
    parameters = inspect.signature(MathWorkerLoop).parameters
    constructor = cast("Any", MathWorkerLoop)
    if "commands" in parameters:
        return cast(
            "MathWorkerLoop",
            constructor(agent=agent, llm=llm, config=_config(), commands=commands),
        )
    loop = cast("MathWorkerLoop", constructor(agent=agent, llm=llm, config=_config()))
    cast("Any", loop)._commands = commands
    return loop


async def _call_bidding(loop: MathWorkerLoop, task_id: str) -> FrozenTaskIntent:
    parameter = next(iter(inspect.signature(loop._phase_bidding).parameters.values()))
    argument: object = _task("open", 7) if parameter.name == "task" else task_id
    return cast("FrozenTaskIntent", await cast("Any", loop._phase_bidding)(argument))


async def _call_submitting(
    loop: MathWorkerLoop,
    task_id: str,
    solution: str,
) -> FrozenTaskIntent:
    return cast(
        "FrozenTaskIntent",
        await cast("Any", loop._phase_submitting)(task_id, solution),
    )


def _bid_intent(
    *,
    operation_id: str = _BID_OPERATION_ID,
    amount: int = 90,
    expected_lifecycle_version: int = 7,
) -> NewTaskIntent:
    return NewTaskIntent(
        task_id=_TASK_ID,
        slot="bid",
        action="submit_bid",
        source_status="open",
        target_status="open",
        operation_id=operation_id,
        expected_lifecycle_version=expected_lifecycle_version,
        public_arguments={
            "task_id": _TASK_ID,
            "amount": amount,
            "operation_id": operation_id,
            "expected_lifecycle_version": expected_lifecycle_version,
        },
        acknowledgment_context={
            "response_kind": "bid",
            "operation_id": operation_id,
            "task_id": _TASK_ID,
            "command": "submit_bid",
            "source_status": "open",
            "source_lifecycle_version": expected_lifecycle_version,
            "target_status": "open",
            "target_lifecycle_version": expected_lifecycle_version,
            "result_facts": {
                "bid_id": f"bid-{operation_id[3:]}",
                "bidder_id": _WORKER_ID,
                "amount": amount,
            },
            "actor_id": _WORKER_ID,
            "frozen_task_facts": {"bidder_id": _WORKER_ID},
        },
    )


def _submission_intent(
    *,
    operation_id: str = _SUBMIT_OPERATION_ID,
    expected_lifecycle_version: int = 11,
) -> NewTaskIntent:
    frozen_facts = {
        "poster_id": _POSTER_ID,
        "worker_id": _WORKER_ID,
        "escrow_id": _ESCROW_ID,
        "accepted_reward": 100,
        "selected_bid_amount": 90,
    }
    native_request = {
        "protocol_version": 1,
        "task_id": _TASK_ID,
        "command": "submit_deliverable",
        "expected_status": "accepted",
        "expected_lifecycle_version": expected_lifecycle_version,
        "actor_kind": "agent",
        "actor_id": _WORKER_ID,
        "arguments": {"worker_id": _WORKER_ID},
    }
    acknowledgment_context: dict[str, object] = {
        "response_kind": "task_mutation",
        "operation_id": operation_id,
        "task_id": _TASK_ID,
        "command": "submit_deliverable",
        "source_status": "accepted",
        "source_lifecycle_version": expected_lifecycle_version,
        "target_status": "submitted",
        "target_lifecycle_version": expected_lifecycle_version + 1,
        "result_facts": {"worker_id": _WORKER_ID},
        "actor_id": _WORKER_ID,
        "frozen_task_facts": frozen_facts,
        "request_sha256": _canonical_sha256(native_request),
    }
    return NewTaskIntent(
        task_id=_TASK_ID,
        slot="submission",
        action="submit_deliverable",
        source_status="accepted",
        target_status="submitted",
        operation_id=operation_id,
        expected_lifecycle_version=expected_lifecycle_version,
        public_arguments={
            "task_id": _TASK_ID,
            "operation_id": operation_id,
            "expected_lifecycle_version": expected_lifecycle_version,
        },
        acknowledgment_context=cast("Any", acknowledgment_context),
    )


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _bid_ack(intent: NewTaskIntent | FrozenTaskIntent) -> dict[str, object]:
    amount = cast("int", intent.public_arguments["amount"])
    return {
        "bid_id": f"bid-{intent.operation_id[3:]}",
        "task_id": intent.task_id,
        "bidder_id": _WORKER_ID,
        "amount": amount,
        "submitted_at": "2026-10-18T12:00:00Z",
    }


def _submission_ack(intent: NewTaskIntent | FrozenTaskIntent) -> dict[str, object]:
    event_id = 41
    result = {
        "task_id": intent.task_id,
        "command": "submit_deliverable",
        "status": "submitted",
        "lifecycle_version": intent.expected_lifecycle_version + 1,
        "event_id": event_id,
        "worker_id": _WORKER_ID,
    }
    operation = {
        "protocol_version": 1,
        "operation_id": intent.operation_id,
        "task_id": intent.task_id,
        "command": "submit_deliverable",
        "request_sha256": intent.acknowledgment_context["request_sha256"],
        "operation_state": "completed",
        "source_status": "accepted",
        "source_lifecycle_version": intent.expected_lifecycle_version,
        "target_status": "submitted",
        "target_lifecycle_version": intent.expected_lifecycle_version + 1,
        "effect_kind": "none",
        "effect_state": "none",
        "effect_operation_id": None,
        "effect_result_reference": None,
        "effect_intent": None,
        "event_id": event_id,
        "result": result,
    }
    return {
        "task_id": intent.task_id,
        "status": "submitted",
        "lifecycle_version": intent.expected_lifecycle_version + 1,
        "worker_id": _WORKER_ID,
        "operation": operation,
    }


def _task(status: str, lifecycle_version: int) -> dict[str, object]:
    return {
        "task_id": _TASK_ID,
        "title": "Solve the deterministic task",
        "spec": "Return the exact integer answer",
        "reward": 100,
        "status": status,
        "lifecycle_version": lifecycle_version,
        "poster_id": _POSTER_ID,
        "worker_id": _WORKER_ID if status != "open" else None,
        "escrow_id": _ESCROW_ID,
        "accepted_reward": 100 if status != "open" else None,
        "accepted_bid_amount": 90 if status != "open" else None,
    }


def _state(intent: NewTaskIntent, owner: RuntimeOwner, state: str) -> FrozenTaskIntent:
    frozen = FrozenTaskIntent.pending(intent, owner_sha256=canonical_sha256(owner.as_json()))
    if state == "pending":
        return frozen
    result: object | None = None
    if state == "completed":
        body = _bid_ack(intent) if intent.action == "submit_bid" else _submission_ack(intent)
        result = {
            "status_code": 201 if intent.action == "submit_bid" else 200,
            "body": body,
        }
    return frozen.with_observation(
        state=state,
        result=result,
        diagnostic=None if state == "completed" else {"kind": state},
        diagnostic_limit=8,
    )


def _assert_public_transport_only(agent: BaseAgent) -> None:
    mocked = cast("Any", agent)
    mocked._request.assert_not_awaited()
    mocked._request_raw.assert_not_awaited()


async def test_real_store_and_commands_bind_deterministic_public_agent_fixture(
    tmp_path: Path,
    worker_agent: BaseAgent,
) -> None:
    with _open_commands(tmp_path / "runtime", worker_agent) as (store, commands):
        raw_key = base64.b64decode(worker_agent.get_public_key_b64(), validate=True)
        assert store.owner.registered_agent_id == _WORKER_ID
        assert store.owner.key_fingerprint == hashlib.sha256(raw_key).hexdigest()
        assert dict(store.owner.service_urls) == _URLS
        assert isinstance(commands, RuntimeTaskCommands)
        assert store.unfinished() == ()
    _assert_public_transport_only(worker_agent)


async def test_loop_constructor_requires_runtime_task_commands(
    tmp_path: Path,
    worker_agent: BaseAgent,
) -> None:
    signature = inspect.signature(MathWorkerLoop)
    assert tuple(signature.parameters) == ("agent", "llm", "config", "commands")
    assert signature.parameters["commands"].default is inspect.Parameter.empty
    phase_signature = inspect.signature(MathWorkerLoop._phase_bidding)
    assert tuple(phase_signature.parameters) == ("self", "task_id")
    with _open_commands(tmp_path / "runtime", worker_agent) as (_store_value, commands):
        loop = cast("Any", MathWorkerLoop)(
            agent=worker_agent,
            llm=_llm(),
            config=_config(),
            commands=commands,
        )
    assert cast("Any", loop)._commands is commands


async def test_stored_bid_reopens_and_replays_without_task_balance_or_llm_read(
    tmp_path: Path,
    worker_agent: BaseAgent,
) -> None:
    root = tmp_path / "runtime"
    with _store(root, worker_agent) as store:
        stored = store.create(_bid_intent())
    mocked = cast("Any", worker_agent)
    mocked.get_task = AsyncMock(side_effect=AssertionError("stored bid fetched task"))
    mocked.get_balance = AsyncMock(side_effect=AssertionError("stored bid fetched balance"))
    llm = _llm()
    llm.complete = AsyncMock(side_effect=AssertionError("stored bid called LLM"))
    mocked.submit_bid = AsyncMock(return_value=TaskBoardCommandResponse(201, _bid_ack(stored)))

    with _open_commands(root, worker_agent) as (_store_value, commands):
        observed = await _call_bidding(_loop(worker_agent, llm, commands), _TASK_ID)

    assert observed.state == "completed"
    mocked.submit_bid.assert_awaited_once_with(
        _TASK_ID,
        90,
        operation_id=_BID_OPERATION_ID,
        expected_lifecycle_version=7,
    )
    mocked.get_task.assert_not_awaited()
    mocked.get_balance.assert_not_awaited()
    llm.complete.assert_not_awaited()
    _assert_public_transport_only(worker_agent)


async def test_absent_bid_reads_once_and_persists_frozen_terms_before_transport(
    tmp_path: Path,
    worker_agent: BaseAgent,
) -> None:
    root = tmp_path / "runtime"
    mocked = cast("Any", worker_agent)
    mocked.get_task = AsyncMock(return_value=_task("open", 7))
    llm = _llm("90")

    with _open_commands(root, worker_agent) as (store, commands):

        async def send(
            task_id: str,
            amount: int,
            *,
            operation_id: str,
            expected_lifecycle_version: int,
        ) -> TaskBoardCommandResponse:
            pending = store.load(task_id, "bid")
            assert pending is not None and pending.state == "pending"
            assert pending.operation_id == operation_id
            assert pending.expected_lifecycle_version == expected_lifecycle_version == 7
            assert pending.public_arguments["amount"] == amount == 90
            return TaskBoardCommandResponse(201, _bid_ack(pending))

        mocked.submit_bid = AsyncMock(side_effect=send)
        observed = await _call_bidding(_loop(worker_agent, llm, commands), _TASK_ID)

    assert observed.state == "completed"
    mocked.get_task.assert_awaited_once_with(_TASK_ID)
    mocked.get_balance.assert_awaited_once_with()
    llm.complete.assert_awaited_once()
    operation_id = mocked.submit_bid.await_args.kwargs["operation_id"]
    assert isinstance(operation_id, str) and operation_id.startswith("op-")
    _assert_public_transport_only(worker_agent)


async def test_bid_storage_failure_blocks_public_mutation_transport(
    tmp_path: Path,
    worker_agent: BaseAgent,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    mocked = cast("Any", worker_agent)
    mocked.get_task = AsyncMock(return_value=_task("open", 7))
    mocked.submit_bid = AsyncMock()
    with _open_commands(root, worker_agent) as (_store_value, commands):

        def fail_fsync(_descriptor: int) -> None:
            raise OSError("injected intent fsync failure")

        monkeypatch.setattr(os, "fsync", fail_fsync)
        with pytest.raises(RuntimeIntentError) as raised:
            await _call_bidding(_loop(worker_agent, _llm(), commands), _TASK_ID)

    assert raised.value.reason == "storage_failure"
    mocked.submit_bid.assert_not_awaited()
    _assert_public_transport_only(worker_agent)


async def test_stored_submission_reopens_and_replays_without_upload_or_task_read(
    tmp_path: Path,
    worker_agent: BaseAgent,
) -> None:
    root = tmp_path / "runtime"
    with _store(root, worker_agent) as store:
        stored = store.create(_submission_intent())
    mocked = cast("Any", worker_agent)
    mocked.upload_asset = AsyncMock(side_effect=AssertionError("stored submission uploaded"))
    mocked.get_task = AsyncMock(side_effect=AssertionError("stored submission fetched task"))
    mocked.submit_deliverable = AsyncMock(
        return_value=TaskBoardCommandResponse(200, _submission_ack(stored))
    )

    with _open_commands(root, worker_agent) as (_store_value, commands):
        observed = await _call_submitting(_loop(worker_agent, _llm(), commands), _TASK_ID, "42")

    assert observed.state == "completed"
    mocked.submit_deliverable.assert_awaited_once_with(
        _TASK_ID,
        operation_id=_SUBMIT_OPERATION_ID,
        expected_lifecycle_version=11,
    )
    mocked.upload_asset.assert_not_awaited()
    mocked.get_task.assert_not_awaited()
    _assert_public_transport_only(worker_agent)


async def test_absent_submission_uploads_then_freezes_current_version_before_transport(
    tmp_path: Path,
    worker_agent: BaseAgent,
) -> None:
    root = tmp_path / "runtime"
    events: list[str] = []

    async def upload(_task_id: str, _filename: str, _content: bytes) -> dict[str, object]:
        events.append("upload")
        return {"asset_id": "asset-worker-solution"}

    async def read_task(_task_id: str) -> dict[str, object]:
        events.append("read")
        return _task("accepted", 11)

    mocked = cast("Any", worker_agent)
    mocked.upload_asset = AsyncMock(side_effect=upload)
    mocked.get_task = AsyncMock(side_effect=read_task)
    with _open_commands(root, worker_agent) as (store, commands):

        async def submit(
            task_id: str,
            *,
            operation_id: str,
            expected_lifecycle_version: int,
        ) -> TaskBoardCommandResponse:
            events.append("submit")
            pending = store.load(task_id, "submission")
            assert pending is not None and pending.state == "pending"
            assert pending.operation_id == operation_id
            assert pending.expected_lifecycle_version == expected_lifecycle_version == 11
            return TaskBoardCommandResponse(200, _submission_ack(pending))

        mocked.submit_deliverable = AsyncMock(side_effect=submit)
        observed = await _call_submitting(_loop(worker_agent, _llm(), commands), _TASK_ID, "42")

    assert observed.state == "completed"
    assert events == ["upload", "read", "submit"]
    mocked.upload_asset.assert_awaited_once_with(
        _TASK_ID,
        f"{_TASK_ID}_solution.txt",
        b"42",
    )
    _assert_public_transport_only(worker_agent)


@pytest.mark.parametrize(
    ("state", "should_poll"),
    [("pending", False), ("blocked", False), ("attention_required", False), ("completed", True)],
)
async def test_only_completed_bid_enters_acceptance_polling(
    tmp_path: Path,
    worker_agent: BaseAgent,
    state: str,
    should_poll: bool,
) -> None:
    root = tmp_path / "runtime"
    owner = _owner(root, worker_agent)
    bid = _state(_bid_intent(), owner, state)
    with _open_commands(root, worker_agent) as (_store_value, commands):
        loop = _loop(worker_agent, _llm(), commands)
        mocked_loop = cast("Any", loop)
        mocked_agent = cast("Any", worker_agent)
        mocked_loop._phase_scanning = AsyncMock(return_value=_TASK_ID)
        mocked_loop._phase_bidding = AsyncMock(return_value=bid)
        mocked_loop._phase_waiting_for_acceptance = AsyncMock(return_value=False)
        mocked_agent.get_task = AsyncMock(return_value=_task("open", 7))

        await loop._cycle()

    if should_poll:
        mocked_loop._phase_waiting_for_acceptance.assert_awaited_once_with(
            _TASK_ID, f"bid-{_BID_OPERATION_ID[3:]}"
        )
    else:
        mocked_loop._phase_waiting_for_acceptance.assert_not_awaited()
        mocked_agent.get_task.assert_not_awaited()
        assert loop._history.records == []
        assert loop._history.total_earnings == 0


@pytest.mark.parametrize(
    ("state", "should_review"),
    [("pending", False), ("blocked", False), ("attention_required", False), ("completed", True)],
)
async def test_only_completed_submission_enters_review_and_can_earn(
    tmp_path: Path,
    worker_agent: BaseAgent,
    state: str,
    should_review: bool,
) -> None:
    root = tmp_path / "runtime"
    owner = _owner(root, worker_agent)
    completed_bid = _state(_bid_intent(), owner, "completed")
    submission = _state(_submission_intent(), owner, state)
    with _open_commands(root, worker_agent) as (_store_value, commands):
        loop = _loop(worker_agent, _llm(), commands)
        mocked_loop = cast("Any", loop)
        mocked_agent = cast("Any", worker_agent)
        mocked_loop._phase_scanning = AsyncMock(return_value=_TASK_ID)
        mocked_loop._phase_bidding = AsyncMock(return_value=completed_bid)
        mocked_loop._phase_waiting_for_acceptance = AsyncMock(return_value=True)
        mocked_loop._phase_solving = AsyncMock(return_value="42")
        mocked_loop._phase_submitting = AsyncMock(return_value=submission)
        mocked_loop._phase_waiting_for_review = AsyncMock(return_value="approved")
        mocked_agent.get_task = AsyncMock(return_value=_task("accepted", 11))

        await loop._cycle()

    if should_review:
        mocked_loop._phase_waiting_for_review.assert_awaited_once_with(_TASK_ID)
        assert loop._history.total_earnings == 100
    else:
        mocked_loop._phase_waiting_for_review.assert_not_awaited()
        assert loop._history.records == []
        assert loop._history.total_earnings == 0


async def test_cycle_replays_known_pending_once_before_new_filtered_discovery(
    tmp_path: Path,
    worker_agent: BaseAgent,
) -> None:
    root = tmp_path / "runtime"
    with _store(root, worker_agent) as store:
        store.create(_bid_intent())
    events: list[str] = []
    request = httpx.Request("POST", f"{_URLS['task_board_url']}/tasks/{_TASK_ID}/bids")

    async def lost_ack(*_args: object, **_kwargs: object) -> TaskBoardCommandResponse:
        events.append("replay")
        raise httpx.ReadTimeout("acknowledgment unavailable", request=request)

    async def discover(*, status: str) -> list[dict[str, object]]:
        assert status == "open"
        events.append("discover")
        return []

    mocked = cast("Any", worker_agent)
    mocked.submit_bid = AsyncMock(side_effect=lost_ack)
    mocked.list_tasks = AsyncMock(side_effect=discover)
    with _open_commands(root, worker_agent) as (store, commands):
        loop = _loop(worker_agent, _llm(), commands)
        await loop._cycle()
        retained = store.load(_TASK_ID, "bid")

    assert events == ["replay", "discover"]
    assert retained is not None and retained.state == "pending"
    mocked.submit_bid.assert_awaited_once_with(
        _TASK_ID,
        90,
        operation_id=_BID_OPERATION_ID,
        expected_lifecycle_version=7,
    )
    mocked.list_tasks.assert_awaited_once_with(status="open")
    _assert_public_transport_only(worker_agent)
