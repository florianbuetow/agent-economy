"""Mocked-transport tests for LLMJudge (GAP-F4, T-071/T-072).

`LLMJudge.evaluate()` is the one production code path that talks to a real
LLM (via `litellm.acompletion`) and had zero test coverage before this file.
`LLMJudge` exposes no client-injection seam, so the network boundary is
mocked at its one crossing point — `litellm.acompletion` itself — the same
way `AsyncMock`-patching an HTTP client's request method stands in for a
mocked transport elsewhere in this codebase. No real network call is ever
made; every response shape below is what a real OpenAI-compatible chat
completion would return.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from service_commons.exceptions import ServiceError

from court_service.judges.base import DisputeContext
from court_service.judges.llm_judge import LLMJudge


def _context() -> DisputeContext:
    return DisputeContext(
        task_spec="Write a haiku about SQLite",
        deliverables=["haiku.txt"],
        claim="The haiku does not scan correctly",
        rebuttal="Haiku scansion is intentionally loose here",
        task_title="Haiku task",
        reward=100,
    )


def _completion_response(content: str) -> dict[str, object]:
    """A realistic litellm/OpenAI-shaped chat completion response."""
    return {"choices": [{"message": {"content": content}}]}


def _judge() -> LLMJudge:
    return LLMJudge(
        judge_id="judge-0",
        model="test-model",
        temperature=0.0,
        api_base="http://mock-llm.invalid",
        api_key="test-key",
    )


@pytest.mark.unit
class TestLLMJudgeHappyPath:
    async def test_evaluate_parses_valid_json_response(self) -> None:
        response = _completion_response('{"worker_pct": 70, "reasoning": "Mostly complete"}')
        with patch(
            "court_service.judges.llm_judge.litellm.acompletion",
            new=AsyncMock(return_value=response),
        ):
            vote = await _judge().evaluate(_context())

        assert vote.judge_id == "judge-0"
        assert vote.worker_pct == 70
        assert vote.reasoning == "Mostly complete"
        assert vote.voted_at

    async def test_evaluate_strips_markdown_code_fences(self) -> None:
        response = _completion_response(
            '```json\n{"worker_pct": 40, "reasoning": "Partial delivery"}\n```'
        )
        with patch(
            "court_service.judges.llm_judge.litellm.acompletion",
            new=AsyncMock(return_value=response),
        ):
            vote = await _judge().evaluate(_context())

        assert vote.worker_pct == 40
        assert vote.reasoning == "Partial delivery"

    async def test_evaluate_passes_model_and_prompts_to_litellm(self) -> None:
        response = _completion_response('{"worker_pct": 50, "reasoning": "Split"}')
        mock_acompletion = AsyncMock(return_value=response)
        with patch("court_service.judges.llm_judge.litellm.acompletion", new=mock_acompletion):
            await _judge().evaluate(_context())

        _, kwargs = mock_acompletion.call_args
        assert kwargs["model"] == "test-model"
        assert kwargs["api_base"] == "http://mock-llm.invalid"
        messages = kwargs["messages"]
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "SQLite" in messages[1]["content"]


@pytest.mark.unit
class TestLLMJudgeErrorHandling:
    async def test_evaluate_raises_judge_unavailable_on_missing_json(self) -> None:
        response = _completion_response("I refuse to answer in JSON.")
        with (
            patch(
                "court_service.judges.llm_judge.litellm.acompletion",
                new=AsyncMock(return_value=response),
            ),
            pytest.raises(ServiceError) as excinfo,
        ):
            await _judge().evaluate(_context())

        assert excinfo.value.error == "judge_unavailable"
        assert excinfo.value.status_code == 502

    async def test_evaluate_raises_judge_unavailable_on_out_of_range_worker_pct(self) -> None:
        response = _completion_response('{"worker_pct": 150, "reasoning": "Overshoot"}')
        with (
            patch(
                "court_service.judges.llm_judge.litellm.acompletion",
                new=AsyncMock(return_value=response),
            ),
            pytest.raises(ServiceError) as excinfo,
        ):
            await _judge().evaluate(_context())

        assert excinfo.value.error == "judge_unavailable"

    async def test_evaluate_raises_judge_unavailable_on_empty_reasoning(self) -> None:
        response = _completion_response('{"worker_pct": 50, "reasoning": "  "}')
        with (
            patch(
                "court_service.judges.llm_judge.litellm.acompletion",
                new=AsyncMock(return_value=response),
            ),
            pytest.raises(ServiceError) as excinfo,
        ):
            await _judge().evaluate(_context())

        assert excinfo.value.error == "judge_unavailable"

    async def test_evaluate_wraps_transport_failure_as_judge_unavailable(self) -> None:
        """A real network/timeout failure from the transport surfaces as the
        same judge_unavailable(502) contract callers already handle."""
        with (
            patch(
                "court_service.judges.llm_judge.litellm.acompletion",
                new=AsyncMock(side_effect=ConnectionError("mock transport unreachable")),
            ),
            pytest.raises(ServiceError) as excinfo,
        ):
            await _judge().evaluate(_context())

        assert excinfo.value.error == "judge_unavailable"
        assert excinfo.value.status_code == 502
        assert "judge-0" in excinfo.value.message
