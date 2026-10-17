from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import httpx
import pytest
from service_auth.financial_identity import operation_id_for

from base_agent.factory import AgentFactory

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from base_agent.agent import BaseAgent
    from base_agent.platform import PlatformAgent

CONFIG_PATH = Path(
    os.environ.get("AGENT_CONFIG_PATH", str(Path(__file__).resolve().parents[2] / "config.yaml"))
)


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
        await platform.create_account(
            agent_id=feeder_agent.agent_id,
            initial_balance=0,
            operation_id=None,
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 409:
            raise

    setup_reference = "fund_feeder_test_setup"
    await platform.credit_account(
        account_id=feeder_agent.agent_id,
        amount=500,
        reference=setup_reference,
        operation_id=operation_id_for("e2e-credit", (feeder_agent.agent_id, setup_reference)),
    )

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
        await platform.create_account(
            agent_id=feeder_agent.agent_id,
            initial_balance=0,
            operation_id=None,
        )
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
    operation_id = operation_id_for("e2e-credit", (funded_feeder.agent_id, reference))
    result = await platform.credit_account(
        account_id=funded_feeder.agent_id,
        amount=100,
        reference=reference,
        operation_id=operation_id,
    )

    assert result["outcome"] == "native_receipt"
    assert result["receipt"]["operation_id"] == operation_id
    assert result["receipt"]["operation_kind"] == "credit"
    assert str(result["receipt"]["legs"][0]["transaction_id"]).startswith("tx-")
    assert isinstance(result["receipt"]["result"]["balance_after"], int)


@pytest.mark.e2e
async def test_credit_idempotency(
    funded_feeder: BaseAgent,
    platform: PlatformAgent,
) -> None:
    """Retrying one credit intent returns its receipt without double-crediting."""
    reference = f"test_idempotent_{uuid4().hex[:8]}"
    operation_id = operation_id_for("e2e-credit", (funded_feeder.agent_id, reference))
    balance_before = (await funded_feeder.get_balance())["balance"]

    first = await platform.credit_account(
        account_id=funded_feeder.agent_id,
        amount=50,
        reference=reference,
        operation_id=operation_id,
    )
    second = await platform.credit_account(
        account_id=funded_feeder.agent_id,
        amount=50,
        reference=reference,
        operation_id=operation_id,
    )

    assert first["receipt"]["operation_id"] == second["receipt"]["operation_id"] == operation_id
    assert (
        first["receipt"]["legs"][0]["transaction_id"]
        == second["receipt"]["legs"][0]["transaction_id"]
    )
    assert (await funded_feeder.get_balance())["balance"] == balance_before + 50


@pytest.mark.e2e
async def test_credit_requires_positive_amount(
    funded_feeder: BaseAgent,
    platform: PlatformAgent,
) -> None:
    """Crediting with zero or negative amount fails."""
    with pytest.raises(httpx.HTTPStatusError) as zero_exc:
        zero_reference = f"test_zero_{uuid4().hex[:8]}"
        await platform.credit_account(
            account_id=funded_feeder.agent_id,
            amount=0,
            reference=zero_reference,
            operation_id=operation_id_for("e2e-credit", (funded_feeder.agent_id, zero_reference)),
        )
    assert zero_exc.value.response.status_code == 400

    with pytest.raises(httpx.HTTPStatusError) as negative_exc:
        negative_reference = f"test_negative_{uuid4().hex[:8]}"
        await platform.credit_account(
            account_id=funded_feeder.agent_id,
            amount=-10,
            reference=negative_reference,
            operation_id=operation_id_for(
                "e2e-credit", (funded_feeder.agent_id, negative_reference)
            ),
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
        operation_id=operation_id_for("e2e-credit", (funded_feeder.agent_id, reference)),
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
    operation_id = operation_id_for("e2e-credit", (funded_feeder.agent_id, reference))
    result = await platform.credit_account(
        account_id=funded_feeder.agent_id,
        amount=amount,
        reference=reference,
        operation_id=operation_id,
    )

    assert result["outcome"] == "native_receipt"
    assert result["receipt"]["operation_id"] == operation_id
    assert str(result["receipt"]["legs"][0]["transaction_id"]).startswith("tx-")
    assert isinstance(result["receipt"]["result"]["balance_after"], int)
    assert result["receipt"]["result"]["balance_after"] >= amount


@pytest.mark.e2e
async def test_credit_without_account_fails(platform: PlatformAgent) -> None:
    """Crediting a non-existent account fails with 404."""
    account_id = "a-00000000-0000-0000-0000-000000000000"
    reference = f"test_missing_account_{uuid4().hex[:8]}"
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await platform.credit_account(
            account_id=account_id,
            amount=10,
            reference=reference,
            operation_id=operation_id_for("e2e-credit", (account_id, reference)),
        )

    assert exc_info.value.response.status_code == 404


@pytest.mark.e2e
async def test_feeder_agent_uses_ed25519_signing(feeder_agent: BaseAgent) -> None:
    """The feeder agent's JWS tokens use Ed25519 signing."""
    token = feeder_agent._sign_jws(
        {"action": "test", "data": "hello"},
        audience="identity",
        resource="POST /agents/verify-jws",
    )
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
            await platform_agent.create_account(
                agent_id=feeder.agent_id,
                initial_balance=0,
                operation_id=None,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 409:
                raise

        reference = "fund_feeder_e2e_test"
        await platform_agent.credit_account(
            account_id=feeder.agent_id,
            amount=amount,
            reference=reference,
            operation_id=operation_id_for("e2e-credit", (feeder.agent_id, reference)),
        )

        balance = await feeder.get_balance()
        assert isinstance(balance["balance"], int)
        assert balance["balance"] >= amount

        print(f"agent_id={feeder.agent_id}")
        print(f"funded_amount={amount}")
        print(f"balance={balance['balance']}")
    finally:
        await feeder.close()
        await platform_agent.close()
