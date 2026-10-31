"""Small test-only primitives for the native Task Board command contract."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from base_agent.agent import BaseAgent


def new_operation_id() -> str:
    """Allocate an explicit caller-owned operation identity for one test command."""
    return f"op-{uuid.uuid4()}"


def lifecycle_version(task: dict[str, Any]) -> int:
    """Read and validate the task's native optimistic-concurrency version."""
    value = task.get("lifecycle_version")
    assert type(value) is int and value >= 0
    return value


async def publish_task(
    agent: BaseAgent,
    *,
    title: str,
    spec: str,
    reward: int,
    bidding_deadline_seconds: int,
    acceptance_window_seconds: int,
    execution_deadline_seconds: int,
    review_deadline_seconds: int,
) -> dict[str, Any]:
    """Publish one fresh fixture task; fault tests retain their own saved intent."""
    intent = agent.prepare_task_posting(
        title=title,
        spec=spec,
        reward=reward,
        bidding_deadline_seconds=bidding_deadline_seconds,
        acceptance_window_seconds=acceptance_window_seconds,
        execution_deadline_seconds=execution_deadline_seconds,
        review_deadline_seconds=review_deadline_seconds,
    )
    response = await agent.submit_task_posting(intent)
    assert response.status_code == 201
    assert response.body["publication_state"] == "published"
    assert response.body["operation_id"] == intent.operation_id
    assert response.body["task_id"] == intent.task_id
    assert response.body["request_sha256"] == intent.request_sha256
    result = response.body["result"]
    assert isinstance(result, dict)
    return cast("dict[str, Any]", result)
