"""The LLM prompt must name the current task's output keys exactly."""

from __future__ import annotations

import pytest

from classification_worker.solvers import SYSTEM_PROMPT, build_prompt
from task_feeder.workloads.classification import ClassificationTaskData

pytestmark = pytest.mark.unit


def test_prompt_uses_actual_input_ids_instead_of_a_literal_placeholder() -> None:
    task = ClassificationTaskData(
        task_type="routing",
        labels=("billing", "bug"),
        inputs=(("case-1", "charged twice"), ("case-2", "button crashes")),
        labels_file="labels.json",
        rationale_file="rationale.txt",
        rationale_max_chars=200,
        min_correct=2,
    )

    prompt = build_prompt(task)

    assert 'Required labels keys (exactly): ["case-1", "case-2"]' in prompt
    assert 'Required rationale keys (exactly): ["case-1", "case-2"]' in prompt
    assert '"<id>"' not in SYSTEM_PROMPT
    assert '"<id>"' not in prompt
