"""Exact-amount escrow settlement (G01 accepted-bid pricing).

The shared SDK only knows percentage splits, so the settlement Gateway call and its
receipt validation live here. The Gateway records the settlement as an
``escrow_split`` receipt whose canonical request carries ``worker_amount``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import ValidationError
from service_auth.financial_responses import NativeFinancialEnvelope
from service_clients.gateway import GatewayClient

_SETTLE_PATH = "/bank/escrow/settle"


class SettlementGatewayClient(GatewayClient):
    """Gateway client with the exact-amount settlement write."""

    async def escrow_settle(
        self,
        *,
        escrow_id: str,
        worker_account_id: str,
        poster_account_id: str,
        worker_tx_id: str,
        poster_tx_id: str,
        resolved_at: str,
        worker_amount: int,
        source_reference: str,
        operation_id: str,
    ) -> dict[str, Any]:
        """POST /bank/escrow/settle."""
        payload: dict[str, Any] = {
            "operation_id": operation_id,
            "escrow_id": escrow_id,
            "worker_account_id": worker_account_id,
            "poster_account_id": poster_account_id,
            "worker_amount": worker_amount,
            "source_reference": source_reference,
            "worker_tx_id": worker_tx_id,
            "poster_tx_id": poster_tx_id,
            "resolved_at": resolved_at,
            "event": self._build_bank_event(
                event_type="escrow.split",
                summary=f"Settled escrow {escrow_id}: {worker_amount} to worker",
                payload={"escrow_id": escrow_id, "worker_amount": worker_amount},
                task_id=None,
                agent_id=worker_account_id,
            ),
        }
        response = await self._post_raw(_SETTLE_PATH, payload)
        if response.status_code == 200:
            return self._response_dict(response)
        self._raise_status_error(_SETTLE_PATH, response)


def settlement_request_sha256(fields: dict[str, object]) -> str:
    """Hash the Gateway's canonical version-1 escrow_split settlement request."""
    payload = {"request_version": 1, "operation_kind": "escrow_split", **fields}
    raw = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def parse_settlement_envelope(
    value: object,
    operation_id: str,
    fields: dict[str, object],
) -> dict[str, object]:
    """Reject a Gateway body that is not exactly the requested settlement receipt."""
    try:
        envelope = NativeFinancialEnvelope.model_validate(value)
    except ValidationError as exc:
        raise RuntimeError("Gateway returned an invalid settlement envelope") from exc
    receipt = envelope.receipt
    escrow = envelope.current_escrow
    worker_amount = fields["worker_amount"]
    if (
        receipt.operation_id != operation_id
        or receipt.operation_kind != "escrow_split"
        or receipt.request_sha256 != settlement_request_sha256(fields)
        or receipt.result
        != {"escrow_status_at_commit": "split", "worker_amount": worker_amount}
        or receipt.escrow_id != fields["escrow_id"]
        or receipt.source_reference != fields["source_reference"]
        or escrow is None
        or escrow.escrow_id != receipt.escrow_id
        or escrow.status != "split"
        or escrow.resolved_at is None
        or escrow.payer_account_id != fields["poster_account_id"]
        or type(worker_amount) is not int
        or worker_amount > escrow.amount
    ):
        raise RuntimeError("Gateway returned a mismatched settlement receipt")
    legs = [(leg.role, leg.account_id, leg.direction, leg.amount) for leg in receipt.legs]
    if legs != [
        ("worker", fields["worker_account_id"], "credit", worker_amount),
        ("poster", fields["poster_account_id"], "credit", escrow.amount - worker_amount),
    ]:
        raise RuntimeError("Gateway returned unexpected settlement legs")
    return envelope.model_dump(mode="json")


__all__ = [
    "SettlementGatewayClient",
    "parse_settlement_envelope",
    "settlement_request_sha256",
]
