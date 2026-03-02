"""Fund the feeder agent with initial coins.

Usage::

    cd agents/
    uv run python -m fund_feeder_cli <amount>

Example::

    cd agents/
    uv run python -m fund_feeder_cli 500
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from uuid import uuid4

import httpx

from base_agent.factory import AgentFactory


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fund the feeder agent with initial coins via the platform agent.",
    )
    parser.add_argument(
        "amount",
        type=int,
        help="Amount of coins to credit to the feeder agent (positive integer).",
    )
    return parser.parse_args()


async def _fund(amount: int) -> None:
    logger = logging.getLogger("fund_feeder_cli")

    if amount <= 0:
        logger.error("Amount must be a positive integer, got %d", amount)
        sys.exit(1)

    factory = AgentFactory()
    feeder = factory.create_agent("feeder")
    platform = factory.platform_agent()

    try:
        # Step 1: Register both agents with Identity service
        await platform.register()
        logger.info("Platform agent registered: agent_id=%s", platform.agent_id)

        await feeder.register()
        logger.info("Feeder agent registered: agent_id=%s", feeder.agent_id)

        feeder_id = feeder.agent_id
        if feeder_id is None:
            logger.error("Feeder registration did not return an agent_id")
            sys.exit(1)

        # Step 2: Ensure feeder has a bank account (idempotent)
        try:
            await platform.create_account(agent_id=feeder_id, initial_balance=0)
            logger.info("Bank account created for feeder")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 409:
                logger.info("Bank account already exists for feeder")
            else:
                raise

        # Step 3: Credit funds via platform agent
        reference = f"fund_feeder_{uuid4().hex[:8]}"
        result = await platform.credit_account(
            account_id=feeder_id,
            amount=amount,
            reference=reference,
        )
        logger.info(
            "Credited %d coins (tx_id=%s, balance_after=%s)",
            amount,
            result["tx_id"],
            result["balance_after"],
        )

        # Step 4: Verify balance
        balance_info = await feeder.get_balance()
        balance = balance_info["balance"]

        # Step 5: Print summary
        print(f"agent_id={feeder_id}")
        print(f"funded_amount={amount}")
        print(f"balance={balance}")

    finally:
        await feeder.close()
        await platform.close()


def main() -> None:
    """Sync entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stderr,
    )
    args = _parse_args()
    asyncio.run(_fund(args.amount))


if __name__ == "__main__":
    main()
