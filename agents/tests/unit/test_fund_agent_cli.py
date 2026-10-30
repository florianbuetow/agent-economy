"""Unit tests for fund_agent_cli: non-roster agent funding, idempotent replay, failure codes."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from fund_agent_cli import __main__ as fund_agent_main
from tests.unit.test_fund_feeder_cli import _credit_response, _http_status_error

_OPERATION_ID = "op-00000000-0000-4000-8000-000000000061"
_EXTERNAL_AGENT_ID = "a-external-not-in-roster"


def _factory(credit_side_effect: object = None) -> MagicMock:
    platform = MagicMock()
    platform.register = AsyncMock()
    platform.create_account = AsyncMock(return_value={"account_id": _EXTERNAL_AGENT_ID})
    platform.credit_account = AsyncMock(
        return_value=_credit_response(_EXTERNAL_AGENT_ID, 250, "grant", _OPERATION_ID),
        side_effect=credit_side_effect,
    )
    platform.close = AsyncMock()
    factory = MagicMock()
    factory.platform_agent = MagicMock(return_value=platform)
    return factory


@pytest.mark.unit
class TestParseArgs:
    def test_parses_all_flags(self) -> None:
        args = fund_agent_main._parse_args(
            [
                "--agent-id",
                _EXTERNAL_AGENT_ID,
                "--amount",
                "250",
                "--operation-id",
                _OPERATION_ID,
                "--reference",
                "grant",
            ]
        )
        assert (args.agent_id, args.amount, args.operation_id, args.reference) == (
            _EXTERNAL_AGENT_ID,
            250,
            _OPERATION_ID,
            "grant",
        )

    def test_reference_is_optional(self) -> None:
        args = fund_agent_main._parse_args(
            ["--agent-id", "a-x", "--amount", "1", "--operation-id", _OPERATION_ID]
        )
        assert args.reference is None

    def test_missing_agent_id_exits(self) -> None:
        with pytest.raises(SystemExit):
            fund_agent_main._parse_args(["--amount", "1", "--operation-id", _OPERATION_ID])


@pytest.mark.unit
class TestFundAgent:
    @pytest.mark.asyncio
    async def test_funds_non_roster_agent_without_roster_lookup(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        factory = _factory()
        with patch.object(fund_agent_main, "AgentFactory", return_value=factory):
            await fund_agent_main.fund_agent(_EXTERNAL_AGENT_ID, 250, _OPERATION_ID, "grant")

        factory.create_agent.assert_not_called()
        platform = factory.platform_agent.return_value
        platform.create_account.assert_awaited_once_with(
            agent_id=_EXTERNAL_AGENT_ID, initial_balance=0, operation_id=None
        )
        platform.credit_account.assert_awaited_once_with(
            account_id=_EXTERNAL_AGENT_ID,
            amount=250,
            reference="grant",
            operation_id=_OPERATION_ID,
        )
        platform.close.assert_awaited_once()
        out = capsys.readouterr().out
        assert f"agent_id={_EXTERNAL_AGENT_ID}" in out
        assert "funded_amount=250" in out
        assert f"operation_id={_OPERATION_ID}" in out

    @pytest.mark.asyncio
    async def test_idempotent_replay_returns_same_receipt_with_same_operation_id(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        factory = _factory()
        platform = factory.platform_agent.return_value
        platform.create_account = AsyncMock(side_effect=_http_status_error(409))
        with patch.object(fund_agent_main, "AgentFactory", return_value=factory):
            await fund_agent_main.fund_agent(_EXTERNAL_AGENT_ID, 250, _OPERATION_ID, "grant")
            await fund_agent_main.fund_agent(_EXTERNAL_AGENT_ID, 250, _OPERATION_ID, "grant")

        calls = platform.credit_account.await_args_list
        assert len(calls) == 2
        assert calls[0] == calls[1]
        outs = capsys.readouterr().out.splitlines()
        assert outs[: len(outs) // 2] == outs[len(outs) // 2 :]

    @pytest.mark.asyncio
    async def test_non_positive_amount_exits_before_factory(self) -> None:
        with (
            patch.object(fund_agent_main, "AgentFactory") as factory_cls,
            pytest.raises(SystemExit) as exc_info,
        ):
            await fund_agent_main.fund_agent(_EXTERNAL_AGENT_ID, 0, _OPERATION_ID, "grant")
        assert exc_info.value.code == 1
        factory_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_receipt_for_other_operation_is_rejected(self) -> None:
        factory = _factory()
        factory.platform_agent.return_value.credit_account = AsyncMock(
            return_value=_credit_response(_EXTERNAL_AGENT_ID, 250, "grant", "op-other")
        )
        with (
            patch.object(fund_agent_main, "AgentFactory", return_value=factory),
            pytest.raises(RuntimeError, match="unexpected receipt"),
        ):
            await fund_agent_main.fund_agent(_EXTERNAL_AGENT_ID, 250, _OPERATION_ID, "grant")
        factory.platform_agent.return_value.close.assert_awaited_once()


@pytest.mark.unit
class TestMainFailureReporting:
    def test_server_error_code_is_printed_and_exit_is_one(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        request = httpx.Request("POST", "http://localhost:8002/accounts/x/credit")
        response = httpx.Response(
            404,
            request=request,
            json={"error": "ACCOUNT_NOT_FOUND", "message": "no such account", "details": {}},
        )
        error = httpx.HTTPStatusError("404", request=request, response=response)
        factory = _factory(credit_side_effect=error)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "fund_agent_cli",
                "--agent-id",
                "a-x",
                "--amount",
                "5",
                "--operation-id",
                _OPERATION_ID,
            ],
        )
        with (
            patch.object(fund_agent_main, "AgentFactory", return_value=factory),
            pytest.raises(SystemExit) as exc_info,
        ):
            fund_agent_main.main()

        assert exc_info.value.code == 1
        assert "error=ACCOUNT_NOT_FOUND" in capsys.readouterr().err
        factory.platform_agent.return_value.close.assert_awaited_once()

    def test_describe_failure_without_json_body(self) -> None:
        request = httpx.Request("POST", "http://x")
        response = httpx.Response(502, request=request, content=b"bad gateway")
        error = httpx.HTTPStatusError("502", request=request, response=response)
        assert fund_agent_main.describe_failure(error).startswith("error=UNKNOWN status=502")
