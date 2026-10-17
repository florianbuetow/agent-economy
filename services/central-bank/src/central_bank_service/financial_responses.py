"""Strict validation for authoritative Gateway financial envelopes."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from central_bank_service.monetary import SQLITE_MAX_INTEGER

if TYPE_CHECKING:
    from collections.abc import Mapping


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class FinancialLeg(_StrictModel):
    role: Literal["account", "payer", "recipient", "worker", "poster"]
    account_id: str
    direction: Literal["debit", "credit"]
    amount: int
    transaction_id: str | None
    balance_after: int | None


class EscrowProjection(_StrictModel):
    escrow_id: str
    payer_account_id: str
    task_id: str
    amount: int = Field(ge=1, le=SQLITE_MAX_INTEGER)
    status: Literal["locked", "released", "split"]
    created_at: str
    resolved_at: str | None


class AccountProjection(_StrictModel):
    account_id: str = Field(min_length=1)
    balance: int = Field(ge=0, le=SQLITE_MAX_INTEGER)
    created_at: str = Field(min_length=1)


class NativeReceipt(_StrictModel):
    receipt_version: Literal[1]
    operation_id: str = Field(
        pattern=r"^op-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    )
    operation_kind: Literal[
        "account_issuance", "credit", "escrow_lock", "escrow_release", "escrow_split"
    ]
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_reference: str = Field(min_length=1)
    committed_at: str = Field(min_length=1)
    event_id: int
    escrow_id: str | None
    result: dict[str, object]
    legs: list[FinancialLeg]


class NativeFinancialEnvelope(_StrictModel):
    outcome: Literal["native_receipt"]
    receipt: NativeReceipt
    current_escrow: EscrowProjection | None


_RESULT_KEYS = {
    "account_issuance": {"account_id", "issued_amount", "balance_after"},
    "credit": {"account_id", "amount", "balance_after", "reference"},
    "escrow_lock": {"escrow_status_at_commit"},
    "escrow_release": {"escrow_status_at_commit"},
    "escrow_split": {"escrow_status_at_commit", "worker_pct"},
}


def validate_operation_id(value: object) -> str:
    if not isinstance(value, str) or len(value) != 39 or not value.startswith("op-"):
        raise ValueError("operation_id must be a canonical op-UUID string")
    try:
        parsed = UUID(value[3:])
    except ValueError as exc:
        raise ValueError("operation_id must be a canonical op-UUID string") from exc
    if value != f"op-{parsed}":
        raise ValueError("operation_id must be a canonical op-UUID string")
    return value


def parse_account_projection(value: object, expected_account_id: str) -> dict[str, object]:
    try:
        account = AccountProjection.model_validate(value)
    except ValidationError as exc:
        raise RuntimeError("Gateway returned an invalid account projection") from exc
    if account.account_id != expected_account_id:
        raise RuntimeError("Gateway returned a mismatched account projection")
    return account.model_dump(mode="json")


def _canonical_hash(operation_kind: str, fields: Mapping[str, object]) -> str:
    payload = {"request_version": 1, "operation_kind": operation_kind, **fields}
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _strict_result(receipt: NativeReceipt) -> None:
    result = receipt.result
    kind = receipt.operation_kind
    if kind == "account_issuance":
        valid = (
            isinstance(result["account_id"], str)
            and bool(result["account_id"])
            and type(result["issued_amount"]) is int
            and type(result["balance_after"]) is int
        )
    elif kind == "credit":
        valid = (
            isinstance(result["account_id"], str)
            and bool(result["account_id"])
            and isinstance(result["reference"], str)
            and bool(result["reference"])
            and type(result["amount"]) is int
            and type(result["balance_after"]) is int
        )
    elif kind == "escrow_split":
        valid = (
            result["escrow_status_at_commit"] == "split"
            and type(result["worker_pct"]) is int
            and 0 <= result["worker_pct"] <= 100
        )
    else:
        expected_status = "locked" if kind == "escrow_lock" else "released"
        valid = result["escrow_status_at_commit"] == expected_status
    if not valid:
        raise RuntimeError("Gateway returned an invalid native financial result")


def parse_native_financial_envelope(
    value: object,
    expected_operation_id: str,
    expected_operation_kind: str,
    expected_business_fields: Mapping[str, object],
) -> dict[str, object]:
    """Reject malformed, coerced, unexpected, or mismatched Gateway success bodies."""
    try:
        envelope = NativeFinancialEnvelope.model_validate(value)
    except ValidationError as exc:
        raise RuntimeError("Gateway returned an invalid native financial envelope") from exc
    receipt = envelope.receipt
    if (
        receipt.operation_id != expected_operation_id
        or receipt.operation_kind != expected_operation_kind
        or receipt.request_sha256
        != _canonical_hash(expected_operation_kind, expected_business_fields)
        or set(receipt.result) != _RESULT_KEYS[receipt.operation_kind]
    ):
        raise RuntimeError("Gateway returned a mismatched native financial receipt")
    _strict_result(receipt)
    if receipt.operation_kind in {"account_issuance", "credit"}:
        if receipt.escrow_id is not None or envelope.current_escrow is not None:
            raise RuntimeError("Gateway returned an invalid account receipt projection")
    elif receipt.escrow_id is None or envelope.current_escrow is None:
        raise RuntimeError("Gateway returned an invalid escrow receipt projection")
    elif envelope.current_escrow.escrow_id != receipt.escrow_id:
        raise RuntimeError("Gateway returned a mismatched escrow projection")
    _validate_business_semantics(receipt, envelope.current_escrow, expected_business_fields)
    return envelope.model_dump(mode="json")


def _validate_business_semantics(
    receipt: NativeReceipt,
    projection: EscrowProjection | None,
    fields: Mapping[str, object],
) -> None:
    legs = receipt.legs
    kind = receipt.operation_kind
    if kind == "account_issuance":
        expected_amount = fields["amount"]
        valid = (
            receipt.source_reference == "initial_balance"
            and receipt.result["account_id"] == fields["account_id"]
            and receipt.result["issued_amount"] == expected_amount
            and len(legs) == 1
            and _leg_matches(legs[0], "account", fields["account_id"], "credit", expected_amount)
            and receipt.result["balance_after"] == legs[0].balance_after
        )
    elif kind == "credit":
        expected_amount = fields["amount"]
        valid = (
            receipt.source_reference == fields["reference"]
            and receipt.result["account_id"] == fields["account_id"]
            and receipt.result["amount"] == expected_amount
            and receipt.result["reference"] == fields["reference"]
            and len(legs) == 1
            and _leg_matches(legs[0], "account", fields["account_id"], "credit", expected_amount)
            and receipt.result["balance_after"] == legs[0].balance_after
        )
    elif kind == "escrow_lock":
        terminal_time_valid = projection is not None and (
            (projection.status == "locked" and projection.resolved_at is None)
            or (projection.status in {"released", "split"} and projection.resolved_at is not None)
        )
        valid = (
            terminal_time_valid
            and projection is not None
            and receipt.source_reference == fields["task_id"]
            and projection.payer_account_id == fields["payer_account_id"]
            and projection.task_id == fields["task_id"]
            and projection.amount == fields["amount"]
            and len(legs) == 1
            and _leg_matches(
                legs[0], "payer", fields["payer_account_id"], "debit", fields["amount"]
            )
        )
    elif kind == "escrow_release":
        valid = (
            projection is not None
            and projection.status == "released"
            and projection.resolved_at is not None
            and receipt.escrow_id == fields["escrow_id"]
            and receipt.source_reference == fields["source_reference"]
            and len(legs) == 1
            and _leg_matches(
                legs[0], "recipient", fields["recipient_account_id"], "credit", projection.amount
            )
        )
    else:
        worker_pct = fields["worker_pct"]
        if type(worker_pct) is not int:
            raise RuntimeError("Gateway returned a receipt inconsistent with the requested command")
        worker_amount = 0 if projection is None else projection.amount * worker_pct // 100
        poster_amount = 0 if projection is None else projection.amount - worker_amount
        valid = (
            projection is not None
            and projection.status == "split"
            and projection.resolved_at is not None
            and receipt.escrow_id == fields["escrow_id"]
            and receipt.source_reference == fields["source_reference"]
            and projection.payer_account_id == fields["poster_account_id"]
            and receipt.result["worker_pct"] == fields["worker_pct"]
            and len(legs) == 2
            and _leg_matches(
                legs[0], "worker", fields["worker_account_id"], "credit", worker_amount
            )
            and _leg_matches(
                legs[1], "poster", fields["poster_account_id"], "credit", poster_amount
            )
        )
    if not valid:
        raise RuntimeError("Gateway returned a receipt inconsistent with the requested command")


def _leg_matches(
    leg: FinancialLeg,
    role: str,
    account_id: object,
    direction: str,
    amount: object,
) -> bool:
    if type(amount) is not int or not 0 <= amount <= SQLITE_MAX_INTEGER:
        return False
    identity_matches = (
        leg.role == role
        and leg.account_id == account_id
        and leg.direction == direction
        and leg.amount == amount
    )
    if amount == 0:
        return identity_matches and leg.transaction_id is None and leg.balance_after is None
    return (
        identity_matches
        and isinstance(leg.transaction_id, str)
        and bool(leg.transaction_id)
        and type(leg.balance_after) is int
        and 0 <= leg.balance_after <= SQLITE_MAX_INTEGER
    )
