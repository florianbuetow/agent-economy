"""Regression tests for feeder review intent preparation and replay."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, cast

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from service_auth import TaskBoardCommandResponse, public_key_to_b64
from service_auth.financial_identity import operation_id_for

from base_agent.runtime_config import RuntimeStateConfig
from base_agent.runtime_intents import FileTaskIntentStore
from base_agent.runtime_task_commands import RuntimeTaskCommands, runtime_owner_from_agent
from base_agent.runtime_task_intent_factory import review_intent
from task_feeder.reader import RawTask
from task_feeder.review import ReviewLoop

if TYPE_CHECKING:
    from pathlib import Path

    from base_agent.agent import BaseAgent

pytestmark = pytest.mark.unit

_TASK_ID = "t-00000000-0000-4000-8000-000000000708"
_POSTER_ID = "a-feeder-review-regressions"
_WORKER_ID = "a-worker-review-regressions"
_ESCROW_ID = "esc-review-regressions-708"
_URLS = MappingProxyType(
    {
        "identity_url": "https://identity.review.test/v1/",
        "bank_url": "https://bank.review.test/v1/",
        "task_board_url": "https://task-board.review.test/v1/",
        "reputation_url": "https://reputation.review.test/v1/",
        "court_url": "https://court.review.test/v1/",
    }
)


@dataclass(frozen=True, slots=True)
class _AgentConfig:
    identity_url: str
    bank_url: str
    task_board_url: str
    reputation_url: str
    court_url: str


class _PublicReviewAgent:
    """Public SDK fake with one deterministic registered feeder identity."""

    def __init__(self, payload: dict[str, object]) -> None:
        private_key = Ed25519PrivateKey.from_private_bytes(bytes([8]) * 32)
        self.agent_id = _POSTER_ID
        self.config = _AgentConfig(**_URLS)
        self._public_key_b64 = public_key_to_b64(private_key.public_key())
        self.payload = payload
        self.assets: list[dict[str, object]] = []
        self.asset_content = b""
        self.asset_list_error: Exception | None = None
        self.asset_download_error: Exception | None = None
        self.task_error: Exception | None = None
        self.get_task_calls = 0
        self.list_asset_calls = 0
        self.download_asset_calls = 0
        self.command_calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def get_public_key_b64(self) -> str:
        return self._public_key_b64

    async def get_task(self, task_id: str) -> dict[str, object]:
        assert task_id == _TASK_ID
        self.get_task_calls += 1
        if self.task_error is not None:
            raise self.task_error
        return self.payload

    def _auth_header(
        self, payload: dict[str, object], *, audience: str, resource: str
    ) -> dict[str, str]:
        assert audience == "task-board"
        assert payload["action"] in {"list_assets", "download_asset"}
        assert resource.startswith(f"GET /tasks/{_TASK_ID}/assets")
        return {"Authorization": "Bearer fixture"}

    async def _request(
        self, method: str, url: str, *, headers: dict[str, str]
    ) -> dict[str, object]:
        assert method == "GET" and url.endswith("/assets")
        assert headers == {"Authorization": "Bearer fixture"}
        return {"assets": await self._list_assets(_TASK_ID)}

    async def list_assets(self, task_id: str) -> list[dict[str, object]]:
        return await self._list_assets(task_id)

    async def download_asset(self, task_id: str, asset_id: str) -> bytes:
        return await self._download_asset(task_id, asset_id)

    async def _list_assets(self, task_id: str) -> list[dict[str, object]]:
        assert task_id == _TASK_ID
        self.list_asset_calls += 1
        if self.asset_list_error is not None:
            raise self.asset_list_error
        return self.assets

    async def _request_raw(
        self, method: str, url: str, *, headers: dict[str, str]
    ) -> httpx.Response:
        assert method == "GET" and headers == {"Authorization": "Bearer fixture"}
        asset_id = url.rsplit("/", 1)[1]
        return httpx.Response(200, content=await self._download_asset(_TASK_ID, asset_id))

    async def _download_asset(self, task_id: str, asset_id: str) -> bytes:
        assert task_id == _TASK_ID
        assert asset_id == "asset-review-708"
        self.download_asset_calls += 1
        if self.asset_download_error is not None:
            raise self.asset_download_error
        return self.asset_content

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

    async def _command(
        self,
        action: str,
        args: tuple[object, ...],
        kwargs: dict[str, object],
    ) -> TaskBoardCommandResponse:
        self.command_calls.append((action, args, kwargs))
        request = httpx.Request("POST", self.config.task_board_url)
        raise httpx.ReadTimeout("acknowledgment unavailable", request=request)


def _submitted_task(**answer_fields: object) -> dict[str, object]:
    return {
        "task_id": _TASK_ID,
        "poster_id": _POSTER_ID,
        "worker_id": _WORKER_ID,
        "status": "submitted",
        "lifecycle_version": 13,
        "escrow_id": _ESCROW_ID,
        "accepted_reward": 100,
        "accepted_bid_amount": 70,
        **answer_fields,
    }


def _raw_task() -> RawTask:
    return RawTask(
        title="Solve 2+2",
        spec="What is 2+2?",
        solutions=["4"],
        level=1,
        problem_type="addition_positive",
        solution_note=None,
    )


def _store(tmp_path: Path, agent: _PublicReviewAgent) -> FileTaskIntentStore:
    runtime_config = RuntimeStateConfig(
        environment_id="mp07-review-regression-test",
        directory=tmp_path,
        diagnostic_limit=8,
        max_record_bytes=65_536,
    )
    owner = runtime_owner_from_agent(
        runtime_config=runtime_config,
        agent=cast("BaseAgent", agent),
        role="task-feeder",
        profile="task-feeder",
        handle="feeder-review-regressions",
    )
    return FileTaskIntentStore(
        root_directory=tmp_path,
        owner=owner,
        diagnostic_limit=8,
        max_record_bytes=65_536,
    )


def _loop(agent: _PublicReviewAgent, store: FileTaskIntentStore) -> ReviewLoop:
    commands = RuntimeTaskCommands(cast("BaseAgent", agent), store)
    return ReviewLoop(
        agent=cast("BaseAgent", agent),
        task_map={_TASK_ID: _raw_task()},
        commands=commands,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_boundary", ["list", "download"])
async def test_asset_read_failure_aborts_preparation_without_intent_or_transport(
    tmp_path: Path,
    failure_boundary: str,
) -> None:
    agent = _PublicReviewAgent(_submitted_task())
    expected_message = f"{failure_boundary} asset unavailable"
    if failure_boundary == "list":
        agent.asset_list_error = RuntimeError(expected_message)
    else:
        agent.assets = [{"asset_id": "asset-review-708"}]
        agent.asset_download_error = RuntimeError(expected_message)

    with _store(tmp_path, agent) as store:
        loop = _loop(agent, store)
        with pytest.raises(RuntimeError, match=expected_message):
            await loop.review_one(_TASK_ID)
        with pytest.raises(RuntimeError, match=expected_message):
            await loop.review_one(_TASK_ID)
        assert store.load(_TASK_ID, "review-decision") is None

    assert agent.get_task_calls == 2
    assert agent.list_asset_calls == 2
    assert agent.download_asset_calls == (0 if failure_boundary == "list" else 2)
    assert agent.command_calls == []


@pytest.mark.asyncio
async def test_saved_review_decision_replays_without_task_or_asset_reads(tmp_path: Path) -> None:
    task = _submitted_task()
    agent = _PublicReviewAgent(task)
    agent.task_error = RuntimeError("current task must not be read")
    agent.asset_list_error = RuntimeError("asset list must not be read")
    agent.asset_download_error = RuntimeError("asset body must not be read")
    operation_id = operation_id_for("task-review", (_TASK_ID, "13"))

    with _store(tmp_path, agent) as store:
        saved = store.create(
            review_intent(
                task=task,
                actor_id=_POSTER_ID,
                action="approve_task",
                reason=None,
                operation_id=operation_id,
            )
        )
        result = await _loop(agent, store).review_one(_TASK_ID)
        replayed = store.load(_TASK_ID, "review-decision")

    assert result == "pending"
    assert replayed == saved
    assert agent.get_task_calls == 0
    assert agent.list_asset_calls == 0
    assert agent.download_asset_calls == 0
    assert agent.command_calls == [
        (
            "approve_task",
            (_TASK_ID,),
            {"operation_id": operation_id, "expected_lifecycle_version": 13},
        )
    ]


@pytest.mark.asyncio
async def test_empty_earlier_answer_projection_uses_later_valid_submission_answer(
    tmp_path: Path,
) -> None:
    agent = _PublicReviewAgent(
        _submitted_task(
            submitted_answer="",
            submission={"answer": "4"},
            deliverable={"answer": "wrong"},
        )
    )
    agent.asset_list_error = AssertionError("valid payload answer must bypass assets")

    with _store(tmp_path, agent) as store:
        result = await _loop(agent, store).review_one(_TASK_ID)
        saved = store.load(_TASK_ID, "review-decision")

    assert result == "pending"
    assert saved is not None
    assert saved.action == "approve_task"
    assert saved.source_status == "submitted"
    assert saved.expected_lifecycle_version == 13
    serialized = saved.as_json()
    context = cast("dict[str, object]", serialized["acknowledgment_context"])
    assert context["actor_id"] == _POSTER_ID
    assert context["frozen_task_facts"] == {
        "poster_id": _POSTER_ID,
        "worker_id": _WORKER_ID,
        "escrow_id": _ESCROW_ID,
        "accepted_reward": 100,
        "selected_bid_amount": 70,
    }
    assert agent.list_asset_calls == 0
    assert [call[0] for call in agent.command_calls] == ["approve_task"]
