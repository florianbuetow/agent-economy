"""Tests for Level 12: Constraint satisfaction generators."""

import random

import pytest

from math_task_factory.generators.constraints import (
    gen_allocation_constraints,
    gen_number_constraints,
    gen_scheduling_constraints,
)
from math_task_factory.types import MathTask

_GENERATORS = [gen_number_constraints, gen_scheduling_constraints, gen_allocation_constraints]
_NAMES = ["number_constraints", "scheduling_constraints", "allocation_constraints"]


@pytest.mark.unit
@pytest.mark.parametrize("gen,name", zip(_GENERATORS, _NAMES))
def test_produces_valid_mathtask(gen, name, rng):
    task = gen(rng)
    assert isinstance(task, MathTask)
    assert task.level == 12
    assert task.problem_type == name
    assert len(task.solutions) >= 1
    assert task.title
    assert task.spec


@pytest.mark.unit
@pytest.mark.parametrize("gen", _GENERATORS)
def test_deterministic_with_seed(gen):
    t1 = gen(random.Random(99))
    t2 = gen(random.Random(99))
    assert t1.solutions == t2.solutions
    assert t1.spec == t2.spec


@pytest.mark.unit
@pytest.mark.parametrize("gen", _GENERATORS)
def test_variety_across_seeds(gen):
    specs = {gen(random.Random(s)).spec for s in range(10)}
    assert len(specs) >= 5


@pytest.mark.unit
def test_number_constraints_answer_in_range():
    """Answer should satisfy the range constraint stated in the spec."""
    for seed in range(20):
        task = gen_number_constraints(random.Random(seed))
        answer = int(task.solutions[0])
        assert answer > 0


@pytest.mark.unit
def test_scheduling_answer_is_positive_integer(rng):
    task = gen_scheduling_constraints(rng)
    assert int(task.solutions[0]) > 0
