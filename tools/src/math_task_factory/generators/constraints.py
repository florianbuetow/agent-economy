"""Level 12: Constraint satisfaction generators."""

from __future__ import annotations

import random

from math_task_factory.types import MathTask


def gen_number_constraints(rng: random.Random) -> MathTask:
    """Find a number satisfying multiple constraints. Answer is picked first."""
    answer = rng.randint(100, 500)

    constraints: list[str] = []
    constraint_details: list[str] = []

    # Constraint 1: divisibility
    divisor = rng.choice([d for d in [3, 4, 5, 6, 7, 8, 9] if answer % d == 0] or [1])
    if divisor == 1:
        # Force a divisor that works by adjusting answer
        divisor = rng.choice([3, 5, 7])
        answer = answer - (answer % divisor)
    constraints.append(f"divisible by {divisor}")
    constraint_details.append(f"  - The number is divisible by {divisor}.")

    # Constraint 2: remainder with a different divisor
    mod_base = rng.choice([d for d in [11, 13, 17, 19] if d != divisor])
    remainder = answer % mod_base
    constraints.append(f"remainder {remainder} when divided by {mod_base}")
    constraint_details.append(
        f"  - When divided by {mod_base}, the remainder is {remainder}."
    )

    # Constraint 3: digit sum
    digit_sum = sum(int(d) for d in str(abs(answer)))
    constraints.append(f"digits sum to {digit_sum}")
    constraint_details.append(f"  - The sum of its digits is {digit_sum}.")

    # Define the search range
    lo = max(1, answer - rng.randint(50, 150))
    hi = answer + rng.randint(50, 150)

    constraints_text = "\n".join(constraint_details)
    return MathTask(
        title="Find a number satisfying constraints",
        spec=f"""TASK: Find a number between {lo} and {hi} (inclusive) that satisfies ALL of the
following constraints:
{constraints_text}

OUTPUT FORMAT: A single integer.

VERIFICATION: Check each constraint against the answer.""",
        solutions=[str(answer)],
        level=12,
        problem_type="number_constraints",
    )


def gen_scheduling_constraints(rng: random.Random) -> MathTask:
    """N tasks with durations and dependencies (DAG). Find earliest completion time."""
    num_tasks = rng.randint(5, 7)
    task_labels = [chr(ord("A") + i) for i in range(num_tasks)]
    durations = {t: rng.randint(1, 8) for t in task_labels}

    # Build a DAG: each task (except first) depends on 1-2 earlier tasks
    deps: dict[str, list[str]] = {task_labels[0]: []}
    for i in range(1, num_tasks):
        num_deps = rng.randint(1, min(2, i))
        dep_pool = task_labels[:i]
        deps[task_labels[i]] = rng.sample(dep_pool, num_deps)

    # Compute earliest start times via topological order
    earliest_start: dict[str, int] = {}
    for t in task_labels:
        if not deps[t]:
            earliest_start[t] = 0
        else:
            earliest_start[t] = max(
                earliest_start[d] + durations[d] for d in deps[t]
            )

    earliest_finish = {t: earliest_start[t] + durations[t] for t in task_labels}
    answer = max(earliest_finish.values())

    # Format task descriptions
    task_lines: list[str] = []
    for t in task_labels:
        dep_str = ", ".join(deps[t]) if deps[t] else "none"
        task_lines.append(
            f"  - Task {t}: duration = {durations[t]} hours, "
            f"depends on: {dep_str}"
        )
    tasks_text = "\n".join(task_lines)

    return MathTask(
        title="Project scheduling (critical path)",
        spec=f"""TASK: A project has {num_tasks} tasks. Each task has a duration and may depend
on other tasks (a task cannot start until all its dependencies are finished).
Tasks with no shared dependency can run in parallel.

{tasks_text}

What is the earliest time (in hours) at which ALL tasks can be completed?

OUTPUT FORMAT: A single integer (number of hours).

VERIFICATION: Find the critical path (longest dependency chain) through the DAG.""",
        solutions=[str(answer)],
        level=12,
        problem_type="scheduling_constraints",
    )


def gen_allocation_constraints(rng: random.Random) -> MathTask:
    """Distribute N items across M bins with min/max constraints."""
    num_bins = rng.randint(3, 4)
    bin_names = [f"Bin {chr(ord('A') + i)}" for i in range(num_bins)]

    # Pick a valid allocation first
    allocation: dict[str, int] = {}
    for bn in bin_names:
        allocation[bn] = rng.randint(10, 80)

    total = sum(allocation.values())

    # Build constraints from the allocation
    constraint_lines: list[str] = []
    for bn in bin_names:
        val = allocation[bn]
        lo = max(0, val - rng.randint(5, 15))
        hi = val + rng.randint(5, 15)
        constraint_lines.append(f"  - {bn}: between {lo} and {hi} items (inclusive).")

    # Add a divisibility constraint on one bin
    target_bin = rng.choice(bin_names)
    target_val = allocation[target_bin]
    divisor = rng.choice([d for d in [2, 3, 5] if target_val % d == 0] or [1])
    if divisor == 1:
        divisor = 2
        allocation[target_bin] = target_val - (target_val % 2)
        total = sum(allocation.values())
    constraint_lines.append(
        f"  - {target_bin} must hold a number of items divisible by {divisor}."
    )

    constraints_text = "\n".join(constraint_lines)
    alloc_text = ", ".join(f"{bn}: {allocation[bn]}" for bn in bin_names)

    return MathTask(
        title="Item allocation with constraints",
        spec=f"""TASK: Distribute exactly {total} items across {num_bins} bins
subject to these constraints:
{constraints_text}

Provide the number of items in each bin.

OUTPUT FORMAT: {', '.join(f'{bn}: N' for bn in bin_names)} (e.g. "{alloc_text}")

VERIFICATION: Check that the total equals {total} and all constraints are satisfied.""",
        solutions=[alloc_text],
        level=12,
        problem_type="allocation_constraints",
    )
