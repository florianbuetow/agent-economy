"""Level 11: Multi-variable state tracking generators."""

from __future__ import annotations

import random

from math_task_factory.types import MathTask

_FLOOR_NAMES = ["Floor A", "Floor B", "Floor C", "Floor D", "Floor E"]


def gen_warehouse_inventory(rng: random.Random) -> MathTask:
    """3-5 floors with initial stock, 3-5 operations, question about totals."""
    num_floors = rng.randint(3, 5)
    floors = _FLOOR_NAMES[:num_floors]
    stock = {f: rng.randint(100, 999) for f in floors}

    num_ops = rng.randint(3, 5)
    ops_text: list[str] = []
    initial_text = ", ".join(f"{f}: {stock[f]} units" for f in floors)

    for _ in range(num_ops):
        op = rng.choice(["receive", "ship", "transfer"])
        if op == "receive":
            floor = rng.choice(floors)
            amount = rng.randint(50, 300)
            stock[floor] += amount
            ops_text.append(f"Receive a shipment of {amount} units on {floor}.")
        elif op == "ship":
            floor = rng.choice(floors)
            pct = rng.choice([10, 15, 20, 25, 30])
            shipped = stock[floor] * pct // 100
            stock[floor] -= shipped
            ops_text.append(
                f"Ship {pct}% of {floor}'s inventory (integer division = {shipped} units)."
            )
        else:
            src, dst = rng.sample(floors, 2)
            amount = rng.randint(20, min(200, stock[src]))
            stock[src] -= amount
            stock[dst] += amount
            ops_text.append(f"Transfer {amount} units from {src} to {dst}.")

    # Question: ask for total or specific floor
    if rng.random() < 0.5:
        answer = sum(stock.values())
        question = "What is the TOTAL inventory across all floors?"
    else:
        target = rng.choice(floors)
        answer = stock[target]
        question = f"How many units are on {target}?"

    ops_numbered = "\n".join(f"  {i+1}. {o}" for i, o in enumerate(ops_text))
    return MathTask(
        title="Warehouse inventory tracking",
        spec=f"""TASK: A warehouse has {num_floors} floors with initial inventory:
  {initial_text}

The following operations occur in order:
{ops_numbered}

{question}

OUTPUT FORMAT: A single integer.

VERIFICATION: Track each floor's inventory through every operation.""",
        solutions=[str(answer)],
        level=11,
        problem_type="warehouse_inventory",
    )


_ACCOUNT_NAMES = ["Account Alpha", "Account Beta", "Account Gamma", "Account Delta"]


def gen_bank_transactions(rng: random.Random) -> MathTask:
    """3-4 accounts with sequential deposits, withdrawals, interest, transfers."""
    num_accounts = rng.randint(3, 4)
    accounts = _ACCOUNT_NAMES[:num_accounts]
    balance = {a: rng.randint(1000, 9999) for a in accounts}

    num_ops = rng.randint(4, 6)
    ops_text: list[str] = []
    initial_text = ", ".join(f"{a}: ${balance[a]}" for a in accounts)

    for _ in range(num_ops):
        op = rng.choice(["deposit", "withdraw", "interest", "transfer"])
        if op == "deposit":
            acct = rng.choice(accounts)
            amount = rng.randint(100, 2000)
            balance[acct] += amount
            ops_text.append(f"Deposit ${amount} into {acct}.")
        elif op == "withdraw":
            acct = rng.choice(accounts)
            amount = rng.randint(50, min(1000, balance[acct]))
            balance[acct] -= amount
            ops_text.append(f"Withdraw ${amount} from {acct}.")
        elif op == "interest":
            acct = rng.choice(accounts)
            rate_pct = rng.choice([2, 3, 5, 8, 10])
            interest_amount = balance[acct] * rate_pct // 100
            balance[acct] += interest_amount
            ops_text.append(
                f"Apply {rate_pct}% interest to {acct} "
                f"(integer division, round down)."
            )
        else:
            src, dst = rng.sample(accounts, 2)
            amount = rng.randint(100, min(1500, balance[src]))
            balance[src] -= amount
            balance[dst] += amount
            ops_text.append(f"Transfer ${amount} from {src} to {dst}.")

    target = rng.choice(accounts)
    answer = balance[target]
    ops_numbered = "\n".join(f"  {i+1}. {o}" for i, o in enumerate(ops_text))

    return MathTask(
        title="Bank account transactions",
        spec=f"""TASK: There are {num_accounts} bank accounts with initial balances:
  {initial_text}

The following transactions occur in order:
{ops_numbered}

What is the final balance of {target}?

OUTPUT FORMAT: A single integer (dollar amount, no $ sign).

VERIFICATION: Track each account's balance through every transaction.""",
        solutions=[str(answer)],
        level=11,
        problem_type="bank_transactions",
    )


def gen_production_pipeline(rng: random.Random) -> MathTask:
    """Raw materials through 3-4 processing stages with yield/defect rates."""
    num_stages = rng.randint(3, 4)
    raw_input = rng.randint(500, 5000)
    current = raw_input

    stages: list[str] = []
    stage_names = ["Stage 1 (Cutting)", "Stage 2 (Assembly)", "Stage 3 (Quality Check)",
                   "Stage 4 (Finishing)"]

    for i in range(num_stages):
        yield_pct = rng.choice([80, 85, 88, 90, 92, 95])
        defect_pct = rng.choice([2, 3, 5, 8, 10])
        after_yield = current * yield_pct // 100
        defects = after_yield * defect_pct // 100
        after_defects = after_yield - defects
        stages.append(
            f"{stage_names[i]}: {yield_pct}% yield (integer division), "
            f"then {defect_pct}% defect rate (integer division, defective items removed)."
        )
        current = after_defects

    stages_text = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(stages))
    return MathTask(
        title="Production pipeline output",
        spec=f"""TASK: A factory starts with {raw_input} raw units and processes them
through {num_stages} stages:
{stages_text}

At each stage:
  - First compute the yield: multiply current count by yield percentage
    using integer division.
  - Then compute defects: multiply the yield result by defect percentage
    using integer division. Subtract defects from the yield result.

How many finished units come out at the end?

OUTPUT FORMAT: A single integer.

VERIFICATION: Track the count through each stage, applying integer division at each step.""",
        solutions=[str(current)],
        level=11,
        problem_type="production_pipeline",
    )
