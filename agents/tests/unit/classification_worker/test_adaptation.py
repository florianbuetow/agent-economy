"""Outcome-based adaptation: bid price and bid/abstain decisions move with recorded outcomes."""

from __future__ import annotations

import pytest

from classification_worker.adaptation import (
    OutcomeRecord,
    PricingRule,
    adapt,
    outcome_records,
)

RULE = PricingRule(
    initial_bid_pct=80,
    min_bid_pct=50,
    max_bid_pct=100,
    raise_step_pct=5,
    lower_step_pct=10,
    min_samples=3,
    min_approval_pct=50,
)


def _rec(seq: int, outcome: str, task_type: str = "sentiment") -> OutcomeRecord:
    return OutcomeRecord(task_type=task_type, outcome=outcome, seq=seq, bid_amount=10, payout=10)


@pytest.mark.unit
class TestAdaptationRule:
    def test_no_history_uses_initial_price_and_bids(self) -> None:
        state = adapt(RULE, [], "sentiment")
        assert state.bid_pct == 80
        assert state.bidding
        assert state.bid_amount(reward=50) == 40

    def test_approvals_raise_the_price_up_to_the_cap(self) -> None:
        records = [_rec(i, "approved") for i in range(1, 6)]
        assert adapt(RULE, records[:1], "sentiment").bid_pct == 85
        assert adapt(RULE, records, "sentiment").bid_pct == 100

    def test_lost_auctions_lower_the_price_down_to_the_floor(self) -> None:
        records = [_rec(i, "bid_timeout") for i in range(1, 6)]
        assert adapt(RULE, records[:1], "sentiment").bid_pct == 70
        assert adapt(RULE, records, "sentiment").bid_pct == 50

    def test_outcomes_apply_in_recorded_order(self) -> None:
        records = [_rec(2, "approved"), _rec(1, "bid_timeout")]
        # 80 -10 (seq 1) = 70, +5 (seq 2) = 75; applying in list order would give 75 too,
        # so use a cap-sensitive sequence to prove ordering.
        capped = [_rec(2, "bid_timeout"), _rec(1, "approved")]
        rule = PricingRule(**{**RULE.__dict__, "initial_bid_pct": 100})
        assert adapt(rule, capped, "sentiment").bid_pct == 90  # 100 (+5 capped) then -10
        assert adapt(RULE, records, "sentiment").bid_pct == 75

    def test_lost_disputes_stop_bidding_after_min_samples(self) -> None:
        records = [_rec(1, "disputed_lost"), _rec(2, "disputed_lost")]
        assert adapt(RULE, records, "sentiment").bidding  # below min_samples
        records.append(_rec(3, "approved"))
        state = adapt(RULE, records, "sentiment")
        assert state.decided == 3
        assert state.successes == 1
        assert not state.bidding

    def test_task_types_adapt_independently(self) -> None:
        records = [_rec(i, "disputed_lost", "ticket_routing") for i in range(1, 4)]
        records.append(_rec(4, "approved", "sentiment"))
        assert not adapt(RULE, records, "ticket_routing").bidding
        sentiment = adapt(RULE, records, "sentiment")
        assert sentiment.bidding
        assert sentiment.bid_pct == 85

    def test_bid_amount_is_at_least_one_and_at_most_reward(self) -> None:
        state = adapt(RULE, [], "sentiment")
        assert state.bid_amount(reward=1) == 1
        assert state.bid_amount(reward=7) == 5

    def test_invalid_rule_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="bid_pct"):
            PricingRule(**{**RULE.__dict__, "min_bid_pct": 90, "initial_bid_pct": 80})


@pytest.mark.unit
class TestOutcomeRecordsFromJournal:
    def test_only_finished_typed_jobs_become_records(self) -> None:
        jobs = (
            (
                "t1",
                {
                    "outcome": "approved",
                    "task_type": "sentiment",
                    "outcome_seq": 2,
                    "bid_amount": 40,
                    "payout": 40,
                },
            ),
            ("t2", {"outcome": None, "task_type": "sentiment"}),
            ("t3", {"outcome": "approved", "bid_amount": 1, "payout": 1}),
        )
        records = outcome_records(jobs)
        assert records == [
            OutcomeRecord(
                task_type="sentiment", outcome="approved", seq=2, bid_amount=40, payout=40
            )
        ]
