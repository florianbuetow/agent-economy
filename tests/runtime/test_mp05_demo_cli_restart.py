"""MP-05 actual demo CLI crash/restart and completed-run replay acceptance proof."""

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
CRASH_RUNNER = Path(__file__).with_name("mp05_demo_cli_crash_runner.py")
TIMEOUT_SECONDS = 45.0
FUNDING_AMOUNT = 500
REWARD = 100


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
            raise RuntimeError("Demo launch gate is no longer owned")
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
        raise AssertionError("Unrelated sentinel did not survive demo child cleanup")


def _process_evidence(child: _OwnedChild | None) -> dict[str, object] | None:
    if child is None:
        return None
    return {
        "pid": child.process.pid,
        "pgid": child.pgid,
        "sid": child.sid,
        "actor_pid": child.actor_pid,
        "returncode": child.process.poll(),
        "drained": child.drained,
        "alive": _alive(child.actor_pid),
    }


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
            f"MP05 demo CLI restart proof failed; evidence: {evidence_path}", failures
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
            classification = (
                "This is the initial CLI capability-red, not posting-replay behavior-red."
                if "unrecognized arguments: --run-id" in log
                else "This is a pre-gate product failure; posting-replay behavior was not reached."
            )
            raise AssertionError(
                f"Demo CLI exited before the native posting gate (exit {return_code}). "
                f"{classification}\n{log}"
            )
        time.sleep(0.05)
    raise TimeoutError(f"Demo CLI did not reach native posting gate; see {log_path}")


def _scenario(actor_name: str, *, title: str) -> dict[str, object]:
    return {
        "name": "MP-05 durable demo CLI",
        "description": "Short register, fund, and native post restart proof.",
        "default_delay": 9.0,
        "agents": [{"handle": "poster", "name": actor_name}],
        "steps": [
            {"action": "register", "agent": "poster"},
            {
                "action": "fund",
                "agent": "poster",
                "amount": FUNDING_AMOUNT,
                "reference": "mp05:demo-cli:fund:poster",
            },
            {
                "action": "post_task",
                "poster": "poster",
                "ref": "restart_task",
                "title": title,
                "spec": "Publish exactly once across a real demo CLI process restart.",
                "reward": REWARD,
                "bidding_deadline_seconds": 3600,
                "acceptance_window_seconds": 300,
                "execution_deadline_seconds": 7200,
                "review_deadline_seconds": 3600,
            },
        ],
    }


def _write_inputs(runtime: IsolatedRuntime, case: Path) -> tuple[Path, Path, Path, str]:
    run_id = f"run-{uuid.uuid4()}"
    actor_name = f"MP05 demo poster {run_id}"
    scenario_path = case / "scenario.yaml"
    scenario_path.write_text(
        yaml.safe_dump(
            _scenario(actor_name, title="Durable demo publication"), sort_keys=False
        ),
        encoding="utf-8",
    )
    changed_path = case / "changed-scenario.yaml"
    changed_path.write_text(
        yaml.safe_dump(
            _scenario(actor_name, title="Changed scope must be rejected"),
            sort_keys=False,
        ),
        encoding="utf-8",
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
        "state_directory": "../demo-cli/state",
        "max_record_bytes": 1_048_576,
        "diagnostic_limit": 16,
    }
    config_path = runtime.run_dir / "configs" / "demo-tools.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    assert state_directory == runtime.run_dir / "demo-cli" / "state"
    return config_path, scenario_path, changed_path, run_id


def _environment(runtime: IsolatedRuntime, config_path: Path) -> dict[str, str]:
    return {**runtime.environment(), "TOOLS_CONFIG_PATH": str(config_path)}


def _production_arguments(scenario_path: Path, run_id: str) -> list[str]:
    return [str(scenario_path), "--run-id", run_id, "--delay", "0"]


def _launch_crash(
    runtime: IsolatedRuntime,
    config_path: Path,
    scenario_path: Path,
    run_id: str,
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
    child = _OwnedChild(
        process=process,
        pgid=pgid,
        sid=sid,
        actor_pid=process.pid,
    )
    ownership.callback(_capture_cleanup, cleanup_failures, _terminate_owned, child)
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


def _rows(
    connection: sqlite3.Connection, query: str, arguments: tuple[object, ...] = ()
) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(query, arguments).fetchall()]


def _projection(runtime: IsolatedRuntime, actor_name: str) -> dict[str, object]:
    with sqlite3.connect(
        f"file:{_database_path(runtime)}?mode=ro", uri=True
    ) as connection:
        connection.row_factory = sqlite3.Row
        actors = _rows(
            connection,
            "SELECT * FROM identity_agents WHERE name=? ORDER BY agent_id",
            (actor_name,),
        )
        actor_ids = tuple(row["agent_id"] for row in actors)
        actor_accounts = (
            _rows(
                connection,
                "SELECT * FROM bank_accounts WHERE account_id=? ORDER BY account_id",
                actor_ids,
            )
            if len(actor_ids) == 1
            else []
        )
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
            "actors": actors,
            "identities": _rows(
                connection, "SELECT * FROM identity_agents ORDER BY agent_id"
            ),
            "actor_accounts": actor_accounts,
            "accounts": _rows(
                connection, "SELECT * FROM bank_accounts ORDER BY account_id"
            ),
            "transactions": _rows(
                connection, "SELECT * FROM bank_transactions ORDER BY tx_id"
            ),
            "tasks": _rows(connection, "SELECT * FROM board_tasks ORDER BY task_id"),
            "publications": _rows(
                connection,
                "SELECT * FROM board_task_publications ORDER BY operation_id",
            ),
            "escrows": _rows(
                connection, "SELECT * FROM bank_escrow ORDER BY escrow_id"
            ),
            "receipts": _rows(
                connection,
                "SELECT * FROM bank_operation_receipts ORDER BY operation_id",
            ),
            "legs": _rows(
                connection, "SELECT * FROM bank_operation_legs ORDER BY operation_id"
            ),
            "events": _rows(connection, "SELECT * FROM events ORDER BY event_id"),
            "conservation": {
                "issuance": int(issuance[0]),
                "balances": int(balances[0]),
                "locked": int(locked[0]),
            },
        }


def _key_hashes(state_directory: Path, run_id: str) -> dict[str, str]:
    keys = state_directory / "runs" / run_id / "keys"
    return {
        str(path.relative_to(keys)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(keys.rglob("*"))
        if path.is_file()
    }


def _new_rows(
    projection: dict[str, object], baseline: dict[str, object], key: str
) -> list[dict[str, Any]]:
    current = projection[key]
    original = baseline[key]
    assert isinstance(current, list) and isinstance(original, list)
    assert all(row in current for row in original)
    return [row for row in current if row not in original]


def _assert_global_effects(
    projection: dict[str, object],
    baseline: dict[str, object],
    *,
    actor_name: str,
    actor_id: str,
    expected_balance: int,
    posting_intent: dict[str, Any] | None,
) -> None:
    actors = projection["actors"]
    actor_accounts = projection["actor_accounts"]
    assert isinstance(actors, list) and len(actors) == 1
    assert actors[0]["agent_id"] == actor_id
    assert isinstance(actor_accounts, list) and len(actor_accounts) == 1
    assert actor_accounts[0]["account_id"] == actor_id
    assert actor_accounts[0]["balance"] == expected_balance
    conservation = projection["conservation"]
    assert isinstance(conservation, dict)
    assert conservation["issuance"] == conservation["balances"] + conservation["locked"]

    new_identities = _new_rows(projection, baseline, "identities")
    assert len(new_identities) == 1
    assert new_identities[0]["agent_id"] == actor_id
    assert new_identities[0]["name"] == actor_name
    assert new_identities[0]["public_key"] == actors[0]["public_key"]
    new_accounts = _new_rows(projection, baseline, "accounts")
    assert new_accounts == actor_accounts

    receipts = _new_rows(projection, baseline, "receipts")
    legs = _new_rows(projection, baseline, "legs")
    transactions = _new_rows(projection, baseline, "transactions")
    events = _new_rows(projection, baseline, "events")
    expected_receipt_kinds = ["credit"] + (
        ["escrow_lock"] if posting_intent is not None else []
    )
    assert sorted(row["operation_kind"] for row in receipts) == expected_receipt_kinds
    assert len(legs) == len(transactions) == len(receipts)
    funding_transactions = [row for row in transactions if row["type"] == "credit"]
    assert len(funding_transactions) == 1
    assert funding_transactions[0]["account_id"] == actor_id
    assert funding_transactions[0]["amount"] == FUNDING_AMOUNT
    assert funding_transactions[0]["reference"] == "mp05:demo-cli:fund:poster"
    funding_receipts = [row for row in receipts if row["operation_kind"] == "credit"]
    assert len(funding_receipts) == 1
    assert funding_receipts[0]["source_reference"] == "mp05:demo-cli:fund:poster"
    assert any(
        row["operation_id"] == funding_receipts[0]["operation_id"]
        and row["direction"] == "credit"
        and row["amount"] == FUNDING_AMOUNT
        and row["balance_after"] == FUNDING_AMOUNT
        for row in legs
    )
    expected_events = ["account.created", "agent.registered", "salary.paid"]
    if posting_intent is not None:
        expected_events.extend(("escrow.locked", "task.created"))
    assert sorted(row["event_type"] for row in events) == sorted(expected_events)
    assert not [
        row
        for row in receipts
        if row["operation_kind"] in {"escrow_release", "escrow_split"}
    ]
    assert not [row for row in transactions if row["type"] == "escrow_release"]


def _assert_posting_effects_once(
    projection: dict[str, object],
    baseline: dict[str, object],
    actor_name: str,
    gate: dict[str, Any],
) -> None:
    intent = gate["intent"]
    assert isinstance(intent, dict)
    _assert_global_effects(
        projection,
        baseline,
        actor_name=actor_name,
        actor_id=gate["actor_id"],
        expected_balance=FUNDING_AMOUNT - REWARD,
        posting_intent=intent,
    )
    tasks = projection["tasks"]
    publications = projection["publications"]
    escrows = projection["escrows"]
    receipts = projection["receipts"]
    legs = projection["legs"]
    assert isinstance(tasks, list) and len(tasks) == 1
    assert tasks[0]["task_id"] == intent["task_id"]
    assert tasks[0]["poster_id"] == gate["actor_id"]
    assert tasks[0]["title"] == intent["title"] and tasks[0]["reward"] == REWARD
    assert isinstance(publications, list) and len(publications) == 1
    assert publications[0]["operation_id"] == intent["operation_id"]
    assert isinstance(escrows, list) and len(escrows) == 1
    assert (
        escrows[0]["task_id"] == intent["task_id"] and escrows[0]["status"] == "locked"
    )
    lock_receipts = [
        row for row in receipts if row["operation_id"] == intent["lock_operation_id"]
    ]
    assert len(lock_receipts) == 1
    lock_legs = [
        row for row in legs if row["operation_id"] == intent["lock_operation_id"]
    ]
    assert len(lock_legs) == 1
    assert lock_legs[0]["account_id"] == gate["actor_id"]
    assert lock_legs[0]["direction"] == "debit" and lock_legs[0]["amount"] == REWARD


@pytest.mark.parametrize("phase", ("before_send", "after_response"))
def test_demo_cli_restores_one_run_across_real_process_death(
    tmp_path: Path,
    phase: str,
) -> None:
    ownership = contextlib.ExitStack()
    cleanup_failures: list[BaseException] = []
    runtime: IsolatedRuntime | None = None
    case: Path | None = None
    child: _OwnedChild | None = None
    sentinel: _OwnedChild | None = None
    primary: BaseException | None = None
    proof: dict[str, object] = {"phase": phase}
    try:
        runtime = IsolatedRuntime.create(repo_root=REPO_ROOT, artifacts_root=tmp_path)
        ownership.callback(_capture_cleanup, cleanup_failures, runtime.stop)
        proof["runtime_id"] = runtime.run_id
        case = runtime.run_dir / "demo-cli"
        case.mkdir(mode=0o700)
        gate_path = case / "crash-gate.json"
        crash_log = case / "crash.log"
        sentinel = _start_sentinel(
            case,
            ownership=ownership,
            cleanup_failures=cleanup_failures,
        )
        ownership.callback(
            _capture_cleanup,
            cleanup_failures,
            _assert_owned_alive,
            sentinel,
        )
        runtime.start()
        config_path, scenario_path, changed_path, run_id = _write_inputs(runtime, case)
        baseline = _projection(runtime, f"absent-{run_id}")
        scenario_bytes = scenario_path.read_bytes()
        scenario_hash = hashlib.sha256(scenario_bytes).hexdigest()
        scenario = yaml.safe_load(scenario_bytes)
        actor_name = scenario["agents"][0]["name"]
        state_directory = case / "state"

        child = _launch_crash(
            runtime,
            config_path,
            scenario_path,
            run_id,
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
        assert gate["phase"] == phase
        _kill_at_gate(child, actor_pid)
        assert _alive(sentinel.actor_pid)

        after_crash = _projection(runtime, actor_name)
        assert len(after_crash["actors"]) == 1
        assert len(after_crash["actor_accounts"]) == 1
        keys_after_crash = _key_hashes(state_directory, run_id)
        assert keys_after_crash
        if phase == "before_send":
            assert gate["response"] is None
            _assert_global_effects(
                after_crash,
                baseline,
                actor_name=actor_name,
                actor_id=gate["actor_id"],
                expected_balance=FUNDING_AMOUNT,
                posting_intent=None,
            )
            assert after_crash["tasks"] == []
            assert after_crash["publications"] == []
            assert after_crash["escrows"] == []
        else:
            assert gate["response"]["status_code"] == 201
            assert gate["response"]["body"]["publication_state"] == "published"
            _assert_posting_effects_once(after_crash, baseline, actor_name, gate)

        assert _run_cli(runtime, config_path, scenario_path, run_id) == 0
        final = _projection(runtime, actor_name)
        _assert_posting_effects_once(final, baseline, actor_name, gate)
        assert final["identities"] == after_crash["identities"]
        assert _key_hashes(state_directory, run_id) == keys_after_crash
        assert scenario_path.read_bytes() == scenario_bytes
        assert hashlib.sha256(scenario_path.read_bytes()).hexdigest() == scenario_hash
        if phase == "after_response":
            assert final == after_crash

        assert _run_cli(runtime, config_path, scenario_path, run_id) == 0
        assert _projection(runtime, actor_name) == final
        assert _key_hashes(state_directory, run_id) == keys_after_crash

        before_changed_scope = _projection(runtime, actor_name)
        assert _run_cli(runtime, config_path, changed_path, run_id) != 0
        assert _projection(runtime, actor_name) == before_changed_scope
        assert _key_hashes(state_directory, run_id) == keys_after_crash
        assert _alive(sentinel.actor_pid)
        proof.update(
            {
                "run_id": run_id,
                "scenario_sha256": scenario_hash,
                "gate": gate,
                "after_crash": after_crash,
                "final": final,
                "keys": keys_after_crash,
            }
        )
    except BaseException as exc:
        proof["failure"] = f"{type(exc).__name__}: {exc}"
        primary = exc
    finally:
        proof["child_before_teardown"] = _process_evidence(child)
        proof["sentinel_before_teardown"] = _process_evidence(sentinel)
        ownership.close()
        proof["child_after_teardown"] = _process_evidence(child)
        proof["sentinel_after_teardown"] = _process_evidence(sentinel)
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
            else tmp_path / f"{phase}-proof.json"
        )
        try:
            proof_path.write_text(
                json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        except BaseException as exc:
            cleanup_failures.append(exc)
        print(f"MP05 demo CLI restart evidence: {proof_path}")
    _raise_failures(primary, cleanup_failures, proof_path)
