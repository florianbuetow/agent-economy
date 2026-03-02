"""Unit test conftest for math_task_factory."""

import random

import pytest


@pytest.fixture
def rng() -> random.Random:
    """Seeded RNG for reproducible tests."""
    return random.Random(42)
