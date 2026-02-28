"""Economy metrics business logic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

    import aiosqlite


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _now() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(UTC)


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


async def compute_gdp_total(db: aiosqlite.Connection) -> int:
    """Compute total GDP from approved + ruled tasks."""
    approved = await _scalar(
        db,
        "SELECT COALESCE(SUM(reward), 0) FROM board_tasks WHERE status = 'approved'",
    )
    ruled = await _scalar(
        db,
        "SELECT COALESCE(SUM(reward * worker_pct / 100), 0) "
        "FROM board_tasks WHERE status = 'ruled' AND worker_pct IS NOT NULL",
    )
    return int(approved) + int(ruled)


async def compute_gdp_window(db: aiosqlite.Connection, since_iso: str) -> int:
    """Compute GDP for tasks approved/ruled since a given timestamp."""
    approved = await _scalar(
        db,
        "SELECT COALESCE(SUM(reward), 0) FROM board_tasks "
        "WHERE status = 'approved' AND approved_at >= ?",
        (since_iso,),
    )
    ruled = await _scalar(
        db,
        "SELECT COALESCE(SUM(reward * worker_pct / 100), 0) "
        "FROM board_tasks WHERE status = 'ruled' AND worker_pct IS NOT NULL "
        "AND ruled_at >= ?",
        (since_iso,),
    )
    return int(approved) + int(ruled)


async def compute_gdp(db: aiosqlite.Connection, active_agents: int) -> dict:
    """Compute all GDP metrics."""
    now = _now()
    total = await compute_gdp_total(db)

    since_24h = (now - timedelta(hours=24)).isoformat(timespec="seconds").replace("+00:00", "Z")
    since_7d = (now - timedelta(days=7)).isoformat(timespec="seconds").replace("+00:00", "Z")

    last_24h = await compute_gdp_window(db, since_24h)
    last_7d = await compute_gdp_window(db, since_7d)

    per_agent = total / active_agents if active_agents > 0 else 0.0
    rate_per_hour = last_24h / 24

    return {
        "total": total,
        "last_24h": last_24h,
        "last_7d": last_7d,
        "per_agent": per_agent,
        "rate_per_hour": rate_per_hour,
    }


async def compute_agents(db: aiosqlite.Connection) -> dict:
    """Compute agent metrics."""
    now = _now()
    since_30d = (now - timedelta(days=30)).isoformat(timespec="seconds").replace("+00:00", "Z")

    total_registered = await _scalar(
        db,
        "SELECT COUNT(*) FROM identity_agents",
    )

    active = await _scalar(
        db,
        "SELECT COUNT(DISTINCT agent_id) FROM ("
        "  SELECT poster_id AS agent_id FROM board_tasks "
        "  WHERE created_at >= ? OR accepted_at >= ? OR submitted_at >= ? OR approved_at >= ? "
        "  UNION "
        "  SELECT worker_id AS agent_id FROM board_tasks "
        "  WHERE worker_id IS NOT NULL "
        "  AND (created_at >= ? OR accepted_at >= ? OR submitted_at >= ? OR approved_at >= ?)"
        ")",
        (since_30d, since_30d, since_30d, since_30d,
         since_30d, since_30d, since_30d, since_30d),
    )

    with_completed = await _scalar(
        db,
        "SELECT COUNT(DISTINCT worker_id) FROM board_tasks WHERE status = 'approved'",
    )

    return {
        "total_registered": int(total_registered or 0),
        "active": int(active or 0),
        "with_completed_tasks": int(with_completed or 0),
    }


async def compute_tasks(db: aiosqlite.Connection) -> dict:
    """Compute task metrics."""
    now = _now()
    since_24h = (now - timedelta(hours=24)).isoformat(timespec="seconds").replace("+00:00", "Z")

    total_created = int(await _scalar(db, "SELECT COUNT(*) FROM board_tasks") or 0)
    completed_all_time = int(await _scalar(
        db, "SELECT COUNT(*) FROM board_tasks WHERE status = 'approved'",
    ) or 0)
    completed_24h = int(await _scalar(
        db,
        "SELECT COUNT(*) FROM board_tasks WHERE status = 'approved' AND approved_at >= ?",
        (since_24h,),
    ) or 0)
    open_count = int(await _scalar(
        db, "SELECT COUNT(*) FROM board_tasks WHERE status = 'open'",
    ) or 0)
    in_execution = int(await _scalar(
        db, "SELECT COUNT(*) FROM board_tasks WHERE status IN ('accepted', 'submitted')",
    ) or 0)
    disputed = int(await _scalar(
        db, "SELECT COUNT(*) FROM board_tasks WHERE status IN ('disputed', 'ruled')",
    ) or 0)

    # completion_rate = approved / (approved + disputed + ruled)
    ruled_count = int(await _scalar(
        db, "SELECT COUNT(*) FROM board_tasks WHERE status = 'ruled'",
    ) or 0)
    disputed_only = int(await _scalar(
        db, "SELECT COUNT(*) FROM board_tasks WHERE status = 'disputed'",
    ) or 0)
    denominator = completed_all_time + disputed_only + ruled_count
    completion_rate = completed_all_time / denominator if denominator > 0 else 0.0

    return {
        "total_created": total_created,
        "completed_all_time": completed_all_time,
        "completed_24h": completed_24h,
        "open": open_count,
        "in_execution": in_execution,
        "disputed": disputed,
        "completion_rate": round(completion_rate, 3),
    }


async def compute_escrow(db: aiosqlite.Connection) -> dict:
    """Compute escrow metrics."""
    total_locked = await _scalar(
        db,
        "SELECT COALESCE(SUM(amount), 0) FROM bank_escrow WHERE status = 'locked'",
    )
    return {"total_locked": int(total_locked)}


async def compute_spec_quality(db: aiosqlite.Connection) -> dict:
    """Compute spec quality metrics from visible feedback only."""
    now = _now()

    # Total visible spec_quality feedback
    total = int(await _scalar(
        db,
        "SELECT COUNT(*) FROM reputation_feedback "
        "WHERE category = 'spec_quality' AND visible = 1",
    ) or 0)

    if total == 0:
        return {
            "avg_score": 0.0,
            "extremely_satisfied_pct": 0.0,
            "satisfied_pct": 0.0,
            "dissatisfied_pct": 0.0,
            "trend_direction": "stable",
            "trend_delta": 0.0,
        }

    es_count = int(await _scalar(
        db,
        "SELECT COUNT(*) FROM reputation_feedback "
        "WHERE category = 'spec_quality' AND visible = 1 AND rating = 'extremely_satisfied'",
    ) or 0)
    sat_count = int(await _scalar(
        db,
        "SELECT COUNT(*) FROM reputation_feedback "
        "WHERE category = 'spec_quality' AND visible = 1 AND rating = 'satisfied'",
    ) or 0)
    dis_count = int(await _scalar(
        db,
        "SELECT COUNT(*) FROM reputation_feedback "
        "WHERE category = 'spec_quality' AND visible = 1 AND rating = 'dissatisfied'",
    ) or 0)

    avg_score = es_count / total
    es_pct = es_count / total
    sat_pct = sat_count / total
    dis_pct = dis_count / total

    # Trend: compare current quarter vs previous quarter
    # Current quarter: last 90 days, previous quarter: 90-180 days ago
    q_now = now
    q_current_start = (q_now - timedelta(days=90)).isoformat(timespec="seconds").replace("+00:00", "Z")
    q_prev_start = (q_now - timedelta(days=180)).isoformat(timespec="seconds").replace("+00:00", "Z")
    q_now_iso = q_now.isoformat(timespec="seconds").replace("+00:00", "Z")

    current_total = int(await _scalar(
        db,
        "SELECT COUNT(*) FROM reputation_feedback "
        "WHERE category = 'spec_quality' AND visible = 1 AND submitted_at >= ?",
        (q_current_start,),
    ) or 0)
    current_es = int(await _scalar(
        db,
        "SELECT COUNT(*) FROM reputation_feedback "
        "WHERE category = 'spec_quality' AND visible = 1 "
        "AND rating = 'extremely_satisfied' AND submitted_at >= ?",
        (q_current_start,),
    ) or 0)

    prev_total = int(await _scalar(
        db,
        "SELECT COUNT(*) FROM reputation_feedback "
        "WHERE category = 'spec_quality' AND visible = 1 "
        "AND submitted_at >= ? AND submitted_at < ?",
        (q_prev_start, q_current_start),
    ) or 0)
    prev_es = int(await _scalar(
        db,
        "SELECT COUNT(*) FROM reputation_feedback "
        "WHERE category = 'spec_quality' AND visible = 1 "
        "AND rating = 'extremely_satisfied' "
        "AND submitted_at >= ? AND submitted_at < ?",
        (q_prev_start, q_current_start),
    ) or 0)

    current_avg = current_es / current_total if current_total > 0 else 0.0
    prev_avg = prev_es / prev_total if prev_total > 0 else 0.0

    trend_delta = current_avg - prev_avg
    if prev_total == 0 and current_total == 0:
        trend_direction = "stable"
        trend_delta = 0.0
    elif trend_delta > 0:
        trend_direction = "improving"
    elif trend_delta < 0:
        trend_direction = "declining"
    else:
        trend_direction = "stable"

    return {
        "avg_score": avg_score,
        "extremely_satisfied_pct": es_pct,
        "satisfied_pct": sat_pct,
        "dissatisfied_pct": dis_pct,
        "trend_direction": trend_direction,
        "trend_delta": trend_delta,
    }


async def compute_labor_market(db: aiosqlite.Connection, active_agents: int) -> dict:
    """Compute labor market metrics."""
    now = _now()

    # avg_bids_per_task: average bid count across tasks that have bids
    avg_bids = await _scalar(
        db,
        "SELECT AVG(bid_count) FROM ("
        "  SELECT COUNT(*) AS bid_count FROM board_bids GROUP BY task_id"
        ")",
    )
    avg_bids_per_task = float(avg_bids) if avg_bids is not None else 0.0

    # avg_reward
    avg_reward_val = await _scalar(db, "SELECT AVG(reward) FROM board_tasks")
    avg_reward = float(avg_reward_val) if avg_reward_val is not None else 0

    # task_posting_rate: tasks created in last 1 hour
    since_1h = (now - timedelta(hours=1)).isoformat(timespec="seconds").replace("+00:00", "Z")
    task_posting_rate = float(await _scalar(
        db,
        "SELECT COUNT(*) FROM board_tasks WHERE created_at >= ?",
        (since_1h,),
    ) or 0)

    # acceptance_latency_minutes: avg of (accepted_at - created_at) in minutes, for last 7 days
    since_7d = (now - timedelta(days=7)).isoformat(timespec="seconds").replace("+00:00", "Z")
    latency = await _scalar(
        db,
        "SELECT AVG((julianday(accepted_at) - julianday(created_at)) * 1440) "
        "FROM board_tasks "
        "WHERE accepted_at IS NOT NULL AND accepted_at >= ?",
        (since_7d,),
    )
    acceptance_latency_minutes = float(latency) if latency is not None else 0.0

    # unemployment_rate: (active - busy) / active
    busy_agents = int(await _scalar(
        db,
        "SELECT COUNT(DISTINCT worker_id) FROM board_tasks "
        "WHERE status IN ('accepted', 'submitted') AND worker_id IS NOT NULL",
    ) or 0)
    unemployment_rate = (
        (active_agents - busy_agents) / active_agents
        if active_agents > 0
        else 0.0
    )

    # reward_distribution
    r_0_10 = int(await _scalar(
        db,
        "SELECT COUNT(*) FROM board_tasks WHERE reward BETWEEN 0 AND 10",
    ) or 0)
    r_11_50 = int(await _scalar(
        db,
        "SELECT COUNT(*) FROM board_tasks WHERE reward BETWEEN 11 AND 50",
    ) or 0)
    r_51_100 = int(await _scalar(
        db,
        "SELECT COUNT(*) FROM board_tasks WHERE reward BETWEEN 51 AND 99",
    ) or 0)
    r_over_100 = int(await _scalar(
        db,
        "SELECT COUNT(*) FROM board_tasks WHERE reward >= 100",
    ) or 0)

    return {
        "avg_bids_per_task": avg_bids_per_task,
        "avg_reward": avg_reward,
        "task_posting_rate": task_posting_rate,
        "acceptance_latency_minutes": acceptance_latency_minutes,
        "unemployment_rate": unemployment_rate,
        "reward_distribution": {
            "0_to_10": r_0_10,
            "11_to_50": r_11_50,
            "51_to_100": r_51_100,
            "over_100": r_over_100,
        },
    }


async def compute_economy_phase(db: aiosqlite.Connection, total_tasks: int) -> dict:
    """Compute economy phase metrics."""
    now = _now()

    # task_creation_trend: compare last 3.5 days vs previous 3.5 days
    since_3_5d = (now - timedelta(days=3.5)).isoformat(timespec="seconds").replace("+00:00", "Z")
    since_7d = (now - timedelta(days=7)).isoformat(timespec="seconds").replace("+00:00", "Z")

    current_period = int(await _scalar(
        db,
        "SELECT COUNT(*) FROM board_tasks WHERE created_at >= ?",
        (since_3_5d,),
    ) or 0)
    previous_period = int(await _scalar(
        db,
        "SELECT COUNT(*) FROM board_tasks WHERE created_at >= ? AND created_at < ?",
        (since_7d, since_3_5d),
    ) or 0)

    # Determine trend with +/- 5% tolerance
    if previous_period == 0 and current_period == 0:
        task_creation_trend = "stable"
    elif previous_period == 0:
        task_creation_trend = "increasing"
    else:
        ratio = current_period / previous_period
        if ratio > 1.05:
            task_creation_trend = "increasing"
        elif ratio < 0.95:
            task_creation_trend = "decreasing"
        else:
            task_creation_trend = "stable"

    # dispute_rate
    disputed_count = int(await _scalar(
        db,
        "SELECT COUNT(*) FROM board_tasks WHERE status IN ('disputed', 'ruled')",
    ) or 0)
    dispute_rate = disputed_count / total_tasks if total_tasks > 0 else 0.0

    # phase determination
    since_60m = (now - timedelta(minutes=60)).isoformat(timespec="seconds").replace("+00:00", "Z")
    recent_tasks = int(await _scalar(
        db,
        "SELECT COUNT(*) FROM board_tasks WHERE created_at >= ?",
        (since_60m,),
    ) or 0)

    if recent_tasks == 0:
        phase = "stalled"
    elif task_creation_trend == "increasing" and dispute_rate < 0.10:
        phase = "growing"
    elif task_creation_trend == "decreasing" or dispute_rate > 0.20:
        phase = "contracting"
    else:
        phase = "stable"

    return {
        "phase": phase,
        "task_creation_trend": task_creation_trend,
        "dispute_rate": round(dispute_rate, 3),
    }


async def compute_gdp_at_timestamp(db: aiosqlite.Connection, ts_iso: str) -> int:
    """Compute cumulative GDP up to a given timestamp."""
    approved = await _scalar(
        db,
        "SELECT COALESCE(SUM(reward), 0) FROM board_tasks "
        "WHERE status = 'approved' AND approved_at <= ?",
        (ts_iso,),
    )
    ruled = await _scalar(
        db,
        "SELECT COALESCE(SUM(reward * worker_pct / 100), 0) "
        "FROM board_tasks WHERE status = 'ruled' AND worker_pct IS NOT NULL "
        "AND ruled_at <= ?",
        (ts_iso,),
    )
    return int(approved) + int(ruled)


async def compute_gdp_history(
    db: aiosqlite.Connection,
    window: str,
    resolution: str,
) -> list[dict[str, Any]]:
    """Compute GDP time series for the given window and resolution."""
    now = _now()

    window_map = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7)}
    resolution_map = {"1m": timedelta(minutes=1), "5m": timedelta(minutes=5), "1h": timedelta(hours=1)}

    window_delta = window_map[window]
    resolution_delta = resolution_map[resolution]

    start = now - window_delta
    data_points: list[dict[str, Any]] = []

    current = start
    while current < now:
        ts_iso = current.isoformat(timespec="seconds").replace("+00:00", "Z")
        gdp = await compute_gdp_at_timestamp(db, ts_iso)
        data_points.append({"timestamp": ts_iso, "gdp": gdp})
        current += resolution_delta

    return data_points
