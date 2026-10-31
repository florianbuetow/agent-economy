"""Durable demo rollout contracts beyond the low-level RunStore slice."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from service_auth.financial_identity import operation_id_for
from service_auth.task_postings import TaskPostingIntent, TaskPostingResponse

from demo_replay.config import load_demo_settings
from demo_replay.config import PlatformConfig
from demo_replay.engine import ReplayEngine
from demo_replay.run_store import ActorDefinition, RunEnvironment, RunStore, RunStoreError
from demo_replay.task_operations import TaskIntent
from demo_replay.task_operations import canonical_json

pytestmark = pytest.mark.unit


def test_demo_settings_are_explicit_and_resolve_state_from_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "tools.yaml"
    config_path.parent.mkdir()
    config_path.write_text(
        """\
platform:
  identity_url: "https://identity.invalid"
  bank_url: "https://bank.invalid"
  task_board_url: "https://board.invalid"
  reputation_url: "https://reputation.invalid"
  court_url: "https://court.invalid"
  platform_key_path: "../keys/platform.key"
  token_ttl_seconds: 60
demo_replay:
  environment_id: "isolated-test"
  state_directory: "../state/demo"
  max_record_bytes: 1048576
  diagnostic_limit: 16
""",
        encoding="utf-8",
    )

    settings = load_demo_settings(config_path)

    assert settings.environment_id == "isolated-test"
    assert settings.state_directory == (tmp_path / "state" / "demo").absolute()
    assert settings.max_record_bytes == 1_048_576
    assert settings.diagnostic_limit == 16


def test_demo_settings_do_not_invent_missing_durable_values(tmp_path: Path) -> None:
    config_path = tmp_path / "tools.yaml"
    config_path.write_text(
        """\
platform:
  identity_url: "https://identity.invalid"
  bank_url: "https://bank.invalid"
  task_board_url: "https://board.invalid"
  reputation_url: "https://reputation.invalid"
  court_url: "https://court.invalid"
  platform_key_path: "platform.key"
  token_ttl_seconds: 60
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_demo_settings(config_path)


def test_task_intent_round_trips_without_a_live_agent() -> None:
    source = {
        "action": "accept_bid",
        "step_json": '{"action":"accept_bid","bidder":"bob","poster":"alice"}',
        "operation_id": "op-00000000-0000-4000-8000-000000000001",
        "expected_lifecycle_version": 3,
        "actor_handle": "alice",
        "actor_id": "a-alice",
        "actor_public_key": "ed25519:alice-key",
        "task_id": "t-00000000-0000-4000-8000-000000000002",
        "arguments_json": (
            '{"bid_amount":90,"bid_id":"bid-1","bidder_handle":"bob","worker_id":"a-bob"}'
        ),
        "observed_task_json": (
            '{"escrow_id":"esc-1","lifecycle_version":3,"poster_id":"a-alice",'
            '"reward":100,"status":"open"}'
        ),
    }

    restored = TaskIntent.from_json(source)

    assert restored.as_json() == source
    assert restored.actor_handle == "alice"


def _store_arguments(state: Path) -> dict[str, Any]:
    scenario = {
        "name": "generic durable step",
        "agents": [{"handle": "alice", "name": "Alice"}],
        "steps": [
            {"action": "register", "agent": "alice"},
            {
                "action": "fund",
                "agent": "alice",
                "amount": 50,
                "reference": "durable-fund",
            },
        ],
    }
    return {
        "state_directory": state.absolute(),
        "run_id": "run-00000000-0000-4000-8000-000000000011",
        "environment": RunEnvironment(
            environment_id="test",
            identity_url="https://identity.invalid",
            bank_url="https://bank.invalid",
            task_board_url="https://board.invalid",
            reputation_url="https://reputation.invalid",
            court_url="https://court.invalid",
            platform_public_fingerprint_sha256="a" * 64,
        ),
        "scenario_bytes": yaml.safe_dump(scenario, sort_keys=False).encode(),
        "scenario": scenario,
        "actor_definitions": (ActorDefinition(handle="alice", name="Alice"),),
        "effective_delay_seconds": 0.0,
        "max_record_bytes": 1_048_576,
        "diagnostic_limit": 16,
    }


def test_generic_step_requires_durable_terminal_readback(tmp_path: Path) -> None:
    arguments = _store_arguments(tmp_path / "state")
    with RunStore.open(**arguments) as store:
        actor = store.actors["alice"]
        store.record_registration(
            actor_handle="alice",
            response={
                "agent_id": "a-alice",
                "name": "Alice",
                "public_key": actor.public_key,
                "registered_at": "2026-10-22T00:00:00Z",
            },
        )
        step = arguments["scenario"]["steps"][1]
        operation_id = operation_id_for("demo-funding", ("a-alice", "durable-fund"))
        prepared = store.reserve_step(
            index=1,
            step=step,
            actor_handle="alice",
            intent={
                "operation_id": operation_id,
                "account_id": "a-alice",
                "amount": 50,
                "reference": "durable-fund",
            },
        )
        assert not store.completed
        dispatched = store.mark_dispatching(index=1, expected_revision=prepared.revision)
        with pytest.raises(RunStoreError, match="funding receipt"):
            store.finish_step(
                index=1,
                expected_revision=dispatched.revision,
                state="completed",
                result={"receipt": "tx-1"},
                diagnostic=None,
            )
        request = {
            "request_version": 1,
            "operation_kind": "credit",
            "account_id": "a-alice",
            "amount": 50,
            "reference": "durable-fund",
        }
        request_sha256 = hashlib.sha256(
            json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        result = {
            "outcome": "native_receipt",
            "receipt": {
                "receipt_version": 1,
                "operation_id": operation_id,
                "operation_kind": "credit",
                "request_sha256": request_sha256,
                "source_reference": "durable-fund",
                "committed_at": "2026-10-22T00:00:00Z",
                "event_id": 1,
                "escrow_id": None,
                "result": {
                    "account_id": "a-alice",
                    "amount": 50,
                    "balance_after": 50,
                    "reference": "durable-fund",
                },
                "legs": [
                    {
                        "role": "account",
                        "account_id": "a-alice",
                        "direction": "credit",
                        "amount": 50,
                        "transaction_id": "tx-1",
                        "balance_after": 50,
                    }
                ],
            },
            "current_escrow": None,
        }
        completed = store.finish_step(
            index=1,
            expected_revision=dispatched.revision,
            state="completed",
            result=result,
            diagnostic=None,
        )
        assert completed.state == "completed"
        assert store.completed

    with RunStore.open(**arguments) as reopened:
        restored = reopened.step(1)
        assert restored is not None
        assert restored.state == "completed"
        assert restored.result == result


def _open_scenario(state: Path, scenario: dict[str, Any], suffix: int) -> RunStore:
    return RunStore.open(
        state_directory=state.absolute(),
        run_id=f"run-00000000-0000-4000-8000-{suffix:012d}",
        environment=RunEnvironment(
            environment_id="binding-test",
            identity_url="https://identity.invalid",
            bank_url="https://bank.invalid",
            task_board_url="https://board.invalid",
            reputation_url="https://reputation.invalid",
            court_url="https://court.invalid",
            platform_public_fingerprint_sha256="b" * 64,
        ),
        scenario_bytes=yaml.safe_dump(scenario, sort_keys=False).encode(),
        scenario=scenario,
        actor_definitions=tuple(
            ActorDefinition(handle=actor["handle"], name=actor["name"])
            for actor in scenario["agents"]
        ),
        effective_delay_seconds=0.0,
        max_record_bytes=1_048_576,
        diagnostic_limit=32,
    )


def _bind_actor(store: RunStore, handle: str, agent_id: str) -> None:
    actor = store.actors[handle]
    store.record_registration(
        actor_handle=handle,
        response={
            "agent_id": agent_id,
            "name": actor.name,
            "public_key": actor.public_key,
            "registered_at": "2026-10-22T00:00:00Z",
        },
    )


def test_funding_actor_must_match_the_scenario_role(tmp_path: Path) -> None:
    step = {"action": "fund", "agent": "alice", "amount": 50, "reference": "fund-a"}
    scenario = {
        "name": "actor binding",
        "agents": [
            {"handle": "alice", "name": "Alice"},
            {"handle": "bob", "name": "Bob"},
        ],
        "steps": [step],
    }
    with _open_scenario(tmp_path / "state", scenario, 21) as store:
        _bind_actor(store, "alice", "a-alice")
        _bind_actor(store, "bob", "a-bob")
        with pytest.raises(RunStoreError, match="actor"):
            store.reserve_step(
                index=0,
                step=step,
                actor_handle="bob",
                intent={
                    "operation_id": operation_id_for("demo-funding", ("a-bob", "fund-a")),
                    "account_id": "a-bob",
                    "amount": 50,
                    "reference": "fund-a",
                },
            )


@pytest.mark.parametrize("arguments_json", ['{"amount":5}', '{"amount":"5"}'])
def test_bid_requires_completed_referenced_post_and_strict_arguments(
    tmp_path: Path, arguments_json: str
) -> None:
    post = {
        "action": "post_task",
        "poster": "alice",
        "ref": "primary",
        "title": "Primary",
        "spec": "Exact",
        "reward": 10,
        "bidding_deadline_seconds": 60,
        "acceptance_window_seconds": 60,
        "execution_deadline_seconds": 120,
        "review_deadline_seconds": 60,
    }
    bid = {"action": "bid", "bidder": "bob", "task_ref": "primary", "amount": 5}
    scenario = {
        "name": "dependency binding",
        "agents": [
            {"handle": "alice", "name": "Alice"},
            {"handle": "bob", "name": "Bob"},
        ],
        "steps": [post, bid],
    }
    with _open_scenario(tmp_path / "state", scenario, 22) as store:
        _bind_actor(store, "alice", "a-alice")
        _bind_actor(store, "bob", "a-bob")
        actor = store.actors["bob"]
        intent = TaskIntent.from_json(
            {
                "action": "bid",
                "step_json": canonical_json(bid),
                "operation_id": "op-00000000-0000-4000-8000-000000000023",
                "expected_lifecycle_version": 0,
                "actor_handle": "bob",
                "actor_id": "a-bob",
                "actor_public_key": actor.public_key,
                "task_id": "t-00000000-0000-4000-8000-000000000099",
                "arguments_json": arguments_json,
                "observed_task_json": canonical_json(
                    {
                        "task_id": "t-00000000-0000-4000-8000-000000000099",
                        "poster_id": "a-alice",
                        "status": "open",
                        "lifecycle_version": 0,
                        "escrow_id": "esc-primary",
                        "reward": 10,
                        "pending_transition": None,
                        "legacy_reconciliations": [],
                    }
                ),
            }
        )
        expected = "arguments" if '"5"' in arguments_json else "producer"
        with pytest.raises(RunStoreError, match=expected):
            store.reserve_step(
                index=1,
                step=bid,
                actor_handle="bob",
                intent=intent.as_json(),
            )


def test_blocked_posting_becomes_durable_attention(tmp_path: Path) -> None:
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
    scenario = {
        "name": "blocked posting",
        "agents": [{"handle": "alice", "name": "Alice"}],
        "steps": [step],
    }
    with _open_scenario(tmp_path / "state", scenario, 24) as store:
        _bind_actor(store, "alice", "a-alice")
        intent = TaskPostingIntent.from_json(
            {
                "protocol_version": 1,
                "operation_id": "op-00000000-0000-4000-8000-000000000024",
                "task_id": "t-00000000-0000-4000-8000-000000000024",
                "actor_kind": "agent",
                "actor_id": "a-alice",
                "lock_operation_id": operation_id_for(
                    "task-lock", ("t-00000000-0000-4000-8000-000000000024", "a-alice")
                ),
                "title": "Blocked",
                "spec": "Exact",
                "reward": 10,
                "bidding_deadline_seconds": 60,
                "acceptance_window_seconds": 60,
                "execution_deadline_seconds": 120,
                "review_deadline_seconds": 60,
            }
        )
        prepared = store.reserve_posting(index=0, step=step, actor_handle="alice", intent=intent)
        dispatched = store.mark_dispatching(index=0, expected_revision=prepared.revision)
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
            index=0,
            expected_revision=dispatched.revision,
            response=TaskPostingResponse(status_code=200, body=blocked),
        )
        assert terminal.state == "attention_required"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action",
    [
        "register",
        "fund",
        "post_task",
        "bid",
        "accept_bid",
        "upload_asset",
        "submit_deliverable",
        "approve",
        "dispute",
        "file_claim",
        "submit_rebuttal",
        "trigger_ruling",
        "feedback",
    ],
)
async def test_effecting_action_requires_open_store_before_transport(action: str) -> None:
    step = {"action": action}
    engine = ReplayEngine(
        {"name": "no fallback", "agents": [], "steps": [step]},
        PlatformConfig(
            identity_url="https://identity.invalid",
            bank_url="https://bank.invalid",
            task_board_url="https://board.invalid",
            reputation_url="https://reputation.invalid",
            court_url="https://court.invalid",
            platform_key_path=Path("unused.key"),
            token_ttl_seconds=60,
        ),
    )
    with pytest.raises(RuntimeError, match="explicit durable run"):
        await engine._execute_step(object(), step)  # type: ignore[arg-type]
