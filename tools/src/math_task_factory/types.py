"""Shared types for math_task_factory — no internal imports to avoid circular dependencies."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class MathTask:
    """A mathematical problem task with title, spec, and solution(s)."""

    title: str
    spec: str
    solutions: list[str]
    level: int
    problem_type: str
    solution_note: str | None = field(default=None)


ProblemType = Literal[
    # Levels 1-9 (original)
    "addition_positive",
    "addition_positive_text",
    "addition_signed",
    "addition_small",
    "addition_small_text",
    "addition_large",
    "addition_large_text",
    "addition_float",
    "subtraction",
    "multiplication",
    "multiplication_text",
    "multiplication_small",
    "multiplication_large",
    "multiplication_float",
    "division",
    "division_float",
    "order_of_operations",
    "modulo",
    "modulo_text",
    "modulo_large",
    "single_variable_add_sub",
    "single_variable_add_sub_text",
    "single_variable_mul_div",
    "single_variable_combined",
    "system_solvable",
    "system_unsolvable",
    "system_infinite",
    "exponential",
    "exponential_equation",
    "square_root",
    "logarithm",
    "prime_check",
    "cube_check",
    "power_of_two_check",
    "perfect_square_check",
    "division_by_zero",
    # Level 10: Chain arithmetic
    "chain_arithmetic",
    "chain_percentage",
    "chain_remainder",
    # Level 11: Multi-variable state tracking
    "warehouse_inventory",
    "bank_transactions",
    "production_pipeline",
    # Level 12: Constraint satisfaction
    "number_constraints",
    "scheduling_constraints",
    "allocation_constraints",
    # Level 13: Compound word problems
    "multi_step_word",
    "distractor_word",
    "conditional_word",
    # Level 14: Large exact arithmetic
    "large_multiplication",
    "large_factorial_mod",
    "large_sum_series",
    "digit_manipulation",
    # Level 15: Combined difficulty
    "combined_chain_constraint",
    "combined_state_large",
    "combined_conditional_chain",
]
