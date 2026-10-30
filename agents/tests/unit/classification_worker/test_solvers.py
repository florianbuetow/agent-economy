"""Classification solvers and the two submitted outputs."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from classification_worker.solvers import (
    Answer,
    KeywordSolver,
    LLMSolver,
    build_outputs,
)
from task_feeder.workloads.classification import ClassificationTaskData

TASK = ClassificationTaskData(
    task_type="sentiment",
    labels=("positive", "negative", "neutral"),
    inputs=(("s1", "Love it, great value"), ("s2", "Terrible, broke fast"), ("s3", "Blue box")),
    labels_file="labels.json",
    rationale_file="rationale.txt",
    rationale_max_chars=60,
    min_correct=2,
)

KEYWORDS = {"positive": ["love", "great"], "negative": ["terrible", "broke"]}


def _llm(content: str) -> MagicMock:
    client = MagicMock()
    client.complete = AsyncMock(return_value=MagicMock(content=content))
    return client


@pytest.mark.unit
class TestKeywordSolver:
    @pytest.mark.asyncio
    async def test_labels_by_keyword_hits_and_falls_back_to_first_listed_label(self) -> None:
        answer = await KeywordSolver(KEYWORDS).solve(TASK)
        assert answer is not None
        assert answer.labels == {"s1": "positive", "s2": "negative", "s3": "positive"}
        assert "s3" in answer.rationale

    @pytest.mark.asyncio
    async def test_keywords_for_labels_outside_task_are_ignored(self) -> None:
        answer = await KeywordSolver({"spam": ["love"]}).solve(TASK)
        assert answer is not None
        assert set(answer.labels.values()) == {"positive"}


@pytest.mark.unit
class TestLLMSolver:
    @pytest.mark.asyncio
    async def test_parses_json_answer_from_completion(self) -> None:
        content = "Here you go:\n" + json.dumps(
            {
                "labels": {"s1": "positive", "s2": "negative", "s3": "neutral"},
                "rationale": {"s1": "praise", "s2": "defect", "s3": "fact"},
            }
        )
        client = _llm(content)
        answer = await LLMSolver(client).solve(TASK)
        assert answer is not None
        assert answer.labels["s3"] == "neutral"
        assert "s2: negative (defect)" in answer.rationale
        prompt = client.complete.await_args.args[1]
        assert "Blue box" in prompt and "neutral" in prompt

    @pytest.mark.asyncio
    async def test_incomplete_or_disallowed_answer_is_rejected(self) -> None:
        missing = json.dumps({"labels": {"s1": "positive"}, "rationale": {}})
        assert await LLMSolver(_llm(missing)).solve(TASK) is None
        wrong = json.dumps(
            {"labels": {"s1": "happy", "s2": "negative", "s3": "neutral"}, "rationale": {}}
        )
        assert await LLMSolver(_llm(wrong)).solve(TASK) is None
        assert await LLMSolver(_llm("no json here")).solve(TASK) is None


@pytest.mark.unit
class TestOutputs:
    def test_two_named_outputs_with_bounded_rationale(self) -> None:
        answer = Answer(
            labels={"s3": "neutral", "s1": "positive", "s2": "negative"}, rationale="x" * 500
        )
        outputs = build_outputs(TASK, answer)
        assert [name for name, _ in outputs] == ["labels.json", "rationale.txt"]
        labels = json.loads(outputs[0][1])
        assert list(labels) == ["s1", "s2", "s3"]
        assert len(outputs[1][1].decode()) == 60

    def test_answer_round_trips_through_journal_text(self) -> None:
        answer = Answer(labels={"s1": "positive"}, rationale="r")
        assert Answer.from_json(answer.to_json()) == answer
