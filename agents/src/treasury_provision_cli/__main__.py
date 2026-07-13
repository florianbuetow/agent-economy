"""Provision the treasury account (idempotent).

Q-9 decision (docs/plans/2026-07-10-q9-treasury-bootstrap-decision.md): treasury
genesis is an explicit bootstrap step, run once via ``just provision`` — never
minted automatically by any single service's startup. Re-running this command
is safe: account creation is idempotent (409 on an existing account) and the
credit itself is idempotent via the Central Bank's replay guard on
``(account_id, reference)`` — the ``treasury.genesis_reference`` config value.

Usage::

    cd agents/
    uv run python -m treasury_provision_cli
"""

from __future__ import annotations

import asyncio
import logging
import sys

import httpx

from base_agent.factory import AgentFactory
from treasury_provision_cli.config import load_treasury_settings


async def _provision() -> None:
    logger = logging.getLogger("treasury_provision_cli")

    settings = load_treasury_settings()
    factory = AgentFactory()
    operator = factory.create_agent(settings.handle)
    platform = factory.platform_agent()

    try:
        # Step 1: Register both agents with Identity service
        await platform.register()
        logger.info("Platform agent registered: agent_id=%s", platform.agent_id)

        await operator.register()
        logger.info("Operator agent registered: agent_id=%s", operator.agent_id)

        operator_id = operator.agent_id
        if operator_id is None:
            logger.error("Operator registration did not return an agent_id")
            sys.exit(1)

        # Step 2: Ensure the operator has a bank account (idempotent)
        try:
            await platform.create_account(agent_id=operator_id, initial_balance=0)
            logger.info("Bank account created for operator")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 409:
                logger.info("Bank account already exists for operator")
            else:
                raise

        # Step 3: Credit the treasury genesis amount (idempotent via reference)
        result = await platform.credit_account(
            account_id=operator_id,
            amount=settings.genesis_amount,
            reference=settings.genesis_reference,
        )
        logger.info(
            "Treasury provisioned (tx_id=%s, balance_after=%s)",
            result["tx_id"],
            result["balance_after"],
        )

        # Step 4: Verify balance
        balance_info = await operator.get_balance()
        balance = balance_info["balance"]

        # Step 5: Print summary
        print(f"agent_id={operator_id}")
        print(f"genesis_amount={settings.genesis_amount}")
        print(f"balance={balance}")

    finally:
        await operator.close()
        await platform.close()


def main() -> None:
    """Sync entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stderr,
    )
    asyncio.run(_provision())


if __name__ == "__main__":
    main()
