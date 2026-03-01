"""Agent data business logic."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

    import aiosqlite


async def _scalar(db: aiosqlite.Connection, sql: str, params: tuple[Any, ...]) -> Any:
    """Execute query and return the first column of the first row."""
    async with db.execute(sql, params) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return row[0]


async def _fetchone(db: aiosqlite.Connection, sql: str, params: tuple[Any, ...]) -> Any:
    """Execute query and return the first row."""
    async with db.execute(sql, params) as cursor:
        return await cursor.fetchone()


async def _fetchall(db: aiosqlite.Connection, sql: str, params: tuple[Any, ...]) -> list[Any]:
    """Execute query and return all rows."""
    async with db.execute(sql, params) as cursor:
        return list(await cursor.fetchall())


async def _compute_agent_stats(db: aiosqlite.Connection, agent_id: str) -> dict[str, Any]:
    """Compute stats for a single agent."""
    tasks_posted = int(
        await _scalar(
            db,
            "SELECT COUNT(*) FROM board_tasks WHERE poster_id = ?",
            (agent_id,),
        )
        or 0
    )

    tasks_completed_as_worker = int(
        await _scalar(
            db,
            "SELECT COUNT(*) FROM board_tasks WHERE worker_id = ? AND status = 'approved'",
            (agent_id,),
        )
        or 0
    )

    total_earned = int(
        await _scalar(
            db,
            "SELECT COALESCE(SUM(amount), 0) FROM bank_transactions "
            "WHERE account_id = ? AND type = 'escrow_release'",
            (agent_id,),
        )
        or 0
    )

    total_spent = int(
        await _scalar(
            db,
            "SELECT COALESCE(SUM(amount), 0) FROM bank_transactions "
            "WHERE account_id = ? AND type = 'escrow_lock'",
            (agent_id,),
        )
        or 0
    )

    # Spec quality from visible feedback
    spec_es = int(
        await _scalar(
            db,
            "SELECT COUNT(*) FROM reputation_feedback "
            "WHERE to_agent_id = ? AND category = 'spec_quality' "
            "AND visible = 1 AND rating = 'extremely_satisfied'",
            (agent_id,),
        )
        or 0
    )
    spec_sat = int(
        await _scalar(
            db,
            "SELECT COUNT(*) FROM reputation_feedback "
            "WHERE to_agent_id = ? AND category = 'spec_quality' "
            "AND visible = 1 AND rating = 'satisfied'",
            (agent_id,),
        )
        or 0
    )
    spec_dis = int(
        await _scalar(
            db,
            "SELECT COUNT(*) FROM reputation_feedback "
            "WHERE to_agent_id = ? AND category = 'spec_quality' "
            "AND visible = 1 AND rating = 'dissatisfied'",
            (agent_id,),
        )
        or 0
    )

    # Delivery quality from visible feedback
    del_es = int(
        await _scalar(
            db,
            "SELECT COUNT(*) FROM reputation_feedback "
            "WHERE to_agent_id = ? AND category = 'delivery_quality' "
            "AND visible = 1 AND rating = 'extremely_satisfied'",
            (agent_id,),
        )
        or 0
    )
    del_sat = int(
        await _scalar(
            db,
            "SELECT COUNT(*) FROM reputation_feedback "
            "WHERE to_agent_id = ? AND category = 'delivery_quality' "
            "AND visible = 1 AND rating = 'satisfied'",
            (agent_id,),
        )
        or 0
    )
    del_dis = int(
        await _scalar(
            db,
            "SELECT COUNT(*) FROM reputation_feedback "
            "WHERE to_agent_id = ? AND category = 'delivery_quality' "
            "AND visible = 1 AND rating = 'dissatisfied'",
            (agent_id,),
        )
        or 0
    )

    return {
        "tasks_posted": tasks_posted,
        "tasks_completed_as_worker": tasks_completed_as_worker,
        "total_earned": total_earned,
        "total_spent": total_spent,
        "spec_quality": {
            "extremely_satisfied": spec_es,
            "satisfied": spec_sat,
            "dissatisfied": spec_dis,
        },
        "delivery_quality": {
            "extremely_satisfied": del_es,
            "satisfied": del_sat,
            "dissatisfied": del_dis,
        },
    }


def _quality_sort_key(stats: dict[str, Any], category: str) -> float:
    """Compute proportion of extremely_satisfied for sorting."""
    quality = stats[category]
    total: int = quality["extremely_satisfied"] + quality["satisfied"] + quality["dissatisfied"]
    if total == 0:
        return 0.0
    return float(quality["extremely_satisfied"]) / float(total)


def _get_sort_key(stats: dict[str, Any], sort_by: str) -> float | int:
    """Return the value to sort by for a given sort_by field."""
    sort_map: dict[str, float | int] = {
        "total_earned": stats["total_earned"],
        "total_spent": stats["total_spent"],
        "tasks_completed": stats["tasks_completed_as_worker"],
        "tasks_completed_as_worker": stats["tasks_completed_as_worker"],
        "tasks_posted": stats["tasks_posted"],
        "spec_quality": _quality_sort_key(stats, "spec_quality"),
        "delivery_quality": _quality_sort_key(stats, "delivery_quality"),
    }
    return sort_map.get(sort_by, 0)


async def list_agents(
    db: aiosqlite.Connection,
    sort_by: str,
    order: str,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """List agents with computed stats, sorted and paginated."""
    # Get all agents
    rows = await _fetchall(
        db,
        "SELECT agent_id, name, registered_at FROM identity_agents",
        (),
    )

    total_count = len(rows)

    # Compute stats for each agent
    agents: list[dict[str, Any]] = []
    for row in rows:
        agent_id, name, registered_at = row
        stats = await _compute_agent_stats(db, agent_id)
        agents.append(
            {
                "agent_id": agent_id,
                "name": name,
                "registered_at": registered_at,
                "stats": stats,
            }
        )

    # Sort
    reverse = order == "desc"
    agents.sort(key=lambda a: _get_sort_key(a["stats"], sort_by), reverse=reverse)

    # Paginate
    paginated = agents[offset : offset + limit]

    return {
        "agents": paginated,
        "total_count": total_count,
        "limit": limit,
        "offset": offset,
    }


async def get_agent_profile(db: aiosqlite.Connection, agent_id: str) -> dict[str, Any] | None:
    """Get a single agent's full profile."""
    # Check agent exists
    agent_row = await _fetchone(
        db,
        "SELECT agent_id, name, registered_at FROM identity_agents WHERE agent_id = ?",
        (agent_id,),
    )
    if agent_row is None:
        return None

    agent_id_val, name, registered_at = agent_row

    # Balance
    balance = int(
        await _scalar(
            db,
            "SELECT COALESCE(balance, 0) FROM bank_accounts WHERE account_id = ?",
            (agent_id,),
        )
        or 0
    )

    # Stats
    stats = await _compute_agent_stats(db, agent_id)

    # Recent tasks (up to 10, most recent first)
    # Tasks where the agent is poster or worker
    task_rows = await _fetchall(
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
    feedback_rows = await _fetchall(
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
            _from_agent_id,
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


# ---------------------------------------------------------------------------
# Event types included in the agent activity feed (per spec §3)
# ---------------------------------------------------------------------------
_INCLUDED_EVENT_TYPES = {
    "agent.registered",
    "salary.paid",
    "task.created",
    "bid.submitted",
    "task.accepted",
    "asset.uploaded",
    "task.submitted",
    "task.approved",
    "task.auto_approved",
    "task.disputed",
    "task.ruled",
    "task.cancelled",
    "task.expired",
    "escrow.locked",
    "escrow.released",
    "escrow.split",
    "feedback.revealed",
}

# Map event_type -> badge category (same taxonomy as macro feed)
_EVENT_TYPE_TO_BADGE: dict[str, str] = {
    "agent.registered": "SYSTEM",
    "salary.paid": "SYSTEM",
    "task.created": "TASK",
    "bid.submitted": "BID",
    "task.accepted": "TASK",
    "asset.uploaded": "TASK",
    "task.submitted": "TASK",
    "task.approved": "PAYOUT",
    "task.auto_approved": "PAYOUT",
    "task.disputed": "TASK",
    "task.ruled": "TASK",
    "task.cancelled": "TASK",
    "task.expired": "TASK",
    "escrow.locked": "ESCROW",
    "escrow.released": "PAYOUT",
    "escrow.split": "ESCROW",
    "feedback.revealed": "REP",
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
    placeholders = ", ".join("?" for _ in _INCLUDED_EVENT_TYPES)
    conditions = [f"e.event_type IN ({placeholders})"]
    params: list[Any] = list(_INCLUDED_EVENT_TYPES)

    # Agent involvement condition
    conditions.append("(e.agent_id = ? OR t.poster_id = ? OR t.worker_id = ?)")
    params.extend([agent_id, agent_id, agent_id])

    if before is not None:
        conditions.append("e.event_id < ?")
        params.append(before)

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
        "poster_agent.name AS poster_name, worker_agent.name AS worker_name "
        "FROM events e "
        "LEFT JOIN board_tasks t ON e.task_id = t.task_id "
        "LEFT JOIN identity_agents poster_agent ON t.poster_id = poster_agent.agent_id "
        "LEFT JOIN identity_agents worker_agent ON t.worker_id = worker_agent.agent_id "
        f"WHERE {where} "  # nosec B608
        "ORDER BY e.event_id DESC "
        "LIMIT ?"
    )
    params.append(limit + 1)

    rows = await _fetchall(db, sql, tuple(params))

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
        ) = row

        role = _derive_agent_role(agent_id, event_agent_id, poster_id, worker_id)

        # Apply role filter after derivation
        if role_filter == "AS_POSTER" and role != "POSTER":
            continue
        if role_filter == "AS_WORKER" and role != "WORKER":
            continue

        badge = _EVENT_TYPE_TO_BADGE.get(event_type, "SYSTEM")

        # Apply type filter
        if type_filter is not None and badge != type_filter:
            continue

        payload = json.loads(payload_raw) if payload_raw else {}

        events.append(
            {
                "event_id": event_id,
                "event_source": event_source,
                "event_type": event_type,
                "timestamp": timestamp,
                "task_id": task_id,
                "agent_id": event_agent_id,
                "summary": summary,
                "payload": payload,
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
    rows = await _fetchall(
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
        await _scalar(
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
        await _scalar(
            db,
            "SELECT COUNT(*) FROM board_tasks WHERE worker_id = ? AND status = 'approved'",
            (agent_id,),
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
