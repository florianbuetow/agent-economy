"""Level 10: Chain arithmetic generators — sequential computation on a running total."""

from __future__ import annotations

import random

from math_task_factory.types import MathTask


def gen_chain_arithmetic(rng: random.Random) -> MathTask:
    """5-8 operations (add, subtract, multiply, integer divide) on a running total."""
    num_ops = rng.randint(5, 8)
    start = rng.randint(100, 999)
    total = start
    steps: list[str] = [f"Start with {start}."]

    for _ in range(num_ops):
        op = rng.choice(["add", "subtract", "multiply", "divide"])
        if op == "add":
            val = rng.randint(10, 500)
            total += val
            steps.append(f"Add {val}.")
        elif op == "subtract":
            val = rng.randint(10, min(300, abs(total) + 100))
            total -= val
            steps.append(f"Subtract {val}.")
        elif op == "multiply":
            val = rng.randint(2, 5)
            total *= val
            steps.append(f"Multiply by {val}.")
        else:
            divisors = [d for d in [2, 3, 4, 5, 7] if total != 0]
            if not divisors:
                val = rng.randint(10, 100)
                total += val
                steps.append(f"Add {val}.")
            else:
                val = rng.choice(divisors)
                total = total // val
                steps.append(f"Divide by {val} (integer division, round toward zero).")

    steps_text = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(steps))
    return MathTask(
        title="Chain arithmetic operations",
        spec=f"""TASK: Perform the following operations in sequence, keeping a running total:
{steps_text}

What is the final result?

OUTPUT FORMAT: A single integer.

VERIFICATION: Apply each operation sequentially from the starting value.""",
        solutions=[str(total)],
        level=10,
        problem_type="chain_arithmetic",
    )


def gen_chain_percentage(rng: random.Random) -> MathTask:
    """Start with a value, apply 3-5 percentage operations. Track rounding at each step."""
    num_ops = rng.randint(3, 5)
    start = rng.randint(1000, 9999)
    total = start
    steps: list[str] = [f"Start with {start}."]

    for _ in range(num_ops):
        if rng.random() < 0.5:
            pct = rng.choice([5, 8, 10, 12, 15, 20, 25])
            increase = total * pct // 100
            total = total + increase
            steps.append(
                f"Increase by {pct}% (compute {pct}% of current value using "
                f"integer division, then add it)."
            )
        else:
            pct = rng.choice([5, 8, 10, 12, 15, 20, 25])
            decrease = total * pct // 100
            total = total - decrease
            steps.append(
                f"Decrease by {pct}% (compute {pct}% of current value using "
                f"integer division, then subtract it)."
            )

    steps_text = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(steps))
    return MathTask(
        title="Chain percentage operations",
        spec=f"""TASK: Perform the following percentage operations in sequence:
{steps_text}

Important: At each step, compute the percentage of the CURRENT value using
integer division (floor toward zero), then add or subtract it.

What is the final result?

OUTPUT FORMAT: A single integer.

VERIFICATION: Apply each percentage operation step by step, rounding down at each step.""",
        solutions=[str(total)],
        level=10,
        problem_type="chain_percentage",
    )


def gen_chain_remainder(rng: random.Random) -> MathTask:
    """Interleave modulo operations with arithmetic."""
    start = rng.randint(500, 2000)
    total = start
    steps: list[str] = [f"Start with {start}."]
    num_ops = rng.randint(4, 6)

    for _ in range(num_ops):
        op = rng.choice(["mod", "multiply", "add"])
        if op == "mod":
            mod_val = rng.randint(11, 50)
            total = total % mod_val
            steps.append(f"Take modulo {mod_val} (remainder when divided by {mod_val}).")
        elif op == "multiply":
            val = rng.randint(3, 20)
            total = total * val
            steps.append(f"Multiply by {val}.")
        else:
            val = rng.randint(5, 100)
            total = total + val
            steps.append(f"Add {val}.")

    steps_text = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(steps))
    return MathTask(
        title="Chain arithmetic with modulo",
        spec=f"""TASK: Perform the following operations in sequence:
{steps_text}

What is the final result?

OUTPUT FORMAT: A single non-negative integer.

VERIFICATION: Apply each operation sequentially from the starting value.""",
        solutions=[str(total)],
        level=10,
        problem_type="chain_remainder",
    )
