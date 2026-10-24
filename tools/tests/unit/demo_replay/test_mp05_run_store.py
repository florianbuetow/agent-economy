"""Prospective acceptance tests for the MP-05 durable demo run store."""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import os
import stat
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
import yaml
from service_auth.financial_identity import operation_id_for
from service_auth.signing import generate_keypair, keypair_fingerprint, load_keypair
from service_auth.task_postings import TaskPostingIntent

pytestmark = pytest.mark.unit

_RUN_ID = "run-00000000-0000-4000-8000-000000000501"
_ACTOR_IDS = {"poster": "a-poster", "worker": "a-worker"}
_REGISTERED_AT = "2026-10-22T10:15:30Z"
_ENVIRONMENT_VALUES = {
    "environment_id": "local-acceptance",
    "identity_url": "http://127.0.0.1:8001",
    "bank_url": "http://127.0.0.1:8002",
    "task_board_url": "http://127.0.0.1:8003",
    "reputation_url": "http://127.0.0.1:8004",
    "court_url": "http://127.0.0.1:8005",
    "platform_public_fingerprint_sha256": "a" * 64,
}
_SCENARIO_BYTES = b"""\
name: durable
agents:
  - handle: poster
    name: Poster
  - handle: worker
    name: Worker
steps:
  - action: post_task
    poster: poster
    title: Durable task
    spec: Return the exact saved result.
    reward: 10
    bidding_deadline_seconds: 60
    acceptance_window_seconds: 61
    execution_deadline_seconds: 120
    review_deadline_seconds: 62
  - action: post_task
    poster: worker
    title: Second durable task
    spec: Preserve the second scenario position.
    reward: 20
    bidding_deadline_seconds: 70
    acceptance_window_seconds: 71
    execution_deadline_seconds: 140
    review_deadline_seconds: 72
"""
_SCENARIO: dict[str, object] = yaml.safe_load(_SCENARIO_BYTES)
_SCOPE_MUTATIONS = (
    "environment_id",
    "identity_url",
    "bank_url",
    "task_board_url",
    "reputation_url",
    "court_url",
    "platform_public_fingerprint_sha256",
    "effective_delay_seconds",
    "actor_order",
    "actor_name",
    "step_count",
    "step_order",
    "step_content",
)


@pytest.fixture(scope="session")
def api() -> Any:
    """Load the one prospective interface shared by every behavior test."""
    return importlib.import_module("demo_replay.run_store")


def _environment(api: Any, **changes: str) -> Any:
    values = dict(_ENVIRONMENT_VALUES)
    values.update(changes)
    return api.RunEnvironment(**values)


def _actor_definitions(api: Any, scenario: dict[str, object] = _SCENARIO) -> tuple[Any, ...]:
    actors = scenario["agents"]
    assert isinstance(actors, list)
    return tuple(
        api.ActorDefinition(handle=actor["handle"], name=actor["name"]) for actor in actors
    )


def _open_arguments(api: Any, state_directory: Path, **changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "state_directory": state_directory,
        "run_id": _RUN_ID,
        "environment": _environment(api),
        "scenario_bytes": _SCENARIO_BYTES,
        "scenario": _SCENARIO,
        "actor_definitions": _actor_definitions(api),
        "effective_delay_seconds": 0.0,
        "max_record_bytes": 65_536,
        "diagnostic_limit": 2_048,
    }
    values.update(changes)
    return values


def _step(index: int) -> dict[str, object]:
    steps = _SCENARIO["steps"]
    assert isinstance(steps, list)
    step = steps[index]
    assert isinstance(step, dict)
    return dict(step)


def _registration(store: Any, handle: str, **changes: str) -> dict[str, object]:
    actor = store.actors[handle]
    values: dict[str, object] = {
        "agent_id": _ACTOR_IDS[handle],
        "name": actor.name,
        "public_key": actor.public_key,
        "registered_at": _REGISTERED_AT,
    }
    values.update(changes)
    return values


def _bind(store: Any, handle: str) -> Any:
    return store.record_registration(
        actor_handle=handle,
        response=_registration(store, handle),
    )


def _intent(
    index: int = 0,
    *,
    actor_id: str | None = None,
    **changes: object,
) -> TaskPostingIntent:
    step = _step(index)
    handle = step["poster"]
    assert isinstance(handle, str)
    owner = _ACTOR_IDS[handle] if actor_id is None else actor_id
    task_id = f"t-00000000-0000-4000-8000-00000000050{index + 2}"
    values: dict[str, object] = {
        "protocol_version": 1,
        "operation_id": operation_id_for("demo-posting", (task_id, owner)),
        "task_id": task_id,
        "actor_kind": "agent",
        "actor_id": owner,
        "lock_operation_id": operation_id_for("task-lock", (task_id, owner)),
        "title": step["title"],
        "spec": step["spec"],
        "reward": step["reward"],
        "bidding_deadline_seconds": step["bidding_deadline_seconds"],
        "acceptance_window_seconds": step["acceptance_window_seconds"],
        "execution_deadline_seconds": step["execution_deadline_seconds"],
        "review_deadline_seconds": step["review_deadline_seconds"],
    }
    values.update(changes)
    return TaskPostingIntent.from_json(values)


def _reserve(store: Any, index: int = 0) -> Any:
    step = _step(index)
    handle = step["poster"]
    assert isinstance(handle, str)
    return store.reserve_posting(
        index=index,
        step=step,
        actor_handle=handle,
        intent=_intent(index),
    )


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _expected_preparation() -> dict[str, object]:
    steps = _SCENARIO["steps"]
    actors = _SCENARIO["agents"]
    assert isinstance(steps, list)
    assert isinstance(actors, list)
    return {
        "schema_version": 1,
        "run_id": _RUN_ID,
        "environment": _ENVIRONMENT_VALUES,
        "scenario_raw_sha256": _sha256(_SCENARIO_BYTES),
        "scenario_raw_size": len(_SCENARIO_BYTES),
        "scenario_canonical_sha256": _sha256(_canonical_json(_SCENARIO)),
        "step_sha256s": [_sha256(_canonical_json(step)) for step in steps],
        "step_count": len(steps),
        "effective_delay_seconds": 0.0,
        "actors": [{"handle": actor["handle"], "name": actor["name"]} for actor in actors],
    }


def _coherent_scope_mutation(api: Any, case: str) -> dict[str, object]:
    changed_environment = {
        "environment_id": "changed-environment",
        "identity_url": "http://127.0.0.1:9001",
        "bank_url": "http://127.0.0.1:9002",
        "task_board_url": "http://127.0.0.1:9003",
        "reputation_url": "http://127.0.0.1:9004",
        "court_url": "http://127.0.0.1:9005",
    }
    if case in changed_environment:
        return {"environment": _environment(api, **{case: changed_environment[case]})}
    if case == "platform_public_fingerprint_sha256":
        return {"environment": _environment(api, **{case: "b" * 64})}
    if case == "effective_delay_seconds":
        return {case: 0.25}

    scenario = copy.deepcopy(_SCENARIO)
    actors = scenario["agents"]
    steps = scenario["steps"]
    assert isinstance(actors, list)
    assert isinstance(steps, list)
    if case == "actor_order":
        actors.reverse()
    elif case == "actor_name":
        actors[0]["name"] = "Changed Poster"
    elif case == "step_count":
        steps.pop()
    elif case == "step_order":
        steps.reverse()
    elif case == "step_content":
        steps[0]["title"] = "Changed durable task"
    else:
        raise AssertionError(f"unhandled scope mutation: {case}")
    return {
        "scenario": scenario,
        "scenario_bytes": yaml.safe_dump(scenario, sort_keys=False).encode(),
        "actor_definitions": _actor_definitions(api, scenario),
    }


def test_reopen_restores_the_same_real_actor_keypairs(api: Any, tmp_path: Path) -> None:
    state = tmp_path / "state"
    with api.RunStore.open(**_open_arguments(api, state)) as first:
        snapshots = {
            handle: (actor.private_key_path.read_bytes(), actor.public_fingerprint_sha256)
            for handle, actor in first.actors.items()
        }

    with api.RunStore.open(**_open_arguments(api, state)) as reopened:
        for handle, (private_bytes, fingerprint) in snapshots.items():
            actor = reopened.actors[handle]
            load_keypair(handle, actor.private_key_path.parent)
            assert _mode(actor.private_key_path) == 0o600
            assert actor.private_key_path.read_bytes() == private_bytes
            assert actor.public_fingerprint_sha256 == fingerprint
            assert (
                keypair_fingerprint(
                    handle,
                    actor.private_key_path.parent,
                    allow_legacy_private_mode=False,
                )
                == fingerprint
            )


def test_preparation_precedes_first_key_and_exact_resume_preserves_partial_key(
    api: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / "state"
    real_link = os.link
    attempted_keys: list[str] = []

    def fail_worker_publication(
        source: os.PathLike[str] | str,
        destination: os.PathLike[str] | str,
        *args: object,
        **kwargs: object,
    ) -> None:
        destination_name = Path(destination).name
        if destination_name in {"poster.pub", "poster.key", "worker.pub", "worker.key"}:
            attempted_keys.append(destination_name)
        if destination_name == "poster.pub":
            preparation = state / "runs" / _RUN_ID / "preparation.json"
            assert preparation.is_file()
            assert _mode(preparation) == 0o600
            assert json.loads(preparation.read_bytes()) == _expected_preparation()
        if destination_name == "worker.pub":
            raise OSError("injected second-key failure")
        real_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "link", fail_worker_publication)
    with pytest.raises(api.RunStoreError) as raised:
        api.RunStore.open(**_open_arguments(api, state))
    assert raised.value.reason == "storage_failure"
    run_directory = state / "runs" / _RUN_ID
    preparation = run_directory / "preparation.json"
    first_key = run_directory / "keys" / "poster" / "poster.key"
    assert attempted_keys == ["poster.pub", "poster.key", "worker.pub"]
    assert preparation.is_file()
    assert _mode(preparation) == 0o600
    assert first_key.is_file()
    original_first_key = first_key.read_bytes()

    attempted_keys.clear()
    changed = _environment(api, bank_url="http://127.0.0.1:9002")
    with pytest.raises(api.RunStoreError) as conflict:
        api.RunStore.open(**_open_arguments(api, state, environment=changed))
    assert conflict.value.reason == "scope_conflict"
    assert attempted_keys == []
    assert first_key.read_bytes() == original_first_key

    monkeypatch.setattr(os, "link", real_link)
    with api.RunStore.open(**_open_arguments(api, state)) as resumed:
        assert resumed.actors["poster"].private_key_path.read_bytes() == original_first_key
        assert resumed.actors["worker"].private_key_path.is_file()


@pytest.mark.parametrize("case", _SCOPE_MUTATIONS)
def test_reopen_rejects_each_coherent_scope_mutation(
    api: Any,
    tmp_path: Path,
    case: str,
) -> None:
    state = tmp_path / "state"
    with api.RunStore.open(**_open_arguments(api, state)) as initial:
        private_path = initial.actors["poster"].private_key_path
        original_key = private_path.read_bytes()

    with pytest.raises(api.RunStoreError) as raised:
        api.RunStore.open(**_open_arguments(api, state, **_coherent_scope_mutation(api, case)))
    assert raised.value.reason == "scope_conflict"
    assert private_path.read_bytes() == original_key


def test_raw_yaml_and_parsed_scenario_must_agree_before_storage(
    api: Any,
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    changed = {**_SCENARIO, "name": "different parsed value"}
    with pytest.raises(api.RunStoreError) as raised:
        api.RunStore.open(**_open_arguments(api, state, scenario=changed))
    assert raised.value.reason == "invalid_scope"
    assert not state.exists()


def test_reopen_rejects_changed_raw_bytes_with_the_same_parsed_scenario(
    api: Any,
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    with api.RunStore.open(**_open_arguments(api, state)):
        pass
    changed_bytes = _SCENARIO_BYTES + b"# byte-only relocation comment\n"
    assert yaml.safe_load(changed_bytes) == _SCENARIO
    with pytest.raises(api.RunStoreError) as raised:
        api.RunStore.open(
            **_open_arguments(
                api,
                state,
                scenario_bytes=changed_bytes,
                scenario=_SCENARIO,
            )
        )
    assert raised.value.reason == "scope_conflict"


def test_reopen_rejects_complete_valid_actor_keypair_replacement_without_repair(
    api: Any,
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    with api.RunStore.open(**_open_arguments(api, state)) as store:
        actor = store.actors["poster"]
        keys_directory = actor.private_key_path.parent
        private_path = actor.private_key_path
        public_path = actor.public_key_path
        original_fingerprint = actor.public_fingerprint_sha256

    replacement_directory = tmp_path / "replacement"
    generate_keypair("poster", replacement_directory)
    replacement_private = (replacement_directory / "poster.key").read_bytes()
    replacement_public = (replacement_directory / "poster.pub").read_bytes()
    private_path.write_bytes(replacement_private)
    private_path.chmod(0o600)
    public_path.write_bytes(replacement_public)
    public_path.chmod(0o644)
    load_keypair("poster", keys_directory)
    replacement_fingerprint = keypair_fingerprint(
        "poster",
        keys_directory,
        allow_legacy_private_mode=False,
    )
    assert replacement_fingerprint != original_fingerprint

    with pytest.raises(api.RunStoreError) as raised:
        api.RunStore.open(**_open_arguments(api, state))
    assert raised.value.reason == "scope_conflict"
    assert private_path.read_bytes() == replacement_private
    assert public_path.read_bytes() == replacement_public


def test_declared_actors_must_match_scenario_actors_before_storage(
    api: Any,
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    definitions = (
        api.ActorDefinition(handle="poster", name="Different Poster"),
        api.ActorDefinition(handle="worker", name="Worker"),
    )
    with pytest.raises(api.RunStoreError) as raised:
        api.RunStore.open(**_open_arguments(api, state, actor_definitions=definitions))
    assert raised.value.reason == "invalid_scope"
    assert not state.exists()


@pytest.mark.parametrize(
    "run_id",
    [None, "", "00000000-0000-4000-8000-000000000501", "run-not-a-uuid"],
)
def test_open_requires_an_explicit_canonical_run_id(
    api: Any,
    tmp_path: Path,
    run_id: object,
) -> None:
    state = tmp_path / "state"
    with pytest.raises((TypeError, api.RunStoreError)):
        api.RunStore.open(**_open_arguments(api, state, run_id=run_id))
    assert not (state / "runs").exists()


def test_record_registration_persists_actual_identity_response_and_agent_id(
    api: Any,
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    with api.RunStore.open(**_open_arguments(api, state)) as store:
        response = _registration(store, "poster")
        bound = store.record_registration(actor_handle="poster", response=response)
        assert bound.agent_id == _ACTOR_IDS["poster"]
        assert dict(bound.registration) == response

    with api.RunStore.open(**_open_arguments(api, state)) as reopened:
        restored = reopened.actors["poster"]
        assert restored.agent_id == _ACTOR_IDS["poster"]
        assert dict(restored.registration) == response


def test_exact_registration_rebind_is_idempotent(api: Any, tmp_path: Path) -> None:
    with api.RunStore.open(**_open_arguments(api, tmp_path / "state")) as store:
        response = _registration(store, "poster")
        first = store.record_registration(actor_handle="poster", response=response)
        path = store.run_directory / "actors" / "poster.json"
        original = path.read_bytes()
        second = store.record_registration(actor_handle="poster", response=response)
        assert second == first
        assert path.read_bytes() == original


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("agent_id", "a-other"),
        ("registered_at", "2026-10-22T11:00:00Z"),
    ],
)
def test_changed_registration_rebind_is_rejected_without_changing_bytes(
    api: Any,
    tmp_path: Path,
    field: str,
    changed: str,
) -> None:
    with api.RunStore.open(**_open_arguments(api, tmp_path / "state")) as store:
        response = _registration(store, "poster")
        store.record_registration(actor_handle="poster", response=response)
        path = store.run_directory / "actors" / "poster.json"
        original = path.read_bytes()
        with pytest.raises(api.RunStoreError) as raised:
            store.record_registration(
                actor_handle="poster",
                response={**response, field: changed},
            )
        assert raised.value.reason == "registration_conflict"
        assert path.read_bytes() == original


@pytest.mark.parametrize(
    ("field", "changed"),
    [("name", "Impostor"), ("public_key", "ed25519:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")],
)
def test_registration_must_match_durable_actor_name_and_key(
    api: Any,
    tmp_path: Path,
    field: str,
    changed: str,
) -> None:
    with api.RunStore.open(**_open_arguments(api, tmp_path / "state")) as store:
        slot = store.run_directory / "actors" / "poster.json"
        initial = slot.read_bytes()
        with pytest.raises(api.RunStoreError) as raised:
            store.record_registration(
                actor_handle="poster",
                response={**_registration(store, "poster"), field: changed},
            )
        assert raised.value.reason == "registration_conflict"
        assert slot.read_bytes() == initial
        assert store.actors["poster"].agent_id is None


@pytest.mark.parametrize("shape", ["missing_registered_at", "extra_status"])
def test_registration_requires_exact_identity_response_fields(
    api: Any,
    tmp_path: Path,
    shape: str,
) -> None:
    with api.RunStore.open(**_open_arguments(api, tmp_path / "state")) as store:
        slot = store.run_directory / "actors" / "poster.json"
        initial = slot.read_bytes()
        response = _registration(store, "poster")
        if shape == "missing_registered_at":
            response.pop("registered_at")
        else:
            response["status"] = "registered"
        with pytest.raises(api.RunStoreError) as raised:
            store.record_registration(actor_handle="poster", response=response)
        assert raised.value.reason == "registration_conflict"
        assert slot.read_bytes() == initial
        assert store.actors["poster"].agent_id is None


def test_reserve_requires_a_durable_registration(api: Any, tmp_path: Path) -> None:
    with api.RunStore.open(**_open_arguments(api, tmp_path / "state")) as store:
        with pytest.raises(api.RunStoreError) as raised:
            _reserve(store)
        assert raised.value.reason == "registration_missing"
        assert store.step(0) is None


def test_fresh_reservation_requires_step_poster_to_match_bound_actor(
    api: Any,
    tmp_path: Path,
) -> None:
    with api.RunStore.open(**_open_arguments(api, tmp_path / "state")) as store:
        _bind(store, "poster")
        _bind(store, "worker")
        with pytest.raises(api.RunStoreError) as raised:
            store.reserve_posting(
                index=0,
                step=_step(0),
                actor_handle="worker",
                intent=_intent(0, actor_id=_ACTOR_IDS["worker"]),
            )
        assert raised.value.reason == "intent_conflict"
        assert store.step(0) is None


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("actor_id", "a-other"),
        ("title", "Changed title"),
        ("spec", "Changed specification"),
        ("reward", 11),
        ("bidding_deadline_seconds", 63),
        ("acceptance_window_seconds", 64),
        ("execution_deadline_seconds", 121),
        ("review_deadline_seconds", 65),
    ],
)
def test_fresh_reservation_validates_owner_and_every_signed_scenario_term(
    api: Any,
    tmp_path: Path,
    field: str,
    changed: object,
) -> None:
    with api.RunStore.open(**_open_arguments(api, tmp_path / "state")) as store:
        _bind(store, "poster")
        if field == "actor_id":
            intent = _intent(actor_id=str(changed))
        else:
            intent = _intent(**{field: changed})
        with pytest.raises(api.RunStoreError) as raised:
            store.reserve_posting(
                index=0,
                step=_step(0),
                actor_handle="poster",
                intent=intent,
            )
        assert raised.value.reason == "intent_conflict"
        assert store.step(0) is None


def test_reserve_rejects_a_different_selected_step_without_a_record(
    api: Any,
    tmp_path: Path,
) -> None:
    with api.RunStore.open(**_open_arguments(api, tmp_path / "state")) as store:
        _bind(store, "poster")
        with pytest.raises(api.RunStoreError) as raised:
            store.reserve_posting(
                index=0,
                step={**_step(0), "reward": 11},
                actor_handle="poster",
                intent=_intent(0),
            )
        assert raised.value.reason == "step_conflict"
        assert store.step(0) is None


def test_exact_reservation_replay_returns_same_record_and_bytes(
    api: Any,
    tmp_path: Path,
) -> None:
    with api.RunStore.open(**_open_arguments(api, tmp_path / "state")) as store:
        _bind(store, "poster")
        first = _reserve(store)
        path = store.run_directory / "steps" / "000000.json"
        original = path.read_bytes()
        second = _reserve(store)
        assert first == second
        assert second.revision == 1
        assert second.agent_id == _ACTOR_IDS["poster"]
        assert path.read_bytes() == original


def test_changed_reservation_replay_preserves_original_bytes(api: Any, tmp_path: Path) -> None:
    with api.RunStore.open(**_open_arguments(api, tmp_path / "state")) as store:
        _bind(store, "poster")
        original = _reserve(store)
        path = store.run_directory / "steps" / "000000.json"
        original_bytes = path.read_bytes()
        with pytest.raises(api.RunStoreError) as raised:
            store.reserve_posting(
                index=0,
                step=_step(0),
                actor_handle="poster",
                intent=_intent(title="Changed title"),
            )
        assert raised.value.reason == "intent_conflict"
        assert store.step(0) == original
        assert path.read_bytes() == original_bytes


def test_stale_dispatch_revision_preserves_prepared_record_bytes(
    api: Any,
    tmp_path: Path,
) -> None:
    with api.RunStore.open(**_open_arguments(api, tmp_path / "state")) as store:
        _bind(store, "poster")
        prepared = _reserve(store)
        path = store.run_directory / "steps" / "000000.json"
        original = path.read_bytes()
        with pytest.raises(api.RunStoreError) as raised:
            store.mark_dispatching(index=0, expected_revision=0)
        assert raised.value.reason == "stale_revision"
        assert store.step(0) == prepared
        assert path.read_bytes() == original


def test_dispatch_increments_revision_and_exact_repeat_is_idempotent(
    api: Any,
    tmp_path: Path,
) -> None:
    with api.RunStore.open(**_open_arguments(api, tmp_path / "state")) as store:
        _bind(store, "poster")
        prepared = _reserve(store)
        dispatched = store.mark_dispatching(index=0, expected_revision=prepared.revision)
        path = store.run_directory / "steps" / "000000.json"
        dispatched_bytes = path.read_bytes()
        repeated = store.mark_dispatching(index=0, expected_revision=dispatched.revision)
        assert dispatched.revision == prepared.revision + 1 == 2
        assert repeated == dispatched
        assert path.read_bytes() == dispatched_bytes


def test_dispatch_fsync_failure_does_not_authorize_transport(
    api: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = Mock()
    with api.RunStore.open(**_open_arguments(api, tmp_path / "state")) as store:
        _bind(store, "poster")
        prepared = _reserve(store)

        def fail_fsync(_descriptor: int) -> None:
            raise OSError("injected dispatch durability failure")

        monkeypatch.setattr(os, "fsync", fail_fsync)
        with pytest.raises(api.RunStoreError) as raised:
            dispatched = store.mark_dispatching(
                index=0,
                expected_revision=prepared.revision,
            )
            transport(dispatched.intent)
    assert raised.value.reason == "storage_failure"
    transport.assert_not_called()


def test_corrupt_registration_is_retained_for_diagnosis(api: Any, tmp_path: Path) -> None:
    state = tmp_path / "state"
    with api.RunStore.open(**_open_arguments(api, state)) as store:
        _bind(store, "poster")
        record = store.run_directory / "actors" / "poster.json"
    corrupt = b'{"agent_id":"a-poster","truncated":'
    record.write_bytes(corrupt)
    record.chmod(0o600)
    with pytest.raises(api.RunStoreError) as raised:
        api.RunStore.open(**_open_arguments(api, state))
    assert raised.value.reason == "corrupt_record"
    assert record.read_bytes() == corrupt


def test_step_with_wrong_mode_is_rejected_without_repair(api: Any, tmp_path: Path) -> None:
    state = tmp_path / "state"
    with api.RunStore.open(**_open_arguments(api, state)) as store:
        _bind(store, "poster")
        _reserve(store)
        record = store.run_directory / "steps" / "000000.json"
    record.chmod(0o644)
    with pytest.raises(api.RunStoreError) as raised:
        api.RunStore.open(**_open_arguments(api, state))
    assert raised.value.reason == "unsafe_mode"
    assert _mode(record) == 0o644


def test_hardlinked_step_is_rejected_without_repair(api: Any, tmp_path: Path) -> None:
    state = tmp_path / "state"
    with api.RunStore.open(**_open_arguments(api, state)) as store:
        _bind(store, "poster")
        _reserve(store)
        steps = store.run_directory / "steps"
        record = steps / "000000.json"
    os.link(record, steps / "duplicate.json")
    assert record.stat().st_nlink == 2
    with pytest.raises(api.RunStoreError) as raised:
        api.RunStore.open(**_open_arguments(api, state))
    assert raised.value.reason == "unsafe_path"
    assert record.stat().st_nlink == 2


def test_oversized_preparation_is_retained_for_diagnosis(api: Any, tmp_path: Path) -> None:
    state = tmp_path / "state"
    limit = 1_024
    with api.RunStore.open(**_open_arguments(api, state, max_record_bytes=limit)) as store:
        run_directory = store.run_directory
    (run_directory / "run.json").unlink()
    preparation = run_directory / "preparation.json"
    oversized = _canonical_json({"padding": "x" * limit})
    assert len(oversized) > limit
    assert json.loads(oversized) == {"padding": "x" * limit}
    preparation.write_bytes(oversized)
    preparation.chmod(0o600)
    with pytest.raises(api.RunStoreError) as raised:
        api.RunStore.open(**_open_arguments(api, state, max_record_bytes=limit))
    assert raised.value.reason == "record_too_large"
    assert preparation.read_bytes() == oversized


def test_competing_open_fails_while_the_run_lease_is_held(api: Any, tmp_path: Path) -> None:
    arguments = _open_arguments(api, tmp_path / "state")
    with api.RunStore.open(**arguments):
        with pytest.raises(api.RunStoreError) as raised:
            api.RunStore.open(**arguments)
        assert raised.value.reason == "lock_unavailable"


def test_unbound_key_residue_is_not_adopted_as_a_new_run(api: Any, tmp_path: Path) -> None:
    state = tmp_path / "state"
    run_directory = state / "runs" / _RUN_ID
    keys_directory = run_directory / "keys"
    keys_directory.mkdir(parents=True, mode=0o700)
    generate_keypair("poster", keys_directory)
    original_key = (keys_directory / "poster.key").read_bytes()
    with pytest.raises(api.RunStoreError) as raised:
        api.RunStore.open(**_open_arguments(api, state))
    assert raised.value.reason == "unbound_residue"
    assert (keys_directory / "poster.key").read_bytes() == original_key
    assert not (run_directory / "run.json").exists()
