"""MP-08 real-process restart proof for a native cancellation before Bank dispatch."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import threading
import time
import uuid
from builtins import BaseExceptionGroup
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml
from scripts.isolated_test_runtime import (
    IsolatedRuntime,
    _drain_owned_process_group,
    _release_owned_launch,
    _start_owned_launch,
)

from db_gateway_service.money_audit import audit_money
from db_gateway_service.receipt_audit import audit_receipt_ownership

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
_ACCEPTED_PRICE = 90


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


def _write_clock(path: Path, clock_us: int) -> None:
    staged = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    staged.write_text(f"{clock_us}\n", encoding="ascii")
    os.replace(staged, path)


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
            if action == "cancel":
                bank_action = "escrow_release"
                bank_path = f"/escrow/{ready['escrow_id']}/release"
                expected_payload = {
                    "operation_id": operation_id,
                    "escrow_id": ready["escrow_id"],
                    "recipient_account_id": ready["recipient_id"],
                    "source_reference": f"task:{ready['task_id']}:cancelled",
                }
            else:
                # Accepted-bid pricing: approval and ruling both split the escrow by
                # an exact worker amount on the accepted price (the Bank's settle route).
                bank_action = "escrow_settle"
                bank_path = f"/escrow/{ready['escrow_id']}/settle"
                expected_payload = {
                    "operation_id": operation_id,
                    "escrow_id": ready["escrow_id"],
                    "worker_account_id": ready["worker_id"],
                    "poster_account_id": ready["poster_id"],
                    "worker_amount": (
                        _ACCEPTED_PRICE * 60 // 100 if action == "ruling" else _ACCEPTED_PRICE
                    ),
                    "source_reference": (
                        f"task:{ready['task_id']}:ruling:{ready['dispute_id']}"
                        if action == "ruling"
                        else f"task:{ready['task_id']}:approved"
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
        # Accepted-bid pricing (Q-M2): the accepted bid is 90 of the 100 budget;
        # approval pays it in full, a 60% ruling pays floor(90 * 60 / 100).
        worker_paid = {"approve": _ACCEPTED_PRICE, "ruling": _ACCEPTED_PRICE * 60 // 100}.get(
            action, 0
        )
        assert final == {
            "completed_within_window": True,
            "operation": ((expected_command, "completed", "succeeded", operation_id),),
            "operation_event_persisted": True,
            "task": ((ready["target_status"], 0, ready["target_lifecycle_version"]),),
            "escrow": ((("released" if action == "cancel" else "split"),),),
            "escrow_resolved": True,
            "receipt": (
                (
                    "escrow_release" if action == "cancel" else "escrow_split",
                    ready["escrow_id"],
                ),
            ),
            "legs": (
                *(
                    (("recipient", ready["recipient_id"], "credit", _REWARD),)
                    if action == "cancel"
                    else (
                        ("worker", ready["recipient_id"], "credit", worker_paid),
                        ("poster", ready["poster_id"], "credit", _REWARD - worker_paid),
                    )
                ),
            ),
            "events": ((1,),),
            "poster_balance": (((_REWARD if action == "cancel" else _REWARD - worker_paid),),),
            "recipient_balance": (((_REWARD if action == "cancel" else worker_paid),),),
            "global_deltas": {
                "bank_operation_receipts": 1,
                "bank_operation_legs": 1 if action == "cancel" else 2,
                "bank_transactions": 1 if action == "cancel" else 2,
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
