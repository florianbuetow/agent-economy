"""Math task factory for generating verifiable mathematical problem tasks."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable, Literal


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
]


class MathTaskFactory:
    """
    Factory for generating mathematical problem tasks with verifiable solutions.

    Uses Python's Mersenne Twister PRNG (random.Random), so an unbounded number
    of distinct problems can be generated. Pass seed for reproducibility.

    Use create() to generate a single task, or create_batch() for multiple tasks.
    """

    def __init__(self, *, seed: int | None = None) -> None:
        """Initialize the factory with an optional random seed for reproducibility."""
        self._rng = random.Random(seed)

    def create(
        self,
        level: int,
        problem_type: ProblemType | None = None,
    ) -> MathTask:
        """
        Create a single math task at the given level.

        If problem_type is None, a random problem type for that level is chosen.
        """
        types_for_level = _PROBLEM_TYPES_BY_LEVEL.get(level)
        if types_for_level is None:
            msg = f"Level {level} not supported. Valid levels: 1-{max(_PROBLEM_TYPES_BY_LEVEL)}"
            raise ValueError(msg)

        if problem_type is None:
            problem_type = self._rng.choice(types_for_level)

        if problem_type not in types_for_level:
            msg = f"Problem type {problem_type!r} not valid for level {level}"
            raise ValueError(msg)

        return _GENERATORS[problem_type](self._rng)

    def create_batch(
        self,
        level: int | None = None,
        levels: tuple[int, ...] | None = None,
        count: int = 1,
        problem_type: ProblemType | None = None,
    ) -> list[MathTask]:
        """
        Create multiple math tasks.

        Either level (single level) or levels (multiple levels) must be provided.
        If levels is used, count tasks are generated per level.
        """
        if level is not None and levels is not None:
            msg = "Provide either level or levels, not both"
            raise ValueError(msg)
        if level is None and levels is None:
            msg = "Provide either level or levels"
            raise ValueError(msg)

        if level is not None:
            target_levels = [level]
        else:
            target_levels = list(levels)  # type: ignore[assignment]

        tasks: list[MathTask] = []
        for lev in target_levels:
            for _ in range(count):
                tasks.append(self.create(level=lev, problem_type=problem_type))
        return tasks


# ---------------------------------------------------------------------------
# Level → problem type mapping
# ---------------------------------------------------------------------------

_PROBLEM_TYPES_BY_LEVEL: dict[int, tuple[ProblemType, ...]] = {
    1: (
        "addition_positive",
        "addition_positive_text",
        "addition_signed",
        "addition_small",
        "addition_small_text",
        "addition_large",
        "addition_large_text",
    ),
    2: (
        "subtraction",
        "multiplication",
        "multiplication_small",
        "multiplication_large",
        "division",
        "division_float",
        "addition_float",
        "multiplication_float",
        "multiplication_text",
    ),
    3: ("order_of_operations", "modulo", "modulo_text", "modulo_large"),
    4: ("single_variable_add_sub", "single_variable_add_sub_text", "single_variable_mul_div"),
    5: ("single_variable_combined",),
    6: ("system_solvable", "system_unsolvable", "system_infinite"),
    7: ("exponential", "exponential_equation"),
    8: ("square_root", "logarithm"),
    9: (
        "prime_check",
        "cube_check",
        "power_of_two_check",
        "perfect_square_check",
        "division_by_zero",
    ),
}


# ---------------------------------------------------------------------------
# Text helpers (number words for natural-language prompts)
# ---------------------------------------------------------------------------

_NUMBER_WORDS: dict[int, str] = {
    0: "zero",
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
    11: "eleven",
    12: "twelve",
    13: "thirteen",
    14: "fourteen",
    15: "fifteen",
    16: "sixteen",
    17: "seventeen",
    18: "eighteen",
    19: "nineteen",
    20: "twenty",
}


def _to_text(n: int | float) -> str:
    """Convert a number to word form for small integers, else string."""
    if isinstance(n, float):
        return str(n)
    if n in _NUMBER_WORDS:
        return _NUMBER_WORDS[n]
    return str(n)


# ---------------------------------------------------------------------------
# Problem generators
# ---------------------------------------------------------------------------


def _gen_addition_positive(rng: random.Random) -> MathTask:
    n = rng.randint(2, 6)
    terms = [rng.randint(1, 100) for _ in range(n)]
    total = sum(terms)
    terms_str = ", ".join(str(t) for t in terms)
    return MathTask(
        title=f"Add {n} positive integers",
        spec=f"""TASK: Calculate the sum of {terms_str}.

OUTPUT FORMAT: A single integer.

VERIFICATION: Sum the numbers and compare.""",
        solutions=[str(total)],
        level=1,
        problem_type="addition_positive",
    )


def _gen_addition_positive_text(rng: random.Random) -> MathTask:
    n = rng.randint(2, 6)
    terms = [rng.randint(1, 100) for _ in range(n)]
    total = sum(terms)
    terms_text = ", ".join(str(t) for t in terms)
    return MathTask(
        title="Add positive integers (word problem)",
        spec=f"""TASK: What is the sum of the following numbers: {terms_text}?

OUTPUT FORMAT: A single integer.

VERIFICATION: Add the numbers and compare.""",
        solutions=[str(total)],
        level=1,
        problem_type="addition_positive_text",
    )


def _gen_addition_signed(rng: random.Random) -> MathTask:
    n = rng.randint(2, 5)
    terms = [rng.randint(-50, 50) for _ in range(n) if rng.random() > 0.1]
    if len(terms) < 2:
        terms = [rng.randint(-20, 20), rng.randint(-20, 20)]
    total = sum(terms)
    parts = []
    for t in terms:
        if t >= 0:
            parts.append(str(t))
        else:
            parts.append(f"({t})")
    terms_str = " + ".join(parts)
    return MathTask(
        title="Add integers including negatives",
        spec=f"""TASK: Calculate {terms_str}.

OUTPUT FORMAT: A single integer.

VERIFICATION: Add the numbers and compare.""",
        solutions=[str(total)],
        level=1,
        problem_type="addition_signed",
    )


def _gen_addition_small(rng: random.Random) -> MathTask:
    n = rng.randint(2, 5)
    terms = [rng.randint(1, 10) for _ in range(n)]
    total = sum(terms)
    terms_str = ", ".join(str(t) for t in terms)
    return MathTask(
        title="Add small positive integers (1-10)",
        spec=f"""TASK: Calculate the sum of {terms_str}.

OUTPUT FORMAT: A single integer.

VERIFICATION: Sum the numbers and compare.""",
        solutions=[str(total)],
        level=1,
        problem_type="addition_small",
    )


def _gen_addition_small_text(rng: random.Random) -> MathTask:
    n = rng.randint(2, 5)
    terms = [rng.randint(1, 10) for _ in range(n)]
    total = sum(terms)
    terms_text = ", ".join(_to_text(t) for t in terms)
    return MathTask(
        title="Add small numbers (word problem)",
        spec=f"""TASK: Add these numbers together: {terms_text}. What is the total?

OUTPUT FORMAT: A single integer.

VERIFICATION: Sum the numbers and compare.""",
        solutions=[str(total)],
        level=1,
        problem_type="addition_small_text",
    )


def _gen_addition_large(rng: random.Random) -> MathTask:
    n = rng.randint(2, 4)
    terms = [rng.randint(1000, 99999) for _ in range(n)]
    total = sum(terms)
    terms_str = ", ".join(str(t) for t in terms)
    return MathTask(
        title="Add large positive integers",
        spec=f"""TASK: Calculate the sum of {terms_str}.

OUTPUT FORMAT: A single integer.

VERIFICATION: Sum the numbers and compare.""",
        solutions=[str(total)],
        level=1,
        problem_type="addition_large",
    )


def _gen_addition_large_text(rng: random.Random) -> MathTask:
    n = rng.randint(2, 4)
    terms = [rng.randint(1000, 99999) for _ in range(n)]
    total = sum(terms)
    terms_text = ", ".join(str(t) for t in terms)
    return MathTask(
        title="Add large numbers (word problem)",
        spec=f"""TASK: Find the total when you add these numbers: {terms_text}.

OUTPUT FORMAT: A single integer.

VERIFICATION: Sum the numbers and compare.""",
        solutions=[str(total)],
        level=1,
        problem_type="addition_large_text",
    )


def _gen_addition_float(rng: random.Random) -> MathTask:
    n = rng.randint(2, 4)
    terms = [round(rng.uniform(0.1, 9.9), 1) for _ in range(n)]
    total = round(sum(terms), 1)
    terms_str = " + ".join(str(t) for t in terms)
    solutions = [str(total)]
    if total == int(total):
        solutions.append(str(int(total)))
    return MathTask(
        title="Add decimal numbers",
        spec=f"""TASK: Calculate {terms_str}.

OUTPUT FORMAT: A decimal number (e.g. 12.5 or 12.50). One decimal place is sufficient.

VERIFICATION: Sum the numbers and compare.""",
        solutions=solutions,
        level=2,
        problem_type="addition_float",
    )


def _gen_subtraction(rng: random.Random) -> MathTask:
    a = rng.randint(50, 200)
    b = rng.randint(10, 80)
    c = rng.randint(1, 30)
    result = a - b - c
    return MathTask(
        title="Subtract three numbers",
        spec=f"""TASK: Calculate {a} - {b} - {c}.

OUTPUT FORMAT: A single integer.

VERIFICATION: Perform the subtraction and compare.""",
        solutions=[str(result)],
        level=2,
        problem_type="subtraction",
    )


def _gen_multiplication(rng: random.Random) -> MathTask:
    a = rng.randint(-12, 12)
    if a == 0:
        a = rng.randint(1, 12)
    b = rng.randint(1, 12)
    result = a * b
    op = "×" if a >= 0 and b >= 0 else "*"
    return MathTask(
        title="Multiply two integers",
        spec=f"""TASK: Calculate {a} {op} {b}.

OUTPUT FORMAT: A single integer.

VERIFICATION: Multiply and compare.""",
        solutions=[str(result)],
        level=2,
        problem_type="multiplication",
    )


def _gen_multiplication_small(rng: random.Random) -> MathTask:
    a = rng.randint(1, 10)
    b = rng.randint(1, 10)
    result = a * b
    return MathTask(
        title="Multiply two small integers (1-10)",
        spec=f"""TASK: Calculate {a} × {b}.

OUTPUT FORMAT: A single integer.

VERIFICATION: Multiply and compare.""",
        solutions=[str(result)],
        level=2,
        problem_type="multiplication_small",
    )


def _gen_multiplication_large(rng: random.Random) -> MathTask:
    a = rng.randint(100, 9999)
    b = rng.randint(10, 99)
    result = a * b
    return MathTask(
        title="Multiply two large integers",
        spec=f"""TASK: Calculate {a} × {b}.

OUTPUT FORMAT: A single integer.

VERIFICATION: Multiply and compare.""",
        solutions=[str(result)],
        level=2,
        problem_type="multiplication_large",
    )


def _gen_multiplication_text(rng: random.Random) -> MathTask:
    a = rng.randint(1, 12)
    b = rng.randint(1, 12)
    result = a * b
    a_text = _to_text(a)
    b_text = _to_text(b)
    return MathTask(
        title="Multiply two numbers (word problem)",
        spec=f"""TASK: What do you get when you multiply {a_text} by {b_text}?

OUTPUT FORMAT: A single integer.

VERIFICATION: Multiply the numbers and compare.""",
        solutions=[str(result)],
        level=2,
        problem_type="multiplication_text",
    )


def _gen_multiplication_float(rng: random.Random) -> MathTask:
    a = round(rng.uniform(1.1, 9.9), 1)
    b = round(rng.uniform(1.1, 9.9), 1)
    result = round(a * b, 2)
    solutions = [str(result)]
    if result == int(result):
        solutions.append(str(int(result)))
    return MathTask(
        title="Multiply two decimal numbers",
        spec=f"""TASK: Calculate {a} × {b}.

OUTPUT FORMAT: A decimal number. Up to two decimal places.

VERIFICATION: Multiply and compare.""",
        solutions=solutions,
        level=2,
        problem_type="multiplication_float",
    )


def _gen_division(rng: random.Random) -> MathTask:
    divisor = rng.choice([2, 3, 4, 5, 6, 8, 10, 12])
    quotient = rng.randint(5, 50)
    dividend = divisor * quotient
    result = dividend // divisor
    return MathTask(
        title="Divide two integers (exact result)",
        spec=f"""TASK: Calculate {dividend} ÷ {divisor}.

OUTPUT FORMAT: A single integer.

VERIFICATION: The result must be exact (no remainder).""",
        solutions=[str(result)],
        level=2,
        problem_type="division",
    )


def _gen_division_float(rng: random.Random) -> MathTask:
    divisor = rng.choice([2, 4, 5, 8, 10])
    quotient = round(rng.uniform(1.1, 20.0), 1)
    dividend = round(divisor * quotient, 1)
    result = round(dividend / divisor, 1)
    solutions = [str(result)]
    if result == int(result):
        solutions.append(str(int(result)))
    return MathTask(
        title="Divide two numbers (decimal result)",
        spec=f"""TASK: Calculate {dividend} ÷ {divisor}. Give the exact decimal result.

OUTPUT FORMAT: A decimal number (e.g. 3.5). One decimal place is sufficient.

VERIFICATION: dividend ÷ divisor equals the result.""",
        solutions=solutions,
        level=2,
        problem_type="division_float",
    )


def _gen_order_of_operations(rng: random.Random) -> MathTask:
    # a + b * c or (a - b) / c
    if rng.random() < 0.5:
        b, c = rng.randint(2, 9), rng.randint(2, 9)
        a = rng.randint(1, 20)
        result = a + b * c
        expr = f"{a} + {b} × {c}"
    else:
        a, b = rng.randint(10, 30), rng.randint(2, 8)
        c = rng.randint(2, 6)
        result = (a - b) // c
        expr = f"({a} - {b}) ÷ {c}"
    return MathTask(
        title="Evaluate expression with order of operations",
        spec=f"""TASK: Calculate {expr}. Apply PEMDAS (parentheses, then multiplication/division, then addition/subtraction).

OUTPUT FORMAT: A single integer.

VERIFICATION: Evaluate following order of operations.""",
        solutions=[str(result)],
        level=3,
        problem_type="order_of_operations",
    )


def _gen_modulo(rng: random.Random) -> MathTask:
    divisor = rng.randint(2, 20)
    quotient = rng.randint(1, 100)
    remainder = rng.randint(0, divisor - 1)
    dividend = quotient * divisor + remainder
    result = dividend % divisor
    return MathTask(
        title="Calculate remainder (modulo)",
        spec=f"""TASK: What is the remainder when {dividend} is divided by {divisor}? Equivalently: calculate {dividend} mod {divisor} (or {dividend} % {divisor}).

OUTPUT FORMAT: A single non-negative integer (the remainder).

VERIFICATION: {dividend} = q × {divisor} + result for some integer q, with 0 ≤ result < {divisor}.""",
        solutions=[str(result)],
        level=3,
        problem_type="modulo",
    )


def _gen_modulo_text(rng: random.Random) -> MathTask:
    divisor = rng.randint(2, 20)
    quotient = rng.randint(1, 100)
    remainder = rng.randint(0, divisor - 1)
    dividend = quotient * divisor + remainder
    result = dividend % divisor
    div_text = _to_text(divisor) if divisor <= 20 else str(divisor)
    return MathTask(
        title="Find the remainder (word problem)",
        spec=f"""TASK: When {dividend} is divided by {divisor}, what is the remainder? In other words, if you divide {dividend} by {divisor}, how much is left over?

OUTPUT FORMAT: A single non-negative integer (the remainder).

VERIFICATION: The remainder must be less than {divisor} and satisfy: {dividend} equals some multiple of {divisor} plus the remainder.""",
        solutions=[str(result)],
        level=3,
        problem_type="modulo_text",
    )


def _gen_modulo_large(rng: random.Random) -> MathTask:
    divisor = rng.randint(10, 100)
    quotient = rng.randint(100, 10000)
    remainder = rng.randint(0, divisor - 1)
    dividend = quotient * divisor + remainder
    result = dividend % divisor
    return MathTask(
        title="Calculate remainder (modulo) with large numbers",
        spec=f"""TASK: What is the remainder when {dividend} is divided by {divisor}? Equivalently: calculate {dividend} mod {divisor}.

OUTPUT FORMAT: A single non-negative integer (the remainder).

VERIFICATION: {dividend} = q × {divisor} + result for some integer q, with 0 ≤ result < {divisor}.""",
        solutions=[str(result)],
        level=3,
        problem_type="modulo_large",
    )


def _gen_single_variable_add_sub(rng: random.Random) -> MathTask:
    x_val = rng.randint(1, 50)
    if rng.random() < 0.5:
        b = rng.randint(1, 20)
        c = x_val + b
        expr = f"x + {b} = {c}"
    else:
        b = rng.randint(1, 20)
        c = x_val - b
        expr = f"x - {b} = {c}"
    return MathTask(
        title="Solve for x (addition or subtraction)",
        spec=f"""TASK: Find x such that {expr}.

OUTPUT FORMAT: x=N where N is the integer solution, or just N.

VERIFICATION: Substitute x into the equation.""",
        solutions=[f"x={x_val}", str(x_val)],
        level=4,
        problem_type="single_variable_add_sub",
    )


def _gen_single_variable_add_sub_text(rng: random.Random) -> MathTask:
    x_val = rng.randint(1, 50)
    if rng.random() < 0.5:
        b = rng.randint(1, 20)
        c = x_val + b
        b_text = _to_text(b) if b <= 20 else str(b)
        c_text = _to_text(c) if c <= 20 else str(c)
        spec = f"""TASK: A number plus {b_text} equals {c_text}. What is the number?"""
    else:
        b = rng.randint(1, 20)
        c = x_val - b
        b_text = _to_text(b) if b <= 20 else str(b)
        c_text = _to_text(c) if c <= 20 else str(c)
        spec = f"""TASK: A number minus {b_text} equals {c_text}. What is the number?"""
    return MathTask(
        title="Find the unknown number (word problem)",
        spec=f"""{spec}

OUTPUT FORMAT: The number as an integer, or x=N.

VERIFICATION: Substitute the answer into the sentence and check.""",
        solutions=[f"x={x_val}", str(x_val)],
        level=4,
        problem_type="single_variable_add_sub_text",
    )


def _gen_single_variable_mul_div(rng: random.Random) -> MathTask:
    if rng.random() < 0.5:
        x_val = rng.randint(2, 20)
        a = rng.randint(2, 10)
        c = a * x_val
        expr = f"{a}x = {c}"
    else:
        x_val = rng.randint(2, 20)
        a = rng.randint(2, 10)
        quotient = x_val
        dividend = quotient * a
        expr = f"x ÷ {a} = {quotient}"
        x_val = dividend
    return MathTask(
        title="Solve for x (multiplication or division)",
        spec=f"""TASK: Find x such that {expr}.

OUTPUT FORMAT: x=N where N is the integer solution, or just N.

VERIFICATION: Substitute x into the equation.""",
        solutions=[f"x={x_val}", str(x_val)],
        level=4,
        problem_type="single_variable_mul_div",
    )


def _gen_single_variable_combined(rng: random.Random) -> MathTask:
    x_val = rng.randint(2, 15)
    a = rng.randint(2, 8)
    b = rng.randint(1, 20)
    c = a * x_val + b
    expr = f"{a}x + {b} = {c}"
    return MathTask(
        title="Solve for x in a linear equation",
        spec=f"""TASK: Find x such that {expr}.

OUTPUT FORMAT: x=N where N is the integer solution, or just N.

VERIFICATION: Substitute x into the equation.""",
        solutions=[f"x={x_val}", str(x_val)],
        level=5,
        problem_type="single_variable_combined",
    )


def _gen_system_solvable(rng: random.Random) -> MathTask:
    x_val = rng.randint(1, 10)
    y_val = rng.randint(1, 10)
    a1, b1 = rng.randint(1, 5), rng.randint(1, 5)
    c1 = a1 * x_val + b1 * y_val
    a2, b2 = rng.randint(1, 5), rng.randint(1, 5)
    while a1 * b2 == a2 * b1:
        a2, b2 = rng.randint(1, 5), rng.randint(1, 5)
    c2 = a2 * x_val + b2 * y_val
    return MathTask(
        title="Solve a 2×2 linear system",
        spec=f"""TASK: Solve the system:
  {a1}x + {b1}y = {c1}
  {a2}x + {b2}y = {c2}

OUTPUT FORMAT: x=A, y=B or equivalent (e.g. "A, B" or "(A, B)").

VERIFICATION: Substitute into both equations.""",
        solutions=[f"x={x_val}, y={y_val}", f"{x_val}, {y_val}", f"({x_val}, {y_val})"],
        level=6,
        problem_type="system_solvable",
    )


def _gen_system_unsolvable(rng: random.Random) -> MathTask:
    # Parallel lines: 2x + 4y = 6, 2x + 4y = 10 (or x + 2y = 3, x + 2y = 5)
    a, b = rng.randint(1, 4), rng.randint(1, 4)
    c1 = rng.randint(2, 8)
    c2 = c1 + rng.randint(2, 5)
    return MathTask(
        title="Determine if a 2×2 linear system has a solution",
        spec=f"""TASK: Solve the system:
  {a}x + {b}y = {c1}
  {a}x + {b}y = {c2}

OUTPUT FORMAT: x=A, y=B if solvable, or NO_SOLUTION if not.

VERIFICATION: The lines are parallel; there is no solution.""",
        solutions=["NO_SOLUTION", "no solution", "none"],
        level=6,
        problem_type="system_unsolvable",
    )


def _gen_system_infinite(rng: random.Random) -> MathTask:
    # Same line: ax + by = c and 2ax + 2by = 2c
    a, b = rng.randint(1, 4), rng.randint(1, 4)
    c = rng.randint(2, 12)
    ex1 = f"{a}x + {b}y = {c}"
    ex2 = f"{2*a}x + {2*b}y = {2*c}"
    return MathTask(
        title="Solve a 2×2 linear system (infinite solutions)",
        spec=f"""TASK: Solve the system:
  {ex1}
  {ex2}

OUTPUT FORMAT: x=A, y=B if unique; INFINITELY_MANY if infinitely many solutions.

VERIFICATION: The equations represent the same line.""",
        solutions=["INFINITELY_MANY"],
        level=6,
        problem_type="system_infinite",
        solution_note="Any (x, y) satisfying the equation. Examples vary.",
    )


def _gen_exponential(rng: random.Random) -> MathTask:
    base = rng.choice([2, 3, 4, 5])
    exp = rng.randint(2, 10)
    result = base**exp
    return MathTask(
        title="Calculate an exponential",
        spec=f"""TASK: Calculate {base}^{exp}.

OUTPUT FORMAT: A single integer.

VERIFICATION: Compute the power and compare.""",
        solutions=[str(result)],
        level=7,
        problem_type="exponential",
    )


def _gen_exponential_equation(rng: random.Random) -> MathTask:
    base = rng.choice([2, 3])
    exp = rng.randint(2, 8)
    k = base**exp
    return MathTask(
        title="Solve an exponential equation",
        spec=f"""TASK: Find x such that {base}^x = {k}.

OUTPUT FORMAT: x=N where N is the integer solution.

VERIFICATION: {base}^N = {k}.""",
        solutions=[f"x={exp}", str(exp)],
        level=7,
        problem_type="exponential_equation",
    )


def _gen_square_root(rng: random.Random) -> MathTask:
    n = rng.choice([4, 9, 16, 25, 36, 49, 64, 81, 100, 121, 144, 169, 196, 225])
    result = int(n**0.5)
    return MathTask(
        title="Calculate a square root",
        spec=f"""TASK: Calculate √{n}.

OUTPUT FORMAT: A single non-negative integer.

VERIFICATION: The result squared equals {n}.""",
        solutions=[str(result)],
        level=8,
        problem_type="square_root",
    )


def _gen_logarithm(rng: random.Random) -> MathTask:
    base = rng.choice([2, 3, 10])
    exp = rng.randint(1, 6)
    arg = base**exp
    return MathTask(
        title="Calculate a logarithm",
        spec=f"""TASK: Calculate log_{base}({arg}).

OUTPUT FORMAT: A single integer.

VERIFICATION: {base}^result = {arg}.""",
        solutions=[str(exp)],
        level=8,
        problem_type="logarithm",
    )


def _gen_prime_check(rng: random.Random) -> MathTask:
    if rng.random() < 0.5:
        primes = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59, 61, 67, 71, 73, 79, 83, 89, 97]
        n = rng.choice(primes)
        solutions = ["yes", "true", "prime"]
    else:
        composites = [4, 6, 8, 9, 10, 12, 14, 15, 16, 18, 20, 21, 22, 24, 25, 26, 27, 28, 30]
        n = rng.choice(composites)
        solutions = ["no", "false", "composite", "not prime"]
    return MathTask(
        title="Check if a number is prime",
        spec=f"""TASK: Is {n} a prime number? Answer yes or no.

OUTPUT FORMAT: yes/no or true/false.

VERIFICATION: A prime number is greater than 1 and has no positive divisors other than 1 and itself.""",
        solutions=solutions,
        level=9,
        problem_type="prime_check",
    )


def _gen_cube_check(rng: random.Random) -> MathTask:
    if rng.random() < 0.5:
        cubes = [1, 8, 27, 64, 125, 216, 343, 512, 729, 1000]
        n = rng.choice(cubes)
        solutions = ["yes", "true", "cube"]
    else:
        non_cubes = [2, 3, 4, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]
        n = rng.choice(non_cubes)
        solutions = ["no", "false", "not a cube"]
    return MathTask(
        title="Check if a number is a perfect cube",
        spec=f"""TASK: Is {n} a perfect cube? In other words, does there exist an integer k such that k³ = {n}?

OUTPUT FORMAT: yes/no or true/false.

VERIFICATION: A perfect cube is a number of the form k³ for some integer k.""",
        solutions=solutions,
        level=9,
        problem_type="cube_check",
    )


def _gen_power_of_two_check(rng: random.Random) -> MathTask:
    if rng.random() < 0.5:
        powers = [1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024]
        n = rng.choice(powers)
        solutions = ["yes", "true", "power of two"]
    else:
        non_powers = [3, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15, 17, 18, 19, 20]
        n = rng.choice(non_powers)
        solutions = ["no", "false", "not a power of two"]
    return MathTask(
        title="Check if a number is a power of two",
        spec=f"""TASK: Is {n} of the form 2^k for some non-negative integer k? In other words, is {n} a power of two?

OUTPUT FORMAT: yes/no or true/false.

VERIFICATION: Powers of two are 1, 2, 4, 8, 16, 32, ...""",
        solutions=solutions,
        level=9,
        problem_type="power_of_two_check",
    )


def _gen_perfect_square_check(rng: random.Random) -> MathTask:
    if rng.random() < 0.5:
        squares = [1, 4, 9, 16, 25, 36, 49, 64, 81, 100, 121, 144, 169, 196, 225]
        n = rng.choice(squares)
        solutions = ["yes", "true", "perfect square"]
    else:
        non_squares = [2, 3, 5, 6, 7, 8, 10, 11, 12, 13, 14, 15, 17, 18, 19, 20]
        n = rng.choice(non_squares)
        solutions = ["no", "false", "not a perfect square"]
    return MathTask(
        title="Check if a number is a perfect square",
        spec=f"""TASK: Is {n} a perfect square? In other words, does there exist an integer k such that k² = {n}?

OUTPUT FORMAT: yes/no or true/false.

VERIFICATION: A perfect square is a number of the form k² for some integer k.""",
        solutions=solutions,
        level=9,
        problem_type="perfect_square_check",
    )


def _gen_division_by_zero(rng: random.Random) -> MathTask:
    dividend = rng.randint(1, 100)
    return MathTask(
        title="Division by zero",
        spec=f"""TASK: What is {dividend} ÷ 0? In other words, {dividend} divided by zero.

OUTPUT FORMAT: The result, or indicate that the operation is undefined/not defined.

VERIFICATION: Division by zero is undefined in standard arithmetic. There is no number that equals {dividend} ÷ 0.""",
        solutions=[
            "undefined",
            "not defined",
            "division by zero",
            "error",
            "NaN",
            "undefined (division by zero)",
        ],
        level=9,
        problem_type="division_by_zero",
    )


_GENERATORS: dict[ProblemType, Callable[[random.Random], MathTask]] = {
    "addition_positive": _gen_addition_positive,
    "addition_positive_text": _gen_addition_positive_text,
    "addition_signed": _gen_addition_signed,
    "addition_small": _gen_addition_small,
    "addition_small_text": _gen_addition_small_text,
    "addition_large": _gen_addition_large,
    "addition_large_text": _gen_addition_large_text,
    "addition_float": _gen_addition_float,
    "subtraction": _gen_subtraction,
    "multiplication": _gen_multiplication,
    "multiplication_text": _gen_multiplication_text,
    "multiplication_small": _gen_multiplication_small,
    "multiplication_large": _gen_multiplication_large,
    "multiplication_float": _gen_multiplication_float,
    "division": _gen_division,
    "division_float": _gen_division_float,
    "order_of_operations": _gen_order_of_operations,
    "modulo": _gen_modulo,
    "modulo_text": _gen_modulo_text,
    "modulo_large": _gen_modulo_large,
    "single_variable_add_sub": _gen_single_variable_add_sub,
    "single_variable_add_sub_text": _gen_single_variable_add_sub_text,
    "single_variable_mul_div": _gen_single_variable_mul_div,
    "single_variable_combined": _gen_single_variable_combined,
    "system_solvable": _gen_system_solvable,
    "system_unsolvable": _gen_system_unsolvable,
    "system_infinite": _gen_system_infinite,
    "exponential": _gen_exponential,
    "exponential_equation": _gen_exponential_equation,
    "square_root": _gen_square_root,
    "logarithm": _gen_logarithm,
    "prime_check": _gen_prime_check,
    "cube_check": _gen_cube_check,
    "power_of_two_check": _gen_power_of_two_check,
    "perfect_square_check": _gen_perfect_square_check,
    "division_by_zero": _gen_division_by_zero,
}
