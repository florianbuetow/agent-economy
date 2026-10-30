"""Fund the feeder agent with initial coins.

Usage::

    cd agents/
    uv run python -m fund_feeder_cli <amount> --operation-id <op-UUID> --reference <reference>

Example::

    cd agents/
    uv run python -m fund_feeder_cli 500 \\
        --operation-id op-00000000-0000-4000-8000-000000000001 --reference manual-funding-1
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from base_agent.factory import AgentFactory
from fund_agent_cli.__main__ import credit_agent


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fund the feeder agent with initial coins via the platform agent.",
    )
    parser.add_argument(
        "amount",
        type=int,
        help="Amount of coins to credit to the feeder agent (positive integer).",
    )
    parser.add_argument(
        "--operation-id",
        required=True,
        help="Caller-owned canonical op-UUID; reuse it when retrying this funding intent.",
    )
    parser.add_argument(
        "--reference",
        required=True,
        help="Stable funding reference; reuse it when retrying the same intent.",
    )
    return parser.parse_args()


async def _fund(amount: int, operation_id: str, reference: str) -> None:
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

        # Step 2-3: Ensure the bank account exists and credit it (shared with fund_agent_cli)
        receipt = await credit_agent(platform, feeder_id, amount, operation_id, reference)

        # Step 4: Verify balance
        balance_info = await feeder.get_balance()
        balance = balance_info["balance"]

        # Step 5: Print summary
        print(f"agent_id={feeder_id}")
        print(f"funded_amount={amount}")
        print(f"balance={balance}")
        print(f"operation_id={receipt['operation_id']}")

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
    asyncio.run(_fund(args.amount, args.operation_id, args.reference))


if __name__ == "__main__":
    main()
