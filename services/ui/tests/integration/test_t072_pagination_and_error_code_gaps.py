"""Gap coverage: an error-code assertion and two clamp/filter boundaries
that existing tests exercise only at the status-code level, or not at all.

Spec IDs (docs/specifications/service-tests/ui-service-tests.md):
- TASK-02: GET /api/tasks?status=<invalid> must use error code
  "invalid_status" specifically (per the Required API Error Contract table),
  not the generic "invalid_parameter". tests/integration/test_task_list.py's
  test_invalid_status_returns_400 only checks the 400 status code.
- TASK-06: GET /api/tasks/-/uncontested?min_age_minutes=N must exclude tasks
  younger than N minutes and include ones at/above N minutes. Existing
  uncontested tests (tests/integration/test_tasks.py) only exercise
  min_age_minutes=0, which has no filtering effect.
- AGT-05: GET /api/agents/{id}/feed?limit=<over 200> is clamped to 200, not
  rejected. No existing feed test in tests/integration/test_agents.py uses a
  limit above 200.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.integration


async def test_invalid_task_status_filter_uses_invalid_status_error_code(client) -> None:
    """TASK-02: the specific error code, not just the 400 status."""
    response = await client.get("/api/tasks?status=not_a_status")
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_status"


async def test_uncontested_min_age_minutes_excludes_younger_tasks(client, write_db) -> None:
    """TASK-06: a task younger than min_age_minutes is excluded; an older one is included."""
    now = datetime.now(UTC)
    young_created_at = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    old_created_at = (now - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")

    for task_id, created_at in (
        ("t-uncontested-young", young_created_at),
        ("t-uncontested-old", old_created_at),
    ):
        await write_db.execute(
            "INSERT INTO board_tasks "
            "(task_id, poster_id, title, spec, reward, status, bidding_deadline_seconds, "
            "deadline_seconds, review_deadline_seconds, bidding_deadline, escrow_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task_id,
                "a-alice",
                f"Task {task_id}",
                "Generated task spec",
                10,
                "open",
                86400,
                604800,
                172800,
                created_at,
                f"esc-{task_id}",
                created_at,
            ),
        )
    await write_db.commit()

    response = await client.get("/api/tasks/-/uncontested?min_age_minutes=10&limit=50")
    assert response.status_code == 200
    task_ids = {t["task_id"] for t in response.json()["tasks"]}
    assert "t-uncontested-old" in task_ids
    assert "t-uncontested-young" not in task_ids


async def test_feed_limit_clamped_above_200_not_rejected(client) -> None:
    """AGT-05: limit=10000 is clamped to 200, not a 400 error."""
    response = await client.get("/api/agents/a-alice/feed?limit=10000")
    assert response.status_code == 200
    assert len(response.json()["events"]) <= 200
