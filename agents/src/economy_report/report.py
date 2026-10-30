"""Collect recorded worker outcomes and compute the measured economy report.

Evidence sources, read-only:

* each worker's durable job journal under ``<runtime_state.directory>/identities``
  (bids, accepted prices, terminal outcomes, payouts, task types);
* Reputation's public ``GET /agents/{agent_id}/quality`` for quality scores.

Nothing here selects or favours a worker: every number is derived from what
the market recorded.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

from classification_worker.adaptation import (
    DECIDED_OUTCOMES,
    LOST_AUCTION,
    SUCCESS_OUTCOMES,
    adapt,
    outcome_records,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from pathlib import Path

    from classification_worker.adaptation import PricingRule

_JOB_DIRECTORY = "jobs"


@dataclass(frozen=True)
class WorkerJournal:
    """One worker identity's recorded jobs."""

    handle: str
    profile: str
    agent_id: str
    jobs: list[dict[str, Any]]


@dataclass(frozen=True)
class TypeSummary:
    """Per-task-type outcome counts and the adapted pricing state."""

    decided: int
    successes: int
    bid_pct: int
    bidding: bool


@dataclass(frozen=True)
class WorkerSummary:
    """The measured outcome of one worker."""

    handle: str
    agent_id: str
    bids: int
    selected: int
    in_flight: int
    lost_auctions: int
    decided: int
    successes: int
    cost_paid: int
    mean_price: float | None
    interval: tuple[float, float]
    by_type: dict[str, TypeSummary] = field(default_factory=dict)

    @property
    def approval_rate(self) -> float | None:
        return self.successes / self.decided if self.decided else None


def wilson_interval(successes: int, samples: int, *, z: float) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion; (0, 1) without samples."""
    if samples == 0:
        return (0.0, 1.0)
    p = successes / samples
    denom = 1 + z * z / samples
    center = (p + z * z / (2 * samples)) / denom
    half = z * math.sqrt(p * (1 - p) / samples + z * z / (4 * samples * samples)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def collect_workers(runtime_dir: Path, *, roles: Sequence[str]) -> list[WorkerJournal]:
    """Read every journal whose owner role is listed, without taking any lease."""
    identities = runtime_dir / "identities"
    if not identities.is_dir():
        return []
    found: list[WorkerJournal] = []
    for identity in sorted(identities.iterdir()):
        owner_path = identity / "owner.json"
        if not owner_path.is_file():
            continue
        owner = _read_json(owner_path)
        if not isinstance(owner, dict) or owner.get("role") not in roles:
            continue
        jobs: list[dict[str, Any]] = []
        job_dir = identity / _JOB_DIRECTORY
        if job_dir.is_dir():
            for path in sorted(job_dir.glob("*.json")):
                if path.name.startswith("."):
                    continue
                record = _read_json(path)
                if isinstance(record, dict) and isinstance(record.get("job"), dict):
                    jobs.append(record["job"])
        found.append(
            WorkerJournal(
                handle=str(owner["handle"]),
                profile=str(owner["profile"]),
                agent_id=str(owner["registered_agent_id"]),
                jobs=jobs,
            )
        )
    return found


def summarize(journal: WorkerJournal, *, rule: PricingRule, z: float) -> WorkerSummary:
    """Derive counts, approval rate with its interval, cost and adapted prices."""
    outcomes = [job.get("outcome") for job in journal.jobs]
    finished = [job for job in journal.jobs if isinstance(job.get("outcome"), str)]
    selected = [job for job in finished if job.get("outcome") != LOST_AUCTION]
    decided = sum(1 for outcome in outcomes if outcome in DECIDED_OUTCOMES)
    successes = sum(1 for outcome in outcomes if outcome in SUCCESS_OUTCOMES)
    prices = [job["bid_amount"] for job in selected if type(job.get("bid_amount")) is int]
    records = outcome_records((str(index), job) for index, job in enumerate(journal.jobs))
    by_type: dict[str, TypeSummary] = {}
    for task_type in sorted({record.task_type for record in records}):
        state = adapt(rule, records, task_type)
        by_type[task_type] = TypeSummary(
            decided=state.decided,
            successes=state.successes,
            bid_pct=state.bid_pct,
            bidding=state.bidding,
        )
    return WorkerSummary(
        handle=journal.handle,
        agent_id=journal.agent_id,
        bids=len(journal.jobs),
        selected=len(selected),
        in_flight=sum(1 for outcome in outcomes if outcome is None),
        lost_auctions=sum(1 for outcome in outcomes if outcome == LOST_AUCTION),
        decided=decided,
        successes=successes,
        cost_paid=sum(job["payout"] for job in finished if type(job.get("payout")) is int),
        mean_price=sum(prices) / len(prices) if prices else None,
        interval=wilson_interval(successes, decided, z=z),
        by_type=by_type,
    )


async def fetch_quality(
    client: httpx.AsyncClient, reputation_url: str, agent_id: str
) -> dict[str, Any] | str:
    """Reputation's quality projection for one agent, or why it is unavailable."""
    try:
        response = await client.get(f"{reputation_url}/agents/{agent_id}/quality")
    except httpx.HTTPError as exc:
        return f"unavailable ({type(exc).__name__})"
    if response.status_code != 200:
        return f"unavailable (HTTP {response.status_code})"
    body = response.json()
    return body if isinstance(body, dict) else f"unavailable (unexpected body {body!r})"


def _quality_text(quality: dict[str, Any] | str) -> str:
    """Render Reputation's quality-v1 dimensions; unknown shapes are shown verbatim."""
    if isinstance(quality, str):
        return quality
    parts: list[str] = []
    for dimension in ("delivery_quality", "specification_quality"):
        value = quality.get(dimension)
        if not isinstance(value, dict):
            continue
        score = value.get("score") if value.get("rated") else None
        shown = "unrated" if score is None else f"{score}/100"
        parts.append(f"{dimension} {shown} (n={value.get('task_count')})")
    return ", ".join(parts) if parts else json.dumps(quality)


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def render(
    summaries: Iterable[WorkerSummary],
    qualities: Mapping[str, dict[str, Any] | str],
    *,
    z: float,
) -> str:
    """Plain-text report; one block per worker."""
    lines = [f"Economy report (approval intervals: Wilson score, z={z})", ""]
    for s in summaries:
        low, high = s.interval
        mean = "n/a" if s.mean_price is None else f"{s.mean_price:.1f}"
        quality = qualities.get(s.agent_id, "not requested")
        quality_text = _quality_text(quality)
        lines += [
            f"{s.handle} ({s.agent_id})",
            f"  bids {s.bids}, selected {s.selected}, lost auctions {s.lost_auctions}, "
            f"in flight {s.in_flight}",
            f"  approved {s.successes}/{s.decided} = {_pct(s.approval_rate)} "
            f"[{low:.2f}, {high:.2f}]",
            f"  cost paid {s.cost_paid}, mean accepted price {mean}",
            f"  quality {quality_text}",
        ]
        for task_type, t in s.by_type.items():
            t_low, t_high = wilson_interval(t.successes, t.decided, z=z)
            lines.append(
                f"    {task_type}: {t.successes}/{t.decided} [{t_low:.2f}, {t_high:.2f}], "
                f"bid {t.bid_pct}% of reward, {'bidding' if t.bidding else 'abstaining'}"
            )
        lines.append("")
    if len(lines) == 2:
        lines.append("No worker journals found.")
    return "\n".join(lines)
