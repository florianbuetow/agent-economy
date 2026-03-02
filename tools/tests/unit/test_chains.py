"""Tests for Level 10: Chain arithmetic generators."""

import random

import pytest

from math_task_factory.generators.chains import (
    gen_chain_arithmetic,
    gen_chain_percentage,
    gen_chain_remainder,
)
from math_task_factory.types import MathTask

_GENERATORS = [gen_chain_arithmetic, gen_chain_percentage, gen_chain_remainder]
_NAMES = ["chain_arithmetic", "chain_percentage", "chain_remainder"]


@pytest.mark.unit
@pytest.mark.parametrize("gen,name", zip(_GENERATORS, _NAMES))
def test_produces_valid_mathtask(gen, name, rng):
    task = gen(rng)
    assert isinstance(task, MathTask)
    assert task.level == 10
    assert task.problem_type == name
    assert len(task.solutions) >= 1
    assert task.title
    assert task.spec


@pytest.mark.unit
@pytest.mark.parametrize("gen", _GENERATORS)
def test_deterministic_with_seed(gen):
    t1 = gen(random.Random(123))
    t2 = gen(random.Random(123))
    assert t1.solutions == t2.solutions
    assert t1.spec == t2.spec


@pytest.mark.unit
@pytest.mark.parametrize("gen", _GENERATORS)
def test_variety_across_seeds(gen):
    specs = {gen(random.Random(s)).spec for s in range(10)}
    assert len(specs) >= 5


@pytest.mark.unit
def test_chain_arithmetic_answer_is_integer(rng):
    task = gen_chain_arithmetic(rng)
    int(task.solutions[0])  # Must not raise


@pytest.mark.unit
def test_chain_percentage_answer_is_integer(rng):
    task = gen_chain_percentage(rng)
    int(task.solutions[0])


@pytest.mark.unit
def test_chain_remainder_answer_is_nonnegative(rng):
    task = gen_chain_remainder(rng)
    assert int(task.solutions[0]) >= 0
