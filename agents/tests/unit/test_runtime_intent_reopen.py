"""T-181/T-182 adversarial reopen proofs for the runtime intent journal."""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path
from typing import Any, cast

import pytest

from base_agent import runtime_intents as runtime

pytestmark = pytest.mark.unit

type _OsPath = str | bytes | os.PathLike[str] | os.PathLike[bytes]

_TASK_ID = "t-10000000-0000-4000-8000-000000000001"
_OTHER_TASK_ID = "t-10000000-0000-4000-8000-000000000002"
_OPERATION_ID = "op-10000000-0000-4000-8000-000000000001"
_FINGERPRINT = "1" * 64
_URLS = {
    "identity_url": "https://identity.example",
    "bank_url": "https://bank.example",
    "task_board_url": "https://task-board.example",
    "reputation_url": "https://reputation.example",
    "court_url": "https://court.example",
}


def _owner(**overrides: object) -> runtime.RuntimeOwner:
    values: dict[str, object] = {
        "schema_version": 1,
        "environment_id": "local-integration",
        "role": "math-worker",
        "profile": "mathbot_openai",
        "handle": "worker-alice",
        "registered_agent_id": "a-worker-alice",
        "key_fingerprint": _FINGERPRINT,
        "service_urls": dict(_URLS),
    }
    values.update(overrides)
    return runtime.RuntimeOwner(
        schema_version=cast("int", values["schema_version"]),
        environment_id=cast("str", values["environment_id"]),
        role=cast("str", values["role"]),
        profile=cast("str", values["profile"]),
        handle=cast("str", values["handle"]),
        registered_agent_id=cast("str", values["registered_agent_id"]),
        key_fingerprint=cast("str", values["key_fingerprint"]),
        service_urls=cast("dict[str, str]", values["service_urls"]),
    )


def _intent(
    *,
    task_id: str = _TASK_ID,
    operation_id: str = _OPERATION_ID,
    amount: int = 9,
) -> runtime.NewTaskIntent:
    return runtime.NewTaskIntent(
        task_id=task_id,
        slot="bid",
        action="submit_bid",
        source_status="open",
        target_status="open",
        operation_id=operation_id,
        expected_lifecycle_version=7,
        public_arguments={
            "task_id": task_id,
            "amount": amount,
            "operation_id": operation_id,
            "expected_lifecycle_version": 7,
        },
        acknowledgment_context={
            "response_kind": "bid",
            "operation_id": operation_id,
            "task_id": task_id,
            "command": "submit_bid",
            "source_status": "open",
            "source_lifecycle_version": 7,
            "target_status": "open",
            "target_lifecycle_version": 7,
            "result_facts": {
                "bid_id": f"bid-{operation_id[3:]}",
                "bidder_id": "a-worker-alice",
                "amount": amount,
            },
            "actor_id": "a-worker-alice",
            "frozen_task_facts": {"bidder_id": "a-worker-alice"},
        },
    )


def _store(root: Path, owner: runtime.RuntimeOwner) -> runtime.FileTaskIntentStore:
    return runtime.FileTaskIntentStore(
        root_directory=root,
        owner=owner,
        diagnostic_limit=8,
        max_record_bytes=65_536,
    )


def _persisted_record(
    root: Path,
    owner: runtime.RuntimeOwner,
    intent: runtime.NewTaskIntent,
) -> tuple[Path, bytes]:
    with _store(root, owner) as store:
        store.create(intent)
        path = store.intent_path(intent.task_id, intent.slot)
        return path, path.read_bytes()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _descriptor_closed(descriptor: int) -> bool:
    try:
        os.fstat(descriptor)
    except OSError as exc:
        return exc.errno == errno.EBADF
    return False


def test_filesystem_root_is_rejected_by_constructor_without_opening_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened = False

    def unexpected_open(path: _OsPath, flags: int, *_args: Any, **_kwargs: Any) -> int:
        nonlocal opened
        opened = True
        raise AssertionError(f"constructor attempted to open {path!s} with flags {flags}")

    monkeypatch.setattr(os, "open", unexpected_open)
    with pytest.raises(runtime.RuntimeIntentError) as raised:
        _store(Path("/"), _owner())

    assert raised.value.reason == "unsafe_path"
    assert not opened


@pytest.mark.parametrize(
    "changed_owner",
    [
        {"environment_id": "production"},
        {"profile": "mathbot_anthropic"},
        {"service_urls": {**_URLS, "court_url": "https://other-court.example"}},
        {"key_fingerprint": "2" * 64},
    ],
)
def test_reopen_rejects_valid_record_copied_from_another_owner_scope(
    tmp_path: Path,
    changed_owner: dict[str, object],
) -> None:
    source_owner = _owner()
    _source_path, source_bytes = _persisted_record(tmp_path / "source", source_owner, _intent())
    target_owner = _owner(**changed_owner)
    with _store(tmp_path / "target", target_owner) as target:
        target_path = target.intent_path(_TASK_ID, "bid")
    target_path.write_bytes(source_bytes)
    target_path.chmod(0o600)

    with (
        pytest.raises(runtime.RuntimeIntentError) as raised,
        _store(tmp_path / "target", target_owner),
    ):
        pass

    assert raised.value.reason == "corrupt_record"
    assert target_path.read_bytes() == source_bytes


def test_reopen_rejects_duplicate_operation_id_across_preexisting_records(
    tmp_path: Path,
) -> None:
    owner = _owner()
    main_root = tmp_path / "main"
    _first_path, _first_bytes = _persisted_record(main_root, owner, _intent())
    _second_path, second_bytes = _persisted_record(
        tmp_path / "second",
        owner,
        _intent(task_id=_OTHER_TASK_ID, operation_id=_OPERATION_ID),
    )
    with _store(main_root, owner) as store:
        duplicate_path = store.intent_path(_OTHER_TASK_ID, "bid")
    duplicate_path.write_bytes(second_bytes)
    duplicate_path.chmod(0o600)

    with pytest.raises(runtime.RuntimeIntentError) as raised, _store(main_root, owner):
        pass

    assert raised.value.reason == "operation_conflict"
    assert duplicate_path.read_bytes() == second_bytes


@pytest.mark.parametrize("mutation", ["malformed-json", "semantic-hash"])
def test_reopen_strictly_validates_every_existing_record_before_use(
    tmp_path: Path,
    mutation: str,
) -> None:
    owner = _owner()
    root = tmp_path / "journal"
    path, original = _persisted_record(root, owner, _intent())
    if mutation == "malformed-json":
        damaged = b"{"
    else:
        payload = json.loads(original)
        payload["semantic_sha256"] = "0" * 64
        damaged = _canonical_bytes(payload)
    path.write_bytes(damaged)
    path.chmod(0o600)

    with pytest.raises(runtime.RuntimeIntentError) as raised, _store(root, owner):
        pass

    assert raised.value.reason == "corrupt_record"
    assert path.read_bytes() == damaged


def test_reopen_removes_known_private_staging_file_and_fsyncs_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _owner()
    root = tmp_path / "journal"
    path, original = _persisted_record(root, owner, _intent())
    staging = path.parent / f".{path.name}.{'a' * 24}.tmp"
    staging.write_bytes(b"crash-left partial record")
    staging.chmod(0o600)
    directory_identity = (path.parent.stat().st_dev, path.parent.stat().st_ino)
    real_fsync = os.fsync
    synced_directory = False

    def track_fsync(descriptor: int) -> None:
        nonlocal synced_directory
        observed = os.fstat(descriptor)
        if (observed.st_dev, observed.st_ino) == directory_identity:
            synced_directory = True
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", track_fsync)
    with _store(root, owner) as reopened:
        assert reopened.load(_TASK_ID, "bid") is not None

    assert not staging.exists()
    assert path.read_bytes() == original
    assert synced_directory


def test_reopen_rejects_unknown_entry_without_removing_it(tmp_path: Path) -> None:
    owner = _owner()
    root = tmp_path / "journal"
    path, _original = _persisted_record(root, owner, _intent())
    unknown = path.parent / "unexpected.tmp"
    unknown.write_bytes(b"unknown")
    unknown.chmod(0o600)

    with pytest.raises(runtime.RuntimeIntentError) as raised, _store(root, owner):
        pass

    assert raised.value.reason == "corrupt_record"
    assert unknown.read_bytes() == b"unknown"


@pytest.mark.parametrize("unsafe_kind", ["public-mode", "symlink"])
def test_reopen_does_not_remove_unsafe_known_staging_entry(
    tmp_path: Path,
    unsafe_kind: str,
) -> None:
    owner = _owner()
    root = tmp_path / "journal"
    path, _original = _persisted_record(root, owner, _intent())
    staging = path.parent / f".{path.name}.{'b' * 24}.tmp"
    if unsafe_kind == "public-mode":
        staging.write_bytes(b"unsafe")
        staging.chmod(0o644)
        expected_reason = "unsafe_mode"
    else:
        outside = tmp_path / "outside.tmp"
        outside.write_bytes(b"outside")
        staging.symlink_to(outside)
        expected_reason = "unsafe_path"

    with pytest.raises(runtime.RuntimeIntentError) as raised, _store(root, owner):
        pass

    assert raised.value.reason == expected_reason
    assert staging.exists() or staging.is_symlink()


def test_reopen_removes_private_owner_staging_file_and_fsyncs_identity_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _owner()
    with _store(tmp_path, owner) as store:
        owner_path = store.owner_path
        original_owner = owner_path.read_bytes()
        identity_path = store.identity_directory
    staging = identity_path / f".owner.json.{'c' * 24}.tmp"
    staging.write_bytes(b"crash-left owner staging")
    staging.chmod(0o600)
    directory_identity = (identity_path.stat().st_dev, identity_path.stat().st_ino)
    real_fsync = os.fsync
    synced_directory = False

    def track_fsync(descriptor: int) -> None:
        nonlocal synced_directory
        observed = os.fstat(descriptor)
        if (observed.st_dev, observed.st_ino) == directory_identity:
            synced_directory = True
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", track_fsync)
    with _store(tmp_path, owner):
        pass

    assert not staging.exists()
    assert owner_path.read_bytes() == original_owner
    assert synced_directory


@pytest.mark.parametrize("unsafe_kind", ["public-mode", "symlink"])
def test_reopen_rejects_and_preserves_unsafe_owner_staging_entry(
    tmp_path: Path,
    unsafe_kind: str,
) -> None:
    owner = _owner()
    with _store(tmp_path, owner) as store:
        identity_path = store.identity_directory
    staging = identity_path / f".owner.json.{'d' * 24}.tmp"
    if unsafe_kind == "public-mode":
        staging.write_bytes(b"unsafe owner staging")
        staging.chmod(0o644)
        expected_reason = "unsafe_mode"
    else:
        outside = tmp_path / "outside-owner.tmp"
        outside.write_bytes(b"outside")
        staging.symlink_to(outside)
        expected_reason = "unsafe_path"

    with pytest.raises(runtime.RuntimeIntentError) as raised, _store(tmp_path, owner):
        pass

    assert raised.value.reason == expected_reason
    assert staging.exists() or staging.is_symlink()


def test_reopen_rejects_and_preserves_unknown_identity_entry(tmp_path: Path) -> None:
    owner = _owner()
    with _store(tmp_path, owner) as store:
        unknown = store.identity_directory / "unexpected.identity"
    unknown.write_bytes(b"unknown identity entry")
    unknown.chmod(0o600)

    with pytest.raises(runtime.RuntimeIntentError) as raised, _store(tmp_path, owner):
        pass

    assert raised.value.reason == "corrupt_record"
    assert unknown.read_bytes() == b"unknown identity entry"


@pytest.mark.parametrize("fault", ["root-fstat", "child-fstat", "child-stat"])
def test_directory_open_failure_closes_new_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    owner = _owner()
    real_open = os.open
    real_fstat = os.fstat
    real_stat = os.stat
    captured: list[int] = []
    target = tmp_path.name if fault == "root-fstat" else "identities"

    def track_open(path: _OsPath, flags: int, *args: Any, **kwargs: Any) -> int:
        descriptor = real_open(path, flags, *args, **kwargs)
        if str(path) == target and not captured:
            captured.append(descriptor)
        return descriptor

    def fail_fstat(descriptor: int) -> os.stat_result:
        if fault.endswith("fstat") and captured and descriptor == captured[0]:
            raise OSError("injected post-open fstat failure")
        return real_fstat(descriptor)

    def fail_stat(path: int | _OsPath, *args: Any, **kwargs: Any) -> os.stat_result:
        if fault == "child-stat" and captured and str(path) == "identities":
            raise OSError("injected post-open stat failure")
        return real_stat(path, *args, **kwargs)

    with monkeypatch.context() as faults:
        faults.setattr(os, "open", track_open)
        faults.setattr(os, "fstat", fail_fstat)
        faults.setattr(os, "stat", fail_stat)
        with pytest.raises(runtime.RuntimeIntentError) as raised, _store(tmp_path, owner):
            pass
        assert raised.value.reason == "unsafe_path"

    assert captured
    closed = _descriptor_closed(captured[0])
    if not closed:
        os.close(captured[0])
    assert closed


def test_open_root_second_fstat_failure_closes_popped_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _owner()
    real_open = os.open
    real_fstat = os.fstat
    captured: list[int] = []
    inspections = 0

    def track_open(path: _OsPath, flags: int, *args: Any, **kwargs: Any) -> int:
        descriptor = real_open(path, flags, *args, **kwargs)
        if str(path) == tmp_path.name and not captured:
            captured.append(descriptor)
        return descriptor

    def fail_second_fstat(descriptor: int) -> os.stat_result:
        nonlocal inspections
        if captured and descriptor == captured[0]:
            inspections += 1
            if inspections == 2:
                raise OSError("injected second root fstat failure")
        return real_fstat(descriptor)

    with monkeypatch.context() as faults:
        faults.setattr(os, "open", track_open)
        faults.setattr(os, "fstat", fail_second_fstat)
        with pytest.raises((OSError, runtime.RuntimeIntentError)), _store(tmp_path, owner):
            pass

    assert captured
    assert inspections == 2
    closed = _descriptor_closed(captured[0])
    if not closed:
        os.close(captured[0])
    assert closed


def test_private_directory_mode_race_during_open_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _owner()
    with _store(tmp_path, owner) as store:
        identity_path = store.identity_directory
    real_open = os.open
    changed = False

    def chmod_after_open(path: _OsPath, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal changed
        descriptor = real_open(path, flags, *args, **kwargs)
        if str(path) == owner.key_fingerprint and not changed:
            identity_path.chmod(0o755)
            changed = True
        return descriptor

    monkeypatch.setattr(os, "open", chmod_after_open)
    try:
        with pytest.raises(runtime.RuntimeIntentError) as raised, _store(tmp_path, owner):
            pass
        assert raised.value.reason == "unsafe_mode"
        assert changed
    finally:
        identity_path.chmod(0o700)


def test_private_directory_mode_change_while_open_is_rejected_on_next_operation(
    tmp_path: Path,
) -> None:
    owner = _owner()
    with _store(tmp_path, owner) as store:
        intents_path = store.intent_path(_TASK_ID, "bid").parent
        intents_path.chmod(0o755)
        try:
            with pytest.raises(runtime.RuntimeIntentError) as raised:
                store.load(_TASK_ID, "bid")
            assert raised.value.reason == "unsafe_mode"
        finally:
            intents_path.chmod(0o700)


def test_same_inode_rewrite_during_record_read_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _owner()
    with _store(tmp_path, owner) as store:
        store.create(_intent())
        path = store.intent_path(_TASK_ID, "bid")
        original = path.read_bytes()
        identity = (path.stat().st_dev, path.stat().st_ino)
        original_stat = path.stat()
        real_read = os.read
        rewritten = False

        def rewrite_then_read(descriptor: int, size: int) -> bytes:
            nonlocal rewritten
            observed = os.fstat(descriptor)
            if not rewritten and (observed.st_dev, observed.st_ino) == identity:
                path.write_bytes(original)
                path.chmod(0o600)
                os.utime(
                    path,
                    ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns + 1_000_000_000),
                )
                rewritten = True
            return real_read(descriptor, size)

        monkeypatch.setattr(os, "read", rewrite_then_read)
        with pytest.raises(runtime.RuntimeIntentError) as raised:
            store.load(_TASK_ID, "bid")
        assert raised.value.reason == "unsafe_path"
        assert rewritten


def test_intent_directory_public_path_swap_during_record_read_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _owner()
    with _store(tmp_path, owner) as store:
        store.create(_intent())
        path = store.intent_path(_TASK_ID, "bid")
        intents_path = path.parent
        parked = intents_path.with_name("intents-parked-during-read")
        record_identity = (path.stat().st_dev, path.stat().st_ino)
        real_read = os.read
        swapped = False

        def swap_then_read(descriptor: int, size: int) -> bytes:
            nonlocal swapped
            observed = os.fstat(descriptor)
            if not swapped and (observed.st_dev, observed.st_ino) == record_identity:
                intents_path.rename(parked)
                intents_path.mkdir(mode=0o700)
                swapped = True
            return real_read(descriptor, size)

        monkeypatch.setattr(os, "read", swap_then_read)
        try:
            with pytest.raises(runtime.RuntimeIntentError) as raised:
                store.load(_TASK_ID, "bid")
            assert raised.value.reason == "unsafe_path"
            assert swapped
        finally:
            if swapped:
                intents_path.rmdir()
                parked.rename(intents_path)


@pytest.mark.parametrize("winner_kind", ["exact", "changed"])
def test_initial_create_preserves_concurrently_inserted_winner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    winner_kind: str,
) -> None:
    owner = _owner()
    candidate = _intent()
    winner = candidate if winner_kind == "exact" else _intent(amount=10)
    _winner_path, winner_bytes = _persisted_record(tmp_path / "winner", owner, winner)
    winner_semantic_sha256 = json.loads(winner_bytes)["semantic_sha256"]
    main_root = tmp_path / "main"
    real_open = os.open
    real_write = os.write
    real_fsync = os.fsync
    real_close = os.close
    real_replace = os.replace
    real_link = os.link
    inserted_inode: int | None = None

    with _store(main_root, owner) as store:
        destination = store.intent_path(_TASK_ID, "bid")

        def insert_winner(directory_descriptor: int) -> None:
            nonlocal inserted_inode
            if inserted_inode is not None:
                return
            descriptor = real_open(
                destination.name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory_descriptor,
            )
            try:
                offset = 0
                while offset < len(winner_bytes):
                    offset += real_write(descriptor, winner_bytes[offset:])
                real_fsync(descriptor)
                inserted_inode = os.fstat(descriptor).st_ino
            finally:
                real_close(descriptor)

        def race_replace(source: _OsPath, target: _OsPath, *args: Any, **kwargs: Any) -> None:
            if str(target) == destination.name:
                insert_winner(kwargs["dst_dir_fd"])
            real_replace(source, target, *args, **kwargs)

        def race_link(source: _OsPath, target: _OsPath, *args: Any, **kwargs: Any) -> None:
            if str(target) == destination.name:
                insert_winner(kwargs["dst_dir_fd"])
            real_link(source, target, *args, **kwargs)

        monkeypatch.setattr(os, "replace", race_replace)
        monkeypatch.setattr(os, "link", race_link)
        if winner_kind == "exact":
            result = store.create(candidate)
            assert result.semantic_sha256 == winner_semantic_sha256
        else:
            with pytest.raises(runtime.RuntimeIntentError) as raised:
                store.create(candidate)
            assert raised.value.reason == "slot_conflict"

        assert inserted_inode is not None
        assert destination.read_bytes() == winner_bytes
        assert destination.stat().st_ino == inserted_inode


def _exception_messages(error: BaseException) -> tuple[list[str], list[str]]:
    messages: list[str] = []
    notes: list[str] = []
    current: BaseException | None = error
    observed: set[int] = set()
    while current is not None and id(current) not in observed:
        observed.add(id(current))
        messages.append(str(current))
        notes.extend(str(note) for note in getattr(current, "__notes__", ()))
        current = current.__cause__ or current.__context__
    return messages, notes


class _PublicationFaultHarness:
    def __init__(self, record_name: str, primary_fault: str) -> None:
        self.record_name = record_name
        self.primary_fault = primary_fault
        self.descriptor: int | None = None
        self.name: str | None = None
        self._close_failure_injected = False
        self._unlink_failure_injected = False
        self._open = os.open
        self._write = os.write
        self._fsync = os.fsync
        self._close = os.close
        self._unlink = os.unlink

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(os, "open", self.track_open)
        monkeypatch.setattr(os, "write", self.fail_write)
        monkeypatch.setattr(os, "fsync", self.fail_fsync)
        monkeypatch.setattr(os, "close", self.noisy_close)
        monkeypatch.setattr(os, "unlink", self.noisy_unlink)

    def track_open(self, path: _OsPath, flags: int, *args: Any, **kwargs: Any) -> int:
        descriptor = self._open(path, flags, *args, **kwargs)
        name = str(path)
        if name.startswith(f".{self.record_name}.") and name.endswith(".tmp"):
            self.descriptor = descriptor
            self.name = name
        return descriptor

    def fail_write(self, descriptor: int, content: bytes) -> int:
        if self.primary_fault == "write" and descriptor == self.descriptor:
            raise OSError("primary write failure")
        return self._write(descriptor, content)

    def fail_fsync(self, descriptor: int) -> None:
        if self.primary_fault == "fsync" and descriptor == self.descriptor:
            raise OSError("primary fsync failure")
        self._fsync(descriptor)

    def noisy_close(self, descriptor: int) -> None:
        if descriptor == self.descriptor and not self._close_failure_injected:
            self._close_failure_injected = True
            self._close(descriptor)
            raise OSError("cleanup close failure")
        self._close(descriptor)

    def noisy_unlink(self, path: _OsPath, *args: Any, **kwargs: Any) -> None:
        if str(path) == self.name and not self._unlink_failure_injected:
            self._unlink_failure_injected = True
            self._unlink(path, *args, **kwargs)
            raise OSError("cleanup unlink failure")
        self._unlink(path, *args, **kwargs)


@pytest.mark.parametrize("primary_fault", ["write", "fsync"])
def test_primary_publication_error_survives_cleanup_failures_and_closes_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    primary_fault: str,
) -> None:
    owner = _owner()

    with _store(tmp_path, owner) as store:
        record_name = store.intent_path(_TASK_ID, "bid").name
        faults = _PublicationFaultHarness(record_name, primary_fault)
        faults.install(monkeypatch)
        with pytest.raises(runtime.RuntimeIntentError) as raised:
            store.create(_intent())

        messages, notes = _exception_messages(raised.value)
        expected_primary = f"primary {primary_fault} failure"
        assert any(expected_primary in message for message in messages)
        assert any("cleanup close failure" in note for note in notes)
        assert any("cleanup unlink failure" in note for note in notes)
        assert faults.descriptor is not None
        assert _descriptor_closed(faults.descriptor)
        assert faults.name is not None
        assert not (store.intent_path(_TASK_ID, "bid").parent / faults.name).exists()
