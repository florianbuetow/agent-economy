"""Plan item 0.3: Court ruling durability against real Gateway semantics.

The decision is persisted before any delivery, deliveries replay the stored
payload (same ``operation_id`` and ``expected_lifecycle_version``), a judging
lease admits exactly one run, and an expired lease is reclaimed.

Task Board's ``record_ruling`` is emulated exactly as Task Board performs it:
claim the native ``record_ruling`` task operation on the live Gateway (which
replays an identical request and rejects a different one under the same
``operation_id``), deliver the frozen bank split, then finalize from Gateway
evidence. Settlements are counted from the Gateway's own escrow writes.
"""

from __future__ import annotations

import asyncio
import sqlite3
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import httpx
import pytest
from db_gateway_service import task_time
from service_auth.signing import load_public_key, public_key_to_b64

from court_service.core.state import get_app_state
from court_service.judges import DisputeContext, JudgeVote
from court_service.judges.base import Judge
from tests.helpers import make_jws_token
from tests.integration.conftest import (
    PLATFORM_AGENT_ID,
    finalize_task_dispute,
    seed_task,
)

if TYPE_CHECKING:
    from court_service.core.state import AppState

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_NOW = "2026-03-01T09:30:00Z"


class _CountingJudge(Judge):
    """Deterministic pct, non-deterministic reasoning (like a real LLM)."""

    def __init__(self, worker_pct: int, delay_seconds: float) -> None:
        self.calls = 0
        self._worker_pct = worker_pct
        self._delay_seconds = delay_seconds

    async def evaluate(self, context: DisputeContext) -> JudgeVote:
        _ = context
        self.calls += 1
        if self._delay_seconds > 0:
            await asyncio.sleep(self._delay_seconds)
        return JudgeVote(
            judge_id="judge-0",
            worker_pct=self._worker_pct,
            spec_quality="satisfied",
            spec_quality_explanation=f"Spec finding attempt {self.calls}",
            delivery_quality="satisfied",
            delivery_quality_explanation=f"Delivery finding attempt {self.calls}",
            reasoning=f"Reasoning attempt {self.calls}",
            voted_at=_NOW,
        )


class _GatewayTaskBoard:
    """Task Board's record_ruling, performed against the live Gateway."""

    def __init__(self, gateway_client: httpx.AsyncClient, db_path: str) -> None:
        self._client = gateway_client
        self._db_path = db_path
        self.settlements: list[str] = []
        self.calls = 0

    def _task(self, task_id: str) -> dict[str, Any]:
        connection = sqlite3.connect(self._db_path)
        try:
            row = connection.execute(
                "SELECT poster_id, worker_id, escrow_id, reward FROM board_tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
        finally:
            connection.close()
        return {"poster_id": row[0], "worker_id": row[1], "escrow_id": row[2], "reward": row[3]}

    @staticmethod
    def _raise_for(response: httpx.Response) -> None:
        if response.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"{response.status_code}", request=response.request, response=response
            )

    async def record_ruling(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        task = self._task(task_id)
        claimed = await self._client.post(
            "/board/task-operations",
            json={
                "protocol_version": 1,
                "operation_id": payload["operation_id"],
                "task_id": task_id,
                "command": "record_ruling",
                "expected_status": "disputed",
                "expected_lifecycle_version": payload["expected_lifecycle_version"],
                "actor_kind": "platform",
                "actor_id": PLATFORM_AGENT_ID,
                "arguments": {
                    **task,
                    "ruling_id": payload["ruling_id"],
                    "worker_pct": payload["worker_pct"],
                    "summary": payload["ruling_summary"],
                    "source_reference": f"task:{task_id}:ruling:{payload['ruling_id']}",
                },
            },
        )
        self._raise_for(claimed)
        envelope: dict[str, Any] = claimed.json()
        if envelope["operation_state"] != "completed":
            financial = envelope["effect_intent"]["financial_request"]
            split = await self._client.post(
                "/bank/escrow/split",
                json={
                    "operation_id": envelope["effect_operation_id"],
                    "escrow_id": financial["escrow_id"],
                    "worker_account_id": financial["worker_account_id"],
                    "poster_account_id": financial["poster_account_id"],
                    "worker_pct": financial["worker_pct"],
                    "source_reference": financial["source_reference"],
                    "worker_tx_id": f"tx-{uuid.uuid4()}",
                    "poster_tx_id": f"tx-{uuid.uuid4()}",
                    "resolved_at": _NOW,
                    "event": {
                        "event_source": "central-bank",
                        "event_type": "escrow.split",
                        "timestamp": _NOW,
                        "task_id": task_id,
                        "summary": f"Escrow split for {task_id}",
                        "payload": "{}",
                    },
                },
            )
            self._raise_for(split)
            self.settlements.append(str(envelope["effect_operation_id"]))
            finalized = await self._client.post(
                f"/board/task-operations/{envelope['operation_id']}/finalize", json={}
            )
            self._raise_for(finalized)
            envelope = finalized.json()
        return {"task_id": task_id, "operation": envelope}


def _read(db_path: str, query: str, params: tuple[object, ...]) -> list[tuple[Any, ...]]:
    connection = sqlite3.connect(db_path)
    try:
        return [tuple(row) for row in connection.execute(query, params).fetchall()]
    finally:
        connection.close()


async def _register_platform_identity(
    gateway_client: httpx.AsyncClient, gateway_db_path: str
) -> None:
    public_key = load_public_key(Path(gateway_db_path).parent / "platform.pub")
    response = await gateway_client.post(
        "/identity/agents",
        json={
            "agent_id": PLATFORM_AGENT_ID,
            "name": "Platform",
            "public_key": f"ed25519:{public_key_to_b64(public_key)}",
            "registered_at": "2026-03-01T09:00:00Z",
            "event": {
                "event_source": "identity",
                "event_type": "agent.registered",
                "timestamp": "2026-03-01T09:00:00Z",
                "agent_id": PLATFORM_AGENT_ID,
                "summary": "platform registered",
                "payload": "{}",
            },
        },
    )
    assert response.status_code == 201, response.text


async def _open_worker_account(gateway_client: httpx.AsyncClient, worker_id: str) -> None:
    response = await gateway_client.post(
        "/bank/accounts",
        json={
            "account_id": worker_id,
            "created_at": "2026-03-01T09:00:00Z",
            "balance": 0,
            "event": {
                "event_source": "central-bank",
                "event_type": "account.created",
                "timestamp": "2026-03-01T09:00:00Z",
                "agent_id": worker_id,
                "summary": f"Created account for {worker_id}",
                "payload": "{}",
            },
        },
    )
    assert response.status_code == 201, response.text


async def _rebutted_dispute(
    client: httpx.AsyncClient,
    gateway_client: httpx.AsyncClient,
    gateway_db_path: str,
) -> str:
    suffix = uuid.uuid4().hex[:8]
    claimant_id = f"a-claimant-{suffix}"
    respondent_id = f"a-respondent-{suffix}"
    task_id = f"t-{uuid.uuid4()}"
    reason = "The worker did not deliver as specified."
    seeded = await seed_task(
        gateway_client,
        task_id=task_id,
        poster_id=claimant_id,
        worker_id=respondent_id,
        reason=reason,
    )
    await _open_worker_account(gateway_client, respondent_id)
    await _register_platform_identity(gateway_client, gateway_db_path)
    filed = await client.post(
        "/disputes/file",
        json={
            "token": make_jws_token(
                {
                    "action": "file_dispute",
                    "task_id": task_id,
                    "claimant_id": claimant_id,
                    "respondent_id": respondent_id,
                    "claim": reason,
                    "escrow_id": seeded.escrow_id,
                },
                kid=PLATFORM_AGENT_ID,
            )
        },
    )
    assert filed.status_code == 201, filed.text
    dispute_id = str(filed.json()["dispute_id"])
    await finalize_task_dispute(gateway_client, seeded)
    rebutted = await client.post(
        f"/disputes/{dispute_id}/rebuttal",
        json={
            "token": make_jws_token(
                {
                    "action": "submit_rebuttal",
                    "dispute_id": dispute_id,
                    "rebuttal": "The specification was ambiguous.",
                },
                kid=PLATFORM_AGENT_ID,
            )
        },
    )
    assert rebutted.status_code == 200, rebutted.text
    return dispute_id


async def _rule(client: httpx.AsyncClient, dispute_id: str) -> httpx.Response:
    token = make_jws_token(
        {"action": "trigger_ruling", "dispute_id": dispute_id}, kid=PLATFORM_AGENT_ID
    )
    return await client.post(f"/disputes/{dispute_id}/rule", json={"token": token})


def _wire(state: AppState, task_board: _GatewayTaskBoard, judge: _CountingJudge) -> None:
    assert state.platform_agent is not None
    state.platform_agent.record_ruling = AsyncMock(side_effect=task_board.record_ruling)
    state.judges = [judge]


def _feedback_failing_once() -> AsyncMock:
    calls = {"n": 0}

    async def submit(_payload: dict[str, Any]) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("reputation down")
        return {"status": "ok"}

    return AsyncMock(side_effect=submit)


async def test_reputation_failure_after_settlement_resumes_without_rejudging(
    client: httpx.AsyncClient,
    gateway_client: httpx.AsyncClient,
    gateway_db_path: str,
) -> None:
    dispute_id = await _rebutted_dispute(client, gateway_client, gateway_db_path)
    state = get_app_state()
    task_board = _GatewayTaskBoard(gateway_client, gateway_db_path)
    judge = _CountingJudge(worker_pct=70, delay_seconds=0)
    _wire(state, task_board, judge)
    assert state.platform_agent is not None
    state.platform_agent.submit_platform_feedback = _feedback_failing_once()

    first = await _rule(client, dispute_id)
    assert first.status_code == 502
    assert len(task_board.settlements) == 1
    stored = _read(
        gateway_db_path,
        "SELECT worker_pct FROM court_rulings WHERE claim_id=?",
        (dispute_id,),
    )
    assert stored == [(70,)], "decision must survive a delivery failure"

    second = await _rule(client, dispute_id)
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "ruled"
    assert second.json()["worker_pct"] == 70
    assert judge.calls == 1
    assert len(task_board.settlements) == 1
    claim = _read(
        gateway_db_path, "SELECT status FROM court_claims WHERE claim_id=?", (dispute_id,)
    )
    assert claim == [("ruled",)]


async def test_reclaim_sweep_resumes_settled_ruling(
    client: httpx.AsyncClient,
    gateway_client: httpx.AsyncClient,
    gateway_db_path: str,
) -> None:
    """After Task Board settled, its periodic trigger stops; Court's sweep resumes."""
    dispute_id = await _rebutted_dispute(client, gateway_client, gateway_db_path)
    state = get_app_state()
    task_board = _GatewayTaskBoard(gateway_client, gateway_db_path)
    judge = _CountingJudge(worker_pct=70, delay_seconds=0)
    _wire(state, task_board, judge)
    assert state.platform_agent is not None
    state.platform_agent.submit_platform_feedback = _feedback_failing_once()
    assert (await _rule(client, dispute_id)).status_code == 502

    assert state.dispute_service is not None
    resumed = await state.dispute_service.reclaim_stalled_rulings(
        judges=state.judges or [],
        platform_agent=state.platform_agent,
        load_task=AsyncMock(side_effect=AssertionError("decided rulings need no task")),
    )

    assert resumed == [dispute_id]
    assert judge.calls == 1
    assert len(task_board.settlements) == 1
    dispute = (await client.get(f"/disputes/{dispute_id}")).json()
    assert dispute["status"] == "ruled"


async def test_two_concurrent_triggers_produce_one_ruling(
    client: httpx.AsyncClient,
    gateway_client: httpx.AsyncClient,
    gateway_db_path: str,
) -> None:
    dispute_id = await _rebutted_dispute(client, gateway_client, gateway_db_path)
    state = get_app_state()
    task_board = _GatewayTaskBoard(gateway_client, gateway_db_path)
    judge = _CountingJudge(worker_pct=70, delay_seconds=0.3)
    _wire(state, task_board, judge)

    first, second = await asyncio.gather(_rule(client, dispute_id), _rule(client, dispute_id))

    assert sorted([first.status_code, second.status_code]) == [200, 409]
    assert judge.calls == 1
    assert len(task_board.settlements) == 1
    rulings = _read(
        gateway_db_path, "SELECT ruling_id FROM court_rulings WHERE claim_id=?", (dispute_id,)
    )
    assert len(rulings) == 1


async def test_expired_judging_lease_is_reclaimed(
    client: httpx.AsyncClient,
    gateway_client: httpx.AsyncClient,
    gateway_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispute_id = await _rebutted_dispute(client, gateway_client, gateway_db_path)
    state = get_app_state()
    task_board = _GatewayTaskBoard(gateway_client, gateway_db_path)
    judge = _CountingJudge(worker_pct=70, delay_seconds=0)
    _wire(state, task_board, judge)
    assert state.store is not None

    # A run that crashed while judging leaves a live lease behind.
    state.store.acquire_judging_lease(dispute_id, "lease-crashed", 60)
    held = await _rule(client, dispute_id)
    assert held.status_code == 409

    later = task_time.utc_now_us() + 61 * 1_000_000
    monkeypatch.setattr(task_time, "utc_now_us", lambda: later)
    reclaimed = await _rule(client, dispute_id)
    assert reclaimed.status_code == 200, reclaimed.text
    assert reclaimed.json()["status"] == "ruled"
    assert judge.calls == 1
    assert len(task_board.settlements) == 1
