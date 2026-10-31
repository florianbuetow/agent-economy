"""The report CLI reads config, journals and Reputation; it never picks a winner."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest
import yaml

from economy_report.__main__ import build_report
from tests.unit.math_worker.runtime_fixtures import WORKER_ID, runtime_worker

if TYPE_CHECKING:
    from pathlib import Path

PRICING = {
    "initial_bid_pct": 80,
    "min_bid_pct": 50,
    "max_bid_pct": 100,
    "raise_step_pct": 5,
    "lower_step_pct": 10,
    "min_samples": 3,
    "min_approval_pct": 50,
}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_report_from_config_journals_and_quality(tmp_path: Path) -> None:
    root = tmp_path / "rt"
    async with runtime_worker(root) as (_agent, _store, commands):
        for index, outcome in enumerate(("approved", "disputed_lost", "approved"), start=1):
            commands.jobs.save(
                f"t-10000000-0000-4000-8000-00000000020{index}",
                {
                    "outcome": outcome,
                    "task_type": "sentiment",
                    "outcome_seq": index,
                    "bid_amount": 40,
                    "payout": 40 if outcome == "approved" else 0,
                },
            )
    config = {
        "platform": {"reputation_url": "http://rep"},
        "runtime_state": {"directory": str(root)},
        "classification_workers": {"mathbot_runtime_fixture": {"pricing": PRICING}},
        "economy_report": {
            "worker_roles": ["math-worker"],
            "confidence_z": 1.96,
            "quality_timeout_seconds": 1,
        },
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/agents/{WORKER_ID}/quality"
        return httpx.Response(
            200,
            json={
                "delivery_quality": {"rated": True, "score": 67, "task_count": 3},
                "specification_quality": {"rated": False, "score": None, "task_count": 0},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        text = await build_report(path, client)
    assert "approved 2/3 = 67%" in text
    assert "cost paid 80" in text
    assert "sentiment: 2/3" in text
    assert "delivery_quality 67/100 (n=3), specification_quality unrated (n=0)" in text
