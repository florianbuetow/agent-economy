"""ECO-07 valid workload exploration without invented routing policy."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

from .native_task_contract import publish_task

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("burst_size", [8])
async def test_valid_adversarial_workloads_preserve_distinct_tasks_and_inputs(
    make_funded_agent: Any,
    burst_size: int,
) -> None:
    poster = await make_funded_agent(name="Adversarial workload poster", balance=100_000)
    try:
        cases = [
            ("Repeated title", "Return A even though a later sentence says return B.", 1),
            ("Repeated title", "Return B; category: coding mixed with arithmetic.", 2),
            ("Unicode Ω task", "Normalize nothing; preserve Ω, é, and 漢字 exactly.", 3),
            ("Long specification", "x" * 10_000, 4),
            ("Large reward", "A valid high-value task inside available funds.", 50_000),
            ("Tiny reward", "A valid one-credit task.", 1),
            ("Contradictory task", "Do X. Do not do X. Explain the ambiguity.", 5),
            ("Mixed category", "Classify text, calculate 2+2, then return JSON.", 6),
        ]
        assert len(cases) == burst_size
        tasks = await asyncio.gather(
            *(
                publish_task(
                    poster,
                    title=title,
                    spec=spec,
                    reward=reward,
                    bidding_deadline_seconds=3600,
                    acceptance_window_seconds=3600,
                    execution_deadline_seconds=3600,
                    review_deadline_seconds=3600,
                )
                for title, spec, reward in cases
            )
        )
        assert len({task["task_id"] for task in tasks}) == burst_size
        assert [task["title"] for task in tasks].count("Repeated title") == 2
        long_task = next(task for task in tasks if task["title"] == "Long specification")
        assert len(long_task["spec"]) == 10_000
        assert all(task["status"] == "open" for task in tasks)

        task_id = f"t-{uuid.uuid4()}"
        intent = poster.new_task_publication(
            task_id=task_id,
            title="Maximum input-count task",
            spec="Accept exactly ten distinct input files.",
            reward=10,
            bidding_deadline_seconds=3600,
            acceptance_window_seconds=3600,
            execution_deadline_seconds=3600,
            review_deadline_seconds=3600,
            expected_input_count=10,
        )
        admitted = await poster.admit_task_publication(intent)
        assert admitted["publication_state"] == "awaiting_inputs"
        uploads = await asyncio.gather(
            *(
                poster.upload_task_input(
                    intent["publication_operation_id"],
                    f"input-{index}.txt",
                    f"input-{index}".encode(),
                    content_type="text/plain",
                    idempotency_key=f"adversarial-input-{index}",
                )
                for index in range(10)
            )
        )
        assert len({upload["artifact_id"] for upload in uploads}) == 10
        status, published = await poster.publish_task_publication(intent)
        assert status == 201
        assert published["publication_state"] == "published"
        manifest = await poster.get_task_inputs(task_id)
        input_manifest = manifest["input_manifest"]
        assert len(input_manifest["files"]) == 10
        assert {file["filename"] for file in input_manifest["files"]} == {
            f"input-{index}.txt" for index in range(10)
        }
    finally:
        await poster.close()
