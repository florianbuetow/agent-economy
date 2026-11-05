"""Two processes sharing one private key remain one cryptographic actor."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import sys
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from service_auth.factory import AgentFactory

if TYPE_CHECKING:
    from asyncio.subprocess import Process

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

ACTOR = Path(__file__).with_name("shared_key_bid_actor.py")


async def _wait_ready(paths: tuple[Path, ...]) -> None:
    for _attempt in range(1_000):
        if all(path.exists() for path in paths):
            return
        await asyncio.sleep(0.01)
    raise TimeoutError("shared-key actors did not reach the barrier")


async def _result(process: Process) -> dict[str, Any]:
    stdout, stderr = await process.communicate()
    assert process.returncode == 0, stderr.decode()
    lines = [line for line in stdout.decode().splitlines() if line.startswith("{")]
    assert len(lines) == 1, stdout.decode()
    return json.loads(lines[0])


async def _actor(
    *,
    config: Path,
    task_id: str,
    operation_id: str,
    amount: int,
    ready: Path,
    release: Path,
) -> Process:
    return await asyncio.create_subprocess_exec(
        sys.executable,
        str(ACTOR),
        "--config",
        str(config),
        "--task-id",
        task_id,
        "--operation-id",
        operation_id,
        "--amount",
        str(amount),
        "--ready",
        str(ready),
        "--release",
        str(release),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )


async def _cleanup(processes: list[Process]) -> None:
    for process in processes:
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        await process.communicate()


async def test_two_processes_with_one_key_reconcile_as_one_worker(
    agent_config,
    tmp_path: Path,
) -> None:
    """IDN-02: process identity never creates a second economic identity."""
    del agent_config  # fixture establishes the isolated agent environment
    config_path = Path(os.environ["AGENT_CONFIG_PATH"])
    factory = AgentFactory(config_path=config_path)
    poster = factory.create_agent("operator")
    shared_worker = factory.create_agent("alice")
    children: list[Process] = []
    try:
        await poster.register()
        worker_id = (await shared_worker.register())["agent_id"]
        intent = poster.prepare_task_posting(
            title="Shared key process identity",
            spec="Accept one immutable bid from one cryptographic worker.",
            reward=100,
            bidding_deadline_seconds=3_600,
            acceptance_window_seconds=3_600,
            execution_deadline_seconds=7_200,
            review_deadline_seconds=3_600,
        )
        published = await poster.submit_task_posting(intent)
        assert published.status_code == 201
        task_id = intent.task_id
        operation_id = f"op-{uuid.uuid4()}"
        release = tmp_path / "release"
        ready = (tmp_path / "first.ready", tmp_path / "second.ready")
        processes = tuple(
            [
                await _actor(
                    config=config_path,
                    task_id=task_id,
                    operation_id=operation_id,
                    amount=60,
                    ready=path,
                    release=release,
                )
                for path in ready
            ]
        )
        children.extend(processes)
        await _wait_ready(ready)
        assert {path.read_text(encoding="utf-8") for path in ready} == {worker_id}
        release.write_text("go", encoding="utf-8")
        first, second = await asyncio.gather(*(_result(process) for process in processes))
        assert first == second
        assert first["status"] == 201
        bids = await poster.list_bids(task_id)
        assert len(bids) == 1
        assert bids[0]["bidder_id"] == worker_id
        assert bids[0]["amount"] == 60

        changed_ready = tmp_path / "changed.ready"
        changed_release = tmp_path / "changed.release"
        changed = await _actor(
            config=config_path,
            task_id=task_id,
            operation_id=operation_id,
            amount=61,
            ready=changed_ready,
            release=changed_release,
        )
        children.append(changed)
        await _wait_ready((changed_ready,))
        changed_release.write_text("go", encoding="utf-8")
        conflict = await _result(changed)
        assert conflict["agent_id"] == worker_id
        assert conflict["status"] == 409
        assert conflict["body"]["error"] == "operation_id_conflict"
        assert await poster.list_bids(task_id) == bids
    finally:
        await _cleanup(children)
        await shared_worker.close()
        await poster.close()
