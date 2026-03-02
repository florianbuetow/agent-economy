# Fund-Feeder Implementation — Codex Task

> Read AGENTS.md FIRST for project conventions.
> Use `uv run` for all Python execution. Never use raw python or pip install.
> Do NOT modify any existing test files.
> Commit after each phase.

## Context

The feeder agent needs initial funds before it can post tasks (posting locks escrow).
The existing agent SDK already has everything needed:
- `AgentFactory` creates agents by roster handle
- `PlatformAgent.credit_account()` credits funds to any account
- `BaseAgent.register()` handles identity registration (idempotent)
- `BaseAgent.create_account()` creates a bank account (idempotent)

The implementation creates a small Python script that orchestrates these calls.
The e2e tests already exist at `agents/tests/e2e/test_fund_feeder.py` and validate
the exact workflow this script must perform.

## Design Decision

The original ticket says "create tools/fund-feeder.sh" (a bash script with curl).
We are instead creating a Python script at `agents/src/fund_feeder_cli/__main__.py`
because:
1. The agent SDK already handles Ed25519 signing, JWS creation, and idempotent registration
2. A bash script would reimplement 200+ lines of crypto that already exists in Python
3. The script runs via `cd agents && uv run python -m fund_feeder_cli <amount>`

## Files to Read FIRST

Read these files in order before doing anything:

1. `AGENTS.md` — project conventions, architecture, testing rules
2. `agents/src/base_agent/factory.py` — AgentFactory (creates agents by handle)
3. `agents/src/base_agent/platform.py` — PlatformAgent (credit_account, create_account)
4. `agents/src/base_agent/agent.py` — BaseAgent (register, get_balance, close)
5. `agents/src/base_agent/config.py` — AgentConfig and load_agent_config()
6. `agents/src/base_agent/mixins/identity.py` — IdentityMixin.register()
7. `agents/src/base_agent/mixins/bank.py` — BankMixin (create_account, get_balance)
8. `agents/src/task_feeder/__main__.py` — existing feeder entry point (reference pattern)
9. `agents/src/task_feeder/config.py` — TaskFeederConfig (reference pattern)
10. `agents/config.yaml` — agent config (service URLs, key paths)
11. `agents/roster.yaml` — agent roster (platform, feeder handles)
12. `agents/tests/e2e/test_fund_feeder.py` — existing e2e tests (your target spec)
13. `justfile` — root justfile (see existing patterns for start-feeder, start-mathbot)

After reading ALL files, execute Phase 1 through Phase 4 in order. Do NOT skip phases.

=== PHASE 1: Create the fund_feeder_cli package ===

Create these files:

### File 1: `agents/src/fund_feeder_cli/__init__.py`

Empty file, just a package marker:
```python
"""Fund-feeder CLI — gives the feeder agent initial funds via the platform agent."""
```

### File 2: `agents/src/fund_feeder_cli/__main__.py`

This is the main script. It must:

1. Accept exactly ONE positional CLI argument: the funding amount (integer, required, no default)
2. Parse via `argparse` with a clear description
3. Run an async main function via `asyncio.run()`
4. Print a summary on success: `agent_id=<id>`, `funded_amount=<amount>`, `balance=<balance>`
5. Exit 0 on success, exit 1 on any failure

**Implementation (follow this EXACTLY):**

```python
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

        # Step 2: Ensure feeder has a bank account (idempotent)
        try:
            await platform.create_account(agent_id=feeder.agent_id, initial_balance=0)
            logger.info("Bank account created for feeder")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 409:
                logger.info("Bank account already exists for feeder")
            else:
                raise

        # Step 3: Credit funds via platform agent
        reference = f"fund_feeder_{uuid4().hex[:8]}"
        result = await platform.credit_account(
            account_id=feeder.agent_id,
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
        print(f"agent_id={feeder.agent_id}")
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
```

**IMPORTANT constraints:**
- The `amount` argument has NO default value — it is required
- Logging goes to stderr, summary output goes to stdout (so it can be piped)
- Uses `AgentFactory()` with no args — it resolves config.yaml automatically
- Reference is unique per invocation via uuid4 prefix
- All agents are closed in the finally block
- Exit code is 0 on success (implicit), 1 on failure (sys.exit)

### Verification (Phase 1):

Check the files exist and are valid Python:
```bash
cd agents && uv run python -c "import fund_feeder_cli; print('OK')"
```

Then check the help message works:
```bash
cd agents && uv run python -m fund_feeder_cli --help
```

Expected output should show the argument description. If this fails, fix it before proceeding.

=== PHASE 2: Add justfile target ===

Edit the root `justfile` to add a `fund-feeder` target. This target must be added
in the "Local Development" section, AFTER the `start-mathbot` / `stop-mathbot` targets
and BEFORE the `status` target.

### Add to help section

In the help target, add this line after the `just stop-mathbot` line and before the
`just status` line:

```
    @printf "  \033[0;37mjust fund-feeder <amount>\033[0;34m Fund the feeder agent with initial coins\033[0m\n"
```

### Add the target

Add this target after the `stop-mathbot` target and before the `status` target:

```just
# Fund the feeder agent with initial coins
fund-feeder amount:
    #!/usr/bin/env bash
    printf "\n"
    printf "\033[0;34m=== Funding Feeder Agent ===\033[0m\n"
    printf "\n"
    cd agents && uv run python -m fund_feeder_cli {{amount}}
    exit_code=$?
    printf "\n"
    if [ $exit_code -eq 0 ]; then
        printf "\033[0;32m✓ Feeder agent funded successfully\033[0m\n"
    else
        printf "\033[0;31m✗ Failed to fund feeder agent\033[0m\n"
        exit 1
    fi
    printf "\n"
```

### Verification (Phase 2):

Check the justfile is valid:
```bash
just --list | grep fund-feeder
```

Should show the fund-feeder target. If `just --list` fails, the justfile has a syntax error — fix it.

=== PHASE 3: Run Verification ===

### 3a: Run the script manually (requires running services)

Check if services are running first:
```bash
just status
```

If services are NOT running, skip to Phase 3b. Do NOT try to start them.

If services ARE running:
```bash
cd agents && uv run python -m fund_feeder_cli 500
```

Expected output (on stdout):
```
agent_id=<some-uuid>
funded_amount=500
balance=<some-number->=500>
```

Exit code should be 0. Verify:
```bash
echo $?
```

### 3b: Run the existing e2e tests

If services ARE running:
```bash
cd agents && uv run pytest tests/e2e/test_fund_feeder.py -v --tb=short 2>&1 | tail -30
```

All 10 tests should pass. If they fail, your implementation broke something — investigate.

If services are NOT running, skip this step and note it.

### 3c: Run CI lint checks on the new file

```bash
cd agents && uv run ruff check src/fund_feeder_cli/ && uv run ruff format --check src/fund_feeder_cli/
```

Fix any formatting or linting errors. Common issues:
- Import ordering
- Line length > 100 characters
- Unused imports

After fixing, re-run the check.

Also run mypy:
```bash
cd agents && uv run mypy src/fund_feeder_cli/
```

Fix any type errors.

=== PHASE 4: Commit ===

Stage and commit the new files:
```bash
git add agents/src/fund_feeder_cli/__init__.py agents/src/fund_feeder_cli/__main__.py justfile
git commit -m "feat: add fund-feeder CLI to credit feeder agent via platform agent

Adds agents/src/fund_feeder_cli/ — a Python CLI that uses AgentFactory
to instantiate both the platform and feeder agents, registers the feeder,
creates a bank account, and credits initial funds via the platform agent.

Usage: cd agents && uv run python -m fund_feeder_cli <amount>
Also: just fund-feeder <amount>

Closes: agent-economy-96r"
```

Do NOT push to remote. Only commit locally.

## Summary of Deliverables

1. `agents/src/fund_feeder_cli/__init__.py` — package marker
2. `agents/src/fund_feeder_cli/__main__.py` — CLI script (~80 lines)
3. `justfile` — updated with `fund-feeder` target and help entry
