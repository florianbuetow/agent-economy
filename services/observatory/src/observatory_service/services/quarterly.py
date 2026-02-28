"""Quarterly report business logic."""

from __future__ import annotations

import calendar
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

    import aiosqlite


_QUARTER_RE = re.compile(r"^(\d{4})-Q([1-4])$")

# Quarter month ranges: Q1=Jan-Mar, Q2=Apr-Jun, Q3=Jul-Sep, Q4=Oct-Dec
_QUARTER_MONTHS = {
    1: (1, 3),
    2: (4, 6),
    3: (7, 9),
    4: (10, 12),
}


def _now() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(UTC)


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


def validate_quarter(quarter: str) -> tuple[int, int]:
    """Validate quarter format and return (year, quarter_number).

    Raises ValueError if the format is invalid.
    """
    match = _QUARTER_RE.match(quarter)
    if not match:
        msg = f"Invalid quarter format: {quarter}. Must match YYYY-QN where N is 1-4."
        raise ValueError(msg)
    return int(match.group(1)), int(match.group(2))


def current_quarter_label() -> str:
    """Return the current quarter label, e.g. '2026-Q1'."""
    now = _now()
    q = (now.month - 1) // 3 + 1
    return f"{now.year}-Q{q}"


def _quarter_period(year: int, q: int) -> tuple[str, str]:
    """Return (start_iso, end_iso) for a given quarter."""
    start_month, end_month = _QUARTER_MONTHS[q]
    start = f"{year}-{start_month:02d}-01T00:00:00Z"

    # End of quarter: last day of the end_month
    if end_month == 12:
        end = f"{year}-12-31T23:59:59Z"
    else:
        last_day = calendar.monthrange(year, end_month)[1]
        end = f"{year}-{end_month:02d}-{last_day:02d}T23:59:59Z"

    return start, end


def _previous_quarter(year: int, q: int) -> tuple[int, int]:
    """Return (year, quarter_number) for the previous quarter."""
    if q == 1:
        return year - 1, 4
    return year, q - 1


async def _compute_gdp_for_period(db: aiosqlite.Connection, start: str, end: str) -> int:
    """Compute GDP for tasks approved/ruled within a period."""
    approved = await _scalar(
        db,
        "SELECT COALESCE(SUM(reward), 0) FROM board_tasks "
        "WHERE status = 'approved' AND approved_at >= ? AND approved_at <= ?",
        (start, end),
    )
    ruled = await _scalar(
        db,
        "SELECT COALESCE(SUM(reward * worker_pct / 100), 0) "
        "FROM board_tasks WHERE status = 'ruled' AND worker_pct IS NOT NULL "
        "AND ruled_at >= ? AND ruled_at <= ?",
        (start, end),
    )
    return int(approved) + int(ruled)


async def _compute_spec_quality(
    db: aiosqlite.Connection,
    start: str,
    end: str,
    prev_start: str,
    prev_end: str,
) -> dict[str, Any]:
    """Compute spec quality metrics for a quarter and its predecessor."""
    spec_total = int(
        await _scalar(
            db,
            "SELECT COUNT(*) FROM reputation_feedback "
            "WHERE category = 'spec_quality' AND visible = 1 "
            "AND submitted_at >= ? AND submitted_at <= ?",
            (start, end),
        )
        or 0
    )

    spec_es = int(
        await _scalar(
            db,
            "SELECT COUNT(*) FROM reputation_feedback "
            "WHERE category = 'spec_quality' AND visible = 1 "
            "AND rating = 'extremely_satisfied' "
            "AND submitted_at >= ? AND submitted_at <= ?",
            (start, end),
        )
        or 0
    )

    avg_score = spec_es / spec_total if spec_total > 0 else 0.0

    prev_spec_total = int(
        await _scalar(
            db,
            "SELECT COUNT(*) FROM reputation_feedback "
            "WHERE category = 'spec_quality' AND visible = 1 "
            "AND submitted_at >= ? AND submitted_at <= ?",
            (prev_start, prev_end),
        )
        or 0
    )

    prev_spec_es = int(
        await _scalar(
            db,
            "SELECT COUNT(*) FROM reputation_feedback "
            "WHERE category = 'spec_quality' AND visible = 1 "
            "AND rating = 'extremely_satisfied' "
            "AND submitted_at >= ? AND submitted_at <= ?",
            (prev_start, prev_end),
        )
        or 0
    )

    prev_avg_score = prev_spec_es / prev_spec_total if prev_spec_total > 0 else 0.0
    spec_delta = (
        round((avg_score - prev_avg_score) / prev_avg_score * 100, 2) if prev_avg_score > 0 else 0.0
    )

    return {
        "avg_score": round(avg_score, 2),
        "previous_quarter_avg": round(prev_avg_score, 2),
        "delta_pct": spec_delta,
    }


async def _compute_notable(
    db: aiosqlite.Connection,
    start: str,
    end: str,
) -> dict[str, Any]:
    """Compute notable tasks and agents for a quarter period."""
    hvt_row = await _fetchone(
        db,
        "SELECT task_id, title, reward FROM board_tasks "
        "WHERE created_at >= ? AND created_at <= ? "
        "ORDER BY reward DESC LIMIT 1",
        (start, end),
    )
    highest_value_task = None
    if hvt_row:
        highest_value_task = {
            "task_id": hvt_row[0],
            "title": hvt_row[1],
            "reward": int(hvt_row[2]),
        }

    mct_row = await _fetchone(
        db,
        "SELECT t.task_id, t.title, COUNT(b.bid_id) AS bid_count "
        "FROM board_tasks t "
        "JOIN board_bids b ON t.task_id = b.task_id "
        "WHERE t.created_at >= ? AND t.created_at <= ? "
        "GROUP BY t.task_id "
        "ORDER BY bid_count DESC LIMIT 1",
        (start, end),
    )
    most_competitive_task = None
    if mct_row:
        most_competitive_task = {
            "task_id": mct_row[0],
            "title": mct_row[1],
            "bid_count": int(mct_row[2]),
        }

    top_workers_rows = await _fetchall(
        db,
        "SELECT a.agent_id, a.name, "
        "  COALESCE(SUM(CASE "
        "    WHEN t.status = 'approved' AND t.approved_at >= ? AND t.approved_at <= ? "
        "      THEN t.reward "
        "    WHEN t.status = 'ruled' AND t.ruled_at >= ? AND t.ruled_at <= ? "
        "      THEN t.reward * t.worker_pct / 100 "
        "    ELSE 0 "
        "  END), 0) AS earned "
        "FROM identity_agents a "
        "JOIN board_tasks t ON a.agent_id = t.worker_id "
        "WHERE (t.status = 'approved' AND t.approved_at >= ? AND t.approved_at <= ?) "
        "   OR (t.status = 'ruled' AND t.ruled_at >= ? AND t.ruled_at <= ?) "
        "GROUP BY a.agent_id "
        "HAVING earned > 0 "
        "ORDER BY earned DESC "
        "LIMIT 3",
        (start, end, start, end, start, end, start, end),
    )
    top_workers = [{"agent_id": r[0], "name": r[1], "earned": int(r[2])} for r in top_workers_rows]

    top_posters_rows = await _fetchall(
        db,
        "SELECT a.agent_id, a.name, "
        "  COALESCE(SUM(t.reward), 0) AS spent "
        "FROM identity_agents a "
        "JOIN board_tasks t ON a.agent_id = t.poster_id "
        "WHERE t.created_at >= ? AND t.created_at <= ? "
        "GROUP BY a.agent_id "
        "HAVING spent > 0 "
        "ORDER BY spent DESC "
        "LIMIT 3",
        (start, end),
    )
    top_posters = [{"agent_id": r[0], "name": r[1], "spent": int(r[2])} for r in top_posters_rows]

    return {
        "highest_value_task": highest_value_task,
        "most_competitive_task": most_competitive_task,
        "top_workers": top_workers,
        "top_posters": top_posters,
    }


async def get_quarterly_report(db: aiosqlite.Connection, quarter: str) -> dict[str, Any] | None:
    """Compute and return the full quarterly report.

    Raises ValueError for invalid quarter format.
    Returns None if no data exists for the quarter.
    """
    year, q = validate_quarter(quarter)
    start, end = _quarter_period(year, q)

    # Check if any data exists in this quarter
    task_count = int(
        await _scalar(
            db,
            "SELECT COUNT(*) FROM board_tasks WHERE created_at >= ? AND created_at <= ?",
            (start, end),
        )
        or 0
    )

    agent_count = int(
        await _scalar(
            db,
            "SELECT COUNT(*) FROM identity_agents WHERE registered_at >= ? AND registered_at <= ?",
            (start, end),
        )
        or 0
    )

    gdp_task_count = int(
        await _scalar(
            db,
            "SELECT COUNT(*) FROM board_tasks "
            "WHERE (status = 'approved' AND approved_at >= ? AND approved_at <= ?) "
            "OR (status = 'ruled' AND ruled_at >= ? AND ruled_at <= ?)",
            (start, end, start, end),
        )
        or 0
    )

    if task_count == 0 and agent_count == 0 and gdp_task_count == 0:
        return None

    # --- GDP ---
    total_gdp = await _compute_gdp_for_period(db, start, end)

    prev_year, prev_q = _previous_quarter(year, q)
    prev_start, prev_end = _quarter_period(prev_year, prev_q)
    prev_gdp = await _compute_gdp_for_period(db, prev_start, prev_end)

    delta_pct = round((total_gdp - prev_gdp) / prev_gdp * 100, 1) if prev_gdp > 0 else 0.0

    total_agents = int(
        await _scalar(
            db,
            "SELECT COUNT(*) FROM identity_agents WHERE registered_at <= ?",
            (end,),
        )
        or 0
    )

    per_agent = total_gdp / total_agents if total_agents > 0 else 0.0

    gdp = {
        "total": total_gdp,
        "previous_quarter": prev_gdp,
        "delta_pct": delta_pct,
        "per_agent": round(per_agent, 1),
    }

    # --- Tasks ---
    posted = int(
        await _scalar(
            db,
            "SELECT COUNT(*) FROM board_tasks WHERE created_at >= ? AND created_at <= ?",
            (start, end),
        )
        or 0
    )

    completed = int(
        await _scalar(
            db,
            "SELECT COUNT(*) FROM board_tasks "
            "WHERE status = 'approved' AND approved_at >= ? AND approved_at <= ?",
            (start, end),
        )
        or 0
    )

    disputed = int(
        await _scalar(
            db,
            "SELECT COUNT(*) FROM board_tasks "
            "WHERE status IN ('disputed', 'ruled') "
            "AND created_at >= ? AND created_at <= ?",
            (start, end),
        )
        or 0
    )

    denom = completed + disputed
    completion_rate = round(completed / denom, 2) if denom > 0 else 0.0

    tasks = {
        "posted": posted,
        "completed": completed,
        "disputed": disputed,
        "completion_rate": completion_rate,
    }

    # --- Labor Market ---
    avg_bids = await _scalar(
        db,
        "SELECT AVG(bid_count) FROM ("
        "  SELECT COUNT(*) AS bid_count FROM board_bids b "
        "  JOIN board_tasks t ON b.task_id = t.task_id "
        "  WHERE t.created_at >= ? AND t.created_at <= ? "
        "  GROUP BY b.task_id"
        ")",
        (start, end),
    )
    avg_bids_per_task = round(float(avg_bids), 1) if avg_bids is not None else 0.0

    latency = await _scalar(
        db,
        "SELECT AVG((julianday(accepted_at) - julianday(created_at)) * 1440) "
        "FROM board_tasks "
        "WHERE accepted_at IS NOT NULL "
        "AND created_at >= ? AND created_at <= ?",
        (start, end),
    )
    avg_time_to_acceptance = round(float(latency), 0) if latency is not None else 0.0

    avg_reward_val = await _scalar(
        db,
        "SELECT AVG(reward) FROM board_tasks WHERE created_at >= ? AND created_at <= ?",
        (start, end),
    )
    avg_reward = round(float(avg_reward_val), 0) if avg_reward_val is not None else 0.0

    labor_market = {
        "avg_bids_per_task": avg_bids_per_task,
        "avg_time_to_acceptance_minutes": avg_time_to_acceptance,
        "avg_reward": avg_reward,
    }

    # --- Spec Quality ---
    spec_quality = await _compute_spec_quality(
        db,
        start,
        end,
        prev_start,
        prev_end,
    )

    # --- Agents ---
    new_registrations = int(
        await _scalar(
            db,
            "SELECT COUNT(*) FROM identity_agents WHERE registered_at >= ? AND registered_at <= ?",
            (start, end),
        )
        or 0
    )

    total_at_quarter_end = int(
        await _scalar(
            db,
            "SELECT COUNT(*) FROM identity_agents WHERE registered_at <= ?",
            (end,),
        )
        or 0
    )

    agents = {
        "new_registrations": new_registrations,
        "total_at_quarter_end": total_at_quarter_end,
    }

    notable = await _compute_notable(db, start, end)

    return {
        "quarter": quarter,
        "period": {"start": start, "end": end},
        "gdp": gdp,
        "tasks": tasks,
        "labor_market": labor_market,
        "spec_quality": spec_quality,
        "agents": agents,
        "notable": notable,
    }
