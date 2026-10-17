"""Unit tests for fund_feeder_cli (T-075): arg parsing, non-positive amount
rejection, exit codes.
"""

from __future__ import annotations

import hashlib
import json
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from fund_feeder_cli import __main__ as fund_feeder_main

_OPERATION_ID = "op-00000000-0000-4000-8000-000000000051"
_REFERENCE = "manual-funding-1"


def _credit_response(
    account_id: str,
    amount: int,
    reference: str,
    operation_id: str,
) -> dict[str, Any]:
    canonical_request = {
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
                    canonical_request,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode()
            ).hexdigest(),
            "source_reference": reference,
            "committed_at": "2026-10-17T14:00:00Z",
            "event_id": 1,
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
                    "transaction_id": "tx-funding",
                    "balance_after": amount,
                }
            ],
        },
        "current_escrow": None,
    }


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://localhost:8002/accounts")
    response = httpx.Response(status_code=status_code, request=request)
    return httpx.HTTPStatusError("error", request=request, response=response)


@pytest.mark.unit
class TestParseArgs:
    def test_parses_positive_amount(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "fund_feeder_cli",
                "500",
                "--operation-id",
                _OPERATION_ID,
                "--reference",
                _REFERENCE,
            ],
        )
        args = fund_feeder_main._parse_args()
        assert (args.amount, args.operation_id, args.reference) == (
            500,
            _OPERATION_ID,
            _REFERENCE,
        )

    def test_parses_negative_amount(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "fund_feeder_cli",
                "-5",
                "--operation-id",
                _OPERATION_ID,
                "--reference",
                _REFERENCE,
            ],
        )
        args = fund_feeder_main._parse_args()
        assert args.amount == -5

    def test_missing_amount_raises_system_exit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "fund_feeder_cli",
                "--operation-id",
                _OPERATION_ID,
                "--reference",
                _REFERENCE,
            ],
        )
        with pytest.raises(SystemExit):
            fund_feeder_main._parse_args()

    def test_non_integer_amount_raises_system_exit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "fund_feeder_cli",
                "not-a-number",
                "--operation-id",
                _OPERATION_ID,
                "--reference",
                _REFERENCE,
            ],
        )
        with pytest.raises(SystemExit):
            fund_feeder_main._parse_args()

    def test_extra_positional_raises_system_exit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "fund_feeder_cli",
                "100",
                "200",
                "--operation-id",
                _OPERATION_ID,
                "--reference",
                _REFERENCE,
            ],
        )
        with pytest.raises(SystemExit):
            fund_feeder_main._parse_args()


@pytest.mark.unit
class TestFundRejectsNonPositiveAmount:
    @pytest.mark.asyncio
    async def test_zero_amount_exits_before_touching_factory(self) -> None:
        with (
            patch.object(fund_feeder_main, "AgentFactory") as mock_factory_cls,
            pytest.raises(SystemExit) as exc_info,
        ):
            await fund_feeder_main._fund(0, _OPERATION_ID, _REFERENCE)
        assert exc_info.value.code == 1
        mock_factory_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_negative_amount_exits_before_touching_factory(self) -> None:
        with (
            patch.object(fund_feeder_main, "AgentFactory") as mock_factory_cls,
            pytest.raises(SystemExit) as exc_info,
        ):
            await fund_feeder_main._fund(-10, _OPERATION_ID, _REFERENCE)
        assert exc_info.value.code == 1
        mock_factory_cls.assert_not_called()


def _make_factory(feeder_agent_id: str | None = "a-feeder") -> MagicMock:
    feeder = MagicMock()
    feeder.agent_id = feeder_agent_id
    feeder.register = AsyncMock()
    feeder.get_balance = AsyncMock(return_value={"balance": 500})
    feeder.close = AsyncMock()

    platform = MagicMock()
    platform.register = AsyncMock()
    platform.create_account = AsyncMock(return_value={"account_id": feeder_agent_id})
    platform.credit_account = AsyncMock(
        return_value=_credit_response("a-feeder", 500, _REFERENCE, _OPERATION_ID)
    )
    platform.close = AsyncMock()

    factory = MagicMock()
    factory.create_agent = MagicMock(return_value=feeder)
    factory.platform_agent = MagicMock(return_value=platform)
    return factory


@pytest.mark.unit
class TestFundMissingAgentId:
    @pytest.mark.asyncio
    async def test_registration_without_agent_id_exits(self) -> None:
        factory = _make_factory(feeder_agent_id=None)
        with (
            patch.object(fund_feeder_main, "AgentFactory", return_value=factory),
            pytest.raises(SystemExit) as exc_info,
        ):
            await fund_feeder_main._fund(100, _OPERATION_ID, _REFERENCE)

        assert exc_info.value.code == 1
        factory.platform_agent.return_value.create_account.assert_not_awaited()


@pytest.mark.unit
class TestFundHappyPath:
    @pytest.mark.asyncio
    async def test_funds_and_prints_summary(self, capsys: pytest.CaptureFixture[str]) -> None:
        factory = _make_factory()
        with patch.object(fund_feeder_main, "AgentFactory", return_value=factory):
            await fund_feeder_main._fund(500, _OPERATION_ID, _REFERENCE)

        platform = factory.platform_agent.return_value
        feeder = factory.create_agent.return_value
        platform.register.assert_awaited_once()
        feeder.register.assert_awaited_once()
        platform.create_account.assert_awaited_once_with(
            agent_id="a-feeder",
            initial_balance=0,
            operation_id=None,
        )
        platform.credit_account.assert_awaited_once_with(
            account_id="a-feeder",
            amount=500,
            reference=_REFERENCE,
            operation_id=_OPERATION_ID,
        )
        feeder.get_balance.assert_awaited_once()
        feeder.close.assert_awaited_once()
        platform.close.assert_awaited_once()

        out = capsys.readouterr().out
        assert "agent_id=a-feeder" in out
        assert "funded_amount=500" in out
        assert "balance=500" in out
        assert f"operation_id={_OPERATION_ID}" in out

    @pytest.mark.asyncio
    async def test_existing_account_409_is_tolerated(self) -> None:
        factory = _make_factory()
        factory.platform_agent.return_value.create_account = AsyncMock(
            side_effect=_http_status_error(409)
        )
        with patch.object(fund_feeder_main, "AgentFactory", return_value=factory):
            await fund_feeder_main._fund(500, _OPERATION_ID, _REFERENCE)

        platform = factory.platform_agent.return_value
        platform.create_account.assert_awaited_once_with(
            agent_id="a-feeder",
            initial_balance=0,
            operation_id=None,
        )
        platform.credit_account.assert_awaited_once_with(
            account_id="a-feeder",
            amount=500,
            reference=_REFERENCE,
            operation_id=_OPERATION_ID,
        )

    @pytest.mark.asyncio
    async def test_non_409_error_on_create_account_propagates(self) -> None:
        factory = _make_factory()
        factory.platform_agent.return_value.create_account = AsyncMock(
            side_effect=_http_status_error(500)
        )
        with (
            patch.object(fund_feeder_main, "AgentFactory", return_value=factory),
            pytest.raises(httpx.HTTPStatusError),
        ):
            await fund_feeder_main._fund(500, _OPERATION_ID, _REFERENCE)

        factory.platform_agent.return_value.credit_account.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_closes_agents_even_on_failure(self) -> None:
        factory = _make_factory()
        factory.platform_agent.return_value.create_account = AsyncMock(
            side_effect=_http_status_error(500)
        )
        with (
            patch.object(fund_feeder_main, "AgentFactory", return_value=factory),
            pytest.raises(httpx.HTTPStatusError),
        ):
            await fund_feeder_main._fund(500, _OPERATION_ID, _REFERENCE)

        factory.create_agent.return_value.close.assert_awaited_once()
        factory.platform_agent.return_value.close.assert_awaited_once()


@pytest.mark.unit
class TestMainEntryPoint:
    def test_main_dispatches_parsed_amount_to_fund(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "fund_feeder_cli",
                "250",
                "--operation-id",
                _OPERATION_ID,
                "--reference",
                _REFERENCE,
            ],
        )
        sentinel = object()
        with (
            patch.object(
                fund_feeder_main, "_fund", new=MagicMock(return_value=sentinel)
            ) as mock_fund,
            patch.object(fund_feeder_main.asyncio, "run") as mock_run,
        ):
            fund_feeder_main.main()

        mock_fund.assert_called_once_with(250, _OPERATION_ID, _REFERENCE)
        mock_run.assert_called_once_with(sentinel)
