"""MP-08 real-process restart proof for a native cancellation before Bank dispatch."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from builtins import BaseExceptionGroup
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from scripts.isolated_test_runtime import (
    IsolatedRuntime,
    _drain_owned_process_group,
    _release_owned_launch,
    _start_owned_launch,
)
from service_auth.gateway import GatewayRequestSigner, GatewaySignerConfig

from db_gateway_service import migration, recovery
from db_gateway_service.financial_requests import canonical_request
from db_gateway_service.money_audit import audit_money
from db_gateway_service.publication_audit import audit_publications
from db_gateway_service.receipt_audit import audit_receipt_ownership
from db_gateway_service.task_contract_audit import audit_task_contracts
from db_gateway_service.task_operation_requests import parse_task_operation_request
from db_gateway_service.task_time import parse_utc_text_to_epoch_us

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PROXY = Path(__file__).with_name("mp08_financial_effect_fault_server.py")
_ACTOR = Path(__file__).with_name("mp08_financial_history.py")
_CLOCK_GATEWAY = Path(__file__).with_name("mp08_gateway_clock_server.py")
_SERVICES = (
    "db-gateway",
    "identity",
    "central-bank",
    "task-board",
    "reputation",
    "court",
)
_TIMEOUT_SECONDS = 30.0
_REWARD = 100


@dataclass(frozen=True, slots=True)
class _Cut:
    name: str
    occurrence: int
    phase: str
    proxy_kind: str


_CUTS = (
    _Cut("after_enqueue_before_dispatch", 1, "before_forward", "gateway_finalize"),
    _Cut("after_bank_commit_before_ack", 1, "after_response", "bank"),
    _Cut("receipt_before_projection", 2, "before_forward", "gateway_finalize"),
    _Cut("after_final_commit_before_caller_ack", 2, "after_response", "gateway_finalize"),
)


@dataclass(slots=True)
class _OwnedChild:
    process: subprocess.Popen[bytes]
    pgid: int
    sid: int
    drained: bool = False


@dataclass(frozen=True, slots=True)
class _NativeV4Source:
    source: Any
    operation_id: str


def _terminate_owned(child: _OwnedChild) -> None:
    if child.drained:
        return
    _drain_owned_process_group(child.process, pgid=child.pgid, sid=child.sid)
    child.drained = True


def _child_evidence(child: _OwnedChild | None) -> dict[str, object] | None:
    if child is None:
        return None
    return {
        "pid": child.process.pid,
        "pgid": child.pgid,
        "sid": child.sid,
        "leader_returncode": child.process.poll(),
        "drained": child.drained,
        "pid_absent": not _pid_exists(child.process.pid),
    }


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _kill_runtime_board(runtime: IsolatedRuntime) -> dict[str, int]:
    process = runtime.processes["task-board"]
    record = runtime.process_records["task-board"]
    identity = {name: int(record[name]) for name in ("pid", "pgid", "sid")}
    assert process.pid == identity["pid"]
    assert os.getpgid(process.pid) == identity["pgid"]
    assert os.getsid(process.pid) == identity["sid"]
    os.killpg(identity["pgid"], signal.SIGSTOP)
    kill_errors: list[BaseException] = []

    def kill_group() -> None:
        try:
            os.killpg(identity["pgid"], signal.SIGKILL)
        except BaseException as exc:
            kill_errors.append(exc)

    killer = threading.Timer(0.05, kill_group)
    killer.start()
    runtime.stop_service("task-board")
    killer.join(timeout=1)
    assert not kill_errors
    assert not _pid_exists(identity["pid"])
    return identity


def _capture_cleanup(failures: list[BaseException], callback: Any, *args: object) -> None:
    try:
        callback(*args)
    except BaseException as exc:
        failures.append(exc)


def _launch(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    log_path: Path,
    pass_fds: tuple[int, ...],
    ownership: contextlib.ExitStack,
    cleanup_failures: list[BaseException],
) -> _OwnedChild:
    with log_path.open("ab") as log:
        process, pgid, sid, descriptor = _start_owned_launch(
            command,
            cwd=str(cwd),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            pass_fds=pass_fds,
        )
        child = _OwnedChild(process, pgid, sid)
        ownership.callback(_capture_cleanup, cleanup_failures, _terminate_owned, child)
        _release_owned_launch(descriptor)
    return child


def _listener(port: int) -> socket.socket:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen(128)
    listener.set_inheritable(True)
    return listener


def _wait_json(path: Path, child: _OwnedChild) -> dict[str, Any]:
    deadline = time.monotonic() + _TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if path.exists():
            value = json.loads(path.read_text(encoding="utf-8"))
            assert isinstance(value, dict)
            return value
        if child.process.poll() is not None:
            raise RuntimeError(f"Owned child exited before evidence: {path}")
        time.sleep(0.05)
    raise TimeoutError(f"Timed out waiting for evidence: {path}")


def _wait_listener(port: int, child: _OwnedChild) -> None:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if child.process.poll() is not None:
            raise RuntimeError("Proxy exited before accepting connections")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.02)
    raise TimeoutError("Proxy did not accept connections")


def _start_clock_gateway(
    runtime: IsolatedRuntime,
    clock_path: Path,
    *,
    ownership: contextlib.ExitStack,
    cleanup_failures: list[BaseException],
) -> _OwnedChild:
    runtime._ensure_keys_provisioned()  # noqa: SLF001 - exact test supervisor ownership
    reservation = runtime._reservations.pop("db-gateway")  # noqa: SLF001
    ownership.callback(reservation.release)
    child = _launch(
        [
            "uv",
            "run",
            "--no-sync",
            "python",
            str(_CLOCK_GATEWAY),
            "--clock",
            str(clock_path),
            "--fd",
            str(reservation.socket.fileno()),
        ],
        cwd=_REPO_ROOT / "services/db-gateway",
        env=runtime.service_environment("db-gateway"),
        log_path=runtime.run_dir / "logs/mp08-clock-gateway.log",
        pass_fds=(reservation.socket.fileno(),),
        ownership=ownership,
        cleanup_failures=cleanup_failures,
    )
    reservation.release()
    _wait_listener(runtime.ports["db-gateway"], child)
    return child


def _wait_gate_or_actor_error(
    gate_path: Path,
    proxy: _OwnedChild,
    actor_result_path: Path,
) -> dict[str, Any]:
    deadline = time.monotonic() + _TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if gate_path.exists():
            return _wait_json(gate_path, proxy)
        if actor_result_path.exists():
            result = json.loads(actor_result_path.read_text(encoding="utf-8"))
            raise RuntimeError(f"SDK actor failed before fault gate: {result}")
        if proxy.process.poll() is not None:
            raise RuntimeError("Fault proxy exited before gate")
        time.sleep(0.05)
    raise TimeoutError(f"Timed out waiting for evidence: {gate_path}")


def _rows(
    database: Path,
    sql: str,
    parameters: tuple[object, ...] = (),
) -> tuple[tuple[object, ...], ...]:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        return tuple(tuple(row) for row in connection.execute(sql, parameters).fetchall())
    finally:
        connection.close()


def _financial_total(database: Path) -> int:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        assert audit_money(connection).financially_clean
        assert audit_receipt_ownership(connection).ownership_clean
        balances = int(
            connection.execute("SELECT COALESCE(SUM(balance),0) FROM bank_accounts").fetchone()[0]
        )
        locked = int(
            connection.execute(
                "SELECT COALESCE(SUM(amount),0) FROM bank_escrow WHERE status='locked'"
            ).fetchone()[0]
        )
        return balances + locked
    finally:
        connection.close()


def _global_counts(database: Path) -> dict[str, int]:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        return {
            table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in (
                "bank_operation_receipts",
                "bank_operation_legs",
                "bank_transactions",
                "events",
            )
        }
    finally:
        connection.close()


def _immutable_snapshot(
    database: Path,
    *,
    task_id: str,
    escrow_id: str,
) -> dict[str, tuple[tuple[object, ...], ...]]:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        result: dict[str, tuple[tuple[object, ...], ...]] = {}
        for table, order, where, parameters in (
            ("bank_operation_receipts", "operation_id", "", ()),
            ("bank_operation_legs", "operation_id,leg_index", "", ()),
            ("bank_transactions", "tx_id", "", ()),
            ("events", "event_id", "", ()),
            ("board_tasks", "task_id", "WHERE task_id!=?", (task_id,)),
            ("bank_escrow", "escrow_id", "WHERE escrow_id!=?", (escrow_id,)),
            ("board_task_operations", "operation_id", "WHERE task_id!=?", (task_id,)),
        ):
            result[table] = tuple(
                tuple(row)
                for row in connection.execute(
                    f'SELECT * FROM "{table}" {where} ORDER BY {order}',  # nosec B608
                    parameters,
                ).fetchall()
            )
        if (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='board_task_effect_deliveries'"
            ).fetchone()
            is not None
        ):
            result["board_task_effect_deliveries"] = tuple(
                tuple(row)
                for row in connection.execute(
                    "SELECT * FROM board_task_effect_deliveries WHERE task_id!=? "
                    "ORDER BY effect_operation_id",
                    (task_id,),
                ).fetchall()
            )
        return result
    finally:
        connection.close()


def _assert_preexisting_rows_unchanged(
    before: dict[str, tuple[tuple[object, ...], ...]],
    after: dict[str, tuple[tuple[object, ...], ...]],
) -> None:
    strict_tables = {
        "board_tasks",
        "bank_escrow",
        "board_task_operations",
        "board_task_effect_deliveries",
    }
    key_indexes = {
        "bank_operation_receipts": (0,),
        "bank_operation_legs": (0, 1),
        "bank_transactions": (0,),
        "events": (0,),
    }
    for table, rows in before.items():
        if table in strict_tables:
            assert after[table] == rows, table
            continue
        indexes = key_indexes[table]
        old = {tuple(row[index] for index in indexes): row for row in rows}
        current = {
            tuple(row[index] for index in indexes): row
            for row in after[table]
            if tuple(row[index] for index in indexes) in old
        }
        assert current == old, table


def _wait_for_completion(
    database: Path,
    operation_id: str,
    *,
    opportunity_seconds: float,
) -> bool:
    deadline = time.monotonic() + opportunity_seconds
    while time.monotonic() < deadline:
        if _rows(
            database,
            "SELECT operation_state,effect_state FROM board_task_operations WHERE operation_id=?",
            (operation_id,),
        ) == (("completed", "succeeded"),):
            return True
        time.sleep(0.05)
    return False


def _copy_sqlite(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with (
        contextlib.closing(sqlite3.connect(source)) as source_connection,
        contextlib.closing(sqlite3.connect(target)) as target_connection,
    ):
        source_connection.backup(target_connection)


def _write_clock(path: Path, clock_us: int) -> None:
    staged = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    staged.write_text(f"{clock_us}\n", encoding="ascii")
    os.replace(staged, path)


def _prepare_native_claimed_v4(root: Path) -> _NativeV4Source:
    module_name = f"mp08_process_legacy_history_{uuid.uuid4().hex}"
    module_path = _REPO_ROOT / "services/db-gateway/tests/fakes/gateway_mp08_legacy_history.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    source = module.prepare_legacy_worker_settlement_v4(
        root,
        kind="pending",
        caller_kid="gateway-task-board",
    )
    operation_id = f"op-{uuid.uuid4()}"
    connection = sqlite3.connect(source.database)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "DELETE FROM board_legacy_effect_observations WHERE task_id=?",
            (source.task_id,),
        )
        connection.execute(
            "UPDATE board_tasks SET status='submitted',approved_at=NULL,escrow_pending=0 "
            "WHERE task_id=?",
            (source.task_id,),
        )
        connection.commit()
        request = parse_task_operation_request(
            {
                "protocol_version": 1,
                "operation_id": operation_id,
                "task_id": source.task_id,
                "command": "approve_task",
                "expected_status": "submitted",
                "expected_lifecycle_version": 0,
                "actor_kind": "agent",
                "actor_id": source.poster_id,
                "arguments": {
                    "poster_id": source.poster_id,
                    "worker_id": source.worker_id,
                    "escrow_id": source.escrow_id,
                    "reward": source.reward,
                    "source_reference": f"task:{source.task_id}:approved",
                },
            }
        )
        financial = canonical_request(
            "escrow_release",
            {
                "escrow_id": source.escrow_id,
                "recipient_account_id": source.worker_id,
                "source_reference": f"task:{source.task_id}:approved",
            },
        )
        intent = json.dumps(
            {
                "financial_request": json.loads(financial.canonical_json),
                "financial_request_sha256": financial.request_sha256,
                "expected_escrow": {
                    "escrow_id": source.escrow_id,
                    "task_id": source.task_id,
                    "payer_account_id": source.poster_id,
                    "amount": source.reward,
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        connection.execute(
            "INSERT INTO board_task_operations "
            "(operation_id,operation_contract_version,task_id,command,canonical_request_json,"
            "request_sha256,caller_kid,actor_kind,actor_id,source_status,"
            "source_lifecycle_version,target_status,target_lifecycle_version,operation_state,"
            "effect_kind,effect_state,effect_operation_id,effect_result_reference,"
            "effect_intent_json,claimed_at_us,completed_at_us,event_id,result_json,error_json) "
            "VALUES (?,1,?,'approve_task',?,?,'gateway-task-board','agent',?,'submitted',0,"
            "'approved',1,'claimed','bank_release','pending',?,NULL,?,?,NULL,NULL,NULL,NULL)",
            (
                operation_id,
                source.task_id,
                request.canonical_json,
                request.request_sha256,
                source.poster_id,
                operation_id,
                intent,
                parse_utc_text_to_epoch_us("2099-01-01T00:00:03Z"),
            ),
        )
        connection.commit()
        assert audit_money(connection).financially_clean
        assert audit_receipt_ownership(connection).ownership_clean
        assert audit_task_contracts(connection).contracts_clean
        assert audit_publications(connection).publications_clean
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA journal_mode=DELETE").fetchone() == ("delete",)
    finally:
        connection.close()
    assert not Path(f"{source.database}-wal").exists()
    assert not Path(f"{source.database}-shm").exists()
    return _NativeV4Source(source, operation_id)


def _signed_gateway_post(
    runtime: IsolatedRuntime,
    *,
    path: str,
    body: dict[str, object],
) -> tuple[int, dict[str, Any]]:
    signer = GatewayRequestSigner(
        GatewaySignerConfig(
            kid="gateway-task-board",
            private_key_path=runtime.run_dir / "keys/gateway-task-board.key",
            audience="db-gateway",
            token_ttl_seconds=30,
        ),
        config_dir=runtime.run_dir,
    )
    request = httpx.Request("POST", runtime.service_url("db-gateway") + path, json=body)
    signer.sign_request(request, action="gateway.board.write")
    with httpx.Client(timeout=5, trust_env=False) as client:
        response = client.send(request)
    value = response.json()
    assert isinstance(value, dict)
    return response.status_code, value


def _hold_recovery_finalize(
    runtime: IsolatedRuntime,
    operation_id: str,
    ownership: contextlib.ExitStack,
    cleanup_failures: list[BaseException],
    upstream_url: str,
) -> tuple[_OwnedChild, str, Path]:
    """Prevent concurrent receipt adoption until the owned Board is killed."""
    listener = _listener(0)
    ownership.callback(listener.close)
    port = int(listener.getsockname()[1])
    gateway = yaml.safe_load(runtime.config_paths["db-gateway"].read_text())
    auth = gateway["auth"]
    gate_path = runtime.run_dir / "mp08-recovery-finalize-gate.json"
    config_path = runtime.run_dir / "configs/mp08-recovery-finalize-proxy.json"
    config_path.write_text(
        json.dumps(
            {
                "upstream_url": upstream_url,
                "timeout_seconds": 10,
                "release_poll_seconds": 0.02,
                "release_path": str(runtime.run_dir / "mp08-recovery-finalize-release"),
                "gate": {
                    "method": "POST",
                    "path": f"/board/task-effect-deliveries/{operation_id}/finalize",
                    "request_sha256": None,
                    "occurrence": 1,
                    "phase": "before_forward",
                    "expected_status": 200,
                    "expected_response": {},
                    "artifact_path": str(gate_path),
                },
                "authentication": {
                    "mode": "gateway_request",
                    "public_key_path": str(runtime.run_dir / "keys/gateway-task-board.pub"),
                    "expected_kid": "gateway-task-board",
                    "audience": auth["audience"],
                    "action": "gateway.board.write",
                    "max_lifetime_seconds": auth["max_lifetime_seconds"],
                    "clock_skew_seconds": auth["clock_skew_seconds"],
                    "expected_payload": {},
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    proxy = _launch(
        [
            "uv",
            "run",
            "--project",
            "services/db-gateway",
            "--no-sync",
            "python",
            str(_PROXY),
            "--config",
            str(config_path),
            "--fd",
            str(listener.fileno()),
        ],
        cwd=_REPO_ROOT,
        env=runtime.environment(),
        log_path=runtime.run_dir / "logs/mp08-recovery-finalize-proxy.log",
        pass_fds=(listener.fileno(),),
        ownership=ownership,
        cleanup_failures=cleanup_failures,
    )
    listener.close()
    _wait_listener(port, proxy)
    return proxy, f"http://127.0.0.1:{port}", gate_path


@pytest.mark.parametrize("action", ["cancel", "approve", "ruling"])
@pytest.mark.parametrize("cut", _CUTS, ids=lambda cut: cut.name)
def test_release_restart_cut_converges_once(  # noqa: PLR0915
    tmp_path: Path,
    action: str,
    cut: _Cut,
) -> None:
    operation_id = f"op-{uuid.uuid4()}"
    runtime = IsolatedRuntime.create(
        repo_root=_REPO_ROOT,
        artifacts_root=tmp_path / "runtime",
        services=_SERVICES,
    )
    ownership = contextlib.ExitStack()
    cleanup_failures: list[BaseException] = []
    ownership.callback(_capture_cleanup, cleanup_failures, runtime.stop)
    proxy: _OwnedChild | None = None
    recovery_proxy: _OwnedChild | None = None
    recovery_gate_path: Path | None = None
    actor: _OwnedChild | None = None
    primary: BaseException | None = None
    evidence: dict[str, object] = {"cut": cut.name, "action": action}
    listener = _listener(0)
    ownership.callback(listener.close)
    try:
        proxy_port = int(listener.getsockname()[1])
        gateway_url = runtime.service_url("db-gateway")
        bank_url = runtime.service_url("central-bank")
        proxy_url = f"http://127.0.0.1:{proxy_port}"
        board_config = yaml.safe_load(runtime.config_paths["task-board"].read_text())
        if cut.proxy_kind == "bank":
            board_config["central_bank"]["base_url"] = proxy_url
        else:
            board_config["db_gateway"]["url"] = proxy_url
        hold_recovery = cut.name != "after_final_commit_before_caller_ack"
        lease_expiry_budget = 0.0
        expiry_backoff_budget = 0.0
        if hold_recovery:
            recovery_proxy, recovery_url, recovery_gate_path = _hold_recovery_finalize(
                runtime,
                operation_id,
                ownership,
                cleanup_failures,
                gateway_url if cut.proxy_kind == "bank" else proxy_url,
            )
            board_config["db_gateway"]["url"] = recovery_url
            gateway_config = yaml.safe_load(runtime.config_paths["db-gateway"].read_text())
            gateway_config["board_effect_recovery"].update(
                {
                    "gateway_timeout_seconds": 1,
                    "bank_timeout_seconds": 1,
                    "lease_duration_seconds": 5,
                    "initial_backoff_seconds": 1,
                    "max_backoff_seconds": 2,
                }
            )
            lease_expiry_budget = float(
                gateway_config["board_effect_recovery"]["lease_duration_seconds"]
            )
            expiry_backoff_budget = float(
                gateway_config["board_effect_recovery"]["initial_backoff_seconds"]
            )
            runtime.config_paths["db-gateway"].write_text(
                yaml.safe_dump(gateway_config, sort_keys=False), encoding="utf-8"
            )
        board_config["db_gateway"]["timeout_seconds"] = 1
        board_config["central_bank"]["timeout_seconds"] = 1
        board_config["posting_recovery"]["interval_seconds"] = 0.05
        board_config["financial_effect_recovery"] = {
            "interval_seconds": 0.05,
            "max_delivery_attempts_per_cycle": 10,
        }
        recovery_interval = float(board_config["financial_effect_recovery"]["interval_seconds"])
        gateway_timeout = float(board_config["db_gateway"]["timeout_seconds"])
        bank_timeout = float(board_config["central_bank"]["timeout_seconds"])
        startup_budget = recovery_interval
        opportunity_seconds = (
            startup_budget + recovery_interval + 3 * gateway_timeout + bank_timeout
        )
        opportunity_seconds += lease_expiry_budget + expiry_backoff_budget
        runtime.config_paths["task-board"].write_text(
            yaml.safe_dump(board_config, sort_keys=False), encoding="utf-8"
        )
        gate_path = runtime.run_dir / "mp08-cancel-finalize-gate.json"
        release_path = runtime.run_dir / "mp08-cancel-finalize-release"
        proxy_config_path = runtime.run_dir / "configs/mp08-cancel-proxy.json"
        proxy_config_path.write_text(
            json.dumps(
                {
                    "upstream_url": bank_url if cut.proxy_kind == "bank" else gateway_url,
                    "timeout_seconds": 10,
                    "release_poll_seconds": 0.02,
                    "release_path": str(release_path),
                    "gate": {
                        "method": "POST",
                        "path": (
                            "/never-match-during-setup"
                            if cut.proxy_kind == "bank"
                            else f"/board/task-operations/{operation_id}/finalize"
                        ),
                        "request_sha256": hashlib.sha256(b"{}").hexdigest(),
                        "occurrence": cut.occurrence,
                        "phase": cut.phase,
                        "expected_status": 200,
                        "expected_response": (
                            {
                                "operation_id": operation_id,
                                "operation_state": "completed",
                                "effect_state": "succeeded",
                            }
                            if cut.phase == "after_response"
                            else {}
                        ),
                        "artifact_path": str(gate_path),
                    },
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        proxy = _launch(
            [
                "uv",
                "run",
                "--project",
                "services/db-gateway",
                "--no-sync",
                "python",
                str(_PROXY),
                "--config",
                str(proxy_config_path),
                "--fd",
                str(listener.fileno()),
            ],
            cwd=_REPO_ROOT,
            env=runtime.environment(),
            log_path=runtime.run_dir / "logs/mp08-proxy.log",
            pass_fds=(listener.fileno(),),
            ownership=ownership,
            cleanup_failures=cleanup_failures,
        )
        listener.close()
        _wait_listener(proxy_port, proxy)
        for service in _SERVICES:
            runtime.start_service(service)
        ready_path = runtime.run_dir / "mp08-actor-ready.json"
        trigger_path = runtime.run_dir / "mp08-actor-trigger"
        actor_result_path = runtime.run_dir / "mp08-actor-result.json"
        actor = _launch(
            [
                "uv",
                "run",
                "--project",
                "services/db-gateway",
                "--no-sync",
                "python",
                str(_ACTOR),
                "--config",
                str(runtime.agent_config_path),
                "--ready",
                str(ready_path),
                "--trigger",
                str(trigger_path),
                "--result",
                str(actor_result_path),
                "--operation-id",
                operation_id,
                "--action",
                action,
                "--reward",
                str(_REWARD),
                "--poll-seconds",
                "0.02",
            ],
            cwd=_REPO_ROOT,
            env=runtime.environment(),
            log_path=runtime.run_dir / "logs/mp08-actor.log",
            pass_fds=(),
            ownership=ownership,
            cleanup_failures=cleanup_failures,
        )
        ready = _wait_json(ready_path, actor)
        if cut.proxy_kind == "bank":
            _terminate_owned(proxy)
            authenticated_public_key = runtime.run_dir / "keys" / f"{ready['platform_id']}.pub"
            shutil.copyfile(runtime.run_dir / "keys/platform.pub", authenticated_public_key)
            agent_config = yaml.safe_load(runtime.agent_config_path.read_text())
            signing = agent_config["signing"]
            if action == "ruling":
                bank_action = "escrow_split"
                bank_path = f"/escrow/{ready['escrow_id']}/split"
                expected_payload = {
                    "operation_id": operation_id,
                    "escrow_id": ready["escrow_id"],
                    "worker_account_id": ready["worker_id"],
                    "poster_account_id": ready["poster_id"],
                    "worker_pct": 60,
                    "source_reference": (f"task:{ready['task_id']}:ruling:{ready['dispute_id']}"),
                }
            else:
                bank_action = "escrow_release"
                bank_path = f"/escrow/{ready['escrow_id']}/release"
                expected_payload = {
                    "operation_id": operation_id,
                    "escrow_id": ready["escrow_id"],
                    "recipient_account_id": ready["recipient_id"],
                    "source_reference": (
                        f"task:{ready['task_id']}:"
                        f"{'cancelled' if action == 'cancel' else 'approved'}"
                    ),
                }
            proxy_config_path.write_text(
                json.dumps(
                    {
                        "upstream_url": bank_url,
                        "timeout_seconds": 10,
                        "release_poll_seconds": 0.02,
                        "release_path": str(release_path),
                        "gate": {
                            "method": "POST",
                            "path": bank_path,
                            "request_sha256": None,
                            "occurrence": 1,
                            "phase": "after_response",
                            "expected_status": 200,
                            "expected_response": {},
                            "artifact_path": str(gate_path),
                        },
                        "authentication": {
                            "mode": "bank_token",
                            "public_key_path": str(authenticated_public_key),
                            "expected_kid": ready["platform_id"],
                            "audience": "central-bank",
                            "action": bank_action,
                            "max_lifetime_seconds": signing["max_lifetime_seconds"],
                            "clock_skew_seconds": signing["clock_skew_seconds"],
                            "expected_payload": expected_payload,
                        },
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            replacement_listener = _listener(proxy_port)
            ownership.callback(replacement_listener.close)
            proxy = _launch(
                [
                    "uv",
                    "run",
                    "--project",
                    "services/db-gateway",
                    "--no-sync",
                    "python",
                    str(_PROXY),
                    "--config",
                    str(proxy_config_path),
                    "--fd",
                    str(replacement_listener.fileno()),
                ],
                cwd=_REPO_ROOT,
                env=runtime.environment(),
                log_path=runtime.run_dir / "logs/mp08-bank-proxy.log",
                pass_fds=(replacement_listener.fileno(),),
                ownership=ownership,
                cleanup_failures=cleanup_failures,
            )
            replacement_listener.close()
            _wait_listener(proxy_port, proxy)
        database = runtime.run_dir / "data/economy.db"
        total_before = _financial_total(database)
        counts_before = _global_counts(database)
        immutable_before = _immutable_snapshot(
            database,
            task_id=str(ready["task_id"]),
            escrow_id=str(ready["escrow_id"]),
        )
        unrelated_before = _rows(
            database,
            "SELECT account_id,balance FROM bank_accounts "
            "WHERE account_id NOT IN (?,?) ORDER BY account_id",
            (ready["poster_id"], ready["recipient_id"]),
        )
        trigger_path.write_text(f"{action}\n", encoding="utf-8")
        gate = _wait_gate_or_actor_error(gate_path, proxy, actor_result_path)
        evidence["gate"] = gate
        if recovery_proxy is not None:
            assert recovery_gate_path is not None
            recovery_gate = _wait_json(recovery_gate_path, recovery_proxy)
            assert recovery_gate["phase"] == "before_forward"
            assert recovery_gate["response_headers_sha256"] is None
            evidence["recovery_finalize_gate"] = recovery_gate
        assert gate["phase"] == cut.phase
        gate_pid = int(gate["pid"])
        assert (int(gate["pgid"]), int(gate["sid"])) == (proxy.pgid, proxy.sid)
        assert os.getpgid(gate_pid) == proxy.pgid
        assert os.getsid(gate_pid) == proxy.sid
        assert len(str(gate["request_sha256"])) == 64
        assert len(str(gate["request_headers_sha256"])) == 64
        if cut.phase == "after_response":
            assert len(str(gate["response_headers_sha256"])) == 64
        else:
            assert gate["response_headers_sha256"] is None
        boundary_operation = _rows(
            database,
            "SELECT command,operation_state,effect_state,effect_operation_id "
            "FROM board_task_operations WHERE operation_id=?",
            (operation_id,),
        )
        expected_command = {
            "cancel": "cancel_task",
            "approve": "approve_task",
            "ruling": "record_ruling",
        }[action]
        if cut.phase == "after_response" and cut.proxy_kind == "gateway_finalize":
            assert boundary_operation == (
                (expected_command, "completed", "succeeded", operation_id),
            )
        else:
            assert boundary_operation == ((expected_command, "claimed", "pending", operation_id),)
        boundary_receipts = _rows(
            database,
            "SELECT COUNT(*) FROM bank_operation_receipts WHERE operation_id=?",
            (operation_id,),
        )
        expected_receipts = 1 if cut.proxy_kind == "bank" or cut.occurrence == 2 else 0
        assert boundary_receipts == ((expected_receipts,),)
        evidence["killed_board"] = _kill_runtime_board(runtime)
        if recovery_proxy is not None:
            # Drain the old held request; only the restarted worker may adopt the receipt.
            _terminate_owned(recovery_proxy)
            board_config["db_gateway"]["url"] = gateway_url
            runtime.config_paths["task-board"].write_text(
                yaml.safe_dump(board_config, sort_keys=False), encoding="utf-8"
            )
        caller_result = (
            json.loads(actor_result_path.read_text(encoding="utf-8"))
            if actor_result_path.exists()
            else None
        )
        assert caller_result is None or caller_result["outcome"] == "error"
        evidence["caller_result_after_kill"] = caller_result
        runtime.start_service("task-board")
        evidence["opportunity_seconds"] = opportunity_seconds
        evidence["opportunity_formula"] = {
            "startup_budget": startup_budget,
            "recovery_interval": recovery_interval,
            "gateway_calls": 3,
            "gateway_timeout": gateway_timeout,
            "bank_calls": 1,
            "bank_timeout": bank_timeout,
            "lease_expiry": lease_expiry_budget,
            "expiry_backoff": expiry_backoff_budget,
        }
        completed_within_window = _wait_for_completion(
            database,
            operation_id,
            opportunity_seconds=opportunity_seconds,
        )
        evidence["completed_within_window"] = completed_within_window
        operation_rows = _rows(
            database,
            "SELECT command,operation_state,effect_state,effect_operation_id,event_id "
            "FROM board_task_operations WHERE operation_id=?",
            (operation_id,),
        )
        escrow_rows = _rows(
            database,
            "SELECT status,resolved_at FROM bank_escrow WHERE escrow_id=?",
            (ready["escrow_id"],),
        )
        counts_after = _global_counts(database)
        immutable_after = _immutable_snapshot(
            database,
            task_id=str(ready["task_id"]),
            escrow_id=str(ready["escrow_id"]),
        )
        final = {
            "completed_within_window": completed_within_window,
            "operation": tuple(row[:4] for row in operation_rows),
            "operation_event_persisted": bool(operation_rows and operation_rows[0][4] is not None),
            "task": _rows(
                database,
                "SELECT t.status,t.escrow_pending,c.lifecycle_version "
                "FROM board_tasks AS t JOIN board_task_contracts AS c USING(task_id) "
                "WHERE t.task_id=?",
                (ready["task_id"],),
            ),
            "escrow": tuple(row[:1] for row in escrow_rows),
            "escrow_resolved": bool(escrow_rows and escrow_rows[0][1] is not None),
            "receipt": _rows(
                database,
                "SELECT operation_kind,escrow_id FROM bank_operation_receipts WHERE operation_id=?",
                (operation_id,),
            ),
            "legs": _rows(
                database,
                "SELECT role,account_id,direction,amount FROM bank_operation_legs "
                "WHERE operation_id=? ORDER BY leg_index",
                (operation_id,),
            ),
            "events": _rows(
                database,
                "SELECT COUNT(*) FROM events WHERE task_id=? AND event_type=?",
                (
                    ready["task_id"],
                    {
                        "cancel": "task.cancelled",
                        "approve": "task.approved",
                        "ruling": "task.ruled",
                    }[action],
                ),
            ),
            "poster_balance": _rows(
                database,
                "SELECT balance FROM bank_accounts WHERE account_id=?",
                (ready["poster_id"],),
            ),
            "recipient_balance": _rows(
                database,
                "SELECT balance FROM bank_accounts WHERE account_id=?",
                (ready["recipient_id"],),
            ),
            "global_deltas": {
                table: counts_after[table] - count for table, count in counts_before.items()
            },
        }
        evidence["final"] = final
        _terminate_owned(actor)
        _terminate_owned(proxy)
        assert _financial_total(database) == total_before
        _assert_preexisting_rows_unchanged(immutable_before, immutable_after)
        assert (
            _rows(
                database,
                "SELECT account_id,balance FROM bank_accounts "
                "WHERE account_id NOT IN (?,?) ORDER BY account_id",
                (ready["poster_id"], ready["recipient_id"]),
            )
            == unrelated_before
        )
        assert final == {
            "completed_within_window": True,
            "operation": ((expected_command, "completed", "succeeded", operation_id),),
            "operation_event_persisted": True,
            "task": ((ready["target_status"], 0, ready["target_lifecycle_version"]),),
            "escrow": ((("split" if action == "ruling" else "released"),),),
            "escrow_resolved": True,
            "receipt": (
                (
                    "escrow_split" if action == "ruling" else "escrow_release",
                    ready["escrow_id"],
                ),
            ),
            "legs": (
                *(
                    (
                        ("worker", ready["recipient_id"], "credit", 60),
                        ("poster", ready["poster_id"], "credit", 40),
                    )
                    if action == "ruling"
                    else (("recipient", ready["recipient_id"], "credit", _REWARD),)
                ),
            ),
            "events": ((1,),),
            "poster_balance": (
                ((_REWARD if action == "cancel" else 40 if action == "ruling" else 0),),
            ),
            "recipient_balance": (((60 if action == "ruling" else _REWARD),),),
            "global_deltas": {
                "bank_operation_receipts": 1,
                "bank_operation_legs": 2 if action == "ruling" else 1,
                "bank_transactions": 2 if action == "ruling" else 1,
                "events": 2,
            },
        }
    except BaseException as exc:
        primary = exc
    finally:
        ownership.close()
        evidence["actor_process"] = _child_evidence(actor)
        evidence["proxy_process"] = _child_evidence(proxy)
        evidence["recovery_proxy_process"] = _child_evidence(recovery_proxy)
        evidence["runtime_process_records"] = runtime.process_records
        if runtime.run_dir.exists():
            (runtime.run_dir / f"mp08-process-{action}-{cut.name}-evidence.json").write_text(
                json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
    failures = ([] if primary is None else [primary]) + cleanup_failures
    if len(failures) == 1:
        raise failures[0]
    if failures:
        raise BaseExceptionGroup("MP-08 process proof and cleanup failed", failures)


def _advance_expired_lease_backoff(
    database: Path,
    operation_id: str,
    clock_path: Path,
    expires_at_us: int,
    backoff_seconds: int,
) -> None:
    """Observe inclusive expiry before advancing the frozen clock to its retry time."""
    due_at = expires_at_us + backoff_seconds * 1_000_000
    deadline = time.monotonic() + _TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        row = _rows(
            database,
            "SELECT delivery_state,last_failure_code,last_failure_at_us,next_attempt_at_us "
            "FROM board_task_effect_deliveries WHERE effect_operation_id=?",
            (operation_id,),
        )
        if row == (("pending", "lease_expired", expires_at_us, due_at),):
            _write_clock(clock_path, due_at)
            return
        time.sleep(0.02)
    raise AssertionError("Expired lease did not retain its exact expiry-based retry schedule")


def _initial_history_clock(database: Path) -> int:
    """Start the controlled clock after the imported history's existing timestamps."""
    claimed = _rows(database, "SELECT MAX(claimed_at_us) FROM board_task_operations", ())[0][0]
    enqueued = _rows(database, "SELECT MAX(enqueued_at_us) FROM board_task_effect_deliveries", ())[
        0
    ][0]
    assert type(claimed) is int and type(enqueued) is int
    return max(int(time.time() * 1_000_000), claimed, enqueued)


def test_after_lease_process_cut_reassigns_with_fencing_and_one_receipt(  # noqa: PLR0915
    tmp_path: Path,
) -> None:
    native = _prepare_native_claimed_v4(tmp_path / "source")
    recovery.create_backup(
        native.source.inventory,
        backup_root=native.source.backup_root,
        backup_id="before-process-v5",
        work_root=native.source.work_root,
    )
    migrate = getattr(migration, "migrate_v4_to_v5", None)
    assert callable(migrate), (
        "Capability baseline: explicit verified V4-to-V5 migration is required "
        "before a real lease crash can execute"
    )
    migrate(
        native.source.inventory,
        backup_root=native.source.backup_root,
        backup_id="before-process-v5",
        work_root=native.source.work_root,
        busy_timeout_ms=5000,
        current_task_board_caller_kid="gateway-task-board",
    )
    runtime = IsolatedRuntime.create(
        repo_root=_REPO_ROOT,
        artifacts_root=tmp_path / "runtime",
        services=_SERVICES,
    )
    _copy_sqlite(native.source.database, runtime.run_dir / "data/economy.db")
    database = runtime.run_dir / "data/economy.db"
    gateway_config = yaml.safe_load(runtime.config_paths["db-gateway"].read_text())
    gateway_config["board_effect_recovery"] = {
        "current_task_board_caller_kid": "gateway-task-board",
        "max_attempts_per_delivery": 8,
        "initial_backoff_seconds": 1,
        "max_backoff_seconds": 2,
        "lease_duration_seconds": 5,
        "evidence_interval_seconds": 2,
        "gateway_timeout_seconds": 1,
        "bank_timeout_seconds": 1,
    }
    runtime.config_paths["db-gateway"].write_text(
        yaml.safe_dump(gateway_config, sort_keys=False), encoding="utf-8"
    )
    listener = _listener(0)
    proxy_port = int(listener.getsockname()[1])
    board_config = yaml.safe_load(runtime.config_paths["task-board"].read_text())
    board_config["db_gateway"]["url"] = f"http://127.0.0.1:{proxy_port}"
    board_config["db_gateway"]["timeout_seconds"] = 1
    board_config["central_bank"]["timeout_seconds"] = 1
    board_config["financial_effect_recovery"] = {
        "interval_seconds": 0.05,
        "max_delivery_attempts_per_cycle": 10,
    }
    runtime.config_paths["task-board"].write_text(
        yaml.safe_dump(board_config, sort_keys=False), encoding="utf-8"
    )
    gateway_auth = gateway_config["auth"]
    gate_path = runtime.run_dir / "mp08-after-lease-gate.json"
    release_path = runtime.run_dir / "mp08-after-lease-release"
    proxy_config_path = runtime.run_dir / "configs/mp08-after-lease-proxy.json"
    proxy_config_path.write_text(
        json.dumps(
            {
                "upstream_url": runtime.service_url("db-gateway"),
                "timeout_seconds": 10,
                "release_poll_seconds": 0.02,
                "release_path": str(release_path),
                "gate": {
                    "method": "POST",
                    "path": "/board/task-effect-deliveries/lease",
                    "request_sha256": None,
                    "occurrence": 1,
                    "phase": "after_response",
                    "expected_status": 200,
                    "expected_response": {"protocol_version": 1},
                    "artifact_path": str(gate_path),
                },
                "authentication": {
                    "mode": "gateway_lease",
                    "public_key_path": str(runtime.run_dir / "keys/gateway-task-board.pub"),
                    "expected_kid": "gateway-task-board",
                    "audience": gateway_auth["audience"],
                    "action": "gateway.board.write",
                    "max_lifetime_seconds": gateway_auth["max_lifetime_seconds"],
                    "clock_skew_seconds": gateway_auth["clock_skew_seconds"],
                    "expected_payload": {"protocol_version": 1},
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    ownership = contextlib.ExitStack()
    cleanup_failures: list[BaseException] = []
    ownership.callback(_capture_cleanup, cleanup_failures, runtime.stop)
    ownership.callback(listener.close)
    proxy: _OwnedChild | None = None
    clock_gateway: _OwnedChild | None = None
    primary: BaseException | None = None
    evidence: dict[str, object] = {"cut": "after_lease"}
    clock_path = runtime.run_dir / "mp08-gateway-clock-us"
    _write_clock(clock_path, _initial_history_clock(database))
    try:
        proxy = _launch(
            [
                "uv",
                "run",
                "--project",
                "services/db-gateway",
                "--no-sync",
                "python",
                str(_PROXY),
                "--config",
                str(proxy_config_path),
                "--fd",
                str(listener.fileno()),
            ],
            cwd=_REPO_ROOT,
            env=runtime.environment(),
            log_path=runtime.run_dir / "logs/mp08-after-lease-proxy.log",
            pass_fds=(listener.fileno(),),
            ownership=ownership,
            cleanup_failures=cleanup_failures,
        )
        listener.close()
        _wait_listener(proxy_port, proxy)
        clock_gateway = _start_clock_gateway(
            runtime,
            clock_path,
            ownership=ownership,
            cleanup_failures=cleanup_failures,
        )
        for service in _SERVICES:
            if service == "db-gateway":
                continue
            runtime.start_service(service)
        gate = _wait_json(gate_path, proxy)
        evidence["gate"] = gate
        assert gate["worker_id"] is not None
        worker_a = str(gate["worker_id"])
        assert len(str(gate["request_sha256"])) == 64
        assert len(str(gate["request_headers_sha256"])) == 64
        assert len(str(gate["response_headers_sha256"])) == 64
        leased = _rows(
            database,
            "SELECT effect_operation_id,delivery_state,lease_owner_id,lease_generation,"
            "lease_expires_at_us,total_attempt_count,cycle_attempt_count "
            "FROM board_task_effect_deliveries WHERE effect_operation_id=?",
            (native.operation_id,),
        )
        assert len(leased) == 1
        effect_id, state, owner_a, generation_a, expires_a, total_a, cycle_a = leased[0]
        assert (effect_id, state, owner_a) == (native.operation_id, "leased", worker_a)
        assert type(generation_a) is int and type(expires_a) is int
        assert type(total_a) is int and type(cycle_a) is int
        evidence["killed_board_a"] = _kill_runtime_board(runtime)
        _write_clock(clock_path, int(expires_a))
        runtime.start_service("task-board")
        _advance_expired_lease_backoff(
            database,
            native.operation_id,
            clock_path,
            int(expires_a),
            gateway_config["board_effect_recovery"]["initial_backoff_seconds"],
        )
        opportunity_seconds = 0.05 + 3 * 1 + 1
        completed = _wait_for_completion(
            database,
            native.operation_id,
            opportunity_seconds=opportunity_seconds,
        )
        assert completed is True
        final_delivery = _rows(
            database,
            "SELECT d.effect_operation_id,d.delivery_state,d.lease_owner_id,d.lease_generation,"
            "d.completion_event_id,r.operation_id,d.total_attempt_count,d.cycle_attempt_count "
            "FROM board_task_effect_deliveries d LEFT JOIN bank_operation_receipts r "
            "ON r.operation_id=d.effect_operation_id WHERE d.effect_operation_id=?",
            (native.operation_id,),
        )
        assert len(final_delivery) == 1
        final = final_delivery[0]
        assert final[0] == native.operation_id
        assert final[1] == "completed"
        assert final[2] is None
        assert final[3] == int(generation_a) + 1
        assert final[4] is not None
        assert final[5] == native.operation_id
        assert final[6] == int(total_a) + 1
        assert final[7] == int(cycle_a) + 1
        stale_status, stale_body = _signed_gateway_post(
            runtime,
            path=(
                f"/board/task-effect-deliveries/{native.operation_id}/attempts/{generation_a}/fail"
            ),
            body={
                "protocol_version": 1,
                "worker_id": worker_a,
                "error_code": "central_bank_unavailable",
            },
        )
        assert stale_status == 409
        assert stale_body["error"] == "effect_delivery_attempt_stale"
        assert _rows(
            database,
            "SELECT COUNT(*) FROM bank_operation_receipts WHERE operation_id=?",
            (native.operation_id,),
        ) == ((1,),)
        assert _rows(
            database,
            "SELECT COUNT(*) FROM board_task_effect_deliveries "
            "WHERE delivery_state IN ('pending','leased')",
        ) == ((0,),)
        evidence["leased"] = leased
        evidence["final_delivery"] = final_delivery
        _terminate_owned(proxy)
    except BaseException as exc:
        primary = exc
    finally:
        ownership.close()
        evidence["proxy_process"] = _child_evidence(proxy)
        evidence["clock_gateway_process"] = _child_evidence(clock_gateway)
        evidence["runtime_process_records"] = runtime.process_records
        if runtime.run_dir.exists():
            (runtime.run_dir / "mp08-process-after-lease-evidence.json").write_text(
                json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
    failures = ([] if primary is None else [primary]) + cleanup_failures
    if len(failures) == 1:
        raise failures[0]
    if failures:
        raise BaseExceptionGroup("MP-08 after-lease proof and cleanup failed", failures)


def test_recovery_predispatch_cut_reassigns_and_dispatches_once(  # noqa: PLR0915
    tmp_path: Path,
) -> None:
    native = _prepare_native_claimed_v4(tmp_path / "source")
    recovery.create_backup(
        native.source.inventory,
        backup_root=native.source.backup_root,
        backup_id="before-predispatch-v5",
        work_root=native.source.work_root,
    )
    migrate = getattr(migration, "migrate_v4_to_v5", None)
    assert callable(migrate), (
        "Capability baseline: explicit verified V4-to-V5 migration is required "
        "before recovery-worker pre-dispatch can execute"
    )
    migrate(
        native.source.inventory,
        backup_root=native.source.backup_root,
        backup_id="before-predispatch-v5",
        work_root=native.source.work_root,
        busy_timeout_ms=5000,
        current_task_board_caller_kid="gateway-task-board",
    )
    runtime = IsolatedRuntime.create(
        repo_root=_REPO_ROOT,
        artifacts_root=tmp_path / "runtime",
        services=_SERVICES,
    )
    _copy_sqlite(native.source.database, runtime.run_dir / "data/economy.db")
    database = runtime.run_dir / "data/economy.db"
    gateway_config = yaml.safe_load(runtime.config_paths["db-gateway"].read_text())
    gateway_config["board_effect_recovery"] = {
        "current_task_board_caller_kid": "gateway-task-board",
        "max_attempts_per_delivery": 8,
        "initial_backoff_seconds": 1,
        "max_backoff_seconds": 2,
        "lease_duration_seconds": 5,
        "evidence_interval_seconds": 2,
        "gateway_timeout_seconds": 1,
        "bank_timeout_seconds": 1,
    }
    runtime.config_paths["db-gateway"].write_text(
        yaml.safe_dump(gateway_config, sort_keys=False), encoding="utf-8"
    )
    lease_listener = _listener(0)
    bank_listener = _listener(0)
    lease_port = int(lease_listener.getsockname()[1])
    bank_port = int(bank_listener.getsockname()[1])
    board_config = yaml.safe_load(runtime.config_paths["task-board"].read_text())
    board_config["db_gateway"]["url"] = f"http://127.0.0.1:{lease_port}"
    # Keep the authenticated lease response alive while configuring the Bank gate.
    board_config["db_gateway"]["timeout_seconds"] = _TIMEOUT_SECONDS
    gateway_config["board_effect_recovery"]["gateway_timeout_seconds"] = int(_TIMEOUT_SECONDS)
    gateway_config["board_effect_recovery"]["lease_duration_seconds"] = (
        2 * (int(_TIMEOUT_SECONDS) + 1) + 1
    )
    runtime.config_paths["db-gateway"].write_text(
        yaml.safe_dump(gateway_config, sort_keys=False), encoding="utf-8"
    )
    board_config["central_bank"]["base_url"] = f"http://127.0.0.1:{bank_port}"
    board_config["central_bank"]["timeout_seconds"] = 1
    board_config["financial_effect_recovery"] = {
        "interval_seconds": 0.05,
        "max_delivery_attempts_per_cycle": 10,
    }
    runtime.config_paths["task-board"].write_text(
        yaml.safe_dump(board_config, sort_keys=False), encoding="utf-8"
    )
    gateway_auth = gateway_config["auth"]
    lease_gate_path = runtime.run_dir / "mp08-predispatch-lease-gate.json"
    lease_release_path = runtime.run_dir / "mp08-predispatch-lease-release"
    lease_config_path = runtime.run_dir / "configs/mp08-predispatch-lease-proxy.json"
    lease_config_path.write_text(
        json.dumps(
            {
                "upstream_url": runtime.service_url("db-gateway"),
                "timeout_seconds": 10,
                "release_poll_seconds": 0.02,
                "release_path": str(lease_release_path),
                "gate": {
                    "method": "POST",
                    "path": "/board/task-effect-deliveries/lease",
                    "request_sha256": None,
                    "occurrence": 1,
                    "phase": "before_forward",
                    "expected_status": 200,
                    "expected_response": {},
                    "artifact_path": str(lease_gate_path),
                },
                "authentication": {
                    "mode": "gateway_lease",
                    "public_key_path": str(runtime.run_dir / "keys/gateway-task-board.pub"),
                    "expected_kid": "gateway-task-board",
                    "audience": gateway_auth["audience"],
                    "action": "gateway.board.write",
                    "max_lifetime_seconds": gateway_auth["max_lifetime_seconds"],
                    "clock_skew_seconds": gateway_auth["clock_skew_seconds"],
                    "expected_payload": {"protocol_version": 1},
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    bank_gate_path = runtime.run_dir / "mp08-predispatch-bank-gate.json"
    bank_release_path = runtime.run_dir / "mp08-predispatch-bank-release"
    bank_config_path = runtime.run_dir / "configs/mp08-predispatch-bank-proxy.json"
    bank_config_path.write_text(
        json.dumps(
            {
                "upstream_url": runtime.service_url("central-bank"),
                "timeout_seconds": 10,
                "release_poll_seconds": 0.02,
                "release_path": str(bank_release_path),
                "gate": {
                    "method": "POST",
                    "path": "/never-match-before-platform-registration",
                    "request_sha256": hashlib.sha256(b"{}").hexdigest(),
                    "occurrence": 1,
                    "phase": "before_forward",
                    "expected_status": 200,
                    "expected_response": {},
                    "artifact_path": str(bank_gate_path),
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    ownership = contextlib.ExitStack()
    cleanup_failures: list[BaseException] = []
    ownership.callback(_capture_cleanup, cleanup_failures, runtime.stop)
    ownership.callback(lease_listener.close)
    ownership.callback(bank_listener.close)
    lease_proxy: _OwnedChild | None = None
    bank_proxy: _OwnedChild | None = None
    clock_gateway: _OwnedChild | None = None
    primary: BaseException | None = None
    evidence: dict[str, object] = {"cut": "recovery_predispatch"}
    clock_path = runtime.run_dir / "mp08-gateway-clock-us"
    _write_clock(clock_path, _initial_history_clock(database))
    try:
        lease_proxy = _launch(
            [
                "uv",
                "run",
                "--project",
                "services/db-gateway",
                "--no-sync",
                "python",
                str(_PROXY),
                "--config",
                str(lease_config_path),
                "--fd",
                str(lease_listener.fileno()),
            ],
            cwd=_REPO_ROOT,
            env=runtime.environment(),
            log_path=runtime.run_dir / "logs/mp08-predispatch-lease-proxy.log",
            pass_fds=(lease_listener.fileno(),),
            ownership=ownership,
            cleanup_failures=cleanup_failures,
        )
        bank_proxy = _launch(
            [
                "uv",
                "run",
                "--project",
                "services/db-gateway",
                "--no-sync",
                "python",
                str(_PROXY),
                "--config",
                str(bank_config_path),
                "--fd",
                str(bank_listener.fileno()),
            ],
            cwd=_REPO_ROOT,
            env=runtime.environment(),
            log_path=runtime.run_dir / "logs/mp08-predispatch-bank-proxy.log",
            pass_fds=(bank_listener.fileno(),),
            ownership=ownership,
            cleanup_failures=cleanup_failures,
        )
        lease_listener.close()
        bank_listener.close()
        _wait_listener(lease_port, lease_proxy)
        _wait_listener(bank_port, bank_proxy)
        clock_gateway = _start_clock_gateway(
            runtime,
            clock_path,
            ownership=ownership,
            cleanup_failures=cleanup_failures,
        )
        for service in _SERVICES:
            if service == "db-gateway":
                continue
            runtime.start_service(service)
        lease_gate = _wait_json(lease_gate_path, lease_proxy)
        worker_a = str(lease_gate["worker_id"])
        platform_row = _rows(
            database,
            "SELECT agent_id FROM identity_agents WHERE name='Platform' ORDER BY agent_id",
        )
        assert len(platform_row) == 1
        platform_id = str(platform_row[0][0])
        _terminate_owned(bank_proxy)
        platform_public = runtime.run_dir / "keys" / f"{platform_id}.pub"
        shutil.copyfile(runtime.run_dir / "keys/platform.pub", platform_public)
        agent_config = yaml.safe_load(runtime.agent_config_path.read_text())
        signing = agent_config["signing"]
        bank_config_path.write_text(
            json.dumps(
                {
                    "upstream_url": runtime.service_url("central-bank"),
                    "timeout_seconds": 10,
                    "release_poll_seconds": 0.02,
                    "release_path": str(bank_release_path),
                    "gate": {
                        "method": "POST",
                        "path": f"/escrow/{native.source.escrow_id}/release",
                        "request_sha256": None,
                        "occurrence": 1,
                        "phase": "before_forward",
                        "expected_status": 200,
                        "expected_response": {},
                        "artifact_path": str(bank_gate_path),
                    },
                    "authentication": {
                        "mode": "bank_token",
                        "public_key_path": str(platform_public),
                        "expected_kid": platform_id,
                        "audience": "central-bank",
                        "action": "escrow_release",
                        "max_lifetime_seconds": signing["max_lifetime_seconds"],
                        "clock_skew_seconds": signing["clock_skew_seconds"],
                        "expected_payload": {
                            "operation_id": native.operation_id,
                            "escrow_id": native.source.escrow_id,
                            "recipient_account_id": native.source.worker_id,
                            "source_reference": f"task:{native.source.task_id}:approved",
                        },
                    },
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        replacement_bank_listener = _listener(bank_port)
        ownership.callback(replacement_bank_listener.close)
        bank_proxy = _launch(
            [
                "uv",
                "run",
                "--project",
                "services/db-gateway",
                "--no-sync",
                "python",
                str(_PROXY),
                "--config",
                str(bank_config_path),
                "--fd",
                str(replacement_bank_listener.fileno()),
            ],
            cwd=_REPO_ROOT,
            env=runtime.environment(),
            log_path=runtime.run_dir / "logs/mp08-predispatch-bank-auth-proxy.log",
            pass_fds=(replacement_bank_listener.fileno(),),
            ownership=ownership,
            cleanup_failures=cleanup_failures,
        )
        replacement_bank_listener.close()
        _wait_listener(bank_port, bank_proxy)
        lease_release_path.write_text("release\n", encoding="utf-8")
        bank_gate = _wait_json(bank_gate_path, bank_proxy)
        assert len(str(bank_gate["request_sha256"])) == 64
        assert len(str(bank_gate["request_headers_sha256"])) == 64
        assert bank_gate["response_headers_sha256"] is None
        leased = _rows(
            database,
            "SELECT delivery_state,lease_owner_id,lease_generation,lease_expires_at_us,"
            "total_attempt_count,cycle_attempt_count FROM board_task_effect_deliveries "
            "WHERE effect_operation_id=?",
            (native.operation_id,),
        )
        assert len(leased) == 1
        state, owner_a, generation_a, expires_a, total_a, cycle_a = leased[0]
        assert (state, owner_a) == ("leased", worker_a)
        assert type(generation_a) is int
        assert type(expires_a) is int
        assert type(total_a) is int
        assert type(cycle_a) is int
        evidence["lease_gate"] = lease_gate
        evidence["bank_gate"] = bank_gate
        evidence["killed_board_a"] = _kill_runtime_board(runtime)
        _write_clock(clock_path, expires_a)
        board_config["db_gateway"]["timeout_seconds"] = 1
        runtime.config_paths["task-board"].write_text(
            yaml.safe_dump(board_config, sort_keys=False), encoding="utf-8"
        )
        runtime.start_service("task-board")
        _advance_expired_lease_backoff(
            database,
            native.operation_id,
            clock_path,
            expires_a,
            gateway_config["board_effect_recovery"]["initial_backoff_seconds"],
        )
        completed = _wait_for_completion(
            database,
            native.operation_id,
            opportunity_seconds=0.05 + 3 * 1 + 1,
        )
        assert completed is True
        final = _rows(
            database,
            "SELECT d.delivery_state,d.lease_owner_id,d.lease_generation,d.completion_event_id,"
            "r.operation_id,d.total_attempt_count,d.cycle_attempt_count "
            "FROM board_task_effect_deliveries d LEFT JOIN bank_operation_receipts r "
            "ON r.operation_id=d.effect_operation_id WHERE d.effect_operation_id=?",
            (native.operation_id,),
        )
        assert final == (
            (
                "completed",
                None,
                generation_a + 1,
                final[0][3],
                native.operation_id,
                total_a + 1,
                cycle_a + 1,
            ),
        )
        assert final[0][3] is not None
        stale_status, stale_body = _signed_gateway_post(
            runtime,
            path=(
                f"/board/task-effect-deliveries/{native.operation_id}/attempts/{generation_a}/fail"
            ),
            body={
                "protocol_version": 1,
                "worker_id": worker_a,
                "error_code": "central_bank_unavailable",
            },
        )
        assert stale_status == 409
        assert stale_body["error"] == "effect_delivery_attempt_stale"
        assert _rows(
            database,
            "SELECT COUNT(*) FROM bank_operation_receipts WHERE operation_id=?",
            (native.operation_id,),
        ) == ((1,),)
        assert _rows(
            database,
            "SELECT COUNT(*) FROM board_task_effect_deliveries "
            "WHERE delivery_state IN ('pending','leased')",
        ) == ((0,),)
        evidence["leased"] = leased
        evidence["final"] = final
        _terminate_owned(lease_proxy)
        _terminate_owned(bank_proxy)
    except BaseException as exc:
        primary = exc
    finally:
        ownership.close()
        evidence["lease_proxy"] = _child_evidence(lease_proxy)
        evidence["bank_proxy"] = _child_evidence(bank_proxy)
        evidence["clock_gateway"] = _child_evidence(clock_gateway)
        evidence["runtime_process_records"] = runtime.process_records
        if runtime.run_dir.exists():
            (runtime.run_dir / "mp08-process-recovery-predispatch-evidence.json").write_text(
                json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
    failures = ([] if primary is None else [primary]) + cleanup_failures
    if len(failures) == 1:
        raise failures[0]
    if failures:
        raise BaseExceptionGroup("MP-08 recovery predispatch proof failed", failures)
