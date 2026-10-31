"""Authoritative persisted financial proof for native task publication."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, cast

from db_gateway_service.financial_requests import canonical_request
from db_gateway_service.schema import SQLITE_MAX_INTEGER
from db_gateway_service.task_time import parse_utc_text_to_epoch_us

if TYPE_CHECKING:
    import sqlite3

    from db_gateway_service.financial_requests import FinancialRequest
    from db_gateway_service.task_publication_requests import TaskPublicationRequest

PublicationBlockReason = Literal[
    "receipt_invalid",
    "escrow_invalid",
    "settlement_conflict",
    "publication_conflict",
    "clock_out_of_range",
]

_LOCK_RESULT_JSON: Final[str] = '{"escrow_status_at_commit":"locked"}'


class PublicationEvidenceError(Exception):
    """A durable contradiction that must block a publication intent."""

    def __init__(self, reason: PublicationBlockReason) -> None:
        self.reason: PublicationBlockReason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class PublicationFinancialProof:
    """The exact lock facts frozen onto a successfully published intent."""

    escrow_id: str
    request_sha256: str
    receipt_event_id: int
    committed_at: str


def _block(reason: PublicationBlockReason) -> None:
    raise PublicationEvidenceError(reason)


def _valid_timestamp(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parse_utc_text_to_epoch_us(value)
    except ValueError:
        return False
    return True


def _receipt_proof(
    receipt: sqlite3.Row | tuple[object, ...],
    expected: FinancialRequest,
    task_id: str,
) -> tuple[str, int, str]:
    escrow_id = receipt[7]
    event_id = receipt[6]
    if (
        type(receipt[0]) is not int
        or receipt[0] != 1
        or receipt[1] != "escrow_lock"
        or receipt[2] != expected.canonical_json
        or receipt[3] != expected.request_sha256
        or receipt[4] != task_id
        or not _valid_timestamp(receipt[5])
        or not isinstance(event_id, int)
        or isinstance(event_id, bool)
        or not 1 <= event_id <= SQLITE_MAX_INTEGER
        or not isinstance(escrow_id, str)
        or escrow_id == ""
        or receipt[8] != _LOCK_RESULT_JSON
    ):
        _block("receipt_invalid")
    return cast("str", escrow_id), cast("int", event_id), str(receipt[5])


def _verify_escrow(
    cursor: sqlite3.Cursor,
    *,
    payer_account_id: str,
    task_id: str,
    amount: int,
    escrow_id: str,
    event_id: int,
    committed_at: str,
    require_locked: bool,
) -> None:
    escrow = cursor.execute(
        "SELECT payer_account_id,amount,task_id,status,created_at,resolved_at,event_id "
        "FROM bank_escrow WHERE escrow_id=?",
        (escrow_id,),
    ).fetchone()
    if escrow is None:
        _block("escrow_invalid")
    assert escrow is not None
    if (
        escrow[0] != payer_account_id
        or type(escrow[1]) is not int
        or escrow[1] != amount
        or escrow[2] != task_id
        or escrow[4] != committed_at
        or type(escrow[6]) is not int
        or escrow[6] != event_id
    ):
        _block("escrow_invalid")
    settlements = cursor.execute(
        "SELECT operation_kind,committed_at FROM bank_operation_receipts "
        "WHERE escrow_id=? AND operation_kind IN ('escrow_release','escrow_split')",
        (escrow_id,),
    ).fetchall()
    if require_locked:
        if escrow[3] != "locked" or escrow[5] is not None or settlements:
            _block("settlement_conflict")
        return
    expected_settlement = {"released": "escrow_release", "split": "escrow_split"}.get(
        str(escrow[3])
    )
    if escrow[3] == "locked":
        coherent = escrow[5] is None and not settlements
    else:
        coherent = (
            expected_settlement is not None
            and _valid_timestamp(escrow[5])
            and len(settlements) == 1
            and settlements[0][0] == expected_settlement
            and settlements[0][1] == escrow[5]
        )
    if not coherent:
        _block("settlement_conflict")


def _verify_leg_transaction_event(
    cursor: sqlite3.Cursor,
    *,
    operation_id: str,
    payer_account_id: str,
    task_id: str,
    amount: int,
    event_id: int,
    committed_at: str,
) -> None:
    legs = cursor.execute(
        "SELECT leg_index,role,account_id,direction,amount,transaction_id,balance_after "
        "FROM bank_operation_legs WHERE operation_id=? ORDER BY leg_index",
        (operation_id,),
    ).fetchall()
    if len(legs) != 1:
        _block("receipt_invalid")
    leg = legs[0]
    if (
        type(leg[0]) is not int
        or leg[0] != 0
        or leg[1] != "payer"
        or leg[2] != payer_account_id
        or leg[3] != "debit"
        or type(leg[4]) is not int
        or leg[4] != amount
        or not isinstance(leg[5], str)
        or leg[5] == ""
        or type(leg[6]) is not int
        or not 0 <= leg[6] <= SQLITE_MAX_INTEGER
    ):
        _block("receipt_invalid")
    transaction = cursor.execute(
        "SELECT account_id,type,amount,balance_after,reference,event_id,timestamp "
        "FROM bank_transactions WHERE tx_id=?",
        (leg[5],),
    ).fetchone()
    if transaction is None or (
        transaction[0] != payer_account_id
        or transaction[1] != "escrow_lock"
        or type(transaction[2]) is not int
        or transaction[2] != amount
        or type(transaction[3]) is not int
        or transaction[3] != leg[6]
        or transaction[4] != task_id
        or type(transaction[5]) is not int
        or transaction[5] != event_id
        or transaction[6] != committed_at
    ):
        _block("receipt_invalid")
    event = cursor.execute(
        "SELECT event_source,event_type,timestamp,task_id,agent_id FROM events WHERE event_id=?",
        (event_id,),
    ).fetchone()
    if event is None or (
        event[0] != "central-bank"
        or event[1] != "escrow.locked"
        or event[2] != committed_at
        or event[3] not in {None, task_id}
        or event[4] != payer_account_id
    ):
        _block("receipt_invalid")


def inspect_lock_facts(
    cursor: sqlite3.Cursor,
    *,
    operation_id: str,
    payer_account_id: str,
    task_id: str,
    amount: int,
    require_locked: bool,
) -> PublicationFinancialProof | None:
    """Validate one original lock without inventing publication semantics."""
    expected = canonical_request(
        "escrow_lock",
        {
            "payer_account_id": payer_account_id,
            "task_id": task_id,
            "amount": amount,
        },
    )
    receipt = cursor.execute(
        "SELECT receipt_version,operation_kind,canonical_request_json,request_sha256,"
        "source_reference,committed_at,event_id,escrow_id,result_json "
        "FROM bank_operation_receipts WHERE operation_id=?",
        (operation_id,),
    ).fetchone()
    if receipt is None:
        return None

    escrow_id, event_id, committed_at = _receipt_proof(receipt, expected, task_id)
    _verify_leg_transaction_event(
        cursor,
        operation_id=operation_id,
        payer_account_id=payer_account_id,
        task_id=task_id,
        amount=amount,
        event_id=event_id,
        committed_at=committed_at,
    )
    _verify_escrow(
        cursor,
        payer_account_id=payer_account_id,
        task_id=task_id,
        amount=amount,
        escrow_id=escrow_id,
        event_id=event_id,
        committed_at=committed_at,
        require_locked=require_locked,
    )

    # The literal comparison above prevents permissive JSON equivalence. Decode
    # once as a final defensive check against an invalid stored object.
    try:
        result = json.loads(str(receipt[8]))
    except (TypeError, ValueError) as exc:
        raise PublicationEvidenceError("receipt_invalid") from exc
    if result != {"escrow_status_at_commit": "locked"}:
        _block("receipt_invalid")
    return PublicationFinancialProof(
        escrow_id=escrow_id,
        request_sha256=expected.request_sha256,
        receipt_event_id=event_id,
        committed_at=committed_at,
    )


def inspect_publication_lock(
    cursor: sqlite3.Cursor,
    request: TaskPublicationRequest,
) -> PublicationFinancialProof | None:
    """Require a currently locked proof for first native publication."""
    proof = inspect_lock_facts(
        cursor,
        operation_id=request.lock_operation_id,
        payer_account_id=request.actor_id,
        task_id=request.task_id,
        amount=request.reward,
        require_locked=True,
    )
    if proof is None:
        # An unrelated or legacy lock for this reserved task is contradictory,
        # while complete absence remains retryable because a request can arrive late.
        history = cursor.execute(
            "SELECT 1 FROM bank_escrow WHERE task_id=? LIMIT 1",
            (request.task_id,),
        ).fetchone()
        if history is not None:
            _block("receipt_invalid")
    return proof


__all__ = [
    "PublicationBlockReason",
    "PublicationEvidenceError",
    "PublicationFinancialProof",
    "inspect_lock_facts",
    "inspect_publication_lock",
]
