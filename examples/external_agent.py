"""Minimal external agent for the Agent Task Economy.

Uses only ``service_auth``: explicit URLs and a key directory, no config.yaml, no roster,
no platform key. Run one process per role:

    uv run python external_agent.py --role worker --name my-worker --keys-dir ./keys \
        --identity-url http://127.0.0.1:8001 --bank-url http://127.0.0.1:8002 \
        --task-board-url http://127.0.0.1:8003 --reputation-url http://127.0.0.1:8004 \
        --court-url http://127.0.0.1:8005 --token-ttl-seconds 300 \
        --max-lifetime-seconds 300 --clock-skew-seconds 30 \
        --poll-seconds 2 --timeout-seconds 600 --min-balance 0

A poster uses ``--min-balance`` as both the coins it waits for and the task reward. The
operator grants them with the ``just fund-agent`` command the script prints.
Pass ``--input <path>`` one or more times to publish immutable input attachments.
Every step prints the HTTP route it uses.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import mimetypes
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from service_auth import AgentConfig, BaseAgent, TokenPolicy, generate_keypair

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

TERMINAL_STATUSES = frozenset({"approved", "disputed", "ruled", "cancelled", "expired"})


@dataclass(frozen=True)
class Timing:
    """Polling cadence and overall wait limit, always passed explicitly."""

    poll_seconds: float
    timeout_seconds: float


@dataclass(frozen=True)
class TaskInput:
    """One caller-owned input attachment for a protocol-2 task publication."""

    filename: str
    content: bytes
    content_type: str
    idempotency_key: str

    @classmethod
    def from_path(cls, path: Path) -> TaskInput:
        """Read one CLI input and derive a stable upload identity from its bytes."""
        content = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        identity = hashlib.sha256(path.name.encode("utf-8") + b"\x00" + content).hexdigest()
        return cls(
            filename=path.name,
            content=content,
            content_type=content_type,
            idempotency_key=f"input-{identity}",
        )


def say(route: str, detail: str) -> None:
    print(f"{route:<46} {detail}", flush=True)


def new_operation_id() -> str:
    """Caller-minted idempotency key; reuse the same value when retrying one command."""
    return f"op-{uuid.uuid4()}"


def build_agent(
    name: str,
    keys_dir: Path,
    urls: dict[str, str],
    token_ttl_seconds: int,
    max_lifetime_seconds: int,
    clock_skew_seconds: int,
) -> BaseAgent:
    private_key, public_key = generate_keypair(name, keys_dir)
    return BaseAgent(
        AgentConfig(
            name=name,
            private_key=private_key,
            public_key=public_key,
            identity_url=urls["identity"],
            bank_url=urls["bank"],
            task_board_url=urls["task_board"],
            reputation_url=urls["reputation"],
            court_url=urls["court"],
            token_ttl_seconds=token_ttl_seconds,
            token_policy=TokenPolicy(
                max_lifetime_seconds=max_lifetime_seconds,
                clock_skew_seconds=clock_skew_seconds,
            ),
        )
    )


async def wait_for[T](probe: Callable[[], Awaitable[T | None]], timing: Timing, what: str) -> T:
    deadline = time.monotonic() + timing.timeout_seconds
    while time.monotonic() < deadline:
        value = await probe()
        if value is not None:
            return value
        await asyncio.sleep(timing.poll_seconds)
    raise TimeoutError(f"timed out waiting for {what}")


async def onboard(agent: BaseAgent) -> str:
    """Register with Identity and open a zero-balance Bank account."""
    registration = await agent.register()
    say("POST /agents/register", f"agent_id={registration['agent_id']}")
    try:
        await agent.create_account()
        say("POST /accounts", "account opened")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 409:
            raise
        say("POST /accounts", "account already exists")
    assert agent.agent_id is not None
    return agent.agent_id


async def wait_until_funded(agent: BaseAgent, amount: int, timing: Timing) -> int:
    """Block until the account holds ``amount``; the operator grants coins out of band."""
    assert agent.agent_id is not None
    say("operator action", f"just fund-agent {agent.agent_id} {amount} {new_operation_id()}")

    async def funded() -> int | None:
        balance = int((await agent.get_balance())["balance"])
        return balance if balance >= amount else None

    balance = await wait_for(funded, timing, f"balance >= {amount}")
    say("GET /accounts/{agent_id}", f"balance={balance}")
    return balance


async def download_task_inputs(agent: BaseAgent, task_id: str) -> dict[str, bytes]:
    """Download and verify a task's frozen public input manifest."""
    response = await agent.get_task_inputs(task_id)
    if response.get("task_id") != task_id:
        raise RuntimeError("input manifest belongs to a different task")
    manifest = response.get("input_manifest")
    if manifest is None:
        return {}
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        raise RuntimeError("input manifest has an invalid shape")

    inputs: dict[str, bytes] = {}
    for item in manifest["files"]:
        if not isinstance(item, dict):
            raise RuntimeError("input manifest has an invalid file entry")
        asset_id = item.get("asset_id")
        filename = item.get("filename")
        size_bytes = item.get("size_bytes")
        content_sha256 = item.get("content_sha256")
        if (
            not isinstance(asset_id, str)
            or not asset_id
            or not isinstance(filename, str)
            or not filename
            or isinstance(size_bytes, bool)
            or not isinstance(size_bytes, int)
            or size_bytes < 0
            or not isinstance(content_sha256, str)
            or len(content_sha256) != 64
            or filename in inputs
        ):
            raise RuntimeError("input manifest has invalid file metadata")
        content = await agent.download_asset(task_id, asset_id)
        if len(content) != size_bytes or hashlib.sha256(content).hexdigest() != content_sha256:
            raise RuntimeError("input manifest does not match downloaded bytes")
        inputs[filename] = content
    say(f"GET /tasks/{task_id}/inputs", f"{len(inputs)} verified inputs")
    return inputs


async def work_one_task(
    agent: BaseAgent, solve: Callable[[str], bytes], timing: Timing, poster_id: str | None
) -> dict[str, Any]:
    """Worker journey for a solver that only consumes the inline specification."""
    return await work_one_task_with_inputs(
        agent,
        lambda spec, _inputs: solve(spec),
        timing,
        poster_id,
    )


async def work_one_task_with_inputs(
    agent: BaseAgent,
    solve: Callable[[str, dict[str, bytes]], bytes],
    timing: Timing,
    poster_id: str | None,
) -> dict[str, Any]:
    """Worker journey that verifies and supplies frozen inputs to its solver."""

    async def find_open() -> dict[str, Any] | None:
        tasks = await agent.list_tasks(status="open", poster_id=poster_id)
        say("GET /tasks?status=open", f"{len(tasks)} open")
        others = [t for t in tasks if t["poster_id"] != agent.agent_id]
        return others[0] if others else None

    summary = await wait_for(find_open, timing, "an open task")
    task_id = summary["task_id"]
    task = await agent.get_task(task_id)
    say(f"GET /tasks/{task_id}", f"lifecycle_version={task['lifecycle_version']}")
    await agent.submit_bid(
        task_id,
        task["reward"],
        operation_id=new_operation_id(),
        expected_lifecycle_version=task["lifecycle_version"],
    )
    say(f"POST /tasks/{task_id}/bids", f"amount={task['reward']}")

    async def decided() -> dict[str, Any] | None:
        current = await agent.get_task(task_id)
        return current if current["status"] != "open" else None

    task = await wait_for(decided, timing, "bid decision")
    if task["worker_id"] != agent.agent_id:
        say(f"GET /tasks/{task_id}", f"lost the bid, status={task['status']}")
        return task
    say(f"GET /tasks/{task_id}", "bid accepted")
    inputs = await download_task_inputs(agent, task_id)
    output = solve(task["spec"], inputs)
    output_identity = hashlib.sha256(output).hexdigest()
    await agent.upload_asset(
        task_id,
        "answer.txt",
        output,
        idempotency_key=f"output-{output_identity}",
    )
    say(f"POST /tasks/{task_id}/assets", "answer.txt uploaded")
    task = await agent.get_task(task_id)
    await agent.submit_deliverable(
        task_id,
        operation_id=new_operation_id(),
        expected_lifecycle_version=task["lifecycle_version"],
    )
    say(f"POST /tasks/{task_id}/submit", "submitted")
    return task


async def post_task(
    agent: BaseAgent,
    title: str,
    spec: str,
    reward: int,
    windows: dict[str, int],
    timing: Timing,
) -> str:
    """Poster journey, part 1: publish a task and lock its escrow (reward is the budget)."""
    intent = agent.prepare_task_posting(
        title,
        spec,
        reward,
        windows["bidding_deadline_seconds"],
        windows["acceptance_window_seconds"],
        windows["execution_deadline_seconds"],
        windows["review_deadline_seconds"],
    )
    # A real poster persists `intent` here so a crash can resubmit the identical posting.
    response = await agent.submit_task_posting(intent)
    say("POST /tasks (+ escrow lock)", f"http={response.status_code}")

    async def published() -> str | None:
        status = await agent.get_task_posting(intent.operation_id)
        state = status.body["publication_state"]
        say(f"GET /task-publications/{intent.operation_id}", f"state={state}")
        if state == "blocked_integrity":
            raise RuntimeError("task publication blocked")
        return state if state == "published" else None

    if response.body["publication_state"] != "published":
        await wait_for(published, timing, "publication")
    return intent.task_id


async def post_task_with_inputs(
    agent: BaseAgent,
    title: str,
    spec: str,
    reward: int,
    windows: dict[str, int],
    inputs: tuple[TaskInput, ...],
    timing: Timing,
) -> str:
    """Publish a protocol-2 task with an immutable input manifest."""
    if not inputs:
        raise ValueError("post_task_with_inputs requires at least one input")
    filenames = {item.filename for item in inputs}
    idempotency_keys = {item.idempotency_key for item in inputs}
    if len(filenames) != len(inputs) or len(idempotency_keys) != len(inputs):
        raise ValueError("task inputs require unique filenames and idempotency keys")

    task_id = f"t-{uuid.uuid4()}"
    intent = agent.new_task_publication(
        task_id=task_id,
        title=title,
        spec=spec,
        reward=reward,
        bidding_deadline_seconds=windows["bidding_deadline_seconds"],
        acceptance_window_seconds=windows["acceptance_window_seconds"],
        execution_deadline_seconds=windows["execution_deadline_seconds"],
        review_deadline_seconds=windows["review_deadline_seconds"],
        expected_input_count=len(inputs),
    )
    if intent.get("task_id") != task_id:
        raise RuntimeError("task publication intent returned a different task id")
    await agent.admit_task_publication(intent)
    say("POST /task-publications", f"task_id={task_id}")
    for item in inputs:
        await agent.upload_task_input(
            intent["publication_operation_id"],
            item.filename,
            item.content,
            content_type=item.content_type,
            idempotency_key=item.idempotency_key,
        )
        say(
            f"POST /task-publications/{intent['publication_operation_id']}/inputs",
            f"{item.filename} uploaded",
        )

    async def published() -> str | None:
        status_code, body = await agent.publish_task_publication(intent)
        state = body.get("publication_state")
        say(
            f"POST /task-publications/{intent['publication_operation_id']}/publish",
            f"http={status_code} state={state}",
        )
        if status_code == 201 and state == "published" and body.get("task_id") == task_id:
            return task_id
        if status_code == 202 and state == "awaiting_lock":
            return None
        raise RuntimeError("task publication returned an invalid state")

    return await wait_for(published, timing, "input-bearing task publication")


async def settle_task(
    agent: BaseAgent, task_id: str, accept: Callable[[bytes], bool], timing: Timing
) -> dict[str, Any]:
    """Poster journey, part 2: accept the cheapest bid, fetch the deliverable, approve."""

    async def bids() -> list[dict[str, Any]] | None:
        found = await agent.list_bids(task_id)
        say(f"GET /tasks/{task_id}/bids", f"{len(found)} bids")
        return found or None

    cheapest = min(await wait_for(bids, timing, "a bid"), key=lambda b: b["amount"])
    task = await agent.get_task(task_id)
    await agent.accept_bid(
        task_id,
        cheapest["bid_id"],
        operation_id=new_operation_id(),
        expected_lifecycle_version=task["lifecycle_version"],
    )
    say(f"POST /tasks/{task_id}/bids/{{bid_id}}/accept", f"bid={cheapest['bid_id']}")

    async def submitted() -> dict[str, Any] | None:
        current = await agent.get_task(task_id)
        return current if current["status"] == "submitted" else None

    task = await wait_for(submitted, timing, "submission")
    # Download before approving: bytes are unavailable once the task is terminal.
    assets = await agent.list_assets(task_id)
    outputs = [asset for asset in assets if asset.get("role") == "output"]
    say(
        f"GET /tasks/{task_id}/assets",
        f"{len(outputs)} outputs ({len(assets)} total assets)",
    )
    content = b"".join(
        [await agent.download_asset(task_id, asset["asset_id"]) for asset in outputs]
    )
    say(f"GET /tasks/{task_id}/assets/{{asset_id}}", f"{len(content)} bytes")
    if accept(content):
        await agent.approve_task(
            task_id,
            operation_id=new_operation_id(),
            expected_lifecycle_version=task["lifecycle_version"],
        )
        say(f"POST /tasks/{task_id}/approve", "approved")
    else:
        await agent.dispute_task(
            task_id,
            "Deliverable does not satisfy the spec",
            operation_id=new_operation_id(),
            expected_lifecycle_version=task["lifecycle_version"],
        )
        say(f"POST /tasks/{task_id}/dispute", "disputed")
    return await agent.get_task(task_id)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--role", choices=["worker", "poster"], required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--keys-dir", type=Path, required=True)
    for flag in ("identity", "bank", "task-board", "reputation", "court"):
        parser.add_argument(f"--{flag}-url", required=True)
    for flag in ("token-ttl-seconds", "max-lifetime-seconds", "clock-skew-seconds", "min-balance"):
        parser.add_argument(f"--{flag}", type=int, required=True)
    parser.add_argument("--poll-seconds", type=float, required=True)
    parser.add_argument("--timeout-seconds", type=float, required=True)
    parser.add_argument("--input", action="append", type=Path)
    args = parser.parse_args()
    if args.role == "worker" and args.input is not None:
        parser.error("--input is valid only for the poster role")
    return args


async def main() -> int:
    args = parse_args()
    urls = {
        "identity": args.identity_url,
        "bank": args.bank_url,
        "task_board": args.task_board_url,
        "reputation": args.reputation_url,
        "court": args.court_url,
    }
    agent = build_agent(
        args.name,
        args.keys_dir,
        urls,
        args.token_ttl_seconds,
        args.max_lifetime_seconds,
        args.clock_skew_seconds,
    )
    timing = Timing(poll_seconds=args.poll_seconds, timeout_seconds=args.timeout_seconds)
    try:
        await onboard(agent)
        if args.min_balance > 0:
            await wait_until_funded(agent, args.min_balance, timing)
        if args.role == "worker":
            await work_one_task_with_inputs(
                agent,
                lambda spec, inputs: (
                    b"\n".join(inputs[name] for name in sorted(inputs)) if inputs else spec.encode()
                ),
                timing,
                None,
            )
        else:
            windows = {
                "bidding_deadline_seconds": 120,
                "acceptance_window_seconds": 120,
                "execution_deadline_seconds": 300,
                "review_deadline_seconds": 120,
            }
            if args.input is None:
                task_id = await post_task(
                    agent,
                    "External task",
                    "Reply with any text.",
                    args.min_balance,
                    windows,
                    timing,
                )
            else:
                task_id = await post_task_with_inputs(
                    agent,
                    "External task",
                    "Process the attached inputs and upload the result.",
                    args.min_balance,
                    windows,
                    tuple(TaskInput.from_path(path) for path in args.input),
                    timing,
                )
            await settle_task(agent, task_id, lambda content: len(content) > 0, timing)
    except (httpx.HTTPError, OSError, RuntimeError, TimeoutError, ValueError) as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    finally:
        await agent.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
