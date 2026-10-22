"""MP-05 real feeder CLI death and status-first durable posting recovery."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pytest
import yaml
from service_auth import generate_keypair
from service_auth.factory import AgentFactory
from service_auth.task_postings import TaskPostingIntent

from base_agent.agent import BaseAgent
from base_agent.config import load_agent_config
from base_agent.runtime_config import load_runtime_state_config
from base_agent.runtime_intent_models import canonical_json_bytes, canonical_sha256
from base_agent.runtime_intents import FileTaskIntentStore
from base_agent.runtime_postings import FileTaskPostingStore
from base_agent.runtime_task_commands import runtime_owner_from_agent
from tests.e2e.test_mp05_posting_server_restart import _child_audit, _write_durable_json
from tests.e2e.test_mp07_runtime_crash_replay import (
    _kill_and_reap,
    _OwnedChild,
    _terminate_owned_child,
    _wait_for_gate,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.isolated_test_runtime import IsolatedRuntime  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Iterator

    from base_agent.runtime_intent_models import RuntimeOwner

pytestmark = [pytest.mark.integration, pytest.mark.e2e, pytest.mark.asyncio]

_TIMEOUT_SECONDS = 30.0
_INITIAL_BALANCE = 500
_REWARD = 100
_HANDLE = "mp05_feeder"
_RAW_TASK = {
    "title": "MP-05 feeder process restart proof",
    "spec": "Calculate 2 + 3. Return a single integer.",
    "solutions": ["5"],
    "level": 1,
    "problem_type": "addition",
    "solution_note": None,
}


@pytest.fixture(scope="module")
def _feeder_runtime() -> Iterator[IsolatedRuntime]:
    artifacts = Path(tempfile.mkdtemp(prefix="mp05-feeder-restart-"))
    runtime = IsolatedRuntime.create(repo_root=_REPO_ROOT, artifacts_root=artifacts)
    try:
        runtime.start()
        yield runtime
    finally:
        runtime.stop()


def _require_owned_health(runtime: IsolatedRuntime, service: str) -> None:
    response = httpx.get(f"{runtime.service_url(service)}/health", timeout=3.0)
    response.raise_for_status()


# Replace the directory's ordinary-stack preflight with the same checks against
# this module's disposable, process-owned runtime.
@pytest.fixture(autouse=True)
def _require_identity_service(_feeder_runtime: IsolatedRuntime) -> None:
    _require_owned_health(_feeder_runtime, "identity")


@pytest.fixture(autouse=True)
def _require_bank_service(_feeder_runtime: IsolatedRuntime) -> None:
    _require_owned_health(_feeder_runtime, "central-bank")


@pytest.fixture(autouse=True)
def _require_task_board_service(_feeder_runtime: IsolatedRuntime) -> None:
    _require_owned_health(_feeder_runtime, "task-board")


@pytest.fixture(autouse=True)
def _require_reputation_service(_feeder_runtime: IsolatedRuntime) -> None:
    _require_owned_health(_feeder_runtime, "reputation")


@pytest.fixture(autouse=True)
def _require_court_service(_feeder_runtime: IsolatedRuntime) -> None:
    _require_owned_health(_feeder_runtime, "court")


def _write_feeder_config(runtime: IsolatedRuntime, case: Path) -> tuple[Path, bytes]:
    keys = case / "keys"
    keys.mkdir(mode=0o700)
    generate_keypair(_HANDLE, keys)
    roster = case / "roster.yaml"
    roster.write_text(
        yaml.safe_dump({"agents": {_HANDLE: {"name": f"MP05 feeder {uuid.uuid4()}"}}}),
        encoding="utf-8",
    )
    source_bytes = (json.dumps(_RAW_TASK, ensure_ascii=False) + "\n").encode("utf-8")
    (case / "tasks.jsonl").write_bytes(source_bytes)
    config = yaml.safe_load(runtime.agent_config_path.read_text(encoding="utf-8"))
    config["data"] = {"keys_dir": str(keys), "roster_path": str(roster)}
    config["runtime_state"]["directory"] = str(case / "runtime-state")
    config["task_feeder"] = {
        "handle": _HANDLE,
        "tasks_file": "tasks.jsonl",
        "source_max_bytes": 8192,
        "feed_interval_seconds": 1,
        "max_open_tasks": 1,
        "bidding_deadline_seconds": 3600,
        "acceptance_window_seconds": 3600,
        "execution_deadline_seconds": 7200,
        "review_deadline_seconds": 3600,
        "review_interval_seconds": 1,
        "auto_approve_on_error": False,
        "base_reward": _REWARD,
        "reward_per_level": 0,
        "shuffle": False,
        "acceptance_after_seconds": 1800,
        "acceptance_poll_interval_seconds": 1,
        "min_bids_to_accept": 1,
    }
    config_path = case / "agents.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return config_path, source_bytes


def _launch(
    runtime: IsolatedRuntime,
    config_path: Path,
    log_path: Path,
    *,
    phase: str | None,
    gate_path: Path,
) -> _OwnedChild:
    command = ["uv", "run", "--no-sync", "python"]
    if phase is None:
        command.extend(("-m", "task_feeder"))
    else:
        command.extend(
            (
                str(Path(__file__).with_name("mp05_feeder_crash_runner.py")),
                "--phase",
                phase,
                "--gate",
                str(gate_path),
            )
        )
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            command,
            cwd=_REPO_ROOT / "agents",
            env={**runtime.environment(), "AGENT_CONFIG_PATH": str(config_path)},
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return _OwnedChild.capture(process)


def _bind_normal_actor(child: _OwnedChild) -> int:
    deadline = time.monotonic() + _TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        listing = subprocess.run(
            ["/bin/ps", "-axo", "pid=,pgid=,command="],
            check=True,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        for line in listing.stdout.splitlines():
            fields = line.strip().split(maxsplit=2)
            if len(fields) != 3 or int(fields[1]) != child.pgid:
                continue
            command = fields[2].split()
            if Path(command[0]).name.startswith("python") and command[-2:] == [
                "-m",
                "task_feeder",
            ]:
                pid = int(fields[0])
                child.bind_actor(pid)
                return pid
        assert child.process.poll() is None, "normal feeder exited before actor binding"
        time.sleep(0.05)
    raise TimeoutError("normal feeder actor was not found in its owned process group")


def _read_ledger(config_path: Path, owner: RuntimeOwner) -> dict[str, Any]:
    config = load_runtime_state_config(config_path)
    with (
        FileTaskIntentStore(
            root_directory=config.directory,
            owner=owner,
            diagnostic_limit=config.diagnostic_limit,
            max_record_bytes=config.max_record_bytes,
        ) as parent,
        FileTaskPostingStore.attach(parent, max_source_bytes=8192) as postings,
    ):
        records = postings.records()
        assert len(records) == 1
        namespace = parent.identity_directory / "postings"
        record_bytes = (namespace / "records" / "00000000000000000000.json").read_bytes()
        assert record_bytes == canonical_json_bytes(records[0].as_json())
        return {
            "owner": owner.as_json(),
            "head": postings.head(),
            "record": records[0].as_json(),
            "source_bytes_sha256": hashlib.sha256(
                (namespace / "source.jsonl").read_bytes()
            ).hexdigest(),
            "record_bytes_sha256": hashlib.sha256(record_bytes).hexdigest(),
        }


async def _projection(agent: BaseAgent) -> dict[str, Any]:
    return {
        "tasks": await agent.list_tasks(poster_id=agent.agent_id),
        "transactions": await agent.get_transactions(),
        "balance": (await agent.get_balance())["balance"],
    }


async def _wait_for_publication(
    child: _OwnedChild, agent: BaseAgent, intent: TaskPostingIntent
) -> dict[str, Any]:
    deadline = time.monotonic() + _TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        assert child.process.poll() is None, "normal feeder exited during recovery"
        try:
            response = await agent.get_task_posting(intent.operation_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
        else:
            if response.body["publication_state"] == "published":
                # Give the normal CLI time to persist the status observation and
                # execute another capacity check without selecting a second task.
                await asyncio.sleep(1.2)
                return response.body
        await asyncio.sleep(0.1)
    raise TimeoutError("normal feeder failed to recover the exact saved publication")


def _receipt_projection(runtime: IsolatedRuntime, intent: TaskPostingIntent) -> dict[str, Any]:
    database = yaml.safe_load(runtime.gateway_config_path.read_text(encoding="utf-8"))["database"][
        "path"
    ]
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        receipts = connection.execute(
            "SELECT * FROM bank_operation_receipts WHERE source_reference = ? "
            "AND operation_kind = 'escrow_lock'",
            (intent.task_id,),
        ).fetchall()
        legs = connection.execute(
            "SELECT * FROM bank_operation_legs WHERE operation_id = ?",
            (intent.lock_operation_id,),
        ).fetchall()
        escrows = connection.execute(
            "SELECT * FROM bank_escrow WHERE task_id = ?", (intent.task_id,)
        ).fetchall()
    return {
        "receipts": [dict(row) for row in receipts],
        "legs": [dict(row) for row in legs],
        "escrows": [dict(row) for row in escrows],
    }


@pytest.mark.parametrize("phase", ("before_send", "after_response"))
async def test_real_feeder_cli_recovers_exact_posting_after_sigkill(  # noqa: PLR0912, PLR0915
    phase: str,
    _feeder_runtime: IsolatedRuntime,
) -> None:
    runtime = _feeder_runtime
    case = runtime.run_dir / f"feeder-{phase}"
    case.mkdir(mode=0o700)
    config_path, source_bytes = _write_feeder_config(runtime, case)
    gate_path = case / "crash-gate.json"
    crash_log = case / "crash-cli.log"
    restart_log = case / "restart-cli.log"
    crash: _OwnedChild | None = None
    restart: _OwnedChild | None = None
    observer: BaseAgent | None = None
    crash_pid: int | None = None
    restart_pid: int | None = None
    restart_signal: int | None = None
    evidence: dict[str, Any] = {"phase": phase, "runtime_id": runtime.run_id}
    failures: list[BaseException] = []
    try:
        platform = AgentFactory(config_path=runtime.agent_config_path).platform_agent()
        feeder = BaseAgent(load_agent_config(_HANDLE, config_path))
        try:
            await platform.register()
            await feeder.register()
            assert feeder.agent_id is not None
            agent_id = feeder.agent_id
            await platform.create_account(
                agent_id=agent_id,
                initial_balance=_INITIAL_BALANCE,
                operation_id=f"op-{uuid.uuid4()}",
            )
            owner = runtime_owner_from_agent(
                runtime_config=load_runtime_state_config(config_path),
                agent=feeder,
                role="task-feeder",
                profile=_HANDLE,
                handle=_HANDLE,
            )
        finally:
            await feeder.close()
            await platform.close()

        crash = _launch(runtime, config_path, crash_log, phase=phase, gate_path=gate_path)
        gate = await asyncio.to_thread(
            _wait_for_gate, crash, gate_path, timeout_seconds=_TIMEOUT_SECONDS
        )
        crash_pid = gate["pid"]
        assert type(crash_pid) is int
        crash.bind_actor(crash_pid)
        assert gate["pgid"] == crash.pgid and gate["sid"] == crash.sid
        assert gate["phase"] == phase and gate["actor_id"] == agent_id
        intent = TaskPostingIntent.from_json(gate["intent"])
        assert gate["operation_id"] == intent.operation_id
        assert gate["task_id"] == intent.task_id
        assert gate["request_sha256"] == intent.request_sha256
        assert gate["lock_operation_id"] == intent.lock_operation_id
        await asyncio.to_thread(_kill_and_reap, crash, crash_pid)

        # The parent's store is opened only after the killed CLI's group drains.
        before = _read_ledger(config_path, owner)
        saved = before["record"]
        assert saved["state"] == "prepared" and saved["revision"] == 0
        assert saved["publication"] is None and saved["observed_status_code"] is None
        assert saved["diagnostics"] == [] and saved["selection_sequence"] == 0
        assert saved["intent"] == intent.as_json()
        assert saved["request_sha256"] == intent.request_sha256
        assert saved["owner_sha256"] == canonical_sha256(owner.as_json())
        assert saved["raw_task"] == _RAW_TASK
        assert before["head"]["committed_selection_count"] == 1
        assert before["head"]["source_binding"]["source_path"] == str(case / "tasks.jsonl")
        assert before["source_bytes_sha256"] == hashlib.sha256(source_bytes).hexdigest()

        observer = BaseAgent(load_agent_config(_HANDLE, config_path))
        await observer.register()
        assert observer.agent_id == agent_id
        initial = await _projection(observer)
        initial_receipts = _receipt_projection(runtime, intent)
        if phase == "before_send":
            assert gate["response"] is None
            assert initial["tasks"] == [] and initial["balance"] == _INITIAL_BALANCE
            assert not [tx for tx in initial["transactions"] if tx["type"] == "escrow_lock"]
            assert initial_receipts == {"receipts": [], "legs": [], "escrows": []}
            with pytest.raises(httpx.HTTPStatusError) as missing:
                await observer.get_task_posting(intent.operation_id)
            assert missing.value.response.status_code == 404
        else:
            assert gate["response"]["status_code"] == 201
            assert gate["response"]["body"]["publication_state"] == "published"
            assert len(initial["tasks"]) == 1
            assert initial["balance"] == _INITIAL_BALANCE - _REWARD
            assert len(initial_receipts["receipts"]) == 1

        restart = _launch(runtime, config_path, restart_log, phase=None, gate_path=gate_path)
        restart_pid = await asyncio.to_thread(_bind_normal_actor, restart)
        recovered = await _wait_for_publication(restart, observer, intent)
        restart.bind_actor(restart_pid)
        os.kill(restart_pid, signal.SIGTERM)
        restart_signal = int(signal.SIGTERM)
        assert await asyncio.to_thread(restart.process.wait, timeout=_TIMEOUT_SECONDS) == 0
        restart.mark_drained()

        after = _read_ledger(config_path, owner)
        published = after["record"]
        assert after["head"] == before["head"]
        assert after["owner"] == before["owner"]
        assert after["source_bytes_sha256"] == before["source_bytes_sha256"]
        assert published["state"] == "published" and published["revision"] == 1
        assert published["observed_status_code"] == (201 if phase == "before_send" else 200)
        assert published["publication"] == recovered
        assert {key for key in saved if saved[key] != published[key]} == {
            "state",
            "revision",
            "observed_status_code",
            "publication",
        }
        final = await _projection(observer)
        assert len(final["tasks"]) == 1 and final["tasks"][0]["task_id"] == intent.task_id
        task = await observer.get_task(intent.task_id)
        assert task["title"] == _RAW_TASK["title"] and task["spec"] == _RAW_TASK["spec"]
        assert task["poster_id"] == agent_id and task["reward"] == _REWARD
        assert task["status"] == "open" and task["escrow_id"] == recovered["escrow_id"]
        assert final["balance"] == _INITIAL_BALANCE - _REWARD
        locks = [tx for tx in final["transactions"] if tx["type"] == "escrow_lock"]
        assert len(locks) == 1 and locks[0]["reference"] == intent.task_id
        assert locks[0]["amount"] == _REWARD
        receipts = _receipt_projection(runtime, intent)
        assert len(receipts["receipts"]) == len(receipts["legs"]) == len(receipts["escrows"]) == 1
        receipt, leg, escrow = (
            receipts["receipts"][0],
            receipts["legs"][0],
            receipts["escrows"][0],
        )
        assert receipt["operation_id"] == leg["operation_id"] == intent.lock_operation_id
        assert receipt["escrow_id"] == escrow["escrow_id"] == recovered["escrow_id"]
        assert leg["account_id"] == agent_id and leg["direction"] == "debit"
        assert leg["amount"] == _REWARD and leg["balance_after"] == _INITIAL_BALANCE - _REWARD
        assert leg["transaction_id"] == locks[0]["tx_id"]
        assert escrow["status"] == "locked" and escrow["payer_account_id"] == agent_id
        if phase == "after_response":
            assert final == initial and receipts == initial_receipts
            assert recovered == gate["response"]["body"]
        log = restart_log.read_text(encoding="utf-8")
        lookup = f"GET {runtime.service_url('task-board')}/task-publications/{intent.operation_id}"
        submit = f"POST {runtime.service_url('task-board')}/tasks "
        assert lookup in log
        if phase == "before_send":
            assert log.index(lookup) < log.index(submit)
            assert log.count(submit) == 1
        else:
            assert submit not in log
        assert "Received shutdown signal" in log and "Task Feeder client closed" in log
        evidence.update(
            {
                "gate": gate,
                "prepared_ledger": before,
                "published_ledger": after,
                "initial": initial,
                "final": final,
                "receipt_projection": receipts,
                "status_first_lookup": lookup,
                "recovered_publication": recovered,
            }
        )
    except BaseException as exc:
        evidence["failure"] = f"{type(exc).__name__}: {exc}"
        failures.append(exc)
    finally:
        for child in (crash, restart):
            if child is None:
                continue
            try:
                _terminate_owned_child(child)
            except BaseException as exc:
                failures.append(exc)
        if observer is not None:
            try:
                await observer.close()
            except BaseException as exc:
                failures.append(exc)
        evidence["children"] = [
            _child_audit("crash", crash, crash_pid, int(signal.SIGKILL)),
            _child_audit("restart", restart, restart_pid, restart_signal),
        ]
        evidence["cleanup_failures"] = [str(exc) for exc in failures[1:]]
        _write_durable_json(case / "proof.json", evidence)
        print(f"MP05 feeder restart evidence: {case}")
    if failures:
        raise BaseExceptionGroup(f"MP05 feeder restart proof failed; evidence: {case}", failures)
