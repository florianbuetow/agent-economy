"""T-181 durable Task Board command coordination contract."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import importlib.util
import inspect
import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, call, patch

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from service_auth import TaskBoardCommandContractError, TaskBoardCommandResponse

from base_agent.runtime_config import RuntimeStateConfig
from base_agent.runtime_intents import (
    FileTaskIntentStore,
    FrozenTaskIntent,
    NewTaskIntent,
    RuntimeIntentError,
    RuntimeOwner,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit

_MODULE_NAME = "base_agent.runtime_task_commands"
_REQUIRED_EXPORTS = {"RuntimeTaskCommands", "runtime_owner_from_agent"}
_module_spec = importlib.util.find_spec(_MODULE_NAME)
_commands: Any | None = importlib.import_module(_MODULE_NAME) if _module_spec is not None else None
_missing_exports = (
    sorted(_REQUIRED_EXPORTS.difference(dir(_commands))) if _commands is not None else []
)

_TASK_ID = "t-00000000-0000-4000-8000-000000000181"
_OPERATION_ID = "op-00000000-0000-4000-8000-000000000181"
_WORKER_ID = "a-worker-alice"
_POSTER_ID = "a-poster-alice"
_ESCROW_ID = "esc-runtime-181"
_URLS = {
    "identity_url": "https://Identity.Example/v1/",
    "bank_url": "https://Bank.Example/v1/",
    "task_board_url": "https://Task-Board.Example/v1/",
    "reputation_url": "https://Reputation.Example/v1/",
    "court_url": "https://Court.Example/v1/",
}
_ACTIONS = (
    "submit_bid",
    "accept_bid",
    "submit_deliverable",
    "approve_task",
    "dispute_task",
)


@dataclass(frozen=True, slots=True)
class _OwnerAgentConfig:
    public_key: Ed25519PublicKey
    identity_url: str
    bank_url: str
    task_board_url: str
    reputation_url: str
    court_url: str


@dataclass(frozen=True, slots=True)
class _OwnerAgent:
    config: _OwnerAgentConfig
    agent_id: str | None


def _task_id(index: int) -> str:
    return f"t-00000000-0000-4000-8000-{index:012d}"


def _operation_id(index: int) -> str:
    return f"op-00000000-0000-4000-8000-{index:012d}"


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _owner_for_action(action: str) -> RuntimeOwner:
    poster_action = action in {"accept_bid", "approve_task", "dispute_task"}
    return RuntimeOwner(
        schema_version=1,
        environment_id="local-integration",
        role="task-feeder" if poster_action else "math-worker",
        profile="task-feeder" if poster_action else "mathbot_openai",
        handle="poster-alice" if poster_action else "worker-alice",
        registered_agent_id=_POSTER_ID if poster_action else _WORKER_ID,
        key_fingerprint=("2" if poster_action else "1") * 64,
        service_urls=_URLS,
    )


def _store(root: Path, *, action: str = "submit_bid") -> FileTaskIntentStore:
    return FileTaskIntentStore(
        root_directory=root,
        owner=_owner_for_action(action),
        diagnostic_limit=8,
        max_record_bytes=65_536,
    )


def _new_intent(
    action: str = "submit_bid",
    *,
    task_id: str = _TASK_ID,
    operation_id: str = _OPERATION_ID,
) -> NewTaskIntent:
    cases: dict[
        str,
        tuple[str, str, str, dict[str, object], dict[str, object]],
    ] = {
        "submit_bid": (
            "bid",
            "open",
            "open",
            {"amount": 9},
            {
                "bid_id": f"bid-{operation_id[3:]}",
                "bidder_id": _WORKER_ID,
                "amount": 9,
            },
        ),
        "accept_bid": (
            "acceptance",
            "open",
            "accepted",
            {"bid_id": "bid-selected"},
            {
                "bid_id": "bid-selected",
                "worker_id": _WORKER_ID,
                "reward": 100,
                "bid_amount": 90,
            },
        ),
        "submit_deliverable": (
            "submission",
            "accepted",
            "submitted",
            {},
            {"worker_id": _WORKER_ID},
        ),
        "approve_task": (
            "review-decision",
            "submitted",
            "approved",
            {},
            {
                "worker_id": _WORKER_ID,
                "reward": 100,
                "effect_result_reference": operation_id,
            },
        ),
        "dispute_task": (
            "review-decision",
            "submitted",
            "disputed",
            {"reason": "Résultat incomplet ∑"},
            {
                "worker_id": _WORKER_ID,
                "reason": "Résultat incomplet ∑",
                "dispute_id": "clm-runtime-181",
                "effect_result_reference": "clm-runtime-181",
            },
        ),
    }
    slot, source, target, action_arguments, result_facts = cases[action]
    public_arguments = {
        "task_id": task_id,
        "operation_id": operation_id,
        "expected_lifecycle_version": 7,
        **action_arguments,
    }
    actor_id = (
        _POSTER_ID if action in {"accept_bid", "approve_task", "dispute_task"} else _WORKER_ID
    )
    frozen_task_facts: dict[str, object]
    if action == "submit_bid":
        frozen_task_facts = {"bidder_id": _WORKER_ID}
    else:
        frozen_task_facts = {
            "poster_id": _POSTER_ID,
            "worker_id": _WORKER_ID,
            "escrow_id": _ESCROW_ID,
            "accepted_reward": 100,
            "selected_bid_amount": 90,
        }
    context: dict[str, object] = {
        "response_kind": "bid" if action == "submit_bid" else "task_mutation",
        "operation_id": operation_id,
        "task_id": task_id,
        "command": action,
        "source_status": source,
        "source_lifecycle_version": 7,
        "target_status": target,
        "target_lifecycle_version": 7 if action == "submit_bid" else 8,
        "result_facts": result_facts,
        "actor_id": actor_id,
        "frozen_task_facts": frozen_task_facts,
    }
    if action != "submit_bid":
        native_arguments: dict[str, object]
        if action == "accept_bid":
            native_arguments = {
                "bid_id": "bid-selected",
                "worker_id": _WORKER_ID,
                "escrow_id": _ESCROW_ID,
                "reward": 100,
                "bid_amount": 90,
            }
        elif action == "submit_deliverable":
            native_arguments = {"worker_id": _WORKER_ID}
        elif action == "approve_task":
            native_arguments = {
                "poster_id": _POSTER_ID,
                "worker_id": _WORKER_ID,
                "escrow_id": _ESCROW_ID,
                "reward": 100,
                "source_reference": f"task:{task_id}:approved",
            }
        else:
            native_arguments = {
                "poster_id": _POSTER_ID,
                "worker_id": _WORKER_ID,
                "escrow_id": _ESCROW_ID,
                "reward": 100,
                "reason": "Résultat incomplet ∑",
            }
        context["request_sha256"] = _canonical_sha256(
            {
                "protocol_version": 1,
                "task_id": task_id,
                "command": action,
                "expected_status": source,
                "expected_lifecycle_version": 7,
                "actor_kind": "agent",
                "actor_id": actor_id,
                "arguments": native_arguments,
            }
        )
    return NewTaskIntent(
        task_id=task_id,
        slot=slot,
        action=action,
        source_status=source,
        target_status=target,
        operation_id=operation_id,
        expected_lifecycle_version=7,
        public_arguments=public_arguments,
        acknowledgment_context=context,
    )


def _bid_ack(intent: NewTaskIntent | FrozenTaskIntent) -> dict[str, object]:
    return {
        "bid_id": f"bid-{intent.operation_id[3:]}",
        "task_id": intent.task_id,
        "bidder_id": _WORKER_ID,
        "amount": 9,
        "submitted_at": "2026-10-18T12:00:00Z",
    }


def _agent_with_result(result: object) -> SimpleNamespace:
    agent = SimpleNamespace(
        submit_bid=AsyncMock(return_value=result),
        accept_bid=AsyncMock(return_value=result),
        submit_deliverable=AsyncMock(return_value=result),
        approve_task=AsyncMock(return_value=result),
        dispute_task=AsyncMock(return_value=result),
        list_tasks=AsyncMock(),
    )
    return agent


def _prepare(intent: NewTaskIntent) -> AsyncMock:
    return AsyncMock(return_value=intent)


def _slot_for(action: str) -> str:
    return {
        "submit_bid": "bid",
        "accept_bid": "acceptance",
        "submit_deliverable": "submission",
        "approve_task": "review-decision",
        "dispute_task": "review-decision",
    }[action]


def _expected_sdk_call(intent: NewTaskIntent) -> call:
    common = {
        "operation_id": intent.operation_id,
        "expected_lifecycle_version": intent.expected_lifecycle_version,
    }
    if intent.action == "submit_bid":
        return call(intent.task_id, 9, **common)
    if intent.action == "accept_bid":
        return call(intent.task_id, "bid-selected", **common)
    if intent.action == "dispute_task":
        return call(intent.task_id, "Résultat incomplet ∑", **common)
    return call(intent.task_id, **common)


def _http_status_error(status_code: int, body: object) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://task-board.example/tasks/mutation")
    response = httpx.Response(status_code, request=request, json=body)
    return httpx.HTTPStatusError(
        f"Task Board returned {status_code}",
        request=request,
        response=response,
    )


@pytest.fixture(autouse=True)
def _required_runtime_command_interface(request: pytest.FixtureRequest) -> None:
    """Keep downstream cases required while one capability assertion owns the RED."""
    if request.node.name == "test_runtime_task_command_interface_is_available":
        return
    assert _commands is not None, (
        "Blocked by absent T-181 interface: base_agent.runtime_task_commands is required"
    )
    assert _missing_exports == [], (
        f"Blocked by incomplete T-181 runtime command interface: {_missing_exports}"
    )


def test_runtime_task_command_interface_is_available() -> None:
    assert _commands is not None, (
        "T-181 capability missing: base_agent.runtime_task_commands has not been implemented"
    )
    assert _missing_exports == [], (
        f"T-181 runtime command interface is incomplete: {_missing_exports}"
    )

    constructor = inspect.signature(_commands.RuntimeTaskCommands)
    assert tuple(constructor.parameters) == ("agent", "store")
    owner_factory = inspect.signature(_commands.runtime_owner_from_agent)
    assert tuple(owner_factory.parameters) == (
        "runtime_config",
        "agent",
        "role",
        "profile",
        "handle",
    )
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        and parameter.default is inspect.Parameter.empty
        for parameter in owner_factory.parameters.values()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("action", _ACTIONS)
async def test_execute_dispatches_exact_frozen_public_sdk_arguments(
    tmp_path: Path,
    action: str,
) -> None:
    assert _commands is not None
    intent = _new_intent(action)
    status_code = 201 if action == "submit_bid" else 200
    agent = _agent_with_result(
        TaskBoardCommandResponse(status_code=status_code, body={"malformed": True})
    )
    prepare = _prepare(intent)
    with _store(tmp_path, action=action) as store:
        coordinator = _commands.RuntimeTaskCommands(agent, store)
        observed = await coordinator.execute(
            task_id=intent.task_id,
            slot=intent.slot,
            prepare=prepare,
        )

    expected_call = _expected_sdk_call(intent)
    getattr(agent, action).assert_awaited_once_with(*expected_call.args, **expected_call.kwargs)
    for other_action in set(_ACTIONS) - {action}:
        getattr(agent, other_action).assert_not_awaited()
    prepare.assert_awaited_once_with()
    assert observed.operation_id == intent.operation_id
    assert observed.state == "pending"
    assert observed.diagnostics[-1] == {
        "kind": "pending_unknown",
        "status_code": status_code,
    }


@pytest.mark.asyncio
async def test_fresh_intent_is_durable_before_sdk_transport_and_reopens_completed(
    tmp_path: Path,
) -> None:
    assert _commands is not None
    intent = _new_intent()
    persisted_before_transport: bytes | None = None
    with _store(tmp_path) as store:

        async def send(*args: object, **kwargs: object) -> TaskBoardCommandResponse:
            nonlocal persisted_before_transport
            assert args == (intent.task_id, 9)
            assert kwargs == {
                "operation_id": intent.operation_id,
                "expected_lifecycle_version": 7,
            }
            stored = store.load(intent.task_id, intent.slot)
            assert stored is not None and stored.state == "pending"
            persisted_before_transport = store.intent_path(intent.task_id, intent.slot).read_bytes()
            return TaskBoardCommandResponse(status_code=201, body=_bid_ack(intent))

        agent = _agent_with_result(TaskBoardCommandResponse(500, {}))
        agent.submit_bid = AsyncMock(side_effect=send)
        coordinator = _commands.RuntimeTaskCommands(agent, store)
        completed = await coordinator.execute(
            task_id=intent.task_id,
            slot=intent.slot,
            prepare=_prepare(intent),
        )

    assert persisted_before_transport is not None
    assert completed.state == "completed"
    with _store(tmp_path) as reopened:
        assert reopened.load(intent.task_id, intent.slot) == completed


@pytest.mark.asyncio
async def test_existing_intent_load_precedes_and_bypasses_prepare(tmp_path: Path) -> None:
    assert _commands is not None
    intent = _new_intent()
    prepare = _prepare(_new_intent(operation_id=_operation_id(999)))
    agent = _agent_with_result(TaskBoardCommandResponse(201, _bid_ack(intent)))
    with _store(tmp_path) as store:
        stored = store.create(intent)
        coordinator = _commands.RuntimeTaskCommands(agent, store)
        completed = await coordinator.execute(
            task_id=intent.task_id,
            slot=intent.slot,
            prepare=prepare,
        )
    prepare.assert_not_awaited()
    expected_call = _expected_sdk_call(intent)
    agent.submit_bid.assert_awaited_once_with(*expected_call.args, **expected_call.kwargs)
    assert stored.operation_id == completed.operation_id == intent.operation_id
    assert completed.state == "completed"


@pytest.mark.asyncio
async def test_prepare_failure_prevents_journal_and_sdk_transport(tmp_path: Path) -> None:
    assert _commands is not None
    agent = _agent_with_result(TaskBoardCommandResponse(201, {}))
    prepare = AsyncMock(side_effect=RuntimeError("decision failed"))
    with _store(tmp_path) as store:
        coordinator = _commands.RuntimeTaskCommands(agent, store)
        with pytest.raises(RuntimeError, match="decision failed"):
            await coordinator.execute(task_id=_TASK_ID, slot="bid", prepare=prepare)
        assert store.load(_TASK_ID, "bid") is None
    agent.submit_bid.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_slot_concurrent_execute_has_one_prepare_and_transport(tmp_path: Path) -> None:
    assert _commands is not None
    intent = _new_intent()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def send(*args: object, **kwargs: object) -> TaskBoardCommandResponse:
        assert args == (intent.task_id, 9)
        assert kwargs["operation_id"] == intent.operation_id
        entered.set()
        await release.wait()
        return TaskBoardCommandResponse(201, _bid_ack(intent))

    agent = _agent_with_result(TaskBoardCommandResponse(500, {}))
    agent.submit_bid = AsyncMock(side_effect=send)
    prepare = _prepare(intent)
    with _store(tmp_path) as store:
        coordinator = _commands.RuntimeTaskCommands(agent, store)
        first = asyncio.create_task(
            coordinator.execute(task_id=_TASK_ID, slot="bid", prepare=prepare)
        )
        await asyncio.wait_for(entered.wait(), timeout=1)
        second_invoked = asyncio.Event()

        async def run_second() -> FrozenTaskIntent:
            second_invoked.set()
            return await coordinator.execute(
                task_id=_TASK_ID,
                slot="bid",
                prepare=prepare,
            )

        second = asyncio.create_task(run_second())
        await asyncio.wait_for(second_invoked.wait(), timeout=1)
        release.set()
        first_result, second_result = await asyncio.gather(first, second)
    assert first_result == second_result
    assert first_result.state == "completed"
    prepare.assert_awaited_once_with()
    agent.submit_bid.assert_awaited_once()


@pytest.mark.asyncio
async def test_independent_slots_do_not_share_one_async_lock(tmp_path: Path) -> None:
    assert _commands is not None
    intents = (
        _new_intent(task_id=_task_id(301), operation_id=_operation_id(301)),
        _new_intent(task_id=_task_id(302), operation_id=_operation_id(302)),
    )
    entered: set[str] = set()
    both_entered = asyncio.Event()

    async def send(
        task_id: str,
        amount: int,
        *,
        operation_id: str,
        expected_lifecycle_version: int,
    ) -> TaskBoardCommandResponse:
        assert amount == 9 and expected_lifecycle_version == 7
        entered.add(task_id)
        if len(entered) == 2:
            both_entered.set()
        await both_entered.wait()
        intent = next(item for item in intents if item.operation_id == operation_id)
        return TaskBoardCommandResponse(201, _bid_ack(intent))

    agent = _agent_with_result(TaskBoardCommandResponse(500, {}))
    agent.submit_bid = AsyncMock(side_effect=send)
    with _store(tmp_path) as store:
        coordinator = _commands.RuntimeTaskCommands(agent, store)
        results = await asyncio.wait_for(
            asyncio.gather(
                *(
                    coordinator.execute(
                        task_id=intent.task_id,
                        slot="bid",
                        prepare=_prepare(intent),
                    )
                    for intent in intents
                )
            ),
            timeout=1,
        )
    assert entered == {item.task_id for item in intents}
    assert {item.state for item in results} == {"completed"}


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [httpx.ReadTimeout, httpx.ConnectError])
async def test_request_error_returns_original_record_without_revision_or_byte_change(
    tmp_path: Path,
    error_type: type[httpx.RequestError],
) -> None:
    assert _commands is not None
    intent = _new_intent()
    request = httpx.Request("POST", "https://task-board.example/tasks/bid")
    error = error_type("response unavailable", request=request)
    agent = _agent_with_result(TaskBoardCommandResponse(500, {}))
    agent.submit_bid = AsyncMock(side_effect=error)
    with _store(tmp_path) as store:
        original = store.create(intent)
        path = store.intent_path(intent.task_id, intent.slot)
        original_bytes = path.read_bytes()
        coordinator = _commands.RuntimeTaskCommands(agent, store)
        observed = await coordinator.execute(
            task_id=intent.task_id,
            slot=intent.slot,
            prepare=_prepare(intent),
        )
        assert path.read_bytes() == original_bytes
    assert observed == original
    assert observed.revision == 1
    assert observed.diagnostics == ()


@pytest.mark.asyncio
async def test_http_status_error_records_actual_status_and_typed_body(tmp_path: Path) -> None:
    assert _commands is not None
    intent = _new_intent()
    body = {
        "error": "task_version_conflict",
        "message": "Task lifecycle version differs",
        "details": {
            "expected_lifecycle_version": 7,
            "actual_lifecycle_version": 8,
            "current_status": "open",
        },
    }
    agent = _agent_with_result(TaskBoardCommandResponse(500, {}))
    agent.submit_bid = AsyncMock(side_effect=_http_status_error(409, body))
    with _store(tmp_path) as store:
        coordinator = _commands.RuntimeTaskCommands(agent, store)
        observed = await coordinator.execute(
            task_id=intent.task_id,
            slot=intent.slot,
            prepare=_prepare(intent),
        )
    assert observed.state == "definitive_rejection"
    assert observed.revision == 2
    assert observed.diagnostics[-1] == {
        "kind": "definitive_nonapplication",
        "status_code": 409,
    }


@pytest.mark.asyncio
async def test_command_contract_error_records_actual_status_with_no_invented_body(
    tmp_path: Path,
) -> None:
    assert _commands is not None
    intent = _new_intent()
    agent = _agent_with_result(TaskBoardCommandResponse(500, {}))
    agent.submit_bid = AsyncMock(
        side_effect=TaskBoardCommandContractError(
            status_code=201,
            reason="Task Board command response is not valid JSON",
        )
    )
    with _store(tmp_path) as store:
        coordinator = _commands.RuntimeTaskCommands(agent, store)
        with patch.object(
            store,
            "record_acknowledgment",
            wraps=store.record_acknowledgment,
        ) as record:
            observed = await coordinator.execute(
                task_id=intent.task_id,
                slot=intent.slot,
                prepare=_prepare(intent),
            )
    record.assert_called_once_with(
        task_id=intent.task_id,
        slot=intent.slot,
        expected_revision=1,
        status_code=201,
        body=None,
    )
    assert observed.state == "pending"
    assert observed.revision == 2
    assert observed.diagnostics[-1] == {
        "kind": "pending_unknown",
        "status_code": 201,
    }


@pytest.mark.asyncio
async def test_unforeseen_sdk_error_propagates_without_journal_observation(
    tmp_path: Path,
) -> None:
    assert _commands is not None
    intent = _new_intent()
    agent = _agent_with_result(TaskBoardCommandResponse(500, {}))
    agent.submit_bid = AsyncMock(side_effect=RuntimeError("programming defect"))
    with _store(tmp_path) as store:
        original = store.create(intent)
        path = store.intent_path(intent.task_id, intent.slot)
        original_bytes = path.read_bytes()
        coordinator = _commands.RuntimeTaskCommands(agent, store)
        with pytest.raises(RuntimeError, match="programming defect"):
            await coordinator.execute(
                task_id=intent.task_id,
                slot=intent.slot,
                prepare=_prepare(intent),
            )
        assert path.read_bytes() == original_bytes
        assert store.load(intent.task_id, intent.slot) == original


@pytest.mark.asyncio
async def test_local_journal_error_propagates_before_sdk_transport(tmp_path: Path) -> None:
    assert _commands is not None
    intent = _new_intent()
    agent = _agent_with_result(TaskBoardCommandResponse(201, _bid_ack(intent)))
    with _store(tmp_path) as store:
        coordinator = _commands.RuntimeTaskCommands(agent, store)
        failure = RuntimeIntentError("storage_failure", "publication failed")
        with (
            patch.object(store, "create", side_effect=failure),
            pytest.raises(RuntimeIntentError, match="publication failed") as raised,
        ):
            await coordinator.execute(
                task_id=intent.task_id,
                slot=intent.slot,
                prepare=_prepare(intent),
            )
        assert store.load(intent.task_id, intent.slot) is None
    assert raised.value is failure
    agent.submit_bid.assert_not_awaited()


def _store_bid_state(
    store: FileTaskIntentStore,
    intent: NewTaskIntent,
    state: str,
) -> FrozenTaskIntent:
    current = store.create(intent)
    if state == "completed":
        return store.record_acknowledgment(
            task_id=intent.task_id,
            slot="bid",
            expected_revision=current.revision,
            status_code=201,
            body=_bid_ack(intent),
        )
    if state == "definitive_rejection":
        return store.record_acknowledgment(
            task_id=intent.task_id,
            slot="bid",
            expected_revision=current.revision,
            status_code=409,
            body={
                "error": "task_version_conflict",
                "message": "Task lifecycle version differs",
                "details": {
                    "expected_lifecycle_version": 7,
                    "actual_lifecycle_version": 8,
                    "current_status": "open",
                },
            },
        )
    if state == "attention_required":
        foreign = _bid_ack(intent)
        foreign["bid_id"] = "bid-foreign"
        return store.record_acknowledgment(
            task_id=intent.task_id,
            slot="bid",
            expected_revision=current.revision,
            status_code=201,
            body=foreign,
        )
    if state == "blocked":
        return store.record_acknowledgment(
            task_id=intent.task_id,
            slot="bid",
            expected_revision=current.revision,
            status_code=409,
            body={
                "error": "transition_pending",
                "message": "Another operation owns the task",
                "details": {"winning_operation_id": _operation_id(999)},
            },
        )
    raise AssertionError(f"unsupported fixture state: {state}")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    ["completed", "definitive_rejection", "attention_required"],
)
async def test_terminal_or_attention_state_never_calls_sdk_or_prepare(
    tmp_path: Path,
    state: str,
) -> None:
    assert _commands is not None
    intent = _new_intent()
    agent = _agent_with_result(TaskBoardCommandResponse(201, _bid_ack(intent)))
    prepare = _prepare(intent)
    with _store(tmp_path) as store:
        stored = _store_bid_state(store, intent, state)
        coordinator = _commands.RuntimeTaskCommands(agent, store)
        observed = await coordinator.execute(
            task_id=intent.task_id,
            slot="bid",
            prepare=prepare,
        )
    assert observed == stored
    prepare.assert_not_awaited()
    agent.submit_bid.assert_not_awaited()


@pytest.mark.asyncio
async def test_blocked_intent_gets_one_exact_attempt_per_replay_pass(tmp_path: Path) -> None:
    assert _commands is not None
    intent = _new_intent()
    request = httpx.Request("POST", "https://task-board.example/tasks/bid")
    agent = _agent_with_result(TaskBoardCommandResponse(500, {}))
    agent.submit_bid = AsyncMock(
        side_effect=httpx.ReadTimeout("response unavailable", request=request)
    )
    with _store(tmp_path) as store:
        blocked = _store_bid_state(store, intent, "blocked")
        path = store.intent_path(intent.task_id, intent.slot)
        blocked_bytes = path.read_bytes()
        coordinator = _commands.RuntimeTaskCommands(agent, store)
        first_pass = await coordinator.replay_unfinished()
        second_pass = await coordinator.replay_unfinished()
        assert path.read_bytes() == blocked_bytes
    assert first_pass == (blocked,)
    assert second_pass == (blocked,)
    assert agent.submit_bid.await_count == 2
    assert agent.submit_bid.await_args_list == [
        _expected_sdk_call(intent),
        _expected_sdk_call(intent),
    ]


@pytest.mark.asyncio
async def test_replay_uses_sorted_known_work_without_remote_discovery(tmp_path: Path) -> None:
    assert _commands is not None
    intents = {
        "attention_required": _new_intent(task_id=_task_id(303), operation_id=_operation_id(303)),
        "pending": _new_intent(task_id=_task_id(301), operation_id=_operation_id(301)),
        "blocked": _new_intent(task_id=_task_id(302), operation_id=_operation_id(302)),
    }

    async def send(
        task_id: str,
        amount: int,
        *,
        operation_id: str,
        expected_lifecycle_version: int,
    ) -> TaskBoardCommandResponse:
        assert amount == 9 and expected_lifecycle_version == 7
        intent = next(item for item in intents.values() if item.operation_id == operation_id)
        assert task_id == intent.task_id
        return TaskBoardCommandResponse(201, _bid_ack(intent))

    agent = _agent_with_result(TaskBoardCommandResponse(500, {}))
    agent.submit_bid = AsyncMock(side_effect=send)
    with _store(tmp_path) as store:
        store.create(intents["pending"])
        blocked = _store_bid_state(store, intents["blocked"], "blocked")
        attention = _store_bid_state(store, intents["attention_required"], "attention_required")
        assert [item.task_id for item in store.unfinished()] == sorted(
            item.task_id for item in intents.values()
        )
        coordinator = _commands.RuntimeTaskCommands(agent, store)
        observed = await coordinator.replay_unfinished()

    assert [item.task_id for item in observed] == sorted(item.task_id for item in intents.values())
    by_task = {item.task_id: item for item in observed}
    assert by_task[intents["pending"].task_id].state == "completed"
    assert by_task[blocked.task_id].state == "completed"
    assert by_task[attention.task_id] == attention
    assert agent.submit_bid.await_args_list == [
        _expected_sdk_call(intents["pending"]),
        _expected_sdk_call(intents["blocked"]),
    ]
    agent.list_tasks.assert_not_awaited()


@pytest.mark.asyncio
async def test_replay_request_loss_does_not_starve_later_known_work(tmp_path: Path) -> None:
    assert _commands is not None
    first = _new_intent(task_id=_task_id(401), operation_id=_operation_id(401))
    second = _new_intent(task_id=_task_id(402), operation_id=_operation_id(402))
    request = httpx.Request("POST", "https://task-board.example/tasks/bid")

    async def send(
        task_id: str,
        amount: int,
        *,
        operation_id: str,
        expected_lifecycle_version: int,
    ) -> TaskBoardCommandResponse:
        assert amount == 9 and expected_lifecycle_version == 7
        if task_id == first.task_id:
            raise httpx.ReadTimeout("response unavailable", request=request)
        assert task_id == second.task_id and operation_id == second.operation_id
        return TaskBoardCommandResponse(201, _bid_ack(second))

    agent = _agent_with_result(TaskBoardCommandResponse(500, {}))
    agent.submit_bid = AsyncMock(side_effect=send)
    with _store(tmp_path) as store:
        original_first = store.create(first)
        store.create(second)
        coordinator = _commands.RuntimeTaskCommands(agent, store)
        observed = await coordinator.replay_unfinished()
    assert observed[0] == original_first
    assert observed[0].state == "pending" and observed[0].revision == 1
    assert observed[1].state == "completed"
    assert agent.submit_bid.await_count == 2


def test_runtime_owner_binds_exact_identity_public_key_and_five_urls(tmp_path: Path) -> None:
    assert _commands is not None
    public_key = Ed25519PrivateKey.generate().public_key()
    config = _OwnerAgentConfig(public_key=public_key, **_URLS)
    agent = _OwnerAgent(config=config, agent_id=_WORKER_ID)
    runtime_config = RuntimeStateConfig(
        environment_id="  Exact Environment / Blue  ",
        directory=tmp_path / "runtime-state",
        diagnostic_limit=8,
        max_record_bytes=65_536,
    )

    owner = _commands.runtime_owner_from_agent(
        runtime_config=runtime_config,
        agent=agent,
        role="math-worker",
        profile="mathbot_openai",
        handle="worker-alice",
    )

    raw_public_key = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
    assert owner == RuntimeOwner(
        schema_version=1,
        environment_id="  Exact Environment / Blue  ",
        role="math-worker",
        profile="mathbot_openai",
        handle="worker-alice",
        registered_agent_id=_WORKER_ID,
        key_fingerprint=hashlib.sha256(raw_public_key).hexdigest(),
        service_urls=_URLS,
    )
    assert dict(owner.service_urls) == _URLS


def test_runtime_owner_requires_registered_agent_without_network_or_gateway(
    tmp_path: Path,
) -> None:
    assert _commands is not None
    config = _OwnerAgentConfig(
        public_key=Ed25519PrivateKey.generate().public_key(),
        **_URLS,
    )
    agent = _OwnerAgent(config=config, agent_id=None)
    runtime_config = RuntimeStateConfig(
        environment_id="local-integration",
        directory=tmp_path / "runtime-state",
        diagnostic_limit=8,
        max_record_bytes=65_536,
    )
    with pytest.raises(RuntimeIntentError, match="registered") as raised:
        _commands.runtime_owner_from_agent(
            runtime_config=runtime_config,
            agent=agent,
            role="math-worker",
            profile="mathbot_openai",
            handle="worker-alice",
        )
    assert raised.value.reason == "invalid_input"
