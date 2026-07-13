"""Regression test for T-047: the ``51_to_100`` reward bucket must include 100.

Failing-first proof for the verify-then-fix item in WP-08 (plan §5 WP-08.2):
before the fix, ``reward=100`` fell into ``over_100`` (query used
``reward >= 100``) instead of ``51_to_100`` (query used
``BETWEEN 51 AND 99``, excluding the boundary).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


async def _insert_task(
    write_db,
    task_id: str,
    reward: int,
) -> None:
    await write_db.execute(
        "INSERT INTO board_tasks "
        "(task_id, poster_id, title, spec, reward, status, bidding_deadline_seconds, "
        "deadline_seconds, review_deadline_seconds, bidding_deadline, execution_deadline, "
        "review_deadline, escrow_id, worker_id, accepted_bid_id, dispute_reason, ruling_id, "
        "worker_pct, ruling_summary, created_at, accepted_at, submitted_at, approved_at, "
        "cancelled_at, disputed_at, ruled_at, expired_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            task_id,
            "a-alice",
            f"Task {task_id}",
            "Generated task spec",
            reward,
            "open",
            86400,
            604800,
            172800,
            "2026-03-01T00:00:00Z",
            None,
            None,
            f"esc-{task_id}",
            None,
            None,
            None,
            None,
            None,
            None,
            "2026-03-01T00:00:00Z",
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        ),
    )


async def test_reward_of_exactly_100_falls_in_51_to_100_bucket(client, write_db):
    """A task with reward == 100 is a boundary case for the ``51_to_100`` bucket."""
    before = await client.get("/api/metrics")
    assert before.status_code == 200
    before_buckets = before.json()["labor_market"]["reward_distribution"]
    before_51_100 = before_buckets["51_to_100"]
    before_over_100 = before_buckets["over_100"]

    await _insert_task(write_db, task_id="t-reward-boundary-100", reward=100)
    await write_db.commit()

    after = await client.get("/api/metrics")
    assert after.status_code == 200
    after_buckets = after.json()["labor_market"]["reward_distribution"]

    assert after_buckets["51_to_100"] == before_51_100 + 1
    assert after_buckets["over_100"] == before_over_100


async def test_reward_of_101_falls_in_over_100_bucket(client, write_db):
    """A task with reward == 101 must NOT land in ``51_to_100``."""
    before = await client.get("/api/metrics")
    assert before.status_code == 200
    before_buckets = before.json()["labor_market"]["reward_distribution"]
    before_51_100 = before_buckets["51_to_100"]
    before_over_100 = before_buckets["over_100"]

    await _insert_task(write_db, task_id="t-reward-boundary-101", reward=101)
    await write_db.commit()

    after = await client.get("/api/metrics")
    assert after.status_code == 200
    after_buckets = after.json()["labor_market"]["reward_distribution"]

    assert after_buckets["51_to_100"] == before_51_100
    assert after_buckets["over_100"] == before_over_100 + 1
