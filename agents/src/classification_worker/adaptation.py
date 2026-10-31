"""Outcome-based adaptation of bid price and bid/abstain decisions per task type.

The state is never stored separately: it is a deterministic fold over the
terminal outcomes the worker already persists in its durable job journal
(``task_type``, ``outcome``, ``outcome_seq``), so it survives restarts and
cannot drift from the recorded evidence.

Rule, applied per task type in ``outcome_seq`` order:

* ``approved`` / ``disputed_won``: the work was paid; raise the bid by
  ``raise_step_pct`` (capped at ``max_bid_pct``).
* ``bid_timeout``: another bidder won the auction; lower the bid by
  ``lower_step_pct`` (floored at ``min_bid_pct``).
* ``disputed_lost``: a quality failure; the price is unchanged, but it counts
  against the approval rate.
* Once at least ``min_samples`` decided outcomes exist and the success rate
  is below ``min_approval_pct``, the worker stops bidding on that type.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

SUCCESS_OUTCOMES = frozenset({"approved", "disputed_won"})
DECIDED_OUTCOMES = SUCCESS_OUTCOMES | {"disputed_lost"}
LOST_AUCTION = "bid_timeout"


@dataclass(frozen=True)
class PricingRule:
    """Configured adaptation parameters (integer percentages of the task reward)."""

    initial_bid_pct: int
    min_bid_pct: int
    max_bid_pct: int
    raise_step_pct: int
    lower_step_pct: int
    min_samples: int
    min_approval_pct: int

    def __post_init__(self) -> None:
        if not 1 <= self.min_bid_pct <= self.initial_bid_pct <= self.max_bid_pct <= 100:
            msg = "bid_pct values must satisfy 1 <= min <= initial <= max <= 100"
            raise ValueError(msg)
        if self.raise_step_pct < 0 or self.lower_step_pct < 0:
            raise ValueError("step percentages must be nonnegative")
        if self.min_samples < 1 or not 0 <= self.min_approval_pct <= 100:
            raise ValueError("min_samples must be positive and min_approval_pct in 0..100")


@dataclass(frozen=True)
class OutcomeRecord:
    """One terminal job outcome read back from the journal."""

    task_type: str
    outcome: str
    seq: int
    bid_amount: int
    payout: int


@dataclass(frozen=True)
class TypeState:
    """The adapted pricing state for one task type."""

    task_type: str
    bid_pct: int
    bidding: bool
    decided: int
    successes: int
    lost_auctions: int

    def bid_amount(self, *, reward: int) -> int:
        """Integer bid for a reward: ``reward * bid_pct // 100``, clamped to ``1..reward``."""
        return max(1, min(reward, reward * self.bid_pct // 100))


def outcome_records(jobs: Iterable[tuple[str, Mapping[str, Any]]]) -> list[OutcomeRecord]:
    """Extract typed terminal outcomes from ``WorkerJobJournal.all()`` rows."""
    records: list[OutcomeRecord] = []
    for _task_id, job in jobs:
        outcome = job.get("outcome")
        task_type = job.get("task_type")
        seq = job.get("outcome_seq")
        if not isinstance(outcome, str) or not isinstance(task_type, str) or type(seq) is not int:
            continue
        bid_amount = job.get("bid_amount")
        payout = job.get("payout")
        records.append(
            OutcomeRecord(
                task_type=task_type,
                outcome=outcome,
                seq=seq,
                bid_amount=bid_amount if type(bid_amount) is int else 0,
                payout=payout if type(payout) is int else 0,
            )
        )
    return records


def adapt(rule: PricingRule, records: Iterable[OutcomeRecord], task_type: str) -> TypeState:
    """Fold the recorded outcomes of one task type into its current pricing state."""
    pct = rule.initial_bid_pct
    decided = successes = lost = 0
    for record in sorted(records, key=lambda item: item.seq):
        if record.task_type != task_type:
            continue
        if record.outcome in SUCCESS_OUTCOMES:
            pct = min(rule.max_bid_pct, pct + rule.raise_step_pct)
            successes += 1
            decided += 1
        elif record.outcome == "disputed_lost":
            decided += 1
        elif record.outcome == LOST_AUCTION:
            pct = max(rule.min_bid_pct, pct - rule.lower_step_pct)
            lost += 1
    bidding = decided < rule.min_samples or successes * 100 >= rule.min_approval_pct * decided
    return TypeState(
        task_type=task_type,
        bid_pct=pct,
        bidding=bidding,
        decided=decided,
        successes=successes,
        lost_auctions=lost,
    )
