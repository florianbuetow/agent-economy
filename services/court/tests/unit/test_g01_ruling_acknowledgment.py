"""G01: Court accepts a ruling acknowledgment priced on the accepted bid."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest
from service_commons.exceptions import ServiceError

from court_service.services.ruling_acknowledgment import validate_ruling_acknowledgment

pytestmark = pytest.mark.unit

_OP = "op-00000000-0000-4000-8000-000000000001"
_TASK = "t-g01"
_RULING = "clm-g01"


def _hash(value: dict[str, Any]) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _ack(financial: dict[str, Any], effect_kind: str, priced: dict[str, int]) -> dict[str, Any]:
    financial = {
        "request_version": 1,
        "escrow_id": "esc-g01",
        "source_reference": f"task:{_TASK}:ruling:{_RULING}",
        **financial,
    }
    return {
        "task_id": _TASK,
        "operation": {
            "protocol_version": 1,
            "operation_id": _OP,
            "task_id": _TASK,
            "command": "record_ruling",
            "request_sha256": "a" * 64,
            "operation_state": "completed",
            "source_status": "disputed",
            "source_lifecycle_version": 3,
            "target_status": "ruled",
            "target_lifecycle_version": 4,
            "effect_kind": effect_kind,
            "effect_state": "succeeded",
            "effect_operation_id": _OP,
            "effect_result_reference": _OP,
            "effect_intent": {
                "financial_request": financial,
                "financial_request_sha256": _hash(financial),
                "expected_escrow": {
                    "escrow_id": "esc-g01",
                    "task_id": _TASK,
                    "payer_account_id": "a-poster",
                    "amount": 100,
                },
            },
            "event_id": 9,
            "result": {
                "task_id": _TASK,
                "command": "record_ruling",
                "status": "ruled",
                "lifecycle_version": 4,
                "event_id": 9,
                "effect_result_reference": _OP,
                "worker_id": "a-worker",
                "ruling_id": _RULING,
                "worker_pct": 50,
                "summary": "Split",
                "reward": 100,
                **priced,
            },
        },
    }


def _validate(response: dict[str, Any]) -> None:
    validate_ruling_acknowledgment(
        response,
        operation_id=_OP,
        task_id=_TASK,
        source_version=3,
        ruling_id=_RULING,
        poster_id="a-poster",
        worker_id="a-worker",
        worker_pct=50,
        ruling_summary="Split",
        reward=100,
    )


_SETTLE = {
    "operation_kind": "escrow_split",
    "worker_account_id": "a-worker",
    "poster_account_id": "a-poster",
}


def test_price_61_at_50_percent_settles_30_and_refunds_70() -> None:
    """The award applies to the accepted price, not the posted budget."""
    _validate(
        _ack(
            {**_SETTLE, "worker_amount": 30},
            "bank_split",
            {"accepted_price": 61, "worker_payment": 30, "poster_refund": 70},
        )
    )


def test_price_equal_to_budget_keeps_the_equivalent_percentage_split() -> None:
    """A whole-budget price yields the same legs as the historical percentage split."""
    _validate(
        _ack(
            {**_SETTLE, "worker_pct": 50},
            "bank_split",
            {"accepted_price": 100, "worker_payment": 50, "poster_refund": 50},
        )
    )
    _validate(_ack({**_SETTLE, "worker_pct": 50}, "bank_split", {}))


def test_budget_based_payment_on_a_lower_price_is_rejected() -> None:
    """Paying 50% of the budget when the price was 61 is not a valid acknowledgment."""
    with pytest.raises(ServiceError) as exc_info:
        _validate(
            _ack(
                {**_SETTLE, "worker_pct": 50},
                "bank_split",
                {"accepted_price": 61, "worker_payment": 30, "poster_refund": 70},
            )
        )
    assert exc_info.value.error == "invalid_task_board_ack"
