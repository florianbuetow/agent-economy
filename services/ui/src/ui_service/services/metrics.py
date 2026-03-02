"""Economy metrics business logic."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from ui_service.services.database import (
    execute_fetchall,
    execute_scalar,
    now_iso,
    to_iso,
    utc_now,
)

if TYPE_CHECKING:
    import aiosqlite

__all__ = ["now_iso"]


def _pct_change(current: float, previous: float) -> float | None:
    """Compute percentage change from previous to current."""
    if previous == 0:
        return None
    return ((current - previous) / previous) * 100


async def compute_gdp_total(db: aiosqlite.Connection) -> int:
    """Compute total GDP from approved + ruled tasks."""
    approved = await execute_scalar(
        db,
        "SELECT COALESCE(SUM(reward), 0) FROM board_tasks WHERE status = 'approved'",
        (),
    )
    ruled = await execute_scalar(
        db,
        "SELECT COALESCE(SUM(reward * worker_pct / 100), 0) "
        "FROM board_tasks WHERE status = 'ruled' AND worker_pct IS NOT NULL",
        (),
    )
    return int(approved) + int(ruled)


async def compute_gdp_window(db: aiosqlite.Connection, since_iso: str) -> int:
    """Compute GDP for tasks approved/ruled since a given timestamp."""
    approved = await execute_scalar(
        db,
        "SELECT COALESCE(SUM(reward), 0) FROM board_tasks "
        "WHERE status = 'approved' AND approved_at >= ?",
        (since_iso,),
    )
    ruled = await execute_scalar(
        db,
        "SELECT COALESCE(SUM(reward * worker_pct / 100), 0) "
        "FROM board_tasks WHERE status = 'ruled' AND worker_pct IS NOT NULL "
        "AND ruled_at >= ?",
        (since_iso,),
    )
    return int(approved) + int(ruled)


async def _compute_gdp_window_between(
    db: aiosqlite.Connection, since_iso: str, until_iso: str
) -> int:
    """Compute GDP for tasks approved/ruled in a specific time window."""
    approved = await execute_scalar(
        db,
        "SELECT COALESCE(SUM(reward), 0) FROM board_tasks "
        "WHERE status = 'approved' AND approved_at >= ? AND approved_at < ?",
        (since_iso, until_iso),
    )
    ruled = await execute_scalar(
        db,
        "SELECT COALESCE(SUM(reward * worker_pct / 100), 0) "
        "FROM board_tasks WHERE status = 'ruled' AND worker_pct IS NOT NULL "
        "AND ruled_at >= ? AND ruled_at < ?",
        (since_iso, until_iso),
    )
    return int(approved) + int(ruled)


async def compute_gdp(db: aiosqlite.Connection, active_agents: int) -> dict[str, Any]:
    """Compute all GDP metrics."""
    now = utc_now()
    total = await compute_gdp_total(db)

    since_1h = to_iso(now - timedelta(hours=1))
    since_2h = to_iso(now - timedelta(hours=2))
    since_24h = to_iso(now - timedelta(hours=24))
    since_48h = to_iso(now - timedelta(hours=48))
    since_7d = to_iso(now - timedelta(days=7))

    last_1h = await compute_gdp_window(db, since_1h)
    prev_1h = await _compute_gdp_window_between(db, since_2h, since_1h)
    last_24h = await compute_gdp_window(db, since_24h)
    prev_24h = await _compute_gdp_window_between(db, since_48h, since_24h)
    last_7d = await compute_gdp_window(db, since_7d)

    per_agent = total / active_agents if active_agents > 0 else 0.0
    rate_per_hour = last_24h / 24

    return {
        "total": total,
        "last_24h": last_24h,
        "last_7d": last_7d,
        "per_agent": per_agent,
        "rate_per_hour": rate_per_hour,
        "delta_1h": _pct_change(float(last_1h), float(prev_1h)),
        "delta_24h": _pct_change(float(last_24h), float(prev_24h)),
    }


async def compute_agents(db: aiosqlite.Connection) -> dict[str, Any]:
    """Compute agent metrics."""
    now = utc_now()
    since_30d = to_iso(now - timedelta(days=30))
    since_60d = to_iso(now - timedelta(days=60))

    total_registered = await execute_scalar(
        db,
        "SELECT COUNT(*) FROM identity_agents",
        (),
    )

    active = await execute_scalar(
        db,
        "SELECT COUNT(DISTINCT agent_id) FROM ("
        "  SELECT poster_id AS agent_id FROM board_tasks "
        "  WHERE created_at >= ? OR accepted_at >= ? OR submitted_at >= ? OR approved_at >= ? "
        "  UNION "
        "  SELECT worker_id AS agent_id FROM board_tasks "
        "  WHERE worker_id IS NOT NULL "
        "  AND (created_at >= ? OR accepted_at >= ? OR submitted_at >= ? OR approved_at >= ?)"
        ")",
        (since_30d, since_30d, since_30d, since_30d, since_30d, since_30d, since_30d, since_30d),
    )

    prev_active = await execute_scalar(
        db,
        "SELECT COUNT(DISTINCT agent_id) FROM ("
        "  SELECT poster_id AS agent_id FROM board_tasks "
        "  WHERE (created_at >= ? AND created_at < ?) OR (accepted_at >= ? AND accepted_at < ?) "
        "  OR (submitted_at >= ? AND submitted_at < ?) OR (approved_at >= ? AND approved_at < ?) "
        "  UNION "
        "  SELECT worker_id AS agent_id FROM board_tasks "
        "  WHERE worker_id IS NOT NULL "
        "  AND ((created_at >= ? AND created_at < ?) OR (accepted_at >= ? AND accepted_at < ?) "
        "  OR (submitted_at >= ? AND submitted_at < ?) OR (approved_at >= ? AND approved_at < ?))"
        ")",
        (
            since_60d,
            since_30d,
            since_60d,
            since_30d,
            since_60d,
            since_30d,
            since_60d,
            since_30d,
            since_60d,
            since_30d,
            since_60d,
            since_30d,
            since_60d,
            since_30d,
            since_60d,
            since_30d,
        ),
    )

    with_completed = await execute_scalar(
        db,
        "SELECT COUNT(DISTINCT worker_id) FROM board_tasks WHERE status = 'approved'",
        (),
    )

    active_val = int(active or 0)
    prev_active_val = int(prev_active or 0)

    return {
        "total_registered": int(total_registered or 0),
        "active": active_val,
        "with_completed_tasks": int(with_completed or 0),
        "delta_active": _pct_change(float(active_val), float(prev_active_val)),
    }


async def compute_tasks(db: aiosqlite.Connection) -> dict[str, Any]:
    """Compute task metrics."""
    now = utc_now()
    since_24h = to_iso(now - timedelta(hours=24))

    total_created = int(await execute_scalar(db, "SELECT COUNT(*) FROM board_tasks", ()) or 0)
    completed_all_time = int(
        await execute_scalar(
            db,
            "SELECT COUNT(*) FROM board_tasks WHERE status = 'approved'",
            (),
        )
        or 0
    )
    completed_24h = int(
        await execute_scalar(
            db,
            "SELECT COUNT(*) FROM board_tasks WHERE status = 'approved' AND approved_at >= ?",
            (since_24h,),
        )
        or 0
    )
    open_count = int(
        await execute_scalar(
            db,
            "SELECT COUNT(*) FROM board_tasks WHERE status = 'open'",
            (),
        )
        or 0
    )
    in_execution = int(
        await execute_scalar(
            db,
            "SELECT COUNT(*) FROM board_tasks WHERE status IN ('accepted', 'submitted')",
            (),
        )
        or 0
    )
    disputed = int(
        await execute_scalar(
            db,
            "SELECT COUNT(*) FROM board_tasks WHERE status IN ('disputed', 'ruled')",
            (),
        )
        or 0
    )

    ruled_count = int(
        await execute_scalar(
            db,
            "SELECT COUNT(*) FROM board_tasks WHERE status = 'ruled'",
            (),
        )
        or 0
    )
    disputed_only = int(
        await execute_scalar(
            db,
            "SELECT COUNT(*) FROM board_tasks WHERE status = 'disputed'",
            (),
        )
        or 0
    )
    denominator = completed_all_time + disputed_only + ruled_count
    completion_rate = completed_all_time / denominator if denominator > 0 else 0.0

    new_open = int(
        await execute_scalar(
            db,
            "SELECT COUNT(*) FROM board_tasks WHERE status = 'open' AND created_at >= ?",
            (since_24h,),
        )
        or 0
    )
    delta_open = float(new_open)

    since_48h = to_iso(now - timedelta(hours=48))
    prev_completed_24h = int(
        await execute_scalar(
            db,
            "SELECT COUNT(*) FROM board_tasks WHERE status = 'approved' "
            "AND approved_at >= ? AND approved_at < ?",
            (since_48h, since_24h),
        )
        or 0
    )
    delta_completed_24h = _pct_change(float(completed_24h), float(prev_completed_24h))

    return {
        "total_created": total_created,
        "completed_all_time": completed_all_time,
        "completed_24h": completed_24h,
        "open": open_count,
        "in_execution": in_execution,
        "disputed": disputed,
        "completion_rate": round(completion_rate, 3),
        "delta_open": delta_open,
        "delta_completed_24h": delta_completed_24h,
    }


async def compute_escrow(db: aiosqlite.Connection) -> dict[str, Any]:
    """Compute escrow metrics."""
    total_locked = await execute_scalar(
        db,
        "SELECT COALESCE(SUM(amount), 0) FROM bank_escrow WHERE status = 'locked'",
        (),
    )
    now = utc_now()
    since_1h = to_iso(now - timedelta(hours=1))
    since_2h = to_iso(now - timedelta(hours=2))
    recent_locked = int(
        await execute_scalar(
            db,
            "SELECT COALESCE(SUM(amount), 0) FROM bank_escrow "
            "WHERE status = 'locked' AND created_at >= ?",
            (since_1h,),
        )
        or 0
    )
    prev_locked = int(
        await execute_scalar(
            db,
            "SELECT COALESCE(SUM(amount), 0) FROM bank_escrow "
            "WHERE status = 'locked' AND created_at >= ? AND created_at < ?",
            (since_2h, since_1h),
        )
        or 0
    )

    locked_val = int(total_locked)
    return {
        "total_locked": locked_val,
        "delta_locked": _pct_change(float(recent_locked), float(prev_locked)),
    }


async def compute_spec_quality(db: aiosqlite.Connection) -> dict[str, Any]:
    """Compute spec quality metrics from visible feedback only."""
    now = utc_now()

    # Total visible spec_quality feedback
    total = int(
        await execute_scalar(
            db,
            "SELECT COUNT(*) FROM reputation_feedback "
            "WHERE category = 'spec_quality' AND visible = 1",
            (),
        )
        or 0
    )

    if total == 0:
        return {
            "avg_score": 0.0,
            "extremely_satisfied_pct": 0.0,
            "satisfied_pct": 0.0,
            "dissatisfied_pct": 0.0,
            "trend_direction": "stable",
            "trend_delta": 0.0,
        }

    es_count = int(
        await execute_scalar(
            db,
            "SELECT COUNT(*) FROM reputation_feedback "
            "WHERE category = 'spec_quality' AND visible = 1 AND rating = 'extremely_satisfied'",
            (),
        )
        or 0
    )
    sat_count = int(
        await execute_scalar(
            db,
            "SELECT COUNT(*) FROM reputation_feedback "
            "WHERE category = 'spec_quality' AND visible = 1 AND rating = 'satisfied'",
            (),
        )
        or 0
    )
    dis_count = int(
        await execute_scalar(
            db,
            "SELECT COUNT(*) FROM reputation_feedback "
            "WHERE category = 'spec_quality' AND visible = 1 AND rating = 'dissatisfied'",
            (),
        )
        or 0
    )

    avg_score = es_count / total
    es_pct = es_count / total
    sat_pct = sat_count / total
    dis_pct = dis_count / total

    # Trend: compare current quarter vs previous quarter
    # Current quarter: last 90 days, previous quarter: 90-180 days ago
    q_now = now
    q_current_start = to_iso(q_now - timedelta(days=90))
    q_prev_start = to_iso(q_now - timedelta(days=180))

    current_total = int(
        await execute_scalar(
            db,
            "SELECT COUNT(*) FROM reputation_feedback "
            "WHERE category = 'spec_quality' AND visible = 1 AND submitted_at >= ?",
            (q_current_start,),
        )
        or 0
    )
    current_es = int(
        await execute_scalar(
            db,
            "SELECT COUNT(*) FROM reputation_feedback "
            "WHERE category = 'spec_quality' AND visible = 1 "
            "AND rating = 'extremely_satisfied' AND submitted_at >= ?",
            (q_current_start,),
        )
        or 0
    )

    prev_total = int(
        await execute_scalar(
            db,
            "SELECT COUNT(*) FROM reputation_feedback "
            "WHERE category = 'spec_quality' AND visible = 1 "
            "AND submitted_at >= ? AND submitted_at < ?",
            (q_prev_start, q_current_start),
        )
        or 0
    )
    prev_es = int(
        await execute_scalar(
            db,
            "SELECT COUNT(*) FROM reputation_feedback "
            "WHERE category = 'spec_quality' AND visible = 1 "
            "AND rating = 'extremely_satisfied' "
            "AND submitted_at >= ? AND submitted_at < ?",
            (q_prev_start, q_current_start),
        )
        or 0
    )

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


async def compute_labor_market(db: aiosqlite.Connection, active_agents: int) -> dict[str, Any]:
    """Compute labor market metrics."""
    now = utc_now()

    # avg_bids_per_task: average bid count across tasks that have bids
    avg_bids = await execute_scalar(
        db,
        "SELECT AVG(bid_count) FROM ("
        "  SELECT COUNT(*) AS bid_count FROM board_bids GROUP BY task_id"
        ")",
        (),
    )
    avg_bids_per_task = float(avg_bids) if avg_bids is not None else 0.0

    # avg_reward
    avg_reward_val = await execute_scalar(db, "SELECT AVG(reward) FROM board_tasks", ())
    avg_reward = float(avg_reward_val) if avg_reward_val is not None else 0.0

    # task_posting_rate: tasks created in last 1 hour
    since_1h = to_iso(now - timedelta(hours=1))
    task_posting_rate = float(
        await execute_scalar(
            db,
            "SELECT COUNT(*) FROM board_tasks WHERE created_at >= ?",
            (since_1h,),
        )
        or 0
    )

    # acceptance_latency_minutes: avg of (accepted_at - created_at) in minutes, for last 7 days
    since_7d = to_iso(now - timedelta(days=7))
    latency = await execute_scalar(
        db,
        "SELECT AVG((julianday(accepted_at) - julianday(created_at)) * 1440) "
        "FROM board_tasks "
        "WHERE accepted_at IS NOT NULL AND accepted_at >= ?",
        (since_7d,),
    )
    acceptance_latency_minutes = float(latency) if latency is not None else 0.0

    busy_agents = int(
        await execute_scalar(
            db,
            "SELECT COUNT(DISTINCT worker_id) FROM board_tasks "
            "WHERE status IN ('accepted', 'submitted') AND worker_id IS NOT NULL",
            (),
        )
        or 0
    )
    unemployment_rate = (active_agents - busy_agents) / active_agents if active_agents > 0 else 0.0

    # reward_distribution
    r_0_10 = int(
        await execute_scalar(
            db,
            "SELECT COUNT(*) FROM board_tasks WHERE reward BETWEEN 0 AND 10",
            (),
        )
        or 0
    )
    r_11_50 = int(
        await execute_scalar(
            db,
            "SELECT COUNT(*) FROM board_tasks WHERE reward BETWEEN 11 AND 50",
            (),
        )
        or 0
    )
    r_51_100 = int(
        await execute_scalar(
            db,
            "SELECT COUNT(*) FROM board_tasks WHERE reward BETWEEN 51 AND 99",
            (),
        )
        or 0
    )
    r_over_100 = int(
        await execute_scalar(
            db,
            "SELECT COUNT(*) FROM board_tasks WHERE reward >= 100",
            (),
        )
        or 0
    )

    since_14d = to_iso(now - timedelta(days=14))
    prev_avg_bids = await execute_scalar(
        db,
        "SELECT AVG(bid_count) FROM ("
        "  SELECT COUNT(*) AS bid_count FROM board_bids "
        "  WHERE submitted_at >= ? AND submitted_at < ? "
        "  GROUP BY task_id"
        ")",
        (since_14d, since_7d),
    )
    prev_avg_bids_val = float(prev_avg_bids) if prev_avg_bids is not None else 0.0
    delta_avg_bids_val = _pct_change(avg_bids_per_task, prev_avg_bids_val)

    prev_avg_reward = await execute_scalar(
        db,
        "SELECT AVG(reward) FROM board_tasks WHERE created_at >= ? AND created_at < ?",
        (since_14d, since_7d),
    )
    prev_avg_reward_val = float(prev_avg_reward) if prev_avg_reward is not None else 0.0
    delta_avg_reward_val = _pct_change(avg_reward, prev_avg_reward_val)

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
        "delta_avg_bids": delta_avg_bids_val,
        "delta_avg_reward": delta_avg_reward_val,
    }


async def compute_economy_phase(db: aiosqlite.Connection, total_tasks: int) -> dict[str, Any]:
    """Compute economy phase metrics."""
    now = utc_now()

    # task_creation_trend: compare last 3.5 days vs previous 3.5 days
    since_3_5d = to_iso(now - timedelta(days=3.5))
    since_7d = to_iso(now - timedelta(days=7))

    current_period = int(
        await execute_scalar(
            db,
            "SELECT COUNT(*) FROM board_tasks WHERE created_at >= ?",
            (since_3_5d,),
        )
        or 0
    )
    previous_period = int(
        await execute_scalar(
            db,
            "SELECT COUNT(*) FROM board_tasks WHERE created_at >= ? AND created_at < ?",
            (since_7d, since_3_5d),
        )
        or 0
    )

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
    disputed_count = int(
        await execute_scalar(
            db,
            "SELECT COUNT(*) FROM board_tasks WHERE status IN ('disputed', 'ruled')",
            (),
        )
        or 0
    )
    dispute_rate = disputed_count / total_tasks if total_tasks > 0 else 0.0

    # phase determination
    since_60m = to_iso(now - timedelta(minutes=60))
    recent_tasks = int(
        await execute_scalar(
            db,
            "SELECT COUNT(*) FROM board_tasks WHERE created_at >= ?",
            (since_60m,),
        )
        or 0
    )

    if recent_tasks == 0:
        phase = "idle"
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
    approved = await execute_scalar(
        db,
        "SELECT COALESCE(SUM(reward), 0) FROM board_tasks "
        "WHERE status = 'approved' AND approved_at <= ?",
        (ts_iso,),
    )
    ruled = await execute_scalar(
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
    now = datetime.fromisoformat(now_iso().replace("Z", "+00:00"))

    window_map = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7)}
    resolution_map = {
        "1m": timedelta(minutes=1),
        "5m": timedelta(minutes=5),
        "1h": timedelta(hours=1),
    }

    window_delta = window_map[window]
    resolution_delta = resolution_map[resolution]

    start = now - window_delta
    data_points: list[dict[str, Any]] = []

    current = start
    while current < now:
        ts_iso = to_iso(current)
        gdp = await compute_gdp_at_timestamp(db, ts_iso)
        data_points.append({"timestamp": ts_iso, "gdp": gdp})
        current += resolution_delta

    return data_points


async def compute_sparkline_history(
    db: aiosqlite.Connection,
    window: str,
) -> dict[str, Any]:
    """Compute hourly-bucket sparkline time series for exchange board metrics.

    Uses efficient GROUP BY queries on the events table instead of
    the per-step loop pattern used by compute_gdp_history.
    """
    now = utc_now()
    window_delta = {"24h": timedelta(hours=24)}[window]
    start = now - window_delta
    since_iso = to_iso(start)

    # Build 24 hourly bucket keys: "2026-03-02T09", "2026-03-02T10", ...
    buckets: list[str] = []
    current = start
    while current < now:
        buckets.append(to_iso(current)[:13])
        current += timedelta(hours=1)

    async def _fetch_buckets(
        sql: str,
        params: tuple[Any, ...],
    ) -> dict[str, float]:
        """Run a GROUP BY bucket query, return {bucket_str: value}."""
        rows = await execute_fetchall(db, sql, params)
        return {str(row[0]): float(row[1]) for row in rows}

    def _to_series(bucket_dict: dict[str, float]) -> list[float]:
        """Convert bucket dict to ordered list aligned with bucket keys."""
        return [bucket_dict.get(b, 0.0) for b in buckets]

    # 1. Tasks created per hour
    created = await _fetch_buckets(
        "SELECT substr(timestamp, 1, 13) AS bucket, COUNT(*) "
        "FROM events WHERE timestamp >= ? AND event_type = 'task.created' "
        "GROUP BY bucket ORDER BY bucket",
        (since_iso,),
    )

    # 2. Tasks accepted per hour (proxy for in-execution activity)
    accepted = await _fetch_buckets(
        "SELECT substr(timestamp, 1, 13) AS bucket, COUNT(*) "
        "FROM events WHERE timestamp >= ? AND event_type = 'task.accepted' "
        "GROUP BY bucket ORDER BY bucket",
        (since_iso,),
    )

    # 3a. Approvals per hour
    approved = await _fetch_buckets(
        "SELECT substr(timestamp, 1, 13) AS bucket, COUNT(*) "
        "FROM events WHERE timestamp >= ? "
        "AND event_type IN ('task.approved', 'task.auto_approved') "
        "GROUP BY bucket ORDER BY bucket",
        (since_iso,),
    )

    # 3b. All terminal events per hour (for completion rate denominator)
    terminal = await _fetch_buckets(
        "SELECT substr(timestamp, 1, 13) AS bucket, COUNT(*) "
        "FROM events WHERE timestamp >= ? "
        "AND event_type IN ('task.approved', 'task.auto_approved', "
        "'task.disputed', 'task.cancelled', 'task.expired') "
        "GROUP BY bucket ORDER BY bucket",
        (since_iso,),
    )

    # 4. Disputes filed per hour
    disputed = await _fetch_buckets(
        "SELECT substr(timestamp, 1, 13) AS bucket, COUNT(*) "
        "FROM events WHERE timestamp >= ? AND event_type = 'task.disputed' "
        "GROUP BY bucket ORDER BY bucket",
        (since_iso,),
    )

    # 5. Escrow locked amount per hour
    escrow = await _fetch_buckets(
        "SELECT substr(timestamp, 1, 13) AS bucket, "
        "COALESCE(SUM(CAST(json_extract(payload, '$.amount') AS REAL)), 0) "
        "FROM events WHERE timestamp >= ? AND event_type = 'escrow.locked' "
        "GROUP BY bucket ORDER BY bucket",
        (since_iso,),
    )

    # 6. Bids submitted per hour
    bids = await _fetch_buckets(
        "SELECT substr(timestamp, 1, 13) AS bucket, COUNT(*) "
        "FROM events WHERE timestamp >= ? AND event_type = 'bid.submitted' "
        "GROUP BY bucket ORDER BY bucket",
        (since_iso,),
    )

    # 7. Average reward of tasks created per hour
    avg_reward = await _fetch_buckets(
        "SELECT substr(timestamp, 1, 13) AS bucket, "
        "COALESCE(AVG(CAST(json_extract(payload, '$.reward') AS REAL)), 0) "
        "FROM events WHERE timestamp >= ? AND event_type = 'task.created' "
        "GROUP BY bucket ORDER BY bucket",
        (since_iso,),
    )

    # 8. Spec quality feedback events per hour
    spec_quality = await _fetch_buckets(
        "SELECT substr(timestamp, 1, 13) AS bucket, COUNT(*) "
        "FROM events WHERE timestamp >= ? "
        "AND event_type = 'feedback.revealed' "
        "AND json_extract(payload, '$.category') = 'spec_quality' "
        "GROUP BY bucket ORDER BY bucket",
        (since_iso,),
    )

    # 9. Agent registrations — cumulative (need baseline before window)
    reg_per_bucket = await _fetch_buckets(
        "SELECT substr(timestamp, 1, 13) AS bucket, COUNT(*) "
        "FROM events WHERE timestamp >= ? AND event_type = 'agent.registered' "
        "GROUP BY bucket ORDER BY bucket",
        (since_iso,),
    )

    baseline = int(
        await execute_scalar(
            db,
            "SELECT COUNT(*) FROM events WHERE timestamp < ? AND event_type = 'agent.registered'",
            (since_iso,),
        )
        or 0
    )

    reg_series = _to_series(reg_per_bucket)
    running = float(baseline)
    registered_cumulative: list[float] = []
    for val in reg_series:
        running += val
        registered_cumulative.append(running)

    # 10. Unemployment rate — cumulative point-in-time state
    # +1 when an agent starts working (task.accepted has worker_id in payload)
    # -1 when an agent stops working (task.approved/auto_approved/disputed has worker_id)
    work_start_per_bucket = await _fetch_buckets(
        "SELECT substr(timestamp, 1, 13) AS bucket, COUNT(*) "
        "FROM events WHERE timestamp >= ? AND event_type = 'task.accepted' "
        "GROUP BY bucket ORDER BY bucket",
        (since_iso,),
    )

    work_stop_per_bucket = await _fetch_buckets(
        "SELECT substr(timestamp, 1, 13) AS bucket, COUNT(*) "
        "FROM events WHERE timestamp >= ? "
        "AND event_type IN ('task.approved', 'task.auto_approved', 'task.disputed') "
        "GROUP BY bucket ORDER BY bucket",
        (since_iso,),
    )

    # Baseline: agents working before the window starts
    working_baseline = int(
        await execute_scalar(
            db,
            "SELECT ("
            "  (SELECT COUNT(*) FROM events WHERE timestamp < ? "
            "   AND event_type = 'task.accepted') - "
            "  (SELECT COUNT(*) FROM events WHERE timestamp < ? "
            "   AND event_type IN ('task.approved', 'task.auto_approved', 'task.disputed'))"
            ")",
            (since_iso, since_iso),
        )
        or 0
    )
    # Clamp baseline to non-negative (data may be inconsistent)
    working_baseline = max(working_baseline, 0)

    work_start_series = _to_series(work_start_per_bucket)
    work_stop_series = _to_series(work_stop_per_bucket)

    unemployment_rate: list[float] = []
    working_running = float(working_baseline)
    for i in range(len(buckets)):
        working_running += work_start_series[i] - work_stop_series[i]
        working_running = max(working_running, 0.0)  # Clamp
        reg = registered_cumulative[i]
        rate = max(0.0, min(1.0, (reg - working_running) / reg)) if reg > 0 else 0.0
        unemployment_rate.append(round(rate, 3))

    # Completion rate per bucket: approved / terminal (or 0 if no terminal)
    completion_rate: list[float] = []
    for bucket in buckets:
        t = terminal.get(bucket, 0.0)
        a = approved.get(bucket, 0.0)
        completion_rate.append(round(a / t, 3) if t > 0 else 0.0)

    return {
        "window": window,
        "buckets": buckets,
        "metrics": {
            "open_tasks": _to_series(created),
            "in_execution": _to_series(accepted),
            "completion_rate": completion_rate,
            "disputes_active": _to_series(disputed),
            "escrow_locked": _to_series(escrow),
            "avg_bids_per_task": _to_series(bids),
            "avg_reward": _to_series(avg_reward),
            "spec_quality": _to_series(spec_quality),
            "registered_agents": registered_cumulative,
            "unemployment_rate": unemployment_rate,
        },
    }
