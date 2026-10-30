"""MP-07 durable native-intent integration for the Task Feeder loops."""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from service_auth import TaskBoardCommandResponse, public_key_to_b64
from service_auth.financial_identity import operation_id_for

from base_agent.runtime_config import RuntimeStateConfig
from base_agent.runtime_intents import (
    FileTaskIntentStore,
    FrozenTaskIntent,
    NewTaskIntent,
)
from base_agent.runtime_task_commands import RuntimeTaskCommands, runtime_owner_from_agent
from task_feeder.acceptance import AcceptanceLoop
from task_feeder.config import AcceptanceConfig
from task_feeder.reader import RawTask
from task_feeder.review import ReviewLoop

if TYPE_CHECKING:
    from pathlib import Path

    from base_agent.agent import BaseAgent

pytestmark = pytest.mark.unit

_TASK_ID = "t-00000000-0000-4000-8000-000000000707"
_POSTER_ID = "a-feeder-native-intents"
_WORKER_ID = "a-math-worker-native-intents"
_ESCROW_ID = "esc-native-intents-707"
_URLS = MappingProxyType(
    {
        "identity_url": "https://identity.example.test/v1/",
        "bank_url": "https://bank.example.test/v1/",
        "task_board_url": "https://task-board.example.test/v1/",
        "reputation_url": "https://reputation.example.test/v1/",
        "court_url": "https://court.example.test/v1/",
    }
)
_NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class _AgentConfig:
    identity_url: str
    bank_url: str
    task_board_url: str
    reputation_url: str
    court_url: str


CommandHook = Callable[
    [str, tuple[object, ...], dict[str, object]], Awaitable[TaskBoardCommandResponse]
]


class _PublicFeederAgent:
    """Small fake of public SDK boundaries, bound to one real deterministic key."""

    def __init__(self) -> None:
        private_key = Ed25519PrivateKey.from_private_bytes(bytes([7]) * 32)
        self.agent_id = _POSTER_ID
        self.config = _AgentConfig(**_URLS)
        self._public_key_b64 = public_key_to_b64(private_key.public_key())
        self.events: list[str] = []
        self.tasks: list[dict[str, object]] = []
        self.bids: list[dict[str, object]] = []
        self.delivery_scores: dict[str, int | None] = {}
        self.task_payload: dict[str, object] = {}
        self.assets: list[dict[str, object]] = []
        self.asset_content = b""
        self.command_hook: CommandHook = self._lose_response

    def get_public_key_b64(self) -> str:
        return self._public_key_b64

    async def _lose_response(
        self,
        action: str,
        args: tuple[object, ...],
        kwargs: dict[str, object],
    ) -> TaskBoardCommandResponse:
        del action, args, kwargs
        request = httpx.Request("POST", self.config.task_board_url)
        raise httpx.ReadTimeout("acknowledgment lost", request=request)

    async def _command(
        self, action: str, args: tuple[object, ...], kwargs: dict[str, object]
    ) -> TaskBoardCommandResponse:
        self.events.append(action)
        return await self.command_hook(action, args, kwargs)

    async def list_tasks(self, **filters: object) -> list[dict[str, object]]:
        self.events.append(f"list_tasks:{filters['status']}")
        return self.tasks

    async def list_bids(self, task_id: str) -> list[dict[str, object]]:
        assert task_id == _TASK_ID
        self.events.append("list_bids")
        return self.bids

    async def get_task(self, task_id: str) -> dict[str, object]:
        assert task_id == _TASK_ID
        self.events.append("get_task")
        return self.task_payload

    def _auth_header(
        self, payload: dict[str, object], *, audience: str, resource: str
    ) -> dict[str, str]:
        assert audience == "task-board"
        assert payload["action"] in {"list_assets", "download_asset"}
        assert resource.startswith(f"GET /tasks/{_TASK_ID}/assets")
        return {"Authorization": "Bearer fixture"}

    async def _request(
        self, method: str, url: str, *, headers: dict[str, str] | None = None
    ) -> dict[str, object]:
        if url.endswith("/quality"):
            assert method == "GET" and headers is None
            agent_id = url.rsplit("/", 2)[1]
            self.events.append(f"quality:{agent_id}")
            score = self.delivery_scores.get(agent_id)
            return {
                "agent_id": agent_id,
                "formula_version": "quality-v1",
                "delivery_quality": {"rated": score is not None, "score": score},
            }
        assert method == "GET" and url.endswith("/assets")
        assert headers == {"Authorization": "Bearer fixture"}
        return {"assets": await self._list_assets(_TASK_ID)}

    async def _list_assets(self, task_id: str) -> list[dict[str, object]]:
        assert task_id == _TASK_ID
        self.events.append("list_assets")
        return self.assets

    async def _request_raw(
        self, method: str, url: str, *, headers: dict[str, str]
    ) -> httpx.Response:
        assert method == "GET" and headers == {"Authorization": "Bearer fixture"}
        asset_id = url.rsplit("/", 1)[1]
        return httpx.Response(200, content=await self._download_asset(_TASK_ID, asset_id))

    async def _download_asset(self, task_id: str, asset_id: str) -> bytes:
        assert task_id == _TASK_ID
        self.events.append(f"download_asset:{asset_id}")
        return self.asset_content

    async def accept_bid(
        self,
        task_id: str,
        bid_id: str,
        *,
        operation_id: str,
        expected_lifecycle_version: int,
    ) -> TaskBoardCommandResponse:
        return await self._command(
            "accept_bid",
            (task_id, bid_id),
            {
                "operation_id": operation_id,
                "expected_lifecycle_version": expected_lifecycle_version,
            },
        )

    async def approve_task(
        self,
        task_id: str,
        *,
        operation_id: str,
        expected_lifecycle_version: int,
    ) -> TaskBoardCommandResponse:
        return await self._command(
            "approve_task",
            (task_id,),
            {
                "operation_id": operation_id,
                "expected_lifecycle_version": expected_lifecycle_version,
            },
        )

    async def dispute_task(
        self,
        task_id: str,
        reason: str,
        *,
        operation_id: str,
        expected_lifecycle_version: int,
    ) -> TaskBoardCommandResponse:
        return await self._command(
            "dispute_task",
            (task_id, reason),
            {
                "operation_id": operation_id,
                "expected_lifecycle_version": expected_lifecycle_version,
            },
        )


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _acceptance_config() -> AcceptanceConfig:
    return AcceptanceConfig(
        acceptance_after_seconds=30,
        acceptance_poll_interval_seconds=5,
        min_bids_to_accept=1,
        bidding_deadline_seconds=120,
    )


def _open_task() -> dict[str, object]:
    return {
        "task_id": _TASK_ID,
        "poster_id": _POSTER_ID,
        "status": "open",
        "lifecycle_version": 7,
        "created_at": "2026-10-18T11:59:00Z",
        "escrow_id": _ESCROW_ID,
        "reward": 100,
        "accepted_reward": None,
        "accepted_bid_amount": None,
    }


def _submitted_task(answer: str) -> dict[str, object]:
    return {
        "task_id": _TASK_ID,
        "poster_id": _POSTER_ID,
        "worker_id": _WORKER_ID,
        "status": "submitted",
        "lifecycle_version": 11,
        "escrow_id": _ESCROW_ID,
        "reward": 100,
        "accepted_reward": 100,
        "accepted_bid_amount": 60,
        "submitted_answer": answer,
    }


def _bid(bid_id: str, worker_id: str, amount: int) -> dict[str, object]:
    return {"bid_id": bid_id, "bidder_id": worker_id, "amount": amount}


def _raw_task() -> RawTask:
    return RawTask(
        title="Solve 2+2",
        spec="What is 2+2?",
        solutions=["4"],
        level=1,
        problem_type="addition_positive",
        solution_note=None,
    )


def _store(tmp_path: Path, agent: _PublicFeederAgent) -> FileTaskIntentStore:
    runtime_config = RuntimeStateConfig(
        environment_id="mp07-feeder-test",
        directory=tmp_path,
        diagnostic_limit=8,
        max_record_bytes=65_536,
    )
    owner = runtime_owner_from_agent(
        runtime_config=runtime_config,
        agent=cast("BaseAgent", agent),
        role="task-feeder",
        profile="task-feeder",
        handle="feeder-native-intents",
    )
    return FileTaskIntentStore(
        root_directory=tmp_path,
        owner=owner,
        diagnostic_limit=8,
        max_record_bytes=65_536,
    )


def _new_intent(
    action: str,
    *,
    operation_id: str,
    reason: str | None = None,
) -> NewTaskIntent:
    source_version = 7 if action == "accept_bid" else 11
    target = {"accept_bid": "accepted", "approve_task": "approved", "dispute_task": "disputed"}[
        action
    ]
    selected_bid = "bid-selected"
    selected_amount = 60
    public_arguments: dict[str, object] = {
        "task_id": _TASK_ID,
        "operation_id": operation_id,
        "expected_lifecycle_version": source_version,
    }
    if action == "accept_bid":
        public_arguments["bid_id"] = selected_bid
    if action == "dispute_task":
        assert reason is not None
        public_arguments["reason"] = reason
    frozen = {
        "poster_id": _POSTER_ID,
        "worker_id": _WORKER_ID,
        "escrow_id": _ESCROW_ID,
        "accepted_reward": 100,
        "selected_bid_amount": selected_amount,
    }
    if action == "accept_bid":
        native_arguments = {
            "bid_id": selected_bid,
            "worker_id": _WORKER_ID,
            "escrow_id": _ESCROW_ID,
            "reward": 100,
            "bid_amount": selected_amount,
        }
        result_facts: dict[str, object] = {
            "bid_id": selected_bid,
            "worker_id": _WORKER_ID,
            "reward": 100,
            "bid_amount": selected_amount,
        }
    elif action == "approve_task":
        native_arguments = {
            "poster_id": _POSTER_ID,
            "worker_id": _WORKER_ID,
            "escrow_id": _ESCROW_ID,
            "reward": 100,
            "source_reference": f"task:{_TASK_ID}:approved",
        }
        result_facts = {
            "worker_id": _WORKER_ID,
            "reward": 100,
            "effect_result_reference": operation_id,
        }
    else:
        assert reason is not None
        native_arguments = {
            "poster_id": _POSTER_ID,
            "worker_id": _WORKER_ID,
            "escrow_id": _ESCROW_ID,
            "reward": 100,
            "reason": reason,
        }
        result_facts = {"worker_id": _WORKER_ID, "reason": reason}
    native_command = {
        "protocol_version": 1,
        "task_id": _TASK_ID,
        "command": action,
        "expected_status": "open" if action == "accept_bid" else "submitted",
        "expected_lifecycle_version": source_version,
        "actor_kind": "agent",
        "actor_id": _POSTER_ID,
        "arguments": native_arguments,
    }
    return NewTaskIntent(
        task_id=_TASK_ID,
        slot="acceptance" if action == "accept_bid" else "review-decision",
        action=action,
        source_status="open" if action == "accept_bid" else "submitted",
        target_status=target,
        operation_id=operation_id,
        expected_lifecycle_version=source_version,
        public_arguments=public_arguments,
        acknowledgment_context={
            "response_kind": "task_mutation",
            "operation_id": operation_id,
            "task_id": _TASK_ID,
            "command": action,
            "source_status": "open" if action == "accept_bid" else "submitted",
            "source_lifecycle_version": source_version,
            "target_status": target,
            "target_lifecycle_version": source_version + 1,
            "result_facts": result_facts,
            "actor_id": _POSTER_ID,
            "frozen_task_facts": frozen,
            "request_sha256": _canonical_sha256(native_command),
        },
    )


def _completion_ack(intent: FrozenTaskIntent) -> dict[str, object]:
    serialized = intent.as_json()
    context = cast("dict[str, object]", serialized["acknowledgment_context"])
    frozen = cast("dict[str, object]", context["frozen_task_facts"])
    result_facts = cast("dict[str, object]", context["result_facts"])
    result = {
        "task_id": intent.task_id,
        "command": intent.action,
        "status": intent.target_status,
        "lifecycle_version": intent.expected_lifecycle_version + 1,
        "event_id": 707,
        **result_facts,
    }
    effect_kind = "none"
    effect_state = "none"
    effect_operation_id: str | None = None
    effect_result_reference: str | None = None
    effect_intent: dict[str, object] | None = None
    if intent.action == "approve_task":
        financial = {
            "request_version": 1,
            "operation_kind": "escrow_release",
            "escrow_id": _ESCROW_ID,
            "source_reference": f"task:{_TASK_ID}:approved",
            "recipient_account_id": _WORKER_ID,
        }
        effect_kind = "bank_release"
        effect_state = "succeeded"
        effect_operation_id = intent.operation_id
        effect_result_reference = intent.operation_id
        effect_intent = {
            "financial_request": financial,
            "financial_request_sha256": _canonical_sha256(financial),
            "expected_escrow": {
                "escrow_id": _ESCROW_ID,
                "task_id": _TASK_ID,
                "payer_account_id": _POSTER_ID,
                "amount": 100,
            },
        }
    elif intent.action == "dispute_task":
        dispute_id = "clm-native-intents-707"
        reason = cast("dict[str, object]", serialized["public_arguments"])["reason"]
        result.update(dispute_id=dispute_id, effect_result_reference=dispute_id)
        effect_kind = "court_claim"
        effect_state = "succeeded"
        effect_result_reference = dispute_id
        effect_intent = {
            "court_claim": {
                "task_id": _TASK_ID,
                "claimant_id": _POSTER_ID,
                "respondent_id": _WORKER_ID,
                "reason": reason,
            },
            "expected_escrow": {
                "escrow_id": _ESCROW_ID,
                "task_id": _TASK_ID,
                "payer_account_id": _POSTER_ID,
                "amount": 100,
            },
        }
    operation = {
        "protocol_version": 1,
        "operation_id": intent.operation_id,
        "task_id": intent.task_id,
        "command": intent.action,
        "request_sha256": context["request_sha256"],
        "operation_state": "completed",
        "source_status": intent.source_status,
        "source_lifecycle_version": intent.expected_lifecycle_version,
        "target_status": intent.target_status,
        "target_lifecycle_version": intent.expected_lifecycle_version + 1,
        "effect_kind": effect_kind,
        "effect_state": effect_state,
        "effect_operation_id": effect_operation_id,
        "effect_result_reference": effect_result_reference,
        "effect_intent": effect_intent,
        "event_id": 707,
        "result": result,
    }
    return {
        "task_id": intent.task_id,
        "status": intent.target_status,
        "lifecycle_version": intent.expected_lifecycle_version + 1,
        "worker_id": frozen["worker_id"],
        "operation": operation,
    }


def _commands(agent: _PublicFeederAgent, store: FileTaskIntentStore) -> RuntimeTaskCommands:
    return RuntimeTaskCommands(cast("BaseAgent", agent), store)


def _acceptance_loop(agent: _PublicFeederAgent, commands: RuntimeTaskCommands) -> AcceptanceLoop:
    return AcceptanceLoop(
        agent=cast("BaseAgent", agent),
        config=_acceptance_config(),
        now=lambda: _NOW,
        commands=commands,
    )


def _review_loop(
    agent: _PublicFeederAgent,
    commands: RuntimeTaskCommands,
    task_map: dict[str, RawTask] | None = None,
) -> ReviewLoop:
    return ReviewLoop(
        agent=cast("BaseAgent", agent),
        task_map={_TASK_ID: _raw_task()} if task_map is None else task_map,
        commands=commands,
    )


def test_loops_require_one_runtime_task_commands_dependency() -> None:
    acceptance = inspect.signature(AcceptanceLoop)
    review = inspect.signature(ReviewLoop)
    assert tuple(acceptance.parameters) == ("agent", "config", "now", "commands")
    assert tuple(review.parameters) == ("agent", "task_map", "commands")
    assert acceptance.parameters["commands"].default is inspect.Parameter.empty
    assert review.parameters["commands"].default is inspect.Parameter.empty


@pytest.mark.asyncio
async def test_saved_acceptance_replays_before_bid_or_reputation_discovery(tmp_path: Path) -> None:
    agent = _PublicFeederAgent()
    operation_id = operation_id_for("task-accept", (_TASK_ID, "7"))
    with _store(tmp_path, agent) as store:
        saved = store.create(_new_intent("accept_bid", operation_id=operation_id))
        loop = _acceptance_loop(agent, _commands(agent, store))
        result = await loop.accept_one({"task_id": _TASK_ID})

        replayed = store.load(_TASK_ID, "acceptance")

    assert result == "pending"
    assert replayed == saved
    assert agent.events == ["accept_bid"]


@pytest.mark.asyncio
async def test_new_acceptance_freezes_source_and_q16_winner_before_transport(
    tmp_path: Path,
) -> None:
    agent = _PublicFeederAgent()
    agent.task_payload = _open_task()
    agent.bids = [_bid("bid-b", "worker-b", 60), _bid("bid-a", _WORKER_ID, 60)]
    agent.delivery_scores = {"worker-b": 50, _WORKER_ID: 100}
    with _store(tmp_path, agent) as store:
        loop = _acceptance_loop(agent, _commands(agent, store))
        result = await loop.accept_one(_open_task())
        saved = store.load(_TASK_ID, "acceptance")

    assert result == "pending"
    assert saved is not None
    assert saved.action == "accept_bid"
    assert saved.expected_lifecycle_version == 7
    assert saved.as_json()["public_arguments"] == {
        "task_id": _TASK_ID,
        "bid_id": "bid-a",
        "operation_id": saved.operation_id,
        "expected_lifecycle_version": 7,
    }
    context = cast("dict[str, object]", saved.as_json()["acknowledgment_context"])
    assert context["frozen_task_facts"] == {
        "poster_id": _POSTER_ID,
        "worker_id": _WORKER_ID,
        "escrow_id": _ESCROW_ID,
        "accepted_reward": 100,
        "selected_bid_amount": 60,
    }
    assert agent.events[0] == "list_bids"
    assert set(agent.events[1:-2]) == {"quality:worker-b", f"quality:{_WORKER_ID}"}
    assert agent.events[-2] == "get_task"
    assert agent.events[-1] == "accept_bid"


@pytest.mark.asyncio
async def test_only_completed_acceptance_is_finalized(tmp_path: Path) -> None:
    agent = _PublicFeederAgent()
    agent.task_payload = _open_task()
    agent.bids = [_bid("bid-selected", _WORKER_ID, 60)]
    with _store(tmp_path, agent) as store:

        async def complete(
            action: str, args: tuple[object, ...], kwargs: dict[str, object]
        ) -> TaskBoardCommandResponse:
            del action, args, kwargs
            saved = store.load(_TASK_ID, "acceptance")
            assert saved is not None
            return TaskBoardCommandResponse(200, _completion_ack(saved))

        agent.command_hook = complete
        loop = _acceptance_loop(agent, _commands(agent, store))
        result = await loop.accept_one(_open_task())
        completed = store.load(_TASK_ID, "acceptance")

    assert result == "accepted"
    assert completed is not None and completed.state == "completed"
    assert _TASK_ID in loop._finalized


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["pending", "blocked", "attention_required"])
async def test_unfinished_acceptance_does_not_finalize(tmp_path: Path, state: str) -> None:
    agent = _PublicFeederAgent()
    operation_id = operation_id_for("task-accept", (_TASK_ID, "7"))
    with _store(tmp_path, agent) as store:
        saved = store.create(_new_intent("accept_bid", operation_id=operation_id))
        if state == "blocked":
            saved = store.record_acknowledgment(
                task_id=_TASK_ID,
                slot="acceptance",
                expected_revision=saved.revision,
                status_code=409,
                body={
                    "error": "transition_pending",
                    "message": "another operation owns the transition",
                    "details": {
                        "winning_operation_id": operation_id_for(
                            "task-accept", (_TASK_ID, "foreign")
                        )
                    },
                },
            )
        elif state == "attention_required":
            foreign = _completion_ack(saved)
            operation = cast("dict[str, object]", foreign["operation"])
            operation["operation_id"] = operation_id_for("task-accept", (_TASK_ID, "foreign"))
            saved = store.record_acknowledgment(
                task_id=_TASK_ID,
                slot="acceptance",
                expected_revision=saved.revision,
                status_code=200,
                body=foreign,
            )
        loop = _acceptance_loop(agent, _commands(agent, store))
        result = await loop.accept_one({"task_id": _TASK_ID})
        observed = store.load(_TASK_ID, "acceptance")

    assert result == state
    assert observed is not None and observed.operation_id == operation_id
    assert _TASK_ID not in loop._finalized


@pytest.mark.asyncio
async def test_definitive_acceptance_rejection_retains_original_operation_id(
    tmp_path: Path,
) -> None:
    agent = _PublicFeederAgent()
    operation_id = operation_id_for("task-accept", (_TASK_ID, "7"))
    with _store(tmp_path, agent) as store:
        pending = store.create(_new_intent("accept_bid", operation_id=operation_id))
        rejected = store.record_acknowledgment(
            task_id=_TASK_ID,
            slot="acceptance",
            expected_revision=pending.revision,
            status_code=409,
            body={
                "error": "invalid_status",
                "message": "task is no longer open",
                "details": {"expected_status": "open", "current_status": "expired"},
            },
        )
        loop = _acceptance_loop(agent, _commands(agent, store))
        first = await loop.accept_one({"task_id": _TASK_ID})
        second = await loop.accept_one({"task_id": _TASK_ID})

    assert first == second == "definitive_rejection"
    assert rejected.operation_id == operation_id
    assert agent.events == []
    assert _TASK_ID not in loop._finalized


@pytest.mark.asyncio
async def test_acceptance_poll_replays_known_work_before_filtered_open_list(tmp_path: Path) -> None:
    agent = _PublicFeederAgent()
    operation_id = operation_id_for("task-accept", (_TASK_ID, "7"))
    with _store(tmp_path, agent) as store:
        store.create(_new_intent("accept_bid", operation_id=operation_id))
        loop = _acceptance_loop(agent, _commands(agent, store))
        await loop.process_open_tasks()

    assert agent.events == ["accept_bid", "list_tasks:open"]


@pytest.mark.asyncio
async def test_saved_review_decision_precedes_oracle_task_and_asset_reads(tmp_path: Path) -> None:
    agent = _PublicFeederAgent()
    operation_id = operation_id_for("task-review", (_TASK_ID, "11"))
    with _store(tmp_path, agent) as store:
        saved = store.create(_new_intent("approve_task", operation_id=operation_id))
        loop = _review_loop(agent, _commands(agent, store), task_map={})
        result = await loop.review_one(_TASK_ID)
        replayed = store.load(_TASK_ID, "review-decision")

    assert result == "pending"
    assert replayed == saved
    assert agent.events == ["approve_task"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "action", "expected_result"),
    [("4", "approve_task", "approved"), ("41", "dispute_task", "disputed")],
)
async def test_completed_review_returns_only_its_frozen_decision(
    tmp_path: Path,
    answer: str,
    action: str,
    expected_result: str,
) -> None:
    agent = _PublicFeederAgent()
    agent.task_payload = _submitted_task(answer)
    with _store(tmp_path, agent) as store:

        async def complete(
            observed_action: str,
            args: tuple[object, ...],
            kwargs: dict[str, object],
        ) -> TaskBoardCommandResponse:
            del args, kwargs
            assert observed_action == action
            saved = store.load(_TASK_ID, "review-decision")
            assert saved is not None
            return TaskBoardCommandResponse(200, _completion_ack(saved))

        agent.command_hook = complete
        result = await _review_loop(agent, _commands(agent, store)).review_one(_TASK_ID)
        saved = store.load(_TASK_ID, "review-decision")

    assert result == expected_result
    assert saved is not None and saved.state == "completed" and saved.action == action


@pytest.mark.asyncio
async def test_dispute_freezes_exact_reason_without_predicting_court_id(tmp_path: Path) -> None:
    agent = _PublicFeederAgent()
    agent.task_payload = _submitted_task("41")
    expected_reason = "Expected one of ['4'] but got '41'"
    with _store(tmp_path, agent) as store:
        result = await _review_loop(agent, _commands(agent, store)).review_one(_TASK_ID)
        saved = store.load(_TASK_ID, "review-decision")

    assert result == "pending"
    assert saved is not None and saved.action == "dispute_task"
    serialized = saved.as_json()
    arguments = cast("dict[str, object]", serialized["public_arguments"])
    context = cast("dict[str, object]", serialized["acknowledgment_context"])
    assert arguments["reason"] == expected_reason
    assert context["result_facts"] == {"worker_id": _WORKER_ID, "reason": expected_reason}
    serialized = json.dumps(saved.as_json(), sort_keys=True)
    assert "dispute_id" not in serialized
    assert "effect_result_reference" not in serialized


@pytest.mark.asyncio
async def test_missing_oracle_without_saved_intent_stays_visible_and_does_not_mutate(
    tmp_path: Path,
) -> None:
    agent = _PublicFeederAgent()
    agent.task_payload = _submitted_task("4")
    with _store(tmp_path, agent) as store:
        loop = _review_loop(agent, _commands(agent, store), task_map={})
        with pytest.raises(LookupError, match=_TASK_ID):
            await loop.review_one(_TASK_ID)
        with pytest.raises(LookupError, match=_TASK_ID):
            await loop.review_one(_TASK_ID)
        assert store.load(_TASK_ID, "review-decision") is None

    assert agent.events == []


@pytest.mark.asyncio
async def test_review_poll_replays_known_work_before_filtered_submitted_list(
    tmp_path: Path,
) -> None:
    agent = _PublicFeederAgent()
    operation_id = operation_id_for("task-review", (_TASK_ID, "11"))
    with _store(tmp_path, agent) as store:
        store.create(_new_intent("approve_task", operation_id=operation_id))
        loop = _review_loop(agent, _commands(agent, store))

        original_list_tasks = agent.list_tasks

        async def stop_after_list(**filters: object) -> list[dict[str, object]]:
            loop.stop()
            return await original_list_tasks(**filters)

        agent.list_tasks = stop_after_list  # type: ignore[method-assign]
        with patch("task_feeder.review.asyncio.sleep", new=AsyncMock()) as sleep:
            await loop.run(interval_seconds=1, retry_backoff_seconds=1)

    assert agent.events == ["approve_task", "list_tasks:submitted"]
    sleep.assert_awaited_once_with(1)
