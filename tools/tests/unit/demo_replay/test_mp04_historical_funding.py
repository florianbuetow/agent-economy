"""Demo funding rejects historical observations as proof of new funding."""

from __future__ import annotations

import hashlib
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from service_auth.financial_identity import operation_id_for
from service_auth.financial_responses import LegacyFinancialEnvelope

from demo_replay import clients
from demo_replay import engine as engine_module
from test_mp04_financial_intents import _durable_funding_engine

pytestmark = pytest.mark.unit

_REFERENCE = "demo:historical:fund:alice"
_HISTORICAL_TX_ID = "tx-demo-historical-credit"


def _sha256(value: object) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _legacy_credit(operation_id: str) -> dict[str, object]:
    request = {
        "request_version": 1,
        "operation_kind": "credit",
        "account_id": "a-alice",
        "amount": 100,
        "reference": _REFERENCE,
    }
    transaction = {
        "tx_id": _HISTORICAL_TX_ID,
        "account_id": "a-alice",
        "type": "credit",
        "amount": 100,
        "balance_after": 100,
        "reference": _REFERENCE,
        "timestamp": "",
        "event_id": -1,
    }
    raw = {
        "outcome": "legacy_observation",
        "attempt": {
            "operation_id": operation_id,
            "operation_kind": "credit",
            "request_sha256": _sha256(request),
        },
        "observation": {
            "operation_id": None,
            "observed_kind": "observed_credit",
            "transaction": transaction,
            "observed_fact_sha256": _sha256(transaction),
        },
        "current_escrow": None,
    }
    return LegacyFinancialEnvelope.model_validate(raw).model_dump(mode="json")


@pytest.mark.asyncio
async def test_demo_fund_rejects_historical_credit_before_success_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step = {"action": "fund", "agent": "alice", "amount": 100, "reference": _REFERENCE}
    engine = _durable_funding_engine([step])
    operation_id = operation_id_for("demo-funding", ("a-alice", _REFERENCE))
    credit = AsyncMock(return_value=_legacy_credit(operation_id))
    console = MagicMock()
    monkeypatch.setattr(clients, "credit_account", credit)
    monkeypatch.setattr(engine_module, "console", console)

    with pytest.raises(
        RuntimeError,
        match=r"historical observation.*no native operation confirmation",
    ) as error:
        await engine._execute_step(MagicMock(), step)

    assert _HISTORICAL_TX_ID in str(error.value)
    credit.assert_awaited_once()
    console.print.assert_not_called()
