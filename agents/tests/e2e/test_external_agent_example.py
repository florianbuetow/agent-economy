"""Run examples/external_agent.py's journeys against the live services.

The example uses only ``service_auth`` with explicit URLs and a key directory. The operator
funding step the script asks for is performed here through the platform agent fixture.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from service_auth.financial_identity import operation_id_for

if TYPE_CHECKING:
    from types import ModuleType

    from base_agent.config import AgentConfig
    from base_agent.platform import PlatformAgent

EXAMPLE_PATH = Path(__file__).resolve().parents[3] / "examples" / "external_agent.py"
REWARD = 100
SPEC = "Reply with any text."
WINDOWS = {
    "bidding_deadline_seconds": 120,
    "acceptance_window_seconds": 120,
    "execution_deadline_seconds": 300,
    "review_deadline_seconds": 120,
}


def _load_example() -> ModuleType:
    spec = importlib.util.spec_from_file_location("external_agent_example", EXAMPLE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.e2e
async def test_external_agents_complete_a_posted_task(
    agent_config: AgentConfig, platform_agent: PlatformAgent, tmp_path: Path
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

    def build(name: str):
        return example.build_agent(
            name,
            tmp_path / "keys",
            urls,
            agent_config.token_ttl_seconds,
            policy.max_lifetime_seconds,
            policy.clock_skew_seconds,
        )

    poster = build("external-poster-e2e")
    worker = build("external-worker-e2e")
    try:
        poster_id = await example.onboard(poster)
        await example.onboard(worker)

        async def operator_funds() -> None:
            reference = "external_agent_example_e2e"
            await platform_agent.credit_account(
                account_id=poster_id,
                amount=REWARD,
                reference=reference,
                operation_id=operation_id_for("e2e-credit", (poster_id, reference)),
            )

        await asyncio.gather(
            example.wait_until_funded(poster, REWARD, timing),
            operator_funds(),
        )

        task_id = await example.post_task(poster, "External task", SPEC, REWARD, WINDOWS, timing)

        worker_task, settled = await asyncio.gather(
            example.work_one_task(worker, lambda spec: spec.encode(), timing, poster_id),
            example.settle_task(poster, task_id, lambda content: content == SPEC.encode(), timing),
        )

        assert worker_task["task_id"] == task_id
        assert settled["worker_id"] == worker.agent_id

        async def approved() -> dict[str, Any] | None:
            current = await poster.get_task(task_id)
            return current if current["status"] == "approved" else None

        await example.wait_for(approved, timing, "approval to settle")
    finally:
        await poster.close()
        await worker.close()
