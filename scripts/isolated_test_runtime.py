#!/usr/bin/env python
"""Run live test phases against a disposable, process-owned service stack."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, TypedDict

import httpx
import yaml
from service_commons.maintenance_lock import (
    MaintenanceLockError,
    MaintenanceLockMode,
    hold_existing_maintenance_lock,
    hold_maintenance_lock,
)

if TYPE_CHECKING:
    from scripts.recovery_preparation import PreparedRecovery

SERVICE_ORDER = (
    "db-gateway",
    "identity",
    "central-bank",
    "task-board",
    "reputation",
    "court",
    "ui",
)
SERVICE_MODULES = {
    "db-gateway": "db_gateway_service.app:create_app",
    "identity": "identity_service.app:create_app",
    "central-bank": "central_bank_service.app:create_app",
    "task-board": "task_board_service.app:create_app",
    "reputation": "reputation_service.app:create_app",
    "court": "court_service.app:create_app",
    "ui": "ui_service.app:create_app",
}
PROCESS_INSPECTION_TIMEOUT_SECONDS = 2.0
LITELLM_LOCAL_MODEL_COST_MAP_ENV = "LITELLM_LOCAL_MODEL_COST_MAP"


class _ProcessRecord(TypedDict):
    pid: int
    pgid: int
    sid: int
    running: bool
    leader_returncode: int | None
    group_drained: bool
    cleanup_error: str | None


class _CommandRecord(_ProcessRecord):
    command: list[str]
    cwd: str


class _StartupInputs(TypedDict):
    config_path: str
    config_sha256: str
    agent_config_path: str
    agent_config_sha256: str
    process_cwd: str


class RuntimeErrorWithLog(RuntimeError):
    """A runtime failure whose service log contains the detailed cause."""


class ShutdownRequested(RuntimeError):
    """The supervisor received SIGINT or SIGTERM."""


class RuntimeCleanupError(RuntimeError):
    """One or more exact owned process groups could not be drained."""

    def __init__(self, failures: list[tuple[str, BaseException]]) -> None:
        self.failures = failures
        detail = "; ".join(f"{owner}: {error}" for owner, error in failures)
        super().__init__(f"Runtime cleanup failed: {detail}")


@dataclass
class _ReservedPort:
    port: int
    socket: socket.socket

    def release(self) -> None:
        self.socket.close()


@dataclass
class IsolatedRuntime:
    """Generated runtime state plus exact handles for processes started by one run."""

    repo_root: Path
    run_id: str
    run_dir: Path
    services: tuple[str, ...]
    ports: dict[str, int]
    config_paths: dict[str, Path]
    log_paths: dict[str, Path]
    agent_config_path: Path
    gateway_config_path: Path
    processes: dict[str, subprocess.Popen[bytes]] = field(default_factory=dict)
    process_records: dict[str, _ProcessRecord] = field(default_factory=dict)
    command_records: list[_CommandRecord] = field(default_factory=list)
    bootstrap_records: list[_CommandRecord] = field(default_factory=list)
    active_command: subprocess.Popen[bytes] | None = None
    active_command_group: tuple[int, int] | None = None
    active_command_record: _CommandRecord | None = None
    active_command_owner: str | None = None
    shutdown_signal: int | None = None
    _keys_provisioned: bool = False
    _stop_completed: bool = False
    _log_handles: dict[str, BinaryIO] = field(default_factory=dict)
    _reservations: dict[str, _ReservedPort] = field(default_factory=dict)
    _startup_inputs: dict[str, _StartupInputs] = field(default_factory=dict)
    _maintenance_identity: tuple[int, int] | None = None
    _recovery_status: dict[str, object] = field(default_factory=lambda: {"state": "not_prepared"})

    @classmethod
    def create(
        cls,
        *,
        repo_root: Path,
        artifacts_root: Path | None,
        services: tuple[str, ...] = SERVICE_ORDER,
    ) -> IsolatedRuntime:
        resolved_root = repo_root.resolve()
        unknown = set(services) - set(SERVICE_ORDER)
        if unknown:
            raise ValueError(f"Unknown services: {sorted(unknown)}")
        if not services:
            raise ValueError("At least one service is required")

        run_id = uuid.uuid4().hex
        if artifacts_root is None:
            run_dir = Path(tempfile.mkdtemp(prefix=f"agent-economy-{run_id}-"))
        else:
            artifacts_root.mkdir(parents=True, exist_ok=True)
            run_dir = Path(tempfile.mkdtemp(prefix=f"runtime-{run_id}-", dir=artifacts_root))
        run_dir = run_dir.resolve()
        for directory in ("configs", "data", "assets", "backups", "keys", "logs"):
            (run_dir / directory).mkdir()

        reservations = {service: _reserve_port() for service in services}
        ports = {service: reserved.port for service, reserved in reservations.items()}
        agent_config_path = run_dir / "configs" / "agents.yaml"
        config_paths = {service: run_dir / "configs" / f"{service}.yaml" for service in services}
        gateway_config_path = config_paths.get(
            "db-gateway", run_dir / "configs" / "gateway-trust.yaml"
        )
        log_paths = {service: run_dir / "logs" / f"{service}.log" for service in services}
        runtime = cls(
            repo_root=resolved_root,
            run_id=run_id,
            run_dir=run_dir,
            services=services,
            ports=ports,
            config_paths=config_paths,
            log_paths=log_paths,
            agent_config_path=agent_config_path,
            gateway_config_path=gateway_config_path,
            _reservations=reservations,
        )
        runtime._write_configs()
        runtime._write_manifest()
        return runtime

    def service_url(self, service: str) -> str:
        return f"http://127.0.0.1:{self.ports[service]}"

    def environment(self) -> dict[str, str]:
        urls = {
            "IDENTITY_URL": "identity",
            "BANK_URL": "central-bank",
            "TASK_BOARD_URL": "task-board",
            "REPUTATION_URL": "reputation",
            "COURT_URL": "court",
            "UI_URL": "ui",
            "DB_GATEWAY_URL": "db-gateway",
        }
        blocked_prefixes = (
            "AGENT__",
            "CENTRAL_BANK__",
            "COURT__",
            "DB_GATEWAY__",
            "IDENTITY__",
            "REPUTATION__",
            "TASK_BOARD__",
            "UI__",
        )
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"CONFIG_PATH", "VIRTUAL_ENV", LITELLM_LOCAL_MODEL_COST_MAP_ENV}
            and not key.startswith(blocked_prefixes)
        }
        env.update(
            {
                "AGENT_CONFIG_PATH": str(self.agent_config_path),
                "ISOLATED_RUNTIME_DIR": str(self.run_dir),
                "ISOLATED_RUNTIME_ID": self.run_id,
            }
        )
        for name, service in urls.items():
            if service in self.ports:
                env[name] = self.service_url(service)
        return env

    def start(self) -> None:
        """Start configured services in dependency order or fail with the owned log."""
        try:
            self._preflight()
            self._ensure_keys_provisioned()
            for service in SERVICE_ORDER:
                if service not in self.services:
                    continue
                self.start_service(service)
        except BaseException:
            self.stop()
            raise

    def _preflight(self) -> None:
        if sys.hexversion < 0x030C0000:
            raise RuntimeError("Isolated live tests require Python 3.12 or newer")
        if shutil.which("uv") is None:
            raise RuntimeError("Isolated live tests require the uv executable")
        if "court" in self.services and not self._court_uses_only_mock_judges():
            raise RuntimeError("Isolated live tests require deterministic mock Court judges")
        self._validate_maintenance_lock_paths()

    def _validate_maintenance_lock_paths(self) -> None:
        """Require every protected service to use the same run-owned lock path."""
        configured_paths: dict[str, Path] = {}
        for service in ("db-gateway", "task-board", "ui"):
            config_path = self.config_paths.get(service)
            if config_path is None:
                continue
            try:
                config = _load_yaml(config_path)
                recovery = _mapping_section(config, "recovery")
                configured_value = recovery.get("maintenance_lock_path")
                if not isinstance(configured_value, str) or not configured_value:
                    raise ValueError("Maintenance lock path must be a nonempty string")
            except (OSError, ValueError, yaml.YAMLError) as exc:
                log_path = self.log_paths[service]
                descriptor = os.open(
                    log_path,
                    os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW,
                    0o600,
                )
                with os.fdopen(descriptor, "a") as handle:
                    handle.write(
                        f"Maintenance preflight: invalid configuration ({type(exc).__name__})\n"
                    )
                raise RuntimeErrorWithLog(
                    f"{service} maintenance preflight failed; see {log_path}"
                ) from exc
            candidate = Path(configured_value)
            if not candidate.is_absolute():
                candidate = config_path.parent / candidate
            configured_paths[service] = _absolute_lexical_path(candidate)

        if not configured_paths:
            return
        expected = self.run_dir / "maintenance.lock"
        if any(path != expected for path in configured_paths.values()):
            rendered = ", ".join(
                f"{service}={path}" for service, path in sorted(configured_paths.items())
            )
            raise ValueError(
                f"Protected services must share the run-owned maintenance lock: {rendered}"
            )

    def service_environment(self, service: str) -> dict[str, str]:
        if service not in self.config_paths:
            raise ValueError(f"Service is not configured for this runtime: {service}")
        env = {**self.environment(), "CONFIG_PATH": str(self.config_paths[service])}
        if service == "court":
            if not self._court_uses_only_mock_judges():
                raise RuntimeError("Local LiteLLM metadata requires an all-mock Court profile")
            env[LITELLM_LOCAL_MODEL_COST_MAP_ENV] = "True"
        return env

    def _court_uses_only_mock_judges(self) -> bool:
        court_config = _load_yaml(self.config_paths["court"])
        judges = court_config.get("judges")
        configured = judges.get("judges") if isinstance(judges, dict) else None
        return (
            isinstance(configured, list)
            and bool(configured)
            and all(
                isinstance(judge, dict) and judge.get("provider") == "mock" for judge in configured
            )
        )

    def stop(self) -> None:
        """Terminate only process groups created by this runtime."""
        if (
            self._stop_completed
            and not self._reservations
            and not self.processes
            and self.active_command is None
            and not self._log_handles
        ):
            return
        self._stop_completed = False
        failures: list[tuple[str, BaseException]] = []
        for reservation in self._reservations.values():
            try:
                reservation.release()
            except BaseException as exc:
                failures.append((f"reserved-port-{reservation.port}", exc))
        self._reservations.clear()

        if self.active_command is not None:
            try:
                self._cleanup_active_command()
            except BaseException as exc:
                failures.append((self.active_command_owner or "owned-command", exc))

        for service in reversed(SERVICE_ORDER):
            try:
                self.stop_service(service, reserve_port=False)
            except BaseException as exc:
                failures.append((service, exc))
        failures.extend(self._close_stopped_log_handles())
        try:
            self._write_manifest()
        except BaseException as exc:
            failures.append(("runtime-manifest", exc))
        if failures:
            raise RuntimeCleanupError(failures)
        self._stop_completed = True

    def _close_stopped_log_handles(self) -> list[tuple[str, BaseException]]:
        failures: list[tuple[str, BaseException]] = []
        for service, handle in tuple(self._log_handles.items()):
            if service not in self.processes:
                try:
                    handle.close()
                except BaseException as exc:
                    failures.append((f"{service}-log", exc))
                else:
                    self._log_handles.pop(service, None)
        return failures

    def run_command(self, command: list[str], *, cwd: Path | None = None) -> int:
        """Run one test phase with this runtime's generated URLs and config."""
        return self._run_owned_command(
            command,
            cwd=cwd,
            records=self.command_records,
            owner="phase-command",
        )

    def _run_key_bootstrap_command(self, command: list[str], *, cwd: Path) -> int:
        """Run one key bootstrap under normal exact process-group ownership."""
        return self._run_owned_command(
            command,
            cwd=cwd,
            records=self.bootstrap_records,
            owner="key-bootstrap",
        )

    def _run_owned_command(
        self,
        command: list[str],
        *,
        cwd: Path | None,
        records: list[_CommandRecord],
        owner: str,
    ) -> int:
        command_cwd = (cwd if cwd is not None else self.repo_root).resolve(strict=True)
        executed_command = list(command)
        self._stop_completed = False
        process = subprocess.Popen(
            executed_command,
            cwd=str(command_cwd),
            env=self.environment(),
            start_new_session=True,
        )
        pgid, sid = _owned_group_identity(process)
        self.active_command = process
        self.active_command_group = (pgid, sid)
        record: _CommandRecord = {
            "command": executed_command,
            "cwd": str(command_cwd),
            "pid": process.pid,
            "pgid": pgid,
            "sid": sid,
            "running": True,
            "leader_returncode": None,
            "group_drained": False,
            "cleanup_error": None,
        }
        records.append(record)
        self.active_command_record = record
        self.active_command_owner = owner
        try:
            self._write_manifest()
            while process.poll() is None and self.shutdown_signal is None:
                time.sleep(0.05)
            captured_return_code = process.returncode
            if self.shutdown_signal is None and captured_return_code is None:
                raise RuntimeError("Phase command stopped without a return code")
        except BaseException:
            self._cleanup_active_command()
            raise
        self._cleanup_active_command()
        if self.shutdown_signal is not None:
            return 128 + self.shutdown_signal
        assert captured_return_code is not None
        return captured_return_code

    def _cleanup_active_command(self) -> None:
        process = self.active_command
        group = self.active_command_group
        record = self.active_command_record
        if process is None or group is None or record is None:
            return
        pgid, sid = group
        try:
            _drain_owned_process_group(process, pgid=pgid, sid=sid)
        except BaseException as exc:
            process.poll()
            record["leader_returncode"] = process.returncode
            record["group_drained"] = False
            record["cleanup_error"] = f"{type(exc).__name__}: {exc}"
            record["running"] = True
            self._write_manifest()
            raise
        process.poll()
        record["leader_returncode"] = process.returncode
        record["group_drained"] = True
        record["cleanup_error"] = None
        record["running"] = False
        self.active_command = None
        self.active_command_group = None
        self.active_command_record = None
        self.active_command_owner = None
        self._write_manifest()

    def request_shutdown(self, signum: int) -> None:
        if self.shutdown_signal is None:
            self.shutdown_signal = signum

    def provision(self) -> int:
        return self.run_command(
            ["uv", "run", "python", "-m", "treasury_provision_cli"],
            cwd=self.repo_root / "agents",
        )

    def start_service(self, service: str) -> None:
        """Start or restart one configured service while retaining exact ownership."""
        if service not in self.services:
            raise ValueError(f"Service is not configured for this runtime: {service}")
        current = self.processes.get(service)
        if current is not None and current.poll() is None:
            raise RuntimeError(f"Service is already running: {service}")
        self._stop_completed = False
        self._validate_maintenance_lock_paths()
        self._ensure_keys_provisioned()
        self._startup_inputs[service] = self._startup_input_snapshot(service)
        if service in {"db-gateway", "task-board", "ui"}:
            self._anchor_maintenance_identity()
        reserved = self._reservations.pop(service, None)
        if reserved is None:
            reserved = _reserve_port(self.ports[service])
        old_handle = self._log_handles.pop(service, None)
        if old_handle is not None:
            old_handle.close()
        log_handle = self.log_paths[service].open("ab")
        self._log_handles[service] = log_handle
        env = self.service_environment(service)
        process = subprocess.Popen(
            [
                "uv",
                "run",
                "uvicorn",
                SERVICE_MODULES[service],
                "--factory",
                "--host",
                "127.0.0.1",
                "--fd",
                str(reserved.socket.fileno()),
            ],
            cwd=str(self.repo_root / "services" / service),
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            pass_fds=(reserved.socket.fileno(),),
        )
        reserved.release()
        pgid, sid = _owned_group_identity(process)
        self.processes[service] = process
        self.process_records[service] = {
            "pid": process.pid,
            "pgid": pgid,
            "sid": sid,
            "running": True,
            "leader_returncode": None,
            "group_drained": False,
            "cleanup_error": None,
        }
        self._write_manifest()
        self._wait_for_health(service, process)
        self._assert_startup_inputs(service)
        if service in {"db-gateway", "task-board", "ui"}:
            self._assert_maintenance_exclusion()
        self._write_manifest()

    def _startup_input_snapshot(self, service: str) -> _StartupInputs:
        from scripts.recovery_inventory import read_recovery_source_file

        config_path = self.config_paths[service]
        service_bytes = read_recovery_source_file(config_path, label=f"{service} startup config")
        agent_bytes = read_recovery_source_file(
            self.agent_config_path, label="startup agent config"
        )
        return {
            "config_path": str(config_path),
            "config_sha256": hashlib.sha256(service_bytes).hexdigest(),
            "agent_config_path": str(self.agent_config_path),
            "agent_config_sha256": hashlib.sha256(agent_bytes).hexdigest(),
            "process_cwd": str(self.repo_root / "services" / service),
        }

    def _assert_startup_inputs(self, service: str) -> None:
        expected = self._startup_inputs.get(service)
        if expected is None or self._startup_input_snapshot(service) != expected:
            raise RuntimeErrorWithLog(
                f"{service} configuration changed after startup; see {self.log_paths[service]}"
            )

    def _anchor_maintenance_identity(self) -> None:
        path = self.run_dir / "maintenance.lock"
        if self._maintenance_identity is None:
            with hold_maintenance_lock(
                path, mode=MaintenanceLockMode.SHARED, blocking=False
            ) as held:
                self._maintenance_identity = (held.device, held.inode)
            return
        device, inode = self._maintenance_identity
        with hold_existing_maintenance_lock(
            path,
            mode=MaintenanceLockMode.SHARED,
            blocking=False,
            expected_device=device,
            expected_inode=inode,
        ):
            pass

    def _assert_maintenance_exclusion(self) -> None:
        """Prove global exclusion on the anchored lock, without identifying its holder.

        Each protected service's lock participation is verified separately by its
        lifespan tests. A busy shared lock alone cannot attribute ownership to a PID.
        """
        assert self._maintenance_identity is not None
        device, inode = self._maintenance_identity
        try:
            with hold_existing_maintenance_lock(
                self.run_dir / "maintenance.lock",
                mode=MaintenanceLockMode.EXCLUSIVE,
                blocking=False,
                expected_device=device,
                expected_inode=inode,
            ):
                raise RuntimeErrorWithLog(
                    f"Runtime maintenance lock is not held: {self.run_dir / 'maintenance.lock'}"
                )
        except MaintenanceLockError as exc:
            if exc.reason == "busy":
                return
            raise RuntimeErrorWithLog(
                f"Runtime maintenance identity changed: {self.run_dir / 'maintenance.lock'}"
            ) from exc

    def _validate_recovery_startup_inputs(self) -> None:
        if not self._startup_inputs or self._maintenance_identity is None:
            raise RuntimeError(
                "Recovery requires recorded startup configuration and maintenance identity"
            )
        for service in self._startup_inputs:
            self._assert_startup_inputs(service)
        self._anchor_maintenance_identity()

    def prepare_recovery_and_stop(
        self,
        *,
        registry_timeout_seconds: float,
        registry_max_attempts: int,
    ) -> PreparedRecovery:
        """Capture recovery evidence and stop this full runtime using its owned handles."""
        from scripts.recovery_preparation import prepare_recovery_and_stop

        return prepare_recovery_and_stop(
            self,
            registry_timeout_seconds=registry_timeout_seconds,
            registry_max_attempts=registry_max_attempts,
        )

    def _ensure_keys_provisioned(self) -> None:
        if self._keys_provisioned:
            return
        command = [
            "uv",
            "run",
            "--no-sync",
            "python",
            "-m",
            "key_provision_cli",
            "provision",
            "--config",
            str(self.agent_config_path),
            "--gateway-config",
            str(self.gateway_config_path),
        ]
        status = self._run_key_bootstrap_command(
            command,
            cwd=self.repo_root / "agents",
        )
        if self.shutdown_signal is not None:
            raise ShutdownRequested(f"received signal {self.shutdown_signal}")
        if status != 0:
            raise RuntimeError(f"Key provisioning failed with status {status}")
        self._keys_provisioned = True

    def stop_service(self, service: str, *, reserve_port: bool = True) -> None:
        """Stop one service if this runtime owns a live handle for it."""
        process = self.processes.get(service)
        if process is None:
            return
        record = self.process_records[service]
        try:
            _drain_owned_process_group(
                process,
                pgid=record["pgid"],
                sid=record["sid"],
            )
        except BaseException as exc:
            process.poll()
            record["leader_returncode"] = process.returncode
            record["group_drained"] = False
            record["cleanup_error"] = f"{type(exc).__name__}: {exc}"
            record["running"] = True
            self._write_manifest()
            raise
        process.poll()
        record["leader_returncode"] = process.returncode
        record["group_drained"] = True
        record["cleanup_error"] = None
        record["running"] = False
        self.processes.pop(service)
        handle = self._log_handles.pop(service, None)
        if handle is not None:
            handle.close()
        if reserve_port:
            self._reservations[service] = _reserve_port(self.ports[service])
        self._write_manifest()

    def _wait_for_health(self, service: str, process: subprocess.Popen[bytes]) -> None:
        deadline = time.monotonic() + 30.0
        health_url = f"{self.service_url(service)}/health"
        last_error = "no response"
        while time.monotonic() < deadline:
            if self.shutdown_signal is not None:
                raise ShutdownRequested(f"received signal {self.shutdown_signal}")
            return_code = process.poll()
            if return_code is not None:
                raise RuntimeErrorWithLog(
                    f"{service} exited with status {return_code}; log: {self.log_paths[service]}"
                )
            try:
                response = httpx.get(health_url, timeout=1.0)
                if (
                    response.status_code == 200
                    and response.json().get("status") == "ok"
                    and (service != "identity" or self._identity_registry_ready())
                ):
                    return
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
            except (httpx.HTTPError, ValueError) as exc:
                last_error = str(exc)
            time.sleep(0.1)
        raise RuntimeErrorWithLog(
            f"{service} readiness failed ({last_error}); log: {self.log_paths[service]}"
        )

    def _identity_registry_ready(self) -> bool:
        try:
            response = httpx.get(f"{self.service_url('identity')}/agents", timeout=1.0)
        except httpx.HTTPError:
            return False
        return response.status_code == 200 and isinstance(response.json().get("agents"), list)

    def _write_configs(self) -> None:
        roster_path = self.run_dir / "configs" / "roster.yaml"
        shutil.copyfile(self.repo_root / "agents" / "roster.yaml", roster_path)
        agent_config = _load_yaml(self.repo_root / "agents" / "config.yaml")
        agent_config["platform"] = {
            "identity_url": self._configured_url("identity"),
            "bank_url": self._configured_url("central-bank"),
            "task_board_url": self._configured_url("task-board"),
            "reputation_url": self._configured_url("reputation"),
            "court_url": self._configured_url("court"),
        }
        agent_config["data"] = {
            "keys_dir": str(self.run_dir / "keys"),
            "roster_path": str(roster_path),
        }
        runtime_state = _mapping_section(agent_config, "runtime_state")
        runtime_state["environment_id"] = self.run_id
        runtime_state["directory"] = str(self.run_dir / "data" / "agent-runtime")
        _mapping_section(agent_config, "task_feeder")["tasks_file"] = str(
            self.run_dir / "data" / "math_tasks.jsonl"
        )
        _dump_yaml(self.agent_config_path, agent_config)

        gateway_auth, gateway_audience, gateway_max_lifetime, trusted_callers = (
            self._runtime_gateway_auth()
        )

        source_configs = {
            service: _load_yaml(self.repo_root / "services" / service / "config.yaml")
            for service in self.services
        }
        _validate_deadline_actor_agreement(source_configs)

        for service in self.services:
            config = source_configs[service]
            _mapping_section(config, "server")["port"] = self.ports[service]
            _mapping_section(config, "logging")["directory"] = str(self.run_dir / "logs")
            if service in {"db-gateway", "task-board", "ui"}:
                recovery = _mapping_section(config, "recovery")
                recovery["maintenance_lock_path"] = str(self.run_dir / "maintenance.lock")
            if "db_gateway" in config:
                self._rewrite_gateway_signer(
                    service,
                    config,
                    gateway_audience=gateway_audience,
                    gateway_max_lifetime=gateway_max_lifetime,
                    trusted_callers=trusted_callers,
                )
            if "identity" in config:
                _mapping_section(config, "identity")["base_url"] = self._configured_url("identity")
            if "central_bank" in config:
                _mapping_section(config, "central_bank")["base_url"] = self._configured_url(
                    "central-bank"
                )
            if "platform" in config:
                _mapping_section(config, "platform")["agent_config_path"] = str(
                    self.agent_config_path
                )
            if service == "db-gateway":
                config["auth"] = gateway_auth
                _mapping_section(config, "board_authority")["platform_public_key_path"] = str(
                    self.run_dir / "keys" / "platform.pub"
                )
                _mapping_section(config, "recovery")["backup_root"] = str(self.run_dir / "backups")
                database = _mapping_section(config, "database")
                database["path"] = str(self.run_dir / "data" / "economy.db")
                database["schema_path"] = str(
                    self.repo_root / "docs" / "specifications" / "schema.sql"
                )
            elif service == "task-board":
                _mapping_section(config, "database")["path"] = str(
                    self.run_dir / "data" / "task-board.db"
                )
                _mapping_section(config, "assets")["storage_path"] = str(self.run_dir / "assets")
            elif service == "ui":
                _mapping_section(config, "database")["path"] = str(
                    self.run_dir / "data" / "economy.db"
                )
                _mapping_section(config, "frontend")["web_root"] = str(
                    self.repo_root / "services" / "ui" / "data" / "web"
                )
                _mapping_section(config, "user_agent")["agent_config_path"] = str(
                    self.agent_config_path
                )
                _mapping_section(config, "browser_security")["allowed_origins"] = [
                    f"http://127.0.0.1:{self.ports['ui']}",
                    f"http://localhost:{self.ports['ui']}",
                ]
            elif service == "court":
                judges = _mapping_section(config, "judges")
                judges["panel_size"] = 1
                judges["mock_worker_pct"] = 50
                judges["judges"] = [{"id": "judge-0", "provider": "mock", "model": "mock-judge"}]
            _dump_yaml(self.config_paths[service], config)

        if "db-gateway" not in self.services:
            _dump_yaml(self.gateway_config_path, {"auth": gateway_auth})

    def _runtime_gateway_auth(
        self,
    ) -> tuple[dict[str, object], str, int, dict[str, object]]:
        gateway_source = _load_yaml(self.repo_root / "services" / "db-gateway" / "config.yaml")
        gateway_auth = _mapping_section(gateway_source, "auth")
        gateway_audience = gateway_auth.get("audience")
        if not isinstance(gateway_audience, str) or not gateway_audience.strip():
            raise ValueError("Gateway auth.audience must be a non-blank string")
        gateway_max_lifetime = gateway_auth.get("max_lifetime_seconds")
        if (
            not isinstance(gateway_max_lifetime, int)
            or isinstance(gateway_max_lifetime, bool)
            or gateway_max_lifetime <= 0
        ):
            raise ValueError("Gateway auth.max_lifetime_seconds must be a positive integer")
        trusted_callers = _mapping_section(gateway_auth, "trusted_callers")
        if not trusted_callers:
            raise ValueError("Gateway auth.trusted_callers must not be empty")
        for kid, caller_value in trusted_callers.items():
            if kid in {"", ".", ".."} or "/" in kid or "\\" in kid:
                raise ValueError(f"Unsafe Gateway trusted caller KID: {kid!r}")
            if not isinstance(caller_value, dict) or not all(
                isinstance(key, str) for key in caller_value
            ):
                raise ValueError(f"Expected Gateway trusted caller mapping: {kid}")
            caller = {key: value for key, value in caller_value.items() if isinstance(key, str)}
            caller["public_key_path"] = str(self.run_dir / "keys" / f"{kid}.pub")
            trusted_callers[kid] = caller
        return gateway_auth, gateway_audience, gateway_max_lifetime, trusted_callers

    def _rewrite_gateway_signer(
        self,
        service: str,
        config: dict[str, object],
        *,
        gateway_audience: str,
        gateway_max_lifetime: int,
        trusted_callers: dict[str, object],
    ) -> None:
        db_gateway = _mapping_section(config, "db_gateway")
        db_gateway["url"] = self._configured_url("db-gateway")
        signing = _mapping_section(db_gateway, "signing")
        kid = signing.get("kid")
        if not isinstance(kid, str) or kid not in trusted_callers:
            raise ValueError(f"{service} db_gateway.signing.kid must name a trusted Gateway caller")
        configured_private_path = signing.get("private_key_path")
        if not isinstance(configured_private_path, str):
            raise ValueError(f"{service} db_gateway.signing.private_key_path must be a string")
        private_path = Path(configured_private_path)
        if private_path.suffix != ".key" or private_path.stem != kid:
            raise ValueError(
                f"{service} Gateway private key filename must match signing KID {kid!r}"
            )
        if signing.get("audience") != gateway_audience:
            raise ValueError(
                f"{service} db_gateway.signing.audience must match Gateway auth.audience"
            )
        token_ttl_seconds = signing.get("token_ttl_seconds")
        if (
            not isinstance(token_ttl_seconds, int)
            or isinstance(token_ttl_seconds, bool)
            or token_ttl_seconds <= 0
            or token_ttl_seconds > gateway_max_lifetime
        ):
            raise ValueError(
                f"{service} db_gateway.signing.token_ttl_seconds must be positive and "
                "must not exceed Gateway auth.max_lifetime_seconds"
            )
        signing["private_key_path"] = str(self.run_dir / "keys" / f"{kid}.key")

    def _configured_url(self, service: str) -> str:
        if service not in self.ports:
            return "http://127.0.0.1:1"
        return self.service_url(service)

    def _write_manifest(self) -> None:
        manifest = {
            "run_id": self.run_id,
            "run_dir": str(self.run_dir),
            "ports": self.ports,
            "config_paths": {name: str(path) for name, path in self.config_paths.items()},
            "agent_config_path": str(self.agent_config_path),
            "gateway_config_path": str(self.gateway_config_path),
            "log_paths": {name: str(path) for name, path in self.log_paths.items()},
            "processes": self.process_records,
            "commands": self.command_records,
            "bootstrap_commands": self.bootstrap_records,
            "startup_inputs": self._startup_inputs,
            "maintenance_identity": (
                {"device": self._maintenance_identity[0], "inode": self._maintenance_identity[1]}
                if self._maintenance_identity is not None
                else None
            ),
            "recovery": self._recovery_status,
        }
        target_path = self.run_dir / "runtime.json"
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".runtime-",
            suffix=".json.tmp",
            dir=self.run_dir,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w") as handle:
                handle.write(json.dumps(manifest, indent=2) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary_path.replace(target_path)
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise


def _reserve_port(port: int = 0) -> _ReservedPort:
    reserved_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    reserved_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    reserved_socket.bind(("127.0.0.1", port))
    reserved_socket.listen()
    return _ReservedPort(port=int(reserved_socket.getsockname()[1]), socket=reserved_socket)


def _absolute_lexical_path(path: Path) -> Path:
    """Normalize an absolute path without resolving any symlink ancestor."""
    candidate = path if path.is_absolute() else Path.cwd() / path
    normalized_parts: list[str] = []
    for part in candidate.parts:
        if part in {candidate.anchor, "", "."}:
            continue
        if part == "..":
            if normalized_parts:
                normalized_parts.pop()
            continue
        normalized_parts.append(part)
    return Path(candidate.anchor, *normalized_parts)


def _owned_group_identity(process: subprocess.Popen[bytes]) -> tuple[int, int]:
    pgid = os.getpgid(process.pid)
    sid = os.getsid(process.pid)
    if pgid != process.pid or sid != process.pid:
        raise RuntimeError(
            f"Owned process {process.pid} did not create the expected private session/group"
        )
    return pgid, sid


def _drain_owned_process_group(
    process: subprocess.Popen[bytes],
    *,
    pgid: int,
    sid: int,
) -> None:
    if pgid != process.pid or sid != process.pid:
        raise RuntimeError(f"Refusing to signal unproven process group {pgid}")
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pgid, signal.SIGTERM)
    if not _wait_for_owned_group_exit(process, pgid=pgid, timeout=1.0):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pgid, signal.SIGKILL)
        if not _wait_for_owned_group_exit(process, pgid=pgid, timeout=5.0):
            raise RuntimeError(f"Owned process group {pgid} survived SIGKILL")
    if process.poll() is None:
        process.wait(timeout=5.0)


def _wait_for_owned_group_exit(
    process: subprocess.Popen[bytes],
    *,
    pgid: int,
    timeout: float,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        process.poll()
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        except PermissionError as exc:
            if _owned_group_absent_after_permission_error(process, pgid=pgid, error=exc):
                return True
        time.sleep(0.05)
    process.poll()
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    except PermissionError as exc:
        if _owned_group_absent_after_permission_error(process, pgid=pgid, error=exc):
            return True
    return False


def _owned_group_absent_after_permission_error(
    process: subprocess.Popen[bytes],
    *,
    pgid: int,
    error: PermissionError,
) -> bool:
    members, inspection_error = _owned_group_members(pgid)
    if inspection_error is None and not members:
        return True
    if (
        inspection_error is None
        and any(
            member.startswith(f"pid={process.pid} ")
            and f" pgid={pgid} " in member
            and " stat=Z" in member
            for member in members
        )
        and process.poll() is not None
    ):
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        except PermissionError as retry_error:
            members, inspection_error = _owned_group_members(pgid)
            if inspection_error is None and not members:
                return True
            error = retry_error
        else:
            return False
    snapshot = inspection_error or " | ".join(members)
    raise PermissionError(
        error.errno,
        f"{error.strerror}; exact PGID {pgid} snapshot: {snapshot}",
    ) from error


def _owned_group_members(pgid: int) -> tuple[list[str], str | None]:
    try:
        completed = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,pgid=,sess=,stat="],
            capture_output=True,
            text=True,
            check=False,
            timeout=PROCESS_INSPECTION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return [], f"ps timed out after {PROCESS_INSPECTION_TIMEOUT_SECONDS} seconds"
    except OSError as exc:
        return [], f"ps could not start: {type(exc).__name__}"
    if completed.returncode != 0:
        return [], f"ps failed with status {completed.returncode}"
    members: list[str] = []
    for line_number, line in enumerate(completed.stdout.splitlines(), start=1):
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 5 or not all(field.isdigit() for field in fields[:4]):
            return [], f"ps returned malformed row {line_number}"
        if int(fields[2]) == pgid:
            members.append(
                " ".join(
                    (
                        f"pid={fields[0]}",
                        f"ppid={fields[1]}",
                        f"pgid={fields[2]}",
                        f"sid={fields[3]}",
                        f"stat={fields[4]}",
                    )
                )
            )
    return members, None


def _load_yaml(path: Path) -> dict[str, object]:
    content = yaml.safe_load(path.read_text())
    if not isinstance(content, dict):
        raise ValueError(f"Expected YAML mapping in {path}")
    if not all(isinstance(key, str) for key in content):
        raise ValueError(f"Expected string YAML keys in {path}")
    return {key: value for key, value in content.items() if isinstance(key, str)}


def _mapping_section(config: dict[str, object], name: str) -> dict[str, object]:
    value = config.get(name)
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"Expected YAML mapping section: {name}")
    normalized = {key: item for key, item in value.items() if isinstance(key, str)}
    config[name] = normalized
    return normalized


def _canonical_actor(config: dict[str, object], section: str, field: str, label: str) -> str:
    value = _mapping_section(config, section).get(field)
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{label} must be a canonical non-blank string")
    return value


def _validate_deadline_actor_agreement(
    source_configs: dict[str, dict[str, object]],
) -> None:
    if "db-gateway" not in source_configs or "task-board" not in source_configs:
        return
    gateway_actor = _canonical_actor(
        source_configs["db-gateway"],
        "board_authority",
        "deadline_actor_id",
        "Gateway board_authority.deadline_actor_id",
    )
    task_board_actor = _canonical_actor(
        source_configs["task-board"],
        "deadline_evaluation",
        "actor_id",
        "Task Board deadline_evaluation.actor_id",
    )
    if gateway_actor != task_board_actor:
        raise ValueError(
            "Gateway board_authority.deadline_actor_id must match "
            "Task Board deadline_evaluation.actor_id"
        )


def _dump_yaml(path: Path, content: dict[str, object]) -> None:
    path.write_text(yaml.safe_dump(content, sort_keys=False))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts-root", type=Path)
    parser.add_argument("--provision", action="store_true")
    parser.add_argument("--services", default=",".join(SERVICE_ORDER))
    parser.add_argument(
        "--prepare-recovery",
        action="store_true",
        help="Stop the full stack and publish recovery inventory after the command exits.",
    )
    parser.add_argument("--registry-timeout-seconds", type=float)
    parser.add_argument("--registry-max-attempts", type=int)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    _validate_recovery_args(parser, args)
    return args


def _validate_recovery_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    limits_supplied = (
        args.registry_timeout_seconds is not None or args.registry_max_attempts is not None
    )
    if not args.prepare_recovery:
        if limits_supplied:
            parser.error("Registry limits require --prepare-recovery")
        return
    if (
        args.registry_timeout_seconds is None
        or not math.isfinite(args.registry_timeout_seconds)
        or args.registry_timeout_seconds <= 0
        or args.registry_max_attempts is None
        or args.registry_max_attempts <= 0
    ):
        parser.error(
            "--prepare-recovery requires positive --registry-timeout-seconds and "
            "--registry-max-attempts"
        )
    if tuple(args.services.split(",")) != SERVICE_ORDER:
        parser.error("Recovery preparation requires all seven services in dependency order")


def _prepare_cli_recovery(runtime: IsolatedRuntime, args: argparse.Namespace) -> bool:
    from scripts.recovery_preparation import RecoveryPreparationError
    from scripts.recovery_registry import RegistrySnapshotError
    from service_auth import KeyMaterialError

    try:
        prepared = runtime.prepare_recovery_and_stop(
            registry_timeout_seconds=args.registry_timeout_seconds,
            registry_max_attempts=args.registry_max_attempts,
        )
    except (
        RecoveryPreparationError,
        RegistrySnapshotError,
        RuntimeCleanupError,
        RuntimeErrorWithLog,
        KeyMaterialError,
        MaintenanceLockError,
        OSError,
        ValueError,
    ) as exc:
        reason = getattr(exc, "reason", type(exc).__name__)
        print(
            f"Recovery preparation failed ({reason}); artifacts: {runtime.run_dir}", file=sys.stderr
        )
        return False
    print(
        json.dumps(
            {
                "recovery_inventory": str(prepared.inventory_path),
                "inventory_sha256": prepared.inventory_sha256,
                "runtime_manifest": str(prepared.runtime_manifest_path),
            }
        ),
        flush=True,
    )
    return True


def _run_cli_work(runtime: IsolatedRuntime, command: list[str], args: argparse.Namespace) -> int:
    if args.provision:
        provision_status = runtime.provision()
        if provision_status != 0:
            print(
                f"Treasury provisioning failed with status {provision_status}; "
                f"artifacts: {runtime.run_dir}",
                file=sys.stderr,
            )
            return provision_status
    return runtime.run_command(command)


def main() -> int:
    args = _parse_args()
    command = args.command[1:] if args.command and args.command[0] == "--" else args.command
    if not command:
        print("isolated_test_runtime.py requires a command after '--'", file=sys.stderr)
        return 2
    repo_root = Path(__file__).resolve().parents[1]
    runtime = IsolatedRuntime.create(
        repo_root=repo_root,
        artifacts_root=args.artifacts_root,
        services=tuple(args.services.split(",")),
    )
    print(f"Isolated runtime: {runtime.run_dir}", flush=True)

    def _record_shutdown_signal(signum: int, frame: object) -> None:
        del frame
        runtime.request_shutdown(signum)

    signal.signal(signal.SIGINT, _record_shutdown_signal)
    signal.signal(signal.SIGTERM, _record_shutdown_signal)
    exit_code = 1
    cleanup_failed = False
    try:
        runtime.start()
        exit_code = _run_cli_work(runtime, command, args)
        if args.prepare_recovery:
            preparation_succeeded = _prepare_cli_recovery(runtime, args)
            if not preparation_succeeded and exit_code == 0:
                exit_code = 1
    except RuntimeErrorWithLog as exc:
        print(str(exc), file=sys.stderr)
        exit_code = 1
    except ShutdownRequested:
        assert runtime.shutdown_signal is not None
        exit_code = 128 + runtime.shutdown_signal
    except RuntimeCleanupError as exc:
        print(str(exc), file=sys.stderr)
        cleanup_failed = True
    finally:
        try:
            runtime.stop()
        except RuntimeCleanupError as exc:
            print(str(exc), file=sys.stderr)
            cleanup_failed = True
    if runtime.shutdown_signal is not None:
        return 128 + runtime.shutdown_signal
    if cleanup_failed:
        return 1
    return exit_code


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(main())
