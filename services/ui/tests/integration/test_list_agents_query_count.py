"""Regression test for GAP-E10: ``GET /api/agents`` must not fan out N+1 queries.

Before this fix, ``list_agents`` fetched every agent, then ran
``_compute_agent_stats`` (8 separate scalar queries) per agent, and finally
sorted/paginated in Python. That means the query count scaled linearly with
the number of registered agents — for a 25-agent economy, roughly 200 SQL
round trips for a single dashboard page load.

This test seeds well beyond the base fixture's 5 agents and asserts the total
number of ``db.execute`` calls behind ``GET /api/agents`` stays a small
constant, proving the aggregation now happens in SQL (one COUNT + one
LEFT-JOINed, sorted, paginated SELECT) rather than fanning out per agent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from ui_service.core.state import get_app_state

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.integration

EXTRA_AGENT_COUNT = 20
# Generous enough for any reasonable future addition to list_agents, but far
# below the O(agent_count) blowup the old N+1 implementation produced.
MAX_EXPECTED_QUERIES = 6


async def _seed_extra_agents(write_db: Any, count: int) -> None:
    for i in range(count):
        await write_db.execute(
            "INSERT INTO identity_agents (agent_id, name, public_key, registered_at) "
            "VALUES (?, ?, ?, ?)",
            (
                f"a-extra-{i}",
                f"Extra Agent {i}",
                f"ed25519:extra-key-{i}",
                "2026-02-01T00:00:00Z",
            ),
        )
    await write_db.commit()


async def test_list_agents_query_count_is_constant_not_per_agent(client, write_db) -> None:
    await _seed_extra_agents(write_db, EXTRA_AGENT_COUNT)

    total_response = await client.get("/api/agents?limit=1000")
    assert total_response.status_code == 200
    total_agents = total_response.json()["total_count"]
    assert total_agents >= EXTRA_AGENT_COUNT + 5, (
        "seeding did not take effect — test setup is broken, not the guard"
    )

    state = get_app_state()
    db = state.db
    assert db is not None

    call_count = 0
    original_execute: Callable[..., Any] = db.execute

    def counting_execute(*args: Any, **kwargs: Any) -> Any:
        nonlocal call_count
        call_count += 1
        return original_execute(*args, **kwargs)

    db.execute = counting_execute  # type: ignore[method-assign]
    try:
        response = await client.get("/api/agents?limit=1000")
    finally:
        db.execute = original_execute  # type: ignore[method-assign]

    assert response.status_code == 200
    assert len(response.json()["agents"]) == total_agents
    assert call_count <= MAX_EXPECTED_QUERIES, (
        f"GET /api/agents made {call_count} db.execute calls for {total_agents} agents "
        f"(expected <= {MAX_EXPECTED_QUERIES}) — looks like the N+1 fan-out is back"
    )
