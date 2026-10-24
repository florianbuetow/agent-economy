"""MP-05 legacy refund survives process death at intent and commit boundaries."""

from __future__ import annotations

import contextlib
import importlib
import json
import os
import signal
import sqlite3
import subprocess
import time
from builtins import BaseExceptionGroup
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx
import pytest
import tests as root_tests
import yaml
from scripts.isolated_test_runtime import (
    IsolatedRuntime,
    _drain_owned_process_group,
    _release_owned_launch,
    _start_owned_launch,
)
from service_auth.gateway import GatewayRequestSigner, GatewaySignerConfig

from db_gateway_service.money_audit import audit_money
from db_gateway_service.migration import migrate_v4_to_v5
from db_gateway_service.publication_migration import migrate_v3_to_v4

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Root acceptance and DB Gateway fixtures both live in packages named ``tests``.
# Extend the already-imported root package only for this process so the historical
# v3 builders remain the single fixture source instead of being copied here.
_GATEWAY_TESTS = _REPO_ROOT / "services" / "db-gateway" / "tests"
assert hasattr(root_tests, "__path__")
root_tests.__path__.append(str(_GATEWAY_TESTS))

pytestmark = pytest.mark.integration

_TASK_BOARD_ROOT = _REPO_ROOT / "services" / "task-board"
_PHASE_SERVER = Path(__file__).with_name("mp05_legacy_refund_phase_server.py")
_SERVICES = ("db-gateway", "identity", "central-bank", "task-board")
_GATE_TIMEOUT_SECONDS = 30.0
_RECOVERY_TIMEOUT_SECONDS = 20.0
_Phase = Literal["refund-intent", "refund-commit"]
_IMMUTABLE_OBSERVATION_FIELDS = (
    "observation_id",
    "observation_contract_version",
    "escrow_id",
    "task_id",
    "poster_id",
    "amount",
    "observed_fact_json",
    "observed_fact_sha256",
    "caller_kid",
    "refund_operation_id",
    "refund_request_json",
    "refund_request_sha256",
)


@dataclass(frozen=True, slots=True)
class _Template:
    database: Path
    history: Any


@dataclass(slots=True)
class _OwnedBoard:
    process: subprocess.Popen[bytes]
    pgid: int
    sid: int
    actor_pid: int | None = None
    drained: bool = False


@dataclass(slots=True)
class _LaunchGate:
    descriptor: int | None

    def release(self) -> None:
        descriptor = self.descriptor
        if descriptor is None:
            raise RuntimeError("Launch gate is no longer owned")
        try:
            _release_owned_launch(descriptor)
        finally:
            # _release_owned_launch closes in its own finally block, including
            # failed authorization writes. Never retain a second close owner.
            self.descriptor = None

    def close(self) -> None:
        descriptor = self.descriptor
        if descriptor is None:
            return
        self.descriptor = None
        os.close(descriptor)


@pytest.fixture(scope="module")
def migrated_template(tmp_path_factory: pytest.TempPathFactory) -> _Template:
    fixtures = importlib.import_module("tests.fakes.mp05_publication_migration_history")
    root = tmp_path_factory.mktemp("mp05-legacy-refund-process")
    source = fixtures.prepare_v3_source(root / "source")
    writer = fixtures.open_v3_writer(source)
    try:
        history = fixtures.add_rich_cutover_history(writer)
    finally:
        writer.close()
    backup_id = "legacy-refund-process-before-mp05"
    fixtures.publish_verified_backup(source, backup_id=backup_id)
    result = migrate_v3_to_v4(
        source.inventory,
        backup_root=source.backup_root,
        backup_id=backup_id,
        work_root=source.work_root,
        busy_timeout_ms=5_000,
    )
    assert result.observation == "committed_v4_verified"
    v5_backup_id = "legacy-refund-process-before-mp08"
    fixtures.publish_verified_backup(source, backup_id=v5_backup_id)
    current = migrate_v4_to_v5(
        source.inventory,
        backup_root=source.backup_root,
        backup_id=v5_backup_id,
        work_root=source.work_root,
        busy_timeout_ms=5_000,
        current_task_board_caller_kid=source.caller_kid,
    )
    assert current.observation == "committed_v5_verified"
    template = root / "template.db"
    _copy_sqlite(source.database, template)
    return _Template(template, history)


@pytest.mark.parametrize("phase", ["refund-intent", "refund-commit"])
def test_legacy_refund_process_restart_converges_once(  # noqa: PLR0915
    migrated_template: _Template,
    tmp_path: Path,
    phase: _Phase,
) -> None:
    ownership = contextlib.ExitStack()
    cleanup_failures: list[BaseException] = []
    runtime: IsolatedRuntime | None = None
    database: Path | None = None
    crash_log: Path | None = None
    sentinel: _OwnedBoard | None = None
    board: _OwnedBoard | None = None
    gate: dict[str, Any] | None = None
    baseline_history: dict[str, Any] | None = None
    final_history: dict[str, Any] | None = None
    selected_escrow_id: str | None = None
    before: dict[str, Any] | None = None
    primary: BaseException | None = None
    try:
        runtime = IsolatedRuntime.create(
            repo_root=_REPO_ROOT,
            artifacts_root=tmp_path / "runtime",
            services=_SERVICES,
        )
        ownership.callback(_capture_cleanup, cleanup_failures, runtime.stop)
        database = runtime.run_dir / "data" / "economy.db"
        _copy_sqlite(migrated_template.database, database)
        baseline_history = _history_projection(database, migrated_template.history)
        gate_path = runtime.run_dir / f"{phase}-gate.json"
        crash_log = runtime.run_dir / "logs" / f"task-board-{phase}.log"
        sentinel = _start_sentinel(
            runtime.run_dir,
            ownership=ownership,
            cleanup_failures=cleanup_failures,
        )
        for service in ("db-gateway", "identity", "central-bank"):
            runtime.start_service(service)
        board = _start_crash_board(
            runtime,
            phase,
            gate_path,
            crash_log,
            ownership=ownership,
            cleanup_failures=cleanup_failures,
        )
        gate = _wait_for_gate(board, gate_path)
        _bind_actor(board, int(gate["pid"]))
        assert gate["phase"] == phase.replace("-", "_")
        selected_escrow_id = str(gate["evidence"]["escrow_id"])
        before = baseline_history["pending"][selected_escrow_id]
        at_boundary = _observation_projection(database, selected_escrow_id)
        _assert_boundary(phase, before, at_boundary)

        _kill_and_reap(board)
        assert _alive(sentinel.actor_pid)
        runtime.start_service("task-board")
        pending_escrow_ids = tuple(baseline_history["pending"])
        final_pending = _wait_for_all_refunds(database, pending_escrow_ids)
        final_history = _history_projection(database, migrated_template.history)
        _assert_complete_history(baseline_history, final_history)
        for escrow_id in pending_escrow_ids:
            _assert_refund_once(
                baseline_history["pending"][escrow_id], final_pending[escrow_id]
            )
            _assert_protected_diagnostic(runtime, final_pending[escrow_id])
        assert _alive(sentinel.actor_pid)
    except BaseException as exc:
        primary = exc
    finally:
        ownership.close()
        if runtime is not None:
            evidence = {
                "phase": phase,
                "gate": gate,
                "selected_before": before,
                "baseline_history": baseline_history,
                "final_history": final_history,
                "board": _process_evidence(board),
                "sentinel": _process_evidence(sentinel),
                "crash_log": (
                    crash_log.read_text(encoding="utf-8")
                    if crash_log is not None and crash_log.exists()
                    else None
                ),
            }
            try:
                (runtime.run_dir / f"{phase}-evidence.json").write_text(
                    json.dumps(evidence, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
            except BaseException as exc:
                cleanup_failures.append(exc)
    _raise_failures(primary, cleanup_failures)


def _copy_sqlite(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with (
        contextlib.closing(sqlite3.connect(source)) as source_connection,
        contextlib.closing(sqlite3.connect(target)) as target_connection,
    ):
        source_connection.backup(target_connection)


def _start_crash_board(
    runtime: IsolatedRuntime,
    phase: _Phase,
    gate_path: Path,
    log_path: Path,
    *,
    ownership: contextlib.ExitStack,
    cleanup_failures: list[BaseException],
) -> _OwnedBoard:
    reservation = runtime._reservations.pop("task-board")
    ownership.callback(_capture_cleanup, cleanup_failures, reservation.release)
    command = [
        "uv",
        "run",
        "--no-sync",
        "python",
        str(_PHASE_SERVER),
        "--phase",
        phase,
        "--fd",
        str(reservation.socket.fileno()),
        "--gate",
        str(gate_path),
    ]
    with log_path.open("ab") as log:
        process, pgid, sid, launch_descriptor = _start_owned_launch(
            command,
            cwd=str(_TASK_BOARD_ROOT),
            env=runtime.service_environment("task-board"),
            stdout=log,
            stderr=subprocess.STDOUT,
            pass_fds=(reservation.socket.fileno(),),
        )
        child = _OwnedBoard(process=process, pgid=pgid, sid=sid)
        ownership.callback(_capture_cleanup, cleanup_failures, _terminate_board, child)
        gate = _LaunchGate(launch_descriptor)
        ownership.callback(_capture_cleanup, cleanup_failures, gate.close)
        reservation.release()
        gate.release()
    return child


def _start_sentinel(
    root: Path,
    *,
    ownership: contextlib.ExitStack,
    cleanup_failures: list[BaseException],
) -> _OwnedBoard:
    process, pgid, sid, descriptor = _start_owned_launch(
        ["/bin/sleep", "60"],
        cwd=str(root),
        env=dict(os.environ),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        pass_fds=(),
    )
    child = _OwnedBoard(process=process, pgid=pgid, sid=sid, actor_pid=process.pid)
    ownership.callback(_capture_cleanup, cleanup_failures, _terminate_board, child)
    gate = _LaunchGate(descriptor)
    ownership.callback(_capture_cleanup, cleanup_failures, gate.close)
    gate.release()
    return child


def _wait_for_gate(board: _OwnedBoard, path: Path) -> dict[str, Any]:
    deadline = time.monotonic() + _GATE_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if path.exists():
            value = json.loads(path.read_text(encoding="utf-8"))
            assert isinstance(value, dict)
            return value
        if board.process.poll() is not None:
            raise AssertionError(
                f"crash Board exited early with {board.process.returncode}"
            )
        time.sleep(0.05)
    raise TimeoutError("crash Board did not reach the legacy-refund gate")


def _bind_actor(board: _OwnedBoard, actor_pid: int) -> None:
    assert actor_pid > 0
    assert os.getpgid(actor_pid) == board.pgid
    assert os.getsid(actor_pid) == board.sid
    board.actor_pid = actor_pid


def _kill_and_reap(board: _OwnedBoard) -> None:
    assert board.actor_pid is not None
    os.kill(board.actor_pid, signal.SIGKILL)
    assert board.process.wait(timeout=15) != 0
    with pytest.raises(ProcessLookupError):
        os.killpg(board.pgid, 0)
    board.drained = True


def _terminate_board(board: _OwnedBoard) -> None:
    if board.drained:
        return
    _drain_owned_process_group(
        board.process,
        pgid=board.pgid,
        sid=board.sid,
    )
    board.drained = True


def _alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _capture_cleanup(
    failures: list[BaseException], callback: Any, *arguments: object
) -> None:
    try:
        callback(*arguments)
    except BaseException as exc:
        failures.append(exc)


def _raise_failures(
    primary: BaseException | None, cleanup_failures: list[BaseException]
) -> None:
    failures = ([] if primary is None else [primary]) + cleanup_failures
    if len(failures) == 1:
        raise failures[0]
    if failures:
        raise BaseExceptionGroup("legacy-refund process test failed", failures)


def _wait_for_all_refunds(
    database: Path, escrow_ids: tuple[str, ...]
) -> dict[str, dict[str, Any]]:
    deadline = time.monotonic() + _RECOVERY_TIMEOUT_SECONDS
    last: dict[str, dict[str, Any]] = {}
    while time.monotonic() < deadline:
        last = {
            escrow_id: _observation_projection(database, escrow_id)
            for escrow_id in escrow_ids
        }
        if all(item["observation"]["state"] == "refunded" for item in last.values()):
            return last
        time.sleep(0.1)
    raise AssertionError(f"legacy refunds did not complete after restart: {last}")


def _observation_projection(database: Path, escrow_id: str) -> dict[str, Any]:
    with contextlib.closing(
        sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    ) as connection:
        connection.row_factory = sqlite3.Row
        observation = connection.execute(
            "SELECT * FROM board_legacy_posting_observations WHERE escrow_id=?",
            (escrow_id,),
        ).fetchone()
        assert observation is not None
        operation_id = str(observation["refund_operation_id"])

        def rows(
            statement: str, parameters: tuple[object, ...]
        ) -> list[dict[str, Any]]:
            return [
                dict(row)
                for row in connection.execute(statement, parameters).fetchall()
            ]

        issuance = connection.execute(
            "SELECT COALESCE(SUM(amount),0) FROM bank_operation_legs l "
            "JOIN bank_operation_receipts r ON r.operation_id=l.operation_id "
            "WHERE r.operation_kind='account_issuance' AND l.direction='credit'"
        ).fetchone()
        balances = connection.execute(
            "SELECT COALESCE(SUM(balance),0) FROM bank_accounts"
        ).fetchone()
        locked = connection.execute(
            "SELECT COALESCE(SUM(amount),0) FROM bank_escrow WHERE status='locked'"
        ).fetchone()
        assert issuance is not None and balances is not None and locked is not None
        return {
            "observation": dict(observation),
            "escrow": dict(
                connection.execute(
                    "SELECT * FROM bank_escrow WHERE escrow_id=?", (escrow_id,)
                ).fetchone()
            ),
            "receipt": rows(
                "SELECT * FROM bank_operation_receipts WHERE operation_id=?",
                (operation_id,),
            ),
            "legs": rows(
                "SELECT * FROM bank_operation_legs WHERE operation_id=?",
                (operation_id,),
            ),
            "transactions": rows(
                "SELECT * FROM bank_transactions WHERE type='escrow_release' AND reference=?",
                (escrow_id,),
            ),
            "events": rows(
                "SELECT * FROM events WHERE event_id IN "
                "(SELECT event_id FROM bank_operation_receipts WHERE operation_id=?)",
                (operation_id,),
            ),
            "conservation": {
                "issuance": int(issuance[0]),
                "balances": int(balances[0]),
                "locked": int(locked[0]),
                "audit_clean": audit_money(connection).financially_clean,
            },
        }


def _history_projection(database: Path, history: Any) -> dict[str, Any]:
    pending = {
        orphan.escrow_id: _observation_projection(database, orphan.escrow_id)
        for orphan in history.pending_orphans
    }
    already_refunded = _observation_projection(
        database, history.refunded_orphan.escrow_id
    )
    with contextlib.closing(
        sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    ) as connection:
        connection.row_factory = sqlite3.Row

        def row(statement: str, parameters: tuple[object, ...]) -> dict[str, Any]:
            result = connection.execute(statement, parameters).fetchone()
            assert result is not None
            return dict(result)

        def count(statement: str) -> int:
            result = connection.execute(statement).fetchone()
            assert result is not None
            return int(result[0])

        def rows(
            statement: str, parameters: tuple[object, ...]
        ) -> list[dict[str, Any]]:
            return [
                dict(result) for result in connection.execute(statement, parameters)
            ]

        immutable_observations = {
            str(result["observation_id"]): {
                field: result[field] for field in _IMMUTABLE_OBSERVATION_FIELDS
            }
            for result in connection.execute(
                "SELECT * FROM board_legacy_posting_observations ORDER BY observation_id"
            ).fetchall()
        }
        balances = count("SELECT COALESCE(SUM(balance),0) FROM bank_accounts")
        locked = count(
            "SELECT COALESCE(SUM(amount),0) FROM bank_escrow WHERE status='locked'"
        )
        return {
            "pending": pending,
            "winner_task": row(
                "SELECT * FROM board_tasks WHERE task_id=?", (history.winner.task_id,)
            ),
            "winner_escrow": row(
                "SELECT * FROM bank_escrow WHERE escrow_id=?",
                (history.winner.escrow_id,),
            ),
            "winner_lock_receipt": rows(
                "SELECT * FROM bank_operation_receipts WHERE operation_id=?",
                (history.winner.operation_id,),
            ),
            "winner_lock_legs": rows(
                "SELECT * FROM bank_operation_legs WHERE operation_id=? ORDER BY leg_index",
                (history.winner.operation_id,),
            ),
            "winner_lock_transactions": rows(
                "SELECT t.* FROM bank_transactions t JOIN bank_operation_legs l "
                "ON l.transaction_id=t.tx_id WHERE l.operation_id=? ORDER BY l.leg_index",
                (history.winner.operation_id,),
            ),
            "winner_lock_events": rows(
                "SELECT e.* FROM events e JOIN bank_operation_receipts r "
                "ON r.event_id=e.event_id WHERE r.operation_id=?",
                (history.winner.operation_id,),
            ),
            "winner_task_events": rows(
                "SELECT * FROM events WHERE event_source='task-board' "
                "AND event_type='task.created' AND task_id=? ORDER BY event_id",
                (history.winner.task_id,),
            ),
            "already_refunded": already_refunded,
            "immutable_observations": immutable_observations,
            "global": {
                "receipt_count": count("SELECT COUNT(*) FROM bank_operation_receipts"),
                "leg_count": count("SELECT COUNT(*) FROM bank_operation_legs"),
                "transaction_count": count("SELECT COUNT(*) FROM bank_transactions"),
                "event_count": count("SELECT COUNT(*) FROM events"),
                "settlement_event_count": count(
                    "SELECT COUNT(*) FROM events WHERE event_source='central-bank' "
                    "AND event_type IN ('escrow.released','escrow.split')"
                ),
                "task_count": count("SELECT COUNT(*) FROM board_tasks"),
                "escrow_count": count("SELECT COUNT(*) FROM bank_escrow"),
                "account_count": count("SELECT COUNT(*) FROM bank_accounts"),
                "balances": balances,
                "locked": locked,
                "economic_total": balances + locked,
                "audit_clean": audit_money(connection).financially_clean,
            },
        }


def _assert_complete_history(before: dict[str, Any], final: dict[str, Any]) -> None:
    for key in (
        "winner_task",
        "winner_escrow",
        "winner_lock_receipt",
        "winner_lock_legs",
        "winner_lock_transactions",
        "winner_lock_events",
        "winner_task_events",
    ):
        assert final[key] == before[key]
    assert final["immutable_observations"] == before["immutable_observations"]
    for key in ("observation", "escrow", "receipt", "legs", "transactions", "events"):
        assert final["already_refunded"][key] == before["already_refunded"][key]

    refund_count = len(before["pending"])
    before_global = before["global"]
    final_global = final["global"]
    assert (
        final_global["receipt_count"] == before_global["receipt_count"] + refund_count
    )
    assert final_global["leg_count"] == before_global["leg_count"] + refund_count
    assert (
        final_global["transaction_count"]
        == before_global["transaction_count"] + refund_count
    )
    assert (
        final_global["settlement_event_count"]
        == before_global["settlement_event_count"] + refund_count
    )
    assert final_global["event_count"] >= before_global["event_count"] + refund_count
    for key in ("task_count", "escrow_count", "account_count", "economic_total"):
        assert final_global[key] == before_global[key]
    assert before_global["audit_clean"] is True
    assert final_global["audit_clean"] is True


def _assert_boundary(
    phase: _Phase,
    before: dict[str, Any],
    boundary: dict[str, Any],
) -> None:
    assert boundary["observation"]["state"] == "refund_pending"
    if phase == "refund-intent":
        assert boundary == before
    else:
        assert boundary["escrow"]["status"] == "released"
        assert len(boundary["receipt"]) == 1
        assert len(boundary["legs"]) == 1
        assert len(boundary["transactions"]) == 1
        assert len(boundary["events"]) == 1
        assert boundary["conservation"]["audit_clean"] is True


def _assert_refund_once(before: dict[str, Any], final: dict[str, Any]) -> None:
    assert {
        key: final["observation"][key] for key in _IMMUTABLE_OBSERVATION_FIELDS
    } == {key: before["observation"][key] for key in _IMMUTABLE_OBSERVATION_FIELDS}
    assert final["observation"]["state"] == "refunded"
    assert final["escrow"]["status"] == "released"
    assert len(final["receipt"]) == 1
    assert len(final["legs"]) == 1
    assert len(final["transactions"]) == 1
    assert len(final["events"]) == 1
    receipt = final["receipt"][0]
    transaction = final["transactions"][0]
    event = final["events"][0]
    assert receipt["operation_id"] == final["observation"]["refund_operation_id"]
    assert receipt["request_sha256"] == final["observation"]["refund_request_sha256"]
    assert receipt["event_id"] == final["observation"]["refund_receipt_event_id"]
    assert final["legs"][0]["account_id"] == final["observation"]["poster_id"]
    assert final["legs"][0]["amount"] == final["observation"]["amount"]
    assert transaction["tx_id"] == final["legs"][0]["transaction_id"]
    assert transaction["reference"] == final["observation"]["escrow_id"]
    assert transaction["event_id"] == receipt["event_id"]
    assert event["event_source"] == "central-bank"
    assert event["event_type"] == "escrow.released"
    assert event["task_id"] == final["observation"]["task_id"]
    assert event["agent_id"] == final["observation"]["poster_id"]
    assert final["observation"]["error_json"] is None
    assert final["conservation"]["audit_clean"] is True
    assert (
        final["conservation"]["balances"] + final["conservation"]["locked"]
        == before["conservation"]["balances"] + before["conservation"]["locked"]
    )


def _assert_protected_diagnostic(
    runtime: IsolatedRuntime, final: dict[str, Any]
) -> None:
    observation_id = str(final["observation"]["observation_id"])
    path = f"/board/legacy-posting-observations/{observation_id}"
    unsigned = httpx.get(f"{runtime.service_url('db-gateway')}{path}", timeout=2.0)
    assert unsigned.status_code == 401
    assert unsigned.json()["error"] == "invalid_authorization"

    config = yaml.safe_load(
        runtime.config_paths["task-board"].read_text(encoding="utf-8")
    )
    signing = config["db_gateway"]["signing"]
    signer = GatewayRequestSigner(
        GatewaySignerConfig.model_validate(signing),
        config_dir=runtime.config_paths["task-board"].parent,
    )
    request = httpx.Request("GET", f"{runtime.service_url('db-gateway')}{path}")
    with httpx.Client(timeout=2.0) as client:
        response = client.send(
            signer.sign_request(request, action="gateway.board.write")
        )
    assert response.status_code == 200
    envelope = response.json()
    assert envelope["observation_state"] == "refunded"
    assert envelope["observation_id"] == observation_id
    assert (
        envelope["observed_fact_sha256"] == final["observation"]["observed_fact_sha256"]
    )
    assert envelope["refund_receipt_event_id"] == final["receipt"][0]["event_id"]


def _process_evidence(board: _OwnedBoard | None) -> dict[str, Any] | None:
    if board is None:
        return None
    return {
        "launcher_pid": board.process.pid,
        "actor_pid": board.actor_pid,
        "pgid": board.pgid,
        "sid": board.sid,
        "returncode": board.process.returncode,
        "group_drained": board.drained,
    }
