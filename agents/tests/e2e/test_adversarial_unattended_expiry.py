"""Unattended no-bid and accepted-worker expiry outcomes for ECO-06."""

from __future__ import annotations

import asyncio
import json
import re
from contextlib import ExitStack
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from base_agent.runtime_postings import FileTaskPostingStore
from math_worker.config import MathWorkerConfig
from math_worker.llm_client import LLMClient, LLMResponse
from math_worker.loop import MathWorkerLoop
from math_worker.prompts import (
    BID_AMOUNT_SYSTEM,
    SOLVE_PROBLEM_SYSTEM,
    TASK_SELECTION_SYSTEM,
)
from task_feeder.acceptance import AcceptanceLoop
from task_feeder.config import AcceptanceConfig, TaskFeederConfig
from task_feeder.loop import TaskFeederLoop
from task_feeder.posting import TaskFeederPostingCoordinator
from task_feeder.reader import load_task_snapshot
from tests.e2e.test_unattended_economy import (
    _leased_commands,
    _placeholder_llm_config,
    _runtime_config,
)

if TYPE_CHECKING:
    from pathlib import Path

    from service_auth.agent import BaseAgent

pytestmark = pytest.mark.e2e

NO_BID_TITLE = "Adversarial unattended no-bid expiry"
STALLED_TITLE = "Adversarial unattended accepted-worker expiry"
TASK_ID_RE = re.compile(r"--- task_id: (\S+) ---")


class _StalledSolveTransport:
    def __init__(self) -> None:
        self.solve_started = asyncio.Event()
        self.release = asyncio.Event()

    async def complete(self, system_prompt: str, user_prompt: str) -> LLMResponse:
        if system_prompt == TASK_SELECTION_SYSTEM:
            match = TASK_ID_RE.search(user_prompt)
            content = match.group(1) if match else "NONE"
        elif system_prompt == BID_AMOUNT_SYSTEM:
            content = "1"
        elif system_prompt == SOLVE_PROBLEM_SYSTEM:
            self.solve_started.set()
            await self.release.wait()
            content = "ANSWER: too late"
        else:
            content = "NONE"
        return LLMResponse(
            content=content,
            finish_reason="stop",
            prompt_tokens=0,
            completion_tokens=0,
        )

    async def close(self) -> None:
        self.release.set()


class _MustNotRunTransport:
    async def complete(self, system_prompt: str, user_prompt: str) -> LLMResponse:
        raise AssertionError((system_prompt, user_prompt))

    async def close(self) -> None:
        return


def _write_tasks(path: Path) -> None:
    rows = (
        {
            "title": NO_BID_TITLE,
            "spec": "TASK: Return 1. OUTPUT FORMAT: A single integer.",
            "solutions": ["1"],
            "level": 1,
            "problem_type": "addition_positive",
        },
        {
            "title": STALLED_TITLE,
            "spec": "TASK: Calculate 10 + 20. OUTPUT FORMAT: A single integer.",
            "solutions": ["30"],
            "level": 2,
            "problem_type": "addition_positive",
        },
    )
    with path.open("w") as output:
        for row in rows:
            output.write(json.dumps(row) + "\n")


async def _wait_for_tasks(feed: TaskFeederLoop) -> tuple[str, str]:
    deadline = asyncio.get_running_loop().time() + 20
    while asyncio.get_running_loop().time() < deadline:
        by_title = {task.title: task_id for task_id, task in feed.task_map.items()}
        if NO_BID_TITLE in by_title and STALLED_TITLE in by_title:
            feed.stop()
            return by_title[NO_BID_TITLE], by_title[STALLED_TITLE]
        await asyncio.sleep(0.1)
    raise AssertionError("feeder did not post both adversarial tasks")


async def _wait_for_expiry(
    feeder: BaseAgent, no_bid_id: str, stalled_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    deadline = asyncio.get_running_loop().time() + 30
    while asyncio.get_running_loop().time() < deadline:
        no_bid = await feeder.get_task(no_bid_id)
        stalled = await feeder.get_task(stalled_id)
        if no_bid["status"] == stalled["status"] == "expired":
            return no_bid, stalled
        await asyncio.sleep(0.2)
    raise AssertionError("unattended expiry outcomes did not converge")


async def _run(make_funded_agent: Any, tmp_path: Path) -> None:
    stack = ExitStack()
    feeder = await make_funded_agent(name="Adversarial Expiry Feeder", balance=500)
    stalled = await make_funded_agent(name="Adversarial Stalled Worker", balance=0)
    ineligible = await make_funded_agent(name="Adversarial Ineligible Worker", balance=0)
    feeder_before = int((await feeder.get_balance())["balance"])
    stalled_before = int((await stalled.get_balance())["balance"])
    tasks_path = tmp_path / "tasks.jsonl"
    _write_tasks(tasks_path)
    runtime_config = _runtime_config(tmp_path / "runtime-state")
    feeder_commands, feeder_store = _leased_commands(
        agent=feeder,
        runtime_config=runtime_config,
        role="task-feeder",
        profile="adversarial-feeder",
        handle="adversarial-feeder",
        journal_stack=stack,
    )
    stalled_commands, _ = _leased_commands(
        agent=stalled,
        runtime_config=runtime_config,
        role="math-worker",
        profile="adversarial-stalled",
        handle="adversarial-stalled",
        journal_stack=stack,
    )
    ineligible_commands, _ = _leased_commands(
        agent=ineligible,
        runtime_config=runtime_config,
        role="math-worker",
        profile="adversarial-ineligible",
        handle="adversarial-ineligible",
        journal_stack=stack,
    )
    feeder_config = TaskFeederConfig(
        handle="adversarial-feeder",
        tasks_file=str(tasks_path),
        source_max_bytes=65_536,
        feed_interval_seconds=1,
        max_open_tasks=2,
        bidding_deadline_seconds=3,
        acceptance_window_seconds=3,
        execution_deadline_seconds=1,
        review_deadline_seconds=10,
        review_interval_seconds=1,
        retry_backoff_seconds=1,
        base_reward=10,
        reward_per_level=10,
        shuffle=False,
    )
    posting_store = stack.enter_context(
        FileTaskPostingStore.attach(feeder_store, max_source_bytes=65_536)
    )
    posting = TaskFeederPostingCoordinator(feeder, posting_store)
    posting.bind_current_source(load_task_snapshot(tasks_path, max_bytes=65_536), feeder_config)
    feed = TaskFeederLoop(agent=feeder, config=feeder_config, posting=posting)
    acceptance = AcceptanceLoop(
        agent=feeder,
        config=AcceptanceConfig(
            acceptance_after_seconds=1,
            acceptance_poll_interval_seconds=1,
            min_bids_to_accept=1,
            bidding_deadline_seconds=3,
        ),
        now=lambda: datetime.now(UTC),
        commands=feeder_commands,
    )
    stalled_transport = _StalledSolveTransport()
    ineligible_transport = _MustNotRunTransport()
    stalled_client = LLMClient(_placeholder_llm_config(), transport=stalled_transport)
    ineligible_client = LLMClient(_placeholder_llm_config(), transport=ineligible_transport)
    stalled_loop = MathWorkerLoop(
        agent=stalled,
        llm=stalled_client,
        config=MathWorkerConfig(
            handle="adversarial-stalled",
            scan_interval_seconds=1,
            poll_interval_seconds=1,
            max_poll_attempts=60,
            error_backoff_seconds=1,
            min_reward=25,
            max_reward=35,
        ),
        commands=stalled_commands,
    )
    ineligible_loop = MathWorkerLoop(
        agent=ineligible,
        llm=ineligible_client,
        config=MathWorkerConfig(
            handle="adversarial-ineligible",
            scan_interval_seconds=1,
            poll_interval_seconds=1,
            max_poll_attempts=60,
            error_backoff_seconds=1,
            min_reward=100,
            max_reward=110,
        ),
        commands=ineligible_commands,
    )
    loops = (feed, acceptance, stalled_loop, ineligible_loop)
    tasks = [
        asyncio.create_task(feed.run()),
        asyncio.create_task(acceptance.run(retry_backoff_seconds=1)),
        asyncio.create_task(stalled_loop.run()),
        asyncio.create_task(ineligible_loop.run()),
    ]
    try:
        no_bid_id, stalled_id = await _wait_for_tasks(feed)
        await asyncio.wait_for(stalled_transport.solve_started.wait(), timeout=10)
        no_bid, stalled_task = await _wait_for_expiry(feeder, no_bid_id, stalled_id)
        assert no_bid["bid_count"] == 0
        assert no_bid["poster_refund"] == 20
        assert stalled_task["accepted_bid_amount"] == 1
        assert stalled_task["poster_refund"] == 30
        assert stalled_task["worker_payment"] == 0
        assert int((await feeder.get_balance())["balance"]) == feeder_before
        assert int((await stalled.get_balance())["balance"]) == stalled_before
    finally:
        for loop in loops:
            loop.stop()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await stalled_client.close()
        await ineligible_client.close()
        await ineligible.close()
        await stalled.close()
        await feeder.close()
        stack.close()


async def test_unattended_no_bid_and_accepted_worker_expiry(
    make_funded_agent: Any, tmp_path: Path
) -> None:
    await _run(make_funded_agent, tmp_path)
