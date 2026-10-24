"""MP-05 real CLI setup crash/restart matrix through native task posting."""

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
from typing import Any

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
CRASH_RUNNER = Path(__file__).with_name("mp05_demo_setup_crash_runner.py")
TIMEOUT_SECONDS = 45.0
FUNDING_AMOUNT = 500
REWARD = 100
FUNDING_REFERENCE = "mp05:demo-setup:fund:poster"


@dataclass
class _OwnedChild:
    process: subprocess.Popen[bytes]
    pgid: int
    sid: int
    actor_pid: int | None = None
    drained: bool = False

    def bind_actor(self, actor_pid: int) -> None:
        if actor_pid <= 0:
            raise RuntimeError("Demo setup actor PID must be positive")
        if os.getpgid(actor_pid) != self.pgid or os.getsid(actor_pid) != self.sid:
            raise RuntimeError("Demo setup actor is outside the owned process group")
        self.actor_pid = actor_pid


@dataclass(slots=True)
class _LaunchGate:
    descriptor: int | None

    def release(self) -> None:
        descriptor = self.descriptor
        if descriptor is None:
            raise RuntimeError("Demo setup launch gate is no longer owned")
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


def _assert_owned_alive(child: _OwnedChild) -> None:
    if not _alive(child.actor_pid):
        raise AssertionError("Unrelated sentinel did not survive demo setup cleanup")


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
            f"MP05 demo setup restart proof failed; evidence: {evidence_path}", failures
        )


def _kill_at_gate(child: _OwnedChild, actor_pid: int) -> None:
    child.bind_actor(actor_pid)
    os.kill(actor_pid, signal.SIGKILL)
    assert child.process.wait(timeout=TIMEOUT_SECONDS) != 0
    assert not _group_exists(child)
    child.drained = True


def _wait_for_gate(
    child: _OwnedChild, gate_path: Path, log_path: Path
) -> dict[str, Any]:
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if gate_path.exists():
            value = json.loads(gate_path.read_text(encoding="utf-8"))
            assert isinstance(value, dict)
            return value
        return_code = child.process.poll()
        if return_code is not None:
            log = log_path.read_text(encoding="utf-8")
            raise AssertionError(
                "Demo CLI exited before the setup client gate "
                f"(exit {return_code}); this is a pre-gate product failure.\n{log}"
            )
        time.sleep(0.05)
    raise TimeoutError(f"Demo CLI did not reach setup client gate; see {log_path}")


def _scenario(actor_name: str) -> dict[str, object]:
    return {
        "name": "MP-05 durable setup restart",
        "description": "Register, fund, and post exactly once after setup process death.",
        "default_delay": 7.0,
        "agents": [{"handle": "poster", "name": actor_name}],
        "steps": [
            {"action": "register", "agent": "poster"},
            {
                "action": "fund",
                "agent": "poster",
                "amount": FUNDING_AMOUNT,
                "reference": FUNDING_REFERENCE,
            },
            {
                "action": "post_task",
                "poster": "poster",
                "ref": "setup_restart_task",
                "title": "Durable setup continuation",
                "spec": "Continue after setup recovery and publish exactly once.",
                "reward": REWARD,
                "bidding_deadline_seconds": 3600,
                "acceptance_window_seconds": 300,
                "execution_deadline_seconds": 7200,
                "review_deadline_seconds": 3600,
            },
        ],
    }


def _write_inputs(
    runtime: IsolatedRuntime, case: Path
) -> tuple[Path, Path, Path, str, str]:
    run_id = f"run-{uuid.uuid4()}"
    actor_name = f"MP05 setup poster {run_id}"
    scenario_path = case / "scenario.yaml"
    scenario_path.write_text(
        yaml.safe_dump(_scenario(actor_name), sort_keys=False), encoding="utf-8"
    )
    state_directory = case / "state"
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
        "state_directory": "../demo-setup/state",
        "max_record_bytes": 1_048_576,
        "diagnostic_limit": 16,
    }
    config_path = runtime.run_dir / "configs" / "demo-setup-tools.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    assert state_directory == runtime.run_dir / "demo-setup" / "state"
    return config_path, scenario_path, state_directory, run_id, actor_name


def _environment(runtime: IsolatedRuntime, config_path: Path) -> dict[str, str]:
    return {**runtime.environment(), "TOOLS_CONFIG_PATH": str(config_path)}


def _production_arguments(scenario_path: Path, run_id: str) -> list[str]:
    return [str(scenario_path), "--run-id", run_id, "--delay", "0"]


def _launch_crash(
    runtime: IsolatedRuntime,
    config_path: Path,
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
    command = [
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
    ]
    with log_path.open("wb") as log:
        process, pgid, sid, descriptor = _start_owned_launch(
            command,
            cwd=str(TOOLS_ROOT),
            env=_environment(runtime, config_path),
            stdout=log,
            stderr=subprocess.STDOUT,
            pass_fds=(),
        )
        child = _OwnedChild(process=process, pgid=pgid, sid=sid)
        ownership.callback(_capture_cleanup, cleanup_failures, _terminate_owned, child)
        launch_gate = _LaunchGate(descriptor)
        ownership.callback(_capture_cleanup, cleanup_failures, launch_gate.close)
        launch_gate.release()
    return child


def _start_sentinel(
    root: Path,
    *,
    ownership: contextlib.ExitStack,
    cleanup_failures: list[BaseException],
) -> _OwnedChild:
    process, pgid, sid, descriptor = _start_owned_launch(
        ["/bin/sleep", "60"],
        cwd=str(root),
        env=dict(os.environ),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        pass_fds=(),
    )
    child = _OwnedChild(process=process, pgid=pgid, sid=sid, actor_pid=process.pid)
    ownership.callback(_capture_cleanup, cleanup_failures, _terminate_owned, child)
    ownership.callback(_capture_cleanup, cleanup_failures, _assert_owned_alive, child)
    launch_gate = _LaunchGate(descriptor)
    ownership.callback(_capture_cleanup, cleanup_failures, launch_gate.close)
    launch_gate.release()
    return child


def _run_cli(
    runtime: IsolatedRuntime,
    config_path: Path,
    scenario_path: Path,
    run_id: str,
) -> int:
    command = [
        "env",
        f"TOOLS_CONFIG_PATH={config_path}",
        "uv",
        "run",
        "--no-sync",
        "python",
        "-m",
        "demo_replay",
        *_production_arguments(scenario_path, run_id),
    ]
    return runtime.run_command(command, cwd=TOOLS_ROOT)


def _database_path(runtime: IsolatedRuntime) -> Path:
    config = yaml.safe_load(runtime.gateway_config_path.read_text(encoding="utf-8"))
    return Path(config["database"]["path"])


def _rows(connection: sqlite3.Connection, query: str) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(query).fetchall()]


def _projection(runtime: IsolatedRuntime) -> dict[str, object]:
    with sqlite3.connect(
        f"file:{_database_path(runtime)}?mode=ro", uri=True
    ) as connection:
        connection.row_factory = sqlite3.Row
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
        return {
            "identities": _rows(
                connection, "SELECT * FROM identity_agents ORDER BY agent_id"
            ),
            "accounts": _rows(
                connection, "SELECT * FROM bank_accounts ORDER BY account_id"
            ),
            "transactions": _rows(
                connection, "SELECT * FROM bank_transactions ORDER BY tx_id"
            ),
            "receipts": _rows(
                connection,
                "SELECT * FROM bank_operation_receipts ORDER BY operation_id",
            ),
            "legs": _rows(
                connection, "SELECT * FROM bank_operation_legs ORDER BY operation_id"
            ),
            "tasks": _rows(connection, "SELECT * FROM board_tasks ORDER BY task_id"),
            "publications": _rows(
                connection,
                "SELECT * FROM board_task_publications ORDER BY operation_id",
            ),
            "escrows": _rows(
                connection, "SELECT * FROM bank_escrow ORDER BY escrow_id"
            ),
            "events": _rows(connection, "SELECT * FROM events ORDER BY event_id"),
            "conservation": {
                "issuance": int(issuance[0]),
                "balances": int(balances[0]),
                "locked": int(locked[0]),
            },
        }


def _new_rows(
    projection: dict[str, object], baseline: dict[str, object], key: str
) -> list[dict[str, Any]]:
    current = projection[key]
    original = baseline[key]
    assert isinstance(current, list) and isinstance(original, list)
    assert all(row in current for row in original)
    return [row for row in current if row not in original]


def _actor_rows(
    projection: dict[str, object], baseline: dict[str, object], actor_name: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    identities = [
        row
        for row in _new_rows(projection, baseline, "identities")
        if row["name"] == actor_name
    ]
    actor_ids = {row["agent_id"] for row in identities}
    accounts = [
        row
        for row in _new_rows(projection, baseline, "accounts")
        if row["account_id"] in actor_ids
    ]
    return identities, accounts


def _assert_after_crash(
    action: str,
    phase: str,
    gate: dict[str, Any],
    projection: dict[str, object],
    baseline: dict[str, object],
    actor_name: str,
) -> None:
    identities, accounts = _actor_rows(projection, baseline, actor_name)
    all_new_identities = _new_rows(projection, baseline, "identities")
    all_new_accounts = _new_rows(projection, baseline, "accounts")
    expected_identity_count = int(action != "register" or phase == "after_response")
    expected_account_count = int(
        action == "fund" or (action == "create_account" and phase == "after_response")
    )
    expected_funding_count = int(action == "fund" and phase == "after_response")
    assert len(identities) == expected_identity_count
    assert len(accounts) == expected_account_count
    assert all_new_identities == identities
    assert all_new_accounts == accounts
    if action == "register" and identities:
        assert identities[0]["public_key"] == gate["request"]["public_key"]
    if accounts:
        assert accounts[0]["balance"] == expected_funding_count * FUNDING_AMOUNT
    if phase == "after_response" and action == "create_account":
        assert gate["response"]["account_id"] == gate["request"]["agent_id"]
        assert gate["response"]["balance"] == 0
    if phase == "after_response" and action == "fund":
        assert gate["response"]["outcome"] == "native_receipt"
        assert (
            gate["response"]["receipt"]["operation_id"]
            == gate["request"]["operation_id"]
        )
        assert gate["response"]["receipt"]["result"] == {
            "account_id": gate["request"]["account_id"],
            "amount": FUNDING_AMOUNT,
            "balance_after": FUNDING_AMOUNT,
            "reference": FUNDING_REFERENCE,
        }
    transactions = [
        row
        for row in _new_rows(projection, baseline, "transactions")
        if row["type"] == "credit" and row["reference"] == FUNDING_REFERENCE
    ]
    receipts = [
        row
        for row in _new_rows(projection, baseline, "receipts")
        if row["operation_kind"] == "credit"
        and row["source_reference"] == FUNDING_REFERENCE
    ]
    legs = [
        row
        for row in _new_rows(projection, baseline, "legs")
        if receipts and row["operation_id"] == receipts[0]["operation_id"]
    ]
    assert len(transactions) == len(receipts) == len(legs) == expected_funding_count
    assert _new_rows(projection, baseline, "tasks") == []
    assert _new_rows(projection, baseline, "publications") == []
    assert _new_rows(projection, baseline, "escrows") == []
    conservation = projection["conservation"]
    baseline_conservation = baseline["conservation"]
    assert isinstance(conservation, dict)
    assert isinstance(baseline_conservation, dict)
    assert conservation["issuance"] == conservation["balances"] + conservation["locked"]
    assert conservation["issuance"] == (
        baseline_conservation["issuance"] + expected_funding_count * FUNDING_AMOUNT
    )
    assert conservation["balances"] == (
        baseline_conservation["balances"] + expected_funding_count * FUNDING_AMOUNT
    )
    assert conservation["locked"] == baseline_conservation["locked"]


def _assert_final(
    projection: dict[str, object],
    baseline: dict[str, object],
    actor_name: str,
    gate: dict[str, Any],
) -> str:
    identities, accounts = _actor_rows(projection, baseline, actor_name)
    assert len(identities) == len(accounts) == 1
    assert _new_rows(projection, baseline, "identities") == identities
    assert _new_rows(projection, baseline, "accounts") == accounts
    actor_id = identities[0]["agent_id"]
    assert accounts[0]["account_id"] == actor_id
    assert accounts[0]["balance"] == FUNDING_AMOUNT - REWARD
    if gate["action"] == "register" and gate["phase"] == "after_response":
        assert gate["response"]["agent_id"] == actor_id
        assert gate["actor_id_after_response"] == actor_id
        assert gate["response"]["public_key"] == identities[0]["public_key"]
    if gate["action"] in {"create_account", "fund"}:
        assert (
            gate["request"][
                "agent_id" if gate["action"] == "create_account" else "account_id"
            ]
            == actor_id
        )

    receipts = _new_rows(projection, baseline, "receipts")
    legs = _new_rows(projection, baseline, "legs")
    transactions = _new_rows(projection, baseline, "transactions")
    funding_receipts = [
        row
        for row in receipts
        if row["operation_kind"] == "credit"
        and row["source_reference"] == FUNDING_REFERENCE
    ]
    assert len(funding_receipts) == 1
    funding_operation_id = funding_receipts[0]["operation_id"]
    funding_transactions = [
        row
        for row in transactions
        if row["type"] == "credit" and row["reference"] == FUNDING_REFERENCE
    ]
    funding_legs = [row for row in legs if row["operation_id"] == funding_operation_id]
    assert len(funding_transactions) == len(funding_legs) == 1
    assert funding_transactions[0]["account_id"] == actor_id
    assert funding_transactions[0]["amount"] == FUNDING_AMOUNT
    assert funding_legs[0]["direction"] == "credit"
    assert funding_legs[0]["amount"] == FUNDING_AMOUNT
    if gate["action"] == "fund":
        assert gate["request"] == {
            "account_id": actor_id,
            "amount": FUNDING_AMOUNT,
            "bank_url": gate["request"]["bank_url"],
            "operation_id": funding_operation_id,
            "reference": FUNDING_REFERENCE,
        }

    tasks = _new_rows(projection, baseline, "tasks")
    publications = _new_rows(projection, baseline, "publications")
    escrows = _new_rows(projection, baseline, "escrows")
    assert len(tasks) == len(publications) == len(escrows) == 1
    assert tasks[0]["poster_id"] == actor_id
    assert tasks[0]["reward"] == REWARD
    assert escrows[0]["task_id"] == tasks[0]["task_id"]
    assert escrows[0]["status"] == "locked"
    assert len(receipts) == len(legs) == len(transactions) == 2
    assert sorted(row["operation_kind"] for row in receipts) == [
        "credit",
        "escrow_lock",
    ]
    event_types = sorted(
        row["event_type"] for row in _new_rows(projection, baseline, "events")
    )
    assert event_types == sorted(
        [
            "account.created",
            "agent.registered",
            "salary.paid",
            "escrow.locked",
            "task.created",
        ]
    )
    conservation = projection["conservation"]
    baseline_conservation = baseline["conservation"]
    assert isinstance(conservation, dict)
    assert isinstance(baseline_conservation, dict)
    assert conservation["issuance"] == conservation["balances"] + conservation["locked"]
    assert (
        conservation["issuance"] == baseline_conservation["issuance"] + FUNDING_AMOUNT
    )
    assert conservation["balances"] == (
        baseline_conservation["balances"] + FUNDING_AMOUNT - REWARD
    )
    assert conservation["locked"] == baseline_conservation["locked"] + REWARD
    return actor_id


def _key_hashes(state_directory: Path, run_id: str) -> dict[str, str]:
    keys = state_directory / "runs" / run_id / "keys"
    return {
        str(path.relative_to(keys)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(keys.rglob("*"))
        if path.is_file()
    }


def _actor_record(state_directory: Path, run_id: str) -> dict[str, Any]:
    value = json.loads(
        (state_directory / "runs" / run_id / "actors" / "poster.json").read_text(
            encoding="utf-8"
        )
    )
    assert isinstance(value, dict)
    return value


def _step_record(state_directory: Path, run_id: str, index: int) -> dict[str, Any]:
    value = json.loads(
        (state_directory / "runs" / run_id / "steps" / f"{index:06d}.json").read_text(
            encoding="utf-8"
        )
    )
    assert isinstance(value, dict)
    return value


@pytest.mark.parametrize("action", ("register", "create_account", "fund"))
@pytest.mark.parametrize("phase", ("before_send", "after_response"))
def test_demo_cli_setup_steps_survive_real_process_death_and_continue_to_post(
    tmp_path: Path,
    action: str,
    phase: str,
) -> None:
    ownership = contextlib.ExitStack()
    cleanup_failures: list[BaseException] = []
    runtime: IsolatedRuntime | None = None
    case: Path | None = None
    child: _OwnedChild | None = None
    sentinel: _OwnedChild | None = None
    primary: BaseException | None = None
    proof: dict[str, object] = {"action": action, "phase": phase}
    try:
        runtime = IsolatedRuntime.create(repo_root=REPO_ROOT, artifacts_root=tmp_path)
        ownership.callback(_capture_cleanup, cleanup_failures, runtime.stop)
        case = runtime.run_dir / "demo-setup"
        case.mkdir(mode=0o700)
        gate_path = case / "crash-gate.json"
        crash_log = case / "crash.log"
        sentinel = _start_sentinel(
            case, ownership=ownership, cleanup_failures=cleanup_failures
        )
        runtime.start()
        config_path, scenario_path, state_directory, run_id, actor_name = _write_inputs(
            runtime, case
        )
        baseline = _projection(runtime)
        scenario_bytes = scenario_path.read_bytes()

        child = _launch_crash(
            runtime,
            config_path,
            scenario_path,
            run_id,
            action,
            phase,
            gate_path,
            crash_log,
            ownership=ownership,
            cleanup_failures=cleanup_failures,
        )
        gate = _wait_for_gate(child, gate_path, crash_log)
        actor_pid = gate["pid"]
        assert type(actor_pid) is int
        child.bind_actor(actor_pid)
        assert gate["pgid"] == child.pgid and gate["sid"] == child.sid
        assert gate["action"] == action and gate["phase"] == phase
        assert (gate["response"] is None) == (phase == "before_send")
        keys_after_crash = _key_hashes(state_directory, run_id)
        assert keys_after_crash
        actor_after_crash = _actor_record(state_directory, run_id)
        _kill_at_gate(child, actor_pid)
        assert _alive(sentinel.actor_pid)

        after_crash = _projection(runtime)
        _assert_after_crash(action, phase, gate, after_crash, baseline, actor_name)

        assert _run_cli(runtime, config_path, scenario_path, run_id) == 0
        final = _projection(runtime)
        actor_id = _assert_final(final, baseline, actor_name, gate)
        final_actor = _actor_record(state_directory, run_id)
        assert final_actor["state"] == "bound"
        assert final_actor["registration"]["agent_id"] == actor_id
        if action == "register":
            assert (
                final_actor["registration"]["public_key"]
                == gate["request"]["public_key"]
            )
            if phase == "after_response":
                assert final_actor["registration"] == gate["response"]
        else:
            assert actor_after_crash["state"] == "bound"
            assert final_actor == actor_after_crash
        if action == "fund":
            funding_step = _step_record(state_directory, run_id, 1)
            assert funding_step["state"] == "completed"
            assert funding_step["intent"] == {
                key: gate["request"][key]
                for key in ("operation_id", "account_id", "amount", "reference")
            }
            assert (
                funding_step["result"]["receipt"]["operation_id"]
                == gate["request"]["operation_id"]
            )
            if phase == "after_response":
                assert funding_step["result"] == gate["response"]
        assert _key_hashes(state_directory, run_id) == keys_after_crash
        assert scenario_path.read_bytes() == scenario_bytes

        before_completed_replay = _projection(runtime)
        assert _run_cli(runtime, config_path, scenario_path, run_id) == 0
        assert _projection(runtime) == before_completed_replay
        assert _actor_record(state_directory, run_id) == final_actor
        assert _key_hashes(state_directory, run_id) == keys_after_crash
        assert _alive(sentinel.actor_pid)
        proof.update(
            {
                "runtime_id": runtime.run_id,
                "run_id": run_id,
                "gate": gate,
                "actor_after_crash": actor_after_crash,
                "after_crash": after_crash,
                "final_actor": final_actor,
                "final": final,
                "keys": keys_after_crash,
            }
        )
    except BaseException as exc:
        proof["failure"] = f"{type(exc).__name__}: {exc}"
        primary = exc
    finally:
        proof["child_before_teardown"] = (
            None
            if child is None
            else {
                "pid": child.process.pid,
                "pgid": child.pgid,
                "sid": child.sid,
                "actor_pid": child.actor_pid,
                "returncode": child.process.poll(),
                "drained": child.drained,
            }
        )
        proof["sentinel_alive_before_teardown"] = (
            None if sentinel is None else _alive(sentinel.actor_pid)
        )
        ownership.close()
        proof["child_drained_after_teardown"] = None if child is None else child.drained
        proof["sentinel_drained_after_teardown"] = (
            None if sentinel is None else sentinel.drained
        )
        if sentinel is not None and (
            not sentinel.drained or _alive(sentinel.actor_pid)
        ):
            cleanup_failures.append(
                AssertionError("Unrelated sentinel was not drained by its exact owner")
            )
        proof["cleanup_failures"] = [
            f"{type(exc).__name__}: {exc}" for exc in cleanup_failures
        ]
        proof_path = (
            case / "proof.json"
            if case is not None
            else tmp_path / f"{action}-{phase}-proof.json"
        )
        try:
            proof_path.write_text(
                json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        except BaseException as exc:
            cleanup_failures.append(exc)
        print(f"MP05 demo setup restart evidence: {proof_path}")
    _raise_failures(primary, cleanup_failures, proof_path)
