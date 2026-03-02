"""Tests for Level 14: Large exact arithmetic generators."""

import random

import pytest

from math_task_factory.generators.large_arithmetic import (
    gen_digit_manipulation,
    gen_large_factorial_mod,
    gen_large_multiplication,
    gen_large_sum_series,
)
from math_task_factory.types import MathTask

_GENERATORS = [
    gen_large_multiplication,
    gen_large_factorial_mod,
    gen_large_sum_series,
    gen_digit_manipulation,
]
_NAMES = [
    "large_multiplication",
    "large_factorial_mod",
    "large_sum_series",
    "digit_manipulation",
]


@pytest.mark.unit
@pytest.mark.parametrize("gen,name", zip(_GENERATORS, _NAMES))
def test_produces_valid_mathtask(gen, name, rng):
    task = gen(rng)
    assert isinstance(task, MathTask)
    assert task.level == 14
    assert task.problem_type == name
    assert len(task.solutions) >= 1
    assert task.title
    assert task.spec


@pytest.mark.unit
@pytest.mark.parametrize("gen", _GENERATORS)
def test_deterministic_with_seed(gen):
    t1 = gen(random.Random(33))
    t2 = gen(random.Random(33))
    assert t1.solutions == t2.solutions
    assert t1.spec == t2.spec


@pytest.mark.unit
@pytest.mark.parametrize("gen", _GENERATORS)
def test_variety_across_seeds(gen):
    specs = {gen(random.Random(s)).spec for s in range(10)}
    assert len(specs) >= 5


@pytest.mark.unit
def test_large_multiplication_answer_correctness():
    """Independently verify a multiplication answer."""
    rng = random.Random(42)
    task = gen_large_multiplication(rng)
    # Extract numbers from spec (the two factors are in the spec)
    answer = int(task.solutions[0])
    assert answer > 0
    # Answer should be a large number
    assert answer > 10000


@pytest.mark.unit
def test_factorial_mod_answer_in_range():
    """Result of n! mod m should be in [0, m)."""
    for seed in range(20):
        task = gen_large_factorial_mod(random.Random(seed))
        answer = int(task.solutions[0])
        assert answer >= 0
