"""Tests for Level 11: Multi-variable state tracking generators."""

import random

import pytest

from math_task_factory.generators.state_tracking import (
    gen_bank_transactions,
    gen_production_pipeline,
    gen_warehouse_inventory,
)
from math_task_factory.types import MathTask

_GENERATORS = [gen_warehouse_inventory, gen_bank_transactions, gen_production_pipeline]
_NAMES = ["warehouse_inventory", "bank_transactions", "production_pipeline"]


@pytest.mark.unit
@pytest.mark.parametrize("gen,name", zip(_GENERATORS, _NAMES))
def test_produces_valid_mathtask(gen, name, rng):
    task = gen(rng)
    assert isinstance(task, MathTask)
    assert task.level == 11
    assert task.problem_type == name
    assert len(task.solutions) >= 1
    assert task.title
    assert task.spec


@pytest.mark.unit
@pytest.mark.parametrize("gen", _GENERATORS)
def test_deterministic_with_seed(gen):
    t1 = gen(random.Random(77))
    t2 = gen(random.Random(77))
    assert t1.solutions == t2.solutions
    assert t1.spec == t2.spec


@pytest.mark.unit
@pytest.mark.parametrize("gen", _GENERATORS)
def test_variety_across_seeds(gen):
    specs = {gen(random.Random(s)).spec for s in range(10)}
    assert len(specs) >= 5


@pytest.mark.unit
def test_warehouse_answer_is_integer(rng):
    task = gen_warehouse_inventory(rng)
    int(task.solutions[0])


@pytest.mark.unit
def test_production_pipeline_output_positive():
    """Pipeline output should always be non-negative."""
    for seed in range(20):
        task = gen_production_pipeline(random.Random(seed))
        assert int(task.solutions[0]) >= 0
