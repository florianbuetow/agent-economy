"""T-109: the ratified Q-13 vagueness rubric must actually reach the judge
prompt, not just exist as a design decision.

docs/plans/2026-07-10-q13-judge-panel-decision.md ratified "an explicit
'vague spec' rubric is written into the judge system prompt" — before this,
`SYSTEM_PROMPT` was a single sentence with no worked criteria for what counts
as ambiguous and no scoring guidance (see the historical note in
docs/specifications/service-api/court-service-specs.md's Judge Architecture
section). These tests assert the rubric's structured content is present in
the prompt LLMJudge actually sends, and would have failed against that
one-sentence prompt.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from court_service.judges.base import DisputeContext
from court_service.judges.llm_judge import LLMJudge
from court_service.judges.prompts import SYSTEM_PROMPT

pytestmark = pytest.mark.unit


class TestVaguenessRubricIsWritten:
    """SYSTEM_PROMPT itself must carry the structured rubric."""

    def test_prompt_still_states_the_core_principle(self) -> None:
        assert "ambiguity in the specification favors the worker" in SYSTEM_PROMPT.lower()

    def test_prompt_defines_concrete_ambiguity_criteria(self) -> None:
        """Not just the one-sentence principle — concrete, checkable criteria
        for what makes a specification vague."""
        lowered = SYSTEM_PROMPT.lower()
        assert "acceptance criteria" in lowered
        assert "edge case" in lowered
        assert "subjective" in lowered

    def test_prompt_gives_scoring_guidance(self) -> None:
        """Not just criteria for vagueness — guidance on how vagueness maps
        to a worker_pct range."""
        assert "worker_pct" in SYSTEM_PROMPT
        lowered = SYSTEM_PROMPT.lower()
        assert "70-100" in lowered
        assert "0-30" in lowered


class TestVaguenessRubricReachesTheLLMCall:
    """The rubric must be in the actual prompt LLMJudge sends to litellm —
    not just defined in prompts.py and never wired through."""

    async def test_evaluate_sends_the_rubric_as_the_system_message(self) -> None:
        response = {"choices": [{"message": {"content": '{"worker_pct": 50, "reasoning": "x"}'}}]}
        mock_acompletion = AsyncMock(return_value=response)
        judge = LLMJudge(
            judge_id="judge-0",
            model="test-model",
            temperature=0.0,
            api_base=None,
            api_key=None,
        )
        context = DisputeContext(
            task_spec="Write something",
            deliverables=["file.txt"],
            claim="Not good enough",
            rebuttal=None,
            task_title="Task",
            reward=100,
        )
        with patch("court_service.judges.llm_judge.litellm.acompletion", new=mock_acompletion):
            await judge.evaluate(context)

        _, kwargs = mock_acompletion.call_args
        system_message = kwargs["messages"][0]
        assert system_message["role"] == "system"
        assert system_message["content"] == SYSTEM_PROMPT
        assert "acceptance criteria" in system_message["content"].lower()
