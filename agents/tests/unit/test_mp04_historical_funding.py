"""Funding CLIs reject historical observations as proof of new funding."""

from __future__ import annotations

import hashlib
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from service_auth.financial_identity import operation_id_for
from service_auth.financial_responses import LegacyFinancialEnvelope

from fund_feeder_cli import __main__ as fund_feeder
from treasury_provision_cli import __main__ as treasury

pytestmark = pytest.mark.unit

_FEEDER_OPERATION_ID = "op-00000000-0000-4000-8000-000000000401"
_FEEDER_REFERENCE = "manual-funding-historical"
_HISTORICAL_TX_ID = "tx-historical-credit"


def _sha256(value: object) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _legacy_credit(
    account_id: str,
    amount: int,
    reference: str,
    operation_id: str,
) -> dict[str, object]:
    request = {
        "request_version": 1,
        "operation_kind": "credit",
        "account_id": account_id,
        "amount": amount,
        "reference": reference,
    }
    transaction = {
        "tx_id": _HISTORICAL_TX_ID,
        "account_id": account_id,
        "type": "credit",
        "amount": amount,
        "balance_after": amount,
        "reference": reference,
        "timestamp": "",
        "event_id": None,
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


def _factory(account_id: str, credit_result: dict[str, object]) -> SimpleNamespace:
    actor = SimpleNamespace(
        agent_id=account_id,
        register=AsyncMock(),
        get_balance=AsyncMock(return_value={"balance": 999}),
        close=AsyncMock(),
    )
    platform = SimpleNamespace(
        agent_id="a-platform",
        register=AsyncMock(),
        create_account=AsyncMock(return_value={"account_id": account_id}),
        credit_account=AsyncMock(return_value=credit_result),
        close=AsyncMock(),
    )
    return SimpleNamespace(
        create_agent=Mock(return_value=actor),
        platform_agent=Mock(return_value=platform),
    )


@pytest.mark.asyncio
async def test_feeder_rejects_historical_credit_before_success_or_balance_read(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    factory = _factory(
        "a-feeder",
        _legacy_credit(
            "a-feeder",
            500,
            _FEEDER_REFERENCE,
            _FEEDER_OPERATION_ID,
        ),
    )
    monkeypatch.setattr(fund_feeder, "AgentFactory", lambda: factory)
    caplog.set_level(logging.INFO, logger="fund_feeder_cli")

    with pytest.raises(
        RuntimeError,
        match=r"historical observation.*no native operation confirmation",
    ) as error:
        await fund_feeder._fund(500, _FEEDER_OPERATION_ID, _FEEDER_REFERENCE)

    assert _HISTORICAL_TX_ID in str(error.value)
    assert capsys.readouterr().out == ""
    assert not any("Funding confirmed" in record.getMessage() for record in caplog.records)
    factory.create_agent.return_value.get_balance.assert_not_awaited()
    factory.create_agent.return_value.close.assert_awaited_once()
    factory.platform_agent.return_value.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_treasury_rejects_historical_credit_before_success_or_balance_read(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = SimpleNamespace(
        handle="operator",
        genesis_amount=1000000,
        genesis_reference="treasury_genesis",
    )
    operation_id = operation_id_for(
        "treasury-genesis",
        ("a-operator", settings.genesis_reference),
    )
    factory = _factory(
        "a-operator",
        _legacy_credit(
            "a-operator",
            settings.genesis_amount,
            settings.genesis_reference,
            operation_id,
        ),
    )
    monkeypatch.setattr(treasury, "load_treasury_settings", lambda: settings)
    monkeypatch.setattr(treasury, "AgentFactory", lambda: factory)
    caplog.set_level(logging.INFO, logger="treasury_provision_cli")

    with pytest.raises(
        RuntimeError,
        match=r"historical observation.*no native operation confirmation",
    ) as error:
        await treasury._provision()

    assert _HISTORICAL_TX_ID in str(error.value)
    assert capsys.readouterr().out == ""
    assert not any("funding confirmed" in record.getMessage().lower() for record in caplog.records)
    factory.create_agent.return_value.get_balance.assert_not_awaited()
    factory.create_agent.return_value.close.assert_awaited_once()
    factory.platform_agent.return_value.close.assert_awaited_once()
