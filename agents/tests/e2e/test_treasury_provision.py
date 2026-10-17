"""E2E tests for the treasury_provision_cli (WP-08 / Q-9).

Proves the treasury-provision command is idempotent genesis for the UI
operator's account: registering the operator, creating its bank account, and
crediting the treasury amount are all safe to repeat.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
from service_auth.financial_identity import operation_id_for

from base_agent.factory import AgentFactory
from treasury_provision_cli.config import load_treasury_settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from base_agent.agent import BaseAgent
    from base_agent.platform import PlatformAgent

CONFIG_PATH = Path(
    os.environ.get("AGENT_CONFIG_PATH", str(Path(__file__).resolve().parents[2] / "config.yaml"))
)


@pytest.fixture()
def treasury_settings():
    return load_treasury_settings(config_path=CONFIG_PATH)


@pytest.fixture()
async def operator_agent(treasury_settings) -> AsyncIterator[BaseAgent]:
    factory = AgentFactory(config_path=CONFIG_PATH)
    agent = factory.create_agent(treasury_settings.handle)
    await agent.register()
    yield agent
    await agent.close()


@pytest.fixture()
async def platform() -> AsyncIterator[PlatformAgent]:
    factory = AgentFactory(config_path=CONFIG_PATH)
    agent = factory.platform_agent()
    await agent.register()
    yield agent
    await agent.close()


@pytest.mark.e2e
async def test_operator_registration_is_idempotent(treasury_settings) -> None:
    """Registering the operator agent twice yields the same agent_id."""
    factory = AgentFactory(config_path=CONFIG_PATH)
    first = factory.create_agent(treasury_settings.handle)
    second = factory.create_agent(treasury_settings.handle)

    try:
        await first.register()
        await second.register()

        assert first.agent_id is not None
        assert first.agent_id == second.agent_id
    finally:
        await first.close()
        await second.close()


@pytest.mark.e2e
async def test_operator_account_creation_is_idempotent(
    platform: PlatformAgent,
    operator_agent: BaseAgent,
) -> None:
    """Creating the operator's zero-balance account twice is a safe no-op."""
    try:
        await platform.create_account(
            agent_id=operator_agent.agent_id, initial_balance=0, operation_id=None
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 409:
            raise

    # Second call: must 409, not raise anything else.
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await platform.create_account(
            agent_id=operator_agent.agent_id, initial_balance=0, operation_id=None
        )
    assert exc_info.value.response.status_code == 409


@pytest.mark.e2e
async def test_treasury_genesis_credit_is_idempotent(
    platform: PlatformAgent,
    operator_agent: BaseAgent,
    treasury_settings,
) -> None:
    """Retrying the genesis operation does not double-credit the treasury."""
    try:
        await platform.create_account(
            agent_id=operator_agent.agent_id, initial_balance=0, operation_id=None
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 409:
            raise

    operation_id = operation_id_for(
        "treasury-genesis", (operator_agent.agent_id, treasury_settings.genesis_reference)
    )
    first = await platform.credit_account(
        account_id=operator_agent.agent_id,
        amount=treasury_settings.genesis_amount,
        reference=treasury_settings.genesis_reference,
        operation_id=operation_id,
    )
    second = await platform.credit_account(
        account_id=operator_agent.agent_id,
        amount=treasury_settings.genesis_amount,
        reference=treasury_settings.genesis_reference,
        operation_id=operation_id,
    )

    assert first["receipt"]["operation_id"] == second["receipt"]["operation_id"] == operation_id
    assert (
        first["receipt"]["legs"][0]["transaction_id"]
        == second["receipt"]["legs"][0]["transaction_id"]
    )
    assert (
        first["receipt"]["result"]["balance_after"] == second["receipt"]["result"]["balance_after"]
    )


@pytest.mark.e2e
async def test_treasury_provision_full_workflow(treasury_settings) -> None:
    """End-to-end: register operator + platform, create account, credit genesis, verify balance."""
    factory = AgentFactory(config_path=CONFIG_PATH)
    operator_agent = factory.create_agent(treasury_settings.handle)
    platform_agent = factory.platform_agent()

    try:
        await operator_agent.register()
        await platform_agent.register()

        try:
            await platform_agent.create_account(
                agent_id=operator_agent.agent_id,
                initial_balance=0,
                operation_id=None,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 409:
                raise

        # Reuses the same explicit treasury-genesis operation identity as
        # `just provision`, so the original receipt is replayed. Do not assert
        # a balance floor tied to genesis_amount: the operator's live balance
        # legitimately drops as other tests spend from the shared account.
        operation_id = operation_id_for(
            "treasury-genesis", (operator_agent.agent_id, treasury_settings.genesis_reference)
        )
        result = await platform_agent.credit_account(
            account_id=operator_agent.agent_id,
            amount=treasury_settings.genesis_amount,
            reference=treasury_settings.genesis_reference,
            operation_id=operation_id,
        )
        assert result["outcome"] == "native_receipt"
        assert result["receipt"]["operation_id"] == operation_id
        assert isinstance(result["receipt"]["result"]["balance_after"], int)

        balance = await operator_agent.get_balance()
        assert isinstance(balance["balance"], int)
        assert balance["balance"] >= 0
    finally:
        await operator_agent.close()
        await platform_agent.close()
