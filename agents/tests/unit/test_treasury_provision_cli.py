"""Unit tests for treasury_provision_cli (T-075 — same gap as fund_feeder_cli):
provisioning branches (missing agent_id, 409-tolerance on create_account) and
the main() entry point's dispatch, none of which are covered by the existing
config-loading unit tests or the e2e provisioning tests.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from treasury_provision_cli import __main__ as treasury_main
from treasury_provision_cli.config import TreasuryConfig


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://localhost:8002/accounts")
    response = httpx.Response(status_code=status_code, request=request)
    return httpx.HTTPStatusError("error", request=request, response=response)


def _make_settings() -> TreasuryConfig:
    return TreasuryConfig(
        handle="operator",
        genesis_amount=1000000,
        genesis_reference="treasury_genesis",
    )


def _make_factory(operator_agent_id: str | None = "a-operator") -> MagicMock:
    operator = MagicMock()
    operator.agent_id = operator_agent_id
    operator.register = AsyncMock()
    operator.get_balance = AsyncMock(return_value={"balance": 1000000})
    operator.close = AsyncMock()

    platform = MagicMock()
    platform.register = AsyncMock()
    platform.create_account = AsyncMock(return_value={"account_id": operator_agent_id})
    platform.credit_account = AsyncMock(return_value={"tx_id": "tx-1", "balance_after": 1000000})
    platform.close = AsyncMock()

    factory = MagicMock()
    factory.create_agent = MagicMock(return_value=operator)
    factory.platform_agent = MagicMock(return_value=platform)
    return factory


@pytest.mark.unit
class TestProvisionMissingAgentId:
    @pytest.mark.asyncio
    async def test_registration_without_agent_id_exits(self) -> None:
        factory = _make_factory(operator_agent_id=None)
        with (
            patch.object(treasury_main, "load_treasury_settings", return_value=_make_settings()),
            patch.object(treasury_main, "AgentFactory", return_value=factory),
            pytest.raises(SystemExit) as exc_info,
        ):
            await treasury_main._provision()

        assert exc_info.value.code == 1
        factory.platform_agent.return_value.create_account.assert_not_awaited()


@pytest.mark.unit
class TestProvisionHappyPath:
    @pytest.mark.asyncio
    async def test_provisions_and_prints_summary(self, capsys: pytest.CaptureFixture[str]) -> None:
        factory = _make_factory()
        with (
            patch.object(treasury_main, "load_treasury_settings", return_value=_make_settings()),
            patch.object(treasury_main, "AgentFactory", return_value=factory),
        ):
            await treasury_main._provision()

        platform = factory.platform_agent.return_value
        operator = factory.create_agent.return_value
        platform.register.assert_awaited_once()
        operator.register.assert_awaited_once()
        platform.create_account.assert_awaited_once_with(agent_id="a-operator", initial_balance=0)
        platform.credit_account.assert_awaited_once()
        assert platform.credit_account.call_args.kwargs["account_id"] == "a-operator"
        assert platform.credit_account.call_args.kwargs["amount"] == 1000000
        assert platform.credit_account.call_args.kwargs["reference"] == "treasury_genesis"
        operator.get_balance.assert_awaited_once()
        operator.close.assert_awaited_once()
        platform.close.assert_awaited_once()

        out = capsys.readouterr().out
        assert "agent_id=a-operator" in out
        assert "genesis_amount=1000000" in out
        assert "balance=1000000" in out

    @pytest.mark.asyncio
    async def test_existing_account_409_is_tolerated(self) -> None:
        factory = _make_factory()
        factory.platform_agent.return_value.create_account = AsyncMock(
            side_effect=_http_status_error(409)
        )
        with (
            patch.object(treasury_main, "load_treasury_settings", return_value=_make_settings()),
            patch.object(treasury_main, "AgentFactory", return_value=factory),
        ):
            await treasury_main._provision()

        factory.platform_agent.return_value.credit_account.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_non_409_error_on_create_account_propagates(self) -> None:
        factory = _make_factory()
        factory.platform_agent.return_value.create_account = AsyncMock(
            side_effect=_http_status_error(500)
        )
        with (
            patch.object(treasury_main, "load_treasury_settings", return_value=_make_settings()),
            patch.object(treasury_main, "AgentFactory", return_value=factory),
            pytest.raises(httpx.HTTPStatusError),
        ):
            await treasury_main._provision()

        factory.platform_agent.return_value.credit_account.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_closes_agents_even_on_failure(self) -> None:
        factory = _make_factory()
        factory.platform_agent.return_value.create_account = AsyncMock(
            side_effect=_http_status_error(500)
        )
        with (
            patch.object(treasury_main, "load_treasury_settings", return_value=_make_settings()),
            patch.object(treasury_main, "AgentFactory", return_value=factory),
            pytest.raises(httpx.HTTPStatusError),
        ):
            await treasury_main._provision()

        factory.create_agent.return_value.close.assert_awaited_once()
        factory.platform_agent.return_value.close.assert_awaited_once()


@pytest.mark.unit
class TestMainEntryPoint:
    def test_main_dispatches_to_provision(self) -> None:
        sentinel = object()
        with (
            patch.object(
                treasury_main, "_provision", new=MagicMock(return_value=sentinel)
            ) as mock_provision,
            patch.object(treasury_main.asyncio, "run") as mock_run,
        ):
            treasury_main.main()

        mock_provision.assert_called_once_with()
        mock_run.assert_called_once_with(sentinel)
