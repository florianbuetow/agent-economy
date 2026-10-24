"""MP-07 process-crash replay proofs for all shipped runtime task commands."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml
from service_auth import generate_keypair

from base_agent.agent import BaseAgent
from base_agent.config import load_agent_config
from base_agent.runtime_config import load_runtime_state_config
from base_agent.runtime_intent_models import canonical_json_bytes, canonical_sha256
from base_agent.runtime_intents import FileTaskIntentStore
from base_agent.runtime_task_commands import runtime_owner_from_agent
from base_agent.runtime_task_intent_factory import (
    acceptance_intent,
    bid_intent,
    review_intent,
    submission_intent,
)

from .native_task_contract import publish_task

if TYPE_CHECKING:
    from collections.abc import Mapping

    from base_agent.platform import PlatformAgent
    from base_agent.runtime_intent_models import NewTaskIntent

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

_ACTIONS = ("submit_bid", "accept_bid", "submit_deliverable", "approve_task", "dispute_task")
_STAGES = ("before_send", "after_response")
_REWARD = 100
_BID_AMOUNT = 90
_PROCESS_TIMEOUT_SECONDS = 30.0
_GATE_TIMEOUT_SECONDS = 30.0
_IMMUTABLE_FIELDS = (
    "owner_sha256",
    "semantic_sha256",
    "task_id",
    "slot",
    "action",
    "source_status",
    "target_status",
    "operation_id",
    "expected_lifecycle_version",
    "public_arguments",
    "acknowledgment_context",
)


@dataclass(frozen=True)
class _Actors:
    config_path: Path
    journal_root: Path
    poster: BaseAgent
    worker: BaseAgent


@dataclass
class _OwnedChild:
    process: subprocess.Popen[bytes]
    pgid: int
    sid: int
    actor_pid: int | None = None
    drained: bool = False

    @classmethod
    def capture(cls, process: subprocess.Popen[bytes]) -> _OwnedChild:
        pgid = os.getpgid(process.pid)
        sid = os.getsid(process.pid)
        if pgid != process.pid or sid != process.pid:
            raise RuntimeError("Child launcher does not own a fresh process group and session")
        return cls(process=process, pgid=pgid, sid=sid)

    def bind_actor(self, actor_pid: int) -> None:
        if actor_pid <= 0:
            raise RuntimeError("Child actor PID must be positive")
        try:
            actual_group = os.getpgid(actor_pid)
            actual_session = os.getsid(actor_pid)
        except ProcessLookupError as exc:
            raise RuntimeError("Child actor exited before ownership verification") from exc
        if actual_group != self.pgid or actual_session != self.sid:
            raise RuntimeError("Child actor is outside the owned process group")
        self.actor_pid = actor_pid

    def actor_is_owned(self) -> bool:
        if self.actor_pid is None or self.drained:
            return False
        try:
            return os.getpgid(self.actor_pid) == self.pgid and os.getsid(self.actor_pid) == self.sid
        except ProcessLookupError:
            return False

    def mark_drained(self) -> None:
        if self.process.poll() is None or _group_exists(self):
            raise RuntimeError("Owned child cannot be marked drained while its group is live")
        self.drained = True


def _atomic_json(path: Path, value: object) -> None:
    staged = path.with_name(f".{path.name}.tmp")
    staged.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    staged.replace(path)


async def _make_actors(tmp_path: Path, platform_agent: PlatformAgent) -> _Actors:
    keys = tmp_path / "keys"
    keys.mkdir(mode=0o700)
    handles = ("crash_poster", "crash_worker")
    for handle in handles:
        generate_keypair(handle, keys)
    roster = tmp_path / "roster.yaml"
    roster.write_text(
        yaml.safe_dump(
            {
                "agents": {
                    "crash_poster": {"name": f"MP07 crash poster {uuid.uuid4()}"},
                    "crash_worker": {"name": f"MP07 crash worker {uuid.uuid4()}"},
                }
            }
        ),
        encoding="utf-8",
    )
    journal_root = tmp_path / "runtime-state"
    journal_root.mkdir(mode=0o700)
    config = {
        "platform": {
            "identity_url": os.environ["IDENTITY_URL"],
            "bank_url": os.environ["BANK_URL"],
            "task_board_url": os.environ["TASK_BOARD_URL"],
            "reputation_url": os.environ["REPUTATION_URL"],
            "court_url": os.environ["COURT_URL"],
        },
        "data": {"keys_dir": str(keys), "roster_path": str(roster)},
        "runtime_state": {
            "environment_id": os.environ["ISOLATED_RUNTIME_ID"],
            "directory": str(journal_root),
            "diagnostic_limit": 8,
            "max_record_bytes": 65_536,
        },
        "signing": {
            "token_ttl_seconds": 300,
            "max_lifetime_seconds": 300,
            "clock_skew_seconds": 30,
        },
    }
    config_path = tmp_path / "agents.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    poster = BaseAgent(load_agent_config("crash_poster", config_path))
    worker = BaseAgent(load_agent_config("crash_worker", config_path))
    try:
        await poster.register()
        await worker.register()
        assert poster.agent_id is not None
        assert worker.agent_id is not None
        await platform_agent.create_account(
            agent_id=poster.agent_id,
            initial_balance=1_000,
            operation_id=f"op-{uuid.uuid4()}",
        )
        await platform_agent.create_account(
            agent_id=worker.agent_id,
            initial_balance=0,
            operation_id=None,
        )
    except BaseException:
        await poster.close()
        await worker.close()
        raise
    return _Actors(config_path, journal_root, poster, worker)


async def _prepare_task(
    actors: _Actors, action: str
) -> tuple[dict[str, Any], Mapping[str, object] | None]:
    posted = await publish_task(
        actors.poster,
        title="MP-07 process crash proof",
        spec="Prove exact replay of one durable native mutation after process death.",
        reward=_REWARD,
        bidding_deadline_seconds=86_400,
        execution_deadline_seconds=86_400,
        review_deadline_seconds=86_400,
        acceptance_window_seconds=86_400,
    )
    task_id = str(posted["task_id"])
    task = await actors.poster.get_task(task_id)
    if action == "submit_bid":
        return task, None

    bid_response = await actors.worker.submit_bid(
        task_id,
        _BID_AMOUNT,
        operation_id=f"op-{uuid.uuid4()}",
        expected_lifecycle_version=0,
    )
    bid = bid_response.body
    if action == "accept_bid":
        return task, bid

    accepted = await actors.poster.accept_bid(
        task_id,
        str(bid["bid_id"]),
        operation_id=f"op-{uuid.uuid4()}",
        expected_lifecycle_version=0,
    )
    assert accepted.status_code == 200
    task = await actors.poster.get_task(task_id)
    await actors.worker.upload_asset(task_id, "proof.txt", b"durable process crash proof\n")
    if action == "submit_deliverable":
        return task, bid

    submitted = await actors.worker.submit_deliverable(
        task_id,
        operation_id=f"op-{uuid.uuid4()}",
        expected_lifecycle_version=1,
    )
    assert submitted.status_code == 200
    return await actors.poster.get_task(task_id), bid


def _intent_for(
    actors: _Actors,
    action: str,
    task: Mapping[str, object],
    bid: Mapping[str, object] | None,
) -> tuple[NewTaskIntent, BaseAgent, str, str]:
    operation_id = f"op-{uuid.uuid4()}"
    if action == "submit_bid":
        assert actors.worker.agent_id is not None
        return (
            bid_intent(
                task=task,
                actor_id=actors.worker.agent_id,
                amount=_BID_AMOUNT,
                operation_id=operation_id,
            ),
            actors.worker,
            "math-worker",
            "crash_worker",
        )
    if action == "accept_bid":
        assert bid is not None and actors.poster.agent_id is not None
        return (
            acceptance_intent(
                task=task,
                actor_id=actors.poster.agent_id,
                bid=bid,
                operation_id=operation_id,
            ),
            actors.poster,
            "task-feeder",
            "crash_poster",
        )
    if action == "submit_deliverable":
        assert actors.worker.agent_id is not None
        return (
            submission_intent(
                task=task,
                actor_id=actors.worker.agent_id,
                operation_id=operation_id,
            ),
            actors.worker,
            "math-worker",
            "crash_worker",
        )
    assert actors.poster.agent_id is not None
    reason = (
        "Delivered result violates the frozen specification" if action == "dispute_task" else None
    )
    return (
        review_intent(
            task=task,
            actor_id=actors.poster.agent_id,
            action=action,
            reason=reason,
            operation_id=operation_id,
        ),
        actors.poster,
        "task-feeder",
        "crash_poster",
    )


def _child_command(
    mode: str,
    spec_path: Path,
    output_path: Path,
    *,
    ready_path: Path,
) -> list[str]:
    command = [
        "uv",
        "run",
        "--no-sync",
        "python",
        str(Path(__file__).with_name("runtime_crash_actor.py")),
        "--mode",
        mode,
        "--spec",
        str(spec_path),
        "--output",
        str(output_path),
    ]
    command.extend(("--ready", str(ready_path)))
    return command


def _wait_for_gate(
    child: _OwnedChild, gate_path: Path, *, timeout_seconds: float
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if gate_path.exists():
            value = json.loads(gate_path.read_text(encoding="utf-8"))
            assert isinstance(value, dict)
            return value
        return_code = child.process.poll()
        if return_code is not None:
            raise AssertionError(f"crash child exited early with status {return_code}")
        time.sleep(0.05)
    raise TimeoutError("child did not reach its durable gate")


def _group_exists(child: _OwnedChild) -> bool:
    try:
        os.killpg(child.pgid, 0)
    except ProcessLookupError:
        return False
    return True


def _kill_and_reap(child: _OwnedChild, actor_pid: int) -> None:
    child.bind_actor(actor_pid)
    os.kill(actor_pid, signal.SIGKILL)
    assert child.process.wait(timeout=_PROCESS_TIMEOUT_SECONDS) != 0
    with pytest.raises(ProcessLookupError):
        os.kill(actor_pid, 0)
    assert not _group_exists(child)
    child.mark_drained()


def _terminate_owned_child(child: _OwnedChild) -> None:
    if child.drained:
        return
    actor_pid = child.actor_pid
    if child.actor_is_owned():
        assert actor_pid is not None
        os.kill(actor_pid, signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            child.process.wait(timeout=2.0)
    if _group_exists(child):
        os.killpg(child.pgid, signal.SIGKILL)
    if child.process.poll() is None:
        child.process.wait(timeout=_PROCESS_TIMEOUT_SECONDS)
    deadline = time.monotonic() + _PROCESS_TIMEOUT_SECONDS
    while _group_exists(child) and time.monotonic() < deadline:
        time.sleep(0.01)
    if _group_exists(child):
        raise RuntimeError(f"Owned process group {child.pgid} did not drain")
    child.mark_drained()


async def _cleanup_history(children: tuple[_OwnedChild | None, ...], agents: list[Any]) -> None:
    failures: list[BaseException] = []
    for child in children:
        if child is None:
            continue
        try:
            _terminate_owned_child(child)
        except BaseException as exc:
            failures.append(exc)
    for agent in agents:
        try:
            await agent.close()
        except BaseException as exc:
            failures.append(exc)
    if failures:
        raise BaseExceptionGroup("MP-07 crash harness cleanup failed", failures)


async def _projection(actors: _Actors, task_id: str) -> dict[str, object]:
    return {
        "task": await actors.poster.get_task(task_id),
        "bids": await actors.poster.list_bids(task_id),
        "poster_balance": (await actors.poster.get_balance())["balance"],
        "worker_balance": (await actors.worker.get_balance())["balance"],
    }


def _assert_final_projection(
    *,
    action: str,
    intent: NewTaskIntent,
    projection: Mapping[str, object],
    balances_before: tuple[int, int],
) -> None:
    task = projection["task"]
    bids = projection["bids"]
    assert isinstance(task, dict)
    assert isinstance(bids, list)
    if action == "dispute_task":
        assert task["status"] in {"disputed", "ruled"}
        assert task["lifecycle_version"] >= intent.expected_lifecycle_version + 1
    else:
        assert task["status"] == intent.target_status
        assert task["lifecycle_version"] == (
            intent.expected_lifecycle_version
            if action == "submit_bid"
            else intent.expected_lifecycle_version + 1
        )
    assert len(bids) == 1
    assert bids[0]["amount"] == _BID_AMOUNT
    assert task["bid_count"] == 1
    if action != "submit_bid":
        assert task["worker_id"] == bids[0]["bidder_id"]
    poster_before, worker_before = balances_before
    poster_after = projection["poster_balance"]
    worker_after = projection["worker_balance"]
    if action == "dispute_task" and task["status"] == "ruled":
        assert poster_after >= poster_before
        assert worker_after >= worker_before
        assert poster_after - poster_before + worker_after - worker_before == _REWARD
    else:
        assert poster_after == poster_before
        expected_worker = worker_before + (_REWARD if action == "approve_task" else 0)
        assert worker_after == expected_worker


@pytest.mark.parametrize("action", _ACTIONS)
@pytest.mark.parametrize("crash_stage", _STAGES)
async def test_runtime_replays_exact_durable_command_after_process_sigkill(  # noqa: PLR0915
    action: str,
    crash_stage: str,
    tmp_path: Path,
    platform_agent: PlatformAgent,
) -> None:
    actors = await _make_actors(tmp_path, platform_agent)
    crash_child: _OwnedChild | None = None
    replay_child: _OwnedChild | None = None
    crash_log = tmp_path / "crash-child.log"
    replay_log = tmp_path / "replay-child.log"
    try:
        task, bid = await _prepare_task(actors, action)
        intent, actor, role, handle = _intent_for(actors, action, task, bid)
        assert actor.agent_id is not None
        balances_before = (
            int((await actors.poster.get_balance())["balance"]),
            int((await actors.worker.get_balance())["balance"]),
        )
        spec = {
            "agent_config_path": str(actors.config_path),
            "registered_agent_id": actor.agent_id,
            "role": role,
            "profile": handle,
            "handle": handle,
            "crash_stage": crash_stage,
            "intent": intent.semantic_json(),
        }
        spec_path = tmp_path / "child-spec.json"
        crash_ready_path = tmp_path / "crash-ready.json"
        gate_path = tmp_path / "crash-gate.json"
        replay_ready_path = tmp_path / "replay-ready.json"
        result_path = tmp_path / "replay-result.json"
        _atomic_json(spec_path, spec)

        with crash_log.open("wb") as log:
            crash_process = subprocess.Popen(
                _child_command("crash", spec_path, gate_path, ready_path=crash_ready_path),
                cwd=Path(__file__).resolve().parents[2],
                env=os.environ.copy(),
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            crash_child = _OwnedChild.capture(crash_process)
            ready = _wait_for_gate(
                crash_child,
                crash_ready_path,
                timeout_seconds=_GATE_TIMEOUT_SECONDS,
            )
            crash_actor_pid = int(ready["pid"])
            assert ready == {"pid": crash_actor_pid, "mode": "crash_starting"}
            crash_child.bind_actor(crash_actor_pid)
            gate = _wait_for_gate(crash_child, gate_path, timeout_seconds=_GATE_TIMEOUT_SECONDS)
            assert gate == {
                "pid": crash_actor_pid,
                "action": action,
                "crash_stage": crash_stage,
                "operation_id": intent.operation_id,
                **({"response": gate["response"]} if crash_stage == "after_response" else {}),
            }
            if crash_stage == "after_response":
                response = gate["response"]
                assert response["status_code"] in {200, 201}
            _kill_and_reap(crash_child, crash_actor_pid)

        runtime_config = load_runtime_state_config(actors.config_path)
        owner = runtime_owner_from_agent(
            runtime_config=runtime_config,
            agent=actor,
            role=role,
            profile=handle,
            handle=handle,
        )
        locator = FileTaskIntentStore(
            root_directory=runtime_config.directory,
            owner=owner,
            diagnostic_limit=runtime_config.diagnostic_limit,
            max_record_bytes=runtime_config.max_record_bytes,
        )
        intent_path = locator.intent_path(intent.task_id, intent.slot)
        pending_bytes = intent_path.read_bytes()
        pending_sha256 = hashlib.sha256(pending_bytes).hexdigest()
        pending = json.loads(pending_bytes)
        assert len(pending_sha256) == 64
        assert pending_bytes == canonical_json_bytes(pending)
        assert pending["revision"] == 1
        assert pending["state"] == "pending"
        assert pending["result"] is None
        assert pending["diagnostics"] == []
        semantic = intent.semantic_json()
        for field, value in semantic.items():
            assert pending[field] == value
        assert pending["semantic_sha256"] == canonical_sha256(semantic)
        assert pending["owner_sha256"] == canonical_sha256(owner.as_json())
        projection_before_replay = await _projection(actors, intent.task_id)

        with replay_log.open("wb") as log:
            replay_process = subprocess.Popen(
                _child_command(
                    "replay",
                    spec_path,
                    result_path,
                    ready_path=replay_ready_path,
                ),
                cwd=Path(__file__).resolve().parents[2],
                env=os.environ.copy(),
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            replay_child = _OwnedChild.capture(replay_process)
            replay_ready = _wait_for_gate(
                replay_child,
                replay_ready_path,
                timeout_seconds=_GATE_TIMEOUT_SECONDS,
            )
            replay_actor_pid = int(replay_ready["pid"])
            assert replay_ready == {"pid": replay_actor_pid, "mode": "replay_starting"}
            replay_child.bind_actor(replay_actor_pid)
            assert replay_process.wait(timeout=_PROCESS_TIMEOUT_SECONDS) == 0, (
                replay_log.read_text()
            )
            replay_child.mark_drained()
        replay_output = json.loads(result_path.read_text(encoding="utf-8"))
        assert type(replay_output["pid"]) is int and replay_output["pid"] > 0
        assert replay_output["mode"] == "replay_unfinished_only"
        assert len(replay_output["results"]) == 1
        completed = replay_output["results"][0]
        assert completed["state"] == "completed"
        assert completed["revision"] == 2
        assert completed["result"] is not None
        assert completed["diagnostics"] == pending["diagnostics"] == []
        for field in _IMMUTABLE_FIELDS:
            assert completed[field] == pending[field]
        assert {field for field in completed if completed[field] != pending[field]} == {
            "revision",
            "state",
            "result",
        }
        final_bytes = intent_path.read_bytes()
        assert hashlib.sha256(final_bytes).hexdigest() != pending_sha256
        final_projection = await _projection(actors, intent.task_id)
        if crash_stage == "after_response" and action != "dispute_task":
            assert final_projection == projection_before_replay
        _assert_final_projection(
            action=action,
            intent=intent,
            projection=final_projection,
            balances_before=balances_before,
        )
        if action != "submit_bid":
            operation = completed["result"]["operation"]
            assert operation["operation_id"] == intent.operation_id
            assert operation["operation_state"] == "completed"
            assert operation["source_lifecycle_version"] == intent.expected_lifecycle_version
            assert operation["command"] == action
            if action == "approve_task":
                assert operation["effect_kind"] == "bank_release"
                assert operation["effect_state"] == "succeeded"
                assert operation["effect_result_reference"] == intent.operation_id
            if action == "dispute_task":
                assert operation["effect_kind"] == "court_claim"
                assert operation["effect_state"] == "succeeded"
                assert operation["effect_result_reference"]
    finally:
        await _cleanup_history((crash_child, replay_child), [actors.poster, actors.worker])
