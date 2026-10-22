"""MP-05 real Task Board process restart after a committed Bank lock."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pytest
import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from service_auth.config import AgentConfig
from service_auth.factory import AgentFactory
from service_auth.financial_identity import operation_id_for
from service_auth.jws import TokenPolicy

from base_agent.agent import BaseAgent
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
    from service_auth.task_postings import TaskPostingResponse

    from base_agent.platform import PlatformAgent

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

_INITIAL_BALANCE = 500
_REWARD = 100
_TITLE = "MP-05 server restart proof"
_SPEC = "Recover one exact funded publication after the posting server dies."
_BIDDING_SECONDS = 3_600
_ACCEPTANCE_SECONDS = 3_600
_EXECUTION_SECONDS = 7_200
_REVIEW_SECONDS = 3_600
_GATE_TIMEOUT_SECONDS = 30.0
_HEALTH_TIMEOUT_SECONDS = 30.0
_RECOVERY_TIMEOUT_SECONDS = 12.0
_POLL_INTERVAL_SECONDS = 0.2
_DEPENDENCY_SERVICES = (
    "db-gateway",
    "identity",
    "central-bank",
    "reputation",
    "court",
    "ui",
)
_TASK_BOARD_ROOT = _REPO_ROOT / "services" / "task-board"
_CRASH_SERVER = _TASK_BOARD_ROOT / "tests" / "integration" / "mp05_crash_server.py"


class _ReservedPort:
    def __init__(self, port: int = 0) -> None:
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(("127.0.0.1", port))
        self.socket.listen()
        self.port = int(self.socket.getsockname()[1])

    def close(self) -> None:
        self.socket.close()


def _write_durable_json(path: Path, value: object) -> None:
    staged = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        staged.replace(path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


def _write_child_config(runtime: IsolatedRuntime, child_root: Path, port: int) -> Path:
    source = yaml.safe_load((_TASK_BOARD_ROOT / "config.yaml").read_text(encoding="utf-8"))
    assert isinstance(source, dict)
    child_root.mkdir(mode=0o700)
    logs = child_root / "logs"
    assets = child_root / "assets"
    logs.mkdir(mode=0o700)
    assets.mkdir(mode=0o700)
    source["service"]["name"] = "task-board-mp05-restart-test"
    source["server"]["host"] = "127.0.0.1"
    source["server"]["port"] = port
    source["logging"]["directory"] = str(logs)
    source["database"]["path"] = str(child_root / "task-board.db")
    source["assets"]["storage_path"] = str(assets)
    source["recovery"]["maintenance_lock_path"] = str(runtime.run_dir / "maintenance.lock")
    source["identity"]["base_url"] = runtime.service_url("identity")
    source["central_bank"]["base_url"] = runtime.service_url("central-bank")
    source["platform"]["agent_config_path"] = str(runtime.agent_config_path)
    source["db_gateway"]["url"] = runtime.service_url("db-gateway")
    source["db_gateway"]["signing"]["private_key_path"] = str(
        runtime.run_dir / "keys" / "gateway-task-board.key"
    )
    config_path = child_root / "task-board.yaml"
    config_path.write_text(yaml.safe_dump(source, sort_keys=False), encoding="utf-8")
    return config_path


def _start_child(
    *,
    mode: str,
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
        str(_CRASH_SERVER),
        "--mode",
        mode,
        "--fd",
        str(reservation.socket.fileno()),
    ]
    if gate_path is not None:
        command.extend(("--gate", str(gate_path)))
    environment = {**os.environ, "CONFIG_PATH": str(config_path)}
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            command,
            cwd=_TASK_BOARD_ROOT,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            pass_fds=(reservation.socket.fileno(),),
        )
    try:
        child = _OwnedChild.capture(process)
    finally:
        reservation.close()
    child.bind_actor(process.pid)
    return child


async def _wait_for_health(child: _OwnedChild, url: str, log_path: Path) -> None:
    deadline = time.monotonic() + _HEALTH_TIMEOUT_SECONDS
    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            return_code = child.process.poll()
            if return_code is not None:
                raise AssertionError(
                    f"owned Task Board exited with {return_code}:\n{log_path.read_text()}"
                )
            try:
                response = await client.get(f"{url}/health", timeout=0.5)
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.05)
    raise TimeoutError(f"owned Task Board did not become healthy: {log_path}")


async def _make_funded_agent(
    runtime: IsolatedRuntime,
    task_board_url: str,
) -> tuple[PlatformAgent, BaseAgent]:
    platform = AgentFactory(config_path=runtime.agent_config_path).platform_agent()
    private_key = Ed25519PrivateKey.generate()
    agent = BaseAgent(
        AgentConfig(
            name="MP05 nested restart poster",
            private_key=private_key,
            public_key=private_key.public_key(),
            identity_url=runtime.service_url("identity"),
            bank_url=runtime.service_url("central-bank"),
            task_board_url=task_board_url,
            reputation_url=runtime.service_url("reputation"),
            court_url=runtime.service_url("court"),
            token_ttl_seconds=300,
            token_policy=TokenPolicy(max_lifetime_seconds=300, clock_skew_seconds=30),
        )
    )
    try:
        await platform.register()
        await agent.register()
        assert agent.agent_id is not None
        await platform.create_account(
            agent_id=agent.agent_id,
            initial_balance=_INITIAL_BALANCE,
            operation_id=f"op-{uuid.uuid4()}",
        )
    except BaseException:
        await agent.close()
        await platform.close()
        raise
    return platform, agent


def _matching_transactions(
    transactions: list[dict[str, Any]],
    *,
    transaction_type: str,
    reference: str,
) -> list[dict[str, Any]]:
    return [
        transaction
        for transaction in transactions
        if transaction["type"] == transaction_type and transaction["reference"] == reference
    ]


async def _bank_projection(agent: BaseAgent, task_id: str, escrow_id: str) -> dict[str, Any]:
    transactions = await agent.get_transactions()
    balance = await agent.get_balance()
    return {
        "locks": _matching_transactions(
            transactions,
            transaction_type="escrow_lock",
            reference=task_id,
        ),
        "refunds": _matching_transactions(
            transactions,
            transaction_type="escrow_release",
            reference=escrow_id,
        ),
        "balance": balance["balance"],
    }


async def _public_projection(
    agent: BaseAgent,
    task_id: str,
    escrow_id: str,
) -> dict[str, Any]:
    summaries = await agent.list_tasks()
    matching_summaries = [summary for summary in summaries if summary["task_id"] == task_id]
    full_tasks = [await agent.get_task(str(summary["task_id"])) for summary in matching_summaries]
    return {
        "summaries": matching_summaries,
        "tasks": full_tasks,
        **await _bank_projection(agent, task_id, escrow_id),
    }


def _is_exact_funded(
    projection: dict[str, Any],
    *,
    task_id: str,
    escrow_id: str,
    poster_id: str,
) -> bool:
    tasks = projection["tasks"]
    if len(projection["summaries"]) != 1 or len(tasks) != 1:
        return False
    task = tasks[0]
    expected = {
        "task_id": task_id,
        "poster_id": poster_id,
        "title": _TITLE,
        "spec": _SPEC,
        "reward": _REWARD,
        "bidding_deadline_seconds": _BIDDING_SECONDS,
        "acceptance_window_seconds": _ACCEPTANCE_SECONDS,
        "deadline_seconds": _EXECUTION_SECONDS,
        "review_deadline_seconds": _REVIEW_SECONDS,
        "escrow_id": escrow_id,
        "status": "open",
    }
    return (
        all(task[field] == value for field, value in expected.items())
        and len(projection["locks"]) == 1
        and projection["refunds"] == []
        and projection["balance"] == _INITIAL_BALANCE - _REWARD
    )


async def _poll_recovery(
    agent: BaseAgent,
    *,
    task_id: str,
    escrow_id: str,
    poster_id: str,
) -> dict[str, Any]:
    deadline = time.monotonic() + _RECOVERY_TIMEOUT_SECONDS
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last = await _public_projection(agent, task_id, escrow_id)
        if _is_exact_funded(
            last,
            task_id=task_id,
            escrow_id=escrow_id,
            poster_id=poster_id,
        ):
            return last
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
    assert last is not None
    return last


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _child_audit(
    phase: str,
    child: _OwnedChild | None,
    actor_pid: int | None,
    kill_signal: int | None,
) -> dict[str, object]:
    if child is None:
        return {"phase": phase, "started": False}
    return {
        "phase": phase,
        "started": True,
        "launcher_pid": child.process.pid,
        "actor_pid": actor_pid,
        "pgid": child.pgid,
        "sid": child.sid,
        "kill_signal": kill_signal,
        "returncode": child.process.returncode,
        "group_drained": child.drained,
    }


async def test_lock_commit_then_task_board_sigkill_converges_after_restart(  # noqa: PLR0912, PLR0915
    tmp_path: Path,
) -> None:
    runtime = IsolatedRuntime.create(
        repo_root=_REPO_ROOT,
        artifacts_root=tmp_path / "nested-runtime",
        services=_DEPENDENCY_SERVICES,
    )
    reservation = _ReservedPort()
    child_url = f"http://127.0.0.1:{reservation.port}"
    child_root = runtime.run_dir / "mp05-task-board"
    gate_path = child_root / "lock-commit-gate.json"
    audit_path = child_root / "process-audit.json"
    crash_log = child_root / "crash-server.log"
    restart_log = child_root / "restart-server.log"
    crash_child: _OwnedChild | None = None
    restart_child: _OwnedChild | None = None
    crash_actor_pid: int | None = None
    restart_actor_pid: int | None = None
    crash_signal: int | None = None
    restart_signal: int | None = None
    sentinel_process = subprocess.Popen(["/bin/sleep", "60"], start_new_session=True)
    sentinel = _OwnedChild.capture(sentinel_process)
    sentinel.bind_actor(sentinel_process.pid)
    sentinel_after_crash = False
    sentinel_after_children_cleanup = False
    platform: PlatformAgent | None = None
    agent: BaseAgent | None = None
    posting: asyncio.Task[TaskPostingResponse] | None = None
    primary: BaseException | None = None
    cleanup_failures: list[BaseException] = []
    try:
        runtime.start()
        config_path = _write_child_config(runtime, child_root, reservation.port)
        platform, agent = await _make_funded_agent(runtime, child_url)
        assert agent.agent_id is not None

        crash_child = _start_child(
            mode="crash",
            config_path=config_path,
            reservation=reservation,
            gate_path=gate_path,
            log_path=crash_log,
        )
        await _wait_for_health(crash_child, child_url, crash_log)
        intent = agent.prepare_task_posting(
            title=_TITLE,
            spec=_SPEC,
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
        receipt_envelope = gate["receipt"]
        receipt = receipt_envelope["receipt"]
        task_id = str(gate["task_id"])
        escrow_id = str(receipt["escrow_id"])
        assert gate["poster_id"] == agent.agent_id
        assert gate["amount"] == _REWARD
        assert gate["lock_operation_id"] == operation_id_for("task-lock", (task_id, agent.agent_id))
        assert receipt_envelope["outcome"] == "native_receipt"
        assert receipt["receipt_version"] == 1
        assert receipt["operation_kind"] == "escrow_lock"
        assert receipt["operation_id"] == gate["lock_operation_id"]
        assert receipt["source_reference"] == task_id
        assert receipt["result"] == {"escrow_status_at_commit": "locked"}
        assert len(receipt["legs"]) == 1
        payer_leg = receipt["legs"][0]
        assert payer_leg["role"] == "payer"
        assert payer_leg["account_id"] == agent.agent_id
        assert payer_leg["direction"] == "debit"
        assert payer_leg["amount"] == _REWARD
        assert payer_leg["balance_after"] == _INITIAL_BALANCE - _REWARD
        assert isinstance(payer_leg["transaction_id"], str) and payer_leg["transaction_id"]
        current_escrow = receipt_envelope["current_escrow"]
        assert current_escrow["escrow_id"] == escrow_id
        assert current_escrow["payer_account_id"] == agent.agent_id
        assert current_escrow["task_id"] == task_id
        assert current_escrow["amount"] == _REWARD
        assert current_escrow["status"] == "locked"
        assert current_escrow["resolved_at"] is None

        before_kill = await _public_projection(agent, task_id, escrow_id)
        assert before_kill["summaries"] == []
        assert before_kill["tasks"] == []
        assert before_kill["refunds"] == []
        assert before_kill["balance"] == _INITIAL_BALANCE - _REWARD
        assert len(before_kill["locks"]) == 1
        assert before_kill["locks"][0]["amount"] == _REWARD
        assert before_kill["locks"][0]["tx_id"] == payer_leg["transaction_id"]
        pending = await agent.get_task_posting(intent.operation_id)
        assert pending.body["publication_state"] == "awaiting_lock"
        assert pending.body["task_id"] == intent.task_id == task_id
        assert pending.body["request_sha256"] == intent.request_sha256

        crash_signal = int(signal.SIGKILL)
        await asyncio.to_thread(_kill_and_reap, crash_child, crash_actor_pid)
        result = await asyncio.gather(posting, return_exceptions=True)
        assert len(result) == 1 and isinstance(result[0], httpx.HTTPError), result
        unresolved = await _bank_projection(agent, task_id, escrow_id)
        assert unresolved == {
            "locks": before_kill["locks"],
            "refunds": [],
            "balance": _INITIAL_BALANCE - _REWARD,
        }
        sentinel_after_crash = _alive(sentinel.process.pid)
        assert sentinel_after_crash

        restart_reservation = _ReservedPort(reservation.port)
        restart_child = _start_child(
            mode="normal",
            config_path=config_path,
            reservation=restart_reservation,
            gate_path=None,
            log_path=restart_log,
        )
        restart_actor_pid = restart_child.actor_pid
        await _wait_for_health(restart_child, child_url, restart_log)
        after_restart = await _poll_recovery(
            agent,
            task_id=task_id,
            escrow_id=escrow_id,
            poster_id=agent.agent_id,
        )
        recovered = await agent.get_task_posting(intent.operation_id)
        assert recovered.body["publication_state"] == "published"
        assert recovered.body["task_id"] == task_id
        assert recovered.body["escrow_id"] == escrow_id
        assert recovered.body["request_sha256"] == intent.request_sha256
        diagnostics = {
            "gate": gate,
            "before_kill": before_kill,
            "unresolved_before_restart": unresolved,
            "after_restart": after_restart,
            "crash_log": crash_log.read_text(encoding="utf-8"),
            "restart_log": restart_log.read_text(encoding="utf-8"),
        }
        assert _is_exact_funded(
            after_restart,
            task_id=task_id,
            escrow_id=escrow_id,
            poster_id=agent.agent_id,
        ), diagnostics
        assert _alive(sentinel.process.pid), diagnostics
    except BaseException as exc:
        primary = exc
    finally:
        if posting is not None and not posting.done():
            posting.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await posting
        for phase, child in (("crash", crash_child), ("restart", restart_child)):
            if child is None:
                continue
            try:
                if not child.drained:
                    _terminate_owned_child(child)
                    if phase == "restart":
                        restart_signal = int(signal.SIGKILL)
            except BaseException as exc:
                cleanup_failures.append(exc)
        sentinel_after_children_cleanup = _alive(sentinel.process.pid)
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
            runtime.stop()
        except BaseException as exc:
            cleanup_failures.append(exc)
        audit = {
            "runtime_id": runtime.run_id,
            "runtime_dir": str(runtime.run_dir),
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
        }
        try:
            _write_durable_json(audit_path, audit)
            print(
                json.dumps({"mp05_process_audit": str(audit_path), "audit": audit}, sort_keys=True)
            )
        except BaseException as exc:
            cleanup_failures.append(exc)

    if primary is not None and not cleanup_failures:
        raise primary
    failures = ([primary] if primary is not None else []) + cleanup_failures
    if failures:
        raise BaseExceptionGroup("MP-05 server restart proof failed", failures)
