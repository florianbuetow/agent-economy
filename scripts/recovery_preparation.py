"""Prepare a stopped, identity-bound recovery inventory for one isolated runtime."""

from __future__ import annotations

import errno
import hashlib
import math
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import yaml
from scripts.isolated_test_runtime import SERVICE_ORDER
from scripts.recovery_inventory import build_recovery_inventory, read_recovery_source_file
from scripts.recovery_registry import capture_registry_bindings
from service_commons.maintenance_lock import (
    MaintenanceLockMode,
    hold_existing_maintenance_lock,
)
from service_commons.recovery_contract import (
    MaintenanceLockIdentityV1,
    OwnedProcessV1,
    RecoveryInventoryV1,
    canonical_json_bytes,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from scripts.isolated_test_runtime import IsolatedRuntime


_INVENTORY_FILENAME = "recovery-inventory.json"
_RUNTIME_MANIFEST_FILENAME = "runtime.json"


class RecoveryPreparationError(RuntimeError):
    """A source runtime could not produce trustworthy recovery evidence."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(message)


class _PublicationError(RecoveryPreparationError):
    def __init__(self, reason: str, message: str, *, published: bool) -> None:
        self.published = published
        super().__init__(reason, message)


@dataclass(frozen=True, slots=True)
class PreparedRecovery:
    """Paths and digest proven durable by a successful preparation transaction."""

    inventory_path: Path
    inventory_sha256: str
    runtime_manifest_path: Path


def _strict_positive_timeout(value: float) -> float:
    if type(value) is not float or not math.isfinite(value) or value <= 0:
        raise ValueError("registry_timeout_seconds must be a finite positive float")
    return value


def _strict_positive_attempts(value: int) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError("registry_max_attempts must be a positive strict integer")
    return value


def _full_runtime(runtime: IsolatedRuntime) -> None:
    if tuple(runtime.services) != SERVICE_ORDER:
        raise ValueError("Recovery preparation requires one full seven-service runtime")
    if frozenset(runtime.config_paths) != frozenset(SERVICE_ORDER):
        raise ValueError("Recovery preparation requires all seven service configurations")
    if frozenset(runtime._startup_inputs) != frozenset(SERVICE_ORDER):
        raise ValueError("Recovery preparation requires all seven recorded startup inputs")
    if runtime._maintenance_identity is None:
        raise ValueError("Recovery preparation requires a recorded maintenance identity")


def _strict_process_identity(record: Mapping[str, object], *, label: str) -> tuple[int, int, int]:
    identities = tuple(record.get(key) for key in ("pid", "pgid", "sid"))
    validated: list[int] = []
    for value in identities:
        if type(value) is not int or value <= 0:
            raise RecoveryPreparationError(
                "invalid_process_record", f"{label} has invalid identity"
            )
        validated.append(value)
    return validated[0], validated[1], validated[2]


def _strict_process_state(
    record: Mapping[str, object], *, label: str
) -> tuple[bool, bool, int | None, str | None]:
    running = record.get("running")
    group_drained = record.get("group_drained")
    leader_returncode = record.get("leader_returncode")
    cleanup_error = record.get("cleanup_error")
    if type(running) is not bool or type(group_drained) is not bool:
        raise RecoveryPreparationError(
            "invalid_process_record", f"{label} has invalid process-state flags"
        )
    if leader_returncode is not None and type(leader_returncode) is not int:
        raise RecoveryPreparationError(
            "invalid_process_record", f"{label} has an invalid leader return code"
        )
    if cleanup_error is not None and type(cleanup_error) is not str:
        raise RecoveryPreparationError(
            "invalid_process_record", f"{label} has an invalid cleanup error"
        )
    return running, group_drained, leader_returncode, cleanup_error


def _require_drained_record(record: Mapping[str, object], *, label: str) -> None:
    _strict_process_identity(record, label=label)
    running, group_drained, leader_returncode, cleanup_error = _strict_process_state(
        record, label=label
    )
    if running or not group_drained or cleanup_error is not None or leader_returncode is None:
        raise RecoveryPreparationError(
            "process_not_drained",
            f"{label} is not a completely drained owned process group",
        )


def _require_live_services(runtime: IsolatedRuntime) -> None:
    if runtime.active_command is not None:
        raise RecoveryPreparationError(
            "active_command", "Recovery preparation cannot run while an owned command is active"
        )
    if frozenset(runtime.processes) != frozenset(SERVICE_ORDER):
        raise RecoveryPreparationError(
            "services_not_live", "Recovery preparation requires all seven owned service handles"
        )
    if frozenset(runtime.process_records) != frozenset(SERVICE_ORDER):
        raise RecoveryPreparationError(
            "invalid_process_record", "Recovery preparation requires seven service records"
        )
    for service in SERVICE_ORDER:
        process = runtime.processes[service]
        record = runtime.process_records[service]
        pid, pgid, sid = _strict_process_identity(record, label=service)
        running, group_drained, leader_returncode, cleanup_error = _strict_process_state(
            record, label=service
        )
        if (
            process.pid != pid
            or process.poll() is not None
            or not running
            or group_drained
            or leader_returncode is not None
            or cleanup_error is not None
            or pgid != pid
            or sid != pid
        ):
            raise RecoveryPreparationError(
                "services_not_live", f"Service {service} is not a live owned process group"
            )
        try:
            actual_pgid = os.getpgid(pid)
            actual_sid = os.getsid(pid)
        except OSError as exc:
            raise RecoveryPreparationError(
                "services_not_live", f"Service {service} process identity is unavailable"
            ) from exc
        if actual_pgid != pgid or actual_sid != sid:
            raise RecoveryPreparationError(
                "services_not_live", f"Service {service} process ownership changed"
            )

    for index, command_record in enumerate(runtime.bootstrap_records):
        _require_drained_record(command_record, label=f"key-bootstrap-{index}")
    for index, command_record in enumerate(runtime.command_records):
        _require_drained_record(command_record, label=f"phase-command-{index}")


def _record_to_owned_process(
    *,
    logical_id: str,
    record: Mapping[str, object],
    config_id: str | None,
    process_cwd: Path,
    agents_package_root: Path | None,
) -> OwnedProcessV1:
    pid, pgid, sid = _strict_process_identity(record, label=logical_id)
    running, group_drained, leader_returncode, cleanup_error = _strict_process_state(
        record, label=logical_id
    )
    return OwnedProcessV1(
        logical_id=logical_id,
        config_id=config_id,
        process_cwd=process_cwd,
        agents_package_root=agents_package_root,
        pid=pid,
        pgid=pgid,
        sid=sid,
        running=running,
        group_drained=group_drained,
        leader_returncode=leader_returncode,
        cleanup_error=cleanup_error,
    )


def _process_ownership(runtime: IsolatedRuntime) -> tuple[OwnedProcessV1, ...]:
    records: list[OwnedProcessV1] = []
    for service in SERVICE_ORDER:
        startup = runtime._startup_inputs[service]
        records.append(
            _record_to_owned_process(
                logical_id=service,
                record=runtime.process_records[service],
                config_id=service,
                process_cwd=Path(startup["process_cwd"]),
                agents_package_root=None,
            )
        )
    agents_package_root = runtime.repo_root / "agents"
    for prefix, source in (
        ("key-bootstrap", runtime.bootstrap_records),
        ("phase-command", runtime.command_records),
    ):
        for index, record_value in enumerate(source):
            record = record_value
            cwd = record.get("cwd")
            if type(cwd) is not str:
                raise RecoveryPreparationError(
                    "invalid_process_record", f"{prefix}-{index} is missing its process CWD"
                )
            records.append(
                _record_to_owned_process(
                    logical_id=f"{prefix}-{index}",
                    record=record,
                    config_id=None,
                    process_cwd=Path(cwd),
                    agents_package_root=agents_package_root,
                )
            )
    return tuple(records)


def _require_all_drained(runtime: IsolatedRuntime) -> None:
    if runtime.processes:
        raise RecoveryPreparationError(
            "process_not_drained", "Owned service handles remain after runtime cleanup"
        )
    if runtime.active_command is not None:
        raise RecoveryPreparationError(
            "process_not_drained", "An owned command remains after runtime cleanup"
        )
    for service in SERVICE_ORDER:
        _require_drained_record(runtime.process_records[service], label=service)
    for index, record in enumerate(runtime.bootstrap_records):
        _require_drained_record(record, label=f"key-bootstrap-{index}")
    for index, record in enumerate(runtime.command_records):
        _require_drained_record(record, label=f"phase-command-{index}")


def _required_registered_handles(runtime: IsolatedRuntime) -> frozenset[str]:
    content = read_recovery_source_file(runtime.config_paths["ui"], label="UI source config")
    try:
        config: Any = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise RecoveryPreparationError("invalid_ui_config", "UI source config is invalid") from exc
    user_agent = config.get("user_agent") if isinstance(config, dict) else None
    handle = user_agent.get("handle") if isinstance(user_agent, dict) else None
    if type(handle) is not str or not handle or handle.strip() != handle:
        raise RecoveryPreparationError(
            "invalid_ui_config", "UI source config has no canonical user-agent handle"
        )
    return frozenset({"platform", handle})


def _build_inventory(
    runtime: IsolatedRuntime,
    *,
    registry: dict[str, str],
    required_handles: frozenset[str],
) -> RecoveryInventoryV1:
    assert runtime._maintenance_identity is not None
    device, inode = runtime._maintenance_identity
    process_cwds = {
        service: Path(runtime._startup_inputs[service]["process_cwd"]) for service in SERVICE_ORDER
    }
    process_cwds["agents"] = runtime.repo_root / "agents"
    inventory = build_recovery_inventory(
        runtime_id=runtime.run_id,
        source_root=runtime.run_dir,
        config_paths={**runtime.config_paths, "agents": runtime.agent_config_path},
        process_cwds=process_cwds,
        agents_package_root=runtime.repo_root / "agents",
        code_root=runtime.repo_root,
        maintenance_lock_identity=MaintenanceLockIdentityV1(device=device, inode=inode),
        registry_bindings=registry,
        required_registered_handles=required_handles,
        process_ownership=_process_ownership(runtime),
    )
    if not isinstance(inventory, RecoveryInventoryV1):
        raise TypeError("build_recovery_inventory must return RecoveryInventoryV1")
    return inventory


def _without_processes(inventory: RecoveryInventoryV1) -> RecoveryInventoryV1:
    return inventory.model_copy(update={"process_ownership": ()})


def _capture_live_inventory(
    runtime: IsolatedRuntime,
    *,
    timeout: float,
    max_attempts: int,
) -> tuple[RecoveryInventoryV1, dict[str, str], frozenset[str]]:
    runtime._validate_recovery_startup_inputs()
    _require_live_services(runtime)
    required_handles = _required_registered_handles(runtime)
    with httpx.Client(
        base_url=runtime.service_url("identity"),
        timeout=timeout,
        follow_redirects=False,
    ) as client:
        registry_a = capture_registry_bindings(client, max_attempts=max_attempts)
        inventory_a = _build_inventory(
            runtime, registry=registry_a, required_handles=required_handles
        )
        registry_b = capture_registry_bindings(client, max_attempts=max_attempts)
        inventory_b = _build_inventory(
            runtime, registry=registry_b, required_handles=required_handles
        )
    runtime._validate_recovery_startup_inputs()
    if registry_a != registry_b or _without_processes(inventory_a) != _without_processes(
        inventory_b
    ):
        raise RecoveryPreparationError(
            "source_changed", "Recovery source changed between immediate live captures"
        )
    return inventory_b, registry_b, required_handles


@dataclass(frozen=True, slots=True)
class _PinnedRunDirectory:
    descriptors: tuple[int, ...]
    component_names: tuple[str, ...]

    @property
    def descriptor(self) -> int:
        return self.descriptors[-1]

    def verify(self) -> None:
        for index, component in enumerate(self.component_names, start=1):
            parent_descriptor = self.descriptors[index - 1]
            child_descriptor = self.descriptors[index]
            try:
                current = os.stat(
                    component,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                opened = os.fstat(child_descriptor)
            except OSError as exc:
                raise RecoveryPreparationError(
                    "unsafe_run_directory", "Runtime directory ancestry changed"
                ) from exc
            if (
                not stat.S_ISDIR(current.st_mode)
                or not stat.S_ISDIR(opened.st_mode)
                or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
            ):
                raise RecoveryPreparationError(
                    "unsafe_run_directory", "Runtime directory ancestry changed"
                )

    def close(self) -> None:
        failures: list[BaseException] = []
        for descriptor in reversed(self.descriptors):
            try:
                os.close(descriptor)
            except BaseException as exc:
                failures.append(exc)
        if failures:
            error = RecoveryPreparationError(
                "unsafe_run_directory", "Runtime directory descriptors could not be closed"
            )
            for failure in failures[1:]:
                error.add_note(f"Additional directory close failed: {failure}")
            raise error from failures[0]


def _directory_open_flags() -> int:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return flags


def _open_run_directory(path: Path) -> _PinnedRunDirectory:
    if not path.is_absolute() or Path(os.path.normpath(path)) != path or not path.name:
        raise RecoveryPreparationError(
            "unsafe_run_directory", "Runtime root must be a canonical absolute directory"
        )
    descriptors: list[int] = []
    components = tuple(path.parts[1:])
    try:
        descriptors.append(os.open(path.anchor, _directory_open_flags()))
        for component in components:
            parent_descriptor = descriptors[-1]
            before = os.stat(
                component,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            if not stat.S_ISDIR(before.st_mode) or stat.S_ISLNK(before.st_mode):
                raise RecoveryPreparationError(
                    "unsafe_run_directory", "Runtime root contains a non-directory ancestor"
                )
            child_descriptor = os.open(
                component,
                _directory_open_flags(),
                dir_fd=parent_descriptor,
            )
            descriptors.append(child_descriptor)
            opened = os.fstat(child_descriptor)
            current = os.stat(
                component,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISDIR(opened.st_mode)
                or not stat.S_ISDIR(current.st_mode)
                or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
                or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
            ):
                raise RecoveryPreparationError(
                    "unsafe_run_directory", "Runtime directory ancestry changed while opening"
                )
        pinned = _PinnedRunDirectory(tuple(descriptors), components)
        pinned.verify()
        return pinned
    except BaseException as exc:
        close_failures: list[BaseException] = []
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except BaseException as close_error:
                close_failures.append(close_error)
        if isinstance(exc, RecoveryPreparationError):
            error = exc
        else:
            error = RecoveryPreparationError(
                "unsafe_run_directory", "Runtime directory could not be opened safely"
            )
        for recorded_close_error in close_failures:
            error.add_note(f"Runtime directory cleanup also failed: {recorded_close_error}")
        if error is exc:
            raise
        raise error from exc


def _write_all(descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(descriptor, content[offset:])
        if written <= 0:
            raise OSError(errno.EIO, "Recovery inventory write made no progress")
        offset += written


def _publish_inventory(run_dir: Path, content: bytes) -> Path:
    target = run_dir / _INVENTORY_FILENAME
    pinned_directory = _open_run_directory(run_dir)
    directory_descriptor = pinned_directory.descriptor
    temporary_name = f".{_INVENTORY_FILENAME}.{os.getpid()}.tmp"
    temporary_descriptor: int | None = None
    linked = False
    primary: BaseException | None = None
    cleanup_errors: list[BaseException] = []
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        temporary_descriptor = os.open(
            temporary_name,
            flags,
            0o600,
            dir_fd=directory_descriptor,
        )
        os.fchmod(temporary_descriptor, 0o600)
        _write_all(temporary_descriptor, content)
        os.fsync(temporary_descriptor)
        os.close(temporary_descriptor)
        temporary_descriptor = None
        os.link(
            temporary_name,
            _INVENTORY_FILENAME,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        linked = True
        pinned_directory.verify()
        os.fsync(directory_descriptor)
        os.unlink(temporary_name, dir_fd=directory_descriptor)
        pinned_directory.verify()
        os.fsync(directory_descriptor)
    except BaseException as exc:
        primary = exc
    finally:
        if temporary_descriptor is not None:
            try:
                os.close(temporary_descriptor)
            except BaseException as exc:
                cleanup_errors.append(exc)
        try:
            os.unlink(temporary_name, dir_fd=directory_descriptor)
        except FileNotFoundError:
            pass
        except BaseException as exc:
            cleanup_errors.append(exc)
        try:
            pinned_directory.close()
        except BaseException as exc:
            cleanup_errors.append(exc)
    if primary is not None or cleanup_errors:
        cause = primary if primary is not None else cleanup_errors[0]
        for cleanup_error in cleanup_errors:
            if cleanup_error is not cause:
                cause.add_note(f"Recovery publication cleanup also failed: {cleanup_error}")
        raise _PublicationError(
            "publication_uncertain" if linked else "publication_failed",
            "Recovery inventory publication could not be proven durable",
            published=linked,
        ) from cause
    return target


def _fsync_run_directory(run_dir: Path) -> None:
    pinned_directory = _open_run_directory(run_dir)
    try:
        pinned_directory.verify()
        os.fsync(pinned_directory.descriptor)
        pinned_directory.verify()
    except BaseException as primary:
        try:
            pinned_directory.close()
        except BaseException as close_error:
            primary.add_note(f"Runtime directory close also failed: {close_error}")
        raise
    else:
        pinned_directory.close()


def _record_failure(runtime: IsolatedRuntime, error: BaseException) -> None:
    runtime._recovery_status = {
        "state": "failed",
        "error_type": type(error).__name__,
    }
    try:
        runtime._write_manifest()
        _fsync_run_directory(runtime.run_dir)
    except BaseException as manifest_error:
        error.add_note(f"Failure-status manifest also failed: {manifest_error}")


def _record_publication_uncertain(
    runtime: IsolatedRuntime,
    *,
    inventory_path: Path,
    inventory_sha256: str,
    error: BaseException,
) -> None:
    runtime._recovery_status = {
        "state": "publication_uncertain",
        "inventory_path": str(inventory_path),
        "inventory_sha256": inventory_sha256,
    }
    try:
        runtime._write_manifest()
        _fsync_run_directory(runtime.run_dir)
    except BaseException as manifest_error:
        error.add_note(f"Publication-uncertain manifest also failed: {manifest_error}")


def prepare_recovery_and_stop(  # noqa: PLR0912, PLR0915
    runtime: IsolatedRuntime,
    *,
    registry_timeout_seconds: float,
    registry_max_attempts: int,
) -> PreparedRecovery:
    """Capture stable live facts, stop owned processes, then publish stopped facts.

    Cleanup is attempted for every input failure. The canonical inventory becomes
    visible only after two matching live captures and one matching stopped capture.
    """

    stop_attempted = False
    inventory_path = runtime.run_dir / _INVENTORY_FILENAME
    inventory_digest: str | None = None
    published = False
    inventory_preexists = inventory_path.exists() or inventory_path.is_symlink()
    preserve_existing_status = inventory_preexists and runtime._recovery_status.get("state") in {
        "ready",
        "publication_uncertain",
    }
    try:
        timeout = _strict_positive_timeout(registry_timeout_seconds)
        max_attempts = _strict_positive_attempts(registry_max_attempts)
        _full_runtime(runtime)
        if inventory_preexists:
            raise RecoveryPreparationError(
                "inventory_exists", "Recovery inventory already exists and will not be replaced"
            )
        runtime._recovery_status = {"state": "preparing"}
        runtime._write_manifest()
        assert runtime._maintenance_identity is not None
        device, inode = runtime._maintenance_identity
        with hold_existing_maintenance_lock(
            runtime.run_dir / "maintenance.lock",
            mode=MaintenanceLockMode.SHARED,
            blocking=False,
            expected_device=device,
            expected_inode=inode,
        ) as held_lock:
            live_error: BaseException | None = None
            live_result: tuple[RecoveryInventoryV1, dict[str, str], frozenset[str]] | None = None
            try:
                live_result = _capture_live_inventory(
                    runtime,
                    timeout=timeout,
                    max_attempts=max_attempts,
                )
            except BaseException as exc:
                live_error = exc

            stop_attempted = True
            try:
                runtime.stop()
            except BaseException as cleanup_error:
                if live_error is not None:
                    live_error.add_note(f"Runtime cleanup also failed: {cleanup_error}")
                else:
                    raise
            if live_error is not None:
                raise live_error.with_traceback(live_error.__traceback__)
            assert live_result is not None

            held_lock.verify_identity()
            _require_all_drained(runtime)
            runtime._validate_recovery_startup_inputs()
            provisional, registry, required_handles = live_result
            final_inventory = _build_inventory(
                runtime,
                registry=registry,
                required_handles=required_handles,
            )
            held_lock.verify_identity()
            if _without_processes(provisional) != _without_processes(final_inventory):
                raise RecoveryPreparationError(
                    "source_changed", "Recovery source changed before stopped capture"
                )

            content = canonical_json_bytes(final_inventory)
            inventory_digest = hashlib.sha256(content).hexdigest()
            try:
                _publish_inventory(runtime.run_dir, content)
            except _PublicationError as exc:
                published = exc.published
                raise
            published = True
            runtime._recovery_status = {
                "state": "ready",
                "inventory_path": str(inventory_path),
                "inventory_sha256": inventory_digest,
                "inventory": final_inventory.model_dump(mode="json"),
            }
            try:
                runtime._write_manifest()
                _fsync_run_directory(runtime.run_dir)
            except BaseException as exc:
                _record_publication_uncertain(
                    runtime,
                    inventory_path=inventory_path,
                    inventory_sha256=inventory_digest,
                    error=exc,
                )
                raise
        return PreparedRecovery(
            inventory_path=inventory_path,
            inventory_sha256=inventory_digest,
            runtime_manifest_path=runtime.run_dir / _RUNTIME_MANIFEST_FILENAME,
        )
    except BaseException as primary:
        if not stop_attempted:
            stop_attempted = True
            try:
                runtime.stop()
            except BaseException as cleanup_error:
                primary.add_note(f"Runtime cleanup also failed: {cleanup_error}")
        if preserve_existing_status:
            pass
        elif published:
            assert inventory_digest is not None
            if runtime._recovery_status.get("state") != "publication_uncertain":
                _record_publication_uncertain(
                    runtime,
                    inventory_path=inventory_path,
                    inventory_sha256=inventory_digest,
                    error=primary,
                )
        else:
            _record_failure(runtime, primary)
        raise
