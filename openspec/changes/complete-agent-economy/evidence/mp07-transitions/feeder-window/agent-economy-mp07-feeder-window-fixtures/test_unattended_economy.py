"""Headline e2e (T-102 / plan §8 item 2 / GAP-E7): the fully unattended
economy — no demo script, no human, no SDK-driven shortcuts.

Runs the REAL production loops as concurrent in-test asyncio tasks against
the live stack:

- ``task_feeder.loop.TaskFeederLoop``      (posts tasks from a JSONL file)
- ``task_feeder.acceptance.AcceptanceLoop`` (autonomously accepts bids, WP-15)
- ``task_feeder.review.ReviewLoop``         (auto-approves/disputes on
  answer match, reading the worker's real uploaded asset)
- ``math_worker.loop.MathWorkerLoop`` x2    (scan -> bid -> solve -> submit
  -> review-poll -> [dispute -> rebuttal -> ruling-poll]), each wired to a
  deterministic ``LLMTransport`` (T-102's injectable seam) instead of a
  real LLM — one worker always solves correctly, the other always submits
  a fixed wrong answer.

Nothing in this test calls ``approve_task``/``dispute_task``/
``trigger_ruling`` directly, and nothing runs ``demo_replay``. Two tasks
travel through disjoint terminal paths:

    task 1 (correct):  posted -> bid -> accepted -> submitted -> approved
    task 2 (wrong):     posted -> bid -> accepted -> submitted -> disputed
                        -> rebutted -> ruled (split payout)

Failing-first note: this exercises machinery whose pieces landed
separately and were never previously proven together end-to-end without a
human or demo script — WP-15 autonomous acceptance (``dd6957f``), WP-06
autonomous ruling triggers (``6aba0e2``), and this WP-09 change set (the
T-102 deterministic transport seam plus the ReviewLoop asset-fallback fix
this test uncovered — without it every submission was disputed
regardless of correctness, so the "approved" path could not have passed
before). A true pre-change red for the seam itself is in
``tests/unit/math_worker/test_llm_client.py`` (the constructor rejects
``transport=`` before T-102) and
``tests/unit/task_feeder/test_review_asset_fallback.py`` (review always
disputed before the asset fallback).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from math_worker.config import LLMConfig, MathWorkerConfig
from math_worker.llm_client import LLMClient
from math_worker.loop import MathWorkerLoop
from task_feeder.acceptance import AcceptanceLoop
from task_feeder.config import AcceptanceConfig, TaskFeederConfig
from task_feeder.loop import TaskFeederLoop
from task_feeder.review import ReviewLoop
from tests.e2e.deterministic_math_transport import DeterministicArithmeticTransport

if TYPE_CHECKING:
    from pathlib import Path

    from base_agent.agent import BaseAgent

CORRECT_TITLE = "WP09 Unattended Economy — Correct"
WRONG_TITLE = "WP09 Unattended Economy — Wrong Answer"

BASE_REWARD = 10
REWARD_PER_LEVEL = 10
CORRECT_LEVEL = 1  # reward = 10 + 1*10 = 20
WRONG_LEVEL = 2  # reward = 10 + 2*10 = 30
CORRECT_REWARD = BASE_REWARD + CORRECT_LEVEL * REWARD_PER_LEVEL
WRONG_REWARD = BASE_REWARD + WRONG_LEVEL * REWARD_PER_LEVEL

BIDDING_DEADLINE_SECONDS = 180
EXECUTION_DEADLINE_SECONDS = 300
REVIEW_DEADLINE_SECONDS = 180

POST_BOTH_TASKS_BUDGET_SECONDS = 30
POLL_BUDGET_SECONDS = 150
POLL_INTERVAL_SECONDS = 1.0


@dataclass
class _Rig:
    """Everything the test needs to tear down after the run."""

    feeder: BaseAgent
    worker_correct: BaseAgent
    worker_wrong: BaseAgent
    worker_correct_balance_before: int
    worker_wrong_balance_before: int
    feed_loop: TaskFeederLoop
    loops: list[Any]
    background_tasks: list[asyncio.Task[None]]


def _write_tasks_jsonl(path: Path) -> None:
    tasks = [
        {
            "title": CORRECT_TITLE,
            "spec": (
                "TASK: Calculate 17 + 25.\n\n"
                "OUTPUT FORMAT: A single integer.\n\n"
                "VERIFICATION: Sum the numbers and compare."
            ),
            "solutions": ["42"],
            "level": CORRECT_LEVEL,
            "problem_type": "addition_positive",
        },
        {
            "title": WRONG_TITLE,
            "spec": (
                "TASK: Calculate 10 + 15.\n\n"
                "OUTPUT FORMAT: A single integer.\n\n"
                "VERIFICATION: Sum the numbers and compare."
            ),
            "solutions": ["25"],
            "level": WRONG_LEVEL,
            "problem_type": "addition_positive",
        },
    ]
    with path.open("w") as fh:
        for task in tasks:
            fh.write(json.dumps(task) + "\n")


def _placeholder_llm_config() -> LLMConfig:
    """Unused by the deterministic transport — LLMClient still requires a
    valid LLMConfig to keep its constructor shape stable (T-102)."""
    return LLMConfig(
        base_url="http://unused.invalid/v1",
        api_key="unused",
        model_id="deterministic",
        temperature=0.0,
        max_tokens=64,
    )


def _build_feeder_loops(feeder: BaseAgent, tasks_path: Path) -> tuple[TaskFeederLoop, list[Any]]:
    feeder_config = TaskFeederConfig(
        handle="wp09_feeder",
        tasks_file=str(tasks_path),
        feed_interval_seconds=1,
        max_open_tasks=2,
        bidding_deadline_seconds=BIDDING_DEADLINE_SECONDS,
        execution_deadline_seconds=EXECUTION_DEADLINE_SECONDS,
        review_deadline_seconds=REVIEW_DEADLINE_SECONDS,
        review_interval_seconds=1,
        base_reward=BASE_REWARD,
        reward_per_level=REWARD_PER_LEVEL,
        shuffle=False,
    )
    acceptance_config = AcceptanceConfig(
        acceptance_after_seconds=60,
        acceptance_poll_interval_seconds=1,
        min_bids_to_accept=1,
        bidding_deadline_seconds=BIDDING_DEADLINE_SECONDS,
    )

    feed_loop = TaskFeederLoop(agent=feeder, config=feeder_config)
    acceptance_loop = AcceptanceLoop(
        agent=feeder, config=acceptance_config, now=lambda: datetime.now(UTC)
    )
    review_loop = ReviewLoop(agent=feeder, task_map=feed_loop.task_map)
    return feed_loop, [feed_loop, acceptance_loop, review_loop]


def _worker_config(handle: str, reward: int) -> MathWorkerConfig:
    return MathWorkerConfig(
        handle=handle,
        scan_interval_seconds=1,
        poll_interval_seconds=1,
        max_poll_attempts=120,
        error_backoff_seconds=1,
        min_reward=reward - 5,
        max_reward=reward + 5,
    )


def _build_worker_loops(
    worker_correct: BaseAgent, worker_wrong: BaseAgent
) -> tuple[MathWorkerLoop, MathWorkerLoop]:
    llm_correct = LLMClient(
        _placeholder_llm_config(), transport=DeterministicArithmeticTransport(correct=True)
    )
    llm_wrong = LLMClient(
        _placeholder_llm_config(), transport=DeterministicArithmeticTransport(correct=False)
    )
    loop_correct = MathWorkerLoop(
        agent=worker_correct,
        llm=llm_correct,
        config=_worker_config("wp09_worker_correct", CORRECT_REWARD),
    )
    loop_wrong = MathWorkerLoop(
        agent=worker_wrong,
        llm=llm_wrong,
        config=_worker_config("wp09_worker_wrong", WRONG_REWARD),
    )
    return loop_correct, loop_wrong


async def _build_rig(make_funded_agent: Any, tasks_path: Path) -> _Rig:
    feeder = await make_funded_agent(name="WP09 Feeder", balance=5000)
    worker_correct = await make_funded_agent(name="WP09 Worker Correct", balance=1000)
    worker_wrong = await make_funded_agent(name="WP09 Worker Wrong", balance=1000)

    worker_correct_balance_before = (await worker_correct.get_balance())["balance"]
    worker_wrong_balance_before = (await worker_wrong.get_balance())["balance"]

    _write_tasks_jsonl(tasks_path)
    feed_loop, feeder_loops = _build_feeder_loops(feeder, tasks_path)
    acceptance_loop, review_loop = feeder_loops[1], feeder_loops[2]
    worker_loop_correct, worker_loop_wrong = _build_worker_loops(worker_correct, worker_wrong)
    loops = [*feeder_loops, worker_loop_correct, worker_loop_wrong]

    background_tasks = [
        asyncio.create_task(feed_loop.run()),
        asyncio.create_task(acceptance_loop.run()),
        asyncio.create_task(review_loop.run(1)),
        asyncio.create_task(worker_loop_correct.run()),
        asyncio.create_task(worker_loop_wrong.run()),
    ]

    return _Rig(
        feeder=feeder,
        worker_correct=worker_correct,
        worker_wrong=worker_wrong,
        worker_correct_balance_before=worker_correct_balance_before,
        worker_wrong_balance_before=worker_wrong_balance_before,
        feed_loop=feed_loop,
        loops=loops,
        background_tasks=background_tasks,
    )


async def _wait_for_both_tasks_posted(feed_loop: TaskFeederLoop) -> tuple[str, str]:
    """Stop feeding as soon as both target tasks are posted — the JSONL
    file cycles forever otherwise, and this test only cares about these
    two.
    """
    loop_time = asyncio.get_event_loop().time
    deadline = loop_time() + POST_BOTH_TASKS_BUDGET_SECONDS
    correct_task_id: str | None = None
    wrong_task_id: str | None = None
    while loop_time() < deadline:
        for task_id, raw_task in feed_loop.task_map.items():
            if raw_task.title == CORRECT_TITLE and correct_task_id is None:
                correct_task_id = task_id
            if raw_task.title == WRONG_TITLE and wrong_task_id is None:
                wrong_task_id = task_id
        if correct_task_id is not None and wrong_task_id is not None:
            break
        await asyncio.sleep(0.5)

    assert correct_task_id is not None, "feeder never posted the correct-answer task"
    assert wrong_task_id is not None, "feeder never posted the wrong-answer task"
    feed_loop.stop()
    return correct_task_id, wrong_task_id


async def _wait_for_terminal_states(
    feeder: BaseAgent, correct_task_id: str, wrong_task_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Poll only — no direct approve_task/dispute_task/trigger_ruling calls."""
    loop_time = asyncio.get_event_loop().time
    deadline = loop_time() + POLL_BUDGET_SECONDS
    approved_task: dict[str, Any] | None = None
    ruled_task: dict[str, Any] | None = None
    while loop_time() < deadline:
        if approved_task is None:
            current = await feeder.get_task(correct_task_id)
            if current["status"] == "approved":
                approved_task = current
        if ruled_task is None:
            current = await feeder.get_task(wrong_task_id)
            if current["status"] == "ruled":
                ruled_task = current
        if approved_task is not None and ruled_task is not None:
            break
        await asyncio.sleep(POLL_INTERVAL_SECONDS)

    assert approved_task is not None, (
        "correct-answer task never reached 'approved' via the real feeder+worker loops"
    )
    assert ruled_task is not None, (
        "wrong-answer task never reached 'ruled' via the real feeder+worker loops"
    )
    return approved_task, ruled_task


async def _teardown(rig: _Rig) -> None:
    for loop in rig.loops:
        loop.stop()
    for task in rig.background_tasks:
        task.cancel()
    await asyncio.gather(*rig.background_tasks, return_exceptions=True)
    for agent in (rig.feeder, rig.worker_correct, rig.worker_wrong):
        await agent.close()


@pytest.mark.e2e
async def test_unattended_economy_two_tasks_diverge_correctly(
    make_funded_agent: Any, tmp_path: Path
) -> None:
    """One task is auto-approved; the other is auto-disputed, rebutted, and
    ruled with a partial payout — driven entirely by the real feeder and
    worker loops, no demo engine, no human, no direct API shortcuts.
    """
    rig = await _build_rig(make_funded_agent, tmp_path / "wp09_tasks.jsonl")
    try:
        correct_task_id, wrong_task_id = await _wait_for_both_tasks_posted(rig.feed_loop)
        approved_task, ruled_task = await _wait_for_terminal_states(
            rig.feeder, correct_task_id, wrong_task_id
        )

        # --- Task 1: posted -> bid -> accepted -> submitted -> approved ---
        assert approved_task["worker_id"] == rig.worker_correct.agent_id
        worker_correct_balance_after = (await rig.worker_correct.get_balance())["balance"]
        assert worker_correct_balance_after == rig.worker_correct_balance_before + CORRECT_REWARD

        # --- Task 2: posted -> bid -> accepted -> submitted -> disputed ---
        # --- -> rebutted -> ruled (split payout) ---
        assert ruled_task["worker_id"] == rig.worker_wrong.agent_id
        assert ruled_task["dispute_id"]
        assert isinstance(ruled_task["worker_pct"], int)
        assert ruled_task["worker_pct"] == 50  # mock judge, config.yaml mock_worker_pct
        worker_wrong_balance_after = (await rig.worker_wrong.get_balance())["balance"]
        payout = worker_wrong_balance_after - rig.worker_wrong_balance_before
        assert 0 < payout < WRONG_REWARD, (
            f"expected a partial (split) payout strictly between 0 and {WRONG_REWARD}, got {payout}"
        )
    finally:
        await _teardown(rig)
