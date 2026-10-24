"""Red regressions from the final independent MP-05 store review."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest
import yaml
from demo_replay.run_store import ActorDefinition, RunEnvironment, RunStore, RunStoreError
from service_commons.maintenance_lock import MaintenanceLockMode, hold_maintenance_lock

pytestmark = pytest.mark.unit

_RUN_ID = "run-00000000-0000-4000-8000-000000000561"
_STEP: dict[str, object] = {
    "action": "post_task",
    "poster": "poster",
    "title": "Strict durable task",
    "spec": "Retain exact JSON types.",
    "reward": 10,
    "bidding_deadline_seconds": 60,
    "acceptance_window_seconds": 61,
    "execution_deadline_seconds": 120,
    "review_deadline_seconds": 62,
}
_SCENARIO: dict[str, object] = {
    "name": "final review",
    "agents": [{"handle": "poster", "name": "Poster"}],
    "steps": [_STEP],
}
_SCENARIO_BYTES = yaml.safe_dump(_SCENARIO, sort_keys=False).encode()


def _arguments(state_directory: Path) -> dict[str, Any]:
    return {
        "state_directory": state_directory,
        "run_id": _RUN_ID,
        "environment": RunEnvironment(
            environment_id="local-final-review",
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


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _assert_staging_fsynced_after_last_generator_unlink(events: list[str]) -> None:
    last_unlink = max(
        index for index, event in enumerate(events) if event == "unlink_generator_temp"
    )
    last_fsync = max(index for index, event in enumerate(events) if event == "fsync_staging")
    assert last_fsync > last_unlink


def test_fresh_actor_staging_is_fsynced_after_generator_cleanup_before_rename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / "state"
    staging = state / "runs" / _RUN_ID / "keys" / ".poster.provisioning"
    real_fsync = os.fsync
    real_unlink = os.unlink
    real_rename = os.rename
    events: list[str] = []

    def observe_fsync(descriptor: int) -> None:
        descriptor_stat = os.fstat(descriptor)
        if staging.exists() and stat.S_ISDIR(descriptor_stat.st_mode):
            staging_stat = staging.stat()
            if (descriptor_stat.st_dev, descriptor_stat.st_ino) == (
                staging_stat.st_dev,
                staging_stat.st_ino,
            ):
                events.append("fsync_staging")
        real_fsync(descriptor)

    def observe_unlink(
        path: os.PathLike[str] | str,
        *args: object,
        **kwargs: object,
    ) -> None:
        name = Path(path).name
        if name.startswith((".poster.private-", ".poster.public-")) and name.endswith(".tmp"):
            events.append("unlink_generator_temp")
        real_unlink(path, *args, **kwargs)

    def observe_rename(
        source: os.PathLike[str] | str,
        destination: os.PathLike[str] | str,
        *args: object,
        **kwargs: object,
    ) -> None:
        if Path(destination).name == "poster":
            _assert_staging_fsynced_after_last_generator_unlink(events)
        real_rename(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "fsync", observe_fsync)
    monkeypatch.setattr(os, "unlink", observe_unlink)
    monkeypatch.setattr(os, "rename", observe_rename)
    with RunStore.open(**_arguments(state)):
        pass


def test_recovered_complete_actor_staging_is_fsynced_before_rename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / "state"
    arguments = _arguments(state)
    staging = state / "runs" / _RUN_ID / "keys" / ".poster.provisioning"
    real_rename = os.rename

    def interrupt_rename(
        source: os.PathLike[str] | str,
        destination: os.PathLike[str] | str,
        *args: object,
        **kwargs: object,
    ) -> None:
        if Path(destination).name == "poster":
            raise OSError("injected complete-staging cut")
        real_rename(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "rename", interrupt_rename)
    with pytest.raises(RunStoreError):
        RunStore.open(**arguments)
    assert staging.is_dir()

    real_fsync = os.fsync
    fsynced = False

    def observe_fsync(descriptor: int) -> None:
        nonlocal fsynced
        descriptor_stat = os.fstat(descriptor)
        if staging.exists():
            staging_stat = staging.stat()
            if stat.S_ISDIR(descriptor_stat.st_mode) and (
                descriptor_stat.st_dev,
                descriptor_stat.st_ino,
            ) == (staging_stat.st_dev, staging_stat.st_ino):
                fsynced = True
        real_fsync(descriptor)

    def require_fsync_before_rename(
        source: os.PathLike[str] | str,
        destination: os.PathLike[str] | str,
        *args: object,
        **kwargs: object,
    ) -> None:
        if Path(destination).name == "poster":
            assert fsynced
        real_rename(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "fsync", observe_fsync)
    monkeypatch.setattr(os, "rename", require_fsync_before_rename)
    with RunStore.open(**arguments):
        pass


@pytest.mark.parametrize(
    ("record_name", "field", "changed"),
    [
        ("preparation.json", "schema_version", True),
        ("preparation.json", "step_count", 1.0),
        ("run.json", "schema_version", True),
        ("actors/poster.json", "schema_version", True),
        ("steps/000000.json", "schema_version", True),
        ("steps/000000.json", "revision", False),
    ],
)
def test_canonical_numeric_type_corruption_fails_closed_and_is_retained(
    tmp_path: Path,
    record_name: str,
    field: str,
    changed: object,
) -> None:
    arguments = _arguments(tmp_path / "state")
    with RunStore.open(**arguments) as store:
        record = store.run_directory / record_name
    value = json.loads(record.read_bytes())
    value[field] = changed
    corrupt = _canonical_json(value)
    record.write_bytes(corrupt)
    record.chmod(0o600)

    with pytest.raises(RunStoreError) as raised:
        RunStore.open(**arguments)

    assert raised.value.reason == "corrupt_record"
    assert record.read_bytes() == corrupt


def test_competing_opener_before_preparation_reports_lock_unavailable(tmp_path: Path) -> None:
    state = tmp_path / "state"
    run_directory = state / "runs" / _RUN_ID
    run_directory.mkdir(parents=True, mode=0o700)
    state.chmod(0o700)
    (state / "runs").chmod(0o700)
    run_directory.chmod(0o700)
    lock_path = run_directory / "runtime.lock"

    with hold_maintenance_lock(
        lock_path,
        mode=MaintenanceLockMode.EXCLUSIVE,
        blocking=False,
    ):
        with pytest.raises(RunStoreError) as raised:
            RunStore.open(**_arguments(state))

    assert raised.value.reason == "lock_unavailable"
    assert not (run_directory / "preparation.json").exists()
