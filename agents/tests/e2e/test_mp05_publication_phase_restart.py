"""MP-05 process-death proof at admission and publication commit boundaries."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import sqlite3
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import httpx
import pytest

from tests.e2e.test_mp05_late_bank_request import _start_gated_child, _start_sentinel
from tests.e2e.test_mp05_posting_server_restart import (
    _DEPENDENCY_SERVICES,
    _alive,
    _child_audit,
    _make_funded_agent,
    _ReservedPort,
    _terminate_owned_child,
    _wait_for_health,
    _write_child_config,
    _write_durable_json,
)
from tests.e2e.test_mp07_runtime_crash_replay import _kill_and_reap, _OwnedChild, _wait_for_gate

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.isolated_test_runtime import IsolatedRuntime  # noqa: E402

if TYPE_CHECKING:
    from service_auth.task_postings import TaskPostingIntent, TaskPostingResponse

    from base_agent.agent import BaseAgent
    from base_agent.platform import PlatformAgent

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

_INITIAL_BALANCE = 500
_REWARD = 100
_BIDDING_SECONDS = 3_600
_ACCEPTANCE_SECONDS = 3_600
_EXECUTION_SECONDS = 7_200
_REVIEW_SECONDS = 3_600
_GATE_TIMEOUT_SECONDS = 30.0
_TASK_BOARD_ROOT = _REPO_ROOT / "services" / "task-board"
_PHASE_SERVER = _TASK_BOARD_ROOT / "tests" / "integration" / "mp05_publication_phase_server.py"
_Phase = Literal["admission-commit", "publication-commit"]


# This module owns its complete disposable stack, so ordinary-runtime probes must
# not run before the isolated services exist.
@pytest.fixture(scope="session", autouse=True)
def _require_identity_service() -> None:
    pass


@pytest.fixture(scope="session", autouse=True)
def _require_bank_service() -> None:
    pass


@pytest.fixture(scope="session", autouse=True)
def _require_task_board_service() -> None:
    pass


@pytest.fixture(scope="session", autouse=True)
def _require_reputation_service() -> None:
    pass


@pytest.fixture(scope="session", autouse=True)
def _require_court_service() -> None:
    pass


def _start_board_child(
    *,
    mode: _Phase | Literal["normal"],
    config_path: Path,
    reservation: _ReservedPort,
    gate_path: Path | None,
    log_path: Path,
) -> _OwnedChild:
    command = [
        "uv",
        "run",
        "--no-sync",
        "python",
        str(_PHASE_SERVER),
        "--mode",
        mode,
        "--fd",
        str(reservation.socket.fileno()),
    ]
    if gate_path is not None:
        command.extend(("--gate", str(gate_path)))
    return _start_gated_child(
        command,
        cwd=_TASK_BOARD_ROOT,
        environment={**os.environ, "CONFIG_PATH": str(config_path)},
        reservation=reservation,
        log_path=log_path,
    )


def _read_projection(
    runtime: IsolatedRuntime,
    intent: TaskPostingIntent,
) -> dict[str, list[dict[str, Any]]]:
    database = runtime.run_dir / "data" / "economy.db"
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row

        def rows(statement: str, parameters: tuple[object, ...] = ()) -> list[dict[str, Any]]:
            return [dict(row) for row in connection.execute(statement, parameters).fetchall()]

        return {
            "publications": rows(
                "SELECT * FROM board_task_publications WHERE operation_id=?",
                (intent.operation_id,),
            ),
            "tasks": rows("SELECT * FROM board_tasks WHERE task_id=?", (intent.task_id,)),
            "escrows": rows("SELECT * FROM bank_escrow WHERE task_id=?", (intent.task_id,)),
            "lock_receipts": rows(
                "SELECT * FROM bank_operation_receipts WHERE operation_id=?",
                (intent.lock_operation_id,),
            ),
            "lock_legs": rows(
                "SELECT * FROM bank_operation_legs WHERE operation_id=?",
                (intent.lock_operation_id,),
            ),
            "lock_debits": rows(
                "SELECT * FROM bank_transactions WHERE type='escrow_lock' AND reference=?",
                (intent.task_id,),
            ),
            "lock_events": rows(
                "SELECT * FROM events WHERE event_source='central-bank' "
                "AND event_type='escrow.locked' AND task_id=?",
                (intent.task_id,),
            ),
            "creation_events": rows(
                "SELECT * FROM events WHERE event_source='task-board' "
                "AND event_type='task.created' AND task_id=?",
                (intent.task_id,),
            ),
            "all_publications": rows("SELECT * FROM board_task_publications"),
            "all_tasks": rows("SELECT * FROM board_tasks"),
            "all_escrows": rows("SELECT * FROM bank_escrow"),
            "all_lock_receipts": rows(
                "SELECT * FROM bank_operation_receipts WHERE operation_kind='escrow_lock'"
            ),
            "all_lock_legs": rows(
                "SELECT l.* FROM bank_operation_legs l JOIN bank_operation_receipts r "
                "ON r.operation_id=l.operation_id WHERE r.operation_kind='escrow_lock'"
            ),
            "all_lock_debits": rows("SELECT * FROM bank_transactions WHERE type='escrow_lock'"),
            "all_lock_events": rows(
                "SELECT * FROM events WHERE event_source='central-bank' "
                "AND event_type='escrow.locked'"
            ),
            "all_creation_events": rows(
                "SELECT * FROM events WHERE event_source='task-board' AND event_type='task.created'"
            ),
            "settlement_receipts": rows(
                "SELECT * FROM bank_operation_receipts "
                "WHERE operation_kind IN ('escrow_release','escrow_split')"
            ),
            "settlement_legs": rows(
                "SELECT l.* FROM bank_operation_legs l JOIN bank_operation_receipts r "
                "ON r.operation_id=l.operation_id "
                "WHERE r.operation_kind IN ('escrow_release','escrow_split')"
            ),
            "settlement_transactions": rows(
                "SELECT * FROM bank_transactions WHERE type IN ('escrow_release','escrow_split')"
            ),
            "settlement_events": rows(
                "SELECT * FROM events WHERE event_source='central-bank' "
                "AND event_type IN ('escrow.released','escrow.split')"
            ),
            "accounts": rows("SELECT * FROM bank_accounts"),
            "issuance_legs": rows(
                "SELECT l.* FROM bank_operation_legs l JOIN bank_operation_receipts r "
                "ON r.operation_id=l.operation_id WHERE r.operation_kind='account_issuance'"
            ),
        }


def _assert_pending_before_funding(
    projection: dict[str, list[dict[str, Any]]],
    intent: TaskPostingIntent,
) -> None:
    assert len(projection["publications"]) == 1
    publication = projection["publications"][0]
    assert publication["operation_id"] == intent.operation_id
    assert publication["task_id"] == intent.task_id
    assert publication["lock_operation_id"] == intent.lock_operation_id
    assert publication["request_sha256"] == intent.request_sha256
    assert publication["state"] == "awaiting_lock"
    assert publication["escrow_id"] is None
    assert projection["tasks"] == []
    assert projection["escrows"] == []
    assert projection["lock_receipts"] == []
    assert projection["lock_legs"] == []
    assert projection["lock_debits"] == []
    assert projection["lock_events"] == []
    assert projection["creation_events"] == []
    assert len(projection["all_publications"]) == 1
    assert projection["all_tasks"] == []
    assert projection["all_escrows"] == []
    assert projection["all_lock_receipts"] == []
    assert projection["all_lock_legs"] == []
    assert projection["all_lock_debits"] == []
    assert projection["all_lock_events"] == []
    assert projection["all_creation_events"] == []
    assert projection["settlement_receipts"] == []
    assert projection["settlement_legs"] == []
    assert projection["settlement_transactions"] == []
    assert projection["settlement_events"] == []


def _assert_exact_funded_publication(
    projection: dict[str, list[dict[str, Any]]],
    intent: TaskPostingIntent,
    frozen: dict[str, Any],
) -> dict[str, int]:
    for key in (
        "publications",
        "tasks",
        "escrows",
        "lock_receipts",
        "lock_legs",
        "lock_debits",
        "lock_events",
        "creation_events",
        "all_publications",
        "all_tasks",
        "all_escrows",
        "all_lock_receipts",
        "all_lock_legs",
        "all_lock_debits",
        "all_lock_events",
        "all_creation_events",
    ):
        assert len(projection[key]) == 1, key
    assert projection["settlement_receipts"] == []
    assert projection["settlement_legs"] == []
    assert projection["settlement_transactions"] == []
    assert projection["settlement_events"] == []

    publication = projection["publications"][0]
    task = projection["tasks"][0]
    escrow = projection["escrows"][0]
    receipt = projection["lock_receipts"][0]
    leg = projection["lock_legs"][0]
    debit = projection["lock_debits"][0]
    creation_event = projection["creation_events"][0]
    assert publication["state"] == "published"
    assert publication["operation_id"] == intent.operation_id
    assert publication["task_id"] == intent.task_id
    assert publication["lock_operation_id"] == intent.lock_operation_id
    assert publication["request_sha256"] == intent.request_sha256
    assert json.loads(publication["result_json"]) == frozen["result"]
    assert task["task_id"] == intent.task_id
    assert task["poster_id"] == intent.actor_id
    assert task["title"] == intent.title
    assert task["spec"] == intent.spec
    assert task["reward"] == intent.reward
    assert task["status"] == "open"
    assert task["escrow_id"] == frozen["escrow_id"] == escrow["escrow_id"]
    assert receipt["operation_id"] == intent.lock_operation_id
    assert receipt["operation_kind"] == "escrow_lock"
    assert receipt["source_reference"] == intent.task_id
    assert publication["lock_receipt_event_id"] == receipt["event_id"]
    assert leg["operation_id"] == intent.lock_operation_id
    assert leg["role"] == "payer"
    assert leg["account_id"] == intent.actor_id
    assert leg["direction"] == "debit"
    assert leg["amount"] == intent.reward
    assert leg["transaction_id"] == debit["tx_id"]
    assert debit["reference"] == intent.task_id
    assert debit["amount"] == intent.reward
    assert debit["balance_after"] == _INITIAL_BALANCE - _REWARD
    assert escrow["payer_account_id"] == intent.actor_id
    assert escrow["task_id"] == intent.task_id
    assert escrow["amount"] == intent.reward
    assert escrow["status"] == "locked"
    assert escrow["resolved_at"] is None
    assert creation_event["event_id"] == frozen["result"]["event_id"]

    total_issuance = sum(int(row["amount"]) for row in projection["issuance_legs"])
    total_balances = sum(int(row["balance"]) for row in projection["accounts"])
    total_locked = sum(
        int(row["amount"]) for row in projection["all_escrows"] if row["status"] == "locked"
    )
    assert total_issuance == _INITIAL_BALANCE
    assert total_issuance == total_balances + total_locked
    return {
        "total_initial_issuance": total_issuance,
        "total_account_balances": total_balances,
        "total_locked_escrow": total_locked,
    }


async def _run_phase_restart_case(tmp_path: Path, phase: _Phase) -> None:  # noqa: PLR0912, PLR0915
    # Construction has a temporary cleanup owner until every setup artifact is
    # valid. Only then is ownership transferred to the scenario's audited finally.
    with contextlib.ExitStack() as setup:
        runtime = IsolatedRuntime.create(
            repo_root=_REPO_ROOT,
            artifacts_root=tmp_path / "publication-phase-runtime",
            services=_DEPENDENCY_SERVICES,
        )
        setup.callback(runtime.stop)
        reservation = _ReservedPort()
        setup.callback(reservation.close)
        board_url = f"http://127.0.0.1:{reservation.port}"
        case_root = runtime.run_dir / phase
        case_root.mkdir(mode=0o700)
        gate_path = case_root / "commit-gate.json"
        audit_path = case_root / "process-audit.json"
        crash_log = case_root / "crash-server.log"
        restart_log = case_root / "restart-server.log"
        sentinel = _start_sentinel(case_root)
        setup.callback(_terminate_owned_child, sentinel)
        setup.pop_all()

    crash_child: _OwnedChild | None = None
    restart_child: _OwnedChild | None = None
    platform: PlatformAgent | None = None
    agent: BaseAgent | None = None
    posting: asyncio.Task[TaskPostingResponse] | None = None
    crash_actor_pid: int | None = None
    restart_actor_pid: int | None = None
    crash_signal: int | None = None
    restart_signal: int | None = None
    sentinel_after_crash = False
    sentinel_after_children_cleanup = False
    evidence: dict[str, Any] = {"phase": phase}
    primary: BaseException | None = None
    cleanup_failures: list[BaseException] = []
    try:
        runtime.start()
        config_path = _write_child_config(runtime, case_root / "board", reservation.port)
        platform, agent = await _make_funded_agent(runtime, board_url)
        assert agent.agent_id is not None
        crash_child = _start_board_child(
            mode=phase,
            config_path=config_path,
            reservation=reservation,
            gate_path=gate_path,
            log_path=crash_log,
        )
        await _wait_for_health(crash_child, board_url, crash_log)
        intent = agent.prepare_task_posting(
            title=f"MP-05 {phase} restart proof",
            spec="One prepared publication survives this exact server commit boundary.",
            reward=_REWARD,
            bidding_deadline_seconds=_BIDDING_SECONDS,
            acceptance_window_seconds=_ACCEPTANCE_SECONDS,
            execution_deadline_seconds=_EXECUTION_SECONDS,
            review_deadline_seconds=_REVIEW_SECONDS,
        )
        posting = asyncio.create_task(agent.submit_task_posting(intent))
        gate = await asyncio.to_thread(
            _wait_for_gate,
            crash_child,
            gate_path,
            timeout_seconds=_GATE_TIMEOUT_SECONDS,
        )
        crash_actor_pid = int(gate["pid"])
        crash_child.bind_actor(crash_actor_pid)
        assert gate["phase"] == phase.replace("-", "_") + "ted"
        before_kill = _read_projection(runtime, intent)
        if phase == "admission-commit":
            assert gate["status_code"] == 202
            assert gate["publication"]["publication_state"] == "awaiting_lock"
            _assert_pending_before_funding(before_kill, intent)
            balance = await agent.get_balance()
            transactions = await agent.get_transactions()
            assert balance["balance"] == _INITIAL_BALANCE
            assert [
                row
                for row in transactions
                if row["type"] == "escrow_lock" and row["reference"] == intent.task_id
            ] == []
        else:
            assert gate["status_code"] == 201
            assert gate["publication"]["publication_state"] == "published"
            _assert_exact_funded_publication(before_kill, intent, gate["publication"])

        crash_signal = int(signal.SIGKILL)
        await asyncio.to_thread(_kill_and_reap, crash_child, crash_actor_pid)
        failed_request = await asyncio.gather(posting, return_exceptions=True)
        assert len(failed_request) == 1
        assert isinstance(failed_request[0], httpx.HTTPError), failed_request
        sentinel_after_crash = _alive(sentinel.actor_pid)
        assert sentinel_after_crash

        restart_reservation = _ReservedPort(reservation.port)
        restart_child = _start_board_child(
            mode="normal",
            config_path=config_path,
            reservation=restart_reservation,
            gate_path=None,
            log_path=restart_log,
        )
        restart_actor_pid = restart_child.actor_pid
        await _wait_for_health(restart_child, board_url, restart_log)
        replay = await agent.submit_task_posting(intent)
        assert replay.status_code == 201
        assert replay.body["publication_state"] == "published"
        status = await agent.get_task_posting(intent.operation_id)
        assert status.status_code == 200
        assert status.body == replay.body
        if phase == "publication-commit":
            assert replay.body == gate["publication"]

        final_projection = _read_projection(runtime, intent)
        conservation = _assert_exact_funded_publication(final_projection, intent, replay.body)
        summaries = [task for task in await agent.list_tasks() if task["task_id"] == intent.task_id]
        assert len(summaries) == 1
        task = await agent.get_task(intent.task_id)
        assert task["task_id"] == intent.task_id
        assert task["escrow_id"] == replay.body["escrow_id"]
        assert (await agent.get_balance())["balance"] == _INITIAL_BALANCE - _REWARD
        assert _alive(sentinel.actor_pid)
        evidence.update(
            {
                "gate": gate,
                "before_kill": before_kill,
                "replay": replay.body,
                "final_projection": final_projection,
                "conservation": conservation,
            }
        )
    except BaseException as exc:
        primary = exc
    finally:
        if posting is not None and not posting.done():
            posting.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await posting
        for child_phase, child in (("crash", crash_child), ("restart", restart_child)):
            if child is None or child.drained:
                continue
            try:
                _terminate_owned_child(child)
                if child_phase == "restart":
                    restart_signal = int(signal.SIGKILL)
            except BaseException as exc:
                cleanup_failures.append(exc)
        sentinel_after_children_cleanup = _alive(sentinel.actor_pid)
        if not sentinel_after_children_cleanup:
            cleanup_failures.append(
                AssertionError("unrelated sentinel was terminated by Board-child cleanup")
            )
        try:
            _terminate_owned_child(sentinel)
        except BaseException as exc:
            cleanup_failures.append(exc)
        for closeable in (agent, platform):
            if closeable is None:
                continue
            try:
                await closeable.close()
            except BaseException as exc:
                cleanup_failures.append(exc)
        try:
            reservation.close()
        except BaseException as exc:
            cleanup_failures.append(exc)
        try:
            runtime.stop()
        except BaseException as exc:
            cleanup_failures.append(exc)
        audit = {
            "runtime_id": runtime.run_id,
            "runtime_dir": str(runtime.run_dir),
            "phase": phase,
            "board_children": [
                _child_audit("crash", crash_child, crash_actor_pid, crash_signal),
                _child_audit("restart", restart_child, restart_actor_pid, restart_signal),
            ],
            "sentinel": {
                "launcher_pid": sentinel.process.pid,
                "actor_pid": sentinel.actor_pid,
                "pgid": sentinel.pgid,
                "sid": sentinel.sid,
                "survived_crash": sentinel_after_crash,
                "survived_board_cleanup": sentinel_after_children_cleanup,
                "cleanup_signal": int(signal.SIGKILL),
                "returncode": sentinel.process.returncode,
                "group_drained": sentinel.drained,
            },
            "evidence": evidence,
            "logs": {
                "crash": crash_log.read_text(encoding="utf-8") if crash_log.exists() else None,
                "restart": restart_log.read_text(encoding="utf-8")
                if restart_log.exists()
                else None,
            },
        }
        try:
            _write_durable_json(audit_path, audit)
            print(json.dumps({"mp05_process_audit": str(audit_path)}, sort_keys=True))
        except BaseException as exc:
            cleanup_failures.append(exc)

    if primary is not None and not cleanup_failures:
        raise primary
    failures = ([primary] if primary is not None else []) + cleanup_failures
    if failures:
        raise BaseExceptionGroup("MP-05 publication phase restart proof failed", failures)


async def test_gateway_admission_commit_survives_task_board_sigkill(tmp_path: Path) -> None:
    """Admission is durable/private/unfunded, then the same intent publishes after restart."""
    await _run_phase_restart_case(tmp_path, "admission-commit")


async def test_publication_commit_survives_lost_http_ack_and_sigkill(tmp_path: Path) -> None:
    """A committed publication replays its exact frozen result after the lost response."""
    await _run_phase_restart_case(tmp_path, "publication-commit")
