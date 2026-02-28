"""Task data business logic."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

    import aiosqlite


async def _scalar(db: aiosqlite.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    """Execute query and return the first column of the first row."""
    async with db.execute(sql, params) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return row[0]


async def _fetchone(db: aiosqlite.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    """Execute query and return the first row."""
    async with db.execute(sql, params) as cursor:
        return await cursor.fetchone()


async def _fetchall(db: aiosqlite.Connection, sql: str, params: tuple[Any, ...] = ()) -> list:
    """Execute query and return all rows."""
    async with db.execute(sql, params) as cursor:
        return await cursor.fetchall()


async def _delivery_quality(db: aiosqlite.Connection, agent_id: str) -> dict:
    """Compute delivery quality counts for a bidder from visible feedback."""
    es = int(
        await _scalar(
            db,
            "SELECT COUNT(*) FROM reputation_feedback "
            "WHERE to_agent_id = ? AND category = 'delivery_quality' "
            "AND visible = 1 AND rating = 'extremely_satisfied'",
            (agent_id,),
        )
        or 0
    )
    sat = int(
        await _scalar(
            db,
            "SELECT COUNT(*) FROM reputation_feedback "
            "WHERE to_agent_id = ? AND category = 'delivery_quality' "
            "AND visible = 1 AND rating = 'satisfied'",
            (agent_id,),
        )
        or 0
    )
    dis = int(
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
        "extremely_satisfied": es,
        "satisfied": sat,
        "dissatisfied": dis,
    }


async def get_task_drilldown(db: aiosqlite.Connection, task_id: str) -> dict | None:
    """Get a full task drilldown with bids, assets, feedback, and dispute."""
    # 1. Query the task
    task_row = await _fetchone(
        db,
        "SELECT bt.task_id, bt.poster_id, bt.worker_id, bt.title, bt.spec, "
        "bt.reward, bt.status, bt.accepted_bid_id, "
        "bt.bidding_deadline, bt.execution_deadline, bt.review_deadline, "
        "bt.created_at, bt.accepted_at, bt.submitted_at, bt.approved_at "
        "FROM board_tasks bt "
        "WHERE bt.task_id = ?",
        (task_id,),
    )
    if task_row is None:
        return None

    (
        tid, poster_id, worker_id, title, spec,
        reward, status, accepted_bid_id,
        bidding_deadline, execution_deadline, review_deadline,
        created_at, accepted_at, submitted_at, approved_at,
    ) = task_row

    # 2. Resolve poster name
    poster_name = await _scalar(
        db,
        "SELECT name FROM identity_agents WHERE agent_id = ?",
        (poster_id,),
    )

    # 3. Resolve worker name (if any)
    worker = None
    if worker_id is not None:
        worker_name = await _scalar(
            db,
            "SELECT name FROM identity_agents WHERE agent_id = ?",
            (worker_id,),
        )
        worker = {"agent_id": worker_id, "name": worker_name}

    # 4. Bids
    bid_rows = await _fetchall(
        db,
        "SELECT bb.bid_id, bb.bidder_id, ia.name, bb.proposal, bb.submitted_at "
        "FROM board_bids bb "
        "JOIN identity_agents ia ON ia.agent_id = bb.bidder_id "
        "WHERE bb.task_id = ? "
        "ORDER BY bb.submitted_at ASC",
        (task_id,),
    )

    bids = []
    for br in bid_rows:
        bid_id, bidder_id, bidder_name, proposal, bid_submitted_at = br
        dq = await _delivery_quality(db, bidder_id)
        bids.append({
            "bid_id": bid_id,
            "bidder": {
                "agent_id": bidder_id,
                "name": bidder_name,
                "delivery_quality": dq,
            },
            "proposal": proposal,
            "submitted_at": bid_submitted_at,
            "accepted": bid_id == accepted_bid_id,
        })

    # 5. Assets
    asset_rows = await _fetchall(
        db,
        "SELECT asset_id, filename, content_type, size_bytes, uploaded_at "
        "FROM board_assets WHERE task_id = ? "
        "ORDER BY uploaded_at ASC",
        (task_id,),
    )
    assets = [
        {
            "asset_id": ar[0],
            "filename": ar[1],
            "content_type": ar[2],
            "size_bytes": ar[3],
            "uploaded_at": ar[4],
        }
        for ar in asset_rows
    ]

    # 6. Visible feedback
    fb_rows = await _fetchall(
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
    feedback = [
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

    # 7. Dispute
    dispute = None
    claim_row = await _fetchone(
        db,
        "SELECT claim_id, reason, filed_at "
        "FROM court_claims WHERE task_id = ?",
        (task_id,),
    )
    if claim_row is not None:
        claim_id, reason, filed_at = claim_row

        # Rebuttal
        rebuttal = None
        reb_row = await _fetchone(
            db,
            "SELECT content, submitted_at "
            "FROM court_rebuttals WHERE claim_id = ?",
            (claim_id,),
        )
        if reb_row is not None:
            rebuttal = {
                "content": reb_row[0],
                "submitted_at": reb_row[1],
            }

        # Ruling
        ruling = None
        rul_row = await _fetchone(
            db,
            "SELECT ruling_id, worker_pct, summary, ruled_at "
            "FROM court_rulings WHERE claim_id = ?",
            (claim_id,),
        )
        if rul_row is not None:
            ruling = {
                "ruling_id": rul_row[0],
                "worker_pct": rul_row[1],
                "summary": rul_row[2],
                "ruled_at": rul_row[3],
            }

        dispute = {
            "claim_id": claim_id,
            "reason": reason,
            "filed_at": filed_at,
            "rebuttal": rebuttal,
            "ruling": ruling,
        }

    return {
        "task_id": tid,
        "poster": {"agent_id": poster_id, "name": poster_name},
        "worker": worker,
        "title": title,
        "spec": spec,
        "reward": reward,
        "status": status,
        "deadlines": {
            "bidding_deadline": bidding_deadline,
            "execution_deadline": execution_deadline,
            "review_deadline": review_deadline,
        },
        "timestamps": {
            "created_at": created_at,
            "accepted_at": accepted_at,
            "submitted_at": submitted_at,
            "approved_at": approved_at,
        },
        "bids": bids,
        "assets": assets,
        "feedback": feedback,
        "dispute": dispute,
    }


async def get_competitive_tasks(
    db: aiosqlite.Connection,
    limit: int = 5,
    status: str = "open",
) -> list[dict]:
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
            "WHERE bt.status IN ('open', 'accepted') "
            "GROUP BY bt.task_id "
            "HAVING bid_count > 0 "
            "ORDER BY bid_count DESC "
            "LIMIT ?"
        )
        rows = await _fetchall(db, sql, (limit,))
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
            "HAVING bid_count > 0 "
            "ORDER BY bid_count DESC "
            "LIMIT ?"
        )
        rows = await _fetchall(db, sql, (limit,))

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
    min_age_minutes: int = 10,
    limit: int = 10,
) -> list[dict]:
    """Return open tasks with zero bids older than min_age_minutes."""
    sql = (
        "SELECT bt.task_id, bt.title, bt.reward, "
        "ia.name as poster_name, bt.poster_id, "
        "bt.created_at, bt.bidding_deadline, "
        "(julianday('now') - julianday(bt.created_at)) * 1440 as minutes_without_bids "
        "FROM board_tasks bt "
        "JOIN identity_agents ia ON bt.poster_id = ia.agent_id "
        "LEFT JOIN board_bids bb ON bt.task_id = bb.task_id "
        "WHERE bt.status = 'open' "
        "AND bb.bid_id IS NULL "
        "AND (julianday('now') - julianday(bt.created_at)) * 1440 >= ? "
        "ORDER BY bt.created_at ASC "
        "LIMIT ?"
    )
    rows = await _fetchall(db, sql, (min_age_minutes, limit))

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
