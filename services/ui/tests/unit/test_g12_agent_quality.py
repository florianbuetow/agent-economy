"""G12 — the UI shows the Reputation service's quality score, not its own SQL.

Agent list and profile quality stats come from ``GET /agents/{id}/quality``
(read through a quality reader). Feedback rows in the read-only database are
deliberately contradictory here, so any local formula would produce other values.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import aiosqlite
import pytest
from service_commons.exceptions import ServiceError

from ui_service.services import agents as agents_service

SCHEMA_PATH = Path(__file__).resolve().parents[4] / "docs" / "specifications" / "schema.sql"
AGENTS = ("a-1", "a-2", "a-3")


def _report(agent_id: str, delivery: int | None, counts: tuple[int, int, int]) -> dict[str, Any]:
    es, sat, dis = counts
    rated = delivery is not None
    return {
        "agent_id": agent_id,
        "formula_version": "quality-v1",
        "specification_quality": {
            "rated": False,
            "score": None,
            "task_count": 0,
            "rating_counts": {"extremely_satisfied": 0, "satisfied": 0, "dissatisfied": 0},
        },
        "delivery_quality": {
            "rated": rated,
            "score": delivery,
            "task_count": es + sat + dis,
            "rating_counts": {"extremely_satisfied": es, "satisfied": sat, "dissatisfied": dis},
        },
    }


class _FakeQualityReader:
    def __init__(self, reports: dict[str, dict[str, Any]]) -> None:
        self.reports = reports
        self.calls: list[str] = []

    async def get_agent_quality(self, agent_id: str) -> dict[str, Any]:
        self.calls.append(agent_id)
        return self.reports[agent_id]


REPORTS = {
    "a-1": _report("a-1", 50, (0, 2, 0)),
    "a-2": _report("a-2", 100, (1, 0, 0)),
    "a-3": _report("a-3", None, (0, 0, 0)),
}


@pytest.fixture
async def db() -> Any:
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.executescript(SCHEMA_PATH.read_text())
    for index, agent_id in enumerate(AGENTS):
        await conn.execute(
            "INSERT INTO identity_agents (agent_id, name, public_key, registered_at) "
            "VALUES (?, ?, ?, ?)",
            (agent_id, agent_id.upper(), f"ed25519:k{index}", f"2026-01-0{index + 1}T00:00:00Z"),
        )
    # Contradictory local rows: a local formula would rank a-3 first.
    await conn.execute(
        "INSERT INTO reputation_feedback (feedback_id, task_id, from_agent_id, to_agent_id, "
        "role, category, rating, comment, submitted_at, visible) "
        "VALUES ('f1', 't1', 'a-1', 'a-3', 'poster', 'delivery_quality', "
        "'extremely_satisfied', NULL, '2026-01-05T00:00:00Z', 1)"
    )
    await conn.commit()
    yield conn
    await conn.close()


@pytest.mark.unit
async def test_list_agents_reads_quality_from_service(db: aiosqlite.Connection) -> None:
    reader = _FakeQualityReader(REPORTS)
    data = await agents_service.list_agents(db, reader, "delivery_quality", "desc", 10, 0)
    ids = [a["agent_id"] for a in data["agents"]]
    # Service score order; unrated a-3 last.
    assert ids == ["a-2", "a-1", "a-3"]
    by_id = {a["agent_id"]: a for a in data["agents"]}
    assert by_id["a-1"]["stats"]["delivery_quality"] == {
        "extremely_satisfied": 0,
        "satisfied": 2,
        "dissatisfied": 0,
    }
    assert by_id["a-3"]["stats"]["delivery_quality"] == {
        "extremely_satisfied": 0,
        "satisfied": 0,
        "dissatisfied": 0,
    }
    assert sorted(reader.calls) == sorted(AGENTS)


@pytest.mark.unit
async def test_list_agents_page_reads_only_page_agents(db: aiosqlite.Connection) -> None:
    reader = _FakeQualityReader(REPORTS)
    data = await agents_service.list_agents(db, reader, "total_earned", "desc", 1, 1)
    assert [a["agent_id"] for a in data["agents"]] == ["a-2"]
    assert reader.calls == ["a-2"]


@pytest.mark.unit
async def test_profile_reads_quality_from_service(db: aiosqlite.Connection) -> None:
    reader = _FakeQualityReader(REPORTS)
    profile = await agents_service.get_agent_profile(db, reader, "a-3")
    assert profile is not None
    assert profile["stats"]["delivery_quality"] == {
        "extremely_satisfied": 0,
        "satisfied": 0,
        "dissatisfied": 0,
    }
    assert reader.calls == ["a-3"]


@pytest.mark.unit
async def test_malformed_service_report_is_an_error(db: aiosqlite.Connection) -> None:
    reader = _FakeQualityReader({"a-1": {"formula_version": "quality-v1"}})
    with pytest.raises(ServiceError) as excinfo:
        await agents_service.get_agent_profile(db, reader, "a-1")
    assert excinfo.value.status_code == 502


class _FakeUserAgent:
    class config:  # noqa: N801 - mirrors the SDK attribute name
        reputation_url = "http://reputation.test"

    def __init__(self) -> None:
        self.urls: list[str] = []

    async def _request(self, method: str, url: str) -> dict[str, Any]:
        assert method == "GET"
        self.urls.append(url)
        return REPORTS["a-1"]


@pytest.mark.unit
async def test_sdk_reader_calls_the_reputation_endpoint() -> None:
    user_agent = _FakeUserAgent()
    reader = agents_service.UserAgentQualityReader(user_agent)  # type: ignore[arg-type]
    assert await reader.get_agent_quality("a-1") == REPORTS["a-1"]
    assert user_agent.urls == ["http://reputation.test/agents/a-1/quality"]
