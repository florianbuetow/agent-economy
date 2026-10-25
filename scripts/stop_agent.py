#!/usr/bin/env python3
"""Stop only a host-confirmed local process owned by this checkout."""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import errno
import math
import os
import re
import signal
import shutil
import socket
import struct
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

_CONFIG_ENVIRONMENT_VARIABLE = "AGENT_CONFIG_PATH"
_KERN_PROCARGS2 = 49
_CTL_KERN = 1
_PROC_PIDTBSDINFO = 3
_PROC_PIDVNODEPATHINFO = 9
_MAXPATHLEN = 1024


class ProcessAbsentError(ProcessLookupError):
    """The inspected PID no longer identifies a process."""


class _DarwinBsdInfo(ctypes.Structure):
    _fields_ = (
        ("flags", ctypes.c_uint32),
        ("status", ctypes.c_uint32),
        ("xstatus", ctypes.c_uint32),
        ("pid", ctypes.c_uint32),
        ("ppid", ctypes.c_uint32),
        ("uid", ctypes.c_uint32),
        ("gid", ctypes.c_uint32),
        ("ruid", ctypes.c_uint32),
        ("rgid", ctypes.c_uint32),
        ("svuid", ctypes.c_uint32),
        ("svgid", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32),
        ("command", ctypes.c_char * 16),
        ("name", ctypes.c_char * 32),
        ("open_files", ctypes.c_uint32),
        ("process_group", ctypes.c_uint32),
        ("job_control_count", ctypes.c_uint32),
        ("controlling_device", ctypes.c_uint32),
        ("terminal_process_group", ctypes.c_uint32),
        ("nice", ctypes.c_int32),
        ("start_seconds", ctypes.c_uint64),
        ("start_microseconds", ctypes.c_uint64),
    )


class _DarwinVinfoStat(ctypes.Structure):
    _fields_ = (
        ("device", ctypes.c_uint32),
        ("mode", ctypes.c_uint16),
        ("link_count", ctypes.c_uint16),
        ("inode", ctypes.c_uint64),
        ("uid", ctypes.c_uint32),
        ("gid", ctypes.c_uint32),
        ("access_seconds", ctypes.c_int64),
        ("access_nanoseconds", ctypes.c_int64),
        ("modified_seconds", ctypes.c_int64),
        ("modified_nanoseconds", ctypes.c_int64),
        ("changed_seconds", ctypes.c_int64),
        ("changed_nanoseconds", ctypes.c_int64),
        ("birth_seconds", ctypes.c_int64),
        ("birth_nanoseconds", ctypes.c_int64),
        ("size", ctypes.c_int64),
        ("blocks", ctypes.c_int64),
        ("block_size", ctypes.c_int32),
        ("flags", ctypes.c_uint32),
        ("generation", ctypes.c_uint32),
        ("raw_device", ctypes.c_uint32),
        ("reserved", ctypes.c_int64 * 2),
    )


class _DarwinFsid(ctypes.Structure):
    _fields_ = (("value", ctypes.c_int32 * 2),)


class _DarwinVnodeInfo(ctypes.Structure):
    _fields_ = (
        ("stat", _DarwinVinfoStat),
        ("vnode_type", ctypes.c_int32),
        ("padding", ctypes.c_int32),
        ("filesystem_id", _DarwinFsid),
    )


class _DarwinVnodeInfoPath(ctypes.Structure):
    _fields_ = (
        ("vnode", _DarwinVnodeInfo),
        ("path", ctypes.c_char * _MAXPATHLEN),
    )


class _DarwinVnodePathInfo(ctypes.Structure):
    _fields_ = (
        ("current_directory", _DarwinVnodeInfoPath),
        ("root_directory", _DarwinVnodeInfoPath),
    )


@dataclass(frozen=True)
class ProcessIdentity:
    """Kernel-backed facts that must remain identical through signal time."""

    pid: int
    ppid: int
    uid: int
    start_id: str
    cwd: Path
    executable: Path
    argv: tuple[str, ...]
    environment: Mapping[str, str]


@dataclass(frozen=True)
class StopTarget:
    """Exact local agent identity selected by a stop recipe."""

    repo_root: Path
    agents_dir: Path
    interpreter: Path
    config_path: Path
    module: str
    profile: str | None

    @classmethod
    def task_feeder(cls, repo_root: Path, config_path: Path) -> StopTarget:
        return cls._create(repo_root, config_path, "task_feeder", None)

    @classmethod
    def math_worker(
        cls, repo_root: Path, config_path: Path, profile: str
    ) -> StopTarget:
        if not profile:
            raise ValueError("Math worker profile must not be empty")
        return cls._create(repo_root, config_path, "math_worker", profile)

    @classmethod
    def _create(
        cls, repo_root: Path, config_path: Path, module: str, profile: str | None
    ) -> StopTarget:
        resolved_repo = repo_root.resolve(strict=True)
        resolved_agents = resolved_repo.joinpath("agents").resolve(strict=True)
        resolved_interpreter = resolved_agents.joinpath(
            ".venv", "bin", "python"
        ).resolve(strict=True)
        if not resolved_interpreter.is_file():
            raise ValueError(f"Agent interpreter is not a file: {resolved_interpreter}")
        selected_config = (
            config_path if config_path.is_absolute() else resolved_agents / config_path
        )
        resolved_config = selected_config.resolve(strict=True)
        if not resolved_config.is_file():
            raise ValueError(f"Agent config is not a file: {resolved_config}")
        return cls(
            repo_root=resolved_repo,
            agents_dir=resolved_agents,
            interpreter=resolved_interpreter,
            config_path=resolved_config,
            module=module,
            profile=profile,
        )


@dataclass(frozen=True)
class ServiceStopTarget:
    """Exact local uvicorn service selected by a component kill recipe."""

    repo_root: Path
    service_root: Path
    interpreter: Path
    uvicorn_launcher: Path
    config_path: Path
    entrypoint: str
    port: int

    @classmethod
    def create(
        cls,
        *,
        repo_root: Path,
        service_root: Path,
        config_path: Path,
        entrypoint: str,
        port: int,
    ) -> ServiceStopTarget:
        resolved_repo = repo_root.resolve(strict=True)
        resolved_service = service_root.resolve(strict=True)
        expected_services_root = resolved_repo.joinpath("services").resolve(strict=True)
        if resolved_service.parent != expected_services_root:
            raise ValueError(
                f"Service directory is outside this checkout: {resolved_service}"
            )
        expected_entrypoint = (
            f"{resolved_service.name.replace('-', '_')}_service.app:create_app"
        )
        if entrypoint != expected_entrypoint:
            raise ValueError(
                f"Service entrypoint {entrypoint!r} does not match {expected_entrypoint!r}"
            )
        if port < 1 or port > 65535:
            raise ValueError(f"Service port is invalid: {port}")
        interpreter = resolved_service.joinpath(".venv", "bin", "python").resolve(
            strict=True
        )
        uvicorn_launcher = resolved_service.joinpath(".venv", "bin", "uvicorn").resolve(
            strict=True
        )
        selected_config = (
            config_path if config_path.is_absolute() else resolved_service / config_path
        )
        resolved_config = selected_config.resolve(strict=True)
        if not interpreter.is_file() or not uvicorn_launcher.is_file():
            raise ValueError("Service virtual environment is incomplete")
        if not resolved_config.is_file():
            raise ValueError(f"Service config is not a file: {resolved_config}")
        return cls(
            repo_root=resolved_repo,
            service_root=resolved_service,
            interpreter=interpreter,
            uvicorn_launcher=uvicorn_launcher,
            config_path=resolved_config,
            entrypoint=entrypoint,
            port=port,
        )


def _decode_host_string(raw: bytes) -> str:
    return raw.decode("utf-8", errors="surrogateescape")


def _parse_nul_fields(raw: bytes) -> tuple[str, ...]:
    fields = raw.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    return tuple(_decode_host_string(value) for value in fields)


def _linux_kernel_identity(proc_dir: Path) -> tuple[int, int, str]:
    stat_result = proc_dir.stat()
    stat_fields = (
        proc_dir.joinpath("stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
    )
    return stat_result.st_uid, int(stat_fields[1]), stat_fields[19]


def _linux_process_identity(pid: int) -> ProcessIdentity:
    proc_dir = Path("/proc") / str(pid)
    try:
        initial_identity = _linux_kernel_identity(proc_dir)
    except (FileNotFoundError, ProcessLookupError) as error:
        raise ProcessAbsentError(pid) from error
    try:
        executable = proc_dir.joinpath("exe").resolve(strict=True)
        argv = _parse_nul_fields(proc_dir.joinpath("cmdline").read_bytes())
        environment_values = _parse_nul_fields(
            proc_dir.joinpath("environ").read_bytes()
        )
        environment = dict(
            value.split("=", 1) for value in environment_values if "=" in value
        )
        cwd = proc_dir.joinpath("cwd").resolve(strict=True)
    except (OSError, RuntimeError) as error:
        try:
            final_identity = _linux_kernel_identity(proc_dir)
        except (FileNotFoundError, ProcessLookupError) as absent_error:
            raise ProcessAbsentError(pid) from absent_error
        if final_identity != initial_identity:
            raise ProcessAbsentError(pid) from error
        raise RuntimeError(
            f"Cannot read complete metadata for live PID {pid}"
        ) from error
    try:
        final_identity = _linux_kernel_identity(proc_dir)
    except (FileNotFoundError, ProcessLookupError) as error:
        raise ProcessAbsentError(pid) from error
    if final_identity != initial_identity:
        raise ProcessAbsentError(pid)
    uid, ppid, start_id = initial_identity
    return ProcessIdentity(pid, ppid, uid, start_id, cwd, executable, argv, environment)


def _read_nul_field(raw: bytes, offset: int, pid: int) -> tuple[str, int]:
    field_end = raw.find(b"\0", offset)
    if field_end < 0:
        raise RuntimeError(f"Host returned malformed argv facts for PID {pid}")
    return _decode_host_string(raw[offset:field_end]), field_end + 1


def _parse_darwin_procargs_buffer(
    raw: bytes, pid: int
) -> tuple[str, tuple[str, ...], dict[str, str]]:
    if len(raw) < struct.calcsize("i"):
        raise RuntimeError(f"Host returned incomplete argv facts for PID {pid}")
    argument_count = struct.unpack_from("i", raw)[0]
    if argument_count < 1:
        raise RuntimeError(f"Host returned incomplete argv facts for PID {pid}")
    executable, offset = _read_nul_field(raw, struct.calcsize("i"), pid)
    while offset < len(raw) and raw[offset] == 0:
        offset += 1

    arguments: list[str] = []
    for _argument_index in range(argument_count):
        argument, offset = _read_nul_field(raw, offset, pid)
        arguments.append(argument)
    environment_values = _parse_nul_fields(raw[offset:])
    environment = dict(
        value.split("=", 1) for value in environment_values if "=" in value
    )
    return executable, tuple(arguments), environment


def _darwin_procargs(pid: int) -> tuple[str, tuple[str, ...], dict[str, str]]:
    library_name = ctypes.util.find_library("c")
    if library_name is None:
        raise RuntimeError("Cannot locate the host C library")
    libc = ctypes.CDLL(library_name, use_errno=True)
    sysctl = libc.sysctl
    sysctl.argtypes = (
        ctypes.POINTER(ctypes.c_int),
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_size_t),
        ctypes.c_void_p,
        ctypes.c_size_t,
    )
    sysctl.restype = ctypes.c_int
    mib = (ctypes.c_int * 3)(_CTL_KERN, _KERN_PROCARGS2, pid)
    size = ctypes.c_size_t(0)
    if sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), pid)
    buffer = ctypes.create_string_buffer(size.value)
    if sysctl(mib, 3, buffer, ctypes.byref(size), None, 0) != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), pid)

    return _parse_darwin_procargs_buffer(bytes(buffer.raw[: size.value]), pid)


def _darwin_proc_pidinfo(pid: int, flavor: int, process_info: ctypes.Structure) -> None:
    library_name = ctypes.util.find_library("proc")
    if library_name is None:
        raise RuntimeError("Cannot locate the host process library")
    libproc = ctypes.CDLL(library_name, use_errno=True)
    proc_pidinfo = libproc.proc_pidinfo
    proc_pidinfo.argtypes = (
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint64,
        ctypes.c_void_p,
        ctypes.c_int,
    )
    proc_pidinfo.restype = ctypes.c_int
    expected_size = ctypes.sizeof(process_info)
    ctypes.set_errno(0)
    returned_size = proc_pidinfo(
        pid,
        flavor,
        0,
        ctypes.byref(process_info),
        expected_size,
    )
    if returned_size != expected_size:
        error_number = ctypes.get_errno()
        if returned_size == 0 and error_number == errno.ESRCH:
            raise ProcessAbsentError(pid)
        if returned_size == 0 and error_number != 0:
            raise OSError(error_number, os.strerror(error_number), pid)
        raise RuntimeError(f"Host returned incomplete process metadata for PID {pid}")


def _darwin_bsd_identity(pid: int) -> tuple[int, int, str]:
    process_info = _DarwinBsdInfo()
    _darwin_proc_pidinfo(pid, _PROC_PIDTBSDINFO, process_info)
    if process_info.pid != pid:
        raise RuntimeError(f"Host returned mismatched identity for PID {pid}")
    start_id = f"{process_info.start_seconds}:{process_info.start_microseconds}"
    return process_info.uid, process_info.ppid, start_id


def _darwin_cwd(pid: int) -> Path:
    path_info = _DarwinVnodePathInfo()
    _darwin_proc_pidinfo(pid, _PROC_PIDVNODEPATHINFO, path_info)
    raw_path = bytes(path_info.current_directory.path)
    if not raw_path:
        raise RuntimeError(f"Host returned an empty cwd for PID {pid}")
    path_text = raw_path.decode("utf-8", errors="surrogateescape")
    return Path(path_text).resolve(strict=True)


def _darwin_process_identity(pid: int) -> ProcessIdentity:
    initial_identity = _darwin_bsd_identity(pid)
    try:
        cwd = _darwin_cwd(pid)
        executable_text, argv, environment = _darwin_procargs(pid)
    except ProcessAbsentError:
        raise
    except (OSError, RuntimeError) as error:
        try:
            final_identity = _darwin_bsd_identity(pid)
        except ProcessAbsentError as absent_error:
            raise ProcessAbsentError(pid) from absent_error
        if final_identity != initial_identity:
            raise ProcessAbsentError(pid) from error
        raise RuntimeError(
            f"Cannot read complete metadata for live PID {pid}"
        ) from error
    final_identity = _darwin_bsd_identity(pid)
    if final_identity != initial_identity:
        raise ProcessAbsentError(pid)
    uid, ppid, start_id = initial_identity
    return ProcessIdentity(
        pid=pid,
        ppid=ppid,
        uid=uid,
        start_id=start_id,
        cwd=cwd,
        executable=Path(executable_text).resolve(strict=True),
        argv=argv,
        environment=environment,
    )


def inspect_process(pid: int) -> ProcessIdentity:
    """Read one process identity from host-owned process metadata."""

    if sys.platform == "linux":
        return _linux_process_identity(pid)
    if sys.platform == "darwin":
        return _darwin_process_identity(pid)
    raise RuntimeError(f"Unsupported host platform: {sys.platform}")


def _effective_config(identity: ProcessIdentity, environment_variable: str) -> Path:
    configured = identity.environment.get(environment_variable)
    selected = Path(configured) if configured else Path("config.yaml")
    if not selected.is_absolute():
        selected = identity.cwd / selected
    return selected.resolve(strict=True)


def _expected_python_arguments(target: StopTarget) -> tuple[str, ...]:
    module_arguments = ("-m", target.module)
    if target.profile is None:
        return module_arguments
    return (*module_arguments, target.profile)


def _matches_target(identity: ProcessIdentity, target: StopTarget) -> bool:
    if (
        identity.uid != os.getuid()
        or identity.cwd != target.agents_dir
        or identity.executable != target.interpreter
    ):
        return False
    if not identity.argv or identity.argv[1:] != _expected_python_arguments(target):
        return False
    try:
        return (
            _effective_config(identity, _CONFIG_ENVIRONMENT_VARIABLE)
            == target.config_path
        )
    except (OSError, RuntimeError):
        return False


def select_owned_processes(
    identities: Iterable[ProcessIdentity], target: StopTarget
) -> tuple[ProcessIdentity, ...]:
    """Return only identities whose host facts exactly match the selected target."""

    return tuple(
        identity for identity in identities if _matches_target(identity, target)
    )


def _service_invocation_kind(
    identity: ProcessIdentity, target: ServiceStopTarget
) -> str | None:
    try:
        argv_interpreter = Path(identity.argv[0]).resolve(strict=True)
        launcher = Path(identity.argv[1]).resolve(strict=True)
    except (IndexError, OSError, RuntimeError):
        return None
    if (
        identity.uid != os.getuid()
        or identity.cwd != target.service_root
        or identity.executable != target.interpreter
        or argv_interpreter != target.interpreter
        or launcher != target.uvicorn_launcher
    ):
        return None
    common_tail = ("--host", "127.0.0.1", "--port", str(target.port))
    foreground = (target.entrypoint, "--factory", *common_tail)
    reload = (target.entrypoint, "--factory", "--reload", *common_tail)
    if identity.argv[2:] == foreground:
        kind = "foreground"
    elif identity.argv[2:] == reload:
        kind = "reload"
    else:
        return None
    try:
        if _effective_config(identity, "CONFIG_PATH") != target.config_path:
            return None
    except (OSError, RuntimeError):
        return None
    return kind


def _matches_service_family(
    identity: ProcessIdentity, target: ServiceStopTarget
) -> bool:
    if (
        identity.uid != os.getuid()
        or identity.cwd != target.service_root
        or identity.executable != target.interpreter
    ):
        return False
    try:
        return _effective_config(identity, "CONFIG_PATH") == target.config_path
    except (OSError, RuntimeError):
        return False


def _descends_from_listener_root(
    identity: ProcessIdentity,
    root: ProcessIdentity,
    identities_by_pid: Mapping[int, ProcessIdentity],
) -> bool:
    seen: set[int] = set()
    parent_pid = identity.ppid
    while parent_pid not in seen:
        if parent_pid == root.pid:
            return True
        seen.add(parent_pid)
        parent = identities_by_pid.get(parent_pid)
        if parent is None:
            return False
        parent_pid = parent.ppid
    return False


def select_service_processes(
    identities: Sequence[ProcessIdentity], target: ServiceStopTarget
) -> tuple[ProcessIdentity, tuple[ProcessIdentity, ...]]:
    """Prove one uvicorn root and every captured reload listener descendant."""

    if not identities:
        raise RuntimeError("No service listener identities were supplied")
    identities_by_pid = {identity.pid: identity for identity in identities}
    if len(identities_by_pid) != len(identities):
        raise RuntimeError("Service listener identity set is ambiguous")
    candidates = tuple(
        (identity, kind)
        for identity in identities
        if (kind := _service_invocation_kind(identity, target)) is not None
    )
    roots = tuple(
        (identity, kind)
        for identity, kind in candidates
        if not any(
            other.pid != identity.pid
            and _descends_from_listener_root(identity, other, identities_by_pid)
            for other, _other_kind in candidates
        )
    )
    if len(roots) != 1:
        raise RuntimeError(
            "Port listener is foreign or ambiguous; refusing service stop"
        )
    root, kind = roots[0]
    if kind == "foreground":
        if len(identities) != 1:
            raise RuntimeError(
                "Port listener is foreign or ambiguous; refusing service stop"
            )
        return root, (root,)

    for identity in identities:
        if identity == root:
            continue
        if not _matches_service_family(
            identity, target
        ) or not _descends_from_listener_root(identity, root, identities_by_pid):
            raise RuntimeError(
                "Port listener is foreign or ambiguous; refusing service stop"
            )
    return root, tuple(identities)


def _wait_for_captured_exit(
    identities: Sequence[ProcessIdentity],
    *,
    timeout_seconds: float,
    inspect_process: Callable[[int], ProcessIdentity],
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
) -> None:
    deadline = monotonic() + timeout_seconds
    remaining = {identity.pid: identity for identity in identities}
    while remaining:
        for pid, original in tuple(remaining.items()):
            try:
                current = inspect_process(pid)
            except ProcessAbsentError:
                del remaining[pid]
                continue
            if current.start_id != original.start_id:
                del remaining[pid]
                continue
            if current != original:
                raise RuntimeError(
                    f"PID {pid} metadata changed while still running; refusing success"
                )
        if not remaining:
            return
        now = monotonic()
        if now >= deadline:
            pending = ", ".join(str(pid) for pid in sorted(remaining))
            raise RuntimeError(
                f"Service process identity still running after timeout: {pending}"
            )
        sleep(min(0.1, deadline - now))


def stop_service(
    target: ServiceStopTarget,
    *,
    captured: Sequence[ProcessIdentity],
    timeout_seconds: float,
    inspect_process: Callable[[int], ProcessIdentity],
    send_signal: Callable[[int, signal.Signals], None],
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
) -> int | None:
    """Signal only a revalidated uvicorn root and confirm its captured family exits."""

    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("Service stop timeout must be finite and positive")
    root, family = select_service_processes(captured, target)
    live_family: list[ProcessIdentity] = []
    revalidation_order = (
        *tuple(identity for identity in family if identity != root),
        root,
    )
    for original in revalidation_order:
        try:
            current = inspect_process(original.pid)
        except ProcessAbsentError:
            continue
        if current.start_id != original.start_id:
            continue
        if current != original:
            raise RuntimeError(
                f"PID {original.pid} identity changed before signal; refusing stop"
            )
        live_family.append(original)
    if root not in live_family:
        if live_family:
            raise RuntimeError(
                "Service supervisor exited while a captured child remains"
            )
        return None
    send_signal(root.pid, signal.SIGTERM)
    _wait_for_captured_exit(
        live_family,
        timeout_seconds=timeout_seconds,
        inspect_process=inspect_process,
        monotonic=monotonic,
        sleep=sleep,
    )
    return root.pid


def _candidate_pids(module: str) -> tuple[int, ...]:
    try:
        listing = subprocess.run(
            ["/bin/ps", "-axo", "pid=,uid=,command="],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.SubprocessError as error:
        raise RuntimeError(f"Cannot enumerate host processes: {error}") from error
    selected: list[int] = []
    for line in listing.stdout.splitlines():
        fields = line.strip().split(maxsplit=2)
        if len(fields) != 3:
            continue
        pid_text, uid_text, command = fields
        if int(uid_text) == os.getuid() and module in command:
            selected.append(int(pid_text))
    return tuple(selected)


def _parse_listener_records(output: str, port: int) -> tuple[int, ...]:
    pids: list[int] = []
    descriptor_seen: dict[int, bool] = {}
    current_pid: int | None = None
    for line in output.splitlines():
        if not line:
            continue
        if line.startswith("p") and line[1:].isdigit():
            current_pid = int(line[1:])
            if current_pid not in pids:
                pids.append(current_pid)
                descriptor_seen[current_pid] = False
            continue
        if (
            line.startswith("f")
            and current_pid is not None
            and re.fullmatch(r"(?:\d+[A-Za-z]*|cwd|rtd|txt|mem)", line[1:]) is not None
        ):
            descriptor_seen[current_pid] = True
            continue
        raise RuntimeError(
            f"Host returned malformed listener facts for TCP port {port}"
        )
    if not pids or not all(descriptor_seen.values()):
        if not pids:
            raise RuntimeError(
                f"Host returned no listener identities for active TCP port {port} query"
            )
        missing = ", ".join(
            str(pid) for pid, seen in descriptor_seen.items() if not seen
        )
        raise RuntimeError(
            f"Host returned incomplete listener facts for TCP port {port}: {missing}"
        )
    return tuple(pids)


def _loopback_refusal(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.25)
            result = probe.connect_ex(("127.0.0.1", port))
    except OSError as error:
        raise RuntimeError(
            f"Cannot prove loopback refusal for TCP port {port}: {error}"
        ) from error
    if result == 0:
        raise RuntimeError(f"TCP port {port} still accepts connections")
    if result != errno.ECONNREFUSED:
        raise RuntimeError(
            f"TCP port {port} cannot prove loopback refusal: {os.strerror(result)}"
        )
    return True


def _lsof_listener_pids(port: int) -> tuple[int, ...]:
    """Return listener PIDs through lsof on hosts without Darwin netstat fields."""

    lsof = shutil.which("lsof")
    if lsof is None:
        raise RuntimeError("Cannot inspect service listener: lsof is unavailable")
    listing = subprocess.run(
        [lsof, "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fp"],
        capture_output=True,
        text=True,
        check=False,
    )
    diagnostics = listing.stderr.strip()
    if listing.returncode == 0:
        pids = _parse_listener_records(listing.stdout, port)
        if diagnostics:
            print(diagnostics, file=sys.stderr)
        return pids
    if listing.returncode == 1 and not listing.stdout.strip():
        if not diagnostics:
            return ()
        if _loopback_refusal(port):
            print(diagnostics, file=sys.stderr)
            return ()
    raise RuntimeError(f"Cannot inspect TCP port {port} listeners")


def _parse_darwin_tcp_listeners(output: str, port: int) -> tuple[int, ...]:
    """Parse matching LISTEN records from macOS netstat's verbose TCP table."""

    lines = output.splitlines()
    title_present = "Active Internet connections (including servers)" in lines
    header_present = any(
        line.startswith("Proto Recv-Q Send-Q") and "process:pid" in line
        for line in lines
    )
    if not title_present or not header_present:
        raise RuntimeError(
            f"Host returned unrecognized Darwin TCP table for port {port}"
        )
    row_prefix = re.compile(
        r"^(?P<protocol>tcp[46])\s+\d+\s+\d+\s+"
        r"(?P<local>\S+)\s+\S+\s+(?P<state>\S+)\s+"
    )
    row_suffix = re.compile(r"^\d+\s+\d+\s+\d+\s+\d+\s+.*:(?P<pid>\d+)\s+\S+")
    pids: list[int] = []
    for line in lines:
        prefix = row_prefix.match(line)
        if prefix is None:
            if (
                line.startswith(("tcp4", "tcp6"))
                and f".{port}" in line
                and "LISTEN" in line
            ):
                raise RuntimeError(
                    f"Host returned malformed Darwin TCP listener for port {port}"
                )
            continue
        if prefix.group("state") != "LISTEN":
            continue
        local_address = prefix.group("local")
        separator, local_port = local_address.rpartition(".")[1:]
        if not separator or not local_port.isdigit() or int(local_port) != port:
            continue
        suffix = row_suffix.match(line[prefix.end() :])
        if suffix is None:
            raise RuntimeError(
                f"Host returned malformed Darwin TCP listener for port {port}"
            )
        pid = int(suffix.group("pid"))
        if pid == 0:
            raise RuntimeError(f"Darwin TCP listener for port {port} reported PID 0")
        if pid not in pids:
            pids.append(pid)
    return tuple(pids)


def _darwin_listener_pids(port: int) -> tuple[int, ...]:
    listing = subprocess.run(
        ["/usr/sbin/netstat", "-anv", "-p", "tcp"],
        capture_output=True,
        text=True,
        check=False,
    )
    if listing.returncode != 0 or listing.stderr.strip():
        raise RuntimeError(f"Cannot inspect Darwin TCP socket table for port {port}")
    return _parse_darwin_tcp_listeners(listing.stdout, port)


def listener_pids(port: int) -> tuple[int, ...]:
    """Return the host-reported listener PIDs for one exact TCP port."""

    if sys.platform == "darwin":
        return _darwin_listener_pids(port)
    if sys.platform == "linux":
        return _lsof_listener_pids(port)
    raise RuntimeError(f"Unsupported host platform: {sys.platform}")


def capture_listener_processes(pids: Sequence[int]) -> tuple[ProcessIdentity, ...]:
    """Capture every initially reported listener or fail closed on a race."""

    identities: list[ProcessIdentity] = []
    for pid in pids:
        try:
            identities.append(inspect_process(pid))
        except ProcessAbsentError as error:
            raise RuntimeError(
                f"TCP listener PID {pid} changed during ownership inspection"
            ) from error
        except (OSError, RuntimeError) as error:
            raise RuntimeError(
                f"Cannot safely inspect listener PID {pid}: {error}"
            ) from error
    return tuple(identities)


def capture_service_processes(
    pids: Sequence[int],
    target: ServiceStopTarget,
    *,
    inspect_process: Callable[[int], ProcessIdentity],
) -> tuple[ProcessIdentity, ...]:
    """Capture listeners and recover only their exact direct reload supervisor."""

    listeners: list[ProcessIdentity] = []
    for pid in pids:
        try:
            listeners.append(inspect_process(pid))
        except ProcessAbsentError as error:
            raise RuntimeError(
                f"TCP listener PID {pid} changed during ownership inspection"
            ) from error
        except (OSError, RuntimeError) as error:
            raise RuntimeError(
                f"Cannot safely inspect listener PID {pid}: {error}"
            ) from error
    if not listeners:
        raise RuntimeError("No service listener identities were captured")
    if any(
        _service_invocation_kind(identity, target) is not None for identity in listeners
    ):
        return tuple(listeners)

    parent_pids: set[int] = set()
    for listener in listeners:
        if not _matches_service_family(listener, target):
            raise RuntimeError(
                f"Listener PID {listener.pid} does not match the selected service family"
            )
        parent_pids.add(listener.ppid)
    if len(parent_pids) != 1:
        raise RuntimeError("Reload listeners do not share one direct supervisor")
    parent_pid = next(iter(parent_pids))
    try:
        parent = inspect_process(parent_pid)
    except ProcessAbsentError as error:
        raise RuntimeError(
            "Reload supervisor exited during ownership inspection"
        ) from error
    except (OSError, RuntimeError) as error:
        raise RuntimeError(
            f"Cannot safely inspect reload supervisor: {error}"
        ) from error
    if parent.pid != parent_pid or _service_invocation_kind(parent, target) != "reload":
        raise RuntimeError("Listener parent is not the exact reload supervisor")
    if any(listener.ppid != parent.pid for listener in listeners):
        raise RuntimeError(
            "Reload listener parentage changed during ownership inspection"
        )
    return (parent, *listeners)


def discover_processes(target: StopTarget) -> tuple[ProcessIdentity, ...]:
    """Capture host identities for plausible same-user module candidates."""

    identities: list[ProcessIdentity] = []
    for pid in _candidate_pids(target.module):
        try:
            identities.append(inspect_process(pid))
        except ProcessAbsentError:
            continue
        except (OSError, RuntimeError) as error:
            raise RuntimeError(
                f"Cannot safely inspect candidate PID {pid}: {error}"
            ) from error
    return tuple(identities)


def stop_agents(
    target: StopTarget,
    *,
    captured: Sequence[ProcessIdentity],
    inspect_process: Callable[[int], ProcessIdentity],
    send_signal: Callable[[int, signal.Signals], None],
) -> tuple[int, ...]:
    """Revalidate each captured identity and signal only unchanged exact matches."""

    owned = select_owned_processes(captured, target)
    stopped: list[int] = []
    for original in owned:
        try:
            current = inspect_process(original.pid)
        except ProcessAbsentError:
            continue
        if current != original or not _matches_target(current, target):
            raise RuntimeError(
                f"PID {original.pid} identity changed before signal; refusing stop"
            )
        send_signal(original.pid, signal.SIGTERM)
        stopped.append(original.pid)
    return tuple(stopped)


def _parse_arguments(arguments: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--module", choices=("task_feeder", "math_worker"))
    parser.add_argument("--profile")
    parser.add_argument("--service-root", type=Path)
    parser.add_argument("--service-entrypoint")
    parser.add_argument("--port", type=int)
    parser.add_argument("--timeout-seconds", type=float)
    return parser.parse_args(arguments)


def main(arguments: Sequence[str]) -> int:
    options = _parse_arguments(arguments)
    try:
        service_mode = options.service_entrypoint is not None
        if service_mode:
            if (
                options.module is not None
                or options.profile is not None
                or options.service_root is None
                or options.port is None
                or options.timeout_seconds is None
            ):
                raise ValueError(
                    "Service stop arguments are incomplete or mixed with agent mode"
                )
            pids = listener_pids(options.port)
            if not pids:
                print(f"No process is listening on TCP port {options.port}")
                return 0
            service_target = ServiceStopTarget.create(
                repo_root=options.repo_root,
                service_root=options.service_root,
                config_path=options.config,
                entrypoint=options.service_entrypoint,
                port=options.port,
            )
            requested = stop_service(
                service_target,
                captured=capture_service_processes(
                    pids, service_target, inspect_process=inspect_process
                ),
                timeout_seconds=options.timeout_seconds,
                inspect_process=inspect_process,
                send_signal=os.kill,
                monotonic=time.monotonic,
                sleep=time.sleep,
            )
            if requested is None:
                print("Host-confirmed service process exited before signaling")
            else:
                print(f"Service process PID {requested} exited after SIGTERM")
            return 0
        if options.module is None:
            raise ValueError("Agent stop requires --module")
        if any(
            value is not None
            for value in (
                options.service_root,
                options.port,
                options.timeout_seconds,
            )
        ):
            raise ValueError("Agent stop cannot include service arguments")
        if options.module == "task_feeder":
            if options.profile is not None:
                raise ValueError("Task feeder does not accept a profile")
            target = StopTarget.task_feeder(options.repo_root, options.config)
        else:
            if options.profile is None:
                raise ValueError("Math worker requires an explicit profile")
            target = StopTarget.math_worker(
                options.repo_root, options.config, options.profile
            )
        captured = discover_processes(target)
        stopped = stop_agents(
            target,
            captured=captured,
            inspect_process=inspect_process,
            send_signal=os.kill,
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Refusing agent stop: {error}", file=sys.stderr)
        return 1

    if stopped:
        print(
            "Termination requested for agent PID(s): "
            + ", ".join(str(pid) for pid in stopped)
        )
    else:
        print("No host-confirmed matching agent process is running")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
