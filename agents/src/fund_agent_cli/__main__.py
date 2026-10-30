"""Credit coins to any registered agent through the platform agent.

Usage::

    cd agents/
    uv run python -m fund_agent_cli --agent-id <id> --amount <n> --operation-id <op-UUID> \\
        [--reference <text>]

The credit is idempotent on ``--operation-id``: replaying the same operation id
returns the original receipt and does not credit twice. Exit code 1 on any failure,
with the server error code printed to stderr as ``error=<CODE>``.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Any

import httpx
from service_auth.financial_responses import LegacyFinancialEnvelope

from base_agent.factory import AgentFactory

_LOGGER_NAME = "fund_agent_cli"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Credit coins to a registered agent via the platform agent.",
    )
    parser.add_argument("--agent-id", required=True, help="Agent id to credit.")
    parser.add_argument("--amount", required=True, type=int, help="Positive integer amount.")
    parser.add_argument(
        "--operation-id",
        required=True,
        help="Caller-owned canonical op-UUID; reuse it when retrying this funding intent.",
    )
    parser.add_argument(
        "--reference",
        required=False,
        default=None,
        help="Funding reference; when omitted, the operation id is used.",
    )
    return parser.parse_args(argv)


async def credit_agent(
    platform: Any,
    agent_id: str,
    amount: int,
    operation_id: str,
    reference: str,
) -> dict[str, Any]:
    """Ensure the bank account exists, credit it, and return the validated receipt."""
    logger = logging.getLogger(_LOGGER_NAME)
    try:
        await platform.create_account(agent_id=agent_id, initial_balance=0, operation_id=None)
        logger.info("Bank account created for %s", agent_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 409:
            raise
        logger.info("Bank account already exists for %s", agent_id)

    result = await platform.credit_account(
        account_id=agent_id,
        amount=amount,
        reference=reference,
        operation_id=operation_id,
    )
    if result["outcome"] == "legacy_observation":
        legacy = LegacyFinancialEnvelope.model_validate(result)
        raise RuntimeError(
            "Funding received a historical observation "
            f"(tx_id={legacy.observation.transaction.tx_id}, "
            f"observed_fact_sha256={legacy.observation.observed_fact_sha256}); "
            "no native operation confirmation was created"
        )
    receipt: dict[str, Any] = result["receipt"]
    if (
        result["outcome"] != "native_receipt"
        or receipt["operation_id"] != operation_id
        or receipt["operation_kind"] != "credit"
    ):
        raise RuntimeError("Funding returned an unexpected receipt")
    logger.info(
        "Funding confirmed for %d coins (operation_id=%s, tx_id=%s, balance_after=%s)",
        amount,
        receipt["operation_id"],
        receipt["legs"][0]["transaction_id"],
        receipt["result"]["balance_after"],
    )
    return receipt


def describe_failure(exc: BaseException) -> str:
    """Return a one-line failure description including the server error code if present."""
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            body = exc.response.json()
        except ValueError:
            body = None
        code = body.get("error") if isinstance(body, dict) else None
        message = body.get("message") if isinstance(body, dict) else None
        return f"error={code or 'UNKNOWN'} status={exc.response.status_code} message={message}"
    return f"error=CLIENT_ERROR message={exc}"


async def fund_agent(agent_id: str, amount: int, operation_id: str, reference: str) -> None:
    """Register the platform agent, credit ``agent_id`` and print a summary."""
    if amount <= 0:
        logging.getLogger(_LOGGER_NAME).error("Amount must be a positive integer, got %d", amount)
        sys.exit(1)

    platform = AgentFactory().platform_agent()
    try:
        await platform.register()
        receipt = await credit_agent(platform, agent_id, amount, operation_id, reference)
        print(f"agent_id={agent_id}")
        print(f"funded_amount={amount}")
        print(f"balance={receipt['result']['balance_after']}")
        print(f"operation_id={receipt['operation_id']}")
    finally:
        await platform.close()


def main() -> None:
    """Sync entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stderr,
    )
    args = _parse_args(sys.argv[1:])
    reference = args.operation_id if args.reference is None else args.reference
    try:
        asyncio.run(fund_agent(args.agent_id, args.amount, args.operation_id, reference))
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        print(describe_failure(exc), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
