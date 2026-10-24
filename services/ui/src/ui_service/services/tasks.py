"""Task data business logic."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ui_service.services.database import (
    execute_fetchall,
    execute_fetchone,
    execute_scalar,
)
from ui_service.services.task_board_reads import (
    get_authoritative_task,
    list_authoritative_tasks,
)
from ui_service.taxonomy import (
    COMPETITIVE_OPEN_STATUSES,
    VALID_TASK_STATUSES,
    TaskStatus,
    sql_placeholders,
)

if TYPE_CHECKING:
    from typing import Any

    import aiosqlite

    from ui_service.services.task_board_reads import TaskBoardReader

# Re-exported from ``ui_service.taxonomy`` so existing consumers (the tasks
# router) keep importing it from this module.
__all__ = ["VALID_TASK_STATUSES"]


async def _delivery_quality(db: aiosqlite.Connection, agent_id: str) -> dict[str, Any]:
    """Compute delivery quality counts for a bidder from visible feedback."""
    es = int(
        await execute_scalar(
            db,
            "SELECT COUNT(*) FROM reputation_feedback "
            "WHERE to_agent_id = ? AND category = 'delivery_quality' "
            "AND visible = 1 AND rating = 'extremely_satisfied'",
            (agent_id,),
        )
        or 0
    )
    sat = int(
        await execute_scalar(
            db,
            "SELECT COUNT(*) FROM reputation_feedback "
            "WHERE to_agent_id = ? AND category = 'delivery_quality' "
            "AND visible = 1 AND rating = 'satisfied'",
            (agent_id,),
        )
        or 0
    )
    dis = int(
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
        "extremely_satisfied": es,
        "satisfied": sat,
        "dissatisfied": dis,
    }


async def _resolve_agent_name(db: aiosqlite.Connection, agent_id: str) -> Any:
    """Resolve an agent's display name by id."""
    return await execute_scalar(
        db,
        "SELECT name FROM identity_agents WHERE agent_id = ?",
        (agent_id,),
    )


async def _load_worker(db: aiosqlite.Connection, worker_id: str | None) -> dict[str, Any] | None:
    """Resolve the worker reference for a task, if one is assigned."""
    if worker_id is None:
        return None
    worker_name = await _resolve_agent_name(db, worker_id)
    return {"agent_id": worker_id, "name": worker_name}


async def _load_bids(
    db: aiosqlite.Connection, task_id: str, accepted_bid_id: Any
) -> list[dict[str, Any]]:
    """Load all bids for a task with bidder delivery-quality counts."""
    bid_rows = await execute_fetchall(
        db,
        "SELECT bb.bid_id, bb.bidder_id, ia.name, bb.amount, bb.submitted_at "
        "FROM board_bids bb "
        "JOIN identity_agents ia ON ia.agent_id = bb.bidder_id "
        "WHERE bb.task_id = ? "
        "ORDER BY bb.submitted_at ASC",
        (task_id,),
    )

    bids = []
    for br in bid_rows:
        bid_id, bidder_id, bidder_name, amount, bid_submitted_at = br
        dq = await _delivery_quality(db, bidder_id)
        bids.append(
            {
                "bid_id": bid_id,
                "bidder": {
                    "agent_id": bidder_id,
                    "name": bidder_name,
                    "delivery_quality": dq,
                },
                "amount": int(amount),
                "submitted_at": bid_submitted_at,
                "accepted": bid_id == accepted_bid_id,
            }
        )
    return bids


async def enrich_authorized_bids(
    db: aiosqlite.Connection,
    bids: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach public identity and visible delivery quality to authorized bids."""
    enriched: list[dict[str, Any]] = []
    for bid in bids:
        bidder_id = str(bid["bidder_id"])
        bidder_name = await _resolve_agent_name(db, bidder_id)
        if not isinstance(bidder_name, str):
            raise ValueError(f"Bidder '{bidder_id}' has no public identity projection")
        enriched.append(
            {
                "bid_id": str(bid["bid_id"]),
                "bidder": {
                    "agent_id": bidder_id,
                    "name": bidder_name,
                    "delivery_quality": await _delivery_quality(db, bidder_id),
                },
                "amount": int(bid["amount"]),
                "submitted_at": str(bid["submitted_at"]),
                "accepted": False,
            }
        )
    return enriched


async def _load_assets(db: aiosqlite.Connection, task_id: str) -> list[dict[str, Any]]:
    """Load uploaded deliverable assets for a task."""
    asset_rows = await execute_fetchall(
        db,
        "SELECT asset_id, filename, content_type, size_bytes, uploaded_at "
        "FROM board_assets WHERE task_id = ? "
        "ORDER BY uploaded_at ASC",
        (task_id,),
    )
    return [
        {
            "asset_id": ar[0],
            "filename": ar[1],
            "content_type": ar[2],
            "size_bytes": ar[3],
            "uploaded_at": ar[4],
        }
        for ar in asset_rows
    ]


async def _load_feedback(db: aiosqlite.Connection, task_id: str) -> list[dict[str, Any]]:
    """Load visible mutual feedback for a task."""
    fb_rows = await execute_fetchall(
        db,
        "SELECT rf.feedback_id, ia_from.name, ia_to.name, "
        "rf.category, rf.rating, rf.comment, rf.visible "
        "FROM reputation_feedback rf "
        "JOIN identity_agents ia_from ON ia_from.agent_id = rf.from_agent_id "
        "JOIN identity_agents ia_to ON ia_to.agent_id = rf.to_agent_id "
        "WHERE rf.task_id = ? AND rf.visible = 1 "
        "ORDER BY rf.submitted_at ASC",
        (task_id,),
    )
    return [
        {
            "feedback_id": fb[0],
            "from_agent_name": fb[1],
            "to_agent_name": fb[2],
            "category": fb[3],
            "rating": fb[4],
            "comment": fb[5],
            "visible": bool(fb[6]),
        }
        for fb in fb_rows
    ]


async def _load_dispute(
    db: aiosqlite.Connection,
    task_id: str,
    *,
    ruling_id: str | None,
    worker_pct: int | None,
    ruling_summary: str | None,
    ruled_at: str | None,
) -> dict[str, Any] | None:
    """Enrich authoritative ruling facts with local claim and rebuttal narrative."""
    claim_row = await execute_fetchone(
        db,
        "SELECT claim_id, reason, filed_at FROM court_claims WHERE task_id = ?",
        (task_id,),
    )
    if claim_row is None:
        return None

    claim_id, reason, filed_at = claim_row

    # Rebuttal
    rebuttal = None
    reb_row = await execute_fetchone(
        db,
        "SELECT content, submitted_at FROM court_rebuttals WHERE claim_id = ?",
        (claim_id,),
    )
    if reb_row is not None:
        rebuttal = {
            "content": reb_row[0],
            "submitted_at": reb_row[1],
        }

    ruling = None
    if ruling_id is not None:
        if worker_pct is None or ruling_summary is None or ruled_at is None:
            raise ValueError("Authoritative ruling projection is incomplete")
        ruling = {
            "ruling_id": ruling_id,
            "worker_pct": worker_pct,
            "summary": ruling_summary,
            "ruled_at": ruled_at,
        }

    return {
        "claim_id": claim_id,
        "reason": reason,
        "filed_at": filed_at,
        "rebuttal": rebuttal,
        "ruling": ruling,
    }


async def get_task_drilldown(
    db: aiosqlite.Connection,
    reader: TaskBoardReader,
    task_id: str,
) -> dict[str, Any]:
    """Enrich one authoritative Task Board snapshot with permitted local data."""
    task = await get_authoritative_task(reader, task_id=task_id)
    poster_name = await _resolve_agent_name(db, task.poster_id)
    worker = await _load_worker(db, task.worker_id)
    bids = (
        []
        if task.status == TaskStatus.OPEN
        else await _load_bids(db, task_id, task.accepted_bid_id)
    )
    assets = await _load_assets(db, task_id)
    feedback = await _load_feedback(db, task_id)
    dispute = await _load_dispute(
        db,
        task_id,
        ruling_id=task.ruling_id,
        worker_pct=task.worker_pct,
        ruling_summary=task.ruling_summary,
        ruled_at=task.ruled_at,
    )

    return {
        "task_id": task.task_id,
        "poster": {"agent_id": task.poster_id, "name": poster_name},
        "worker": worker,
        "title": task.title,
        "spec": task.spec,
        "reward": task.reward,
        "status": task.status,
        "lifecycle_version": task.lifecycle_version,
        "bid_count": task.bid_count,
        "deadlines": {
            "bidding_deadline": task.bidding_deadline,
            "execution_deadline": task.execution_deadline,
            "review_deadline": task.review_deadline,
        },
        "timestamps": {
            "created_at": task.created_at,
            "accepted_at": task.accepted_at,
            "submitted_at": task.submitted_at,
            "approved_at": task.approved_at,
        },
        "bids": bids,
        "assets": assets,
        "feedback": feedback,
        "dispute": dispute,
        "financial_effect": task.financial_effect.model_dump(),
    }


async def get_competitive_tasks(
    db: aiosqlite.Connection,
    limit: int,
    status: str,
) -> list[dict[str, Any]]:
    """Return tasks sorted by bid count descending."""
    if status == "open":
        sql = (
            "SELECT bt.task_id, bt.title, bt.reward, bt.status, "
            "COUNT(bb.bid_id) as bid_count, "
            "ia.name as poster_name, bt.poster_id, "
            "bt.created_at, bt.bidding_deadline "
            "FROM board_tasks bt "
            "LEFT JOIN board_bids bb ON bt.task_id = bb.task_id "
            "JOIN identity_agents ia ON bt.poster_id = ia.agent_id "
            f"WHERE bt.status IN ({sql_placeholders(COMPETITIVE_OPEN_STATUSES)}) "  # nosec B608
            "GROUP BY bt.task_id "
            "HAVING COUNT(bb.bid_id) > 0 "
            "ORDER BY COUNT(bb.bid_id) DESC "
            "LIMIT ?"
        )
        rows = await execute_fetchall(db, sql, (*COMPETITIVE_OPEN_STATUSES, limit))
    else:
        sql = (
            "SELECT bt.task_id, bt.title, bt.reward, bt.status, "
            "COUNT(bb.bid_id) as bid_count, "
            "ia.name as poster_name, bt.poster_id, "
            "bt.created_at, bt.bidding_deadline "
            "FROM board_tasks bt "
            "LEFT JOIN board_bids bb ON bt.task_id = bb.task_id "
            "JOIN identity_agents ia ON bt.poster_id = ia.agent_id "
            "GROUP BY bt.task_id "
            "HAVING COUNT(bb.bid_id) > 0 "
            "ORDER BY COUNT(bb.bid_id) DESC "
            "LIMIT ?"
        )
        rows = await execute_fetchall(db, sql, (limit,))

    return [
        {
            "task_id": r[0],
            "title": r[1],
            "reward": r[2],
            "status": r[3],
            "bid_count": r[4],
            "poster": {"agent_id": r[6], "name": r[5]},
            "created_at": r[7],
            "bidding_deadline": r[8],
        }
        for r in rows
    ]


async def get_uncontested_tasks(
    db: aiosqlite.Connection,
    min_age_minutes: int,
    limit: int,
) -> list[dict[str, Any]]:
    """Return open tasks with zero bids older than min_age_minutes."""
    sql = (
        "SELECT bt.task_id, bt.title, bt.reward, "
        "ia.name as poster_name, bt.poster_id, "
        "bt.created_at, bt.bidding_deadline, "
        "(julianday('now') - julianday(bt.created_at)) * 1440 as minutes_without_bids "
        "FROM board_tasks bt "
        "JOIN identity_agents ia ON bt.poster_id = ia.agent_id "
        "LEFT JOIN board_bids bb ON bt.task_id = bb.task_id "
        "WHERE bt.status = ? "
        "AND bb.bid_id IS NULL "
        "AND (julianday('now') - julianday(bt.created_at)) * 1440 >= ? "
        "ORDER BY bt.created_at ASC "
        "LIMIT ?"
    )
    rows = await execute_fetchall(db, sql, (TaskStatus.OPEN, min_age_minutes, limit))

    return [
        {
            "task_id": r[0],
            "title": r[1],
            "reward": r[2],
            "poster": {"agent_id": r[4], "name": r[3]},
            "created_at": r[5],
            "bidding_deadline": r[6],
            "minutes_without_bids": r[7],
        }
        for r in rows
    ]


async def get_task_list(
    reader: TaskBoardReader,
    status: str | None,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    """Slice one coherent, ordered Task Board list snapshot."""
    tasks = await list_authoritative_tasks(reader, status=status)
    return tasks[offset : offset + limit], len(tasks)
