"""Red regressions for independently reviewed MP-05 run-store gaps."""

from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path
from typing import Any

import pytest
import yaml
from demo_replay import run_store as run_store_module
from demo_replay import run_store_files
from demo_replay.run_store import ActorDefinition, RunEnvironment, RunStore, RunStoreError
from service_auth.financial_identity import operation_id_for
from service_auth.signing import keypair_fingerprint
from service_auth.task_postings import TaskPostingIntent

pytestmark = pytest.mark.unit

_RUN_ID = "run-00000000-0000-4000-8000-000000000521"
_AGENT_ID = "a-poster"
_STEP: dict[str, object] = {
    "action": "post_task",
    "poster": "poster",
    "title": "Durable task",
    "spec": "Preserve this exact posting intent.",
    "reward": 10,
    "bidding_deadline_seconds": 60,
    "acceptance_window_seconds": 61,
    "execution_deadline_seconds": 120,
    "review_deadline_seconds": 62,
}
_SCENARIO: dict[str, object] = {
    "name": "request changes",
    "agents": [{"handle": "poster", "name": "Poster"}],
    "steps": [_STEP],
}
_SCENARIO_BYTES = yaml.safe_dump(_SCENARIO, sort_keys=False).encode()


def _arguments(state_directory: Path) -> dict[str, Any]:
    return {
        "state_directory": state_directory,
        "run_id": _RUN_ID,
        "environment": RunEnvironment(
            environment_id="local-request-changes",
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


def _intent(**changes: object) -> TaskPostingIntent:
    task_id = "t-00000000-0000-4000-8000-000000000522"
    actor_id = changes.get("actor_id", _AGENT_ID)
    assert isinstance(actor_id, str)
    values: dict[str, object] = {
        "protocol_version": 1,
        "operation_id": operation_id_for("demo-posting", (task_id, _AGENT_ID)),
        "task_id": task_id,
        "actor_kind": "agent",
        "actor_id": actor_id,
        "lock_operation_id": operation_id_for("task-lock", (task_id, actor_id)),
        "title": _STEP["title"],
        "spec": _STEP["spec"],
        "reward": _STEP["reward"],
        "bidding_deadline_seconds": _STEP["bidding_deadline_seconds"],
        "acceptance_window_seconds": _STEP["acceptance_window_seconds"],
        "execution_deadline_seconds": _STEP["execution_deadline_seconds"],
        "review_deadline_seconds": _STEP["review_deadline_seconds"],
    }
    values.update(changes)
    if "actor_id" in changes and "lock_operation_id" not in changes:
        values["lock_operation_id"] = operation_id_for("task-lock", (task_id, actor_id))
    return TaskPostingIntent.from_json(values)


def _reserve(store: RunStore) -> object:
    return store.reserve_posting(
        index=0,
        step=_STEP,
        actor_handle="poster",
        intent=_intent(),
    )


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


@pytest.mark.parametrize("state", ["prepared", "dispatching"])
def test_deleted_reserved_step_fails_closed_and_stays_absent(
    tmp_path: Path,
    state: str,
) -> None:
    arguments = _arguments(tmp_path / "state")
    with RunStore.open(**arguments) as store:
        _bind(store)
        prepared = _reserve(store)
        if state == "dispatching":
            store.mark_dispatching(index=0, expected_revision=getattr(prepared, "revision"))
        record = store.run_directory / "steps" / "000000.json"
    record.unlink()

    with pytest.raises(RunStoreError) as raised:
        RunStore.open(**arguments)

    assert raised.value.reason == "corrupt_record"
    assert not record.exists()


def test_legitimate_unreserved_step_has_a_stable_validated_empty_slot(tmp_path: Path) -> None:
    arguments = _arguments(tmp_path / "state")
    with RunStore.open(**arguments) as store:
        slot = store.run_directory / "steps" / "000000.json"
        initial = slot.read_bytes()
        assert store.step(0) is None

    with RunStore.open(**arguments) as reopened:
        assert reopened.step(0) is None
        assert slot.read_bytes() == initial


def test_published_manifest_missing_keypair_never_regenerates_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments = _arguments(tmp_path / "state")
    with RunStore.open(**arguments) as store:
        actor = store.actors["poster"]
        private_key = actor.private_key_path
        public_key = actor.public_key_path
    private_key.unlink()
    public_key.unlink()
    generated: list[str] = []
    real_generate = run_store_module.generate_keypair

    def observe_generate(handle: str, directory: Path) -> object:
        generated.append(handle)
        return real_generate(handle, directory)

    monkeypatch.setattr(run_store_module, "generate_keypair", observe_generate)
    with pytest.raises(RunStoreError) as raised:
        RunStore.open(**arguments)

    assert raised.value.reason == "corrupt_record"
    assert generated == []
    assert not private_key.exists()
    assert not public_key.exists()


@pytest.mark.parametrize("directory_name", ["actors", "steps"])
def test_published_manifest_missing_required_directory_is_not_recreated(
    tmp_path: Path,
    directory_name: str,
) -> None:
    arguments = _arguments(tmp_path / "state")
    with RunStore.open(**arguments) as store:
        _bind(store)
        _reserve(store)
        directory = store.run_directory / directory_name
    shutil.rmtree(directory)

    with pytest.raises(RunStoreError) as raised:
        RunStore.open(**arguments)

    assert raised.value.reason == "corrupt_record"
    assert not directory.exists()


def test_active_store_rejects_deleted_registration_before_reservation(tmp_path: Path) -> None:
    with RunStore.open(**_arguments(tmp_path / "state")) as store:
        _bind(store)
        registration = store.run_directory / "actors" / "poster.json"
        step = store.run_directory / "steps" / "000000.json"
        initial_step = step.read_bytes()
        registration.unlink()

        with pytest.raises(RunStoreError) as raised:
            _reserve(store)

        assert raised.value.reason == "corrupt_record"
        assert not registration.exists()
        assert step.read_bytes() == initial_step


def test_active_store_rejects_valid_registration_cache_tamper(tmp_path: Path) -> None:
    with RunStore.open(**_arguments(tmp_path / "state")) as store:
        _bind(store)
        registration = store.run_directory / "actors" / "poster.json"
        step = store.run_directory / "steps" / "000000.json"
        initial_step = step.read_bytes()
        changed = json.loads(registration.read_bytes())
        if "registration" in changed:
            changed["registration"]["agent_id"] = "a-other"
        else:
            changed["agent_id"] = "a-other"
        tampered = _canonical_json(changed)
        registration.write_bytes(tampered)
        registration.chmod(0o600)

        with pytest.raises(RunStoreError) as raised:
            _reserve(store)

        assert raised.value.reason == "corrupt_record"
        assert registration.read_bytes() == tampered
        assert step.read_bytes() == initial_step


def test_active_store_rejects_deleted_prepared_step_before_dispatch(tmp_path: Path) -> None:
    with RunStore.open(**_arguments(tmp_path / "state")) as store:
        _bind(store)
        prepared = _reserve(store)
        record = store.run_directory / "steps" / "000000.json"
        record.unlink()

        with pytest.raises(RunStoreError) as raised:
            store.mark_dispatching(index=0, expected_revision=getattr(prepared, "revision"))

        assert raised.value.reason == "corrupt_record"
        assert not record.exists()


@pytest.mark.parametrize(
    "changed_intent",
    [
        _intent(actor_id="a-attacker").as_json(),
        _intent(title="Changed durable task").as_json(),
    ],
)
def test_reopen_revalidates_stored_intent_against_actor_and_scenario(
    tmp_path: Path,
    changed_intent: dict[str, object],
) -> None:
    arguments = _arguments(tmp_path / "state")
    with RunStore.open(**arguments) as store:
        _bind(store)
        _reserve(store)
        record = store.run_directory / "steps" / "000000.json"
    value = json.loads(record.read_bytes())
    value["intent"] = changed_intent
    tampered = _canonical_json(value)
    record.write_bytes(tampered)
    record.chmod(0o600)

    with pytest.raises(RunStoreError) as raised:
        RunStore.open(**arguments)

    assert raised.value.reason == "corrupt_record"
    assert record.read_bytes() == tampered


def test_reopen_accepts_exact_canonical_owner_and_terms(tmp_path: Path) -> None:
    arguments = _arguments(tmp_path / "state")
    with RunStore.open(**arguments) as store:
        _bind(store)
        reserved = _reserve(store)

    with RunStore.open(**arguments) as reopened:
        restored = reopened.step(0)

    assert restored == reserved


@pytest.mark.parametrize(
    ("dispatch_first", "expected_revision"),
    [(False, True), (True, 2.0)],
)
def test_dispatch_revision_requires_a_strict_integer_without_changing_bytes(
    tmp_path: Path,
    dispatch_first: bool,
    expected_revision: object,
) -> None:
    with RunStore.open(**_arguments(tmp_path / "state")) as store:
        _bind(store)
        prepared = _reserve(store)
        if dispatch_first:
            store.mark_dispatching(index=0, expected_revision=getattr(prepared, "revision"))
        record = store.run_directory / "steps" / "000000.json"
        original = record.read_bytes()

        with pytest.raises(RunStoreError) as raised:
            store.mark_dispatching(index=0, expected_revision=expected_revision)  # type: ignore[arg-type]

        assert raised.value.reason == "stale_revision"
        assert record.read_bytes() == original


def test_dispatch_revision_accepts_exact_integers_only(tmp_path: Path) -> None:
    with RunStore.open(**_arguments(tmp_path / "state")) as store:
        _bind(store)
        prepared = _reserve(store)
        dispatched = store.mark_dispatching(index=0, expected_revision=1)
        repeated = store.mark_dispatching(index=0, expected_revision=2)

    assert getattr(prepared, "revision") == 1
    assert getattr(dispatched, "revision") == 2
    assert repeated == dispatched


def test_public_only_unpublished_actor_staging_is_retried_without_adoption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments = _arguments(tmp_path / "state")
    real_link = os.link

    def interrupt_private_publication(
        source: os.PathLike[str] | str,
        destination: os.PathLike[str] | str,
        *args: object,
        **kwargs: object,
    ) -> None:
        if Path(destination).name == "poster.key":
            raise OSError("injected public-only keypair cut")
        real_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "link", interrupt_private_publication)
    with pytest.raises(RunStoreError) as raised:
        RunStore.open(**arguments)
    assert raised.value.reason == "storage_failure"

    staging = tmp_path / "state" / "runs" / _RUN_ID / "keys" / ".poster.provisioning"
    assert (staging / "poster.pub").is_file()
    assert not (staging / "poster.key").exists()
    monkeypatch.setattr(os, "link", real_link)

    with RunStore.open(**arguments) as resumed:
        actor = resumed.actors["poster"]
        assert actor.private_key_path == staging.parent / "poster" / "poster.key"
        assert actor.private_key_path.is_file()
    assert not staging.exists()


def test_complete_unpublished_actor_staging_is_renamed_without_regeneration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments = _arguments(tmp_path / "state")
    real_rename = os.rename

    def interrupt_actor_rename(
        source: os.PathLike[str] | str,
        destination: os.PathLike[str] | str,
        *args: object,
        **kwargs: object,
    ) -> None:
        if Path(destination).name == "poster":
            raise OSError("injected complete-staging rename cut")
        real_rename(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "rename", interrupt_actor_rename)
    with pytest.raises(RunStoreError) as raised:
        RunStore.open(**arguments)
    assert raised.value.reason == "storage_failure"

    staging = tmp_path / "state" / "runs" / _RUN_ID / "keys" / ".poster.provisioning"
    fingerprint = keypair_fingerprint(
        "poster",
        staging,
        allow_legacy_private_mode=False,
    )
    monkeypatch.setattr(os, "rename", real_rename)

    def reject_regeneration(_handle: str, _directory: Path) -> object:
        raise AssertionError("complete unpublished keypair must not be regenerated")

    monkeypatch.setattr(run_store_module, "generate_keypair", reject_regeneration)
    with RunStore.open(**arguments) as resumed:
        assert resumed.actors["poster"].public_fingerprint_sha256 == fingerprint
    assert not staging.exists()


def test_manifest_is_published_after_all_keys_and_empty_slots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / "state"
    run_directory = state / "runs" / _RUN_ID
    real_link = os.link
    observed_manifest = False

    def observe_manifest_publication(
        source: os.PathLike[str] | str,
        destination: os.PathLike[str] | str,
        *args: object,
        **kwargs: object,
    ) -> None:
        nonlocal observed_manifest
        if Path(destination).name == "run.json":
            observed_manifest = True
            assert (run_directory / "keys" / "poster" / "poster.key").is_file()
            actor_slot = run_directory / "actors" / "poster.json"
            step_slot = run_directory / "steps" / "000000.json"
            assert json.loads(actor_slot.read_bytes())["state"] == "empty"
            assert json.loads(step_slot.read_bytes())["state"] == "empty"
        real_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "link", observe_manifest_publication)
    with RunStore.open(**_arguments(state)):
        pass

    assert observed_manifest


def test_visible_exact_manifest_is_refsynced_after_directory_fsync_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / "state"
    run_directory = state / "runs" / _RUN_ID
    real_fsync = os.fsync
    failed = False

    def fail_first_manifest_directory_fsync(descriptor: int) -> None:
        nonlocal failed
        descriptor_stat = os.fstat(descriptor)
        if (
            not failed
            and stat.S_ISDIR(descriptor_stat.st_mode)
            and (run_directory / "run.json").exists()
            and (descriptor_stat.st_dev, descriptor_stat.st_ino)
            == (run_directory.stat().st_dev, run_directory.stat().st_ino)
        ):
            failed = True
            raise OSError("injected manifest directory fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_first_manifest_directory_fsync)
    with pytest.raises(RunStoreError) as raised:
        RunStore.open(**_arguments(state))
    assert raised.value.reason == "storage_failure"
    manifest = run_directory / "run.json"
    original = manifest.read_bytes()
    assert failed

    refsyncs = 0

    def observe_refsync(descriptor: int) -> None:
        nonlocal refsyncs
        descriptor_stat = os.fstat(descriptor)
        if stat.S_ISDIR(descriptor_stat.st_mode) and (
            descriptor_stat.st_dev,
            descriptor_stat.st_ino,
        ) == (run_directory.stat().st_dev, run_directory.stat().st_ino):
            refsyncs += 1
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", observe_refsync)
    with RunStore.open(**_arguments(state)):
        pass

    assert refsyncs >= 1
    assert manifest.read_bytes() == original


def test_exclusive_publication_fsyncs_directory_after_staging_unlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "private"
    directory = run_store_files.open_private_directory(
        root,
        create=True,
        private_root=root,
    )
    real_fsync = os.fsync
    real_unlink = os.unlink
    events: list[str] = []

    def observe_fsync(descriptor: int) -> None:
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            events.append("fsync_directory")
        real_fsync(descriptor)

    def observe_unlink(
        path: os.PathLike[str] | str,
        *args: object,
        **kwargs: object,
    ) -> None:
        if str(path).startswith(".record.json.") and str(path).endswith(".tmp"):
            events.append("unlink_temporary")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "fsync", observe_fsync)
    monkeypatch.setattr(os, "unlink", observe_unlink)
    try:
        assert run_store_files.publish_exclusive(directory, "record.json", b"{}")
    finally:
        directory.close()

    assert events[-2:] == ["unlink_temporary", "fsync_directory"]
