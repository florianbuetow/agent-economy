"""Independent public-agent cycle with inputs and earned-money reposting."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from service_auth.financial_identity import operation_id_for

if TYPE_CHECKING:
    from types import ModuleType

    from base_agent.agent import BaseAgent
    from base_agent.config import AgentConfig
    from base_agent.platform import PlatformAgent

EXAMPLE_PATH = Path(__file__).resolve().parents[3] / "examples" / "external_agent.py"
WINDOWS = {
    "bidding_deadline_seconds": 120,
    "acceptance_window_seconds": 120,
    "execution_deadline_seconds": 300,
    "review_deadline_seconds": 120,
}
INITIAL_GRANT = 500
FIRST_PRICE = 200
SECOND_PRICE = 150
INPUT_BYTES = b"customer says the device is reliable"
OUTPUT_BYTES = b"positive"


def _load_example() -> ModuleType:
    spec = importlib.util.spec_from_file_location("external_agent_input_cycle", EXAMPLE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


async def _complete_first_job(
    example: ModuleType,
    poster: BaseAgent,
    worker: BaseAgent,
    poster_id: str,
    timing: Any,
) -> None:
    task_id = await example.post_task(
        poster,
        "Earn before posting",
        "Return the first result.",
        FIRST_PRICE,
        WINDOWS,
        timing,
    )
    worker_result, settled = await asyncio.gather(
        example.work_one_task(worker, lambda _spec: b"first result", timing, poster_id),
        example.settle_task(
            poster,
            task_id,
            lambda content: content == b"first result",
            timing,
        ),
    )
    assert worker_result["task_id"] == task_id
    assert settled["status"] == "approved"
    assert settled["accepted_bid_amount"] == FIRST_PRICE


async def _complete_input_job(
    example: ModuleType,
    poster: BaseAgent,
    worker: BaseAgent,
    worker_id: str,
    timing: Any,
) -> None:
    task_input = example.TaskInput(
        filename="request.txt",
        content=INPUT_BYTES,
        content_type="text/plain",
        idempotency_key="external-cycle-request-v1",
    )
    task_id = await example.post_task_with_inputs(
        poster,
        "Classify attached input",
        "Read request.txt and return its sentiment label.",
        SECOND_PRICE,
        WINDOWS,
        (task_input,),
        timing,
    )
    manifest_response = await poster.get_task_inputs(task_id)
    manifest = manifest_response["input_manifest"]
    assert isinstance(manifest, dict)
    assert len(manifest["files"]) == 1
    input_metadata = manifest["files"][0]
    assert input_metadata["asset_id"].startswith("asset-")
    assert input_metadata["filename"] == "request.txt"
    assert input_metadata["size_bytes"] == len(INPUT_BYTES)
    assert input_metadata["content_sha256"] == hashlib.sha256(INPUT_BYTES).hexdigest()
    assert input_metadata["content_type"].startswith("text/plain")
    assert input_metadata["role"] == "input"

    observed_inputs: dict[str, bytes] = {}

    def classify(_spec: str, inputs: dict[str, bytes]) -> bytes:
        observed_inputs.update(inputs)
        return OUTPUT_BYTES

    worker_result, settled = await asyncio.gather(
        example.work_one_task_with_inputs(worker, classify, timing, worker_id),
        example.settle_task(
            poster,
            task_id,
            lambda content: content == OUTPUT_BYTES,
            timing,
        ),
    )
    assert observed_inputs == {"request.txt": INPUT_BYTES}
    assert worker_result["task_id"] == task_id
    assert settled["status"] == "approved"
    assert settled["accepted_bid_amount"] == SECOND_PRICE
    assert settled["worker_id"] == worker.agent_id


@pytest.mark.e2e
async def test_external_worker_earns_then_posts_an_input_task(
    agent_config: AgentConfig,
    platform_agent: PlatformAgent,
    tmp_path: Path,
) -> None:
    example = _load_example()
    urls = {
        "identity": agent_config.identity_url,
        "bank": agent_config.bank_url,
        "task_board": agent_config.task_board_url,
        "reputation": agent_config.reputation_url,
        "court": agent_config.court_url,
    }
    policy = agent_config.token_policy
    timing = example.Timing(poll_seconds=0.5, timeout_seconds=60.0)

    def build(name: str) -> BaseAgent:
        return example.build_agent(
            name,
            tmp_path / "keys",
            urls,
            agent_config.token_ttl_seconds,
            policy.max_lifetime_seconds,
            policy.clock_skew_seconds,
        )

    first_poster = build("external-cycle-first-poster")
    earning_worker = build("external-cycle-earning-worker")
    try:
        first_poster_id = await example.onboard(first_poster)
        earning_worker_id = await example.onboard(earning_worker)

        reference = "external-input-cycle-initial-grant"
        await platform_agent.credit_account(
            account_id=first_poster_id,
            amount=INITIAL_GRANT,
            reference=reference,
            operation_id=operation_id_for("e2e-credit", (first_poster_id, reference)),
        )
        assert (await first_poster.get_balance())["balance"] == INITIAL_GRANT
        assert (await earning_worker.get_balance())["balance"] == 0

        await _complete_first_job(
            example,
            first_poster,
            earning_worker,
            first_poster_id,
            timing,
        )
        assert (await earning_worker.get_balance())["balance"] == FIRST_PRICE

        await _complete_input_job(
            example,
            earning_worker,
            first_poster,
            earning_worker_id,
            timing,
        )

        first_balance = (await first_poster.get_balance())["balance"]
        earned_then_spent_balance = (await earning_worker.get_balance())["balance"]
        assert first_balance == INITIAL_GRANT - FIRST_PRICE + SECOND_PRICE
        assert earned_then_spent_balance == FIRST_PRICE - SECOND_PRICE
        assert first_balance + earned_then_spent_balance == INITIAL_GRANT
    finally:
        await first_poster.close()
        await earning_worker.close()
