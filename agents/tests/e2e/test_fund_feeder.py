from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import httpx
import pytest

from base_agent.factory import AgentFactory

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from base_agent.agent import BaseAgent
    from base_agent.platform import PlatformAgent

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"


@pytest.fixture()
async def feeder_agent() -> AsyncIterator[BaseAgent]:
    factory = AgentFactory(config_path=CONFIG_PATH)
    agent = factory.create_agent("feeder")
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


@pytest.fixture()
async def funded_feeder(
    feeder_agent: BaseAgent,
    platform: PlatformAgent,
) -> AsyncIterator[BaseAgent]:
    try:
        await platform.create_account(agent_id=feeder_agent.agent_id, initial_balance=0)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 409:
            raise

    try:
        await platform.credit_account(
            account_id=feeder_agent.agent_id,
            amount=500,
            reference="fund_feeder_test_setup",
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code not in {400, 409}:
            raise
        error_data = exc.response.json()
        if error_data.get("error") != "PAYLOAD_MISMATCH":
            raise

    yield feeder_agent


@pytest.mark.e2e
async def test_feeder_registration_is_idempotent() -> None:
    """Registering the feeder agent twice yields the same agent_id."""
    factory = AgentFactory(config_path=CONFIG_PATH)
    first = factory.create_agent("feeder")
    second = factory.create_agent("feeder")

    try:
        await first.register()
        await second.register()

        assert first.agent_id is not None
        assert second.agent_id is not None
        assert first.agent_id == second.agent_id
    finally:
        await first.close()
        await second.close()


@pytest.mark.e2e
async def test_feeder_account_creation(
    platform: PlatformAgent,
    feeder_agent: BaseAgent,
) -> None:
    """Platform agent can create a bank account for the feeder."""
    try:
        await platform.create_account(agent_id=feeder_agent.agent_id, initial_balance=0)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 409:
            raise

    balance = await feeder_agent.get_balance()
    assert "account_id" in balance
    assert "balance" in balance


@pytest.mark.e2e
async def test_platform_can_credit_feeder(
    funded_feeder: BaseAgent,
    platform: PlatformAgent,
) -> None:
    """Platform agent can credit funds to the feeder's account."""
    reference = f"test_credit_{uuid4().hex[:8]}"
    result = await platform.credit_account(
        account_id=funded_feeder.agent_id,
        amount=100,
        reference=reference,
    )

    assert "tx_id" in result
    assert "balance_after" in result
    assert isinstance(result["balance_after"], int)


@pytest.mark.e2e
async def test_credit_idempotency(
    funded_feeder: BaseAgent,
    platform: PlatformAgent,
) -> None:
    """Crediting with the same reference and amount is idempotent."""
    reference = f"test_idempotent_{uuid4().hex[:8]}"

    first = await platform.credit_account(
        account_id=funded_feeder.agent_id,
        amount=50,
        reference=reference,
    )
    second = await platform.credit_account(
        account_id=funded_feeder.agent_id,
        amount=50,
        reference=reference,
    )

    assert first["tx_id"] == second["tx_id"]


@pytest.mark.e2e
async def test_credit_requires_positive_amount(
    funded_feeder: BaseAgent,
    platform: PlatformAgent,
) -> None:
    """Crediting with zero or negative amount fails."""
    with pytest.raises(httpx.HTTPStatusError) as zero_exc:
        await platform.credit_account(
            account_id=funded_feeder.agent_id,
            amount=0,
            reference=f"test_zero_{uuid4().hex[:8]}",
        )
    assert zero_exc.value.response.status_code == 400

    with pytest.raises(httpx.HTTPStatusError) as negative_exc:
        await platform.credit_account(
            account_id=funded_feeder.agent_id,
            amount=-10,
            reference=f"test_negative_{uuid4().hex[:8]}",
        )
    assert negative_exc.value.response.status_code == 400


@pytest.mark.e2e
async def test_feeder_balance_reflects_credits(
    funded_feeder: BaseAgent,
    platform: PlatformAgent,
) -> None:
    """After crediting, the feeder's balance increases by the credited amount."""
    before = await funded_feeder.get_balance()
    reference = f"test_balance_{uuid4().hex[:8]}"
    await platform.credit_account(
        account_id=funded_feeder.agent_id,
        amount=200,
        reference=reference,
    )
    after = await funded_feeder.get_balance()

    assert after["balance"] == before["balance"] + 200


@pytest.mark.e2e
async def test_funding_summary_fields(
    funded_feeder: BaseAgent,
    platform: PlatformAgent,
) -> None:
    """The credit response contains tx_id and balance_after for summary output."""
    amount = 75
    reference = f"test_summary_{uuid4().hex[:8]}"
    result = await platform.credit_account(
        account_id=funded_feeder.agent_id,
        amount=amount,
        reference=reference,
    )

    assert "tx_id" in result
    assert "balance_after" in result
    assert str(result["tx_id"]).startswith("tx-")
    assert isinstance(result["balance_after"], int)
    assert result["balance_after"] >= amount


@pytest.mark.e2e
async def test_credit_without_account_fails(platform: PlatformAgent) -> None:
    """Crediting a non-existent account fails with 404."""
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await platform.credit_account(
            account_id="a-00000000-0000-0000-0000-000000000000",
            amount=10,
            reference=f"test_missing_account_{uuid4().hex[:8]}",
        )

    assert exc_info.value.response.status_code == 404


@pytest.mark.e2e
async def test_feeder_agent_uses_ed25519_signing(feeder_agent: BaseAgent) -> None:
    """The feeder agent's JWS tokens use Ed25519 signing."""
    token = feeder_agent._sign_jws({"action": "test", "data": "hello"})
    parts = token.split(".")

    assert len(parts) == 3

    header_b64 = parts[0]
    padding = "=" * (-len(header_b64) % 4)
    header = json.loads(base64.urlsafe_b64decode(header_b64 + padding))
    assert header["alg"] == "EdDSA"


@pytest.mark.e2e
async def test_fund_feeder_full_workflow() -> None:
    """End-to-end: register feeder, create account, fund, verify balance."""
    factory = AgentFactory(config_path=CONFIG_PATH)
    feeder = factory.create_agent("feeder")
    platform_agent = factory.platform_agent()
    amount = 1000

    try:
        await feeder.register()
        await platform_agent.register()

        try:
            await platform_agent.create_account(agent_id=feeder.agent_id, initial_balance=0)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 409:
                raise

        reference = "fund_feeder_e2e_test"
        try:
            await platform_agent.credit_account(
                account_id=feeder.agent_id,
                amount=amount,
                reference=reference,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in {400, 409}:
                raise
            error_data = exc.response.json()
            if error_data.get("error") == "PAYLOAD_MISMATCH":
                await platform_agent.credit_account(
                    account_id=feeder.agent_id,
                    amount=amount,
                    reference=f"{reference}_{uuid4().hex[:8]}",
                )
            else:
                raise

        balance = await feeder.get_balance()
        assert isinstance(balance["balance"], int)
        assert balance["balance"] >= amount

        print(f"agent_id={feeder.agent_id}")
        print(f"funded_amount={amount}")
        print(f"balance={balance['balance']}")
    finally:
        await feeder.close()
        await platform_agent.close()
