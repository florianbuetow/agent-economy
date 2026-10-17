"""Funding intent owners reuse explicit identities and read native receipts."""

from __future__ import annotations

import hashlib
import json
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from service_auth.financial_identity import operation_id_for

from fund_feeder_cli import __main__ as funding
from treasury_provision_cli import __main__ as treasury

pytestmark = pytest.mark.unit

_FUNDING_ID = "op-00000000-0000-4000-8000-000000000123"


def _credit_reply(
    account_id: str, amount: int, reference: str, operation_id: str
) -> dict[str, Any]:
    canonical = {
        "request_version": 1,
        "operation_kind": "credit",
        "account_id": account_id,
        "amount": amount,
        "reference": reference,
    }
    return {
        "outcome": "native_receipt",
        "receipt": {
            "receipt_version": 1,
            "operation_id": operation_id,
            "operation_kind": "credit",
            "request_sha256": hashlib.sha256(
                json.dumps(
                    canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode()
            ).hexdigest(),
            "source_reference": reference,
            "committed_at": "2026-10-17T14:00:00Z",
            "event_id": 7,
            "escrow_id": None,
            "result": {
                "account_id": account_id,
                "amount": amount,
                "balance_after": amount,
                "reference": reference,
            },
            "legs": [
                {
                    "role": "account",
                    "account_id": account_id,
                    "direction": "credit",
                    "amount": amount,
                    "balance_after": amount,
                    "transaction_id": "tx-funding",
                }
            ],
        },
        "current_escrow": None,
    }


def _actors(account_id: str, reply: dict[str, Any]) -> tuple[SimpleNamespace, SimpleNamespace]:
    agent = SimpleNamespace(
        agent_id=account_id,
        register=AsyncMock(),
        close=AsyncMock(),
        get_balance=AsyncMock(return_value={"balance": 100}),
    )
    platform = SimpleNamespace(
        agent_id="a-platform",
        register=AsyncMock(),
        close=AsyncMock(),
        create_account=AsyncMock(),
        credit_account=AsyncMock(return_value=reply),
    )
    factory = SimpleNamespace(
        create_agent=Mock(return_value=agent), platform_agent=Mock(return_value=platform)
    )
    return factory, platform


@pytest.mark.asyncio
async def test_treasury_reuses_genesis_identity_and_consumes_native_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation_id = operation_id_for("treasury-genesis", ("a-operator", "genesis-1"))
    factory, platform = _actors(
        "a-operator", _credit_reply("a-operator", 100, "genesis-1", operation_id)
    )
    monkeypatch.setattr(treasury, "AgentFactory", lambda: factory)
    monkeypatch.setattr(
        treasury,
        "load_treasury_settings",
        lambda: SimpleNamespace(
            handle="operator", genesis_amount=100, genesis_reference="genesis-1"
        ),
    )
    await treasury._provision()
    await treasury._provision()
    assert platform.create_account.await_count == 2
    assert platform.credit_account.await_count == 2
    for call in platform.create_account.await_args_list:
        assert call.kwargs == {"agent_id": "a-operator", "initial_balance": 0, "operation_id": None}
    for call in platform.credit_account.await_args_list:
        assert call.kwargs == {
            "account_id": "a-operator",
            "amount": 100,
            "reference": "genesis-1",
            "operation_id": operation_id,
        }
    assert platform.close.await_count == 2


@pytest.mark.asyncio
async def test_feeder_funding_reuses_the_operators_identity_and_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, platform = _actors("a-feeder", _credit_reply("a-feeder", 100, "fund-1", _FUNDING_ID))
    monkeypatch.setattr(funding, "AgentFactory", lambda: factory)
    await funding._fund(100, operation_id=_FUNDING_ID, reference="fund-1")
    await funding._fund(100, operation_id=_FUNDING_ID, reference="fund-1")
    assert platform.create_account.await_count == 2
    assert platform.credit_account.await_count == 2
    for call in platform.create_account.await_args_list:
        assert call.kwargs == {"agent_id": "a-feeder", "initial_balance": 0, "operation_id": None}
    for call in platform.credit_account.await_args_list:
        assert call.kwargs == {
            "account_id": "a-feeder",
            "amount": 100,
            "reference": "fund-1",
            "operation_id": _FUNDING_ID,
        }
    assert platform.close.await_count == 2


def test_funding_cli_accepts_explicit_replayable_intent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["fund_feeder_cli", "100", "--operation-id", _FUNDING_ID, "--reference", "fund-1"],
    )
    args = funding._parse_args()
    assert (args.amount, args.operation_id, args.reference) == (100, _FUNDING_ID, "fund-1")


@pytest.mark.parametrize("extra", [[], ["--reference", "fund-1"], ["--operation-id", _FUNDING_ID]])
def test_funding_cli_requires_both_intent_fields(
    monkeypatch: pytest.MonkeyPatch, extra: list[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["fund_feeder_cli", "100", *extra])
    with pytest.raises(SystemExit) as error:
        funding._parse_args()
    assert error.value.code == 2
