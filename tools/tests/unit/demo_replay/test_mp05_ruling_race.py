"""Ruling recovery adopts only exact authoritative Court results."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from demo_replay import clients
from test_mp07_task_operations import REASON, TASK, _engine, _response, _task

pytestmark = pytest.mark.unit


def _steps() -> tuple[dict[str, object], dict[str, object]]:
    dispute = {
        "action": "dispute",
        "poster": "alice",
        "task_ref": "task",
        "reason": REASON,
    }
    ruling = {"action": "trigger_ruling", "agent": "alice", "task_ref": "task"}
    return dispute, ruling


def _ruled() -> dict[str, object]:
    return {
        "dispute_id": "clm-demo",
        "task_id": TASK,
        "status": "ruled",
        "worker_pct": 50,
        "ruling_summary": "Exact immutable ruling",
        "ruled_at": "2026-10-22T00:00:00Z",
    }


async def _prepared_engine(monkeypatch: pytest.MonkeyPatch) -> tuple[object, dict[str, object]]:
    dispute, ruling = _steps()
    engine = _engine([dispute, ruling])
    engine.platform = MagicMock(agent_id="a-platform")
    monkeypatch.setattr(clients, "get_task", AsyncMock(return_value=_task("submitted", 2)))
    monkeypatch.setattr(
        clients,
        "dispute_task",
        AsyncMock(
            side_effect=lambda *_args, **kwargs: _response(
                "dispute", kwargs["operation_id"], 2, pending=False
            )
        ),
    )
    await engine._execute_step(MagicMock(), dispute)
    return engine, ruling


@pytest.mark.asyncio
async def test_fresh_ruling_adopts_authoritative_completion_before_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, ruling = await _prepared_engine(monkeypatch)
    trigger = AsyncMock()
    monkeypatch.setattr(clients, "get_dispute", AsyncMock(return_value=_ruled()))
    monkeypatch.setattr(clients, "trigger_ruling", trigger)

    await engine._execute_step(MagicMock(), ruling)

    trigger.assert_not_awaited()
    stored = engine._require_store().step(len(engine._scenario_steps) - 1)
    assert stored is not None and stored.state == "completed"


@pytest.mark.asyncio
async def test_ruling_post_409_rereads_and_adopts_exact_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, ruling = await _prepared_engine(monkeypatch)
    pending = {"dispute_id": "clm-demo", "task_id": TASK, "status": "rebuttal_pending"}
    get_dispute = AsyncMock(side_effect=[pending, _ruled()])
    request = httpx.Request("POST", "https://court.invalid/disputes/clm-demo/rule")
    conflict = httpx.Response(409, json={"error": "dispute_already_ruled"}, request=request)
    trigger = AsyncMock(
        side_effect=httpx.HTTPStatusError("already ruled", request=request, response=conflict)
    )
    monkeypatch.setattr(clients, "get_dispute", get_dispute)
    monkeypatch.setattr(clients, "trigger_ruling", trigger)

    await engine._execute_step(MagicMock(), ruling)

    assert get_dispute.await_count == 2
    trigger.assert_awaited_once()
    stored = engine._require_store().step(len(engine._scenario_steps) - 1)
    assert stored is not None and stored.state == "completed"


@pytest.mark.asyncio
async def test_ruling_post_409_with_unresolved_evidence_requires_attention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, ruling = await _prepared_engine(monkeypatch)
    pending = {"dispute_id": "clm-demo", "task_id": TASK, "status": "rebuttal_pending"}
    judging = {"dispute_id": "clm-demo", "task_id": TASK, "status": "judging"}
    request = httpx.Request("POST", "https://court.invalid/disputes/clm-demo/rule")
    conflict = httpx.Response(409, json={"error": "conflict"}, request=request)
    monkeypatch.setattr(clients, "get_dispute", AsyncMock(side_effect=[pending, judging]))
    monkeypatch.setattr(
        clients,
        "trigger_ruling",
        AsyncMock(
            side_effect=httpx.HTTPStatusError("conflict", request=request, response=conflict)
        ),
    )

    with pytest.raises(RuntimeError, match="operator attention"):
        await engine._execute_step(MagicMock(), ruling)

    stored = engine._require_store().step(len(engine._scenario_steps) - 1)
    assert stored is not None and stored.state == "attention_required"


@pytest.mark.asyncio
async def test_fresh_ruling_allows_reserved_rebuttal_submitted_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, ruling = await _prepared_engine(monkeypatch)
    eligible = {
        "dispute_id": "clm-demo",
        "task_id": TASK,
        "status": "rebuttal_submitted",
    }
    trigger = AsyncMock(return_value=_ruled())
    monkeypatch.setattr(clients, "get_dispute", AsyncMock(return_value=eligible))
    monkeypatch.setattr(clients, "trigger_ruling", trigger)

    await engine._execute_step(MagicMock(), ruling)

    trigger.assert_awaited_once()
    stored = engine._require_store().step(len(engine._scenario_steps) - 1)
    assert stored is not None and stored.state == "completed"
