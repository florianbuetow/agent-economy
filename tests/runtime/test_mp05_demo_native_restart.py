"""MP-05 real-process restart proof for the five MP-07 demo mutations."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import signal
import sqlite3
import subprocess
import time
import uuid
from builtins import BaseExceptionGroup
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import pytest
import yaml
from scripts.isolated_test_runtime import (
    IsolatedRuntime,
    _drain_owned_process_group,
    _release_owned_launch,
    _start_owned_launch,
)

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_ROOT = REPO_ROOT / "tools"
CRASH_RUNNER = Path(__file__).with_name("mp05_demo_native_crash_runner.py")
TIMEOUT_SECONDS = 60.0
FUNDING_AMOUNT = 500
REWARD = 100
# Full-budget price: approval stays a whole release and a ruling the Court's
# percentage split; accepted-bid pricing has its own Gateway and e2e coverage.
BID_AMOUNT = REWARD
TARGET_EVENT = {
    "bid": "bid.submitted",
    "accept_bid": "task.accepted",
    "submit_deliverable": "task.submitted",
    "approve": "task.approved",
    "dispute": "task.disputed",
}
TARGET_COMMAND = {
    "bid": "submit_bid",
    "accept_bid": "accept_bid",
    "submit_deliverable": "submit_deliverable",
    "approve": "approve_task",
    "dispute": "dispute_task",
}
PROJECTION_TABLES = (
    "identity_agents",
    "bank_accounts",
    "bank_transactions",
    "bank_escrow",
    "bank_operation_receipts",
    "bank_operation_legs",
    "board_tasks",
    "board_task_publications",
    "board_bids",
    "board_artifact_uploads",
    "board_task_operations",
    "court_claims",
    "events",
)


@dataclass
class _OwnedChild:
    process: subprocess.Popen[bytes]
    pgid: int
    sid: int
    actor_pid: int | None = None
    drained: bool = False

    def bind_actor(self, actor_pid: int) -> None:
        if actor_pid <= 0:
            raise RuntimeError("Demo CLI actor PID must be positive")
        if os.getpgid(actor_pid) != self.pgid or os.getsid(actor_pid) != self.sid:
            raise RuntimeError("Demo CLI actor is outside the owned process group")
        self.actor_pid = actor_pid


@dataclass(slots=True)
class _LaunchGate:
    descriptor: int | None

    def release(self) -> None:
        descriptor = self.descriptor
        if descriptor is None:
            raise RuntimeError("Native demo launch gate is no longer owned")
        try:
            _release_owned_launch(descriptor)
        finally:
            self.descriptor = None

    def close(self) -> None:
        descriptor = self.descriptor
        if descriptor is None:
            return
        self.descriptor = None
        os.close(descriptor)


@dataclass(slots=True)
class _Harness:
    runtime: IsolatedRuntime
    config_path: Path
    state_directory: Path
    sentinel: _OwnedChild


def _group_exists(child: _OwnedChild) -> bool:
    try:
        os.killpg(child.pgid, 0)
    except ProcessLookupError:
        return False
    return True


def _terminate_owned(child: _OwnedChild) -> None:
    if child.drained:
        return
    _drain_owned_process_group(
        child.process,
        pgid=child.pgid,
        sid=child.sid,
    )
    child.drained = True


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
    primary: BaseException | None,
    cleanup_failures: list[BaseException],
    evidence_path: Path,
) -> None:
    failures = ([] if primary is None else [primary]) + cleanup_failures
    if len(failures) == 1:
        raise failures[0]
    if failures:
        raise BaseExceptionGroup(
            f"MP05 native restart proof failed; evidence: {evidence_path}", failures
        )


def _launch(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    log_path: Path,
    ownership: contextlib.ExitStack,
    cleanup_failures: list[BaseException],
) -> _OwnedChild:
    with log_path.open("wb") as log:
        process, pgid, sid, descriptor = _start_owned_launch(
            command,
            cwd=str(cwd),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            pass_fds=(),
        )
        child = _OwnedChild(process=process, pgid=pgid, sid=sid)
        ownership.callback(_capture_cleanup, cleanup_failures, _terminate_owned, child)
        gate = _LaunchGate(descriptor)
        ownership.callback(_capture_cleanup, cleanup_failures, gate.close)
        gate.release()
    return child


def _start_sentinel(
    root: Path,
    *,
    ownership: contextlib.ExitStack,
    cleanup_failures: list[BaseException],
) -> _OwnedChild:
    child = _launch(
        ["/bin/sleep", "600"],
        cwd=root,
        env=dict(os.environ),
        log_path=root / "sentinel.log",
        ownership=ownership,
        cleanup_failures=cleanup_failures,
    )
    child.bind_actor(child.process.pid)
    return child


@pytest.fixture(scope="module")
def native_harness(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Harness]:
    artifacts = tmp_path_factory.mktemp("mp05-demo-native-restart", numbered=True)
    runtime = IsolatedRuntime.create(repo_root=REPO_ROOT, artifacts_root=artifacts)
    ownership = contextlib.ExitStack()
    cleanup_failures: list[BaseException] = []
    ownership.callback(_capture_cleanup, cleanup_failures, runtime.stop)
    sentinel: _OwnedChild | None = None
    primary: BaseException | None = None
    try:
        sentinel = _start_sentinel(
            runtime.run_dir,
            ownership=ownership,
            cleanup_failures=cleanup_failures,
        )
        runtime.start()
        config = yaml.safe_load((TOOLS_ROOT / "config.yaml").read_text(encoding="utf-8"))
        config["platform"].update(
            {
                "identity_url": runtime.service_url("identity"),
                "bank_url": runtime.service_url("central-bank"),
                "task_board_url": runtime.service_url("task-board"),
                "reputation_url": runtime.service_url("reputation"),
                "court_url": runtime.service_url("court"),
                "platform_key_path": "../keys/platform.key",
            }
        )
        config["demo_replay"] = {
            "environment_id": runtime.run_id,
            "state_directory": "../demo-native/state",
            "max_record_bytes": 1_048_576,
            "diagnostic_limit": 16,
        }
        config_path = runtime.run_dir / "configs" / "demo-native-tools.yaml"
        config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        yield _Harness(
            runtime=runtime,
            config_path=config_path,
            state_directory=runtime.run_dir / "demo-native" / "state",
            sentinel=sentinel,
        )
    except BaseException as exc:
        primary = exc
    finally:
        ownership.close()
    _raise_failures(primary, cleanup_failures, artifacts / "fixture-cleanup.json")


def _steps(action: str, run_id: str) -> list[dict[str, object]]:
    steps: list[dict[str, object]] = [
        {"action": "register", "agent": "poster"},
        {"action": "register", "agent": "worker"},
        {
            "action": "fund",
            "agent": "poster",
            "amount": FUNDING_AMOUNT,
            "reference": f"mp05:native:{run_id}:fund:poster",
        },
        {
            "action": "post_task",
            "poster": "poster",
            "ref": "restart_task",
            "title": f"MP05 native {action}",
            "spec": "Prove one native mutation across a real process restart.",
            "reward": REWARD,
            "bidding_deadline_seconds": 3600,
            "acceptance_window_seconds": 300,
            "execution_deadline_seconds": 7200,
            "review_deadline_seconds": 3600,
        },
        {
            "action": "bid",
            "bidder": "worker",
            "task_ref": "restart_task",
            "amount": BID_AMOUNT,
        },
    ]
    if action == "bid":
        return steps
    steps.append(
        {
            "action": "accept_bid",
            "poster": "poster",
            "bidder": "worker",
            "task_ref": "restart_task",
        }
    )
    if action == "accept_bid":
        return steps
    steps.extend(
        (
            {
                "action": "upload_asset",
                "worker": "worker",
                "task_ref": "restart_task",
                "filename": "restart-proof.txt",
                "content": f"real upload for {action}",
            },
            {
                "action": "submit_deliverable",
                "worker": "worker",
                "task_ref": "restart_task",
            },
        )
    )
    if action == "submit_deliverable":
        return steps
    steps.append(
        {
            "action": action,
            "poster": "poster",
            "task_ref": "restart_task",
            **(
                {"reason": "The uploaded proof does not satisfy the task."}
                if action == "dispute"
                else {}
            ),
        }
    )
    return steps


def _write_scenario(case: Path, action: str, run_id: str) -> tuple[Path, list[dict[str, object]]]:
    steps = _steps(action, run_id)
    scenario = {
        "name": f"MP-05 native restart: {action}",
        "description": "Short real-process restart acceptance proof.",
        "default_delay": 0,
        "agents": [
            {"handle": "poster", "name": f"MP05 poster {run_id}"},
            {"handle": "worker", "name": f"MP05 worker {run_id}"},
        ],
        "steps": steps,
    }
    path = case / "scenario.yaml"
    path.write_text(yaml.safe_dump(scenario, sort_keys=False), encoding="utf-8")
    return path, steps


def _environment(harness: _Harness) -> dict[str, str]:
    return {
        **harness.runtime.environment(),
        "TOOLS_CONFIG_PATH": str(harness.config_path),
    }


def _production_arguments(scenario_path: Path, run_id: str) -> list[str]:
    return [str(scenario_path), "--run-id", run_id, "--delay", "0"]


def _launch_crash(
    harness: _Harness,
    scenario_path: Path,
    run_id: str,
    action: str,
    phase: str,
    gate_path: Path,
    log_path: Path,
    *,
    ownership: contextlib.ExitStack,
    cleanup_failures: list[BaseException],
) -> _OwnedChild:
    return _launch(
        [
            "uv",
            "run",
            "--no-sync",
            "python",
            str(CRASH_RUNNER),
            "--action",
            action,
            "--phase",
            phase,
            "--gate",
            str(gate_path),
            "--",
            *_production_arguments(scenario_path, run_id),
        ],
        cwd=TOOLS_ROOT,
        env=_environment(harness),
        log_path=log_path,
        ownership=ownership,
        cleanup_failures=cleanup_failures,
    )


def _run_cli(
    harness: _Harness,
    scenario_path: Path,
    run_id: str,
    log_path: Path,
) -> int:
    ownership = contextlib.ExitStack()
    cleanup_failures: list[BaseException] = []
    primary: BaseException | None = None
    return_code: int | None = None
    try:
        child = _launch(
            [
                "uv",
                "run",
                "--no-sync",
                "python",
                "-m",
                "demo_replay",
                *_production_arguments(scenario_path, run_id),
            ],
            cwd=TOOLS_ROOT,
            env=_environment(harness),
            log_path=log_path,
            ownership=ownership,
            cleanup_failures=cleanup_failures,
        )
        return_code = child.process.wait(timeout=TIMEOUT_SECONDS)
        deadline = time.monotonic() + TIMEOUT_SECONDS
        while _group_exists(child) and time.monotonic() < deadline:
            time.sleep(0.01)
        if _group_exists(child):
            raise RuntimeError(f"Completed CLI process group {child.pgid} did not drain")
        child.drained = True
    except BaseException as exc:
        primary = exc
    finally:
        ownership.close()
    _raise_failures(primary, cleanup_failures, log_path)
    assert return_code is not None
    return return_code


def _wait_for_gate(child: _OwnedChild, gate_path: Path, log_path: Path) -> dict[str, Any]:
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if gate_path.exists():
            value = json.loads(gate_path.read_text(encoding="utf-8"))
            assert isinstance(value, dict)
            return value
        return_code = child.process.poll()
        if return_code is not None:
            raise AssertionError(
                f"Demo CLI exited before native mutation gate ({return_code}).\n"
                f"{log_path.read_text(encoding='utf-8')}"
            )
        time.sleep(0.05)
    raise TimeoutError(f"Demo CLI did not reach native mutation gate; see {log_path}")


def _kill_at_gate(child: _OwnedChild, gate: dict[str, Any]) -> None:
    actor_pid = gate["pid"]
    assert type(actor_pid) is int
    child.bind_actor(actor_pid)
    assert gate["pgid"] == child.pgid and gate["sid"] == child.sid
    os.kill(actor_pid, signal.SIGKILL)
    assert child.process.wait(timeout=TIMEOUT_SECONDS) != 0
    assert not _group_exists(child)
    child.drained = True


def _database_path(runtime: IsolatedRuntime) -> Path:
    config = yaml.safe_load(runtime.gateway_config_path.read_text(encoding="utf-8"))
    return Path(config["database"]["path"])


def _rows(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(f"SELECT * FROM {table}").fetchall()]


def _projection(runtime: IsolatedRuntime) -> dict[str, object]:
    with sqlite3.connect(f"file:{_database_path(runtime)}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        values: dict[str, object] = {
            table: _rows(connection, table) for table in PROJECTION_TABLES
        }
        issuance = connection.execute(
            "SELECT COALESCE(SUM(amount),0) FROM bank_transactions WHERE type='credit'"
        ).fetchone()
        balances = connection.execute(
            "SELECT COALESCE(SUM(balance),0) FROM bank_accounts"
        ).fetchone()
        locked = connection.execute(
            "SELECT COALESCE(SUM(amount),0) FROM bank_escrow WHERE status='locked'"
        ).fetchone()
        assert issuance is not None and balances is not None and locked is not None
        values["conservation"] = {
            "issuance": int(issuance[0]),
            "balances": int(balances[0]),
            "locked": int(locked[0]),
        }
        return values


def _new_rows(
    current: dict[str, object], baseline: dict[str, object], table: str
) -> list[dict[str, Any]]:
    observed = current[table]
    original = baseline[table]
    assert isinstance(observed, list) and isinstance(original, list)
    return [row for row in observed if row not in original]


def _key_hashes(state_directory: Path, run_id: str) -> dict[str, str]:
    keys = state_directory / "runs" / run_id / "keys"
    return {
        str(path.relative_to(keys)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(keys.rglob("*"))
        if path.is_file()
    }


def _step_records(state_directory: Path, run_id: str) -> list[dict[str, Any]]:
    steps = state_directory / "runs" / run_id / "steps"
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(steps.glob("*.json"))
    ]


def _actor_records(state_directory: Path, run_id: str) -> list[dict[str, Any]]:
    actors = state_directory / "runs" / run_id / "actors"
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(actors.glob("*.json"))
    ]


def _assert_frozen_gate(
    gate: dict[str, Any], target_record: dict[str, Any], action: str
) -> None:
    intent = target_record["intent"]
    assert target_record["action"] == action
    assert target_record["state"] == "dispatching"
    assert target_record["result"] is None
    assert intent["action"] == action
    assert intent["operation_id"] == gate["operation_id"]
    assert intent["expected_lifecycle_version"] == gate["expected_lifecycle_version"]
    assert intent["task_id"] == gate["task_id"]
    assert intent["actor_handle"] == gate["actor_handle"]
    assert intent["actor_id"] == gate["actor_id"] == target_record["agent_id"]
    assert intent["actor_public_key"] == gate["actor_public_key"]
    arguments = json.loads(intent["arguments_json"])
    assert all(arguments[key] == value for key, value in gate["arguments"].items())
    if action == "accept_bid":
        assert arguments["bid_id"].startswith("bid-")
        assert arguments["worker_id"]
        assert arguments["bidder_handle"] == "worker"


def _assert_target_once(
    projection: dict[str, object], baseline: dict[str, object], gate: dict[str, Any], action: str
) -> None:
    operations = _new_rows(projection, baseline, "board_task_operations")
    task_operations = [row for row in operations if row["task_id"] == gate["task_id"]]
    expected_commands = ["submit_bid"]
    if action != "bid":
        expected_commands.append("accept_bid")
    if action in {"submit_deliverable", "approve", "dispute"}:
        expected_commands.append("submit_deliverable")
    if action in {"approve", "dispute"}:
        expected_commands.append(TARGET_COMMAND[action])
    assert sorted(row["command"] for row in task_operations) == sorted(expected_commands)
    assert len({row["operation_id"] for row in task_operations}) == len(expected_commands)
    target_operations = [
        row for row in operations if row["operation_id"] == gate["operation_id"]
    ]
    assert len(target_operations) == 1
    operation = target_operations[0]
    assert operation["command"] == TARGET_COMMAND[action]
    assert operation["task_id"] == gate["task_id"]
    assert operation["actor_id"] == gate["actor_id"]
    assert operation["source_lifecycle_version"] == gate["expected_lifecycle_version"]
    assert operation["operation_state"] == "completed"
    target_events = [
        row
        for row in _new_rows(projection, baseline, "events")
        if row["event_type"] == TARGET_EVENT[action] and row["task_id"] == gate["task_id"]
    ]
    assert len(target_events) == 1
    assert target_events[0]["event_id"] == operation["event_id"]
    expected_events = ["escrow.locked", "task.created", "bid.submitted"]
    if action != "bid":
        expected_events.append("task.accepted")
    if action in {"submit_deliverable", "approve", "dispute"}:
        expected_events.append("task.submitted")
    if action == "approve":
        expected_events.extend(("escrow.released", "task.approved"))
    elif action == "dispute":
        expected_events.extend(("claim.filed", "task.disputed"))
    task_events = [
        row
        for row in _new_rows(projection, baseline, "events")
        if row["task_id"] == gate["task_id"]
    ]
    assert sorted(row["event_type"] for row in task_events) == sorted(expected_events)
    publications = [
        row
        for row in _new_rows(projection, baseline, "board_task_publications")
        if row["task_id"] == gate["task_id"]
    ]
    tasks = [
        row
        for row in _new_rows(projection, baseline, "board_tasks")
        if row["task_id"] == gate["task_id"]
    ]
    escrows = [
        row
        for row in _new_rows(projection, baseline, "bank_escrow")
        if row["task_id"] == gate["task_id"]
    ]
    assert len(publications) == len(tasks) == len(escrows) == 1
    if action == "bid":
        bids = [
            row
            for row in _new_rows(projection, baseline, "board_bids")
            if row["task_id"] == gate["task_id"] and row["bidder_id"] == gate["actor_id"]
        ]
        assert len(bids) == 1 and bids[0]["amount"] == BID_AMOUNT
    if action == "submit_deliverable":
        assets = [
            row
            for row in _new_rows(projection, baseline, "board_artifact_uploads")
            if row["task_id"] == gate["task_id"]
        ]
        assert len(assets) == 1 and assets[0]["display_filename"] == "restart-proof.txt"
    if action == "approve":
        receipts = [
            row
            for row in _new_rows(projection, baseline, "bank_operation_receipts")
            if row["operation_id"] == gate["operation_id"]
        ]
        assert len(receipts) == 1 and receipts[0]["operation_kind"] == "escrow_release"
    if action == "dispute":
        claims = [
            row
            for row in _new_rows(projection, baseline, "court_claims")
            if row["task_id"] == gate["task_id"]
        ]
        assert len(claims) == 1 and claims[0]["claimant_id"] == gate["actor_id"]
    conservation = projection["conservation"]
    assert isinstance(conservation, dict)
    assert conservation["issuance"] == conservation["balances"] + conservation["locked"]
    _assert_global_deltas(projection, baseline, gate, action)


def _assert_global_deltas(
    projection: dict[str, object],
    baseline: dict[str, object],
    gate: dict[str, Any],
    action: str,
) -> None:
    for table in PROJECTION_TABLES:
        current = projection[table]
        original = baseline[table]
        assert isinstance(current, list) and isinstance(original, list)
        assert all(row in current for row in original), f"baseline row changed in {table}"

    identities = _new_rows(projection, baseline, "identity_agents")
    accounts = _new_rows(projection, baseline, "bank_accounts")
    transactions = _new_rows(projection, baseline, "bank_transactions")
    escrows = _new_rows(projection, baseline, "bank_escrow")
    receipts = _new_rows(projection, baseline, "bank_operation_receipts")
    legs = _new_rows(projection, baseline, "bank_operation_legs")
    tasks = _new_rows(projection, baseline, "board_tasks")
    publications = _new_rows(projection, baseline, "board_task_publications")
    bids = _new_rows(projection, baseline, "board_bids")
    assets = _new_rows(projection, baseline, "board_artifact_uploads")
    operations = _new_rows(projection, baseline, "board_task_operations")
    claims = _new_rows(projection, baseline, "court_claims")
    events = _new_rows(projection, baseline, "events")
    event_by_id = {row["event_id"]: row for row in events}
    assert len(event_by_id) == len(events)

    assert len(identities) == len(accounts) == 2
    identity_ids = {row["agent_id"] for row in identities}
    assert len(identity_ids) == 2
    assert {row["account_id"] for row in accounts} == identity_ids
    assert gate["actor_id"] in identity_ids
    has_bid = action != "post_task"
    has_acceptance = action not in {"post_task", "bid"}
    assert len(tasks) == len(publications) == len(escrows) == 1
    assert len(bids) == int(has_bid)
    task, publication, escrow = tasks[0], publications[0], escrows[0]
    assert task["task_id"] == publication["task_id"] == escrow["task_id"]
    assert task["task_id"] == gate["task_id"]
    assert task["poster_id"] in identity_ids
    assert task["reward"] == escrow["amount"] == REWARD
    worker_id = next(iter(identity_ids - {task["poster_id"]}))
    bid = bids[0] if bids else None
    if bid is not None:
        assert bid["task_id"] == task["task_id"]
        assert bid["bidder_id"] == worker_id
        assert bid["amount"] == BID_AMOUNT
    if has_acceptance:
        assert bid is not None
        expected_accepted_bid_id = bid["bid_id"]
    else:
        expected_accepted_bid_id = None
    assert task["accepted_bid_id"] == expected_accepted_bid_id
    assert task["worker_id"] == (worker_id if has_acceptance else None)
    expected_status = {
        "post_task": "open",
        "bid": "open",
        "accept_bid": "accepted",
        "upload_asset": "accepted",
        "submit_deliverable": "submitted",
        "approve": "approved",
        "dispute": "disputed",
    }
    assert task["status"] == expected_status[action]
    assert escrow["status"] == ("released" if action == "approve" else "locked")

    expects_asset = action in {"upload_asset", "submit_deliverable", "approve", "dispute"}
    expects_submission = action in {"submit_deliverable", "approve", "dispute"}
    assert len(assets) == int(expects_asset)
    if assets:
        assert assets[0]["task_id"] == task["task_id"]
        assert assets[0]["actor_id"] == worker_id
        assert assets[0]["display_filename"] == "restart-proof.txt"
        assert assets[0]["state"] == "ready"

    expected_commands: list[str] = []
    if has_bid:
        expected_commands.append("submit_bid")
    if has_acceptance:
        expected_commands.append("accept_bid")
    if expects_submission:
        expected_commands.append("submit_deliverable")
    if action in {"approve", "dispute"}:
        expected_commands.append(TARGET_COMMAND[action])
    assert len(operations) == len(expected_commands)
    assert sorted(row["command"] for row in operations) == sorted(expected_commands)
    assert all(row["task_id"] == task["task_id"] for row in operations)
    assert len({row["operation_id"] for row in operations}) == len(operations)

    expected_receipt_kinds = ["credit", "escrow_lock"]
    expected_transaction_types = ["credit", "escrow_lock"]
    if action == "approve":
        expected_receipt_kinds.append("escrow_release")
        expected_transaction_types.append("escrow_release")
    assert len(receipts) == len(legs) == len(transactions) == len(expected_receipt_kinds)
    assert sorted(row["operation_kind"] for row in receipts) == sorted(
        expected_receipt_kinds
    )
    assert sorted(row["type"] for row in transactions) == sorted(expected_transaction_types)
    assert {row["operation_id"] for row in receipts} == {
        row["operation_id"] for row in legs
    }
    transaction_by_id = {row["tx_id"]: row for row in transactions}
    receipt_by_operation = {row["operation_id"]: row for row in receipts}
    assert len(transaction_by_id) == len(transactions)
    for leg in legs:
        receipt = receipt_by_operation[leg["operation_id"]]
        transaction = transaction_by_id[leg["transaction_id"]]
        assert receipt["event_id"] == transaction["event_id"]
        assert leg["account_id"] == transaction["account_id"]
        assert leg["amount"] == transaction["amount"]
        assert leg["balance_after"] == transaction["balance_after"]

    funding = [row for row in receipts if row["operation_kind"] == "credit"]
    lock = [row for row in receipts if row["operation_kind"] == "escrow_lock"]
    assert len(funding) == len(lock) == 1
    assert funding[0]["source_reference"].startswith("mp05:native:run-")
    assert lock[0]["operation_id"] == publication["lock_operation_id"]
    assert lock[0]["escrow_id"] == escrow["escrow_id"]
    leg_by_operation = {row["operation_id"]: row for row in legs}
    funding_leg = leg_by_operation[funding[0]["operation_id"]]
    funding_transaction = transaction_by_id[funding_leg["transaction_id"]]
    assert funding_leg["role"] == "account"
    assert funding_leg["direction"] == "credit"
    assert funding_leg["account_id"] == task["poster_id"]
    assert funding_leg["amount"] == funding_transaction["amount"] == FUNDING_AMOUNT
    assert funding_transaction["type"] == "credit"
    assert funding_transaction["reference"] == funding[0]["source_reference"]
    assert event_by_id[funding[0]["event_id"]]["agent_id"] == task["poster_id"]
    lock_leg = leg_by_operation[lock[0]["operation_id"]]
    lock_transaction = transaction_by_id[lock_leg["transaction_id"]]
    assert lock_leg["role"] == "payer"
    assert lock_leg["direction"] == "debit"
    assert lock_leg["account_id"] == task["poster_id"]
    assert lock_leg["amount"] == lock_transaction["amount"] == REWARD
    assert lock_transaction["type"] == "escrow_lock"
    assert lock_transaction["reference"] == task["task_id"]
    assert event_by_id[lock[0]["event_id"]]["task_id"] == task["task_id"]
    if action == "approve":
        release = [row for row in receipts if row["operation_kind"] == "escrow_release"]
        assert len(release) == 1
        assert release[0]["operation_id"] == gate["operation_id"]
        assert release[0]["escrow_id"] == escrow["escrow_id"]
        assert release[0]["source_reference"] == f"task:{task['task_id']}:approved"
        release_leg = leg_by_operation[release[0]["operation_id"]]
        release_transaction = transaction_by_id[release_leg["transaction_id"]]
        assert release_leg["role"] == "recipient"
        assert release_leg["direction"] == "credit"
        assert release_leg["account_id"] == worker_id
        assert release_leg["amount"] == release_transaction["amount"] == REWARD
        assert release_transaction["type"] == "escrow_release"
        assert release_transaction["reference"] == escrow["escrow_id"]
        assert event_by_id[release[0]["event_id"]]["task_id"] == task["task_id"]
    else:
        assert not [
            row
            for row in receipts
            if row["operation_kind"] in {"escrow_release", "escrow_split"}
        ]
        assert not [
            row
            for row in transactions
            if row["type"] in {"escrow_release", "escrow_split"}
        ]

    expected_event_types = [
        "account.created",
        "account.created",
        "agent.registered",
        "agent.registered",
        "salary.paid",
        "escrow.locked",
        "task.created",
    ]
    if has_bid:
        expected_event_types.append("bid.submitted")
    if has_acceptance:
        expected_event_types.append("task.accepted")
    if expects_submission:
        expected_event_types.append("task.submitted")
    if action == "approve":
        expected_event_types.extend(("escrow.released", "task.approved"))
    elif action == "dispute":
        expected_event_types.extend(("claim.filed", "task.disputed"))
    assert len(events) == len(expected_event_types)
    assert sorted(row["event_type"] for row in events) == sorted(expected_event_types)
    for transaction in transactions:
        expected_type = {
            "credit": "salary.paid",
            "escrow_lock": "escrow.locked",
            "escrow_release": "escrow.released",
        }[transaction["type"]]
        assert event_by_id[transaction["event_id"]]["event_type"] == expected_type
    for operation in operations:
        assert event_by_id[operation["event_id"]]["event_type"] == {
            "submit_bid": "bid.submitted",
            "accept_bid": "task.accepted",
            "submit_deliverable": "task.submitted",
            "approve_task": "task.approved",
            "dispute_task": "task.disputed",
        }[operation["command"]]

    assert len(claims) == int(action == "dispute")
    if claims:
        assert claims[0]["task_id"] == task["task_id"]
        assert claims[0]["claim_id"] == task["dispute_id"]
        assert claims[0]["claimant_id"] == task["poster_id"]
        assert claims[0]["respondent_id"] == task["worker_id"]

    expected_balances = {
        task["poster_id"]: FUNDING_AMOUNT - REWARD,
        worker_id: REWARD if action == "approve" else 0,
    }
    assert {row["account_id"]: row["balance"] for row in accounts} == expected_balances
    conservation = projection["conservation"]
    assert isinstance(conservation, dict)
    assert conservation["issuance"] == conservation["balances"] + conservation["locked"]


def _run_restart_case(
    native_harness: _Harness,
    action: str,
    phase: str,
    *,
    ownership: contextlib.ExitStack,
    cleanup_failures: list[BaseException],
) -> None:
    harness = native_harness
    run_id = f"run-{uuid.uuid4()}"
    case = harness.runtime.run_dir / "demo-native" / f"{action}-{phase}-{run_id}"
    case.mkdir(parents=True, mode=0o700)
    scenario_path, steps = _write_scenario(case, action, run_id)
    target_index = len(steps) - 1
    gate_path = case / "crash-gate.json"
    baseline = _projection(harness.runtime)
    child = _launch_crash(
        harness,
        scenario_path,
        run_id,
        action,
        phase,
        gate_path,
        case / "01-crash.log",
        ownership=ownership,
        cleanup_failures=cleanup_failures,
    )
    gate = _wait_for_gate(child, gate_path, case / "01-crash.log")
    records_at_gate = _step_records(harness.state_directory, run_id)
    assert len(records_at_gate) == len(steps)
    assert [record["state"] for record in records_at_gate[:target_index]] == [
        "empty",
        "empty",
        *("completed" for _ in range(target_index - 2)),
    ]
    _assert_frozen_gate(gate, records_at_gate[target_index], action)
    keys_at_gate = _key_hashes(harness.state_directory, run_id)
    assert set(keys_at_gate) == {
        "poster/poster.key",
        "poster/poster.lock",
        "poster/poster.pub",
        "worker/worker.key",
        "worker/worker.lock",
        "worker/worker.pub",
    }
    _kill_at_gate(child, gate)

    assert _alive(harness.sentinel.actor_pid)
    after_crash = _projection(harness.runtime)
    target_after_crash = [
        row
        for row in _new_rows(after_crash, baseline, "board_task_operations")
        if row["operation_id"] == gate["operation_id"]
    ]
    assert len(target_after_crash) == (0 if phase == "before_send" else 1)
    if phase == "after_response":
        assert gate["response"] is not None
        _assert_target_once(after_crash, baseline, gate, action)
    else:
        assert gate["response"] is None
        predecessor = {
            "bid": "post_task",
            "accept_bid": "bid",
            "submit_deliverable": "upload_asset",
            "approve": "submit_deliverable",
            "dispute": "submit_deliverable",
        }[action]
        _assert_global_deltas(after_crash, baseline, gate, predecessor)

    assert _run_cli(harness, scenario_path, run_id, case / "02-resume.log") == 0
    final = _projection(harness.runtime)
    _assert_target_once(final, baseline, gate, action)
    final_records = _step_records(harness.state_directory, run_id)
    assert [record["state"] for record in final_records] == [
        "empty",
        "empty",
        *("completed" for _ in range(len(final_records) - 2)),
    ]
    assert final_records[target_index]["intent"] == records_at_gate[target_index]["intent"]
    assert final_records[target_index]["agent_id"] == records_at_gate[target_index]["agent_id"]
    assert _key_hashes(harness.state_directory, run_id) == keys_at_gate
    retained_actors = _actor_records(harness.state_directory, run_id)
    assert len(retained_actors) == 2
    actors = [
        row
        for row in _new_rows(final, baseline, "identity_agents")
        if row["agent_id"]
        in {record["registration"]["agent_id"] for record in retained_actors}
    ]
    assert len(actors) == 2
    assert {row["public_key"] for row in actors} == {
        record["registration"]["public_key"] for record in retained_actors
    }
    posting_result = final_records[3]["result"]["body"]
    assert posting_result["task_id"] == gate["task_id"]
    if action in {"accept_bid", "submit_deliverable", "approve", "dispute"}:
        bid = final_records[4]["result"]["bid"]
        assert bid["bid_id"] == json.loads(
            final_records[5]["intent"]["arguments_json"]
        )["bid_id"]
        assert final_records[5]["result"]["bidder_handle"] == "worker"
    if action == "dispute":
        assert final_records[-1]["result"]["dispute_id"] == final_records[-1]["result"][
            "response"
        ]["operation"]["result"]["dispute_id"]

    assert _run_cli(harness, scenario_path, run_id, case / "03-completed-noop.log") == 0
    assert _projection(harness.runtime) == final
    assert _step_records(harness.state_directory, run_id) == final_records
    assert _key_hashes(harness.state_directory, run_id) == keys_at_gate
    assert _alive(harness.sentinel.actor_pid)
    (case / "proof.json").write_text(
        json.dumps(
            {
                "action": action,
                "phase": phase,
                "run_id": run_id,
                "gate": gate,
                "target_record_at_gate": records_at_gate[target_index],
                "target_record_final": final_records[target_index],
                "keys": keys_at_gate,
                "conservation": final["conservation"],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"MP05 native restart evidence: {case}")


@pytest.mark.parametrize(
    ("action", "phase"),
    [
        (action, phase)
        for action in ("bid", "accept_bid", "submit_deliverable", "approve", "dispute")
        for phase in ("before_send", "after_response")
    ],
)
def test_native_demo_mutation_is_exactly_once_across_process_restart(
    native_harness: _Harness,
    action: str,
    phase: str,
) -> None:
    ownership = contextlib.ExitStack()
    cleanup_failures: list[BaseException] = []
    primary: BaseException | None = None
    try:
        _run_restart_case(
            native_harness,
            action,
            phase,
            ownership=ownership,
            cleanup_failures=cleanup_failures,
        )
    except BaseException as exc:
        primary = exc
    finally:
        ownership.close()
    _raise_failures(
        primary,
        cleanup_failures,
        native_harness.runtime.run_dir / "demo-native" / f"{action}-{phase}-failure.json",
    )
