"""Durable generic actions bind exact frozen payloads after valid dependencies."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from service_auth.task_postings import TaskPostingResponse

from demo_replay.run_store import RunStoreError
from test_mp05_durable_rollout import _bind_actor, _open_scenario
from test_mp07_task_operations import TASK, _engine, _seed_posting

pytestmark = pytest.mark.unit


def test_upload_intent_binds_filename_and_content_hash() -> None:
    step = {
        "action": "upload_asset",
        "worker": "bob",
        "task_ref": "task",
        "filename": "expected.txt",
        "content": "expected bytes",
    }
    engine = _engine([step])
    store = engine._require_store()
    index = len(engine._scenario_steps) - 1

    with pytest.raises(RunStoreError, match="asset intent"):
        store.reserve_step(
            index=index,
            step=step,
            actor_handle="bob",
            intent={
                "task_id": TASK,
                "filename": "wrong.txt",
                "content_sha256": "0" * 64,
            },
        )


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("category", "delivery_quality"),
        ("rating", "dissatisfied"),
        ("comment", "changed"),
    ],
)
def test_feedback_intent_binds_scenario_payload(field: str, changed: str) -> None:
    step = {
        "action": "feedback",
        "agent": "alice",
        "task_ref": "task",
        "to_agent_id": "bob",
        "category": "spec_quality",
        "rating": "satisfied",
        "comment": "exact comment",
    }
    engine = _engine([step])
    store = engine._require_store()
    index = len(engine._scenario_steps) - 1
    intent = {
        "task_id": TASK,
        "to_agent_id": "a-bob",
        "category": "spec_quality",
        "rating": "satisfied",
        "comment": "exact comment",
    }
    intent[field] = changed

    with pytest.raises(RunStoreError, match="feedback intent"):
        store.reserve_step(
            index=index,
            step=step,
            actor_handle="alice",
            intent=intent,
        )


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("claim", "changed claim"),
        ("respondent_id", "a-foreign"),
        ("escrow_id", "esc-foreign"),
    ],
)
def test_claim_intent_binds_scenario_and_producer_facts(field: str, changed: str) -> None:
    step = {
        "action": "file_claim",
        "poster": "alice",
        "worker": "bob",
        "task_ref": "task",
        "claim": "exact claim",
    }
    engine = _engine([step])
    store = engine._require_store()
    index = len(engine._scenario_steps) - 1
    intent = {
        "task_id": TASK,
        "claimant_id": "a-alice",
        "respondent_id": "a-bob",
        "claim": "exact claim",
        "escrow_id": "esc-demo",
    }
    intent[field] = changed

    with pytest.raises(RunStoreError, match="claim intent"):
        store.reserve_step(
            index=index,
            step=step,
            actor_handle="alice",
            intent=intent,
        )


def test_claim_with_explicit_worker_does_not_require_assignment(tmp_path: Path) -> None:
    post = {
        "action": "post_task",
        "poster": "alice",
        "ref": "task",
        "title": "Demo task",
        "spec": "Exact task",
        "reward": 100,
        "bidding_deadline_seconds": 60,
        "acceptance_window_seconds": 60,
        "execution_deadline_seconds": 120,
        "review_deadline_seconds": 180,
    }
    claim = {
        "action": "file_claim",
        "poster": "alice",
        "worker": "bob",
        "task_ref": "task",
        "claim": "exact claim",
    }
    scenario = {
        "name": "explicit claim worker",
        "agents": [
            {"handle": "alice", "name": "Alice"},
            {"handle": "bob", "name": "Bob"},
        ],
        "steps": [post, claim],
    }
    state = tmp_path / "claim-state"
    with _open_scenario(state, scenario, 25) as store:
        _bind_actor(store, "alice", "a-alice")
        _bind_actor(store, "bob", "a-bob")
        _seed_posting(store, post)

        prepared = store.reserve_step(
            index=1,
            step=claim,
            actor_handle="alice",
            intent={
                "task_id": TASK,
                "claimant_id": "a-alice",
                "respondent_id": "a-bob",
                "claim": "exact claim",
                "escrow_id": "esc-demo",
            },
        )

        assert prepared.state == "prepared"


@pytest.mark.asyncio
async def test_blocked_posting_resume_stops_with_explicit_attention() -> None:
    step = {
        "action": "post_task",
        "poster": "alice",
        "ref": "blocked",
        "title": "Blocked",
        "spec": "Exact",
        "reward": 10,
        "bidding_deadline_seconds": 60,
        "acceptance_window_seconds": 60,
        "execution_deadline_seconds": 120,
        "review_deadline_seconds": 60,
    }
    engine = _engine([step])
    store = engine._require_store()
    index = len(engine._scenario_steps) - 1
    poster = engine.agents["alice"]
    intent = poster.prepare_task_posting("Blocked", "Exact", 10, 60, 60, 120, 60)
    prepared = store.reserve_posting(
        index=index,
        step=step,
        actor_handle="alice",
        intent=intent,
    )
    dispatched = store.mark_dispatching(index=index, expected_revision=prepared.revision)
    blocked = {
        "protocol_version": 1,
        "operation_id": intent.operation_id,
        "task_id": intent.task_id,
        "request_sha256": intent.request_sha256,
        "publication_state": "blocked_integrity",
        "lock_operation_id": intent.lock_operation_id,
        "escrow_id": None,
        "admitted_at": "2026-10-22T00:00:00.000000Z",
        "published_at": None,
        "result": None,
        "error": {
            "error": "publication_integrity_blocked",
            "message": "Publication evidence requires reconciliation",
            "details": {"reason": "receipt_invalid"},
        },
    }
    terminal = store.complete_posting(
        index=index,
        expected_revision=dispatched.revision,
        response=TaskPostingResponse(200, blocked),
    )
    assert terminal.state == "attention_required"
    get_status = AsyncMock()
    submit = AsyncMock()
    poster.get_task_posting = get_status  # type: ignore[method-assign]
    poster.submit_task_posting = submit  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="operator attention"):
        await engine._execute_step(MagicMock(), step)

    get_status.assert_not_awaited()
    submit.assert_not_awaited()
