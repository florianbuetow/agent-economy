"""Regression coverage for reviewed MP-05 run-store invariants."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml
from demo_replay.run_store import ActorDefinition, RunEnvironment, RunStore, RunStoreError
from service_auth.financial_identity import operation_id_for
from service_auth.task_postings import TaskPostingIntent

pytestmark = pytest.mark.unit

_RUN_ID = "run-00000000-0000-4000-8000-000000000511"
_AGENT_ID = "a-poster"
_SCENARIO: dict[str, object] = {
    "name": "regressions",
    "agents": [{"handle": "poster", "name": "Poster"}],
    "steps": [
        {
            "action": "post_task",
            "poster": "poster",
            "title": "Durable task",
            "spec": "Preserve this intent.",
            "reward": 10,
            "bidding_deadline_seconds": 60,
            "acceptance_window_seconds": 61,
            "execution_deadline_seconds": 120,
            "review_deadline_seconds": 62,
        }
    ],
}
_SCENARIO_BYTES = yaml.safe_dump(_SCENARIO, sort_keys=False).encode()


def _arguments(state_directory: Path) -> dict[str, object]:
    return {
        "state_directory": state_directory,
        "run_id": _RUN_ID,
        "environment": RunEnvironment(
            environment_id="local-regression",
            identity_url="http://127.0.0.1:8001",
            bank_url="http://127.0.0.1:8002",
            task_board_url="http://127.0.0.1:8003",
            reputation_url="http://127.0.0.1:8004",
            court_url="http://127.0.0.1:8005",
            platform_public_fingerprint_sha256="a" * 64,
        ),
        "scenario_bytes": _SCENARIO_BYTES,
        "scenario": _SCENARIO,
        "actor_definitions": (ActorDefinition(handle="poster", name="Poster"),),
        "effective_delay_seconds": 0.0,
        "max_record_bytes": 65_536,
        "diagnostic_limit": 2_048,
    }


def _bind(store: RunStore) -> None:
    actor = store.actors["poster"]
    store.record_registration(
        actor_handle="poster",
        response={
            "agent_id": _AGENT_ID,
            "name": actor.name,
            "public_key": actor.public_key,
            "registered_at": "2026-10-22T10:15:30Z",
        },
    )


def _intent() -> TaskPostingIntent:
    step = _SCENARIO["steps"]
    assert isinstance(step, list)
    terms = step[0]
    assert isinstance(terms, dict)
    task_id = "t-00000000-0000-4000-8000-000000000512"
    return TaskPostingIntent.from_json(
        {
            "protocol_version": 1,
            "operation_id": operation_id_for("demo-posting", (task_id, _AGENT_ID)),
            "task_id": task_id,
            "actor_kind": "agent",
            "actor_id": _AGENT_ID,
            "lock_operation_id": operation_id_for("task-lock", (task_id, _AGENT_ID)),
            "title": terms["title"],
            "spec": terms["spec"],
            "reward": terms["reward"],
            "bidding_deadline_seconds": terms["bidding_deadline_seconds"],
            "acceptance_window_seconds": terms["acceptance_window_seconds"],
            "execution_deadline_seconds": terms["execution_deadline_seconds"],
            "review_deadline_seconds": terms["review_deadline_seconds"],
        }
    )


def _reserve(store: RunStore) -> object:
    steps = _SCENARIO["steps"]
    assert isinstance(steps, list)
    step = steps[0]
    assert isinstance(step, dict)
    return store.reserve_posting(
        index=0,
        step=step,
        actor_handle="poster",
        intent=_intent(),
    )


def test_missing_published_manifest_is_not_reconstructed(tmp_path: Path) -> None:
    arguments = _arguments(tmp_path / "state")
    with RunStore.open(**arguments) as store:  # type: ignore[arg-type]
        _bind(store)
        manifest = store.run_directory / "run.json"
    manifest.unlink()

    with pytest.raises(RunStoreError) as raised:
        RunStore.open(**arguments)  # type: ignore[arg-type]

    assert raised.value.reason == "corrupt_record"
    assert not manifest.exists()


def test_hardlinked_private_key_is_rejected_without_repair(tmp_path: Path) -> None:
    arguments = _arguments(tmp_path / "state")
    with RunStore.open(**arguments) as store:  # type: ignore[arg-type]
        private_key = store.actors["poster"].private_key_path
        duplicate = private_key.with_suffix(".duplicate")
    os.link(private_key, duplicate)

    with pytest.raises(RunStoreError) as raised:
        RunStore.open(**arguments)  # type: ignore[arg-type]

    assert raised.value.reason == "unsafe_path"
    assert private_key.stat().st_nlink == 2


def test_exact_reservation_replay_returns_dispatching_record(tmp_path: Path) -> None:
    with RunStore.open(**_arguments(tmp_path / "state")) as store:  # type: ignore[arg-type]
        _bind(store)
        prepared = _reserve(store)
        revision = getattr(prepared, "revision")
        dispatched = store.mark_dispatching(index=0, expected_revision=revision)

        repeated = _reserve(store)

    assert repeated == dispatched
