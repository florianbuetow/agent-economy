"""Accepted-bid settlement terms (G01, Q-M2).

The posted reward is the escrowed budget. The accepted bid is the price. Approval pays
the price; a Court ruling pays floor(price * worker_pct / 100). The poster receives the
rest of the original escrow in the same Bank operation.
"""

from __future__ import annotations


def ruling_worker_amount(accepted_price: int, worker_pct: int) -> int:
    """Return the whole-credit Court award on the accepted price, rounded down."""
    return accepted_price * worker_pct // 100


def settlement_request_fields(
    *,
    escrow_id: str,
    poster_id: str,
    worker_id: str | None,
    escrow_amount: int,
    worker_amount: int,
    source_reference: str,
) -> tuple[str, dict[str, object]]:
    """Choose the exact Bank primitive for one final settlement.

    One-sided outcomes stay a full release. Every two-sided outcome is an exact-amount
    escrow_split that pays ``worker_amount`` and refunds ``escrow_amount - worker_amount``.
    """
    if not 0 <= worker_amount <= escrow_amount:
        raise ValueError("Worker amount must fit the escrowed budget")
    if worker_amount == 0 or worker_amount == escrow_amount:
        recipient = poster_id if worker_amount == 0 else worker_id
        if recipient is None:
            raise ValueError("A worker payment requires an accepted worker")
        return "escrow_release", {
            "escrow_id": escrow_id,
            "recipient_account_id": recipient,
            "source_reference": source_reference,
        }
    if worker_id is None:
        raise ValueError("A worker payment requires an accepted worker")
    return "escrow_split", {
        "escrow_id": escrow_id,
        "worker_account_id": worker_id,
        "poster_account_id": poster_id,
        "worker_amount": worker_amount,
        "source_reference": source_reference,
    }


__all__ = ["ruling_worker_amount", "settlement_request_fields"]
