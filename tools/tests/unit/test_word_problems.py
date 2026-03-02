"""Tests for Level 13: Compound word problem generators."""

import random

import pytest

from math_task_factory.generators.word_problems import (
    gen_conditional_word,
    gen_distractor_word,
    gen_multi_step_word,
)
from math_task_factory.types import MathTask

_GENERATORS = [gen_multi_step_word, gen_distractor_word, gen_conditional_word]
_NAMES = ["multi_step_word", "distractor_word", "conditional_word"]


@pytest.mark.unit
@pytest.mark.parametrize("gen,name", zip(_GENERATORS, _NAMES))
def test_produces_valid_mathtask(gen, name, rng):
    task = gen(rng)
    assert isinstance(task, MathTask)
    assert task.level == 13
    assert task.problem_type == name
    assert len(task.solutions) >= 1
    assert task.title
    assert task.spec


@pytest.mark.unit
@pytest.mark.parametrize("gen", _GENERATORS)
def test_deterministic_with_seed(gen):
    t1 = gen(random.Random(55))
    t2 = gen(random.Random(55))
    assert t1.solutions == t2.solutions
    assert t1.spec == t2.spec


@pytest.mark.unit
@pytest.mark.parametrize("gen", _GENERATORS)
def test_variety_across_seeds(gen):
    specs = {gen(random.Random(s)).spec for s in range(10)}
    assert len(specs) >= 5


@pytest.mark.unit
def test_distractor_word_answer_is_positive():
    """Widget production count should always be positive."""
    for seed in range(20):
        task = gen_distractor_word(random.Random(seed))
        assert int(task.solutions[0]) > 0


@pytest.mark.unit
def test_conditional_word_answer_is_integer(rng):
    task = gen_conditional_word(rng)
    int(task.solutions[0])
