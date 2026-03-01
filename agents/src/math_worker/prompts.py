"""Prompt templates for each LLM decision point in the Math Worker Agent."""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# System prompts (one per decision type)
# ---------------------------------------------------------------------------

TASK_SELECTION_SYSTEM = """\
You are a math worker agent in a task economy.  You earn money by solving
math problems posted on a task board.

You will be shown a list of open tasks.  Each task has a title, a reward,
and a specification describing the math problem.

Your job: pick the ONE task you are most confident you can solve correctly.
Consider the reward, the difficulty of the problem, and your ability to
produce the exact answer format requested.

Respond with ONLY the task_id of your chosen task.  Nothing else.
If none of the tasks are solvable, respond with NONE."""

BID_AMOUNT_SYSTEM = """\
You are a math worker agent deciding how much to bid on a task.

You will be shown:
- The task specification (the math problem)
- The reward offered by the poster
- Your current account balance

Your bid amount must be between 1 and the task reward (inclusive).
A lower bid makes you more competitive but earns less.
A higher bid earns more but may lose to cheaper competitors.

Respond with ONLY a single integer: your bid amount.  Nothing else."""

SOLVE_PROBLEM_SYSTEM = """\
You are a math solver.  You will be given a math problem specification.

Read the specification carefully.  It tells you:
- What to calculate
- What output format to use
- How to verify your answer

Solve the problem step by step, then give your final answer on the
last line in EXACTLY the format requested by the specification.

Your final line must start with "ANSWER: " followed by your answer.
Example: ANSWER: 42
Example: ANSWER: x=5, y=3
Example: ANSWER: yes"""

DISPUTE_REBUTTAL_SYSTEM = """\
You are a math worker agent defending your submitted solution in a dispute.

You will be shown:
- The original task specification
- Your submitted solution
- The reason the poster rejected your work

Write a clear, factual rebuttal explaining why your solution is correct,
or acknowledge the error if you made one.  Reference the specification
requirements directly.  Be concise and precise."""


# ---------------------------------------------------------------------------
# User prompt builders
# ---------------------------------------------------------------------------


def build_task_selection_prompt(
    tasks: list[dict[str, Any]],
    balance: int,
) -> str:
    """Format the list of open tasks for the task-selection decision.

    Args:
        tasks:   List of task dicts from the task board API.
        balance: The agent's current account balance.

    Returns:
        A user prompt string.
    """
    lines = [f"Your current balance: {balance} credits.", "", "Open tasks:", ""]
    for task in tasks:
        task_id = task.get("task_id", "unknown")
        title = task.get("title", "untitled")
        reward = task.get("reward", 0)
        spec = task.get("spec", "no spec")
        lines.append(f"--- task_id: {task_id} ---")
        lines.append(f"Title: {title}")
        lines.append(f"Reward: {reward}")
        lines.append(f"Spec: {spec}")
        lines.append("")
    return "\n".join(lines)


def build_bid_amount_prompt(
    task: dict[str, Any],
    balance: int,
) -> str:
    """Format the task details for the bid-amount decision.

    Args:
        task:    Full task dict from the task board API.
        balance: The agent's current account balance.

    Returns:
        A user prompt string.
    """
    reward = task.get("reward", 0)
    spec = task.get("spec", "no spec")
    title = task.get("title", "untitled")
    return (
        f"Task: {title}\n"
        f"Reward: {reward}\n"
        f"Your balance: {balance}\n\n"
        f"Specification:\n{spec}"
    )


def build_solve_prompt(task: dict[str, Any]) -> str:
    """Format the task spec for the solve-problem decision.

    Args:
        task: Full task dict from the task board API.

    Returns:
        A user prompt string.
    """
    spec = task.get("spec", "no spec")
    title = task.get("title", "untitled")
    return f"Problem: {title}\n\n{spec}"


def build_rebuttal_prompt(
    task: dict[str, Any],
    submitted_solution: str,
    rejection_reason: str,
) -> str:
    """Format context for the dispute-rebuttal decision.

    Args:
        task:               Full task dict from the task board API.
        submitted_solution: The solution text the agent uploaded.
        rejection_reason:   The poster's stated reason for disputing.

    Returns:
        A user prompt string.
    """
    spec = task.get("spec", "no spec")
    title = task.get("title", "untitled")
    return (
        f"Task: {title}\n\n"
        f"Specification:\n{spec}\n\n"
        f"Your submitted solution:\n{submitted_solution}\n\n"
        f"Rejection reason:\n{rejection_reason}"
    )
