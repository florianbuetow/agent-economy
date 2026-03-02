"""Level 14: Large exact arithmetic generators."""

from __future__ import annotations

import math
import random

from math_task_factory.types import MathTask


def gen_large_multiplication(rng: random.Random) -> MathTask:
    """Two 5-7 digit numbers multiplied."""
    digits_a = rng.randint(5, 7)
    digits_b = rng.randint(5, 7)
    a = rng.randint(10 ** (digits_a - 1), 10**digits_a - 1)
    b = rng.randint(10 ** (digits_b - 1), 10**digits_b - 1)
    result = a * b

    return MathTask(
        title="Large number multiplication",
        spec=f"""TASK: Calculate the exact product of {a} and {b}.

{a} * {b} = ?

OUTPUT FORMAT: A single integer. Must be exact — no approximations.

VERIFICATION: Multiply the two numbers and verify the result.""",
        solutions=[str(result)],
        level=14,
        problem_type="large_multiplication",
    )


def gen_large_factorial_mod(rng: random.Random) -> MathTask:
    """Compute n! mod m for n=10-20 and specific m."""
    n = rng.randint(10, 20)
    m = rng.choice([97, 101, 127, 131, 137, 139, 149, 151, 157, 163])
    factorial = math.factorial(n)
    result = factorial % m

    return MathTask(
        title="Factorial modulo",
        spec=f"""TASK: Calculate {n}! mod {m}.

That is: compute {n} factorial ({n}! = 1 * 2 * 3 * ... * {n}), then find
the remainder when divided by {m}.

OUTPUT FORMAT: A single non-negative integer (the remainder).

VERIFICATION: {n}! = {factorial}, and {factorial} mod {m} = {result}.""",
        solutions=[str(result)],
        level=14,
        problem_type="large_factorial_mod",
    )


def gen_large_sum_series(rng: random.Random) -> MathTask:
    """Sum of arithmetic or geometric series with large terms."""
    if rng.random() < 0.5:
        # Arithmetic series: a, a+d, a+2d, ..., a+(n-1)d
        a = rng.randint(100, 999)
        d = rng.randint(10, 50)
        n = rng.randint(20, 50)
        last = a + (n - 1) * d
        total = n * (a + last) // 2

        return MathTask(
            title="Arithmetic series sum",
            spec=f"""TASK: Calculate the sum of the arithmetic series:
  First term: {a}
  Common difference: {d}
  Number of terms: {n}

The series is: {a}, {a + d}, {a + 2*d}, ..., {last}

What is the sum of all {n} terms?

OUTPUT FORMAT: A single integer.

VERIFICATION: Sum = n * (first + last) / 2 = {n} * ({a} + {last}) / 2 = {total}.""",
            solutions=[str(total)],
            level=14,
            problem_type="large_sum_series",
        )
    else:
        # Sum of squares: 1^2 + 2^2 + ... + n^2
        n = rng.randint(30, 80)
        total = n * (n + 1) * (2 * n + 1) // 6

        return MathTask(
            title="Sum of squares series",
            spec=f"""TASK: Calculate the sum of the first {n} perfect squares:
  1^2 + 2^2 + 3^2 + ... + {n}^2

What is the total?

OUTPUT FORMAT: A single integer.

VERIFICATION: Using the formula n*(n+1)*(2n+1)/6 = {n}*{n+1}*{2*n+1}/6 = {total}.""",
            solutions=[str(total)],
            level=14,
            problem_type="large_sum_series",
        )


def gen_digit_manipulation(rng: random.Random) -> MathTask:
    """Digit-based operations on large numbers."""
    variant = rng.choice(["digit_sum_product", "reverse_subtract", "digit_rearrange"])

    if variant == "digit_sum_product":
        a = rng.randint(1000, 9999)
        b = rng.randint(1000, 9999)
        product = a * b
        digit_sum = sum(int(d) for d in str(product))
        return MathTask(
            title="Digit sum of a product",
            spec=f"""TASK: Calculate {a} * {b}, then find the sum of the digits of the result.

Step 1: Compute {a} * {b}.
Step 2: Sum the individual digits of the product.

OUTPUT FORMAT: A single integer (the digit sum).

VERIFICATION: {a} * {b} = {product}, digit sum = {digit_sum}.""",
            solutions=[str(digit_sum)],
            level=14,
            problem_type="digit_manipulation",
        )
    elif variant == "reverse_subtract":
        num = rng.randint(100000, 999999)
        reversed_num = int(str(num)[::-1])
        result = abs(num - reversed_num)
        return MathTask(
            title="Reverse and subtract",
            spec=f"""TASK: Take the number {num}. Reverse its digits, then subtract the
smaller number from the larger.

Step 1: Reverse {num} to get its digit-reversed form.
Step 2: Subtract the smaller of the two from the larger.

OUTPUT FORMAT: A single non-negative integer.

VERIFICATION: Reversed = {reversed_num}, |{num} - {reversed_num}| = {result}.""",
            solutions=[str(result)],
            level=14,
            problem_type="digit_manipulation",
        )
    else:
        num = rng.randint(10000, 99999)
        digits = sorted(str(num))
        smallest = int("".join(digits))
        largest = int("".join(reversed(digits)))
        result = largest - smallest
        return MathTask(
            title="Digit rearrangement difference",
            spec=f"""TASK: Take the number {num}. Rearrange its digits to form:
  - The LARGEST possible number
  - The SMALLEST possible number (no leading zeros — if a digit is 0,
    it goes after the first non-zero digit)

Then subtract the smallest from the largest.

OUTPUT FORMAT: A single integer.

VERIFICATION: Digits of {num} sorted: largest = {largest}, smallest = {smallest},
difference = {result}.""",
            solutions=[str(result)],
            level=14,
            problem_type="digit_manipulation",
        )
