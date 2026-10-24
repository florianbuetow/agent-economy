"""MP-05 real delayed Bank request racing an exact fresh-token retry."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import sqlite3
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
from service_auth import parse_jws
from service_auth.config import AgentConfig
from service_auth.factory import AgentFactory
from service_auth.jws import TokenPolicy

from base_agent.agent import BaseAgent
from tests.e2e.test_mp05_posting_server_restart import (
    _alive,
    _child_audit,
    _ReservedPort,
    _terminate_owned_child,
    _wait_for_health,
    _write_durable_json,
)
from tests.e2e.test_mp07_runtime_crash_replay import (
    _kill_and_reap,
    _OwnedChild,
    _wait_for_gate,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.isolated_test_runtime import (  # noqa: E402
    IsolatedRuntime,
    _release_owned_launch,
    _start_owned_launch,
)

if TYPE_CHECKING:
    from service_auth.task_postings import TaskPostingIntent, TaskPostingResponse

    from base_agent.platform import PlatformAgent

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

_INITIAL_BALANCE = 500
_REWARD = 100
_TITLE = "MP-05 delayed Bank request proof"
_SPEC = "Converge one posting while an authenticated original lock request remains alive."
_BIDDING_SECONDS = 3_600
_ACCEPTANCE_SECONDS = 3_600
_EXECUTION_SECONDS = 7_200
_REVIEW_SECONDS = 3_600
_TOKEN_TTL_SECONDS = 2
_GATE_TIMEOUT_SECONDS = 30.0
_CENTRAL_BANK_ROOT = _REPO_ROOT / "services" / "central-bank"
_TASK_BOARD_ROOT = _REPO_ROOT / "services" / "task-board"
_BANK_SERVER = Path(__file__).with_name("mp05_late_bank_server.py")
_BOARD_SERVER = Path(__file__).with_name("mp05_late_board_server.py")


# This module creates and health-checks its own complete disposable dependency
# stack inside each scenario. Shadow the directory fixtures that probe the
# ordinary developer stack before a test can create those owned processes.
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


def _rewrite_yaml(path: Path, update: Any) -> None:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    update(value)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def _configure_test_profile(
    runtime: IsolatedRuntime,
    *,
    bank_url: str,
    task_board_url: str,
) -> None:
    def update_identity(config: dict[str, Any]) -> None:
        config["token_validation"]["clock_skew_seconds"] = 0

    def update_agents(config: dict[str, Any]) -> None:
        config["platform"]["bank_url"] = bank_url
        config["platform"]["task_board_url"] = task_board_url
        config["signing"]["token_ttl_seconds"] = _TOKEN_TTL_SECONDS
        config["signing"]["max_lifetime_seconds"] = _TOKEN_TTL_SECONDS
        config["signing"]["clock_skew_seconds"] = 0

    _rewrite_yaml(runtime.config_paths["identity"], update_identity)
    _rewrite_yaml(runtime.agent_config_path, update_agents)


def _write_bank_config(runtime: IsolatedRuntime, root: Path, port: int) -> Path:
    config = yaml.safe_load((_CENTRAL_BANK_ROOT / "config.yaml").read_text(encoding="utf-8"))
    assert isinstance(config, dict)
    root.mkdir(mode=0o700)
    config["service"]["name"] = "central-bank-mp05-late-request-test"
    config["server"]["host"] = "127.0.0.1"
    config["server"]["port"] = port
    config["logging"]["directory"] = str(root / "logs")
    config["identity"]["base_url"] = runtime.service_url("identity")
    config["platform"]["agent_config_path"] = str(runtime.agent_config_path)
    config["db_gateway"]["url"] = runtime.service_url("db-gateway")
    config["db_gateway"]["signing"]["private_key_path"] = str(
        runtime.run_dir / "keys" / "gateway-central-bank.key"
    )
    (root / "logs").mkdir(mode=0o700)
    path = root / "central-bank.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


def _write_board_config(
    runtime: IsolatedRuntime,
    root: Path,
    *,
    port: int,
    bank_url: str,
) -> Path:
    config = yaml.safe_load((_TASK_BOARD_ROOT / "config.yaml").read_text(encoding="utf-8"))
    assert isinstance(config, dict)
    root.mkdir(mode=0o700)
    logs = root / "logs"
    assets = root / "assets"
    logs.mkdir(mode=0o700)
    assets.mkdir(mode=0o700)
    config["service"]["name"] = "task-board-mp05-late-request-test"
    config["server"]["host"] = "127.0.0.1"
    config["server"]["port"] = port
    config["logging"]["directory"] = str(logs)
    config["database"]["path"] = str(root / "task-board.db")
    config["assets"]["storage_path"] = str(assets)
    config["recovery"]["maintenance_lock_path"] = str(runtime.run_dir / "maintenance.lock")
    config["identity"]["base_url"] = runtime.service_url("identity")
    config["central_bank"]["base_url"] = bank_url
    config["platform"]["agent_config_path"] = str(runtime.agent_config_path)
    config["db_gateway"]["url"] = runtime.service_url("db-gateway")
    config["db_gateway"]["signing"]["private_key_path"] = str(
        runtime.run_dir / "keys" / "gateway-task-board.key"
    )
    path = root / "task-board.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


def _start_bank_child(
    *,
    config_path: Path,
    reservation: _ReservedPort,
    gate_root: Path,
    log_path: Path,
) -> _OwnedChild:
    command = [
        "uv",
        "run",
        "--no-sync",
        "python",
        str(_BANK_SERVER),
        "--fd",
        str(reservation.socket.fileno()),
        "--gate-root",
        str(gate_root),
    ]
    return _start_gated_child(
        command,
        cwd=_CENTRAL_BANK_ROOT,
        environment={**os.environ, "CONFIG_PATH": str(config_path)},
        reservation=reservation,
        log_path=log_path,
    )


def _start_board_child(
    *,
    config_path: Path,
    reservation: _ReservedPort,
    ready_path: Path,
    log_path: Path,
) -> _OwnedChild:
    command = [
        "uv",
        "run",
        "--no-sync",
        "python",
        str(_BOARD_SERVER),
        "--fd",
        str(reservation.socket.fileno()),
        "--ready",
        str(ready_path),
    ]
    return _start_gated_child(
        command,
        cwd=_TASK_BOARD_ROOT,
        environment={**os.environ, "CONFIG_PATH": str(config_path)},
        reservation=reservation,
        log_path=log_path,
    )


def _start_gated_child(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    reservation: _ReservedPort | None,
    log_path: Path,
) -> _OwnedChild:
    pass_fds = () if reservation is None else (reservation.socket.fileno(),)
    with log_path.open("ab") as log:
        process, _pgid, _sid, launch_descriptor = _start_owned_launch(
            command,
            cwd=str(cwd),
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            pass_fds=pass_fds,
        )
    pending_descriptor: int | None = launch_descriptor
    try:
        child = _OwnedChild.capture(process)
        child.bind_actor(process.pid)
        if reservation is not None:
            reservation.close()
        descriptor_to_release = pending_descriptor
        pending_descriptor = None
        _release_owned_launch(descriptor_to_release)
        return child
    except BaseException:
        if pending_descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(pending_descriptor)
        if reservation is not None:
            reservation.close()
        with contextlib.suppress(BaseException):
            _terminate_owned_child(_OwnedChild.capture(process))
        raise


def _start_sentinel(case_root: Path) -> _OwnedChild:
    return _start_gated_child(
        ["/bin/sleep", "60"],
        cwd=case_root,
        environment=dict(os.environ),
        reservation=None,
        log_path=case_root / "sentinel.log",
    )


async def _wait_for_service(child: _OwnedChild, url: str, log_path: Path) -> None:
    deadline = time.monotonic() + _GATE_TIMEOUT_SECONDS
    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            if child.process.poll() is not None:
                raise AssertionError(
                    f"owned Central Bank exited with {child.process.returncode}:\n"
                    f"{log_path.read_text(encoding='utf-8')}"
                )
            try:
                response = await client.get(f"{url}/health", timeout=0.5)
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.05)
    raise TimeoutError(f"owned Central Bank did not become healthy: {log_path}")


async def _make_funded_agent(
    runtime: IsolatedRuntime,
    *,
    bank_url: str,
    task_board_url: str,
) -> tuple[PlatformAgent, BaseAgent]:
    platform = AgentFactory(config_path=runtime.agent_config_path).platform_agent()
    private_key = Ed25519PrivateKey.generate()
    agent = BaseAgent(
        AgentConfig(
            name="MP05 late Bank poster",
            private_key=private_key,
            public_key=private_key.public_key(),
            identity_url=runtime.service_url("identity"),
            bank_url=bank_url,
            task_board_url=task_board_url,
            reputation_url="http://127.0.0.1:1",
            court_url="http://127.0.0.1:1",
            token_ttl_seconds=_TOKEN_TTL_SECONDS,
            token_policy=TokenPolicy(
                max_lifetime_seconds=_TOKEN_TTL_SECONDS,
                clock_skew_seconds=0,
            ),
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


def _posting_tokens(agent: BaseAgent, intent: TaskPostingIntent) -> tuple[str, str]:
    task_token = agent._sign_jws(
        {
            "action": "create_task",
            "protocol_version": intent.protocol_version,
            "operation_id": intent.operation_id,
            "task_id": intent.task_id,
            "poster_id": intent.actor_id,
            "lock_operation_id": intent.lock_operation_id,
            "title": intent.title,
            "spec": intent.spec,
            "reward": intent.reward,
            "bidding_deadline_seconds": intent.bidding_deadline_seconds,
            "acceptance_window_seconds": intent.acceptance_window_seconds,
            "execution_deadline_seconds": intent.execution_deadline_seconds,
            "review_deadline_seconds": intent.review_deadline_seconds,
        },
        audience="task-board",
        resource="POST /tasks",
    )
    escrow_token = agent._sign_jws(
        {
            "action": "escrow_lock",
            "operation_id": intent.lock_operation_id,
            "task_id": intent.task_id,
            "amount": intent.reward,
            "agent_id": intent.actor_id,
        },
        audience="central-bank",
        resource="POST /escrow/lock",
    )
    return task_token, escrow_token


async def _post_original(
    task_board_url: str,
    task_token: str,
    escrow_token: str,
) -> httpx.Response:
    async with httpx.AsyncClient(timeout=20.0) as client:
        return await client.post(
            f"{task_board_url}/tasks",
            json={"task_token": task_token, "escrow_token": escrow_token},
        )


async def _prove_expired(bank_url: str, token: str, exp: int) -> dict[str, Any]:
    deadline = time.monotonic() + _TOKEN_TTL_SECONDS + 2.0
    while int(time.time()) < exp and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    assert int(time.time()) >= exp, "bounded test token expiration window elapsed"
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{bank_url}/escrow/lock",
            json={"token": token},
            timeout=2.0,
        )
    body = response.json()
    assert response.status_code == 401, body
    assert body == {
        "error": "token_expired",
        "message": "JWS token has expired",
        "details": {},
    }
    return {"status_code": response.status_code, "body": body, "proved_at": int(time.time())}


def _touch_release(path: Path) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)


def _read_projection(runtime: IsolatedRuntime, intent: TaskPostingIntent) -> dict[str, Any]:
    config = yaml.safe_load(runtime.gateway_config_path.read_text(encoding="utf-8"))
    database = config["database"]["path"]
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row

        def rows(statement: str, parameters: tuple[object, ...]) -> list[dict[str, Any]]:
            return [dict(row) for row in connection.execute(statement, parameters).fetchall()]

        return {
            "publications": rows(
                "SELECT * FROM board_task_publications WHERE operation_id = ?",
                (intent.operation_id,),
            ),
            "tasks": rows("SELECT * FROM board_tasks WHERE task_id = ?", (intent.task_id,)),
            "receipts": rows(
                "SELECT * FROM bank_operation_receipts WHERE operation_id = ?",
                (intent.lock_operation_id,),
            ),
            "legs": rows(
                "SELECT * FROM bank_operation_legs WHERE operation_id = ?",
                (intent.lock_operation_id,),
            ),
            "escrows": rows("SELECT * FROM bank_escrow WHERE task_id = ?", (intent.task_id,)),
            "debits": rows(
                "SELECT * FROM bank_transactions WHERE account_id = ? "
                "AND type = 'escrow_lock' AND reference = ?",
                (intent.actor_id, intent.task_id),
            ),
            "lock_events": rows(
                "SELECT * FROM events WHERE event_source = 'central-bank' "
                "AND event_type = 'escrow.locked' AND task_id = ?",
                (intent.task_id,),
            ),
            "creation_events": rows(
                "SELECT * FROM events WHERE event_source = 'task-board' "
                "AND event_type = 'task.created' AND task_id = ?",
                (intent.task_id,),
            ),
            "all_publications": rows("SELECT * FROM board_task_publications", ()),
            "all_tasks": rows("SELECT * FROM board_tasks", ()),
            "all_escrows": rows("SELECT * FROM bank_escrow", ()),
            "all_lock_receipts": rows(
                "SELECT * FROM bank_operation_receipts WHERE operation_kind = 'escrow_lock'",
                (),
            ),
            "all_lock_legs": rows(
                "SELECT l.* FROM bank_operation_legs AS l "
                "JOIN bank_operation_receipts AS r ON r.operation_id = l.operation_id "
                "WHERE r.operation_kind = 'escrow_lock'",
                (),
            ),
            "all_lock_debits": rows(
                "SELECT * FROM bank_transactions WHERE type = 'escrow_lock'",
                (),
            ),
            "all_lock_events": rows(
                "SELECT * FROM events WHERE event_source = 'central-bank' "
                "AND event_type = 'escrow.locked'",
                (),
            ),
            "all_creation_events": rows(
                "SELECT * FROM events WHERE event_source = 'task-board' "
                "AND event_type = 'task.created'",
                (),
            ),
            "settlement_receipts": rows(
                "SELECT * FROM bank_operation_receipts "
                "WHERE operation_kind IN ('escrow_release', 'escrow_split')",
                (),
            ),
            "settlement_legs": rows(
                "SELECT l.* FROM bank_operation_legs AS l "
                "JOIN bank_operation_receipts AS r ON r.operation_id = l.operation_id "
                "WHERE r.operation_kind IN ('escrow_release', 'escrow_split')",
                (),
            ),
            "settlement_transactions": rows(
                "SELECT * FROM bank_transactions WHERE type IN ('escrow_release', 'escrow_split')",
                (),
            ),
            "settlement_events": rows(
                "SELECT * FROM events WHERE event_source = 'central-bank' "
                "AND event_type IN ('escrow.released', 'escrow.split')",
                (),
            ),
            "accounts": rows("SELECT * FROM bank_accounts", ()),
            "issuance_legs": rows(
                "SELECT l.* FROM bank_operation_legs AS l "
                "JOIN bank_operation_receipts AS r ON r.operation_id = l.operation_id "
                "WHERE r.operation_kind = 'account_issuance'",
                (),
            ),
        }


@pytest.mark.parametrize("winner", ("retry", "original"))
async def test_late_authenticated_bank_request_and_fresh_retry_converge_once(  # noqa: PLR0912, PLR0915
    tmp_path: Path,
    winner: str,
) -> None:
    # Construction has a temporary cleanup owner until every setup artifact is
    # valid. Only then is ownership transferred to the scenario's audited finally.
    with contextlib.ExitStack() as setup:
        runtime = IsolatedRuntime.create(
            repo_root=_REPO_ROOT,
            artifacts_root=tmp_path / "late-bank-runtime",
            services=("db-gateway", "identity"),
        )
        setup.callback(runtime.stop)
        bank_reservation = _ReservedPort()
        setup.callback(bank_reservation.close)
        board_reservation = _ReservedPort()
        setup.callback(board_reservation.close)
        bank_url = f"http://127.0.0.1:{bank_reservation.port}"
        board_url = f"http://127.0.0.1:{board_reservation.port}"
        case_root = runtime.run_dir / f"late-bank-{winner}"
        bank_root = case_root / "bank"
        board_root = case_root / "board"
        gate_root = case_root / "gate"
        bank_log = case_root / "bank.log"
        first_board_log = case_root / "board-first.log"
        retry_board_log = case_root / "board-retry.log"
        first_board_ready = case_root / "board-first-ready.json"
        retry_board_ready = case_root / "board-retry-ready.json"
        audit_path = case_root / "audit.json"
        gate_root.mkdir(parents=True, mode=0o700)
        _configure_test_profile(runtime, bank_url=bank_url, task_board_url=board_url)
        bank_config = _write_bank_config(runtime, bank_root, bank_reservation.port)
        board_config = _write_board_config(
            runtime,
            board_root,
            port=board_reservation.port,
            bank_url=bank_url,
        )
        sentinel = _start_sentinel(case_root)
        setup.callback(_terminate_owned_child, sentinel)
        setup.pop_all()

    bank_child: _OwnedChild | None = None
    first_board: _OwnedChild | None = None
    retry_board: _OwnedChild | None = None
    platform: PlatformAgent | None = None
    agent: BaseAgent | None = None
    original_request: asyncio.Task[httpx.Response] | None = None
    retry_request: asyncio.Task[TaskPostingResponse] | None = None
    intent: TaskPostingIntent | None = None
    retry_reservation: _ReservedPort | None = None
    evidence: dict[str, Any] = {"winner": winner, "runtime_id": runtime.run_id}
    primary: BaseException | None = None
    cleanup_failures: list[BaseException] = []
    try:
        runtime.start_service("db-gateway")
        runtime.start_service("identity")
        bank_child = _start_bank_child(
            config_path=bank_config,
            reservation=bank_reservation,
            gate_root=gate_root,
            log_path=bank_log,
        )
        await _wait_for_service(bank_child, bank_url, bank_log)
        platform, agent = await _make_funded_agent(
            runtime,
            bank_url=bank_url,
            task_board_url=board_url,
        )
        assert agent.agent_id is not None
        first_board = _start_board_child(
            config_path=board_config,
            reservation=board_reservation,
            ready_path=first_board_ready,
            log_path=first_board_log,
        )
        first_board_identity = await asyncio.to_thread(
            _wait_for_gate,
            first_board,
            first_board_ready,
            timeout_seconds=_GATE_TIMEOUT_SECONDS,
        )
        first_board.bind_actor(int(first_board_identity["pid"]))
        await _wait_for_health(first_board, board_url, first_board_log)

        intent = agent.prepare_task_posting(
            title=_TITLE,
            spec=_SPEC,
            reward=_REWARD,
            bidding_deadline_seconds=_BIDDING_SECONDS,
            acceptance_window_seconds=_ACCEPTANCE_SECONDS,
            execution_deadline_seconds=_EXECUTION_SECONDS,
            review_deadline_seconds=_REVIEW_SECONDS,
        )
        first_task_token, first_escrow_token = _posting_tokens(agent, intent)
        first_header = parse_jws(first_escrow_token).header
        original_request = asyncio.create_task(
            _post_original(board_url, first_task_token, first_escrow_token)
        )
        entered_first = await asyncio.to_thread(
            _wait_for_gate,
            bank_child,
            gate_root / "entered-1.json",
            timeout_seconds=_GATE_TIMEOUT_SECONDS,
        )
        assert entered_first["operation_id"] == intent.lock_operation_id
        assert entered_first["task_id"] == intent.task_id
        assert entered_first["payer_account_id"] == intent.actor_id
        assert entered_first["amount"] == intent.reward
        assert entered_first["kid"] == intent.actor_id
        assert entered_first["iat"] == first_header.iat
        assert entered_first["exp"] == first_header.exp
        bank_child.bind_actor(int(entered_first["pid"]))

        first_board_pid = int(first_board_identity["pid"])
        await asyncio.to_thread(_kill_and_reap, first_board, first_board_pid)
        original_result = await asyncio.gather(original_request, return_exceptions=True)
        assert len(original_result) == 1 and isinstance(original_result[0], httpx.HTTPError)
        assert _alive(bank_child.actor_pid), "held original Bank request died with Task Board"
        assert not (gate_root / "completed-1.json").exists()
        assert _alive(sentinel.actor_pid)

        expiry_proof = await _prove_expired(bank_url, first_escrow_token, first_header.exp)
        assert not (gate_root / "entered-2.json").exists()

        retry_reservation = _ReservedPort(board_reservation.port)
        retry_board = _start_board_child(
            config_path=board_config,
            reservation=retry_reservation,
            ready_path=retry_board_ready,
            log_path=retry_board_log,
        )
        retry_board_identity = await asyncio.to_thread(
            _wait_for_gate,
            retry_board,
            retry_board_ready,
            timeout_seconds=_GATE_TIMEOUT_SECONDS,
        )
        retry_board.bind_actor(int(retry_board_identity["pid"]))
        await _wait_for_health(retry_board, board_url, retry_board_log)
        retry_request = asyncio.create_task(agent.submit_task_posting(intent))
        entered_retry = await asyncio.to_thread(
            _wait_for_gate,
            bank_child,
            gate_root / "entered-2.json",
            timeout_seconds=_GATE_TIMEOUT_SECONDS,
        )
        assert entered_retry["operation_id"] == entered_first["operation_id"]
        assert entered_retry["task_id"] == entered_first["task_id"]
        assert entered_retry["payer_account_id"] == entered_first["payer_account_id"]
        assert entered_retry["amount"] == entered_first["amount"]
        assert entered_retry["token_sha256"] != entered_first["token_sha256"]
        assert entered_retry["iat"] >= entered_first["exp"]
        assert entered_retry["exp"] > entered_first["exp"]

        first_to_release, second_to_release = (2, 1) if winner == "retry" else (1, 2)
        _touch_release(gate_root / f"release-{first_to_release}")
        completed_winner = await asyncio.to_thread(
            _wait_for_gate,
            bank_child,
            gate_root / f"completed-{first_to_release}.json",
            timeout_seconds=_GATE_TIMEOUT_SECONDS,
        )
        _touch_release(gate_root / f"release-{second_to_release}")
        completed_other = await asyncio.to_thread(
            _wait_for_gate,
            bank_child,
            gate_root / f"completed-{second_to_release}.json",
            timeout_seconds=_GATE_TIMEOUT_SECONDS,
        )
        response = await asyncio.wait_for(retry_request, timeout=10.0)
        assert response.status_code == 201
        assert response.body["publication_state"] == "published"
        assert response.body["operation_id"] == intent.operation_id
        assert response.body["task_id"] == intent.task_id
        assert response.body["lock_operation_id"] == intent.lock_operation_id
        assert response.body["request_sha256"] == intent.request_sha256
        assert response.body["escrow_id"] == completed_winner["escrow_id"]
        assert completed_other["escrow_id"] == completed_winner["escrow_id"]
        assert completed_other["receipt_event_id"] == completed_winner["receipt_event_id"]
        assert completed_other["leg_transaction_ids"] == completed_winner["leg_transaction_ids"]
        assert completed_other["financial_envelope"] == completed_winner["financial_envelope"]

        tasks = [task for task in await agent.list_tasks() if task["task_id"] == intent.task_id]
        assert len(tasks) == 1
        task = await agent.get_task(intent.task_id)
        assert task["poster_id"] == intent.actor_id
        assert task["escrow_id"] == completed_winner["escrow_id"]
        balance = await agent.get_balance()
        transactions = await agent.get_transactions()
        locks = [
            transaction
            for transaction in transactions
            if transaction["type"] == "escrow_lock" and transaction["reference"] == intent.task_id
        ]
        assert balance["balance"] == _INITIAL_BALANCE - _REWARD
        assert len(locks) == 1

        projection = _read_projection(runtime, intent)
        assert len(projection["publications"]) == 1
        assert len(projection["tasks"]) == 1
        assert len(projection["receipts"]) == 1
        assert len(projection["legs"]) == 1
        assert len(projection["escrows"]) == 1
        assert len(projection["debits"]) == 1
        assert len(projection["lock_events"]) == 1
        assert len(projection["creation_events"]) == 1
        assert len(projection["all_publications"]) == 1
        assert len(projection["all_tasks"]) == 1
        assert len(projection["all_escrows"]) == 1
        assert len(projection["all_lock_receipts"]) == 1
        assert len(projection["all_lock_legs"]) == 1
        assert len(projection["all_lock_debits"]) == 1
        assert len(projection["all_lock_events"]) == 1
        assert len(projection["all_creation_events"]) == 1
        assert projection["settlement_receipts"] == []
        assert projection["settlement_legs"] == []
        assert projection["settlement_transactions"] == []
        assert projection["settlement_events"] == []
        publication = projection["publications"][0]
        receipt = projection["receipts"][0]
        leg = projection["legs"][0]
        escrow = projection["escrows"][0]
        debit = projection["debits"][0]
        assert publication["operation_id"] == intent.operation_id
        assert publication["task_id"] == intent.task_id
        assert publication["lock_operation_id"] == intent.lock_operation_id
        assert publication["request_sha256"] == intent.request_sha256
        assert publication["state"] == "published"
        assert publication["actor_id"] == intent.actor_id
        assert publication["caller_kid"] == "gateway-task-board"
        assert publication["escrow_id"] == task["escrow_id"]
        assert publication["lock_receipt_event_id"] == receipt["event_id"]
        assert receipt["operation_id"] == intent.lock_operation_id
        assert receipt["receipt_version"] == 1
        assert receipt["operation_kind"] == "escrow_lock"
        assert receipt["source_reference"] == intent.task_id
        assert json.loads(receipt["canonical_request_json"]) == {
            "amount": intent.reward,
            "operation_kind": "escrow_lock",
            "payer_account_id": intent.actor_id,
            "request_version": 1,
            "task_id": intent.task_id,
        }
        assert json.loads(receipt["result_json"]) == {"escrow_status_at_commit": "locked"}
        assert receipt["escrow_id"] == escrow["escrow_id"] == task["escrow_id"]
        assert receipt["event_id"] == projection["lock_events"][0]["event_id"]
        assert publication["lock_request_sha256"] == receipt["request_sha256"]
        assert leg["operation_id"] == intent.lock_operation_id
        assert leg["leg_index"] == 0
        assert leg["role"] == "payer"
        assert leg["account_id"] == intent.actor_id
        assert leg["direction"] == "debit"
        assert leg["amount"] == intent.reward
        assert leg["balance_after"] == _INITIAL_BALANCE - _REWARD
        assert leg["transaction_id"] == debit["tx_id"] == locks[0]["tx_id"]
        assert escrow["payer_account_id"] == intent.actor_id
        assert escrow["task_id"] == intent.task_id
        assert escrow["amount"] == intent.reward
        assert escrow["status"] == "locked"
        assert escrow["resolved_at"] is None
        assert debit["account_id"] == intent.actor_id
        assert debit["type"] == "escrow_lock"
        assert debit["amount"] == intent.reward
        assert debit["balance_after"] == _INITIAL_BALANCE - _REWARD
        assert debit["reference"] == intent.task_id
        lock_event = projection["lock_events"][0]
        assert lock_event["event_source"] == "central-bank"
        assert lock_event["event_type"] == "escrow.locked"
        assert lock_event["task_id"] == intent.task_id
        assert lock_event["agent_id"] == intent.actor_id
        creation_event = projection["creation_events"][0]
        assert creation_event["event_source"] == "task-board"
        assert creation_event["event_type"] == "task.created"
        assert creation_event["task_id"] == intent.task_id
        assert creation_event["agent_id"] == intent.actor_id
        assert projection["creation_events"][0]["event_id"] == response.body["result"]["event_id"]
        assert task["title"] == intent.title
        assert task["spec"] == intent.spec
        assert task["reward"] == intent.reward
        assert task["status"] == "open"
        assert task["bidding_deadline_seconds"] == intent.bidding_deadline_seconds
        assert task["deadline_seconds"] == intent.execution_deadline_seconds
        assert task["review_deadline_seconds"] == intent.review_deadline_seconds
        total_issuance = sum(int(item["amount"]) for item in projection["issuance_legs"])
        total_balances = sum(int(item["balance"]) for item in projection["accounts"])
        total_locked = sum(
            int(item["amount"]) for item in projection["all_escrows"] if item["status"] == "locked"
        )
        assert total_issuance == _INITIAL_BALANCE
        assert total_issuance == total_balances + total_locked
        assert _alive(sentinel.actor_pid)
        evidence.update(
            {
                "entered_original": entered_first,
                "expired_original": expiry_proof,
                "entered_retry": entered_retry,
                "completed_first_release": completed_winner,
                "completed_second_release": completed_other,
                "publication": response.body,
                "projection": projection,
                "conservation": {
                    "total_initial_issuance": total_issuance,
                    "total_account_balances": total_balances,
                    "total_locked_escrow": total_locked,
                },
            }
        )
    except BaseException as exc:
        primary = exc
    finally:
        for task in (original_request, retry_request):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        for child in (first_board, retry_board, bank_child):
            if child is None or child.drained:
                continue
            try:
                _terminate_owned_child(child)
            except BaseException as exc:
                cleanup_failures.append(exc)
        sentinel_survived = _alive(sentinel.actor_pid)
        if not sentinel_survived:
            cleanup_failures.append(AssertionError("unrelated sentinel did not survive cleanup"))
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
        for reservation in (bank_reservation, board_reservation, retry_reservation):
            if reservation is None:
                continue
            with contextlib.suppress(OSError):
                reservation.close()
        try:
            runtime.stop()
        except BaseException as exc:
            cleanup_failures.append(exc)
        evidence["children"] = [
            _child_audit(
                "board-original",
                first_board,
                None if first_board is None else first_board.actor_pid,
                int(signal.SIGKILL),
            ),
            _child_audit(
                "board-retry",
                retry_board,
                None if retry_board is None else retry_board.actor_pid,
                int(signal.SIGKILL),
            ),
            _child_audit(
                "bank",
                bank_child,
                None if bank_child is None else bank_child.actor_pid,
                int(signal.SIGKILL),
            ),
        ]
        evidence["sentinel"] = {
            "launcher_pid": sentinel.process.pid,
            "actor_pid": sentinel.actor_pid,
            "pgid": sentinel.pgid,
            "sid": sentinel.sid,
            "survived_children_cleanup": sentinel_survived,
            "cleanup_signal": int(signal.SIGKILL),
            "returncode": sentinel.process.returncode,
            "group_drained": sentinel.drained,
        }
        try:
            _write_durable_json(audit_path, evidence)
            print(json.dumps({"mp05_late_bank_audit": str(audit_path)}, sort_keys=True))
        except BaseException as exc:
            cleanup_failures.append(exc)

    if primary is not None and not cleanup_failures:
        raise primary
    failures = ([primary] if primary is not None else []) + cleanup_failures
    if failures:
        raise BaseExceptionGroup("MP-05 late Bank request proof failed", failures)
