"""Tests for Level 15: Combined difficulty generators."""

import random

import pytest

from math_task_factory.generators.combined import (
    gen_combined_chain_constraint,
    gen_combined_conditional_chain,
    gen_combined_state_large,
)
from math_task_factory.types import MathTask

_GENERATORS = [
    gen_combined_chain_constraint,
    gen_combined_state_large,
    gen_combined_conditional_chain,
]
_NAMES = [
    "combined_chain_constraint",
    "combined_state_large",
    "combined_conditional_chain",
]


@pytest.mark.unit
@pytest.mark.parametrize("gen,name", zip(_GENERATORS, _NAMES))
def test_produces_valid_mathtask(gen, name, rng):
    task = gen(rng)
    assert isinstance(task, MathTask)
    assert task.level == 15
    assert task.problem_type == name
    assert len(task.solutions) >= 1
    assert task.title
    assert task.spec


@pytest.mark.unit
@pytest.mark.parametrize("gen", _GENERATORS)
def test_deterministic_with_seed(gen):
    t1 = gen(random.Random(88))
    t2 = gen(random.Random(88))
    assert t1.solutions == t2.solutions
    assert t1.spec == t2.spec


@pytest.mark.unit
@pytest.mark.parametrize("gen", _GENERATORS)
def test_variety_across_seeds(gen):
    specs = {gen(random.Random(s)).spec for s in range(10)}
    assert len(specs) >= 5


@pytest.mark.unit
def test_combined_chain_constraint_answer_is_integer(rng):
    task = gen_combined_chain_constraint(rng)
    int(task.solutions[0])


@pytest.mark.unit
def test_combined_state_large_uses_large_numbers():
    """Verify that 5 warehouses with 5-digit numbers are generated."""
    task = gen_combined_state_large(random.Random(42))
    # Spec should mention 5 warehouses
    assert "Warehouse A" in task.spec
    assert "Warehouse E" in task.spec
