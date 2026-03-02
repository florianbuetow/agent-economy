"""Tests for LLM dress-up generator."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from math_task_factory.llm_dressup import (
    LLMDressUp,
    LLMDressUpConfig,
    _check_numbers_preserved,
    _extract_numbers,
)
from math_task_factory.types import MathTask

_CONFIG = LLMDressUpConfig(
    base_url="http://fake:1234/v1",
    api_key="test-key",
    model_id="test-model",
    temperature=0.9,
    max_tokens=2048,
    max_retries=2,
)


def _sample_task() -> MathTask:
    return MathTask(
        title="Add two numbers",
        spec="TASK: Calculate 347 + 158.\n\nOUTPUT FORMAT: A single integer.\n\nVERIFICATION: Sum and compare.",
        solutions=["505"],
        level=1,
        problem_type="addition_positive",
    )


@pytest.mark.unit
def test_extract_numbers():
    text = "Start with 347. Add 158. Multiply by 3.5. Result is 1234."
    nums = _extract_numbers(text)
    assert "347" in nums
    assert "158" in nums
    assert "3.5" in nums
    assert "1234" in nums


@pytest.mark.unit
def test_check_numbers_preserved_all_present():
    missing = _check_numbers_preserved(["347", "158"], "There are 347 items and 158 more")
    assert missing == []


@pytest.mark.unit
def test_check_numbers_preserved_some_missing():
    missing = _check_numbers_preserved(["347", "158", "99"], "There are 347 items")
    assert "158" in missing
    assert "99" in missing


@pytest.mark.unit
def test_dress_up_returns_same_solutions():
    """Dressed-up task must have identical solutions to the original."""
    task = _sample_task()

    # Mock the OpenAI client to return a rewritten spec containing the original numbers
    mock_response = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = (
        "TASK: A shopkeeper has 347 apples and receives 158 more.\n\n"
        "OUTPUT FORMAT: A single integer.\n\n"
        "VERIFICATION: Sum and compare."
    )
    mock_response.choices = [mock_choice]

    with patch("math_task_factory.llm_dressup.AsyncOpenAI") as mock_cls:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_client.close = AsyncMock()
        mock_cls.return_value = mock_client

        dresser = LLMDressUp(config=_CONFIG)
        result = asyncio.run(dresser.dress_up(task))

    assert result.solutions == task.solutions
    assert result.level == task.level
    assert result.problem_type == task.problem_type
    assert "347" in result.spec
    assert "158" in result.spec


@pytest.mark.unit
def test_dress_up_falls_back_on_failure():
    """If all retries fail, returns the original task unchanged."""
    task = _sample_task()

    with patch("math_task_factory.llm_dressup.AsyncOpenAI") as mock_cls:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=RuntimeError("connection refused")
        )
        mock_client.close = AsyncMock()
        mock_cls.return_value = mock_client

        dresser = LLMDressUp(config=_CONFIG)
        result = asyncio.run(dresser.dress_up(task))

    assert result.spec == task.spec
    assert result.solutions == task.solutions


@pytest.mark.unit
def test_dress_up_retries_on_missing_numbers():
    """If LLM drops numbers, retries until max_retries, then falls back."""
    task = _sample_task()

    # Always return spec that drops "158"
    mock_response = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = "A shopkeeper has 347 apples and some more."
    mock_response.choices = [mock_choice]

    with patch("math_task_factory.llm_dressup.AsyncOpenAI") as mock_cls:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_client.close = AsyncMock()
        mock_cls.return_value = mock_client

        dresser = LLMDressUp(config=_CONFIG)
        result = asyncio.run(dresser.dress_up(task))

    # Falls back to original since 158 is missing from every attempt
    assert result.spec == task.spec
    # Should have tried max_retries times
    assert mock_client.chat.completions.create.call_count == _CONFIG.max_retries


@pytest.mark.unit
def test_dress_up_batch():
    """Batch dresses up multiple tasks concurrently."""
    tasks = [_sample_task(), _sample_task()]

    mock_response = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = (
        "A baker has 347 loaves and gets 158 more.\n\n"
        "OUTPUT FORMAT: A single integer.\n\n"
        "VERIFICATION: Sum and compare."
    )
    mock_response.choices = [mock_choice]

    with patch("math_task_factory.llm_dressup.AsyncOpenAI") as mock_cls:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_client.close = AsyncMock()
        mock_cls.return_value = mock_client

        dresser = LLMDressUp(config=_CONFIG)
        results = asyncio.run(dresser.dress_up_batch(tasks))

    assert len(results) == 2
    for r in results:
        assert r.solutions == ["505"]
