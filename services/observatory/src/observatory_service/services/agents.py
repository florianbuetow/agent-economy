"""Agent data business logic."""

from __future__ import annotations

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
