"""One disposable lifecycle combining publication, worker, Court, and UI faults."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import signal
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pytest
import yaml
from incident_chain import _agent_config, _operation_id
from scripts.isolated_test_runtime import IsolatedRuntime

if TYPE_CHECKING:
    from typing import BinaryIO
from service_auth.errors import EconomyApiError
from service_auth.financial_identity import operation_id_for

from base_agent.agent import BaseAgent
from base_agent.factory import AgentFactory


class _LostPostingAck(httpx.AsyncBaseTransport):
    def __init__(self, task_board_url: str) -> None:
        self._inner = httpx.AsyncHTTPTransport()
        self._port = httpx.URL(task_board_url).port
        self.lost = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._inner.handle_async_request(request)
        if (
            not self.lost
            and request.method == "POST"
            and request.url.port == self._port
            and request.url.path == "/tasks"
        ):
            self.lost = True
            await response.aread()
            raise httpx.ReadTimeout("injected lost publication acknowledgment", request=request)
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()


class _StallingJudgeHandler(BaseHTTPRequestHandler):
    entered = threading.Event()
    release = threading.Event()

    def do_POST(self) -> None:
        self.entered.set()
        self.release.wait(timeout=30)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def _judge_server() -> tuple[ThreadingHTTPServer, threading.Thread]:
    _StallingJudgeHandler.entered.clear()
    _StallingJudgeHandler.release.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StallingJudgeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _configure_timeout_panel(config_path: Path, api_base: str) -> None:
    config = yaml.safe_load(config_path.read_text())
    config["judges"]["panel_size"] = 3
    config["judges"]["mock_worker_pct"] = 0
    config["judges"]["judges"] = [
        {"id": "judge-0", "provider": "mock", "model": "campaign-mock"},
        {
            "id": "judge-1",
            "provider": "llm",
            "model": "openai/campaign-timeout",
            "api_base": api_base,
            "api_key_env": "LMSTUDIO_API_KEY",
            "temperature": 0.2,
            "timeout_seconds": 1,
            "prompt_suffix": None,
        },
        {"id": "judge-2", "provider": "mock", "model": "campaign-mock"},
    ]
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))


def _configure_zero_panel(config_path: Path) -> None:
    config = yaml.safe_load(config_path.read_text())
    config["judges"]["panel_size"] = 3
    config["judges"]["mock_worker_pct"] = 0
    config["judges"]["judges"] = [
        {"id": f"judge-{index}", "provider": "mock", "model": "campaign-mock"} for index in range(3)
    ]
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))


def _start_manual_court(
    runtime: IsolatedRuntime, log_path: Path
) -> tuple[subprocess.Popen[bytes], BinaryIO]:
    reservation = runtime._reservations.pop("court", None)
    if reservation is not None:
        reservation.release()
    env = {
        **runtime.environment(),
        "CONFIG_PATH": str(runtime.config_paths["court"]),
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "LMSTUDIO_API_KEY": "campaign-local",
    }
    handle = log_path.open("ab")
    process = subprocess.Popen(
        [
            "uv",
            "run",
            "uvicorn",
            "court_service.app:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(runtime.ports["court"]),
        ],
        cwd=runtime.repo_root / "services/court",
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            handle.close()
            raise RuntimeError(f"Manual Court exited {process.returncode}")
        try:
            if httpx.get(f"{runtime.service_url('court')}/health", timeout=0.5).status_code == 200:
                return process, handle
        except httpx.HTTPError:
            pass
        time.sleep(0.05)
    raise RuntimeError("Manual Court did not become healthy")


def _stop_manual_court(process: subprocess.Popen[bytes], handle: BinaryIO) -> None:
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
    handle.close()


async def _agent(
    runtime: IsolatedRuntime, platform, name: str, balance: int
) -> tuple[BaseAgent, Any]:
    config = _agent_config(runtime, name)
    agent = BaseAgent(config)
    await agent.register()
    assert agent.agent_id is not None
    await platform.create_account(
        agent.agent_id,
        balance,
        operation_id=(operation_id_for("compound-account", (agent.agent_id,)) if balance else None),
    )
    return agent, config


async def _publish_and_accept(
    runtime: IsolatedRuntime,
    poster: BaseAgent,
    worker_a: BaseAgent,
    worker_b: BaseAgent,
) -> tuple[str, int]:
    lost_transport = _LostPostingAck(runtime.service_url("task-board"))
    await poster._http.aclose()
    poster._http = httpx.AsyncClient(transport=lost_transport)
    intent = poster.prepare_task_posting(
        "Compound recovery",
        "Return exact output and survive every injected fault.",
        100,
        60,
        30,
        120,
        90,
    )
    try:
        await poster.submit_task_posting(intent)
    except httpx.ReadTimeout:
        pass
    else:
        raise AssertionError("Publication acknowledgment must be lost")
    posting = await poster.get_task_posting(intent.operation_id)
    assert posting.body["publication_state"] == "published"
    assert (await poster.submit_task_posting(intent)).body == posting.body
    task = await poster.get_task(intent.task_id)
    bids = await asyncio.gather(
        worker_a.submit_bid(
            intent.task_id,
            60,
            operation_id=_operation_id(),
            expected_lifecycle_version=task["lifecycle_version"],
        ),
        worker_b.submit_bid(
            intent.task_id,
            60,
            operation_id=_operation_id(),
            expected_lifecycle_version=task["lifecycle_version"],
        ),
    )
    assert all(response.status_code == 201 for response in bids)
    accepted = await poster.accept_bid(
        intent.task_id,
        str(bids[0].body["bid_id"]),
        operation_id=_operation_id(),
        expected_lifecycle_version=task["lifecycle_version"],
    )
    try:
        await poster.accept_bid(
            intent.task_id,
            str(bids[1].body["bid_id"]),
            operation_id=_operation_id(),
            expected_lifecycle_version=task["lifecycle_version"],
        )
    except EconomyApiError as exc:
        assert exc.error == "task_version_conflict"
    else:
        raise AssertionError("Stale equal-bid acceptance must conflict")
    return intent.task_id, int(accepted.body["lifecycle_version"])


async def _restart_worker_and_submit(
    worker: BaseAgent,
    worker_config: Any,
    task_id: str,
    accepted_version: int,
) -> tuple[BaseAgent, str, str, int]:
    upload_key = f"compound:{uuid.uuid4()}"
    first_asset = await worker.upload_asset(
        task_id, "answer.txt", b"wrong", idempotency_key=upload_key
    )
    worker_id = str(worker.agent_id)
    await worker.close()
    restarted = BaseAgent(worker_config)
    await restarted.register()
    assert restarted.agent_id == worker_id
    replay_asset = await restarted.upload_asset(
        task_id, "answer.txt", b"wrong", idempotency_key=upload_key
    )
    assert replay_asset["asset_id"] == first_asset["asset_id"]
    submitted = await restarted.submit_deliverable(
        task_id,
        operation_id=_operation_id(),
        expected_lifecycle_version=accepted_version,
    )
    return (
        restarted,
        worker_id,
        str(first_asset["asset_id"]),
        int(submitted.body["lifecycle_version"]),
    )


async def _dispute_with_failed_panel(
    runtime: IsolatedRuntime,
    fault_server: ThreadingHTTPServer,
    poster: BaseAgent,
    worker: BaseAgent,
    task_id: str,
    submitted_version: int,
) -> tuple[str, subprocess.Popen[bytes], BinaryIO]:
    runtime.stop_service("court")
    operation_id = _operation_id()
    with contextlib.suppress(EconomyApiError, httpx.ReadTimeout):
        await poster.dispute_task(
            task_id,
            "The output is wrong.",
            operation_id=operation_id,
            expected_lifecycle_version=submitted_version,
        )
    pending = await poster.get_task(task_id)
    assert pending["pending_transition"]["operation_id"] == operation_id
    _configure_timeout_panel(
        runtime.config_paths["court"],
        f"http://127.0.0.1:{fault_server.server_address[1]}/v1",
    )
    process, handle = _start_manual_court(runtime, runtime.run_dir / "logs/court-timeout.log")
    replay = await poster.dispute_task(
        task_id,
        "The output is wrong.",
        operation_id=operation_id,
        expected_lifecycle_version=submitted_version,
    )
    assert replay.status_code == 200
    dispute_id = str((await poster.list_disputes(task_id=task_id))[0]["dispute_id"])
    await worker.submit_worker_rebuttal(
        task_id,
        dispute_id,
        "The complaint is valid; the frozen output is wrong.",
    )
    assert (await poster.get_task(task_id))["status"] == "disputed"
    assert _StallingJudgeHandler.entered.wait(timeout=5)
    court_state = httpx.get(
        f"{runtime.service_url('court')}/disputes/{dispute_id}", timeout=3
    ).json()
    assert (court_state["status"], court_state["votes"]) == ("rebuttal_pending", [])
    return dispute_id, process, handle


async def _restart_court_and_settle(
    runtime: IsolatedRuntime,
    poster: BaseAgent,
    worker: BaseAgent,
    platform: Any,
    task_id: str,
    dispute_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    _configure_zero_panel(runtime.config_paths["court"])
    runtime.start_service("court")
    ruled = await poster.get_task(task_id)
    assert (ruled["status"], ruled["worker_payment"], ruled["poster_refund"]) == (
        "ruled",
        0,
        100,
    )
    with pytest.raises(httpx.HTTPStatusError) as retry:
        await platform.trigger_ruling(dispute_id)
    assert retry.value.response.status_code == 409
    court = httpx.get(f"{runtime.service_url('court')}/disputes/{dispute_id}", timeout=3).json()
    assert len(court["votes"]) == 3
    assert (await poster.get_balance())["balance"] == 1000
    assert (await worker.get_balance())["balance"] == 0
    async with httpx.AsyncClient(base_url=runtime.service_url("ui")) as client:
        ui = (await client.get(f"/api/tasks/{task_id}")).json()
    assert (ui["status"], ui["worker_payment"], ui["poster_refund"]) == ("ruled", 0, 100)
    return ruled, court


async def _run(runtime: IsolatedRuntime, fault_server: ThreadingHTTPServer) -> dict[str, Any]:
    platform = AgentFactory(config_path=runtime.agent_config_path).platform_agent()
    poster: BaseAgent | None = None
    worker_a: BaseAgent | None = None
    worker_b: BaseAgent | None = None
    restarted_worker: BaseAgent | None = None
    manual_court: subprocess.Popen[bytes] | None = None
    manual_log: BinaryIO | None = None
    await platform.register()
    try:
        poster, _ = await _agent(runtime, platform, "Compound Poster", 1000)
        worker_a, worker_config = await _agent(runtime, platform, "Compound Worker A", 0)
        worker_b, _ = await _agent(runtime, platform, "Compound Worker B", 0)
        assert poster.agent_id and worker_a.agent_id and worker_b.agent_id
        task_id, accepted_version = await _publish_and_accept(runtime, poster, worker_a, worker_b)
        restarted_worker, worker_id, asset_id, submitted_version = await _restart_worker_and_submit(
            worker_a,
            worker_config,
            task_id,
            accepted_version,
        )
        worker_a = None
        dispute_id, manual_court, manual_log = await _dispute_with_failed_panel(
            runtime,
            fault_server,
            poster,
            restarted_worker,
            task_id,
            submitted_version,
        )
        _StallingJudgeHandler.release.set()
        _stop_manual_court(manual_court, manual_log)
        manual_court = None
        ruled, court = await _restart_court_and_settle(
            runtime,
            poster,
            restarted_worker,
            platform,
            task_id,
            dispute_id,
        )

        return {
            "task_id": task_id,
            "winner": worker_id,
            "accepted_price": 60,
            "asset_id": asset_id,
            "dispute_id": dispute_id,
            "votes": len(court["votes"]),
            "worker_payment": ruled["worker_payment"],
            "poster_refund": ruled["poster_refund"],
        }
    finally:
        _StallingJudgeHandler.release.set()
        if manual_court is not None and manual_log is not None:
            _stop_manual_court(manual_court, manual_log)
        for agent in (poster, worker_a, worker_b, restarted_worker):
            if agent is not None:
                await agent.close()
        await platform.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts-root", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    runtime = IsolatedRuntime.create(
        repo_root=Path(__file__).resolve().parents[2],
        artifacts_root=args.artifacts_root,
    )
    _configure_zero_panel(runtime.config_paths["court"])
    fault_server, fault_thread = _judge_server()
    try:
        runtime.start()
        assert runtime.provision() == 0
        result = asyncio.run(_run(runtime, fault_server))
        args.result.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        print(json.dumps(result, sort_keys=True))
        return 0
    finally:
        _StallingJudgeHandler.release.set()
        fault_server.shutdown()
        fault_thread.join(timeout=5)
        fault_server.server_close()
        runtime.stop()


if __name__ == "__main__":
    raise SystemExit(main())
