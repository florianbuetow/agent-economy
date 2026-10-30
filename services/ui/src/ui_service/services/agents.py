"""Agent data business logic."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Protocol

from service_commons.exceptions import ServiceError

from ui_service.services.database import (
    execute_fetchall,
    execute_fetchone,
    execute_scalar,
)
from ui_service.services.public_events import (
    BID_SUBMITTED,
    PUBLIC_BID_STATUSES,
    project_public_event,
)
from ui_service.taxonomy import (
    AGENT_FEED_EVENT_TYPES,
    DEFAULT_EVENT_BADGE,
    EVENT_TYPE_TO_BADGE,
    TaskStatus,
    sql_placeholders,
)

if TYPE_CHECKING:
    import aiosqlite
    import httpx
    from service_auth import UserAgent


QUALITY_FORMULA_VERSION = "quality-v1"
_RATINGS = ("extremely_satisfied", "satisfied", "dissatisfied")
_DIMENSIONS = {"spec_quality": "specification_quality", "delivery_quality": "delivery_quality"}


class QualityReader(Protocol):
    """Reads the Reputation service's ``GET /agents/{id}/quality`` report (G12)."""

    async def get_agent_quality(self, agent_id: str) -> dict[str, Any]: ...


class ReputationQualityReader:
    """Public, unsigned quality reads from the operator SDK's Reputation URL."""

    def __init__(self, user_agent: UserAgent, client: httpx.AsyncClient) -> None:
        self._user_agent = user_agent
        self._client = client

    async def get_agent_quality(self, agent_id: str) -> dict[str, Any]:
        base_url = self._user_agent.config.reputation_url
        response = await self._client.get(f"{base_url}/agents/{agent_id}/quality")
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        return body


def _quality_unavailable(agent_id: str) -> ServiceError:
    return ServiceError(
        error="reputation_unavailable",
        message="Reputation quality report is unavailable",
        status_code=502,
        details={"agent_id": agent_id},
    )


def _quality_stats(agent_id: str, report: object) -> dict[str, Any]:
    """Map a service quality report to the UI's per-dimension rating counts.

    Also returns each dimension's service score (None when unrated) under
    ``_scores`` for sorting; it is not part of the response shape.
    """
    if not isinstance(report, dict) or report.get("formula_version") != QUALITY_FORMULA_VERSION:
        raise _quality_unavailable(agent_id)
    stats: dict[str, Any] = {"_scores": {}}
    for ui_name, service_name in _DIMENSIONS.items():
        dimension = report.get(service_name)
        counts = dimension.get("rating_counts") if isinstance(dimension, dict) else None
        if not isinstance(dimension, dict) or not isinstance(counts, dict):
            raise _quality_unavailable(agent_id)
        values = {rating: counts.get(rating) for rating in _RATINGS}
        score = dimension.get("score")
        if any(type(v) is not int for v in values.values()) or not (
            score is None or type(score) is int
        ):
            raise _quality_unavailable(agent_id)
        stats[ui_name] = values
        stats["_scores"][ui_name] = score
    return stats


async def _read_quality(reader: QualityReader, agent_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Fetch and validate quality reports for ``agent_ids`` concurrently."""

    async def one(agent_id: str) -> dict[str, Any]:
        try:
            report = await reader.get_agent_quality(agent_id)
        except ServiceError:
            raise
        except Exception as exc:
            raise _quality_unavailable(agent_id) from exc
        return _quality_stats(agent_id, report)

    results = await asyncio.gather(*(one(agent_id) for agent_id in agent_ids))
    return dict(zip(agent_ids, results, strict=True))


async def _compute_agent_stats(db: aiosqlite.Connection, agent_id: str) -> dict[str, Any]:
    """Compute stats for a single agent."""
    tasks_posted = int(
        await execute_scalar(
            db,
            "SELECT COUNT(*) FROM board_tasks WHERE poster_id = ?",
            (agent_id,),
        )
        or 0
    )

    tasks_completed_as_worker = int(
        await execute_scalar(
            db,
            "SELECT COUNT(*) FROM board_tasks WHERE worker_id = ? AND status = ?",
            (agent_id, TaskStatus.APPROVED),
        )
        or 0
    )

    total_earned = int(
        await execute_scalar(
            db,
            "SELECT COALESCE(SUM(amount), 0) FROM bank_transactions "
            "WHERE account_id = ? AND type = 'escrow_release'",
            (agent_id,),
        )
        or 0
    )

    total_spent = int(
        await execute_scalar(
            db,
            "SELECT COALESCE(SUM(amount), 0) FROM bank_transactions "
            "WHERE account_id = ? AND type = 'escrow_lock'",
            (agent_id,),
        )
        or 0
    )

    return {
        "tasks_posted": tasks_posted,
        "tasks_completed_as_worker": tasks_completed_as_worker,
        "total_earned": total_earned,
        "total_spent": total_spent,
    }


# Maps the router's validated sort_by values to a SELECT-list alias in the
# list_agents query below. The "0" fallback is unreachable: the router already
# 400s on an unknown sort_by before calling this.
# Quality sorts use the Reputation service score instead (G12).
_SORT_ALIASES: dict[str, str] = {
    "total_earned": "total_earned",
    "total_spent": "total_spent",
    "tasks_completed": "tasks_completed_as_worker",
    "tasks_completed_as_worker": "tasks_completed_as_worker",
    "tasks_posted": "tasks_posted",
}
_QUALITY_SORTS = frozenset({"spec_quality", "delivery_quality"})
# Unrated agents sort below every rated agent (scores are 0-100).
_UNRATED_SORT_SCORE = -1


async def list_agents(
    db: aiosqlite.Connection,
    quality_reader: QualityReader,
    sort_by: str,
    order: str,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """List agents with computed stats, sorted and paginated.

    Non-quality stats come from one aggregated query (GAP-E10). Quality stats
    come from the Reputation service (G12): only the page's agents are read,
    except for a quality sort, which needs every agent's service score.
    """
    total_count = int(await execute_scalar(db, "SELECT COUNT(*) FROM identity_agents", ()) or 0)

    quality_sort = sort_by in _QUALITY_SORTS
    direction = "DESC" if order == "desc" else "ASC"
    order_clause = (
        "ia.rowid ASC"
        if quality_sort
        else f"{_SORT_ALIASES.get(sort_by, '0')} {direction}, ia.rowid ASC"
    )
    page_clause = "" if quality_sort else "LIMIT ? OFFSET ?"

    # nosec B608 -- sort_alias/direction come from the fixed _SORT_ALIASES lookup
    # above, never raw user input; sort_by is also pre-validated by the router's
    # VALID_SORT_FIELDS allow-list before reaching here.
    sql = (
        "SELECT ia.agent_id, ia.name, ia.registered_at, "  # nosec B608
        "COALESCE(tp.tasks_posted, 0) AS tasks_posted, "
        "COALESCE(tc.tasks_completed_as_worker, 0) AS tasks_completed_as_worker, "
        "COALESCE(er.total_earned, 0) AS total_earned, "
        "COALESCE(ts.total_spent, 0) AS total_spent "
        "FROM identity_agents ia "
        "LEFT JOIN ("
        "  SELECT poster_id AS agent_id, COUNT(*) AS tasks_posted "
        "  FROM board_tasks GROUP BY poster_id"
        ") tp ON tp.agent_id = ia.agent_id "
        "LEFT JOIN ("
        "  SELECT worker_id AS agent_id, COUNT(*) AS tasks_completed_as_worker "
        "  FROM board_tasks WHERE status = ? GROUP BY worker_id"
        ") tc ON tc.agent_id = ia.agent_id "
        "LEFT JOIN ("
        "  SELECT account_id AS agent_id, SUM(amount) AS total_earned "
        "  FROM bank_transactions WHERE type = 'escrow_release' GROUP BY account_id"
        ") er ON er.agent_id = ia.agent_id "
        "LEFT JOIN ("
        "  SELECT account_id AS agent_id, SUM(amount) AS total_spent "
        "  FROM bank_transactions WHERE type = 'escrow_lock' GROUP BY account_id"
        ") ts ON ts.agent_id = ia.agent_id "
        f"ORDER BY {order_clause} "
        f"{page_clause}"
    )
    params: tuple[Any, ...] = (
        (TaskStatus.APPROVED,) if quality_sort else (TaskStatus.APPROVED, limit, offset)
    )
    rows = list(await execute_fetchall(db, sql, params))
    quality = await _read_quality(quality_reader, [str(row["agent_id"]) for row in rows])

    if quality_sort:

        def score_key(row: Any) -> int:
            score = quality[str(row["agent_id"])]["_scores"][sort_by]
            return _UNRATED_SORT_SCORE if score is None else int(score)

        rows = sorted(rows, key=score_key, reverse=order == "desc")[offset : offset + limit]

    agents: list[dict[str, Any]] = [
        {
            "agent_id": row["agent_id"],
            "name": row["name"],
            "registered_at": row["registered_at"],
            "stats": {
                "tasks_posted": int(row["tasks_posted"]),
                "tasks_completed_as_worker": int(row["tasks_completed_as_worker"]),
                "total_earned": int(row["total_earned"]),
                "total_spent": int(row["total_spent"]),
                "spec_quality": quality[str(row["agent_id"])]["spec_quality"],
                "delivery_quality": quality[str(row["agent_id"])]["delivery_quality"],
            },
        }
        for row in rows
    ]

    return {
        "agents": agents,
        "total_count": total_count,
        "limit": limit,
        "offset": offset,
    }


async def get_agent_profile(
    db: aiosqlite.Connection,
    quality_reader: QualityReader,
    agent_id: str,
) -> dict[str, Any] | None:
    """Get a single agent's full profile."""
    # Check agent exists
    agent_row = await execute_fetchone(
        db,
        "SELECT agent_id, name, registered_at FROM identity_agents WHERE agent_id = ?",
        (agent_id,),
    )
    if agent_row is None:
        return None

    agent_id_val, name, registered_at = agent_row

    # Balance
    balance = int(
        await execute_scalar(
            db,
            "SELECT COALESCE(balance, 0) FROM bank_accounts WHERE account_id = ?",
            (agent_id,),
        )
        or 0
    )

    # Stats
    stats = await _compute_agent_stats(db, agent_id)
    quality = (await _read_quality(quality_reader, [agent_id]))[agent_id]
    stats["spec_quality"] = quality["spec_quality"]
    stats["delivery_quality"] = quality["delivery_quality"]

    # Recent tasks (up to 10, most recent first)
    # Tasks where the agent is poster or worker
    task_rows = await execute_fetchall(
        db,
        "SELECT task_id, title, poster_id, worker_id, status, reward, "
        "approved_at, ruled_at, created_at "
        "FROM board_tasks "
        "WHERE poster_id = ? OR worker_id = ? "
        "ORDER BY created_at DESC "
        "LIMIT 10",
        (agent_id, agent_id),
    )

    recent_tasks = []
    for t in task_rows:
        (
            task_id_val,
            title,
            poster_id,
            _worker_id,
            status,
            reward,
            approved_at,
            ruled_at,
            _created_at,
        ) = t
        role = "poster" if poster_id == agent_id else "worker"
        completed_at = approved_at or ruled_at
        recent_tasks.append(
            {
                "task_id": task_id_val,
                "title": title,
                "role": role,
                "status": status,
                "reward": reward,
                "completed_at": completed_at,
            }
        )

    # Recent feedback (up to 10, most recent, visible only)
    feedback_rows = await execute_fetchall(
        db,
        "SELECT rf.feedback_id, rf.task_id, rf.from_agent_id, "
        "ia.name, rf.category, rf.rating, rf.comment, rf.submitted_at "
        "FROM reputation_feedback rf "
        "JOIN identity_agents ia ON ia.agent_id = rf.from_agent_id "
        "WHERE rf.to_agent_id = ? AND rf.visible = 1 "
        "ORDER BY rf.submitted_at DESC "
        "LIMIT 10",
        (agent_id,),
    )

    recent_feedback = []
    for fb in feedback_rows:
        (
            feedback_id,
            task_id_val,
            from_agent_id,
            from_agent_name,
            category,
            rating,
            comment,
            submitted_at,
        ) = fb
        recent_feedback.append(
            {
                "feedback_id": feedback_id,
                "task_id": task_id_val,
                "from_agent_id": from_agent_id,
                "from_agent_name": from_agent_name,
                "category": category,
                "rating": rating,
                "comment": comment,
                "submitted_at": submitted_at,
            }
        )

    return {
        "agent_id": agent_id_val,
        "name": name,
        "registered_at": registered_at,
        "balance": balance,
        "stats": stats,
        "recent_tasks": recent_tasks,
        "recent_feedback": recent_feedback,
    }


def _derive_agent_role(
    agent_id: str,
    event_agent_id: str | None,
    poster_id: str | None,
    worker_id: str | None,
) -> str | None:
    """Derive the agent's role in this event."""
    if event_agent_id == agent_id:
        if poster_id == agent_id:
            return "POSTER"
        if worker_id == agent_id:
            return "WORKER"
        return None
    if poster_id == agent_id:
        return "POSTER"
    if worker_id == agent_id:
        return "WORKER"
    return None


# Feed filters finer than the backend badge taxonomy: disputes and rulings
# share the TASK badge but can be filtered on their own.
_FEED_FINE_TYPES: dict[str, tuple[str, ...]] = {
    "DISPUTE": ("task.disputed",),
    "RULING": ("task.ruled",),
}


async def get_agent_feed(
    db: aiosqlite.Connection,
    agent_id: str,
    limit: int,
    before: int | None,
    role_filter: str | None,
    type_filter: str | None,
    time_filter: str | None,
) -> tuple[list[dict[str, Any]], bool]:
    """Get agent-scoped activity feed with agent-centric framing.

    Returns (events, has_more).
    """
    # Build the base query per spec §2: join events with board_tasks
    # to find events where agent is actor, poster, or worker.
    placeholders = sql_placeholders(AGENT_FEED_EVENT_TYPES)
    conditions = [f"e.event_type IN ({placeholders})"]
    params: list[Any] = list(AGENT_FEED_EVENT_TYPES)

    # Open-task bid membership is sealed. Requiring a known post-open status
    # also fails closed when an event references a missing task.
    conditions.append(
        f"(e.event_type != ? OR t.status IN ({sql_placeholders(PUBLIC_BID_STATUSES)}))"
    )
    params.extend((BID_SUBMITTED, *PUBLIC_BID_STATUSES))

    # Agent involvement condition
    conditions.append("(e.agent_id = ? OR t.poster_id = ? OR t.worker_id = ?)")
    params.extend([agent_id, agent_id, agent_id])

    if before is not None:
        conditions.append("e.event_id < ?")
        params.append(before)

    if role_filter == "AS_POSTER":
        conditions.append("t.poster_id = ?")
        params.append(agent_id)
    elif role_filter == "AS_WORKER":
        conditions.append("t.worker_id = ?")
        params.append(agent_id)

    if type_filter is not None:
        matching_event_types = _FEED_FINE_TYPES.get(type_filter) or tuple(
            event_type for event_type, badge in EVENT_TYPE_TO_BADGE.items() if badge == type_filter
        )
        if matching_event_types:
            conditions.append(f"e.event_type IN ({sql_placeholders(matching_event_types)})")
            params.extend(matching_event_types)
        else:
            conditions.append("0 = 1")

    if time_filter == "LAST_7D":
        conditions.append("e.timestamp >= datetime('now', '-7 days')")
    elif time_filter == "LAST_30D":
        conditions.append("e.timestamp >= datetime('now', '-30 days')")

    where = " AND ".join(conditions)

    # Fetch limit + 1 for has_more pagination
    sql = (
        "SELECT DISTINCT e.event_id, e.event_source, e.event_type, "
        "e.timestamp, e.task_id, e.agent_id, e.summary, e.payload, "
        "t.poster_id, t.worker_id, t.title AS task_title, t.reward AS task_reward, "
        "poster_agent.name AS poster_name, worker_agent.name AS worker_name, "
        "t.status AS task_status "
        "FROM events e "
        "LEFT JOIN board_tasks t ON e.task_id = t.task_id "
        "LEFT JOIN identity_agents poster_agent ON t.poster_id = poster_agent.agent_id "
        "LEFT JOIN identity_agents worker_agent ON t.worker_id = worker_agent.agent_id "
        f"WHERE {where} "  # nosec B608
        "ORDER BY e.event_id DESC "
        "LIMIT ?"
    )
    params.append(limit + 1)

    rows = await execute_fetchall(db, sql, tuple(params))

    has_more = len(rows) > limit
    rows = rows[:limit]

    events: list[dict[str, Any]] = []
    for row in rows:
        (
            event_id,
            event_source,
            event_type,
            timestamp,
            task_id,
            event_agent_id,
            summary,
            payload_raw,
            poster_id,
            worker_id,
            task_title,
            task_reward,
            poster_name,
            worker_name,
            task_status,
        ) = row

        role = _derive_agent_role(agent_id, event_agent_id, poster_id, worker_id)

        badge = EVENT_TYPE_TO_BADGE.get(event_type, DEFAULT_EVENT_BADGE)
        public_event = project_public_event(
            {
                "event_id": event_id,
                "event_source": event_source,
                "event_type": event_type,
                "timestamp": timestamp,
                "task_id": task_id,
                "agent_id": event_agent_id,
                "summary": summary,
                "payload": payload_raw,
                "task_status": task_status,
            }
        )

        events.append(
            {
                **public_event,
                "badge": badge,
                "role": role,
                "task_title": task_title,
                "task_reward": task_reward,
                "poster_id": poster_id,
                "worker_id": worker_id,
                "poster_name": poster_name,
                "worker_name": worker_name,
            }
        )

    return events, has_more


async def get_agent_earnings(
    db: aiosqlite.Connection,
    agent_id: str,
) -> dict[str, Any]:
    """Get cumulative earnings over time for an agent.

    Queries bank_transactions for escrow_release events only.
    """
    rows = await execute_fetchall(
        db,
        "SELECT timestamp, amount FROM bank_transactions "
        "WHERE account_id = ? AND type = 'escrow_release' "
        "ORDER BY timestamp ASC",
        (agent_id,),
    )

    data_points: list[dict[str, Any]] = []
    cumulative = 0
    for row in rows:
        timestamp, amount = row
        cumulative += int(amount)
        data_points.append(
            {
                "timestamp": timestamp,
                "cumulative": cumulative,
            }
        )

    # Last 7 days earnings
    last_7d_earned = int(
        await execute_scalar(
            db,
            "SELECT COALESCE(SUM(amount), 0) FROM bank_transactions "
            "WHERE account_id = ? AND type = 'escrow_release' "
            "AND timestamp >= datetime('now', '-7 days')",
            (agent_id,),
        )
        or 0
    )

    # Count of approved tasks as worker (for avg per task)
    tasks_approved = int(
        await execute_scalar(
            db,
            "SELECT COUNT(*) FROM board_tasks WHERE worker_id = ? AND status = ?",
            (agent_id, TaskStatus.APPROVED),
        )
        or 0
    )

    total_earned = cumulative
    avg_per_task = round(total_earned / tasks_approved) if tasks_approved > 0 else 0

    return {
        "data_points": data_points,
        "total_earned": total_earned,
        "last_7d_earned": last_7d_earned,
        "avg_per_task": avg_per_task,
        "tasks_approved": tasks_approved,
    }
