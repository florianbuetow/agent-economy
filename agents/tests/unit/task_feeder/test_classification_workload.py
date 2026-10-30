"""Unit tests for the text-classification task source, spec contract and reviewer."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from task_feeder.reader import RawTask, load_task_snapshot
from task_feeder.review import ReviewLoop
from task_feeder.workloads.classification import (
    CLASSIFICATION_PROBLEM_TYPE,
    ClassificationSpecError,
    ClassificationTask,
    is_classification_task,
    load_seed,
    parse_task_spec,
    reference_labels,
    render_raw_task,
    review_submission,
    write_task_source,
)

from ._runtime_fixtures import TASK_ID, WORKER_ID, make_agent, runtime_commands, submitted_task

SEED_PATH = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "task_feeder"
    / "workloads"
    / "classification_seed.json"
)


def _task() -> ClassificationTask:
    return ClassificationTask(
        title="Route three tickets",
        task_type="ticket_routing",
        level=2,
        instructions="Assign each support ticket to the team that should handle it.",
        labels=("billing", "bug"),
        inputs=(
            ("t1", "I was charged twice on my invoice"),
            ("t2", "The app crashes on start"),
            ("t3", "Refund my last payment please"),
        ),
        expected={"t1": "billing", "t2": "bug", "t3": "billing"},
        min_correct=2,
        rationale_max_chars=300,
    )


def _raw() -> RawTask:
    return render_raw_task(_task())


@pytest.mark.unit
class TestTaskSource:
    def test_rendered_raw_task_carries_problem_type_and_reference(self) -> None:
        raw = _raw()
        assert raw.problem_type == CLASSIFICATION_PROBLEM_TYPE
        assert raw.level == 2
        assert is_classification_task(raw)
        assert reference_labels(raw) == {"t1": "billing", "t2": "bug", "t3": "billing"}

    def test_spec_states_inputs_outputs_and_acceptance_rule(self) -> None:
        spec = _raw().spec
        assert "I was charged twice on my invoice" in spec
        assert "labels.json" in spec
        assert "rationale.txt" in spec
        assert "at least 2 of 3" in spec

    def test_spec_never_leaks_reference_labels(self) -> None:
        spec = _raw().spec
        data = parse_task_spec(spec)
        assert "expected" not in spec
        assert data.task_type == "ticket_routing"

    def test_worker_parses_spec_back_to_public_task_data(self) -> None:
        parsed = parse_task_spec(_raw().spec)
        assert parsed.labels == ("billing", "bug")
        assert parsed.input_ids == ("t1", "t2", "t3")
        assert parsed.inputs[1] == ("t2", "The app crashes on start")
        assert parsed.labels_file == "labels.json"
        assert parsed.rationale_file == "rationale.txt"
        assert parsed.min_correct == 2
        assert parsed.rationale_max_chars == 300

    def test_non_classification_spec_is_rejected(self) -> None:
        with pytest.raises(ClassificationSpecError):
            parse_task_spec("TASK: Calculate the sum of 10, 2.")

    def test_math_raw_task_is_not_classification(self) -> None:
        raw = RawTask(
            title="Add",
            spec="2+2",
            solutions=("4",),
            level=1,
            problem_type="addition_small",
            solution_note=None,
        )
        assert not is_classification_task(raw)

    def test_expected_labels_must_cover_inputs_with_allowed_labels(self) -> None:
        with pytest.raises(ClassificationSpecError):
            ClassificationTask(
                title="Bad",
                task_type="ticket_routing",
                level=1,
                instructions="x",
                labels=("billing",),
                inputs=(("t1", "text"),),
                expected={"t1": "bug"},
                min_correct=1,
                rationale_max_chars=100,
            )

    def test_min_correct_cannot_exceed_input_count(self) -> None:
        with pytest.raises(ClassificationSpecError):
            ClassificationTask(
                title="Bad",
                task_type="ticket_routing",
                level=1,
                instructions="x",
                labels=("billing",),
                inputs=(("t1", "text"),),
                expected={"t1": "billing"},
                min_correct=2,
                rationale_max_chars=100,
            )

    def test_tracked_seed_renders_a_source_the_feeder_accepts(self, tmp_path: Path) -> None:
        tasks = load_seed(SEED_PATH)
        assert len(tasks) >= 6
        assert {task.task_type for task in tasks} >= {"ticket_routing", "sentiment"}
        out = tmp_path / "classification_tasks.jsonl"
        write_task_source(tasks, out)
        snapshot = load_task_snapshot(out, max_bytes=1_000_000)
        assert len(snapshot.entries) == len(tasks)
        assert all(is_classification_task(entry.raw_task) for entry in snapshot.entries)


@pytest.mark.unit
class TestReviewSubmission:
    def test_enough_correct_labels_with_rationale_is_approved(self) -> None:
        labels = json.dumps({"t1": "billing", "t2": "bug", "t3": "bug"}).encode()
        verdict = review_submission(_raw(), labels_file=labels, rationale_file=b"t1: invoice")
        assert verdict.approved
        assert verdict.correct == 2

    def test_too_few_correct_labels_is_disputed_with_counts(self) -> None:
        labels = json.dumps({"t1": "bug", "t2": "billing", "t3": "billing"}).encode()
        verdict = review_submission(_raw(), labels_file=labels, rationale_file=b"why")
        assert not verdict.approved
        assert verdict.correct == 1
        assert "1 of 3" in verdict.reason

    def test_missing_rationale_is_disputed(self) -> None:
        labels = json.dumps({"t1": "billing", "t2": "bug", "t3": "billing"}).encode()
        verdict = review_submission(_raw(), labels_file=labels, rationale_file=None)
        assert not verdict.approved
        assert "rationale.txt" in verdict.reason

    def test_label_outside_allowed_set_is_disputed(self) -> None:
        labels = json.dumps({"t1": "billing", "t2": "crash", "t3": "billing"}).encode()
        verdict = review_submission(_raw(), labels_file=labels, rationale_file=b"why")
        assert not verdict.approved
        assert "crash" in verdict.reason

    def test_invalid_json_is_disputed(self) -> None:
        verdict = review_submission(_raw(), labels_file=b"billing", rationale_file=b"why")
        assert not verdict.approved
        assert "labels.json" in verdict.reason


def _asset_agent(files: dict[str, bytes]) -> MagicMock:
    agent = make_agent()
    agent.list_tasks = AsyncMock(return_value=[{"task_id": TASK_ID, "status": "submitted"}])
    agent.get_task = AsyncMock(return_value=submitted_task(None))
    assets = [
        {
            "asset_id": f"asset-{index}",
            "filename": name,
            "uploader_id": WORKER_ID,
            "uploaded_at": f"2026-01-01T00:00:0{index}Z",
        }
        for index, name in enumerate(files)
    ]
    by_id = {f"asset-{index}": content for index, content in enumerate(files.values())}

    async def _raw(_method: str, url: str, headers: object) -> MagicMock:
        del headers
        return MagicMock(content=by_id[url.rsplit("/", 1)[-1]])

    agent._request = AsyncMock(return_value={"assets": assets})
    agent._request_raw = AsyncMock(side_effect=_raw)
    return agent


@pytest.mark.unit
class TestFeederReviewLoopDispatch:
    @pytest.mark.asyncio
    async def test_classification_task_is_reviewed_from_labels_file(self, tmp_path: Path) -> None:
        labels = json.dumps({"t1": "billing", "t2": "bug", "t3": "billing"}).encode()
        # rationale uploaded last: the reviewer must pick files by name, not order
        agent = _asset_agent({"labels.json": labels, "rationale.txt": b"t1: invoice"})
        with runtime_commands(tmp_path, agent) as (commands, _store):
            loop = ReviewLoop(agent, {TASK_ID: _raw()}, commands)
            result = await loop.review_one(TASK_ID)
        assert result == "approved"

    @pytest.mark.asyncio
    async def test_wrong_classification_is_disputed_with_reason(self, tmp_path: Path) -> None:
        labels = json.dumps({"t1": "bug", "t2": "billing", "t3": "bug"}).encode()
        agent = _asset_agent({"labels.json": labels, "rationale.txt": b"why"})
        with runtime_commands(tmp_path, agent) as (commands, _store):
            loop = ReviewLoop(agent, {TASK_ID: _raw()}, commands)
            result = await loop.review_one(TASK_ID)
        assert result == "disputed"
        reason = agent.dispute_task.await_args.args[1]
        assert "0 of 3" in reason
