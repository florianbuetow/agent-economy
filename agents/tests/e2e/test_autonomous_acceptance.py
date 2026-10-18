"""E2E: the Task Feeder autonomously accepts the winning bid (WP-15 / Q-16).

With the live stack and no demo engine and no human, a posted task is accepted by
the feeder's own acceptance loop — picking the lowest bid — then submitted and
approved through the normal flow.  Before WP-15 the feeder never accepted, so the
task would sit ``open`` until it expired.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import ExitStack
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from service_auth import TaskBoardCommandResponse
from service_auth.financial_identity import operation_id_for

from base_agent.runtime_config import RuntimeStateConfig
from base_agent.runtime_intents import FileTaskIntentStore
from base_agent.runtime_task_commands import RuntimeTaskCommands, runtime_owner_from_agent
from task_feeder.acceptance import AcceptanceLoop
from task_feeder.config import AcceptanceConfig

if TYPE_CHECKING:
    from pathlib import Path

    from base_agent.agent import BaseAgent

REWARD = 500
LOW_BID = 6
HIGH_BID = 8
BIDDING_DEADLINE_SECONDS = 120
ACCEPTANCE_WINDOW_SECONDS = 60


async def _close_agents_and_journals(
    agents_to_close: list[BaseAgent], journal_stack: ExitStack
) -> None:
    results = await asyncio.gather(
        *(agent.close() for agent in agents_to_close), return_exceptions=True
    )
    failures = [result for result in results if isinstance(result, BaseException)]
    try:
        journal_stack.close()
    except BaseException as exc:
        failures.append(exc)
    if failures:
        raise BaseExceptionGroup("autonomous acceptance cleanup failed", failures)


def _task_version(task: dict[str, Any], *, expected_status: str) -> int:
    assert task["status"] == expected_status
    lifecycle_version = task["lifecycle_version"]
    assert type(lifecycle_version) is int
    return lifecycle_version


def _assert_command_response(
    response: TaskBoardCommandResponse, *, status_codes: frozenset[int]
) -> None:
    assert isinstance(response, TaskBoardCommandResponse)
    assert response.status_code in status_codes
    assert isinstance(response.body, dict)


@pytest.mark.e2e
async def test_feeder_autonomously_accepts_lowest_bid(
    make_funded_agent: Any, tmp_path: Path
) -> None:
    agents_to_close: list[BaseAgent] = []
    journal_stack = ExitStack()
    try:
        feeder = await make_funded_agent(name="WP15 Feeder", balance=5000)
        agents_to_close.append(feeder)
        worker_low = await make_funded_agent(name="WP15 Worker Low", balance=1000)
        agents_to_close.append(worker_low)
        worker_high = await make_funded_agent(name="WP15 Worker High", balance=1000)
        agents_to_close.append(worker_high)

        assert worker_low.agent_id is not None
        low_balance_before = (await worker_low.get_balance())["balance"]

        task = await feeder.post_task(
            title="WP15 autonomous acceptance",
            spec="Compute 2 + 2",
            reward=REWARD,
            bidding_deadline_seconds=BIDDING_DEADLINE_SECONDS,
            execution_deadline_seconds=600,
            review_deadline_seconds=300,
            acceptance_window_seconds=ACCEPTANCE_WINDOW_SECONDS,
        )
        task_id = task["task_id"]
        open_version = _task_version(task, expected_status="open")

        # Two competing bids; the lower one must win the undercutting contest.
        high_bid_response = await worker_high.submit_bid(
            task_id=task_id,
            amount=HIGH_BID,
            operation_id=operation_id_for("e2e-autonomous-bid", (task_id, worker_high.agent_id)),
            expected_lifecycle_version=open_version,
        )
        low_bid_response = await worker_low.submit_bid(
            task_id=task_id,
            amount=LOW_BID,
            operation_id=operation_id_for("e2e-autonomous-bid", (task_id, worker_low.agent_id)),
            expected_lifecycle_version=open_version,
        )
        _assert_command_response(high_bid_response, status_codes=frozenset({201}))
        _assert_command_response(low_bid_response, status_codes=frozenset({201}))

        # Quorum of 2 bids makes the task eligible immediately; the acceptance
        # window (60s) stays well inside the 120s bidding deadline (T-035).
        acceptance_config = AcceptanceConfig(
            acceptance_after_seconds=ACCEPTANCE_WINDOW_SECONDS,
            acceptance_poll_interval_seconds=1,
            min_bids_to_accept=2,
            bidding_deadline_seconds=BIDDING_DEADLINE_SECONDS,
        )
        runtime_directory = tmp_path / "runtime-state"
        runtime_directory.mkdir(mode=0o700)
        runtime_config = RuntimeStateConfig(
            environment_id=os.environ["ISOLATED_RUNTIME_ID"],
            directory=runtime_directory,
            diagnostic_limit=8,
            max_record_bytes=65_536,
        )
        owner = runtime_owner_from_agent(
            runtime_config=runtime_config,
            agent=feeder,
            role="task-feeder",
            profile="wp15_feeder",
            handle="wp15_feeder",
        )
        store = journal_stack.enter_context(
            FileTaskIntentStore(
                root_directory=runtime_config.directory,
                owner=owner,
                diagnostic_limit=runtime_config.diagnostic_limit,
                max_record_bytes=runtime_config.max_record_bytes,
            )
        )
        commands = RuntimeTaskCommands(feeder, store)
        loop = AcceptanceLoop(
            agent=feeder,
            config=acceptance_config,
            now=lambda: datetime.now(UTC),
            commands=commands,
        )

        # The feeder accepts autonomously — no demo engine, no human.
        await loop.process_open_tasks()

        accepted = await feeder.get_task(task_id)
        accepted_version = _task_version(accepted, expected_status="accepted")
        assert accepted["worker_id"] == worker_low.agent_id

        # Normal flow to completion: winner delivers, poster approves, winner paid.
        await worker_low.upload_asset(task_id, "result.txt", b"4")
        submission_response = await worker_low.submit_deliverable(
            task_id,
            operation_id=operation_id_for("e2e-autonomous-submit", (task_id,)),
            expected_lifecycle_version=accepted_version,
        )
        _assert_command_response(submission_response, status_codes=frozenset({200}))
        submitted = await feeder.get_task(task_id)
        submitted_version = _task_version(submitted, expected_status="submitted")
        approval_response = await feeder.approve_task(
            task_id,
            operation_id=operation_id_for("e2e-autonomous-approve", (task_id,)),
            expected_lifecycle_version=submitted_version,
        )
        _assert_command_response(approval_response, status_codes=frozenset({200, 202}))

        completed = await feeder.get_task(task_id)
        _task_version(completed, expected_status="approved")

        low_balance_after = (await worker_low.get_balance())["balance"]
        assert low_balance_after == low_balance_before + REWARD
    finally:
        await _close_agents_and_journals(agents_to_close, journal_stack)
