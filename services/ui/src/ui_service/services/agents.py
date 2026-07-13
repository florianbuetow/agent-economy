"""Agent data business logic."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ui_service.services.database import (
    execute_fetchall,
    execute_fetchone,
    execute_scalar,
)
from ui_service.taxonomy import (
    AGENT_FEED_EVENT_TYPES,
    DEFAULT_EVENT_BADGE,
    EVENT_TYPE_TO_BADGE,
    TaskStatus,
    sql_placeholders,
)

if TYPE_CHECKING:
    from typing import Any

    import aiosqlite


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

    # Spec quality from visible feedback
    spec_es = int(
        await execute_scalar(
            db,
            "SELECT COUNT(*) FROM reputation_feedback "
            "WHERE to_agent_id = ? AND category = 'spec_quality' "
            "AND visible = 1 AND rating = 'extremely_satisfied'",
            (agent_id,),
        )
        or 0
    )
    spec_sat = int(
        await execute_scalar(
            db,
            "SELECT COUNT(*) FROM reputation_feedback "
            "WHERE to_agent_id = ? AND category = 'spec_quality' "
            "AND visible = 1 AND rating = 'satisfied'",
            (agent_id,),
        )
        or 0
    )
    spec_dis = int(
        await execute_scalar(
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
        await execute_scalar(
            db,
            "SELECT COUNT(*) FROM reputation_feedback "
            "WHERE to_agent_id = ? AND category = 'delivery_quality' "
            "AND visible = 1 AND rating = 'extremely_satisfied'",
            (agent_id,),
        )
        or 0
    )
    del_sat = int(
        await execute_scalar(
            db,
            "SELECT COUNT(*) FROM reputation_feedback "
            "WHERE to_agent_id = ? AND category = 'delivery_quality' "
            "AND visible = 1 AND rating = 'satisfied'",
            (agent_id,),
        )
        or 0
    )
    del_dis = int(
        await execute_scalar(
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


def _quality_ratio_sql(es_expr: str, sat_expr: str, dis_expr: str) -> str:
    """Build a SQL CASE expression for extremely_satisfied / total ratio.

    Mirrors ``_quality_sort_key``'s Python-side computation, but as SQL so it
    can be used directly in an ``ORDER BY`` clause (GAP-E10).
    """
    total = f"({es_expr} + {sat_expr} + {dis_expr})"
    return f"CASE WHEN {total} = 0 THEN 0.0 ELSE CAST({es_expr} AS REAL) / {total} END"


# Maps the router's validated sort_by values to a SELECT-list alias in the
# list_agents query below. "0" is a safe no-op fallback (matches the
# pre-GAP-E10 dict.get(sort_by, 0) behavior) — unreachable in practice since
# the router already 400s on an unknown sort_by before calling this.
_SORT_ALIASES: dict[str, str] = {
    "total_earned": "total_earned",
    "total_spent": "total_spent",
    "tasks_completed": "tasks_completed_as_worker",
    "tasks_completed_as_worker": "tasks_completed_as_worker",
    "tasks_posted": "tasks_posted",
    "spec_quality": "spec_quality_ratio",
    "delivery_quality": "delivery_quality_ratio",
}


async def list_agents(
    db: aiosqlite.Connection,
    sort_by: str,
    order: str,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """List agents with computed stats, sorted and paginated.

    Single aggregated query (GAP-E10): one LEFT JOIN per stat category,
    computed and sorted in SQL, instead of an N+1 fan-out (~10 queries per
    agent) followed by a Python-side sort and slice.
    """
    total_count = int(await execute_scalar(db, "SELECT COUNT(*) FROM identity_agents", ()) or 0)

    sort_alias = _SORT_ALIASES.get(sort_by, "0")
    direction = "DESC" if order == "desc" else "ASC"

    spec_es_expr = "COALESCE(sq.spec_es, 0)"
    spec_sat_expr = "COALESCE(sq.spec_sat, 0)"
    spec_dis_expr = "COALESCE(sq.spec_dis, 0)"
    del_es_expr = "COALESCE(dq.del_es, 0)"
    del_sat_expr = "COALESCE(dq.del_sat, 0)"
    del_dis_expr = "COALESCE(dq.del_dis, 0)"

    # nosec B608 -- sort_alias/direction come from the fixed _SORT_ALIASES lookup
    # above, never raw user input; sort_by is also pre-validated by the router's
    # VALID_SORT_FIELDS allow-list before reaching here.
    sql = (
        "SELECT ia.agent_id, ia.name, ia.registered_at, "  # nosec B608
        "COALESCE(tp.tasks_posted, 0) AS tasks_posted, "
        "COALESCE(tc.tasks_completed_as_worker, 0) AS tasks_completed_as_worker, "
        "COALESCE(er.total_earned, 0) AS total_earned, "
        "COALESCE(ts.total_spent, 0) AS total_spent, "
        f"{spec_es_expr} AS spec_es, {spec_sat_expr} AS spec_sat, {spec_dis_expr} AS spec_dis, "
        f"{del_es_expr} AS del_es, {del_sat_expr} AS del_sat, {del_dis_expr} AS del_dis, "
        f"{_quality_ratio_sql(spec_es_expr, spec_sat_expr, spec_dis_expr)} AS spec_quality_ratio, "
        f"{_quality_ratio_sql(del_es_expr, del_sat_expr, del_dis_expr)} AS delivery_quality_ratio "
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
        "LEFT JOIN ("
        "  SELECT to_agent_id AS agent_id, "
        "    SUM(CASE WHEN rating = 'extremely_satisfied' THEN 1 ELSE 0 END) AS spec_es, "
        "    SUM(CASE WHEN rating = 'satisfied' THEN 1 ELSE 0 END) AS spec_sat, "
        "    SUM(CASE WHEN rating = 'dissatisfied' THEN 1 ELSE 0 END) AS spec_dis "
        "  FROM reputation_feedback WHERE category = 'spec_quality' AND visible = 1 "
        "  GROUP BY to_agent_id"
        ") sq ON sq.agent_id = ia.agent_id "
        "LEFT JOIN ("
        "  SELECT to_agent_id AS agent_id, "
        "    SUM(CASE WHEN rating = 'extremely_satisfied' THEN 1 ELSE 0 END) AS del_es, "
        "    SUM(CASE WHEN rating = 'satisfied' THEN 1 ELSE 0 END) AS del_sat, "
        "    SUM(CASE WHEN rating = 'dissatisfied' THEN 1 ELSE 0 END) AS del_dis "
        "  FROM reputation_feedback WHERE category = 'delivery_quality' AND visible = 1 "
        "  GROUP BY to_agent_id"
        ") dq ON dq.agent_id = ia.agent_id "
        f"ORDER BY {sort_alias} {direction}, ia.rowid ASC "
        "LIMIT ? OFFSET ?"
    )

    rows = await execute_fetchall(db, sql, (TaskStatus.APPROVED, limit, offset))

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
                "spec_quality": {
                    "extremely_satisfied": int(row["spec_es"]),
                    "satisfied": int(row["spec_sat"]),
                    "dissatisfied": int(row["spec_dis"]),
                },
                "delivery_quality": {
                    "extremely_satisfied": int(row["del_es"]),
                    "satisfied": int(row["del_sat"]),
                    "dissatisfied": int(row["del_dis"]),
                },
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


async def get_agent_profile(db: aiosqlite.Connection, agent_id: str) -> dict[str, Any] | None:
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
    placeholders = sql_placeholders(AGENT_FEED_EVENT_TYPES)
    conditions = [f"e.event_type IN ({placeholders})"]
    params: list[Any] = list(AGENT_FEED_EVENT_TYPES)

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
        ) = row

        role = _derive_agent_role(agent_id, event_agent_id, poster_id, worker_id)

        # Apply role filter after derivation
        if role_filter == "AS_POSTER" and role != "POSTER":
            continue
        if role_filter == "AS_WORKER" and role != "WORKER":
            continue

        badge = EVENT_TYPE_TO_BADGE.get(event_type, DEFAULT_EVENT_BADGE)

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
